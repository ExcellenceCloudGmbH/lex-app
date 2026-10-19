"""Which model fields a value-list filter can list.

One answer for the endpoint that lists values and for the ``filter_bar`` list
option, so the bar can never name a column the endpoint would refuse.

The serializer layer is imported inside the functions that use it, never at
module level: ``base_serializers`` imports this module while it loads, so an
import back into ``lex.api.serializers`` here would fail on a partially
initialised module whenever this module happens to be imported first.
"""

from __future__ import annotations

from typing import Any, Optional

from django.core.exceptions import FieldDoesNotExist
from django.db.models import BooleanField, CharField, DateField, DateTimeField, IntegerField, TextField
from rest_framework import serializers

# Framework-private. Marks a ``to_representation`` that leaves every field's
# value as its field class rendered it. Only the framework's own ``id``
# wrapper (``_wrap_custom_serializer``) ever sets this -- it is not a public
# opt-in, and no project serializer should set or rely on it.
_KEEPS_FIELD_VALUES = "__lex_keeps_field_values"


def is_plain_text_field(model_field) -> bool:
    """A ``CharField`` or ``TextField`` (subclasses included) with no ``choices``.

    Used both to decide whether a search runs in the database and to decide
    whether an empty string on this column means the same thing as blank.
    """
    return isinstance(model_field, (CharField, TextField)) and not model_field.choices


def is_value_list_field(model_field) -> bool:
    """True for the column types the design lists as getting a value list.

    An allowlist, not a denylist: a column qualifies only by being one of a
    known-safe shape. Long text (``TextField``), decimal, float, date-time,
    binary, UUID, JSON, file and many-to-many columns are refused by
    omission, not by name, so a new Django field type is refused until
    someone decides it belongs here. "Text" in the design's table means
    ``CharField``: a ``TextField``'s values are near-unique bodies, so a list
    of them is useless and costly, and the column keeps its text condition.
    A credential-shaped name (``secret``, ``api_key``, a ``*_token`` suffix,
    ...) is refused whatever its type -- the value list must never offer a
    stored secret as a choice, even behind a custom serializer that
    publishes the column.
    """
    from lex.api.serializers.sensitive_fields import is_sensitive_field_name  # see module docstring

    if not getattr(model_field, "concrete", False) or getattr(model_field, "many_to_many", False):
        return False
    if is_sensitive_field_name(getattr(model_field, "name", "")):
        return False

    # A column with choices gets a value list regardless of its underlying
    # type -- the design's §1 table lists this first and unconditionally.
    if getattr(model_field, "choices", None):
        return True

    if getattr(model_field, "is_relation", False) and getattr(model_field, "related_model", None):
        return bool(getattr(model_field, "many_to_one", False) or getattr(model_field, "one_to_one", False))

    if isinstance(model_field, BooleanField):
        return True
    if isinstance(model_field, CharField):
        return True
    # DateTimeField subclasses DateField -- every value is an instant, so it
    # must be excluded explicitly before the DateField check below.
    if isinstance(model_field, DateTimeField):
        return False
    if isinstance(model_field, DateField):
        return True
    if isinstance(model_field, IntegerField):
        return True

    return False


def renders_rows_as_stored(serializer_class) -> bool:
    """False when ``serializer_class`` rewrites its rows in a ``to_representation`` of its own.

    DRF's ``to_representation`` renders each field through its field class, and
    ``LexSerializer``'s only removes what the read permission hides. Any other
    definition anywhere in the class hierarchy can change or blank any column on
    any row, so the grid may show something other than the stored value for
    every one of the serializer's columns. A wrapped custom serializer
    (``_wrap_custom_serializer``) puts ``LexSerializer`` ahead of the custom
    class, so the whole hierarchy is walked, not just the first definition
    found; the framework's own wrapper that only re-adds ``id`` carries the
    framework-private ``_KEEPS_FIELD_VALUES`` mark.
    """
    from lex.api.serializers.base_serializers import LexSerializer  # see module docstring

    for klass in getattr(serializer_class, "__mro__", ()):
        method = vars(klass).get("to_representation")
        if method is None or klass is LexSerializer or getattr(method, _KEEPS_FIELD_VALUES, False):
            continue
        if not getattr(method, "__module__", "").startswith("rest_framework."):
            return False
    return True


