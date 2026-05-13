from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta
from decimal import Decimal
from functools import lru_cache
from typing import Any, Dict, Iterable, List, Optional, Tuple

from django.core.exceptions import FieldDoesNotExist
from django.db.models import (
    Avg,
    Case,
    Count,
    F,
    Field,
    IntegerField,
    Max,
    Min,
    Model,
    Q,
    QuerySet,
    Sum,
    When,
)
from django.http import QueryDict
from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime
from rest_framework.generics import ListAPIView
from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response

from lex.api.utils import can_read_from_payload
from lex.api.views.model_entries.filter_backends import UserReadRestrictionFilterBackend
from lex.api.views.model_entries.mixins.ModelEntryProviderMixin import ModelEntryProviderMixin
from lex.core.models.LexModel import UserContext


MAX_PIVOT_COMBINATIONS = 200
AgColumn = Dict[str, Optional[str]]


def _safe_key(value: Any) -> str:
    raw = "null" if value is None else str(value)
    return re.sub(r"[^0-9a-zA-Z_]+", "_", raw).strip("_").lower() or "empty"


def _parse_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "1", "yes", "y"}:
            return True
        if lowered in {"false", "0", "no", "n"}:
            return False
    return default


def _parse_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _ag_filter_has_time(value: Any) -> bool:
    if isinstance(value, datetime):
        return True
    if isinstance(value, str):
        return bool(re.search(r"\d{2}:\d{2}(:\d{2})?", value))
    return False


def _parse_ag_datetime(value: Any) -> Optional[datetime]:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        if timezone.is_naive(value):
            return timezone.make_aware(value, timezone.get_current_timezone())
        return value
    if isinstance(value, date):
        combined = datetime.combine(value, datetime.min.time())
        return timezone.make_aware(combined, timezone.get_current_timezone())

    raw = str(value).strip()
    if not raw:
        return None

    normalized = raw
    if normalized.endswith("Z"):
        normalized = f"{normalized[:-1]}+00:00"

    parsed_dt = parse_datetime(normalized)
    if parsed_dt:
        if timezone.is_naive(parsed_dt):
            return timezone.make_aware(parsed_dt, timezone.get_current_timezone())
        return parsed_dt

    parsed_date = parse_date(raw.split("T")[0].split(" ")[0])
    if parsed_date:
        combined = datetime.combine(parsed_date, datetime.min.time())
        return timezone.make_aware(combined, timezone.get_current_timezone())

    return None


def _parse_ag_date(value: Any) -> Optional[date]:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value

    raw = str(value).strip()
    if not raw:
        return None

    normalized = raw
    if normalized.endswith("Z"):
        normalized = f"{normalized[:-1]}+00:00"

    parsed_dt = parse_datetime(normalized)
    if parsed_dt:
        return parsed_dt.date()

    return parse_date(raw.split("T")[0].split(" ")[0])


# Query params reserved for control-flow (not data filtering)
RESERVED_QUERY_PARAMS = {
    "page",
    "perPage",
    "filter",
    "sort",
    "order",
    "ordering",
    "serializer",
    "as_of",
    "_start",
    "_end",
    "_sort",
    "_order",
    "startRow",
    "endRow",
    "ag_request",
}


SAFE_LOOKUPS = {
    "exact",
    "iexact",
    "contains",
    "icontains",
    "startswith",
    "istartswith",
    "endswith",
    "iendswith",
    "gt",
    "gte",
    "lt",
    "lte",
    "in",
    "range",
    "isnull",
    "date",
    "date__gt",
    "date__gte",
    "date__lt",
    "date__lte",
}


def normalize_field_path(path: str) -> str:
    return (path or "").replace(".", "__")


ResolvedLookup = Tuple[str, str, Any]


def _is_json_field(model_field: Field) -> bool:
    return getattr(model_field, "get_internal_type", lambda: "")() == "JSONField"


def _split_safe_lookup_suffix(parts: list[str]) -> Tuple[list[str], Optional[str]]:
    for size in range(len(parts), 0, -1):
        candidate = "__".join(parts[-size:])
        if candidate in SAFE_LOOKUPS:
            return parts[:-size], candidate
    return parts, None


