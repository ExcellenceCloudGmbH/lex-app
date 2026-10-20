"""A list column's distinct values with row counts, for value-list filters.

The grid's value-list filters ask this endpoint what a column holds. The answer
must match what the grid itself would show, so the view inherits the list view's
queryset (``as_of`` included), its read filter and its filter translation, and
applies every filter EXCEPT the column's own: a column's list shows what the
other filters leave, so a ticked value can always be unticked and others added.

The value list must never show a value the user could not see in the grid
(design spec §3.7 / §8): not a row the read filter hides, not a field value a
per-row ``permission_read`` / ``can_read`` hides, and not a foreign key's name
resolved from a row the user cannot read.
"""

from __future__ import annotations

import logging
from collections import Counter
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

from django.conf import settings
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db.models import BooleanField, Count, IntegerField, Model, QuerySet
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response

from lex.api.serializers.base_serializers import LexSerializer, _get_capabilities, _resolve_history_source_model
from lex.api.utils.filterable_fields import is_plain_text_field, value_list_field
from lex.api.views.model_entries.List import ListModelEntries, normalize_field_path
from lex.core.models.LexModel import LexModel

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


def _capability_target(model_class):
    """The class whose ``permission_read`` / ``can_read`` decide this model's
    masking -- ``model_class`` itself, or the root tracked model when
    ``model_class`` is a History / MetaHistory wrapper.

    Reuses :func:`_resolve_history_source_model`, which walks the WHOLE
    ``instance_type`` chain (not just one level): a meta-history class's own
    ``instance_type`` is its historical class, not the root tracked model,
    so a single-level walk stops one short and resolves to a class that
    itself declares neither method -- making ``uses_default_read_permission``
    wrongly answer True (database path) for a model whose ``can_read`` hides
    fields per row. Both the read filter and the serializer unwrap all the
    way to the root tracked model, so this must too.
    """
    return _resolve_history_source_model(model_class) or model_class


