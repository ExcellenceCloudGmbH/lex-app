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

import hashlib
import logging

from django.db import models, transaction
from django.utils import timezone
from simple_history.models import HistoricalRecords

logger = logging.getLogger(__name__)


def create_meta_history_record(
    meta_manager,
    instance,
    history_type,
    using=None,
    meta_history_user=None,
):
    """
    Create a level-2 (meta) history row with compatibility for manager APIs.
    """
    creator = getattr(meta_manager, "create_historical_record", None)
    if callable(creator):
        return creator(instance, history_type, using=using)

    meta_model = meta_manager.model

    attrs = {}
    for field in getattr(meta_model, "tracked_fields", ()):
        attrs[field.attname] = getattr(instance, field.attname)

    # Strict-chaining in-place update
    if getattr(instance, "_strict_chaining_update", False):
        with transaction.atomic():
            # Lock the latest row for deterministic strict-chaining refinement.
            latest = (
                meta_model.objects.select_for_update()
                .filter(history_object=instance)
                .order_by("-sys_from", "-meta_history_id")
                .first()
            )
            if latest and latest.sys_to is None:
                for field_name, value in attrs.items():
                    setattr(latest, field_name, value)
                latest.save(using=using, update_fields=list(attrs.keys()))
                return latest

    if meta_history_user is None:
        meta_history_user = getattr(instance, "_history_user", None)

    history_instance = meta_model(
        sys_from=getattr(instance, "_history_date", timezone.now()),
        meta_history_type=history_type,
        meta_history_change_reason=getattr(
            instance, "_history_change_reason", ""
        ),
        meta_history_user=meta_history_user,
        history_object=instance,
        **attrs,
    )
    history_instance.save(using=using)
    return history_instance


def scheduled_activation_index(table_name: str) -> models.Index:
    """
    The partial index both activation paths scan every minute, forever.

    ``SELECT ... WHERE meta_task_status = 'SCHEDULED'`` runs per meta table per tick —
    from the in-database applier (``lex_apply_due_activations()``), from the pending
    view, and from the reconcile floor. ``SCHEDULED`` rows are transient and rare, so a
    partial index is tiny and serves the scan and the ``DONE`` flip alike. Declared on
    the generated model, so every customer repository picks it up as one ``AddIndex``
    migration per meta table on its next ``makemigrations``. Correctness never depends
    on it; only the cost of the scan does.

    The name is derived from the table name (Django caps index names at 30 characters
    and they must be unique per database).
    """
    digest = hashlib.md5(table_name.encode("utf-8")).hexdigest()[:12]
    return models.Index(
        fields=["history_object"],
        condition=models.Q(meta_task_status="SCHEDULED"),
        name=f"lexsched_{digest}",
    )


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
        table_name = self.table_name or f"{model._meta.db_table}_meta_history"
        meta_fields["indexes"] = [
            *meta_fields.get("indexes", ()),
            scheduled_activation_index(table_name),
        ]
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
        return create_meta_history_record(
            meta_manager=manager,
            instance=instance,
            history_type=history_type,
            using=using,
            meta_history_user=self.get_history_user(instance),
        )
