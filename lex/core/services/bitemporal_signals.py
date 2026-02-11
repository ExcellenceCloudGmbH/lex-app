"""
Bitemporal signal handlers for the 3-layer history architecture.

These handlers are connected to Django signals on the History model
and MetaHistory model to maintain the bitemporal invariants:

Level 1 (History → History):
    - ``on_history_saved__chain_valid_to``       — strict chaining of valid_to
    - ``on_history_saved__create_meta``           — create/update MetaHistory record
    - ``on_history_saved__sync_main_table``       — sync main table to current truth
    - ``on_history_pre_delete__cancel_schedules`` — cancel tasks before deletion
    - ``on_history_post_delete__repair_chain``    — repair chain after deletion

Level 2 (MetaHistory → MetaHistory):
    - ``on_meta_saved__chain_sys_to``             — strict chaining of sys_to
"""

import os
import logging

from django.utils import timezone

logger = logging.getLogger(__name__)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Level 1 handlers — connected to post_save / pre_delete / post_delete
#                     on the *Historical* model
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def on_history_saved__chain_valid_to(
    sender, instance=None, history_instance=None, *,
    main_model, historical_model, **kwargs
):
    """
    Strict Chaining: set ``valid_to`` of every history record to the
    ``valid_from`` of the next record (ordered by valid_from).

    The last record in the chain has ``valid_to = None`` (open-ended / ∞).

    When a refinement happens (``valid_to`` changes from one non-None value
    to another), we mark the record with ``_strict_chaining_update = True``
    so the MetaHistory layer can update the existing meta record in-place
    instead of creating a new version.
    """
    history_instance = history_instance or instance
    if sender != historical_model or not history_instance:
        return

    HistoryModel = history_instance.__class__
    pk_name = main_model._meta.pk.name
    pk_val = getattr(history_instance, pk_name)

    all_records = list(
        HistoryModel.objects.filter(**{pk_name: pk_val})
        .order_by("valid_from", "history_id")
    )

    for i, record in enumerate(all_records):
        next_record = all_records[i + 1] if i < len(all_records) - 1 else None
        new_valid_to = next_record.valid_from if next_record else None

        if record.valid_to != new_valid_to:
            is_refinement = (record.valid_to is not None) and (new_valid_to is not None)
            record.valid_to = new_valid_to
            record._strict_chaining_update = is_refinement
            record.save(update_fields=["valid_to"])


def on_history_saved__create_meta(
    sender, instance, *,
    main_model, historical_model, model_name, **kwargs
):
    """
    Create or update a MetaHistory record whenever a History row is saved.

    Also handles future-dated records by scheduling activation via Celery
    (or the LocalSchedulerBackend if Celery is not active).
    """
    if sender != historical_model:
        return

    history_instance = instance

    try:
        MetaModel = sender.meta_history.model

        # Get or create meta record
        meta_instance = (
            MetaModel.objects.filter(history_object=history_instance)
            .order_by("-sys_from", "-meta_history_id")
            .first()
        )
        if not meta_instance:
            # Use the meta_history manager to create the initial record
            history_manager = getattr(sender, 'meta_history', None)
            if history_manager:
                meta_instance = history_manager.create_historical_record(
                    history_instance, "+"
                )
            else:
                return

        # ── Schedule activation if valid_from is in the future ──
        now = timezone.now()
        if history_instance.valid_from > now + timezone.timedelta(seconds=5):
            _schedule_future_activation(
                meta_instance, history_instance, main_model, model_name, now
            )

    except ImportError:
        logger.warning("django-celery-beat not installed. Skipping scheduling.")
    except Exception as e:
        logger.error(
            f"Error in meta-history / scheduling for {sender.__name__}: {e}",
            exc_info=True,
        )


def on_history_saved__sync_main_table(
    sender, instance, *,
    main_model, historical_model, **kwargs
):
    """
    Ensure the main table reflects the History record that is valid *right now*.
    Delegates to ``BitemporalSynchronizer``.
    """
    if sender != historical_model:
        return

    from lex.process_admin.utils.bitemporal_sync import BitemporalSynchronizer

    pk_name = main_model._meta.pk.name
    pk_val = getattr(instance, pk_name)
    BitemporalSynchronizer.sync_record_for_model(main_model, pk_val, sender)