def model_offers_value_lists(model_class) -> bool:
    """False when the model opts out with ``lex_value_lists = False``.

    Checked on the model and on the tracked model a History / MetaHistory
    container records, so an opted-out model's history tables are covered
    too.
    """
    from lex.api.serializers.base_serializers import _resolve_history_source_model  # see module docstring

    root = _resolve_history_source_model(model_class) or model_class
    return all(getattr(candidate, "lex_value_lists", True) for candidate in (model_class, root))


def value_lists_offered(serializer_class, model_class) -> bool:
    """Whether any column of ``model_class`` can get a value list through ``serializer_class``.

    The checks that need no serializer instance -- the model's opt-out and
    :func:`renders_rows_as_stored`; :func:`value_list_field` applies them
    first.
    """
    return model_offers_value_lists(model_class) and renders_rows_as_stored(serializer_class)


def _renders_value_as_stored(serializer_field) -> bool:
    """True when the field class reads and renders the stored value with DRF's own methods.

    ``Serializer.to_representation`` calls ``get_attribute`` and then
    ``to_representation`` on every field, so a class that defines either one
    itself (to mask, round or reformat) can show the grid something other
    than the stored value. A class that only inherits DRF's methods passes.

    A nested serializer (a ``BaseSerializer``, which includes the
    ``ListSerializer`` DRF substitutes for ``many=True``) is refused before
    that walk, not by it: ``Serializer.get_attribute`` is plain ``Field``'s,
    unmodified, and ``Serializer.to_representation`` is still defined in a
    ``rest_framework.*`` module, so both would otherwise pass -- even though
    what they render is a nested dict built from the related row (often just
    ``{"id": ...}``), not that row's own value. A ``filter_values`` label
    built from ``str(related_object)`` would show the whole related row
    regardless of what the nested serializer actually publishes.
    """
    if isinstance(serializer_field, serializers.BaseSerializer):
        return False
    for method_name in ("get_attribute", "to_representation"):
        method = getattr(type(serializer_field), method_name, None)
        if not getattr(method, "__module__", "").startswith("rest_framework."):
            return False
    return True


def value_list_field(serializer, model_class, name: str, request=None) -> Optional[Any]:
    """The model field behind ``name``, or ``None`` when it is not filterable.

    ``serializer`` is the requested serializer instance, and only a column
    the grid renders exactly as stored can be listed. Shared by the
    ``filter_values`` endpoint, ``Meta.filter_bar`` validation and the fields
    endpoint's ``value_list`` flag, so none of them can name a column the
    others would refuse. ``None`` when:

    * the model opts out of value lists (:func:`model_offers_value_lists`);
    * the serializer class rewrites its rows (:func:`renders_rows_as_stored`);
    * ``name`` is not a field of the serializer, or is a write-only field
      (``HiddenField`` included) the grid never renders;
    * the field's ``source`` is not itself -- a ``SerializerMethodField`` has
      source ``"*"`` and a renamed field (``source="other_column"``) shows
      another column;
    * the field class reads or renders the value with a ``get_attribute`` or
      ``to_representation`` of its own, or is itself a nested serializer
      (:func:`_renders_value_as_stored`);
    * it is not a real column or foreign key on the model of a type
      :func:`is_value_list_field` accepts;
    * ``request`` is given and its caller may not filter by the field
      (``LEX_SEC_FILTERS``, :func:`lex.api.filter_policy.path_allowed`): the
      list treats such a field as one that does not exist and ignores a
      filter on it, so it has no value list either. ``filter_values`` and the
      fields endpoint pass their request; ``Meta.filter_bar`` validation has
      none, and the fields endpoint narrows the bar per request instead.
    """
    name = (name or "").strip()
    if not name or "__" in name:
        return None
    if not value_lists_offered(type(serializer), model_class):
        return None
    serializer_fields = serializer.fields
    if name not in serializer_fields:
        return None
    serializer_field = serializer_fields[name]
    if getattr(serializer_field, "write_only", False):
        return None
    if getattr(serializer_field, "source", name) != name:
        return None
    if not _renders_value_as_stored(serializer_field):
        return None
    try:
        model_field = model_class._meta.get_field(name)
    except FieldDoesNotExist:
        return None
    if not is_value_list_field(model_field):
        return None
    if request is not None:
        from lex.api.filter_policy import path_allowed

        if not path_allowed(model_class, name, request):
            return None
    return model_field
