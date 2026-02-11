"""
MetaLevelHistoricalRecords — Level 2 of the Bitemporal Architecture.

This class creates a "History of History" (MetaHistory) model.  For every
row in the History table (Level 1) it tracks *when the system knew about it*
using two system-time fields:

    sys_from  — when this version of knowledge was recorded
    sys_to    — when it was superseded (NULL = current knowledge)

The control fields are prefixed with ``meta_`` to avoid collisions with
the Level 1 fields that are copied into the MetaHistory model.
"""

from django.db import models
from django.utils import timezone
from simple_history.models import HistoricalRecords

import logging

logger = logging.getLogger(__name__)


class MetaLevelHistoricalRecords(HistoricalRecords):
    """
    History-on-History provider.

    Generates a Django model named ``Meta{HistoricalModelName}`` with:
      - All data fields copied from the History model (via ``fields_included``)
      - ``sys_from`` / ``sys_to``  — system time window
      - ``meta_history_type``      — +/~/- marker
      - ``history_object``         — FK back to the History row
      - ``meta_task_name/status``  — for Celery scheduling bookkeeping
    """

    # ------------------------------------------------------------------
    # Field definition
    # ------------------------------------------------------------------

    def get_extra_fields(self, model, fields):
        """Return meta-level control fields with ``meta_`` prefix."""

        extra_fields = {
            "meta_history_id": self._get_history_id_field(),

            # System-time window
            "sys_from": models.DateTimeField(
                db_index=self._date_indexing is True,
            ),
            "sys_to": models.DateTimeField(
                default=None,
                null=True,
                blank=True,
                help_text="When this system record was superseded.",
            ),

            # Change metadata
            "meta_history_change_reason": self._get_history_change_reason_field(),
            "meta_history_type": models.CharField(
                max_length=1,
                choices=(("+", "Created"), ("~", "Changed"), ("-", "Deleted")),
            ),

            # FK to the History row this meta record describes
            "history_object": models.ForeignKey(
                model,
                null=True,
                on_delete=models.SET_NULL,
                db_constraint=False,
            ),

            # Celery scheduling bookkeeping
            "meta_task_name": models.CharField(
                max_length=255,
                null=True,
                blank=True,
                unique=True,
                help_text="Name of the scheduled PeriodicTask.",
            ),
            "meta_task_status": models.CharField(
                max_length=20,
                default="NONE",
                choices=(
                    ("NONE", "None"),
                    ("SCHEDULED", "Scheduled"),
                    ("DONE", "Done"),
                    ("CANCELLED", "Cancelled"),
                ),
            ),

            # Required by simple_history internals
            "instance": property(lambda self: None),
            "instance_type": model,
        }

        # User tracking
        if self.user_id_field is not None:
            extra_fields["meta_history_user_id"] = self.user_id_field
            extra_fields["meta_history_user"] = property(
                self.user_getter, self.user_setter
            )
        else:
            extra_fields["meta_history_user"] = models.ForeignKey(
                "auth.User",
                null=True,
                on_delete=models.SET_NULL,
                db_constraint=False,
            )

        return extra_fields

    # ------------------------------------------------------------------
    # Meta / ordering
    # ------------------------------------------------------------------

    def get_meta_options(self, model):
        meta_fields = super().get_meta_options(model)
        meta_fields["ordering"] = ("-sys_from", "-meta_history_id")
        meta_fields["get_latest_by"] = ("sys_from", "meta_history_id")
        return meta_fields

    # ------------------------------------------------------------------
    # Record creation
    # ------------------------------------------------------------------

    def create_historical_record(self, instance, history_type, using=None):
        """
        Create (or update-in-place) a MetaHistory record.

        When ``instance._strict_chaining_update`` is True the latest OPEN
        meta record is updated in-place instead of creating a new row.
        This happens during validity-chain refinements (e.g. correcting a
        ``valid_to`` from one value to another value).
        """
        manager = getattr(instance, self.manager_name)
        attrs = {}
        for field in self.fields_included(instance):
            attrs[field.attname] = getattr(instance, field.attname)

        # ── Strict-chaining in-place update ──
        if getattr(instance, "_strict_chaining_update", False):
            latest = (
                manager.all().order_by("-sys_from", "-meta_history_id").first()
            )
            if latest and latest.sys_to is None:
                for field in self.fields_included(instance):
                    setattr(latest, field.attname, getattr(instance, field.attname))
                # sys_from intentionally NOT updated (original creation time)
                latest.save(using=using)
                return latest

        # ── Normal creation ──
        history_instance = manager.model(
            sys_from=getattr(instance, "_history_date", timezone.now()),
            meta_history_type=history_type,
            meta_history_change_reason=getattr(
                instance, "_history_change_reason", ""
            ),
            meta_history_user=self.get_history_user(instance),
            history_object=instance,
            **attrs,
        )
        history_instance.save(using=using)
        return history_instance