@lru_cache(maxsize=2048)
def _resolve_lookup(model_class: type[Model], raw_lookup: str) -> Optional[ResolvedLookup]:
    lookup = normalize_field_path(raw_lookup)
    if not lookup:
        return None

    parts = lookup.split("__")
    current_model = model_class
    field_parts: list[str] = []
    model_field = None

    i = 0
    while i < len(parts):
        part = parts[i]
        try:
            field = current_model._meta.get_field(part)
        except FieldDoesNotExist:
            break

        field_parts.append(part)
        model_field = field
        i += 1

        if i < len(parts):
            if getattr(field, "is_relation", False) and getattr(field, "related_model", None):
                current_model = field.related_model
            else:
                break

    if not field_parts or model_field is None:
        return None

    suffix_parts = parts[i:]
    if not suffix_parts:
        field_path = "__".join(field_parts)
        return (field_path, field_path, model_field)

    if _is_json_field(model_field):
        json_path_parts, lookup_suffix = _split_safe_lookup_suffix(suffix_parts)
        if any(not re.fullmatch(r"[A-Za-z0-9_]+", part) for part in json_path_parts):
            return None

        lookup_parts = field_parts + json_path_parts
        if lookup_suffix:
            lookup_parts.append(lookup_suffix)
        return ("__".join(field_parts), "__".join(lookup_parts), model_field)

    suffix = "__".join(suffix_parts)
    if suffix not in SAFE_LOOKUPS:
        return None

    return ("__".join(field_parts), "__".join(field_parts + suffix_parts), model_field)


def _coerce_value(model_field, value):
    if value is None:
        return None

    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"null", "none"}:
            return None
        if lowered in {"true", "false"}:
            return lowered == "true"

    internal = getattr(model_field, "get_internal_type", lambda: "")()

    if internal in {
        "IntegerField",
        "BigIntegerField",
        "SmallIntegerField",
        "PositiveIntegerField",
        "PositiveSmallIntegerField",
        "AutoField",
        "BigAutoField",
    }:
        try:
            return int(value)
        except (TypeError, ValueError):
            return value

    if internal in {"FloatField"}:
        try:
            return float(value)
        except (TypeError, ValueError):
            return value

    if internal in {"DecimalField"}:
        try:
            return Decimal(str(value))
        except Exception:
            return value

    if internal in {"BooleanField", "NullBooleanField"}:
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            lowered = value.strip().lower()
            if lowered in {"1", "true", "t", "yes", "y"}:
                return True
            if lowered in {"0", "false", "f", "no", "n"}:
                return False
        return value

    if internal in {"DateField"}:
        if isinstance(value, str):
            parsed = parse_date(value)
            return parsed or value
        return value

    if internal in {"DateTimeField"}:
        if isinstance(value, str):
            parsed = parse_datetime(value)
            if parsed:
                return parsed
            parsed_date = parse_date(value)
            if parsed_date:
                return datetime.combine(parsed_date, datetime.min.time())
        return value

    if internal in {"JSONField"} and isinstance(value, str):
        try:
            return json.loads(value)
        except Exception:
            return value

    return value


def _build_query_from_values(resolved: ResolvedLookup, values: list[str]) -> Optional[Q]:
    cleaned = [v for v in values if v != ""]
    if not cleaned:
        return None

    _, lookup_path, model_field = resolved

    if lookup_path.endswith("__in"):
        if len(cleaned) == 1 and isinstance(cleaned[0], str) and "," in cleaned[0]:
            items = [x for x in cleaned[0].split(",") if x != ""]
        else:
            items = cleaned
        coerced = [_coerce_value(model_field, v) for v in items]
        return Q(**{lookup_path: coerced})

    if lookup_path.endswith("__range"):
        if len(cleaned) == 1 and "," in cleaned[0]:
            parts = [x.strip() for x in cleaned[0].split(",", 1)]
        elif len(cleaned) >= 2:
            parts = [cleaned[0], cleaned[1]]
        else:
            return None
        coerced = [_coerce_value(model_field, p) for p in parts]
        return Q(**{lookup_path: coerced})

    if len(cleaned) == 1:
        return Q(**{lookup_path: _coerce_value(model_field, cleaned[0])})

    combined = Q()
    for v in cleaned:
        combined |= Q(**{lookup_path: _coerce_value(model_field, v)})
    return combined


def apply_query_param_filters(
    queryset: QuerySet,
    query_params: QueryDict,
    model_class: type[Model],
    reserved_params: Optional[Iterable[str]] = None,
) -> QuerySet:
    reserved = set(RESERVED_QUERY_PARAMS)
    if reserved_params:
        reserved.update(set(reserved_params))

    for raw_key, values in query_params.lists():
        if raw_key in reserved:
            continue

        negated = raw_key.endswith("!")
        key = raw_key[:-1] if negated else raw_key

        resolved = _resolve_lookup(model_class, key)
        if not resolved:
            continue

        q = _build_query_from_values(resolved, values)
        if q is None:
            continue

        queryset = queryset.exclude(q) if negated else queryset.filter(q)

    return queryset


