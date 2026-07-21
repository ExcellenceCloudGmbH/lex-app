from __future__ import annotations

import base64
import datetime as _dt
import logging
import os
import re
import tempfile
import time
from io import BytesIO
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple
from urllib.parse import parse_qs

import pandas as pd
from django.conf import settings
from django.core.exceptions import FieldDoesNotExist
from django.db.models import Q
from django.utils import timezone as _dj_tz
from django.db.models import QuerySet
from django.http import FileResponse, JsonResponse
from lex.api.filters import UserReadRestrictionFilterBackend, ForeignKeyFilterBackend
from lex.api.utils.temporal import parse_as_of_datetime
from lex.api.views.model_entries.List import ListModelEntries
from lex.api.views.model_entries.filter_backends import PrimaryKeyListFilterBackend
from lex.process_admin.models.utils import get_relation_fields
from rest_framework.generics import GenericAPIView
from rest_framework.permissions import IsAuthenticated
from rest_framework_api_key.permissions import HasAPIKey

MAX_AG_EXPORT_ROWS = 1_000_000
AG_GROUP_HIERARCHY_COLUMN = "__ag_group_hierarchy_label"
AG_GROUP_HIERARCHY_DEPTH_COLUMN = "__ag_group_hierarchy_depth"

logger = logging.getLogger(__name__)


def _to_excel_naive(df: "pd.DataFrame") -> "pd.DataFrame":
    """Make datetime cells timezone-naive for Excel.

    Under ``USE_TZ=True`` the ORM yields timezone-aware (UTC) datetimes, and
    ``xlsxwriter`` refuses them (``Excel does not support datetimes with
    timezones``). Convert any aware value to its wall-clock in the configured
    display zone (``settings.TIME_ZONE``) so the workbook shows the same instant
    the API serves. A no-op under ``USE_TZ=False`` (values are already naive).
    """
    def _strip(value):
        if isinstance(value, _dt.datetime) and value.tzinfo is not None:
            return _dj_tz.localtime(value).replace(tzinfo=None)
        return value

    for col in df.columns:
        series = df[col]
        if isinstance(series.dtype, pd.DatetimeTZDtype):
            df[col] = series.dt.tz_convert(settings.TIME_ZONE).dt.tz_localize(None)
        elif series.dtype == object:
            df[col] = series.map(_strip)
    return df


# Streaming export tunables. Kept conservative so the process stays well
# within a 2 GB RAM / 0.1 CPU production envelope.
STREAM_EXPORT_DB_CHUNK_SIZE = 500
STREAM_EXPORT_FK_LOOKUP_BATCH = 1000


def _safe_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "1", "yes", "y"}:
            return True
        if lowered in {"false", "0", "no", "n"}:
            return False
    return default


