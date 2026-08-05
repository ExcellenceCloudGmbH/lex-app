"""Catch-up pass for future-dated bitemporal activations.

Why this exists
---------------
Saving a history row with a future ``valid_from`` schedules its own activation:
:func:`lex.core.services.bitemporal_signals._schedule_future_activation` writes a
timer and marks the meta record ``SCHEDULED``. Which timer depends on the
deployment:

* ``CELERY_ACTIVE=true`` — a ``ClockedSchedule`` + one-off ``PeriodicTask`` row,
  fired by a per-instance ``django-celery-beat`` pod;
* otherwise — an in-process ``sched`` entry held by a daemon thread
  (:class:`~lex.process_admin.utils.local_scheduler.LocalSchedulerBackend`).

**Both timers are volatile, and the second one is lost on every restart.**
Nothing rehydrates the in-process queue, so a deploy, node move or OOM silently
drops every pending activation on that instance; the meta rows stay
``SCHEDULED`` forever and the data never becomes current. No error is raised.
The beat timer survives restarts, but only by keeping an always-on pod per
instance whose sole remaining job is holding a clock.

The durable fact is not the timer — it is the meta row. ``SCHEDULED`` plus the
history row's ``valid_from`` states exactly what should already have happened.
This module reads that and finishes the job.

That inverts the failure model. A timer that never fires becomes a *latency*
problem rather than a silent data-loss one, because this pass converges on the
same result. It is what makes it safe to retire per-instance beat, and what
makes any future cluster-level scheduler an optimisation that is allowed to
fail rather than a component every instance depends on.

Safety
------
Running this repeatedly, or concurrently with a timer firing the same
activation, is safe by construction:

* ``BitemporalSynchronizer.sync_record_for_model`` is convergent — it derives
  the main-table row from whichever history record is currently valid, so two
  runs produce one result;
* ``activate_history_version`` re-checks ``valid_from`` and refuses to act
  early;
* the ``SCHEDULED -> DONE`` transition is a no-op on repeat.

Deliberately **not** dispatched to Celery. Activation is a small convergent
write, not a calculation, and running it in-process means this works
identically on instances that have no broker at all — which is most of them.
"""

from __future__ import annotations

import logging
import os
import threading
from datetime import timedelta
from typing import Any, Dict, Iterator, Tuple

from django.apps import apps
from django.utils import timezone

logger = logging.getLogger(__name__)

_SCHEDULED = "SCHEDULED"
_DONE = "DONE"

# Per-process failure counters, keyed by (meta model label, meta pk).
#
# A row that fails to activate stays SCHEDULED and would otherwise be retried on
# every pass forever. Bounding it here rather than with a terminal FAILED status
# is deliberate: meta models are generated per customer model, so adding a
# choice would require a migration in every customer repository for a value the
# database does not enforce anyway. The cost is that the counter resets on
# restart, which is acceptable — a restart is exactly when a retry is most
# likely to succeed.
_failures: Dict[Tuple[str, Any], int] = {}
_failures_lock = threading.Lock()


def _setting(name: str, default):
    from django.conf import settings

    return getattr(settings, name, default)


def _max_attempts() -> int:
    return max(1, int(_setting("LEX_ACTIVATION_RECONCILE_MAX_ATTEMPTS", 5)))


def _max_age() -> timedelta:
    return timedelta(days=max(1, int(_setting("LEX_ACTIVATION_RECONCILE_MAX_AGE_DAYS", 30))))


def _interval() -> float:
    return max(5.0, float(_setting("LEX_ACTIVATION_RECONCILE_INTERVAL_SECONDS", 60)))


def _enabled() -> bool:
    return bool(_setting("LEX_ACTIVATION_RECONCILE_ENABLED", True))


def _outcome_of(returned) -> str:
    """Normalise what ``activate_history_version`` handed back.

    The task's own contract is a status string ("success", "failed_too_early",
    "skipped_missing_record", "failed_model_lookup"), but it is wrapped by
    ``@lex_shared_task``, whose wrapper returns ``(result, args)``. Calling the
    task in-process therefore yields the tuple, not the string.

    Unwrapping defensively rather than indexing ``[0]`` blindly means a change
    to the decorator's return shape degrades to "not success" — a retry — rather
    than being read as a spurious success and dropping the record.
    """
    if isinstance(returned, (tuple, list)) and returned:
        returned = returned[0]
    return returned if isinstance(returned, str) else ""


def iter_bitemporal_models() -> Iterator[Tuple[type, type, type]]:
    """Yield ``(main_model, history_model, meta_model)`` for every bitemporal model.

    Discovered from the app registry rather than a list, so a customer model
    gains catch-up simply by having history enabled — there is nothing to
    register and nothing to keep in sync.
    """
    for model in apps.get_models():
        history = getattr(model, "history", None)
        history_model = getattr(history, "model", None) if history is not None else None
        if history_model is None:
            continue
        meta = getattr(history_model, "meta_history", None)
        meta_model = getattr(meta, "model", None) if meta is not None else None
        if meta_model is None:
            continue
        yield model, history_model, meta_model


