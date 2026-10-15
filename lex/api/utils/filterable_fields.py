"""Which model fields a value-list filter can list.

One answer for the endpoint that lists values and for the ``filter_bar`` list
option, so the bar can never name a column the endpoint would refuse.
"""

from __future__ import annotations

from django.db.models import FileField, JSONField


def is_value_list_field(model_field) -> bool:
    """A real column or a foreign key: not many-to-many, not JSON, not a file."""
    return (
        bool(getattr(model_field, "concrete", False))
        and not getattr(model_field, "many_to_many", False)
        and not isinstance(model_field, (JSONField, FileField))
    )
