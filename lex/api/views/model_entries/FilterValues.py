"""A list column's distinct values with row counts, for value-list filters.

The grid's value-list filters ask this endpoint what a column holds. The answer
must match what the grid itself would show, so the view inherits the list view's
queryset (``as_of`` included), its read filter and its filter translation, and
applies every filter EXCEPT the column's own: a column's list shows what the
other filters leave, so a ticked value can always be unticked and others added.
"""

from __future__ import annotations

import logging
from collections import Counter
from typing import Any, Dict, Iterable, List, Optional, Tuple

from django.conf import settings
from django.core.exceptions import FieldDoesNotExist
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db.models import BooleanField, CharField, Count, Model, QuerySet, TextField
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response

from lex.api.serializers.base_serializers import LexSerializer
from lex.api.utils.filterable_fields import is_value_list_field
from lex.api.views.model_entries.List import ListModelEntries, normalize_field_path
from lex.core.models.LexModel import LexModel, UserContext

logger = logging.getLogger(__name__)

DEFAULT_LIMIT = 200
MAX_LIMIT = 1000
BLANK_LABEL = "(Blank)"
TRUE_LABEL = "Yes"
FALSE_LABEL = "No"
ROW_CHUNK_SIZE = 2000


def scan_limit() -> int:
    """Distinct values aggregated before a list is cut."""
    return int(getattr(settings, "LEX_FILTER_VALUES_SCAN_LIMIT", 5000))


def row_scan_limit() -> int:
    """Rows a per-row permission count may read before it is cut."""
    return int(getattr(settings, "LEX_FILTER_VALUES_ROW_SCAN_LIMIT", 50000))


def value_key(value: Any) -> Optional[str]:
    """The wire key for a stored value: what a value-list filter sends back."""
    if value is None:
        return None
    if isinstance(value, bool):
        return "true" if value else "false"
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def uses_default_read_permission(model_class: type[Model]) -> bool:
    """True when reads are all-or-nothing per row, so a database count is exact."""
    target = model_class
    if not (isinstance(target, type) and issubclass(target, LexModel)):
        target = getattr(model_class, "instance_type", None)
    if not (isinstance(target, type) and issubclass(target, LexModel)):
        return False
    return getattr(target, "permission_read", None) is LexModel.permission_read


def _bounded_limit(raw: Any) -> int:
    try:
        limit = int(raw)
    except (TypeError, ValueError):
        return DEFAULT_LIMIT
    return max(1, min(limit, MAX_LIMIT))


