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

import logging
import os
from contextlib import contextmanager
from contextvars import ContextVar

from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from lex.core.services.MetaHistory import create_meta_history_record
from lex.core.services.StandardHistory import MAIN_TABLE_SYNC_NOT_NEEDED_ATTR

logger = logging.getLogger(__name__)

_suppress_main_table_sync = ContextVar(
    "suppress_main_table_sync",
    default=False,
)
_suppress_history_valid_to_chaining = ContextVar(
    "suppress_history_valid_to_chaining",
    default=False,
)
_suppress_meta_sys_to_chaining = ContextVar(
    "suppress_meta_sys_to_chaining",
    default=False,
)
_history_chain_dedupe_attr = "_bt_history_chain_processed"


@contextmanager
def suppress_main_table_sync():
    """
    Suppress main-table synchronization while executing bulk history maintenance.

    Backfill commands already copy from the live table into history, so syncing
    history back into the live table is redundant and can cause unintended
    side-effects when timestamps are not "currently valid".
    """
    token = _suppress_main_table_sync.set(True)
    try:
        yield
    finally:
        _suppress_main_table_sync.reset(token)


@contextmanager
def suppress_history_valid_to_chaining():
    """
    Suppress recursive strict-chaining while valid_to links are being repaired.

    Without this guard, each internal ``record.save(update_fields=["valid_to"])``
    retriggers this same handler and can recurse deeply on long timelines.
    """
    token = _suppress_history_valid_to_chaining.set(True)
    try:
        yield
    finally:
        _suppress_history_valid_to_chaining.reset(token)