def uses_default_read_permission(model_class: type[Model]) -> bool:
    """True when every readable row shows every field, so a database count is exact.

    The read filter has already removed the rows the user may not see; the
    question is whether a remaining row can still hide a field. It cannot when
    the target -- the model itself, or the root tracked model of a History /
    MetaHistory container (see ``_capability_target``) -- declares neither
    ``permission_read`` nor ``can_read``:

    * a plain Django model has neither method: the read filter allows every
      row and the serializer shows every field;
    * a LexModel that keeps BOTH LexModel defaults (checked by identity) goes
      through the Keycloak UMA read filter, which drops unreadable rows in the
      database (a record-level grant admits that record only), and the default
      ``permission_read`` is all-or-nothing, so a readable row shows every
      field. Overriding either method disqualifies it: ``to_representation``
      asks ``can_read`` first, and either one can hide fields per row.

    Every other model is counted row by row.
    """
    target = _capability_target(model_class)

    if (
        isinstance(target, type)
        and issubclass(target, LexModel)
        and getattr(target, "permission_read", None) is LexModel.permission_read
        and getattr(target, "can_read", None) is LexModel.can_read
    ):
        return True

    caps = _get_capabilities(target)
    return not caps["has_permission_read"] and not caps["has_can_read"]


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
        serializer = self.get_serializer()
        model_field = self._filterable_field(serializer, field)
        is_plain_text = is_plain_text_field(model_field)
        is_relation = bool(
            getattr(model_field, "is_relation", False) and getattr(model_field, "related_model", None)
        )

        filter_model = body.get("filterModel") or {}
        if not isinstance(filter_model, dict):
            raise ValidationError({"filterModel": "must be an object"})
        other_filters = {
            key: value
            for key, value in filter_model.items()
            if normalize_field_path(str(key or "")).strip() != field
        }

        raw_selected = body.get("selected") or []
        if not isinstance(raw_selected, list):
            raise ValidationError({"selected": "must be a list"})
        selected_keys: List[Optional[str]] = []
        for raw in raw_selected[:MAX_LIMIT]:
            key = value_key(raw)
            if is_plain_text and key == "":
                key = None
            selected_keys.append(key)

        search = str(body.get("search") or "").strip()
        limit = _bounded_limit(body.get("limit", DEFAULT_LIMIT))

        # The read filter and as_of, nothing else -- the base every other
        # filter (and the ticked-foreign-key visibility probe below) builds on.
        base_queryset = self.filter_queryset(self.get_queryset())
        queryset = self._apply_filter_model(base_queryset, other_filters)
        searched_in_database = bool(search) and is_plain_text
        if searched_in_database:
            queryset = queryset.filter(**{f"{field}__icontains": search})

        # A serializer that is not a LexSerializer (e.g. auth.User's) applies
        # no per-row field filter at all, so the database aggregate is exact
        # regardless of permission_read / can_read.
        use_database = not isinstance(serializer, LexSerializer) or uses_default_read_permission(queryset.model)
        total_skipped = 0
        if use_database:
            counts, truncated = self._count_in_database(queryset, field)
        else:
            counts, truncated, skipped = self._count_per_row(request, queryset, model_field, serializer)
            total_skipped += skipped

        if is_plain_text:
            counts = self._fold_blank(counts)

        visible_keys = set(counts)
        ticked = {key for key in selected_keys if key is not None}
        missing = ticked - visible_keys
        if missing and is_relation:
            # A foreign key's name is sensitive: resolve it only for ticked
            # keys a readable row actually carries, checked the same way the
            # real counts were (database or per-row), ignoring every OTHER
            # filter -- the question here is visibility, not the current
            # facet. Every other missing key keeps its own key as its label.
            # A key that cannot even convert to the target field's type
            # (stale client state, not a live user-typed filter) can never
            # match a row either way -- probe only the ones that could, so
            # one bad ticked key never turns into a 400 for the whole list.
            target_to_python = model_field.target_field.to_python
            convertible = []
            for key in missing:
                try:
                    target_to_python(key)
                except (DjangoValidationError, TypeError, ValueError):
                    continue
                convertible.append(key)
            if convertible:
                confirmed, probe_skipped = self._confirm_visible(
                    request, base_queryset, field, model_field, sorted(convertible), use_database, serializer,
                )
                total_skipped += probe_skipped
                missing = missing & confirmed
            else:
                missing = set()

        if total_skipped:
            logger.warning(
                "filter_values: readable_field_names raised for %d row(s) while listing %r; skipped",
                total_skipped, field,
            )

        labels = self._labels(model_field, visible_keys | missing)
        items = [
            {"key": key, "label": labels.get(key, key or BLANK_LABEL), "count": count}
            for key, count in counts.items()
        ]
        if search and not searched_in_database:
            needle = search.casefold()
            items = [item for item in items if needle in item["label"].casefold()]

        # Blank last always. Ties broken by key: sort by key first (a stable
        # sort keeps this as the secondary order), then by the column's real
        # ordering -- numeric for a plain integer column, label otherwise --
        # so both counting paths produce the same order for the same data.
        # The tie-break itself is numeric whenever the key IS an integer
        # (a foreign key's primary key, or an integer column) -- a string
        # comparison disagrees with the numeric one across a digit boundary
        # (e.g. "10" sorts before "9"), and a foreign key's keys are
        # auto-incrementing integers even though the column itself is a
        # relation, not an IntegerField.
        tie_break_numeric = is_relation or isinstance(model_field, IntegerField)
        if tie_break_numeric:
            items.sort(key=lambda item: (item["key"] is None, _safe_int(item["key"])))
        else:
            items.sort(key=lambda item: (item["key"] is None, "" if item["key"] is None else str(item["key"])))
        is_numeric_order = isinstance(model_field, IntegerField) and not getattr(model_field, "choices", None)
        if is_numeric_order:
            items.sort(key=lambda item: (item["key"] is None, _safe_int(item["key"])))
        else:
            items.sort(key=lambda item: (item["key"] is None, item["label"].casefold()))

        truncated = truncated or len(items) > limit
        items = items[:limit]
        shown = {item["key"] for item in items}
        needle = search.casefold() if search else None
        for key in selected_keys:
            if key in shown:
                continue
            label = labels.get(key, key or BLANK_LABEL)
            if needle is not None and needle not in label.casefold():
                continue
            items.append({"key": key, "label": label, "count": counts.get(key, 0)})
            shown.add(key)

        return Response({"values": items, "truncated": truncated})

    # -- which column ------------------------------------------------------

    def _filterable_field(self, serializer, field: str):
        """The model field behind ``field``, or a 400.

        Delegates to :func:`value_list_field` -- the one place that decides
        which columns a value list can cover, shared with the fields endpoint's
        ``value_list`` flag -- and raises the single generic refusal when
        it returns ``None``, so the response never says which fields exist. A
        field the caller may not filter by (``LEX_SEC_FILTERS``) gets the same
        refusal: the list treats it as a field that does not exist.
        """
        model_field = value_list_field(serializer, self._ag_model_class, field, request=self.request)
        if model_field is None:
            raise ValidationError({"field": f"{field!r} is not filterable."})
        return model_field

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
        self, request, queryset: QuerySet, model_field, serializer
    ) -> Tuple[Dict[Optional[str], int], bool, int]:
        """Count only rows whose ``readable_field_names`` includes this field.

        ``serializer`` is the SAME instance used to resolve ``field`` and
        must be a :class:`LexSerializer` (``post`` only takes this path when
        it is). ``readable_field_names`` is the serializer's own per-record
        read decision, so a row counts exactly when the grid would show this
        field on it: the serializer caches one base context for the whole
        scan (not one ``user.groups`` query per row) and reuses what the read
        filter already decided for this request. Returns the rows skipped by
        a raised check too, so a caller that makes more than one pass (the
        main count and the ticked-foreign-key visibility probe) can log one
        combined warning instead of one per pass.
        """
        name = model_field.name
        attname = model_field.attname
        max_rows = row_scan_limit()
        counts: Counter = Counter()
        truncated = False
        skipped = 0

        for scanned, row in enumerate(queryset.order_by().iterator(chunk_size=ROW_CHUNK_SIZE), start=1):
            if scanned > max_rows:
                truncated = True
                break
            try:
                visible = serializer.readable_field_names(row, request)
            except Exception:
                skipped += 1
                continue
            if name not in visible:
                continue
            counts[value_key(getattr(row, attname))] += 1

        limit = scan_limit()
        if len(counts) > limit:
            truncated = True
            counts = Counter(dict(counts.most_common(limit)))
        return dict(counts), truncated, skipped

    def _confirm_visible(
        self,
        request,
        base_queryset: QuerySet,
        field: str,
        model_field,
        candidate_keys: list,
        use_database: bool,
        serializer,
    ) -> Tuple[Set[Optional[str]], int]:
        """Which of ``candidate_keys`` a row the user may read actually carries,
        and how many rows this probe had to skip (for the caller's combined
        warning)."""
        probe_queryset = self._apply_filter_model(
            base_queryset, {field: {"filterType": "set", "values": candidate_keys}},
        )
        if use_database:
            probe_counts, _ = self._count_in_database(probe_queryset, field)
            return set(probe_counts), 0
        probe_counts, _, skipped = self._count_per_row(request, probe_queryset, model_field, serializer)
        return set(probe_counts), skipped

    @staticmethod
    def _fold_blank(counts: Dict[Optional[str], int]) -> Dict[Optional[str], int]:
        """On a plain text column a stored ``""`` is blank too -- fold its
        count into the ``None`` bucket so the grid shows one "(Blank)" entry
        instead of two indistinguishable ones."""
        if "" not in counts:
            return counts
        folded = dict(counts)
        folded[None] = folded.get(None, 0) + folded.pop("")
        return folded

    # -- labels ------------------------------------------------------------

    def _labels(self, model_field, keys: Iterable[Optional[str]]) -> Dict[Optional[str], str]:
        keys = set(keys)
        labels: Dict[Optional[str], str] = {}
        if None in keys:
            labels[None] = BLANK_LABEL
        present = [key for key in keys if key is not None]

        if getattr(model_field, "is_relation", False) and getattr(model_field, "related_model", None):
            # A foreign key stores its target field's value -- the primary
            # key, or the column ``to_field`` names -- so the related rows are
            # found and keyed by that field, never by a primary key that
            # merely equals the stored value.
            target_field = model_field.target_field
            target_values = []
            for key in present:
                try:
                    target_values.append(target_field.to_python(key))
                except (DjangoValidationError, TypeError, ValueError):
                    continue
            related = model_field.related_model._default_manager.filter(
                **{f"{target_field.name}__in": target_values}
            )
            for obj in related:
                key = value_key(getattr(obj, target_field.attname))
                try:
                    labels[key] = str(obj)
                except Exception:
                    # A __str__ that raises must not fail the whole list;
                    # the key stays its own label (set below).
                    continue
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


def _safe_int(key: Any) -> int:
    try:
        return int(key)
    except (TypeError, ValueError):
        return 0