class FilterValues(ListModelEntries):
    """``POST api/model_entries/<model>/filter_values``."""

    # The list view's GET would otherwise answer here with rows.
    http_method_names = ["post", "options"]

    def post(self, request, *args, **kwargs):
        self._ag_model_class = self.kwargs["model_container"].model_class
        self._ag_field_validity_cache: Dict[str, bool] = {}
        self._ag_model_field_cache: Dict[str, Any] = {}

        body = request.data if isinstance(request.data, dict) else {}
        field = normalize_field_path(str(body.get("field") or "")).strip()
        model_field = self._filterable_field(field)

        filter_model = body.get("filterModel") or {}
        if not isinstance(filter_model, dict):
            raise ValidationError({"filterModel": "must be an object"})
        other_filters = {
            key: value
            for key, value in filter_model.items()
            if normalize_field_path(str(key or "")).strip() != field
        }
        selected = body.get("selected") or []
        if not isinstance(selected, list):
            raise ValidationError({"selected": "must be a list"})
        selected_keys: List[Optional[str]] = [None if key is None else str(key) for key in selected]
        search = str(body.get("search") or "").strip()
        limit = _bounded_limit(body.get("limit", DEFAULT_LIMIT))

        queryset = self.filter_queryset(self.get_queryset())
        queryset = self._apply_filter_model(queryset, other_filters)
        searched_in_database = bool(search) and self._is_plain_text(model_field)
        if searched_in_database:
            queryset = queryset.filter(**{f"{field}__icontains": search})

        if uses_default_read_permission(queryset.model):
            counts, truncated = self._count_in_database(queryset, field)
        else:
            counts, truncated = self._count_per_row(request, queryset, model_field)

        labels = self._labels(model_field, set(counts) | set(selected_keys))
        items = [
            {"key": key, "label": labels.get(key, key or BLANK_LABEL), "count": count}
            for key, count in counts.items()
        ]
        if search and not searched_in_database:
            needle = search.casefold()
            items = [item for item in items if needle in item["label"].casefold()]
        items.sort(key=lambda item: (item["key"] is None, item["label"].casefold()))

        truncated = truncated or len(items) > limit
        items = items[:limit]
        shown = {item["key"] for item in items}
        for key in selected_keys:
            if key not in shown:
                items.append(
                    {"key": key, "label": labels.get(key, key or BLANK_LABEL), "count": counts.get(key, 0)}
                )
                shown.add(key)

        return Response({"values": items, "truncated": truncated})

    # -- which column ------------------------------------------------------

    def _filterable_field(self, field: str):
        """The model field behind ``field``, or a 400.

        It must be a field of the requested serializer -- a column the user can
        see in this list -- and a real column or foreign key on the model. One
        answer for every refusal, so the response never says which fields exist.
        """
        refusal = ValidationError({"field": f"{field!r} is not filterable."})
        if not field or "__" in field:
            raise refusal
        if field not in self.get_serializer().fields:
            raise refusal
        try:
            model_field = self._ag_model_class._meta.get_field(field)
        except FieldDoesNotExist:
            raise refusal
        if not is_value_list_field(model_field):
            raise refusal
        return model_field

    @staticmethod
    def _is_plain_text(model_field) -> bool:
        return isinstance(model_field, (CharField, TextField)) and not model_field.choices

    # -- counting ----------------------------------------------------------

    def _count_in_database(
        self, queryset: QuerySet, field: str
    ) -> Tuple[Dict[Optional[str], int], bool]:
        limit = scan_limit()
        grouped = (
            queryset.order_by()
            .values(field)
            .annotate(value_count=Count("pk"))
            .order_by("-value_count", field)
        )
        rows = list(grouped[: limit + 1])
        counts: Dict[Optional[str], int] = {}
        for row in rows[:limit]:
            key = value_key(row[field])
            counts[key] = counts.get(key, 0) + row["value_count"]
        return counts, len(rows) > limit

    def _count_per_row(
        self, request, queryset: QuerySet, model_field
    ) -> Tuple[Dict[Optional[str], int], bool]:
        """Count only rows whose ``permission_read`` lets this user read the field."""
        base_context = UserContext.from_request_base(request)
        name = model_field.name
        attname = model_field.attname
        max_rows = row_scan_limit()
        counts: Counter = Counter()
        truncated = False

        for scanned, row in enumerate(queryset.order_by().iterator(chunk_size=ROW_CHUNK_SIZE), start=1):
            if scanned > max_rows:
                truncated = True
                break
            target = row if isinstance(row, LexModel) else LexSerializer._unwrap_instance(row)
            try:
                result = target.permission_read(base_context.with_instance(request, target))
            except Exception:
                logger.warning("permission_read raised while listing %s values; row skipped", name, exc_info=True)
                continue
            if not result.allowed or name not in result.get_fields({name}):
                continue
            counts[value_key(getattr(row, attname))] += 1

        limit = scan_limit()
        if len(counts) > limit:
            truncated = True
            counts = Counter(dict(counts.most_common(limit)))
        return dict(counts), truncated

    # -- labels ------------------------------------------------------------

    def _labels(self, model_field, keys: Iterable[Optional[str]]) -> Dict[Optional[str], str]:
        keys = set(keys)
        labels: Dict[Optional[str], str] = {}
        if None in keys:
            labels[None] = BLANK_LABEL
        present = [key for key in keys if key is not None]

        if getattr(model_field, "is_relation", False) and getattr(model_field, "related_model", None):
            primary_keys = []
            for key in present:
                try:
                    primary_keys.append(model_field.target_field.to_python(key))
                except (DjangoValidationError, TypeError, ValueError):
                    continue
            related = model_field.related_model._default_manager.filter(pk__in=primary_keys)
            for obj in related:
                labels[value_key(obj.pk)] = str(obj)
        elif isinstance(model_field, BooleanField):
            for key in present:
                labels[key] = TRUE_LABEL if key == "true" else FALSE_LABEL
        else:
            choices = {value_key(value): str(label) for value, label in (model_field.flatchoices or [])}
            for key in present:
                labels[key] = choices.get(key, key)

        for key in present:
            labels.setdefault(key, key)
        return labels
