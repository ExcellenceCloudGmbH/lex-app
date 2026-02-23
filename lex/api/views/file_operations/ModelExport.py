from __future__ import annotations

import base64
from io import BytesIO
from typing import Any, Dict, List, Set
from urllib.parse import parse_qs

import pandas as pd
from django.core.exceptions import FieldDoesNotExist
from django.db.models import Q
from django.db.models import QuerySet
from django.http import FileResponse, JsonResponse
from django.utils.dateparse import parse_datetime
from rest_framework.generics import GenericAPIView
from rest_framework.permissions import IsAuthenticated
from rest_framework_api_key.permissions import HasAPIKey

from lex.api.filters import UserReadRestrictionFilterBackend, ForeignKeyFilterBackend
from lex.api.views.model_entries.List import ListModelEntries
from lex.api.views.model_entries.filter_backends import PrimaryKeyListFilterBackend
from lex.process_admin.models.utils import get_relation_fields

MAX_AG_EXPORT_ROWS = 1_000_000
AG_GROUP_HIERARCHY_COLUMN = "__ag_group_hierarchy_label"
AG_GROUP_HIERARCHY_DEPTH_COLUMN = "__ag_group_hierarchy_depth"


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

        # Process objects and build data with field-level masking
        export_data = []

        # Process in batches to avoid memory issues
        batch_size = 1000
        total_count = queryset.count()

        for i in range(0, total_count, batch_size):
            batch = queryset[i:i + batch_size]

            for obj in batch:
                exportable_fields = self.get_exportable_fields_for_object(obj, request)

                # Only include rows that have at least some exportable fields
                if exportable_fields:
                    # Create row data with field masking
                    row_data = {}
                    obj_values = {field.name: getattr(obj, field.name) for field in model._meta.fields}

                    for field_name in all_fields:
                        if field_name in exportable_fields:
                            # Field is exportable, include its value
                            row_data[field_name] = obj_values[field_name]
                        else:
                            # Field is not exportable, mask it (empty/None)
                            row_data[field_name] = None  # or '' for empty string

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

        ag_view = ListModelEntries()
        ag_view.request = request
        ag_view.args = ()
        ag_view.kwargs = self.kwargs
        ag_view.format_kwarg = None
        ag_view._ag_model_class = model
        ag_view._ag_field_validity_cache = {}
        ag_view._ag_model_field_cache = {}

        normalized_request = self._normalize_ag_request(raw_request)
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

            field_objects = remote_model.objects.all()
            field_objects_dict: Dict[Any, str] = {}
            for item in field_objects:
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
            as_of_date = parse_datetime(as_of_param)
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
            df = self._build_ag_grid_dataframe(queryset, request, model, ag_export_payload)
        else:
            # Apply field-level export permission filtering and masking
            df = self.filter_and_mask_data_for_export(queryset, request)

        # Preserve readable names for relation values.
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
        writer = pd.ExcelWriter(excel_file, engine='xlsxwriter')

        df.to_excel(writer, sheet_name=model.__name__, merge_cells=False, freeze_panes=(1, 1), index=True)

        worksheet = writer.sheets.get(model.__name__)
        if worksheet is not None and hierarchy_depths:
            # Preserve grouped hierarchy in Excel with row outline levels.
            for row_index, depth in enumerate(hierarchy_depths, start=1):
                worksheet.set_row(row_index, None, None, {"level": min(depth, 7)})

        writer.close()
        excel_file.seek(0)

        return FileResponse(excel_file)