def _overdue_history_ids(meta_model, history_model, now) -> list:
    """History ids whose activation was scheduled and is now due.

    Only *current* meta versions (``sys_to IS NULL``) count — a superseded meta
    row describes what we used to believe, not outstanding work.

    Bounded below by ``LEX_ACTIVATION_RECONCILE_MAX_AGE_DAYS`` so a long-dormant
    instance does not wake up and replay a year of history in one pass. Rows
    older than the window are reported, never silently actioned.
    """
    scheduled_ids = list(
        meta_model.objects.filter(
            meta_task_status=_SCHEDULED,
            sys_to__isnull=True,
        ).values_list("history_object_id", flat=True)
    )
    if not scheduled_ids:
        return []

    return list(
        history_model.objects.filter(
            pk__in=scheduled_ids,
            valid_from__lte=now,
            valid_from__gte=now - _max_age(),
        ).values_list("pk", flat=True)
    )


def reconcile_pending_activations(now=None) -> Dict[str, int]:
    """Activate every future-dated record whose moment has passed.

    ``now`` selects which rows are considered due. It does **not** override the
    clock inside ``activate_history_version``, which independently re-checks
    ``valid_from`` before acting — so passing a synthetic future ``now`` selects
    a row that the task then declines. That asymmetry is deliberate: the task's
    own guard is the authority on whether a record may activate, and this pass
    is only allowed to decide *when to look*.

    Returns counters for observability and tests. Never raises: a failure on one
    record must not stop the rest, and this runs on a background thread where an
    escaping exception would kill the loop.
    """
    stats = {"models": 0, "overdue": 0, "activated": 0, "failed": 0, "gave_up": 0}
    if not _enabled():
        return stats

    now = now or timezone.now()

    from lex.lex_app.celery_tasks import activate_history_version

    for main_model, history_model, meta_model in iter_bitemporal_models():
        stats["models"] += 1
        try:
            overdue = _overdue_history_ids(meta_model, history_model, now)
        except Exception:
            # A model whose meta table is missing or mid-migration must not take
            # the whole pass down with it.
            logger.warning(
                "Activation reconcile: could not query %s", meta_model.__name__,
                exc_info=True,
            )
            continue

        for history_id in overdue:
            stats["overdue"] += 1
            key = (meta_model._meta.label_lower, history_id)

            with _failures_lock:
                attempts = _failures.get(key, 0)
            if attempts >= _max_attempts():
                stats["gave_up"] += 1
                continue

            try:
                # Called directly, not .delay(): see the module docstring.
                #
                # This task reports failure by RETURN VALUE, not by raising:
                # "success", or one of "failed_too_early" /
                # "skipped_missing_record" / "failed_model_lookup". Treating a
                # clean return as success would silently count activations that
                # never happened and drop them from the retry set.
                outcome = _outcome_of(
                    activate_history_version(
                        main_model._meta.app_label,
                        main_model.__name__,
                        history_id,
                    )
                )
                if outcome == "success":
                    with _failures_lock:
                        _failures.pop(key, None)
                    stats["activated"] += 1
                    logger.info(
                        "Activation reconcile: activated %s history %s (overdue)",
                        main_model.__name__, history_id,
                    )
                else:
                    with _failures_lock:
                        _failures[key] = attempts + 1
                    stats["failed"] += 1
                    logger.warning(
                        "Activation reconcile: %s history %s declined (%s), "
                        "attempt %s/%s",
                        main_model.__name__, history_id, outcome,
                        attempts + 1, _max_attempts(),
                    )
            except Exception:
                with _failures_lock:
                    _failures[key] = attempts + 1
                stats["failed"] += 1
                logger.warning(
                    "Activation reconcile: %s history %s failed (attempt %s/%s)",
                    main_model.__name__, history_id, attempts + 1, _max_attempts(),
                    exc_info=True,
                )

    if stats["overdue"]:
        logger.info("Activation reconcile pass: %s", stats)
    return stats


_stop = threading.Event()
_thread: threading.Thread | None = None


def _loop() -> None:
    interval = _interval()
    logger.info("Activation reconcile loop started (interval=%ss)", interval)
    # One pass immediately: a restart is the single most likely reason an
    # in-process timer was lost, so startup is when catch-up matters most.
    while True:
        try:
            reconcile_pending_activations()
        except Exception:  # pragma: no cover - reconcile already swallows
            logger.exception("Activation reconcile: pass failed")
        if _stop.wait(interval):
            return


def start_background_reconcile() -> bool:
    """Start the catch-up loop. Idempotent; returns True if it is now running.

    Call only from the served backend process — see ``apps.ready``. Running it
    from a management command or a worker would duplicate the work for no gain.
    """
    global _thread
    if not _enabled():
        logger.debug("Activation reconcile disabled (LEX_ACTIVATION_RECONCILE_ENABLED)")
        return False
    if _thread is not None and _thread.is_alive():
        return True
    _stop.clear()
    _thread = threading.Thread(
        target=_loop, name="lex-activation-reconcile", daemon=True
    )
    _thread.start()
    return True


def stop_background_reconcile() -> None:
    """Signal the loop to exit (tests, shutdown)."""
    _stop.set()