def apply_ordering(
    queryset: QuerySet,
    ordering_param: Optional[str],
    model_class: type[Model],
    extra_allowed: Optional[set[str]] = None,
) -> QuerySet:
    if not ordering_param:
        return queryset

    allowed_extra = extra_allowed or set()
    order_fields: list[str] = []

    for raw_token in [t.strip() for t in ordering_param.split(",") if t.strip()]:
        desc = raw_token.startswith("-")
        token = raw_token[1:] if desc else raw_token
        token = normalize_field_path(token)

        if token in allowed_extra:
            order_fields.append(f"-{token}" if desc else token)
            continue

        resolved = _resolve_lookup(model_class, token)
        if not resolved:
            continue

        field_path, _, _ = resolved
        order_fields.append(f"-{field_path}" if desc else field_path)

    if not order_fields:
        return queryset

    return queryset.order_by(*order_fields)


class CustomPageNumberPagination(PageNumberPagination):
    page_query_param = "page"
    page_size_query_param = "perPage"

    def paginate_queryset(self, queryset, request, view=None):
        per_page = request.query_params.get(self.page_size_query_param)
        if per_page == "-1":
            # Keep pagination response shape, but include all filtered rows.
            self.page_size = max(queryset.count(), 1)

        return super().paginate_queryset(queryset, request, view)