@contextmanager
def suppress_meta_sys_to_chaining():
    """
    Suppress MetaHistory sys_to strict chaining during bulk backfills.

    Backfill inserts are typically first-time MetaHistory rows for each
    history_object; recalculating sys_to on every row save is redundant and
    creates high query volume.
    """
    token = _suppress_meta_sys_to_chaining.set(True)
    try:
        yield
    finally:
        _suppress_meta_sys_to_chaining.reset(token)


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

    Note on the L2 / MetaHistory side-effect: closing a previous L1 row's
    ``valid_to`` is a real save() against that L1 row, so it triggers
    ``on_history_saved__create_meta`` and produces a *new* L2 row
    capturing the updated ``valid_to``. This is intentional — the L2
    layer's job is system-time fidelity ("what did the system know at
    instant T?"), and the system genuinely went through two states for
    that L1 row: open-ended at creation, then closed at the moment the
    next save chained the boundary. An ``?as_of=...`` system-time query
    must be able to distinguish the two, which requires distinct L2
    rows. As a result, *N customer saves produce 2N − 1 L2 rows*, not
    N. See ``test_5_82_three_saves_chain_sys_to`` for the pinned
    contract. (Older versions of this docstring described an "in-place
    L2 update" optimization gated by a ``_strict_chaining_update`` flag
    — that flag was never fully wired up; the system-time-correct
    behaviour above is now considered the intended contract.)
    """
    history_instance = history_instance or instance
    if sender != historical_model or not history_instance:
        return
    if _suppress_history_valid_to_chaining.get():
        return

    HistoryModel = history_instance.__class__
    pk_name = main_model._meta.pk.name
    pk_val = getattr(history_instance, pk_name)
    is_post_create_signal = history_instance is not instance
    is_new_history_row = bool(kwargs.get("created")) or is_post_create_signal

    # Standard history creation emits both post_save on the history row and
    # post_create_historical_record for the same object. Process the chain only
    # once for that newly created row to avoid duplicate maintenance work.
    if is_post_create_signal and getattr(history_instance, _history_chain_dedupe_attr, False):
        delattr(history_instance, _history_chain_dedupe_attr)
        return
    if kwargs.get("created"):
        setattr(history_instance, _history_chain_dedupe_attr, True)

    with suppress_history_valid_to_chaining(), suppress_main_table_sync():
        with transaction.atomic():
            if is_new_history_row:
                current_key = (
                    history_instance.valid_from,
                    getattr(history_instance, "history_id", 0),
                )
                previous_record = (
                    HistoryModel.objects.select_for_update()
                    .filter(**{pk_name: pk_val})
                    .filter(
                        Q(valid_from__lt=current_key[0])
                        | Q(valid_from=current_key[0], history_id__lt=current_key[1])
                    )
                    .order_by("-valid_from", "-history_id")
                    .first()
                )
                next_record = (
                    HistoryModel.objects.select_for_update()
                    .filter(**{pk_name: pk_val})
                    .filter(
                        Q(valid_from__gt=current_key[0])
                        | Q(valid_from=current_key[0], history_id__gt=current_key[1])
                    )
                    .order_by("valid_from", "history_id")
                    .first()
                )

                if previous_record and previous_record.valid_to != history_instance.valid_from:
                    previous_record.valid_to = history_instance.valid_from
                    try:
                        previous_record.save(update_fields=["valid_to"])
                    finally:
                        if hasattr(previous_record, "_strict_chaining_update"):
                            delattr(previous_record, "_strict_chaining_update")

                new_current_valid_to = next_record.valid_from if next_record else None
                if history_instance.valid_to != new_current_valid_to:
                    is_refinement = (
                        history_instance.valid_to is not None
                        and new_current_valid_to is not None
                    )
                    history_instance.valid_to = new_current_valid_to
                    history_instance._strict_chaining_update = is_refinement
                    try:
                        history_instance.save(update_fields=["valid_to"])
                    finally:
                        if hasattr(history_instance, "_strict_chaining_update"):
                            delattr(history_instance, "_strict_chaining_update")
                return

            # Existing history rows can be edited manually, which may relocate
            # them within the timeline. In that case recalculate the full chain
            # to preserve correctness.
            all_records = list(
                HistoryModel.objects.select_for_update()
                .filter(**{pk_name: pk_val})
                .order_by("valid_from", "history_id")
            )

            for i, record in enumerate(all_records):
                next_record = all_records[i + 1] if i < len(all_records) - 1 else None
                new_valid_to = next_record.valid_from if next_record else None

                if record.valid_to != new_valid_to:
                    is_refinement = (record.valid_to is not None) and (new_valid_to is not None)
                    record.valid_to = new_valid_to
                    record._strict_chaining_update = is_refinement
                    try:
                        record.save(update_fields=["valid_to"])
                    finally:
                        # Keep refinement marker scoped to this internal save.
                        if hasattr(record, "_strict_chaining_update"):
                            delattr(record, "_strict_chaining_update")


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
                meta_instance = create_meta_history_record(
                    meta_manager=history_manager,
                    instance=history_instance,
                    history_type="+",
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
    if _suppress_main_table_sync.get():
        return
    if getattr(instance, MAIN_TABLE_SYNC_NOT_NEEDED_ATTR, False):
        return

    from lex.process_admin.utils.bitemporal_sync import BitemporalSynchronizer

    pk_name = main_model._meta.pk.name
    pk_val = getattr(instance, pk_name)

    # Run synchronization inline so the main table is consistent before
    # the transaction commits.  This avoids race conditions with Celery
    # tasks that read the main table immediately after dispatch.
    # Re-entrancy is prevented by the _suppress_main_table_sync guard:
    # BitemporalSynchronizer saves the main model with
    # skip_history_when_saving=True, so no new history record is created
    # and this handler is not re-triggered.
    with suppress_main_table_sync():
        BitemporalSynchronizer.sync_record_for_model(main_model, pk_val, sender)


def on_history_pre_delete__cancel_schedules(
    sender, instance, *,
    historical_model, **kwargs
):
    """Cancel scheduled tasks and close open meta records before deletion.

    This runs *before* the DB delete, so the FK (history_object_id) is
    still intact.  We must close all open meta records here because once
    Django executes the DELETE, the FK is SET_NULL and sys_to chaining
    can no longer find them.
    """
    if sender != historical_model:
        return

    try:
        MetaModel = sender.meta_history.model
        now = timezone.now()

        # ── Close all open meta records for this history row ──
        # This ensures system-time queries after the deletion no longer
        # see the old meta versions (their sys_to window is closed).
        open_metas = MetaModel.objects.filter(
            history_object_id=instance.pk,
            sys_to__isnull=True,
        )
        open_metas.update(sys_to=now)

        # ── Cancel scheduled tasks ──
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
    with transaction.atomic():
        # Lock the chain in deterministic order before repairing links.
        all_records = list(
            HistoryModel.objects.select_for_update()
            .filter(**{pk_name: pk_val})
            .order_by("valid_from", "history_id")
        )

        previous_record = None
        next_record = None
        deleted_key = (instance.valid_from, getattr(instance, "history_id", 0))
        for record in all_records:
            record_key = (record.valid_from, getattr(record, "history_id", 0))
            if record_key < deleted_key:
                previous_record = record
                continue
            if record_key > deleted_key:
                next_record = record
                break

        if previous_record:
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
    if _suppress_meta_sys_to_chaining.get():
        return

    MetaModel = instance.__class__
    history_object_id = instance.history_object_id
    update_fields = kwargs.get("update_fields")
    is_new_meta_row = bool(kwargs.get("created"))

    # Metadata-only updates that do not move a record within the system-time
    # chain do not require re-chaining.
    if (
        not is_new_meta_row
        and update_fields is not None
        and "sys_from" not in update_fields
        and "history_object" not in update_fields
        and "history_object_id" not in update_fields
    ):
        return

    with suppress_meta_sys_to_chaining():
        with transaction.atomic():
            if is_new_meta_row:
                current_key = (
                    instance.sys_from,
                    getattr(instance, "meta_history_id", 0),
                )
                previous_meta = (
                    MetaModel.objects.select_for_update()
                    .filter(history_object_id=history_object_id)
                    .filter(
                        Q(sys_from__lt=current_key[0])
                        | Q(sys_from=current_key[0], meta_history_id__lt=current_key[1])
                    )
                    .order_by("-sys_from", "-meta_history_id")
                    .first()
                )
                next_meta = (
                    MetaModel.objects.select_for_update()
                    .filter(history_object_id=history_object_id)
                    .filter(
                        Q(sys_from__gt=current_key[0])
                        | Q(sys_from=current_key[0], meta_history_id__gt=current_key[1])
                    )
                    .order_by("sys_from", "meta_history_id")
                    .first()
                )

                changed_records = []
                if previous_meta and previous_meta.sys_to != instance.sys_from:
                    previous_meta.sys_to = instance.sys_from
                    changed_records.append(previous_meta)

                new_current_sys_to = next_meta.sys_from if next_meta else None
                if instance.sys_to != new_current_sys_to:
                    instance.sys_to = new_current_sys_to
                    changed_records.append(instance)

                if changed_records:
                    MetaModel.objects.bulk_update(changed_records, ["sys_to"])
                return

            # Manual edits to existing meta rows can relocate them within the
            # chain. Rebuild the full ordering in that case.
            all_meta = list(
                MetaModel.objects.select_for_update()
                .filter(history_object_id=history_object_id)
                .order_by("sys_from", "meta_history_id")
            )

            changed_records = []
            for i, record in enumerate(all_meta):
                next_record = all_meta[i + 1] if i < len(all_meta) - 1 else None
                new_sys_to = next_record.sys_from if next_record else None

                if record.sys_to != new_sys_to:
                    record.sys_to = new_sys_to
                    changed_records.append(record)

            if changed_records:
                MetaModel.objects.bulk_update(changed_records, ["sys_to"])


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
