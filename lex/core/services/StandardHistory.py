"""
StandardHistory — Level 1 of the Bitemporal Architecture.

Extends ``simple_history.HistoricalRecords`` to rename ``history_date``
to ``valid_from`` and adds a ``valid_to`` field for business-time tracking.

Together these two fields define the *valid time* window: the period during
which a fact was true in the real world.
"""

from django.db import models
from django.utils import timezone
from simple_history import manager as simple_history_manager_module
from simple_history import models as simple_history_models_module
from simple_history.models import HistoricalRecords


MAIN_TABLE_SYNC_NOT_NEEDED_ATTR = "_bt_main_table_sync_not_needed"


class CachedHistoryDescriptor(simple_history_manager_module.HistoryDescriptor):
    """
    Reuse the dynamic history-manager subclass for repeated descriptor access.

    django-simple-history rebuilds ``manager_class.from_queryset(...)`` on every
    ``instance.history`` access. During large delete/rebuild flows that spends a
    surprising amount of time in Django manager class construction while
    producing the exact same manager subclass each time.
    """

    def __init__(self, model, manager=simple_history_manager_module.HistoryManager, queryset=simple_history_manager_module.HistoricalQuerySet):
        super().__init__(model, manager=manager, queryset=queryset)
        self._resolved_manager_class = self.manager_class.from_queryset(
            self.queryset_class
        )

    def __get__(self, instance, owner):
        return self._resolved_manager_class(self.model, instance)


if simple_history_manager_module.HistoryDescriptor is not CachedHistoryDescriptor:
    simple_history_manager_module.HistoryDescriptor = CachedHistoryDescriptor
    simple_history_models_module.HistoryDescriptor = CachedHistoryDescriptor


def create_standard_history_record(
    history_manager,
    instance,
    history_type,
    using=None,
    history_user=None,
):
    """
    Create a level-1 history row for ``instance`` with API compatibility
    across django-simple-history versions.
    """
    creator = getattr(history_manager, "create_historical_record", None)
    if callable(creator):
        return creator(instance, history_type, using=using)

    history_model = history_manager.model

    attrs = {}
    for field in getattr(history_model, "tracked_fields", ()):
        attrs[field.attname] = getattr(instance, field.attname)

    if history_user is None:
        history_user = getattr(instance, "_history_user", None)

    history_instance = history_model(
        valid_from=getattr(instance, "_history_date", timezone.now()),
        history_type=history_type,
        history_change_reason=getattr(
            instance, "_history_change_reason", ""
        ),
        history_user=history_user,
        **attrs,
    )

    # A regular live-model save has already written the correct state to the
    # main table. The expensive history -> main-table synchronizer is only
    # needed when callers intentionally override valid time via ``_history_date``
    # or edit/delete history rows directly.
    if not hasattr(instance, "_history_date"):
        setattr(history_instance, MAIN_TABLE_SYNC_NOT_NEEDED_ATTR, True)

    history_instance.save(using=using)

    from simple_history.signals import post_create_historical_record

    post_create_historical_record.send(
        sender=history_model,
        instance=instance,
        history_instance=history_instance,
        history_date=history_instance.valid_from,
        history_user=history_instance.history_user,
        history_change_reason=history_instance.history_change_reason,
        using=using,
    )
    return history_instance


class StandardHistory(HistoricalRecords):
    """
    Historical records provider that uses bitemporal naming:

    - ``valid_from`` — when this version became true (replaces ``history_date``)
    - ``valid_to``   — when it was superseded (NULL = still current)
    """

    def get_extra_fields(self, model, fields):
        """Return control fields using bitemporal naming convention."""

        def get_instance(self):
            return getattr(self, self.instance_type._meta.model_name)

        extra_fields = {
            "history_id": self._get_history_id_field(),
            "valid_from": models.DateTimeField(
                db_index=self._date_indexing is True,
            ),
            "valid_to": models.DateTimeField(
                default=None,
                null=True,
                blank=True,
                help_text=(
                    "When this fact ceased to be true in the real world. "
                    "NULL means it is still the current truth."
                ),
            ),
            "history_change_reason": self._get_history_change_reason_field(),
            "history_type": models.CharField(
                max_length=1,
                choices=(("+", "Created"), ("~", "Changed"), ("-", "Deleted")),
            ),
            "instance": property(get_instance),
            "instance_type": model,
        }

        # User tracking
        if self.user_id_field is not None:
            extra_fields["history_user_id"] = self.user_id_field
            extra_fields["history_user"] = property(
                self.user_getter, self.user_setter
            )
        else:
            extra_fields["history_user"] = models.ForeignKey(
                "auth.User",
                null=True,
                on_delete=models.SET_NULL,
                db_constraint=False,
            )

        return extra_fields

    def get_meta_options(self, model):
        """Order by ``valid_from`` descending (most recent first)."""
        meta_fields = super().get_meta_options(model)
        meta_fields["ordering"] = ("-valid_from", "-history_id")
        meta_fields["get_latest_by"] = ("valid_from", "history_id")
        return meta_fields

    def create_historical_record(self, instance, history_type, using=None):
        """Create a history record, mapping ``_history_date`` → ``valid_from``."""
        manager = getattr(instance, self.manager_name)
        return create_standard_history_record(
            history_manager=manager,
            instance=instance,
            history_type=history_type,
            using=using,
            history_user=self.get_history_user(instance),
        )

    @classmethod
    def get_default_history_user(cls, instance):
        """Fallback for ``populate_history`` management command."""
        return None
