"""
BitemporalSynchronizer — keeps the main table in sync with history.

When a history record is saved, the main table must be updated to reflect
the **currently-valid** version of the record (i.e., the history row whose
``[valid_from, valid_to)`` window covers *now*).

If no valid record exists (record deleted or not yet active), the main
table row is removed.
"""

from django.db import models
from django.utils import timezone
import logging

logger = logging.getLogger(__name__)


class BitemporalSynchronizer:
    """Synchronizes a main table row with its effective history record."""

    @staticmethod
    def sync_record_for_model(model_class, pk_val, history_model=None):
        """
        Update or delete the main-table row for ``pk_val`` based on
        whichever history record is currently valid.

        Args:
            model_class: The main model class.
            pk_val: Primary key of the record to synchronize.
            history_model: The history model (resolved automatically if omitted).
        """
        if history_model is None:
            if hasattr(model_class, "history"):
                history_model = model_class.history.model
            else:
                logger.error(
                    f"Cannot sync {model_class.__name__}: no history model."
                )
                return

        pk_name = model_class._meta.pk.name
        now = timezone.now()

        # ── Find the effective record ──
        effective_record = (
            history_model.objects.filter(**{pk_name: pk_val})
            .filter(valid_from__lte=now)
            .filter(
                models.Q(valid_to__gt=now) | models.Q(valid_to__isnull=True)
            )
            .order_by("-valid_from", "-history_id")
            .first()
        )

        is_valid = effective_record and effective_record.history_type != "-"

        if is_valid:
            # ── Upsert main table ──
            try:
                main_instance = model_class.objects.get(pk=pk_val)
            except model_class.DoesNotExist:
                main_instance = model_class(pk=pk_val)

            changed = False
            for field in model_class._meta.fields:
                if field.attname == model_class._meta.pk.attname:
                    continue
                if hasattr(effective_record, field.attname):
                    new_val = getattr(effective_record, field.attname)
                    if getattr(main_instance, field.attname) != new_val:
                        setattr(main_instance, field.attname, new_val)
                        changed = True

            if changed or main_instance._state.adding:
                main_instance.skip_history_when_saving = True
                main_instance.save()
        else:
            # ── Remove stale main table row ──
            try:
                main_instance = model_class.objects.get(pk=pk_val)
                main_instance.skip_history_when_saving = True
                main_instance.delete()
            except model_class.DoesNotExist:
                pass
