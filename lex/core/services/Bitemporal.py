"""
Bitemporal query helpers.

Provides ``get_queryset_as_of`` for time-travel queries across the
3-layer architecture, and ``resurrect_object`` to re-create deleted records.
"""

from django.db import models


def get_queryset_as_of(model_class, as_of):
    """
    Query historical data at a specific point in time.

    The *type* of time-travel depends on what you pass:

    - **Main model** (has ``history`` attr) → **Valid Time** query.
      Returns history records whose ``[valid_from, valid_to)`` window
      contains ``as_of``.

    - **History model** (has ``history_id`` attr) → **System Time** query.
      Returns meta-history records whose ``[sys_from, sys_to)`` window
      contains ``as_of``.

    Args:
        model_class: Either a main model class or a historical model class.
        as_of: Timezone-aware datetime for the point-in-time query.

    Returns:
        QuerySet of matching records.

    Raises:
        ValueError: If ``model_class`` has no history tracking.
    """
    is_main_model = (
        hasattr(model_class, "history") and not hasattr(model_class, "history_id")
    )
    is_history_model = hasattr(model_class, "history_id")

    if is_main_model:
        # Valid Time: "What was effectively true at `as_of`?"
        HistoryModel = model_class.history.model
        return (
            HistoryModel.objects.filter(valid_from__lte=as_of)
            .filter(
                models.Q(valid_to__gt=as_of) | models.Q(valid_to__isnull=True)
            )
            .exclude(history_type="-")
        )

    if is_history_model:
        # System Time: "What did the system know at `as_of`?"
        if not hasattr(model_class, "meta_history"):
            raise ValueError(
                f"History model {model_class.__name__} has no meta_history tracking."
            )
        MetaModel = model_class.meta_history.model
        return MetaModel.objects.filter(sys_from__lte=as_of).filter(
            models.Q(sys_to__gt=as_of) | models.Q(sys_to__isnull=True)
        )

    raise ValueError(
        f"Model {model_class.__name__} is neither a main model "
        f"with history nor a history model."
    )


def resurrect_object(model_class, pk, valid_from, attributes=None, valid_to=None):
    """
    Re-create a previously deleted object with a specific validity window.

    Args:
        model_class: The main model class (e.g. ``Invoice``).
        pk: Primary key of the object to resurrect.
        valid_from: When validity starts.
        attributes: Dict of field values to set on the resurrected object.
        valid_to: Optional end of validity. If given, a deletion marker (``-``)
                  is inserted at this time to close the validity window.

    Returns:
        The (re-)created model instance.
    """
    if attributes is None:
        attributes = {}

    instance = model_class(pk=pk, **attributes)
    instance._history_date = valid_from
    instance.save()

    if valid_to and hasattr(model_class, "history"):
        HistoryModel = model_class.history.model
        HistoryModel.objects.create(
            id=pk,
            history_type="-",
            valid_from=valid_to,
            history_user=None,
            **attributes,
        )

    return instance