class ListModelEntries(ModelEntryProviderMixin, ListAPIView):
    pagination_class = CustomPageNumberPagination
    # see https://stackoverflow.com/a/40585846
    # We use the UserReadRestrictionFilterBackend for filtering out those instances that the user
    #   does not have access to
    filter_backends = [UserReadRestrictionFilterBackend]
    post_as_read = True
    # permission_classes = [IsAuthenticated, KeycloakUMAPermission]

    def get_queryset(self):
        queryset = super().get_queryset()
        model_class = self.kwargs["model_container"].model_class
        queryset = apply_query_param_filters(queryset, self.request.query_params, model_class)
        queryset = apply_ordering(queryset, self.request.query_params.get("ordering"), model_class)

        return queryset

    def list(self, request, *args, **kwargs):
        # Fast path: when the client only needs the set of primary keys matching
        # the current filters (e.g. AG Grid "select all rows" bulk selection),
        # skip serialization and pagination entirely.
        if _parse_bool(request.query_params.get("pk_only"), default=False):
            queryset = self.filter_queryset(self.get_queryset())
            model_class = self.kwargs["model_container"].model_class
            pk_name = model_class._meta.pk.name
            ids = list(queryset.values_list(pk_name, flat=True))
            return Response({"ids": ids, "count": len(ids)})
        return super().list(request, *args, **kwargs)

    def _normalize_ag_request(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        if isinstance(payload, dict) and isinstance(payload.get("request"), dict):
            return payload.get("request") or {}
        return payload if isinstance(payload, dict) else {}

    def _is_auditlog_flat_leaf_request(self, req: Dict[str, Any]) -> bool:
        if self._ag_model_class._meta.model_name.lower() != "auditlog":
            return False

        row_group_cols = self._parse_columns(req.get("rowGroupCols"), include_agg=False)
        group_keys = list(req.get("groupKeys") or [])
        pivot_mode = _parse_bool(req.get("pivotMode"), default=False)

        return not pivot_mode and (not row_group_cols or len(group_keys) >= len(row_group_cols))

    def post(self, request, *args, **kwargs):
        self._ag_model_class = self.kwargs["model_container"].model_class
        self._ag_field_validity_cache: Dict[str, bool] = {}
        self._ag_model_field_cache: Dict[str, Field] = {}

        payload = request.data or {}
        ag_request = self._normalize_ag_request(payload)

        defer_auditlog_permissions = self._is_auditlog_flat_leaf_request(ag_request)
        queryset = self.get_queryset() if defer_auditlog_permissions else self.filter_queryset(self.get_queryset())
        result = self._execute_ag_grid_request(
            queryset,
            payload,
            req=ag_request,
            defer_auditlog_permissions=defer_auditlog_permissions,
        )
        return Response(result)

    def _execute_ag_grid_request(
        self,
        queryset: QuerySet,
        payload: Dict[str, Any],
        req: Optional[Dict[str, Any]] = None,
        defer_auditlog_permissions: bool = False,
    ) -> Dict[str, Any]:
        req = req or self._normalize_ag_request(payload)

        start_row = _parse_int(req.get("startRow"), 0)
        end_row = _parse_int(req.get("endRow"), start_row + 100)
        if end_row <= start_row:
            end_row = start_row + 100

        row_group_cols = self._parse_columns(req.get("rowGroupCols"), include_agg=False)
        value_cols = self._parse_columns(req.get("valueCols"), include_agg=True)
        pivot_cols = self._parse_columns(req.get("pivotCols"), include_agg=False)
        sort_model = req.get("sortModel") or []
        filter_model = req.get("filterModel") or {}
        group_keys = list(req.get("groupKeys") or [])
        pivot_mode = _parse_bool(req.get("pivotMode"), default=False)

        qs = self._apply_filter_model(queryset, filter_model)
        qs = self._apply_group_key_filters(qs, row_group_cols, group_keys)

        if pivot_mode:
            return self._execute_pivot_mode(
                qs=qs,
                start_row=start_row,
                end_row=end_row,
                row_group_cols=row_group_cols,
                group_keys=group_keys,
                pivot_cols=pivot_cols,
                value_cols=value_cols,
                sort_model=sort_model,
            )

        if row_group_cols and len(group_keys) < len(row_group_cols):
            return self._execute_group_level(
                qs=qs,
                start_row=start_row,
                end_row=end_row,
                row_group_cols=row_group_cols,
                group_keys=group_keys,
                value_cols=value_cols,
                sort_model=sort_model,
            )

        return self._execute_leaf_level(
            qs=qs,
            start_row=start_row,
            end_row=end_row,
            sort_model=sort_model,
            defer_auditlog_permissions=defer_auditlog_permissions,
        )

    def _parse_columns(self, raw_cols: Any, include_agg: bool) -> List[AgColumn]:
        cols: List[AgColumn] = []
        for raw in raw_cols or []:
            if not isinstance(raw, dict):
                continue
            field = normalize_field_path(str(raw.get("field") or raw.get("id") or "")).strip()
            col_id = str(raw.get("id") or field).strip()
            if not field or not col_id:
                continue
            cols.append(
                {
                    "field": field,
                    "id": col_id,
                    "agg_func": str(raw.get("aggFunc") or "").strip() if include_agg else None,
                }
            )
        return cols

    def _apply_filter_model(self, queryset: QuerySet, filter_model: Dict[str, Any]) -> QuerySet:
        if not isinstance(filter_model, dict) or not filter_model:
            return queryset

        combined = Q()

        for raw_field, model in filter_model.items():
            field = normalize_field_path(str(raw_field or "")).strip()
            if not field:
                continue
            if not self._is_valid_field_path(field):
                continue

            query_condition = self._build_filter_q(field, model)
            if query_condition is None:
                continue
            combined &= query_condition

        if not combined.children:
            return queryset

        return queryset.filter(combined)

    def _build_filter_q(self, field: str, model: Any) -> Optional[Q]:
        if model in (None, ""):
            return None

        # Backward compatibility: some clients send scalar values for custom filters.
        if not isinstance(model, dict):
            if isinstance(model, (list, tuple, set)):
                values = [value for value in model if value not in (None, "")]
                if not values:
                    return None
                return Q(**{f"{field}__in": values})
            return Q(**{field: model})

        # New AG model for advanced conditions
        if "operator" in model and isinstance(model.get("conditions"), list):
            conditions = [self._build_filter_q(field, condition) for condition in model.get("conditions") or []]
            conditions = [condition for condition in conditions if condition is not None]
            if not conditions:
                return None
            operator = str(model.get("operator") or "AND").upper()
            query_condition = conditions[0]
            for condition in conditions[1:]:
                query_condition = (query_condition | condition) if operator == "OR" else (query_condition & condition)
            return query_condition

        # Legacy AG model
        if model.get("condition1") and model.get("condition2"):
            condition_1 = self._build_filter_q(field, model.get("condition1"))
            condition_2 = self._build_filter_q(field, model.get("condition2"))
            operator = str(model.get("operator") or "AND").upper()
            if condition_1 is None:
                return condition_2
            if condition_2 is None:
                return condition_1
            return (condition_1 | condition_2) if operator == "OR" else (condition_1 & condition_2)

        has_date_keys = ("dateFrom" in model) or ("dateTo" in model)
        filter_type = str(model.get("filterType") or ("date" if has_date_keys else "text"))

        if filter_type == "set":
            values = model.get("values") or []
            if not isinstance(values, list) or not values:
                return None
            return Q(**{f"{field}__in": values})

        operation_type = str(model.get("type") or model.get("operator") or "contains")

        if filter_type == "text":
            value = model.get("filter")
            # BUG-016 fix: `blank` / `notBlank` are value-less ops —
            # they ask "is this column empty?" and don't carry a
            # ``filter`` payload. The early-return below previously
            # killed them before the per-op dispatch could run, so the
            # AG Grid dropdown items appeared to do nothing. Skip the
            # guard for those two ops; every other op still requires a
            # value.
            if value in (None, "") and operation_type not in ("blank", "notBlank"):
                return None
            value = str(value)
            mapping = {
                "contains": f"{field}__icontains",
                "notContains": f"{field}__icontains",
                "equals": f"{field}",
                "notEqual": f"{field}",
                "startsWith": f"{field}__istartswith",
                "endsWith": f"{field}__iendswith",
                "blank": f"{field}",
                "notBlank": f"{field}",
            }
            lookup = mapping.get(operation_type, f"{field}__icontains")
            if operation_type == "blank":
                return Q(**{f"{field}__isnull": True}) | Q(**{field: ""})
            if operation_type == "notBlank":
                return ~Q(**{f"{field}__isnull": True}) & ~Q(**{field: ""})
            base = Q(**{lookup: value})
            if operation_type in {"notContains", "notEqual"}:
                return ~base
            return base

        if filter_type == "number":
            value = model.get("filter")
            value_to = model.get("filterTo")
            # BUG-016 fix: extend the value-less bypass to ``notBlank``
            # too — previously only ``blank`` was exempted, so the
            # ``notBlank`` op was unreachable on numeric columns.
            if value in (None, "") and operation_type not in ("blank", "notBlank"):
                return None
            mapping = {
                "equals": field,
                "notEqual": field,
                "greaterThan": f"{field}__gt",
                "greaterThanOrEqual": f"{field}__gte",
                "lessThan": f"{field}__lt",
                "lessThanOrEqual": f"{field}__lte",
            }
            if operation_type == "inRange":
                if value in (None, "") or value_to in (None, ""):
                    return None
                return Q(**{f"{field}__gte": value}) & Q(**{f"{field}__lte": value_to})
            if operation_type == "blank":
                return Q(**{f"{field}__isnull": True})
            if operation_type == "notBlank":
                return ~Q(**{f"{field}__isnull": True})
            lookup = mapping.get(operation_type, field)
            base = Q(**{lookup: value})
            if operation_type == "notEqual":
                return ~base
            return base

        if filter_type == "date":
            date_from_raw = model.get("dateFrom") or model.get("filter")
            date_to_raw = model.get("dateTo") or model.get("filterTo")

            # BUG-016 fix: ``notBlank`` is value-less just like
            # ``blank`` — the original guard only exempted ``blank``,
            # so ``notBlank`` on date columns was unreachable.
            if operation_type not in ("blank", "notBlank") and not date_from_raw:
                return None
            if operation_type == "blank":
                return Q(**{f"{field}__isnull": True})
            if operation_type == "notBlank":
                return ~Q(**{f"{field}__isnull": True})

            model_field = self._get_model_field(field)
            is_datetime = getattr(model_field, "get_internal_type", lambda: "")() == "DateTimeField"

            if is_datetime:
                from_dt = _parse_ag_datetime(date_from_raw)
                to_dt = _parse_ag_datetime(date_to_raw)
                if not from_dt and operation_type != "inRange":
                    return None
                if operation_type == "inRange":
                    if not from_dt or not to_dt:
                        return None
                    if _ag_filter_has_time(date_from_raw) or _ag_filter_has_time(date_to_raw):
                        return Q(**{f"{field}__gte": from_dt}) & Q(**{f"{field}__lte": to_dt})
                    return Q(**{f"{field}__date__gte": from_dt.date()}) & Q(**{f"{field}__date__lte": to_dt.date()})

                if operation_type == "equals":
                    if _ag_filter_has_time(date_from_raw):
                        return Q(**{f"{field}__gte": from_dt}) & Q(**{f"{field}__lt": from_dt + timedelta(seconds=1)})
                    return Q(**{f"{field}__date": from_dt.date()})
                if operation_type == "notEqual":
                    if _ag_filter_has_time(date_from_raw):
                        return ~(Q(**{f"{field}__gte": from_dt}) & Q(**{f"{field}__lt": from_dt + timedelta(seconds=1)}))
                    return ~Q(**{f"{field}__date": from_dt.date()})
                if operation_type == "greaterThan":
                    if _ag_filter_has_time(date_from_raw):
                        return Q(**{f"{field}__gt": from_dt})
                    return Q(**{f"{field}__date__gt": from_dt.date()})
                if operation_type == "greaterThanOrEqual":
                    if _ag_filter_has_time(date_from_raw):
                        return Q(**{f"{field}__gte": from_dt})
                    return Q(**{f"{field}__date__gte": from_dt.date()})
                if operation_type == "lessThan":
                    if _ag_filter_has_time(date_from_raw):
                        return Q(**{f"{field}__lt": from_dt})
                    return Q(**{f"{field}__date__lt": from_dt.date()})
                if operation_type == "lessThanOrEqual":
                    if _ag_filter_has_time(date_from_raw):
                        return Q(**{f"{field}__lte": from_dt})
                    return Q(**{f"{field}__date__lte": from_dt.date()})

                return Q(**{f"{field}__date": from_dt.date()})

            from_date = _parse_ag_date(date_from_raw)
            to_date = _parse_ag_date(date_to_raw)
            if not from_date and operation_type != "inRange":
                return None

            if operation_type == "inRange":
                if not from_date or not to_date:
                    return None
                return Q(**{f"{field}__gte": from_date}) & Q(**{f"{field}__lte": to_date})

            mapping = {
                "equals": f"{field}",
                "greaterThan": f"{field}__gt",
                "greaterThanOrEqual": f"{field}__gte",
                "lessThan": f"{field}__lt",
                "lessThanOrEqual": f"{field}__lte",
                "notEqual": f"{field}",
            }
            lookup = mapping.get(operation_type, f"{field}")
            base = Q(**{lookup: from_date})
            if operation_type == "notEqual":
                return ~base
            return base

        return None

    def _apply_group_key_filters(
        self,
        queryset: QuerySet,
        row_group_cols: List[AgColumn],
        group_keys: List[Any],
    ) -> QuerySet:
        if not row_group_cols or not group_keys:
            return queryset

        for index, raw_key in enumerate(group_keys):
            if index >= len(row_group_cols):
                break
            field = str(row_group_cols[index]["field"])
            if not self._is_valid_field_path(field):
                continue

            if raw_key in {"(empty)", "__empty__", "null", None}:
                queryset = queryset.filter(**{f"{field}__isnull": True})
            else:
                queryset = queryset.filter(**{field: raw_key})

        return queryset

    def _execute_group_level(
        self,
        qs: QuerySet,
        start_row: int,
        end_row: int,
        row_group_cols: List[AgColumn],
        group_keys: List[Any],
        value_cols: List[AgColumn],
        sort_model: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        level = len(group_keys)
        group_col = row_group_cols[level]

        annotations = {"__childCount": Count("pk")}
        annotations.update(self._build_value_annotations(value_cols))

        group_field = str(group_col["field"])

        # Defensive validation: when a project overrides
        # ``api_serializers["default"]`` with a custom serializer that
        # exposes a ``SerializerMethodField`` (e.g. ``formatted_name``)
        # or a property with no underlying Django column, the frontend's
        # AG Grid still marks the column ``enableRowGroup: true`` and the
        # user can drag it into the row-group panel. Without this guard
        # ``qs.values(group_field).annotate(...)`` would raise
        # ``FieldError`` and return HTTP 500, which the SSRM datasource
        # surfaces as a blank group label and breaks the grouping UX.
        # Returning an empty group level instead lets AG Grid render
        # gracefully (no groups for this column) and the user can
        # choose a different column to group by.
        if not self._is_valid_field_path(group_field):
            return {
                "rowData": [],
                "rowCount": 0,
            }

        grouped = qs.values(group_field).annotate(**annotations)
        grouped = self._apply_sort_model(grouped, sort_model, allowed_columns={group_field, *annotations.keys()})

        row_count = grouped.count()
        rows = list(grouped[start_row:end_row])

        return {
            "rowData": rows,
            "rowCount": row_count,
        }

    def _execute_leaf_level(
        self,
        qs: QuerySet,
        start_row: int,
        end_row: int,
        sort_model: List[Dict[str, Any]],
        defer_auditlog_permissions: bool = False,
    ) -> Dict[str, Any]:
        qs = self._apply_sort_model(qs, sort_model, allowed_columns=None)

        # Stable default ordering for deterministic paging
        if not qs.query.order_by:
            qs = qs.order_by(self._ag_model_class._meta.pk.name)

        page_size = max(end_row - start_row, 1)

        if defer_auditlog_permissions and self._ag_model_class._meta.model_name.lower() == "auditlog":
            return self._execute_deferred_auditlog_leaf_level(
                qs=qs,
                start_row=start_row,
                page_size=page_size,
            )

        if self._ag_model_class._meta.model_name.lower() == "auditlog":
            qs = qs.select_related("content_type").prefetch_related("status_records")
            rows = list(qs[start_row:end_row + 1])
            has_more = len(rows) > page_size
            if has_more:
                rows = rows[:page_size]
                row_count = None
            else:
                row_count = start_row + len(rows)
            self._annotate_has_calculation_log(rows)
            serializer = self.get_serializer(rows, many=True)
            return {
                "rowData": serializer.data,
                "rowCount": row_count,
            }

        row_count = qs.count()
        page_qs = qs[start_row:end_row]
        serializer = self.get_serializer(page_qs, many=True)
        return {
            "rowData": serializer.data,
            "rowCount": row_count,
        }

    def _execute_deferred_auditlog_leaf_level(
        self,
        qs: QuerySet,
        start_row: int,
        page_size: int,
    ) -> Dict[str, Any]:
        visibility_backend = UserReadRestrictionFilterBackend()
        handled_resources, allowed_q = visibility_backend._build_auditlog_db_visibility_filters(
            getattr(self, "request", None)
        )

        if handled_resources:
            residual_q = ~Q(resource__in=handled_resources)
            if allowed_q is None:
                candidate_qs = qs.filter(residual_q)
            else:
                candidate_qs = qs.filter(allowed_q | residual_q)
        else:
            candidate_qs = qs

        candidate_qs = candidate_qs.select_related("content_type").only(
            "id",
            "date",
            "author",
            "resource",
            "action",
            "payload",
            "calculation_id",
            "content_type_id",
            "object_id",
            "content_type__app_label",
            "content_type__model",
        )

        request = getattr(self, "request", None)
        base_user_context = UserContext.from_request_base(request) if request is not None else None

        rows = []
        visible_offset = 0
        has_more = False

        for row in candidate_qs.iterator(
            chunk_size=UserReadRestrictionFilterBackend.ITERATOR_CHUNK_SIZE
        ):
            if row.resource not in handled_resources:
                if not can_read_from_payload(
                    request,
                    row,
                    base_user_context=base_user_context,
                ):
                    continue

            if visible_offset < start_row:
                visible_offset += 1
                continue

            rows.append(row)
            visible_offset += 1

            if len(rows) > page_size:
                has_more = True
                rows = rows[:page_size]
                break

        from django.db.models import prefetch_related_objects
        prefetch_related_objects(rows, "status_records")
        self._annotate_has_calculation_log(rows)
        serializer = self.get_serializer(rows, many=True)
        return {
            "rowData": serializer.data,
            "rowCount": None if has_more else (start_row + len(rows)),
        }

    @staticmethod
    def _annotate_has_calculation_log(rows):
        """Batch-set ``_has_calculation_log`` on a list of AuditLog instances."""
        from lex.audit_logging.models.CalculationLog import CalculationLog

        calc_ids = {r.calculation_id for r in rows if r.calculation_id}
        if calc_ids:
            existing = set(
                CalculationLog.objects.filter(calculationId__in=calc_ids)
                .values_list("calculationId", flat=True)
                .distinct()
            )
            for r in rows:
                r._has_calculation_log = (r.calculation_id in existing) if r.calculation_id else False
        else:
            for r in rows:
                r._has_calculation_log = False

    def _execute_pivot_mode(
        self,
        qs: QuerySet,
        start_row: int,
        end_row: int,
        row_group_cols: List[AgColumn],
        group_keys: List[Any],
        pivot_cols: List[AgColumn],
        value_cols: List[AgColumn],
        sort_model: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        annotations, pivot_fields = self._build_pivot_annotations(qs, pivot_cols, value_cols)

        if row_group_cols and len(group_keys) < len(row_group_cols):
            group_col = row_group_cols[len(group_keys)]
            group_field = str(group_col["field"])
            # Same defensive guard as ``_execute_group_level``: skip the
            # ORM ``.values(group_field)`` call for non-DB-backed
            # serializer fields (e.g. ``SerializerMethodField`` exposed
            # by an overridden ``api_serializers["default"]``) to avoid a
            # ``FieldError`` 500 that the FE would render as broken
            # pivot rows.
            if not self._is_valid_field_path(group_field):
                return {
                    "rowData": [],
                    "rowCount": 0,
                    "pivotResultFields": pivot_fields,
                }
            grouped = qs.values(group_field).annotate(__childCount=Count("pk"), **annotations)
            grouped = self._apply_sort_model(
                grouped,
                sort_model,
                allowed_columns={group_field, "__childCount", *annotations.keys()},
            )
            row_count = grouped.count()
            rows = list(grouped[start_row:end_row])
            return {
                "rowData": rows,
                "rowCount": row_count,
                "pivotResultFields": pivot_fields,
            }

        # Deepest level (or no row groups): aggregate into a single row
        aggregated = qs.aggregate(**annotations) if annotations else {}
        row_data = [aggregated] if aggregated else []

        return {
            "rowData": row_data,
            "rowCount": len(row_data),
            "pivotResultFields": pivot_fields,
        }

    def _build_value_annotations(self, value_cols: List[AgColumn]) -> Dict[str, Any]:
        annotations: Dict[str, Any] = {}
        for col in value_cols:
            field = str(col["field"])
            col_id = str(col["id"])
            if not self._is_valid_field_path(field):
                continue
            agg_func = (col.get("agg_func") or "sum").strip() or "sum"
            expr = self._build_agg_expression(field, agg_func)
            if expr is None:
                continue
            annotations[col_id] = expr
        return annotations

    def _build_pivot_annotations(
        self,
        qs: QuerySet,
        pivot_cols: List[AgColumn],
        value_cols: List[AgColumn],
    ) -> Tuple[Dict[str, Any], List[str]]:
        if not value_cols:
            return {}, []

        if not pivot_cols:
            annotations = self._build_value_annotations(value_cols)
            return annotations, list(annotations.keys())

        pivot_fields = [str(c["field"]) for c in pivot_cols if self._is_valid_field_path(str(c["field"]))]
        if not pivot_fields:
            annotations = self._build_value_annotations(value_cols)
            return annotations, list(annotations.keys())

        distinct_qs = qs.values(*pivot_fields).order_by(*pivot_fields).distinct()
        combos = list(distinct_qs[:MAX_PIVOT_COMBINATIONS])

        annotations: Dict[str, Any] = {}
        result_fields: List[str] = []

        for combo in combos:
            cond_kwargs = {field: combo[field] for field in pivot_fields}
            pivot_key = "__".join(_safe_key(combo[field]) for field in pivot_fields)

            for value_col in value_cols:
                field = str(value_col["field"])
                col_id = str(value_col["id"])
                if not self._is_valid_field_path(field):
                    continue
                agg_func = (value_col.get("agg_func") or "sum").strip() or "sum"
                alias = f"{pivot_key}__{_safe_key(col_id)}__{_safe_key(agg_func)}"
                expr = self._build_conditional_agg_expression(field, agg_func, cond_kwargs)
                if expr is None:
                    continue
                annotations[alias] = expr
                result_fields.append(alias)

        return annotations, result_fields

    def _build_agg_expression(self, field: str, agg_func: str):
        agg = agg_func.lower()
        if agg == "sum":
            return Sum(field)
        if agg == "avg":
            return Avg(field)
        if agg == "min":
            return Min(field)
        if agg == "max":
            return Max(field)
        if agg == "count":
            return Count(field)
        if agg in {"distinctcount", "countdistinct"}:
            return Count(field, distinct=True)
        if agg == "first":
            return Min(field)
        if agg == "last":
            return Max(field)
        return None

    def _build_conditional_agg_expression(self, field: str, agg_func: str, when_kwargs: Dict[str, Any]):
        agg = agg_func.lower()

        if agg in {"count", "distinctcount", "countdistinct"}:
            case_expr = Case(When(**when_kwargs, then=F(field)), default=None)
            return Count(case_expr, distinct=agg in {"distinctcount", "countdistinct"})

        output_field = self._get_model_field(field)
        case_expr = Case(
            When(**when_kwargs, then=F(field)),
            default=None,
            output_field=output_field,
        )

        if agg == "sum":
            return Sum(case_expr)
        if agg == "avg":
            return Avg(case_expr)
        if agg == "min":
            return Min(case_expr)
        if agg == "max":
            return Max(case_expr)
        if agg == "first":
            return Min(case_expr)
        if agg == "last":
            return Max(case_expr)
        return None

    def _apply_sort_model(
        self,
        queryset: QuerySet,
        sort_model: List[Dict[str, Any]],
        allowed_columns: Optional[set[str]],
    ) -> QuerySet:
        if not isinstance(sort_model, list) or not sort_model:
            return queryset

        order_by: List[str] = []

        for sort in sort_model:
            if not isinstance(sort, dict):
                continue

            col_id = normalize_field_path(str(sort.get("colId") or "")).strip()
            if not col_id:
                continue

            direction = "-" if str(sort.get("sort") or "asc").lower() == "desc" else ""

            if allowed_columns is not None:
                if col_id in allowed_columns:
                    order_by.append(f"{direction}{col_id}")
                continue

            # Leaf-row sorting: only allow valid model paths
            if self._is_valid_field_path(col_id):
                order_by.append(f"{direction}{col_id}")

        if not order_by:
            return queryset

        return queryset.order_by(*order_by)

    def _get_model_field(self, field_path: str):
        normalized = normalize_field_path(field_path).strip()
        cached = self._ag_model_field_cache.get(normalized)
        if cached is not None:
            return cached

        parts = normalized.split("__")
        current_model = self._ag_model_class
        field: Optional[Field] = None

        for part in parts:
            try:
                field = current_model._meta.get_field(part)
            except FieldDoesNotExist:
                fallback = IntegerField()
                self._ag_model_field_cache[normalized] = fallback
                return fallback

            if getattr(field, "is_relation", False) and getattr(field, "related_model", None):
                current_model = field.related_model

        resolved = field or IntegerField()
        self._ag_model_field_cache[normalized] = resolved
        return resolved

    def _is_valid_field_path(self, field_path: str) -> bool:
        normalized = normalize_field_path(field_path).strip()
        cached = self._ag_field_validity_cache.get(normalized)
        if cached is not None:
            return cached

        parts = normalized.split("__")
        current_model = self._ag_model_class

        for i, part in enumerate(parts):
            try:
                field = current_model._meta.get_field(part)
            except FieldDoesNotExist:
                self._ag_field_validity_cache[normalized] = False
                return False

            if i < len(parts) - 1:
                if not getattr(field, "is_relation", False) or not getattr(field, "related_model", None):
                    self._ag_field_validity_cache[normalized] = False
                    return False
                current_model = field.related_model

        self._ag_field_validity_cache[normalized] = True
        return True
