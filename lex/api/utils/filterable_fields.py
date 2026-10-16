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

# Marks a ``to_representation`` that leaves every field's value as its field
# class rendered it (the framework's wrapper that only re-adds ``id``).
KEEPS_FIELD_VALUES = "_lex_keeps_field_values"


def is_plain_text_field(model_field) -> bool:
    """A ``CharField`` or ``TextField`` (subclasses included) with no ``choices``.

    Used both to decide whether a search runs in the database and to decide
    whether an empty string on this column means the same thing as blank.
    """
    return isinstance(model_field, (CharField, TextField)) and not model_field.choices


def is_value_list_field(model_field) -> bool:
    """True for the column types the design lists as getting a value list.

    An allowlist, not a denylist: a column qualifies only by being one of a
    known-safe shape. Decimal, float, date-time, binary, UUID, JSON, file and
    many-to-many columns are refused by omission, not by name, so a new
    Django field type is refused until someone decides it belongs here.
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
    if isinstance(model_field, (CharField, TextField)):
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
    ``KEEPS_FIELD_VALUES`` mark.
    """
    from lex.api.serializers.base_serializers import LexSerializer  # see module docstring

    for klass in getattr(serializer_class, "__mro__", ()):
        method = vars(klass).get("to_representation")
        if method is None or klass is LexSerializer or getattr(method, KEEPS_FIELD_VALUES, False):
            continue
        if not getattr(method, "__module__", "").startswith("rest_framework."):
            return False
    return True


def value_lists_offered(serializer_class, model_class) -> bool:
    """Whether any column of ``model_class`` can get a value list through ``serializer_class``.

    The checks that need no serializer instance; :func:`value_list_field`
    applies them first.
    """
    return renders_rows_as_stored(serializer_class)


def _renders_value_as_stored(serializer_field) -> bool:
    """True when the field class renders the stored value with DRF's own method.

    A class that only inherits DRF's ``to_representation`` passes; one that
    defines its own (to mask, round or reformat) does not.
    """
    method = getattr(type(serializer_field), "to_representation", None)
    return getattr(method, "__module__", "").startswith("rest_framework.")


def value_list_field(serializer, model_class, name: str) -> Optional[Any]:
    """The model field behind ``name``, or ``None`` when it is not filterable.

    ``serializer`` is the requested serializer instance, and only a column
    the grid renders exactly as stored can be listed. Shared by the
    ``filter_values`` endpoint, ``Meta.filter_bar`` validation and the fields
    endpoint's ``value_list`` flag, so none of them can name a column the
    others would refuse. ``None`` when:

    * the serializer class rewrites its rows (:func:`renders_rows_as_stored`);
    * ``name`` is not a field of the serializer, or is a write-only field
      (``HiddenField`` included) the grid never renders;
    * the field's ``source`` is not itself -- a ``SerializerMethodField`` has
      source ``"*"`` and a renamed field (``source="other_column"``) shows
      another column;
    * the field class renders the value with a ``to_representation`` of its
      own (:func:`_renders_value_as_stored`);
    * it is not a real column or foreign key on the model of a type
      :func:`is_value_list_field` accepts.
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
    return model_field