class ModelExportView(GenericAPIView):
    filter_backends = [UserReadRestrictionFilterBackend, PrimaryKeyListFilterBackend, ForeignKeyFilterBackend]
    model_collection = None
    http_method_names = ['post']
    permission_classes = [HasAPIKey | IsAuthenticated]

    def get_exportable_fields_for_object(self, obj, request):
        """Get the set of exportable fields for a single object"""
        try:
            # Use new permission system if available
            if hasattr(obj, 'permission_export'):
                from lex.core.models.LexModel import UserContext
                user_context = UserContext.from_request(request, obj)
                result = obj.permission_export(user_context)
                if result.allowed:
                    all_fields = {f.name for f in obj._meta.fields}
                    exportable_fields = result.get_fields(all_fields)
                else:
                    exportable_fields = set()
            # Fallback to legacy method
            elif hasattr(obj, 'can_export'):
                exportable_fields = obj.can_export(request)
                if exportable_fields is None:
                    exportable_fields = set()
                elif not isinstance(exportable_fields, set):
                    exportable_fields = set(exportable_fields)
            else:
                # Default to all fields if no permission method
                exportable_fields = {f.name for f in obj._meta.fields}
        except Exception:
            # Default to all fields on error
            exportable_fields = {f.name for f in obj._meta.fields}
        
        # Always include basic identifying fields
        return exportable_fields.union({'id', 'created_by', 'edited_by'})

    def filter_and_mask_data_for_export(self, queryset: QuerySet, request) -> pd.DataFrame:
        """
        Create DataFrame with field-level export permissions applied.
        Only includes rows that have at least some exportable fields.
        """

        # Get all field names for the model
        model = queryset.model
        all_fields = [field.name for field in model._meta.fields]

        # Stream rows in chunks to avoid loading the entire queryset into
        # memory and to avoid expensive LIMIT/OFFSET pagination over large
        # tables.
        export_data: List[Dict[str, Any]] = []

        # Fast path: when permissions are uniform we can compute the allowed
        # field set once instead of evaluating it per object.
        uniform_allowed_fields: "Set[str] | None" = None
        if self._has_default_export_permissions(model):
            uniform_allowed_fields = self._compute_uniform_export_mask(queryset, request, model)
            if uniform_allowed_fields is not None:
                uniform_allowed_fields = uniform_allowed_fields.union(
                    {"id", "created_by", "edited_by"}
                )

        if uniform_allowed_fields is not None:
            value_fields = [name for name in all_fields if name in uniform_allowed_fields]
            if not value_fields:
                return pd.DataFrame(columns=all_fields)

            for row in queryset.values(*value_fields).iterator(chunk_size=2000):
                masked_row = {name: row.get(name) if name in value_fields else None for name in all_fields}
                export_data.append(masked_row)
        else:
            for obj in queryset.iterator(chunk_size=1000):
                exportable_fields = self.get_exportable_fields_for_object(obj, request)

                # Only include rows that have at least some exportable fields
                if not exportable_fields:
                    continue

                row_data: Dict[str, Any] = {}
                obj_values = {field.name: getattr(obj, field.name) for field in model._meta.fields}
                for field_name in all_fields:
                    if field_name in exportable_fields:
                        row_data[field_name] = obj_values[field_name]
                    else:
                        row_data[field_name] = None
                export_data.append(row_data)

        # Create DataFrame from the processed data
        if export_data:
            df = pd.DataFrame(export_data)
        else:
            # No exportable data - create empty DataFrame
            df = pd.DataFrame(columns=all_fields)

        return df

    def _normalize_ag_request(self, raw_request: Dict[str, Any]) -> Dict[str, Any]:
        request_payload = dict(raw_request) if isinstance(raw_request, dict) else {}
        request_payload["startRow"] = 0
        request_payload["endRow"] = MAX_AG_EXPORT_ROWS
        request_payload["groupKeys"] = list(request_payload.get("groupKeys") or [])
        request_payload["rowGroupCols"] = list(request_payload.get("rowGroupCols") or [])
        request_payload["pivotCols"] = list(request_payload.get("pivotCols") or [])
        request_payload["valueCols"] = list(request_payload.get("valueCols") or [])
        request_payload["sortModel"] = list(request_payload.get("sortModel") or [])
        request_payload["filterModel"] = request_payload.get("filterModel") or {}
        request_payload["pivotMode"] = _safe_bool(request_payload.get("pivotMode"), default=False)
        return request_payload

    def _build_ag_grid_dataframe(
        self,
        queryset: QuerySet,
        request,
        model,
        ag_export: Dict[str, Any],
    ) -> pd.DataFrame:
        raw_request = ag_export.get("request")
        if not isinstance(raw_request, dict):
            return pd.DataFrame()

        normalized_request = self._normalize_ag_request(raw_request)

        # Fast path: flat (non-grouped, non-pivot) exports with default
        # export-permission semantics can skip the DRF serializer and the
        # per-row Python masking pass entirely. This is the dominant cost of
        # large exports and the main reason the request would hit upstream
        # 524 timeouts.
        fast_df = self._try_build_flat_fast_export_dataframe(
            queryset=queryset,
            request=request,
            model=model,
            ag_export=ag_export,
            normalized_request=normalized_request,
        )
        if fast_df is not None:
            return fast_df

        ag_view = ListModelEntries()
        ag_view.request = request
        ag_view.args = ()
        ag_view.kwargs = self.kwargs
        ag_view.format_kwarg = None
        ag_view._ag_model_class = model
        ag_view._ag_field_validity_cache = {}
        ag_view._ag_model_field_cache = {}

        row_data = self._collect_ag_export_rows(
            ag_view=ag_view,
            queryset=queryset,
            normalized_request=normalized_request,
        )
        if not isinstance(row_data, list) or len(row_data) == 0:
            return pd.DataFrame()

        row_data = self._apply_export_mask_to_ag_rows(
            row_data=row_data,
            queryset=queryset,
            request=request,
            model=model,
        )

        df = pd.DataFrame(row_data)
        # Preserve FK readable-name conversion before header renaming/layout selection.
        df = self._apply_foreign_key_display_names(df, model)
        df = self._refresh_hierarchy_labels_with_readable_values(df, normalized_request)
        return self._apply_ag_column_layout(df, ag_export)

    def _has_default_export_permissions(self, model) -> bool:
        try:
            from lex.core.models.LexModel import LexModel
        except Exception:
            return False

        permission_export = getattr(model, "permission_export", None)
        can_export = getattr(model, "can_export", None)
        return (
            permission_export is getattr(LexModel, "permission_export", None)
            and can_export is getattr(LexModel, "can_export", None)
        )

    def _resolve_export_field_paths(
        self,
        model,
        ag_export: Dict[str, Any],
    ) -> "List[str] | None":
        from lex.api.views.model_entries.List import _resolve_lookup, normalize_field_path

        raw_columns = ag_export.get("columns")
        if not isinstance(raw_columns, list) or not raw_columns:
            return None

        field_paths: List[str] = []
        for raw_column in raw_columns:
            if not isinstance(raw_column, dict):
                continue
            data_key = str(raw_column.get("dataKey") or "").strip()
            fallback_key = str(raw_column.get("field") or raw_column.get("colId") or "").strip()
            candidate = data_key or fallback_key
            if not candidate:
                continue
            # Synthetic AG hierarchy columns are only present in grouped exports
            # which we already filter out before calling this method.
            if candidate.startswith("__ag_"):
                return None
            normalized = normalize_field_path(candidate)
            resolved = _resolve_lookup(model, normalized)
            if resolved is None:
                # Includes serializer-only / computed columns (e.g.
                # `short_description`). Fall back to the slow DRF path so we
                # don't silently drop columns the user expects.
                return None
            field_paths.append(normalized)

        if not field_paths:
            return None

        pk_name = model._meta.pk.name
        if pk_name not in field_paths:
            field_paths.append(pk_name)

        # Preserve order while removing duplicates.
        seen: set = set()
        unique_paths: List[str] = []
        for path in field_paths:
            if path in seen:
                continue
            seen.add(path)
            unique_paths.append(path)
        return unique_paths

    def _compute_uniform_export_mask(
        self,
        queryset: QuerySet,
        request,
        model,
    ) -> "Set[str] | None":
        """Return the field-level export mask if it is safe to assume uniform.

        Restricted to models that have NOT overridden ``permission_export`` /
        ``can_export``. With the default LexModel implementation the result
        depends only on the request user, so a single sample is correct for
        every row. Models with custom per-instance export rules fall back to
        the slow per-row path.
        """
        if not self._has_default_export_permissions(model):
            return None

        sample = queryset.first()
        if sample is None:
            return set()
        try:
            allowed = self.get_exportable_fields_for_object(sample, request)
        except Exception:
            return None
        return allowed if isinstance(allowed, set) else None

    def _try_build_flat_fast_export_dataframe(
        self,
        queryset: QuerySet,
        request,
        model,
        ag_export: Dict[str, Any],
        normalized_request: Dict[str, Any],
    ) -> "pd.DataFrame | None":
        if normalized_request.get("pivotMode"):
            return None
        if normalized_request.get("rowGroupCols"):
            return None
        if normalized_request.get("pivotCols"):
            return None
        if not self._has_default_export_permissions(model):
            return None

        field_paths = self._resolve_export_field_paths(model, ag_export)
        if not field_paths:
            return None

        # Apply AG Grid filter & sort using the existing helpers so we stay
        # consistent with the on-screen view.
        ag_view = ListModelEntries()
        ag_view.request = request
        ag_view.args = ()
        ag_view.kwargs = self.kwargs
        ag_view.format_kwarg = None
        ag_view._ag_model_class = model
        ag_view._ag_field_validity_cache = {}
        ag_view._ag_model_field_cache = {}

        qs = ag_view._apply_filter_model(queryset, normalized_request.get("filterModel") or {})
        qs = ag_view._apply_sort_model(qs, normalized_request.get("sortModel") or [], allowed_columns=None)
        if not qs.query.order_by:
            qs = qs.order_by(model._meta.pk.name)

        # Compute the field-level export mask once. With default LexModel
        # permissions the result is the same for every row.
        allowed_fields = self._compute_uniform_export_mask(qs, request, model)
        if allowed_fields is None:
            return None

        always_allowed = {"id", "id_field", "short_description", "lex_reserved_scopes"}
        allowed_fields = allowed_fields.union(always_allowed)
        # Always retain the PK so downstream layout/excel index is stable.
        allowed_fields.add(model._meta.pk.name)

        # Drop top-level fields the user is not allowed to export. Lookup paths
        # (with `__`) are kept – masking those would require per-row evaluation
        # which the default permission impl does not need.
        effective_paths = [
            path for path in field_paths
            if "__" in path or path in allowed_fields
        ]
        if not effective_paths:
            return pd.DataFrame()

        # Stream rows in chunks straight from the database. Avoids both
        # full DRF serialization and a second per-row masking loop.
        end_row_raw = normalized_request.get("endRow")
        try:
            end_row = int(end_row_raw)
        except (TypeError, ValueError):
            end_row = MAX_AG_EXPORT_ROWS
        end_row = max(1, min(end_row or MAX_AG_EXPORT_ROWS, MAX_AG_EXPORT_ROWS))

        values_iter = qs.values(*effective_paths)[:end_row].iterator(chunk_size=5000)
        rows = list(values_iter)
        if not rows:
            return pd.DataFrame()

        df = pd.DataFrame(rows)

        # Mask any columns that are present but not in the allowed set
        # (defensive – effective_paths should already exclude them).
        for column in list(df.columns):
            if "__" in column:
                continue
            if column not in allowed_fields:
                df[column] = None

        df = self._apply_foreign_key_display_names(df, model)
        return self._apply_ag_column_layout(df, ag_export)


    def _collect_ag_export_rows(
        self,
        ag_view: ListModelEntries,
        queryset: QuerySet,
        normalized_request: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        row_group_cols = normalized_request.get("rowGroupCols") or []
        if not isinstance(row_group_cols, list) or len(row_group_cols) <= 1:
            result = ag_view._execute_ag_grid_request(queryset, normalized_request)
            row_data = result.get("rowData") if isinstance(result, dict) else []
            return row_data if isinstance(row_data, list) else []

        collected_rows: List[Dict[str, Any]] = []
        seen_group_paths: Set[str] = set()

        def build_base_row(group_keys: List[Any]) -> Dict[str, Any]:
            base_row: Dict[str, Any] = {}
            for index, col in enumerate(row_group_cols):
                field = str((col or {}).get("field") or "").strip()
                if not field:
                    continue
                base_row[field] = group_keys[index] if index < len(group_keys) else None
            return base_row

        def walk(group_keys: List[Any]) -> None:
            request_payload = {
                **normalized_request,
                "groupKeys": group_keys,
            }
            result = ag_view._execute_ag_grid_request(queryset, request_payload)
            rows = result.get("rowData") if isinstance(result, dict) else []
            if not isinstance(rows, list) or not rows:
                return

            depth = len(group_keys)
            has_more_group_levels = depth < len(row_group_cols)
            current_group_field = None
            if has_more_group_levels:
                current_group_field = str((row_group_cols[depth] or {}).get("field") or "").strip()

            for row in rows:
                if not isinstance(row, dict):
                    continue

                export_row = build_base_row(group_keys)
                export_row.update(row)

                if has_more_group_levels and current_group_field:
                    current_key = row.get(current_group_field)
                    export_row[current_group_field] = current_key
                    display_key = "(empty)" if current_key in {None, "", "(empty)", "__empty__", "null"} else current_key
                    export_row[AG_GROUP_HIERARCHY_COLUMN] = f"{'  ' * depth}{display_key}"
                    export_row[AG_GROUP_HIERARCHY_DEPTH_COLUMN] = depth
                    path_values = group_keys + [current_key]
                    path_key = f"{current_group_field}:{path_values}"
                    if path_key not in seen_group_paths:
                        seen_group_paths.add(path_key)
                        collected_rows.append(export_row)
                        # Expand nested grouped levels, but stop at deepest grouped aggregation level.
                        if depth < len(row_group_cols) - 1:
                            walk(path_values)
                    continue

                collected_rows.append(export_row)

        walk([])
        return collected_rows

    def _apply_export_mask_to_ag_rows(
        self,
        row_data: List[Dict[str, Any]],
        queryset: QuerySet,
        request,
        model,
    ) -> List[Dict[str, Any]]:
        if not row_data:
            return row_data

        pk_name = model._meta.pk.name
        # Aggregate/group rows do not carry concrete ids; keep them untouched.
        is_leaf_shape = all(
            isinstance(row, dict) and (row.get("id") is not None or row.get(pk_name) is not None)
            for row in row_data
        )
        if not is_leaf_shape:
            return row_data

        # Fast path: when the export mask is uniform across rows we can skip
        # re-fetching every object from the database (an `IN` query with
        # potentially millions of ids was the dominant cost / 524 timeout
        # trigger for large exports).
        uniform_allowed = self._compute_uniform_export_mask(queryset, request, model)
        if uniform_allowed is not None:
            always_allowed = {"id", "id_field", "short_description", "lex_reserved_scopes"}
            allowed_fields = uniform_allowed.union(always_allowed)
            return [
                {
                    key: value if (key in allowed_fields or "__" in str(key)) else None
                    for key, value in row.items()
                }
                if isinstance(row, dict)
                else row
                for row in row_data
            ]

        row_ids: List[Any] = []
        for row in row_data:
            row_id = row.get("id") if isinstance(row, dict) else None
            if row_id is None and isinstance(row, dict):
                row_id = row.get(pk_name)
            if row_id is not None:
                row_ids.append(row_id)

        if not row_ids:
            return row_data

        objects_by_id = {
            str(obj.pk): obj
            for obj in queryset.filter(pk__in=row_ids)
        }
        always_allowed = {"id", "id_field", "short_description", "lex_reserved_scopes"}

        masked_rows: List[Dict[str, Any]] = []
        for row in row_data:
            if not isinstance(row, dict):
                masked_rows.append(row)
                continue

            row_id = row.get("id")
            if row_id is None:
                row_id = row.get(pk_name)
            obj = objects_by_id.get(str(row_id))
            if obj is None:
                masked_rows.append(row)
                continue

            exportable_fields = self.get_exportable_fields_for_object(obj, request).union(always_allowed)
            masked_row = {
                key: value if key in exportable_fields else None
                for key, value in row.items()
            }
            masked_rows.append(masked_row)

        return masked_rows

    def _extract_selected_ids_for_export(self, json_data: Dict[str, Any]) -> List[str]:
        encoded_filter = json_data.get("filtered_export")
        if not isinstance(encoded_filter, str) or encoded_filter == "":
            return []

        try:
            decoded = base64.b64decode(encoded_filter).decode("utf-8")
        except Exception:
            return []

        params = parse_qs(decoded)
        ids = params.get("ids", [])
        return [raw_id for raw_id in ids if raw_id not in ("", None)]

    def _extract_selected_group_key_paths(self, ag_export_payload: Any) -> List[List[Dict[str, Any]]]:
        if not isinstance(ag_export_payload, dict):
            return []

        selection_payload = ag_export_payload.get("selection")
        if not isinstance(selection_payload, dict):
            return []

        raw_paths = selection_payload.get("groupKeyPaths")
        if not isinstance(raw_paths, list):
            return []

        group_paths: List[List[Dict[str, Any]]] = []
        for raw_path in raw_paths:
            if not isinstance(raw_path, list) or not raw_path:
                continue

            normalized_path: List[Dict[str, Any]] = []
            for step in raw_path:
                if not isinstance(step, dict):
                    continue
                field = str(step.get("field") or "").replace(".", "__").strip()
                if not field:
                    continue
                normalized_path.append({"field": field, "key": step.get("key")})

            if normalized_path:
                group_paths.append(normalized_path)

        return group_paths

    def _get_model_field_for_path(self, model, field_path: str):
        parts = field_path.split("__")
        current_model = model
        resolved_field = None

        for index, part in enumerate(parts):
            try:
                resolved_field = current_model._meta.get_field(part)
            except FieldDoesNotExist:
                return None

            if index < len(parts) - 1:
                if not getattr(resolved_field, "is_relation", False) or not getattr(resolved_field, "related_model", None):
                    return None
                current_model = resolved_field.related_model

        return resolved_field

    def _coerce_group_key(self, model_field, key: Any) -> Any:
        if key is None:
            return None
        if not isinstance(key, str):
            return key

        lowered = key.strip().lower()
        if lowered in {"null", "none"}:
            return None
        if model_field is None:
            return key

        internal_type = getattr(model_field, "get_internal_type", lambda: "")()

        if internal_type in {"BooleanField", "NullBooleanField"}:
            if lowered in {"true", "1", "yes", "y"}:
                return True
            if lowered in {"false", "0", "no", "n"}:
                return False

        if internal_type in {
            "IntegerField",
            "BigIntegerField",
            "SmallIntegerField",
            "PositiveIntegerField",
            "PositiveSmallIntegerField",
            "AutoField",
            "BigAutoField",
        }:
            try:
                return int(key)
            except (TypeError, ValueError):
                return key

        if internal_type == "FloatField":
            try:
                return float(key)
            except (TypeError, ValueError):
                return key

        return key

    def _apply_ag_selection_filters(
        self,
        queryset: QuerySet,
        model,
        selected_ids: List[str],
        selected_group_key_paths: List[List[Dict[str, Any]]],
    ) -> QuerySet:
        selection_requested = bool(selected_ids) or bool(selected_group_key_paths)
        selection_query = Q()
        has_valid_condition = False

        pk_name = model._meta.pk.name
        if selected_ids:
            selection_query |= Q(**{f"{pk_name}__in": selected_ids})
            has_valid_condition = True

        for path in selected_group_key_paths:
            path_query = Q()
            path_has_condition = False
            path_is_valid = True

            for step in path:
                field_path = str(step.get("field") or "").replace(".", "__").strip()
                if not field_path:
                    path_is_valid = False
                    break

                model_field = self._get_model_field_for_path(model, field_path)
                if model_field is None:
                    path_is_valid = False
                    break

                raw_key = step.get("key")
                if raw_key in {"(empty)", "__empty__", "null", None}:
                    path_query &= Q(**{f"{field_path}__isnull": True})
                else:
                    coerced_key = self._coerce_group_key(model_field, raw_key)
                    path_query &= Q(**{field_path: coerced_key})
                path_has_condition = True

            if path_is_valid and path_has_condition:
                selection_query |= path_query
                has_valid_condition = True

        if has_valid_condition:
            return queryset.filter(selection_query)
        if selection_requested:
            return queryset.none()
        return queryset

    def _apply_ag_column_layout(self, df: pd.DataFrame, ag_export: Dict[str, Any]) -> pd.DataFrame:
        if df.empty:
            return df

        raw_columns = ag_export.get("columns")
        if not isinstance(raw_columns, list) or len(raw_columns) == 0:
            return df

        include_column_labels = _safe_bool(ag_export.get("includeColumnLabels"), default=True)

        selected_columns: List[str] = []
        rename_map: Dict[str, str] = {}
        used_headers: Set[str] = set()

        for raw_column in raw_columns:
            if not isinstance(raw_column, dict):
                continue

            data_key = str(raw_column.get("dataKey") or "").strip()
            fallback_key = str(raw_column.get("field") or raw_column.get("colId") or "").strip()
            matched_key = None
            if data_key and data_key in df.columns:
                matched_key = data_key
            elif fallback_key and fallback_key in df.columns:
                matched_key = fallback_key

            if not matched_key or matched_key in selected_columns:
                continue

            selected_columns.append(matched_key)
            if include_column_labels:
                header_name = str(raw_column.get("headerName") or matched_key).strip()
                if header_name:
                    unique_header = header_name
                    suffix = 2
                    while unique_header in used_headers:
                        unique_header = f"{header_name} ({suffix})"
                        suffix += 1
                    used_headers.add(unique_header)
                    rename_map[matched_key] = unique_header

        if selected_columns:
            if AG_GROUP_HIERARCHY_DEPTH_COLUMN in df.columns and AG_GROUP_HIERARCHY_DEPTH_COLUMN not in selected_columns:
                selected_columns.append(AG_GROUP_HIERARCHY_DEPTH_COLUMN)
            df = df.loc[:, selected_columns]
        if rename_map:
            df = df.rename(columns=rename_map)
        return df

    # ────────────────────────────────────────────────────────────────────
    # Streaming "fast path" for flat exports
    # ────────────────────────────────────────────────────────────────────

    def _resolve_ag_column_layout(
        self,
        ag_export: Dict[str, Any],
        available_keys: Set[str],
    ) -> Tuple[List[str], List[str]]:
        """Pick (data_keys, header_labels) honouring the AG column layout.

        Mirrors `_apply_ag_column_layout` but operates on a known set of
        available column keys instead of a DataFrame, so we can use it before
        any data is materialised in memory.
        """
        raw_columns = ag_export.get("columns")
        include_column_labels = _safe_bool(ag_export.get("includeColumnLabels"), default=True)

        if not isinstance(raw_columns, list) or not raw_columns:
            ordered_keys = sorted(available_keys)
            return ordered_keys, list(ordered_keys)

        data_keys: List[str] = []
        headers: List[str] = []
        used_headers: Set[str] = set()
        seen_keys: Set[str] = set()

        for raw_column in raw_columns:
            if not isinstance(raw_column, dict):
                continue
            data_key = str(raw_column.get("dataKey") or "").strip()
            fallback_key = str(raw_column.get("field") or raw_column.get("colId") or "").strip()
            matched_key: Optional[str] = None
            if data_key and data_key in available_keys:
                matched_key = data_key
            elif fallback_key and fallback_key in available_keys:
                matched_key = fallback_key

            if not matched_key or matched_key in seen_keys:
                continue

            seen_keys.add(matched_key)
            data_keys.append(matched_key)

            header_name = matched_key
            if include_column_labels:
                header_candidate = str(raw_column.get("headerName") or matched_key).strip() or matched_key
                unique_header = header_candidate
                suffix = 2
                while unique_header in used_headers:
                    unique_header = f"{header_candidate} ({suffix})"
                    suffix += 1
                used_headers.add(unique_header)
                header_name = unique_header
            headers.append(header_name)

        return data_keys, headers

    def _build_fk_display_maps(
        self,
        model,
        queryset: QuerySet,
        column_keys: Iterable[str],
    ) -> Dict[str, Dict[Any, str]]:
        """Build {column → {pk → readable}} maps using DISTINCT pk lookups.

        Uses one cheap ``DISTINCT`` per FK column (so memory stays O(unique
        referenced ids) rather than O(rows)) and only loads the related rows
        actually referenced by the export.
        """
        column_keys = set(column_keys)
        relation_fields = get_relation_fields(model)
        fk_maps: Dict[str, Dict[Any, str]] = {}

        for field in relation_fields:
            remote_model = getattr(getattr(field, "remote_field", None), "model", None)
            if remote_model is None:
                continue

            candidate_columns = [
                name
                for name in (getattr(field, "name", None), getattr(field, "attname", None))
                if isinstance(name, str) and name in column_keys
            ]
            if not candidate_columns:
                continue

            attname = getattr(field, "attname", None) or candidate_columns[0]
            try:
                referenced_pks = list(
                    queryset.exclude(**{f"{attname}__isnull": True})
                    .values_list(attname, flat=True)
                    .distinct()
                    .iterator(chunk_size=STREAM_EXPORT_FK_LOOKUP_BATCH)
                )
            except Exception:
                referenced_pks = []

            if not referenced_pks:
                continue

            display_map: Dict[Any, str] = {}
            try:
                related_iter = remote_model.objects.filter(pk__in=referenced_pks).iterator(
                    chunk_size=STREAM_EXPORT_FK_LOOKUP_BATCH
                )
                for item in related_iter:
                    readable = str(item)
                    display_map[item.pk] = readable
                    display_map[str(item.pk)] = readable
            except Exception:
                continue

            if not display_map:
                continue

            for column in candidate_columns:
                fk_maps[column] = display_map

        return fk_maps

    def _normalize_cell_value(self, value: Any) -> Any:
        """Coerce DB values into something xlsxwriter can write directly."""
        if value is None:
            return None
        if isinstance(value, (str, int, float, bool)):
            return value
        if isinstance(value, _dt.datetime):
            return value.replace(tzinfo=None) if value.tzinfo is not None else value
        if isinstance(value, (_dt.date, _dt.time)):
            return value
        if isinstance(value, (list, tuple, set, dict)):
            return str(value)
        return str(value)

    # ────────────────────────────────────────────────────────────────────
    # Universal streaming export (handles both DB fields and computed
    # attributes). Replaces the pandas + DRF fallback for AG Grid exports.
    # ────────────────────────────────────────────────────────────────────

    def _classify_export_columns(
        self,
        model,
        ag_export: Dict[str, Any],
    ) -> "Tuple[List[Tuple[str, str, str, Optional[str]]], Set[str], Set[str]] | None":
        """Classify each AG column as DB-resolvable or computed.

        Returns a tuple ``(columns, db_paths, select_related_paths)`` where
        ``columns`` is a list of ``(data_key, header_label, kind, db_path)``
        with ``kind`` ∈ {``"db"``, ``"attr"``}. ``db_path`` is the dotted
        field path for ``"db"`` columns and ``None`` otherwise.

        Returns ``None`` only when the AG payload has no usable columns,
        which is the only case in which we must fall back to legacy
        behaviour.
        """
        from lex.api.views.model_entries.List import _resolve_lookup, normalize_field_path

        raw_columns = ag_export.get("columns")
        if not isinstance(raw_columns, list) or not raw_columns:
            return None

        include_column_labels = _safe_bool(ag_export.get("includeColumnLabels"), default=True)

        columns: List[Tuple[str, str, str, Optional[str]]] = []
        db_paths: Set[str] = set()
        select_related: Set[str] = set()
        used_headers: Set[str] = set()
        seen_keys: Set[str] = set()

        for raw_column in raw_columns:
            if not isinstance(raw_column, dict):
                continue
            data_key = str(raw_column.get("dataKey") or "").strip()
            fallback_key = str(raw_column.get("field") or raw_column.get("colId") or "").strip()
            candidate = data_key or fallback_key
            if not candidate or candidate.startswith("__ag_"):
                # Synthetic group hierarchy columns – grouped exports take a
                # different code path.
                continue
            if candidate.startswith("ag-Grid-"):
                # AG Grid's internal UI columns (e.g. ``ag-Grid-SelectionColumn``)
                # carry no data and exist only in the browser. Never export.
                continue
            if candidate in seen_keys:
                continue
            seen_keys.add(candidate)

            header_candidate = (
                str(raw_column.get("headerName") or candidate).strip()
                if include_column_labels
                else candidate
            ) or candidate
            unique_header = header_candidate
            suffix = 2
            while unique_header in used_headers:
                unique_header = f"{header_candidate} ({suffix})"
                suffix += 1
            used_headers.add(unique_header)

            normalized = normalize_field_path(candidate)
            resolved = _resolve_lookup(model, normalized) if normalized else None
            if resolved is not None:
                db_path = resolved[0]
                db_paths.add(db_path)
                if "__" in db_path:
                    # Pre-warm the FK chain so attribute traversal does not
                    # trigger N+1 queries when we later resolve via getattr.
                    select_related.add(db_path.rsplit("__", 1)[0])
                columns.append((candidate, unique_header, "db", db_path))
            else:
                # Computed property / serializer-only field. Resolved per row
                # via getattr on the hydrated instance.
                columns.append((candidate, unique_header, "attr", None))

        if not columns:
            return None

        # Always make the pk available for the mask + downstream use.
        pk_name = model._meta.pk.name
        db_paths.add(pk_name)

        return columns, db_paths, select_related

    def _make_attr_resolver(self, attr_name: str):
        """Return a function ``(instance) -> value`` for a computed attribute.

        Supports dotted attribute paths (e.g. ``customer.display_name``) and
        invokes zero-arg callables transparently so model methods exposed as
        export columns work without code changes.

        Special case: ``short_description`` is exposed as a DRF serializer
        method (``str(obj)``) rather than a model attribute. We reproduce
        that semantic here so exports stay consistent with what the grid
        shows.
        """
        if attr_name == "short_description":
            def short_description_resolver(instance: Any) -> Any:
                # Prefer a real attribute/property if the model defines one;
                # otherwise fall back to the serializer semantic (``str(obj)``).
                value = getattr(instance, "short_description", None)
                if callable(value):
                    try:
                        value = value()
                    except Exception:
                        value = None
                if value is None:
                    try:
                        return str(instance)
                    except Exception:
                        return None
                return value
            return short_description_resolver

        parts = attr_name.split(".")

        def resolver(instance: Any) -> Any:
            current = instance
            for part in parts:
                if current is None:
                    return None
                current = getattr(current, part, None)
                if callable(current):
                    try:
                        current = current()
                    except TypeError:
                        # Bound method that needs args we don't have – skip.
                        return None
                    except Exception:
                        return None
            return current

        return resolver

    def _make_db_resolver(self, db_path: str):
        """Return a function ``(instance) -> value`` traversing FKs via
        Django attribute access. Cheap because we already issued
        ``select_related`` for all FK chains."""
        parts = db_path.split("__")

        def resolver(instance: Any) -> Any:
            current = instance
            for part in parts:
                if current is None:
                    return None
                current = getattr(current, part, None)
            return current

        return resolver

    def _try_stream_universal_fast_export(
        self,
        queryset: QuerySet,
        request,
        model,
        ag_export_payload: Dict[str, Any],
        normalized_request: Dict[str, Any],
    ) -> Optional[FileResponse]:
        """Universal flat-export streaming path.

        Handles both DB-resolvable columns and computed properties by
        iterating model instances (with ``select_related`` for FK chains)
        and resolving each cell via a per-column callable. Writes xlsx in
        constant memory mode straight to disk.

        Returns ``FileResponse`` on success, ``None`` to fall back to the
        legacy pandas path. The only architectural reasons to bail are
        grouping/pivot mode (which require the legacy aggregator) and
        non-default per-row export permissions (which require per-row
        masking that the streaming path can't do uniformly).
        """
        t_start = time.perf_counter()
        model_name = getattr(model, "__name__", "?")

        if normalized_request.get("pivotMode") or normalized_request.get("rowGroupCols") or normalized_request.get("pivotCols"):
            return None
        if not self._has_default_export_permissions(model):
            logger.info(
                "[export:%s] universal-stream skipped: non-default permissions", model_name,
            )
            return None

        classification = self._classify_export_columns(model, ag_export_payload)
        if classification is None:
            return None
        columns, db_paths, select_related = classification

        # Filter & sort using the existing helpers so on-screen and exported
        # row sets stay consistent.
        ag_view = ListModelEntries()
        ag_view.request = request
        ag_view.args = ()
        ag_view.kwargs = self.kwargs
        ag_view.format_kwarg = None
        ag_view._ag_model_class = model
        ag_view._ag_field_validity_cache = {}
        ag_view._ag_model_field_cache = {}

        qs = ag_view._apply_filter_model(queryset, normalized_request.get("filterModel") or {})
        qs = ag_view._apply_sort_model(qs, normalized_request.get("sortModel") or [], allowed_columns=None)
        if not qs.query.order_by:
            qs = qs.order_by(model._meta.pk.name)
        t_filter = time.perf_counter()

        allowed_fields = self._compute_uniform_export_mask(qs, request, model)
        if allowed_fields is None:
            logger.info("[export:%s] universal-stream skipped: non-uniform mask", model_name)
            return None
        t_mask = time.perf_counter()

        # Under default permissions the AG payload already represents the
        # exact set of columns the user wants (and is allowed to see via the
        # grid). Build resolvers for all of them; we only drop a *top-level*
        # DB field when the uniform mask explicitly denies it – that is the
        # single real security gate, and it only applies to direct fields,
        # not to FK traversals or to computed properties (which are
        # explicitly exposed by the model author and opaque to field-level
        # masking).
        always_allowed = {"id", "id_field", "short_description", "lex_reserved_scopes"}
        denied_top_level: Set[str] = set()
        if isinstance(allowed_fields, set):
            model_db_fields = {f.name for f in model._meta.fields}
            denied_top_level = {
                name for name in model_db_fields
                if name not in allowed_fields and name not in always_allowed
            }

        effective_columns: List[Tuple[str, str, Any]] = []
        for data_key, header, kind, db_path in columns:
            if kind == "db":
                top = db_path.split("__", 1)[0] if db_path else ""
                if "__" not in (db_path or "") and top in denied_top_level:
                    continue
                effective_columns.append((data_key, header, self._make_db_resolver(db_path)))
            else:
                effective_columns.append((data_key, header, self._make_attr_resolver(data_key)))
        if not effective_columns:
            logger.info("[export:%s] universal-stream skipped: no permitted columns", model_name)
            return None

        # Row cap honours the AG payload but is bounded by MAX_AG_EXPORT_ROWS.
        try:
            end_row_raw = normalized_request.get("endRow")
            end_row = int(end_row_raw) if end_row_raw is not None else MAX_AG_EXPORT_ROWS
        except (TypeError, ValueError):
            end_row = MAX_AG_EXPORT_ROWS
        end_row = max(1, min(end_row or MAX_AG_EXPORT_ROWS, MAX_AG_EXPORT_ROWS))

        try:
            import xlsxwriter  # type: ignore
        except Exception:
            logger.info("[export:%s] universal-stream skipped: xlsxwriter unavailable", model_name)
            return None

        # Fast path: when every column is a plain DB field (no computed
        # properties) we skip model hydration entirely and stream dicts
        # via ``queryset.values(...)``. This is typically ~15-20x faster
        # than ``.iterator()`` over full model instances because Django
        # doesn't build ``Model.__init__`` for each row.
        attr_count = sum(1 for _, _, k, _ in columns if k == "attr")
        use_values_fast_path = attr_count == 0

        if use_values_fast_path:
            values_keys: List[str] = []
            for _data_key, _header, kind, db_path in columns:
                if kind == "db" and db_path:
                    # Honour the uniform mask deny list for top-level fields.
                    top = db_path.split("__", 1)[0]
                    if "__" not in db_path and top in denied_top_level:
                        continue
                    values_keys.append(db_path)
            # Re-align headers/order with the kept keys.
            header_map = {db_path: header for _k, header, kind, db_path in columns
                          if kind == "db" and db_path}
            header_labels = [header_map[k] for k in values_keys]

            # FK display maps so rows show readable names instead of pks.
            fk_maps = self._build_fk_display_maps(model, qs, values_keys)

            stream_qs = qs.values(*values_keys)
            if end_row < MAX_AG_EXPORT_ROWS:
                stream_qs = stream_qs[:end_row]
        else:
            # Mixed path: hydrate full model instances so computed
            # ``@property`` columns can freely read any field. FK chains
            # are pre-joined via ``select_related`` to avoid N+1
            # round-trips.
            stream_qs = qs
            if select_related:
                stream_qs = stream_qs.select_related(*sorted(select_related))
            if end_row < MAX_AG_EXPORT_ROWS:
                stream_qs = stream_qs[:end_row]
            header_labels = [hdr for _, hdr, _ in effective_columns]

        tmp = tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False)
        tmp.close()
        tmp_path = tmp.name

        first_row_t: Optional[float] = None
        written = 0
        try:
            workbook = xlsxwriter.Workbook(
                tmp_path,
                {
                    "constant_memory": True,
                    "default_date_format": "yyyy-mm-dd hh:mm:ss",
                    "in_memory": False,
                },
            )
            worksheet = workbook.add_worksheet(model.__name__[:31] or "Export")
            datetime_format = workbook.add_format({"num_format": "yyyy-mm-dd hh:mm:ss"})
            date_format = workbook.add_format({"num_format": "yyyy-mm-dd"})

            for col_idx, header in enumerate(header_labels):
                worksheet.write_string(0, col_idx, str(header))
            worksheet.freeze_panes(1, 0)

            row_idx = 1
            t_rows_start = time.perf_counter()

            if use_values_fast_path:
                for raw_row in stream_qs.iterator(chunk_size=STREAM_EXPORT_DB_CHUNK_SIZE):
                    if first_row_t is None:
                        first_row_t = time.perf_counter()
                    for col_idx, key in enumerate(values_keys):
                        value = raw_row.get(key)
                        if value is not None and key in fk_maps:
                            mapped = fk_maps[key].get(value, fk_maps[key].get(str(value)))
                            if mapped is not None:
                                value = mapped
                        if value is None:
                            worksheet.write_blank(row_idx, col_idx, None)
                            continue
                        normalized = self._normalize_cell_value(value)
                        if isinstance(normalized, bool):
                            worksheet.write_boolean(row_idx, col_idx, normalized)
                        elif isinstance(normalized, (int, float)):
                            worksheet.write_number(row_idx, col_idx, normalized)
                        elif isinstance(normalized, _dt.datetime):
                            worksheet.write_datetime(row_idx, col_idx, normalized, datetime_format)
                        elif isinstance(normalized, _dt.date):
                            worksheet.write_datetime(row_idx, col_idx, normalized, date_format)
                        else:
                            worksheet.write_string(row_idx, col_idx, str(normalized))
                    row_idx += 1
                    written += 1
                    if written >= end_row:
                        break
            else:
                for instance in stream_qs.iterator(chunk_size=STREAM_EXPORT_DB_CHUNK_SIZE):
                    if first_row_t is None:
                        first_row_t = time.perf_counter()
                    for col_idx, (_key, _header, resolver) in enumerate(effective_columns):
                        value = resolver(instance)
                        if value is None:
                            worksheet.write_blank(row_idx, col_idx, None)
                            continue
                        normalized = self._normalize_cell_value(value)
                        if isinstance(normalized, bool):
                            worksheet.write_boolean(row_idx, col_idx, normalized)
                        elif isinstance(normalized, (int, float)):
                            worksheet.write_number(row_idx, col_idx, normalized)
                        elif isinstance(normalized, _dt.datetime):
                            worksheet.write_datetime(row_idx, col_idx, normalized, datetime_format)
                        elif isinstance(normalized, _dt.date):
                            worksheet.write_datetime(row_idx, col_idx, normalized, date_format)
                        else:
                            worksheet.write_string(row_idx, col_idx, str(normalized))
                    row_idx += 1
                    written += 1
                    if written >= end_row:
                        break
            workbook.close()
            t_rows_end = time.perf_counter()
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            logger.exception(
                "[export:%s] universal-stream failed; falling back", model_name,
            )
            return None

        if written == 0:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            logger.info(
                "[export:%s] universal-stream produced 0 rows in %.3fs",
                model_name, time.perf_counter() - t_start,
            )
            return JsonResponse({'error': 'No data available for export'}, status=404)

        try:
            file_size = os.path.getsize(tmp_path)
        except OSError:
            file_size = -1
        ttfr = (first_row_t - t_rows_start) if first_row_t else 0.0
        rows_per_s = written / (t_rows_end - t_rows_start) if t_rows_end > t_rows_start else 0.0
        cols_written = len(values_keys) if use_values_fast_path else len(effective_columns)
        logger.info(
            "[export:%s] universal-stream OK path=%s rows=%d cols=%d (attr=%d) bytes=%d "
            "total=%.3fs (filter=%.3fs mask=%.3fs xlsx_ttfr=%.3fs xlsx_loop=%.3fs rows/s=%.0f)",
            model_name,
            "values" if use_values_fast_path else "instance",
            written, cols_written, attr_count, file_size,
            time.perf_counter() - t_start,
            t_filter - t_start, t_mask - t_filter, ttfr,
            t_rows_end - t_rows_start, rows_per_s,
        )

        file_handle = open(tmp_path, "rb")
        response = FileResponse(
            file_handle,
            as_attachment=False,
            content_type=(
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            ),
        )
        original_close = response.close

        def _cleanup():
            try:
                original_close()
            finally:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass

        response.close = _cleanup  # type: ignore[assignment]
        return response

    def _try_stream_flat_fast_export(
        self,
        queryset: QuerySet,
        request,
        model,
        ag_export_payload: Dict[str, Any],
        normalized_request: Dict[str, Any],
    ) -> Optional[FileResponse]:
        """Stream a flat export straight to disk via xlsxwriter.

        Returns a ``FileResponse`` on success, or ``None`` to signal that the
        caller should fall back to the legacy DataFrame-based path. Designed
        to use O(1) memory regardless of result size – the dominant constraint
        in resource-constrained production environments.
        """
        t_start = time.perf_counter()
        model_name = getattr(model, "__name__", "?")

        if normalized_request.get("pivotMode"):
            logger.info("[export:%s] fast-path skipped: pivotMode", model_name)
            return None
        if normalized_request.get("rowGroupCols"):
            logger.info("[export:%s] fast-path skipped: rowGroupCols", model_name)
            return None
        if normalized_request.get("pivotCols"):
            logger.info("[export:%s] fast-path skipped: pivotCols", model_name)
            return None
        if not self._has_default_export_permissions(model):
            logger.info("[export:%s] fast-path skipped: non-default permissions", model_name)
            return None

        field_paths = self._resolve_export_field_paths(model, ag_export_payload)
        if not field_paths:
            logger.info("[export:%s] fast-path skipped: unresolvable columns", model_name)
            return None

        # Apply AG Grid filter & sort using the existing helpers so we stay
        # consistent with the on-screen view.
        ag_view = ListModelEntries()
        ag_view.request = request
        ag_view.args = ()
        ag_view.kwargs = self.kwargs
        ag_view.format_kwarg = None
        ag_view._ag_model_class = model
        ag_view._ag_field_validity_cache = {}
        ag_view._ag_model_field_cache = {}

        qs = ag_view._apply_filter_model(queryset, normalized_request.get("filterModel") or {})
        qs = ag_view._apply_sort_model(qs, normalized_request.get("sortModel") or [], allowed_columns=None)
        if not qs.query.order_by:
            qs = qs.order_by(model._meta.pk.name)
        t_filter = time.perf_counter()

        # Field-level export mask (uniform under default permissions).
        allowed_fields = self._compute_uniform_export_mask(qs, request, model)
        if allowed_fields is None:
            logger.info("[export:%s] fast-path skipped: non-uniform mask", model_name)
            return None
        t_mask = time.perf_counter()

        always_allowed = {"id", "id_field", "short_description", "lex_reserved_scopes"}
        allowed_fields = allowed_fields.union(always_allowed)
        allowed_fields.add(model._meta.pk.name)

        effective_paths = [
            path for path in field_paths
            if "__" in path or path in allowed_fields
        ]
        if not effective_paths:
            return None

        try:
            end_row_raw = normalized_request.get("endRow")
            end_row = int(end_row_raw) if end_row_raw is not None else MAX_AG_EXPORT_ROWS
        except (TypeError, ValueError):
            end_row = MAX_AG_EXPORT_ROWS
        end_row = max(1, min(end_row or MAX_AG_EXPORT_ROWS, MAX_AG_EXPORT_ROWS))

        data_keys, header_labels = self._resolve_ag_column_layout(
            ag_export_payload, set(effective_paths)
        )
        if not data_keys:
            data_keys = list(effective_paths)
            header_labels = list(effective_paths)

        # Build FK display maps using DISTINCT lookups so memory stays small
        # even for FKs that point at huge tables.
        fk_maps = self._build_fk_display_maps(model, qs, data_keys)
        t_fk = time.perf_counter()

        # Defer importing xlsxwriter until we know we can take this path.
        try:
            import xlsxwriter  # type: ignore
        except Exception:
            logger.info("[export:%s] fast-path skipped: xlsxwriter unavailable", model_name)
            return None

        tmp = tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False)
        tmp.close()
        tmp_path = tmp.name

        try:
            workbook = xlsxwriter.Workbook(
                tmp_path,
                {
                    # Stream rows straight to disk – constant memory regardless
                    # of result size. Safe here because we control the
                    # row-by-row write order ourselves.
                    "constant_memory": True,
                    "default_date_format": "yyyy-mm-dd hh:mm:ss",
                    "in_memory": False,
                },
            )
            worksheet = workbook.add_worksheet(model.__name__[:31] or "Export")
            datetime_format = workbook.add_format({"num_format": "yyyy-mm-dd hh:mm:ss"})
            date_format = workbook.add_format({"num_format": "yyyy-mm-dd"})

            # Header row.
            for col_idx, label in enumerate(header_labels):
                worksheet.write_string(0, col_idx, str(label))
            worksheet.freeze_panes(1, 0)

            row_idx = 1
            written = 0
            t_rows_start = time.perf_counter()
            first_row_t: Optional[float] = None

            qs_to_stream = qs.values(*effective_paths)
            if end_row < MAX_AG_EXPORT_ROWS:
                qs_to_stream = qs_to_stream[:end_row]

            for raw_row in qs_to_stream.iterator(chunk_size=STREAM_EXPORT_DB_CHUNK_SIZE):
                if first_row_t is None:
                    first_row_t = time.perf_counter()
                for col_idx, key in enumerate(data_keys):
                    value: Any = raw_row.get(key)
                    if value is not None and key in fk_maps:
                        mapped = fk_maps[key].get(value, fk_maps[key].get(str(value)))
                        if mapped is not None:
                            value = mapped

                    if value is None:
                        worksheet.write_blank(row_idx, col_idx, None)
                        continue

                    normalized = self._normalize_cell_value(value)
                    if isinstance(normalized, bool):
                        worksheet.write_boolean(row_idx, col_idx, normalized)
                    elif isinstance(normalized, (int, float)):
                        worksheet.write_number(row_idx, col_idx, normalized)
                    elif isinstance(normalized, _dt.datetime):
                        worksheet.write_datetime(row_idx, col_idx, normalized, datetime_format)
                    elif isinstance(normalized, _dt.date):
                        worksheet.write_datetime(row_idx, col_idx, normalized, date_format)
                    else:
                        worksheet.write_string(row_idx, col_idx, str(normalized))

                row_idx += 1
                written += 1
                if written >= end_row:
                    break

            workbook.close()
            t_rows_end = time.perf_counter()
        except Exception:
            # Clean up the temp file and let the caller fall back.
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            logger.exception("[export:%s] fast-path failed; falling back", model_name)
            return None

        if written == 0:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            logger.info("[export:%s] fast-path produced 0 rows in %.3fs",
                        model_name, time.perf_counter() - t_start)
            return JsonResponse({'error': 'No data available for export'}, status=404)

        try:
            file_size = os.path.getsize(tmp_path)
        except OSError:
            file_size = -1

        ttfr = (first_row_t - t_rows_start) if first_row_t else 0.0
        total = t_rows_end - t_start
        rows_per_s = written / (t_rows_end - t_rows_start) if t_rows_end > t_rows_start else 0.0
        logger.info(
            "[export:%s] fast-path OK rows=%d cols=%d bytes=%d total=%.3fs "
            "(filter=%.3fs mask=%.3fs fk=%.3fs xlsx_ttfr=%.3fs xlsx_loop=%.3fs rows/s=%.0f)",
            model_name,
            written,
            len(data_keys),
            file_size,
            total,
            t_filter - t_start,
            t_mask - t_filter,
            t_fk - t_mask,
            ttfr,
            t_rows_end - t_rows_start,
            rows_per_s,
        )

        # Hand the file off to Django; FileResponse will close it and we
        # remove the temp file on shutdown via the response's close hook.
        file_handle = open(tmp_path, "rb")
        response = FileResponse(
            file_handle,
            as_attachment=False,
            content_type=(
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            ),
        )

        original_close = response.close

        def _cleanup():
            try:
                original_close()
            finally:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass

        response.close = _cleanup  # type: ignore[assignment]
        return response

    def _apply_foreign_key_display_names(self, df: pd.DataFrame, model) -> pd.DataFrame:
        if df.empty:
            return df

        relation_fields = get_relation_fields(model)
        for field in relation_fields:
            remote_model = getattr(getattr(field, "remote_field", None), "model", None)
            if remote_model is None:
                continue

            candidate_columns = {
                name
                for name in [getattr(field, "name", None), getattr(field, "attname", None)]
                if isinstance(name, str) and len(name) > 0
            }
            available_columns = [column for column in candidate_columns if column in df.columns]
            if not available_columns:
                continue

            # Only fetch FK targets that are actually referenced in the data.
            # Loading the entire related table (`remote_model.objects.all()`)
            # is the dominant cost when exporting wide rows, especially when
            # several FKs point at large tables.
            referenced_pks: set = set()
            for column_name in available_columns:
                column = df[column_name]
                try:
                    unique_values = column.dropna().unique()
                except Exception:
                    unique_values = column.tolist()
                for raw_value in unique_values:
                    if raw_value is None or isinstance(raw_value, (list, tuple, set, dict)):
                        continue
                    referenced_pks.add(raw_value)

            if not referenced_pks:
                continue

            field_objects_dict: Dict[Any, str] = {}
            try:
                related_iter = remote_model.objects.filter(pk__in=referenced_pks).iterator(
                    chunk_size=2000
                )
                for item in related_iter:
                    readable = str(item)
                    field_objects_dict[item.pk] = readable
                    field_objects_dict[str(item.pk)] = readable
            except (TypeError, ValueError):
                # Mixed/incoercible pk types: fall back to a bounded scan.
                for item in remote_model.objects.all().iterator(chunk_size=2000):
                    readable = str(item)
                    field_objects_dict[item.pk] = readable
                    field_objects_dict[str(item.pk)] = readable

            if not field_objects_dict:
                continue

            for column_name in available_columns:
                if df[column_name].isna().all():
                    continue

                def map_foreign_key_value(value: Any):
                    if value is None:
                        return None
                    if isinstance(value, (list, tuple, set, dict)):
                        return value
                    try:
                        is_na = pd.isna(value)
                        if isinstance(is_na, bool) and is_na:
                            return None
                    except Exception:
                        pass

                    return field_objects_dict.get(value, field_objects_dict.get(str(value), value))

                df[column_name] = df[column_name].map(
                    map_foreign_key_value,
                )

        return df

    def _refresh_hierarchy_labels_with_readable_values(
        self,
        df: pd.DataFrame,
        normalized_request: Dict[str, Any],
    ) -> pd.DataFrame:
        if df.empty:
            return df
        if AG_GROUP_HIERARCHY_COLUMN not in df.columns:
            return df
        if AG_GROUP_HIERARCHY_DEPTH_COLUMN not in df.columns:
            return df

        row_group_cols = normalized_request.get("rowGroupCols") or []
        if not isinstance(row_group_cols, list) or len(row_group_cols) <= 1:
            return df

        group_fields = [
            str((col or {}).get("field") or "").strip()
            for col in row_group_cols
        ]

        def normalize_depth(value: Any) -> int:
            try:
                depth = int(value)
            except (TypeError, ValueError):
                depth = 0
            return max(depth, 0)

        refreshed_labels: List[Any] = []
        for _, row in df.iterrows():
            depth = normalize_depth(row.get(AG_GROUP_HIERARCHY_DEPTH_COLUMN))
            if depth >= len(group_fields):
                refreshed_labels.append(row.get(AG_GROUP_HIERARCHY_COLUMN))
                continue

            field_name = group_fields[depth]
            if not field_name or field_name not in df.columns:
                refreshed_labels.append(row.get(AG_GROUP_HIERARCHY_COLUMN))
                continue

            value = row.get(field_name)
            if value in {None, "", "(empty)", "__empty__", "null"}:
                display_value = "(empty)"
            else:
                display_value = value

            refreshed_labels.append(f"{'  ' * depth}{display_value}")

        df[AG_GROUP_HIERARCHY_COLUMN] = refreshed_labels
        return df

    def post(self, request, *args, **kwargs):
        model_container = kwargs['model_container']
        model = model_container.model_class

        queryset = model.objects.all()
        as_of_param = request.query_params.get("as_of")
        if as_of_param:
            as_of_date = parse_as_of_datetime(as_of_param)
            if as_of_date:
                from lex.core.services.Bitemporal import get_queryset_as_of
                queryset = get_queryset_as_of(model, as_of_date)

        queryset = ForeignKeyFilterBackend().filter_queryset(request, queryset, None)
        queryset = UserReadRestrictionFilterBackend()._filter_queryset(request, queryset, model_container)

        json_data = request.data if isinstance(request.data, dict) else {}
        ag_export_payload = json_data.get("ag_export")

        if isinstance(ag_export_payload, dict) and isinstance(ag_export_payload.get("request"), dict):
            selected_ids = self._extract_selected_ids_for_export(json_data)
            selected_group_key_paths = self._extract_selected_group_key_paths(ag_export_payload)
            queryset = self._apply_ag_selection_filters(
                queryset=queryset,
                model=model,
                selected_ids=selected_ids,
                selected_group_key_paths=selected_group_key_paths,
            )
        elif json_data.get("filtered_export") is not None:
            queryset = PrimaryKeyListFilterBackend().filter_for_export(json_data, queryset, self)

        if isinstance(ag_export_payload, dict) and isinstance(ag_export_payload.get("request"), dict):
            # Try the constant-memory streaming path first. It writes xlsx
            # straight to disk row-by-row, never materialising the full
            # result in RAM and never copying through a pandas DataFrame.
            # This is the only path that fits within the production
            # 0.1 CPU / 2 GB RAM envelope for large "select all" exports.
            normalized_request = self._normalize_ag_request(ag_export_payload.get("request") or {})

            # 1) Universal streaming – handles both DB columns and computed
            #    properties via per-column resolvers. Replaces the pandas
            #    fallback for the vast majority of exports.
            streamed = self._try_stream_universal_fast_export(
                queryset=queryset,
                request=request,
                model=model,
                ag_export_payload=ag_export_payload,
                normalized_request=normalized_request,
            )
            if streamed is not None:
                return streamed

            # 2) Pure-DB streaming – kept as a marginally faster path for
            #    flat exports that have zero computed columns. Skipped
            #    silently when the universal path is preferable.
            streamed = self._try_stream_flat_fast_export(
                queryset=queryset,
                request=request,
                model=model,
                ag_export_payload=ag_export_payload,
                normalized_request=normalized_request,
            )
            if streamed is not None:
                return streamed

            t_fallback_start = time.perf_counter()
            logger.info(
                "[export:%s] streaming paths unavailable; entering pandas fallback",
                getattr(model, "__name__", "?"),
            )
            df = self._build_ag_grid_dataframe(queryset, request, model, ag_export_payload)
            logger.info(
                "[export:%s] pandas dataframe built in %.3fs rows=%d cols=%d",
                getattr(model, "__name__", "?"),
                time.perf_counter() - t_fallback_start,
                len(df) if df is not None else 0,
                len(df.columns) if df is not None else 0,
            )
        else:
            # Apply field-level export permission filtering and masking
            df = self.filter_and_mask_data_for_export(queryset, request)
            # FK readable names for the legacy / non-AG path. The AG path
            # already applies them inside `_build_ag_grid_dataframe`, so we
            # avoid running the (still relatively expensive) conversion twice.
            df = self._apply_foreign_key_display_names(df, model)

        # Check if there's any data to export
        if df.empty:
            return JsonResponse({'error': 'No data available for export'}, status=404)

        hierarchy_depths: List[int] = []
        if AG_GROUP_HIERARCHY_DEPTH_COLUMN in df.columns:
            raw_depths = df[AG_GROUP_HIERARCHY_DEPTH_COLUMN].tolist()
            for depth in raw_depths:
                try:
                    normalized_depth = int(depth)
                except (TypeError, ValueError):
                    normalized_depth = 0
                hierarchy_depths.append(max(normalized_depth, 0))
            df = df.drop(columns=[AG_GROUP_HIERARCHY_DEPTH_COLUMN], errors="ignore")

        excel_file = BytesIO()
        # NOTE: Do NOT enable xlsxwriter "constant_memory" mode here.
        # That mode requires strictly row-by-row writes, but pandas'
        # ``DataFrame.to_excel`` writes column-by-column, which silently
        # produces a workbook where every row except the last is blank.
        writer = pd.ExcelWriter(excel_file, engine='xlsxwriter')

        # Excel limits worksheet names to 31 chars and disallows []:*?/\
        sheet_name = re.sub(r'[\[\]:*?/\\]', '_', model.__name__)[:31]

        # Excel has no timezone type; strip tz from aware (USE_TZ=True) datetimes.
        df = _to_excel_naive(df)
        df.to_excel(writer, sheet_name=sheet_name, merge_cells=False, freeze_panes=(1, 1), index=True)

        worksheet = writer.sheets.get(sheet_name)
        if worksheet is not None and hierarchy_depths:
            # Preserve grouped hierarchy in Excel with row outline levels.
            for row_index, depth in enumerate(hierarchy_depths, start=1):
                worksheet.set_row(row_index, None, None, {"level": min(depth, 7)})

        writer.close()
        excel_file.seek(0)

        return FileResponse(excel_file)