def on_history_pre_delete__cancel_schedules(
    sender, instance, *,
    historical_model, **kwargs
):
    """Cancel any scheduled Celery tasks before a History row is deleted."""
    if sender != historical_model:
        return

    try:
        MetaModel = sender.meta_history.model
        scheduled = MetaModel.objects.filter(
            history_object_id=instance.pk,
            meta_task_status="SCHEDULED",
        )

        try:
            from django_celery_beat.models import PeriodicTask
            for meta_log in scheduled:
                if meta_log.meta_task_name:
                    PeriodicTask.objects.filter(name=meta_log.meta_task_name).delete()
                    logger.info(f"Revoked Task (Deletion): {meta_log.meta_task_name}")
                meta_log.meta_task_status = "CANCELLED"
                meta_log.save(update_fields=["meta_task_status"])
        except ImportError:
            # Celery beat not installed, just mark as cancelled
            scheduled.update(meta_task_status="CANCELLED")
    except Exception:
        pass


def on_history_post_delete__repair_chain(
    sender, instance, *,
    main_model, historical_model, **kwargs
):
    """
    Repair the validity chain after a History row is deleted.

    If the chain was ``A → B → C`` and B is deleted, A's ``valid_to``
    is extended to C's ``valid_from``.
    """
    if sender != historical_model:
        return

    HistoryModel = sender
    pk_name = main_model._meta.pk.name
    pk_val = getattr(instance, pk_name)

    previous_record = (
        HistoryModel.objects.filter(
            **{pk_name: pk_val}, valid_from__lt=instance.valid_from
        )
        .order_by("-valid_from")
        .first()
    )

    if previous_record:
        next_record = (
            HistoryModel.objects.filter(
                **{pk_name: pk_val}, valid_from__gt=instance.valid_from
            )
            .order_by("valid_from")
            .first()
        )
        new_valid_to = next_record.valid_from if next_record else None
        previous_record.valid_to = new_valid_to
        previous_record.save(update_fields=["valid_to"])


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Level 2 handler — connected to post_save on the *MetaHistory* model
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def on_meta_saved__chain_sys_to(
    sender, instance, *,
    meta_historical_model, **kwargs
):
    """
    Strict Chaining for MetaHistory: set ``sys_to`` of every meta record
    for the same ``history_object`` to the ``sys_from`` of the next record.
    """
    if sender != meta_historical_model:
        return

    MetaModel = instance.__class__
    history_object_id = instance.history_object_id

    all_meta = list(
        MetaModel.objects.filter(history_object_id=history_object_id)
        .order_by("sys_from", "id")
    )

    for i, record in enumerate(all_meta):
        next_record = all_meta[i + 1] if i < len(all_meta) - 1 else None
        new_sys_to = next_record.sys_from if next_record else None

        if record.sys_to != new_sys_to:
            record.sys_to = new_sys_to
            record.save(update_fields=["sys_to"])


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Private helpers
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def _schedule_future_activation(
    meta_instance, history_instance, main_model, model_name, now
):
    """Schedule a Celery task (or local thread) to activate a future-dated record."""

    if os.getenv("CELERY_ACTIVE", "false").lower() != "true":
        # ── Local in-process scheduler ──
        from lex.process_admin.utils.local_scheduler import LocalSchedulerBackend
        from lex.lex_app.celery_tasks import activate_history_version

        scheduler = LocalSchedulerBackend()
        scheduler.schedule(
            run_at_time=history_instance.valid_from,
            func=activate_history_version,
            kwargs={
                "model_app_label": main_model._meta.app_label,
                "model_name": model_name,
                "history_id": history_instance.pk,
            },
        )
        meta_instance.meta_task_status = "SCHEDULED"
        meta_instance.meta_task_name = f"local_thread_{history_instance.pk}_{int(now.timestamp())}"
        meta_instance.save(update_fields=["meta_task_status", "meta_task_name"])
    else:
        # ── Celery Beat scheduler ──
        import json
        import uuid
        from django_celery_beat.models import ClockedSchedule, PeriodicTask

        if meta_instance.meta_task_name:
            PeriodicTask.objects.filter(name=meta_instance.meta_task_name).delete()

        clocked, _ = ClockedSchedule.objects.get_or_create(
            clocked_time=history_instance.valid_from
        )

        task_unique_name = (
            f"activate_{main_model._meta.app_label}_{model_name}"
            f"_{history_instance.pk}_{int(now.timestamp())}_{uuid.uuid4()}"
        )

        PeriodicTask.objects.create(
            clocked=clocked,
            name=task_unique_name,
            task="activate_history_version",
            args=json.dumps([
                main_model._meta.app_label,
                model_name,
                history_instance.pk,
            ]),
            one_off=True,
        )

        meta_instance.meta_task_name = task_unique_name
        meta_instance.meta_task_status = "SCHEDULED"
        meta_instance.save(update_fields=["meta_task_name", "meta_task_status"])

        logger.info(
            f"Scheduled Activation for {history_instance.valid_from} "
            f"(Task: {task_unique_name})"
        )
