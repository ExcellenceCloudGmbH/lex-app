"""
StandardHistory — Level 1 of the Bitemporal Architecture.

Extends ``simple_history.HistoricalRecords`` to rename ``history_date``
to ``valid_from`` and adds a ``valid_to`` field for business-time tracking.

Together these two fields define the *valid time* window: the period during
which a fact was true in the real world.
"""

from django.db import models
from django.utils import timezone
from simple_history.models import HistoricalRecords


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

        # Control fields we set explicitly — skip them from copied attrs
        # to avoid "multiple values for keyword argument" if the tracked
        # model happens to have a field with the same name.
        CONTROL_FIELDS = {
            "valid_from", "valid_to", "history_type",
            "history_change_reason", "history_user", "history_user_id",
        }

        attrs = {}
        for field in self.fields_included(instance):
            if field.attname not in CONTROL_FIELDS and field.name not in CONTROL_FIELDS:
                attrs[field.attname] = getattr(instance, field.attname)

        history_instance = manager.model(
            valid_from=getattr(instance, "_history_date", timezone.now()),
            history_type=history_type,
            history_change_reason=getattr(
                instance, "_history_change_reason", ""
            ),
            history_user=self.get_history_user(instance),
            **attrs,
        )
        history_instance.save(using=using)

        # Emit signal so chaining / meta-history handlers fire
        from simple_history.signals import post_create_historical_record

        post_create_historical_record.send(
            sender=manager.model,
            instance=instance,
            history_instance=history_instance,
            history_date=history_instance.valid_from,
            history_user=history_instance.history_user,
            using=using,
        )
        return history_instance

    @classmethod
    def get_default_history_user(cls, instance):
        """Fallback for ``populate_history`` management command."""
        return None
