"""Which model fields a value-list filter can list.

One answer for the endpoint that lists values and for the ``filter_bar`` list
option, so the bar can never name a column the endpoint would refuse.
"""

from __future__ import annotations

from typing import Any, Optional

from django.core.exceptions import FieldDoesNotExist
from django.db.models import BooleanField, CharField, DateField, DateTimeField, IntegerField, TextField

from lex.api.serializers.sensitive_fields import is_sensitive_field_name


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


def value_list_field(serializer_fields, model_class, name: str) -> Optional[Any]:
    """The model field behind ``name``, or ``None`` when it is not filterable.

    Shared by the ``filter_values`` endpoint and (later) ``Meta.filter_bar``
    validation, so neither can name a column the other would refuse.

    ``name`` must be a field of ``serializer_fields`` whose ``source`` is
    itself -- a ``SerializerMethodField`` has source ``"*"`` and a renamed
    field (``source="other_column"``) has another column's name, and both
    would show the grid something other than this raw column, so both are
    refused -- and it must be a real column or foreign key on the model,
    per :func:`is_value_list_field`.
    """
    name = (name or "").strip()
    if not name or "__" in name:
        return None
    if name not in serializer_fields:
        return None
    if getattr(serializer_fields[name], "source", name) != name:
        return None
    try:
        model_field = model_class._meta.get_field(name)
    except FieldDoesNotExist:
        return None
    if not is_value_list_field(model_field):
        return None
    return model_field
