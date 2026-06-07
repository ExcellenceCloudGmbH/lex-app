"""Supervisor: detect dead workers and requeue their tasks (bounded).

A single scan pass (:func:`scan_and_recover`) reads the registry, finds
tasks that are still tracked but whose heartbeat has expired, and for each:

* **within budget** — re-dispatches the *same* ``task_id`` onto its queue
  (so a parent waiting on ``AsyncResult(task_id).get()`` still receives the
  eventual result) and grants a short heartbeat grace so the next scan does
  not re-detect it before the new run starts;
* **budget exhausted** — writes a FAILURE result (unblocking any waiter)
  and flips the calculation row out of ``IN_PROGRESS`` to ``ABORTED`` so it
  stops being stuck, then drops it from the registry.

Run it either way:

* via Celery beat — the wired ``sweep_dead_workers`` task runs one pass; or
* via a dedicated always-on process — :func:`run_forever` loops it.

The always-on form is preferable in the cluster (see the module docs): the
re-dispatch itself puts a message on the queue KEDA watches, which is what
brings a fresh worker up to actually run the recovered task — without the
beat path's churn of spawning a worker every interval just to poll.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Dict, List, Optional

from celery import shared_task

from . import registry
from .exceptions import MaxRequeueExceeded

logger = logging.getLogger(__name__)


def _settings():
    from django.conf import settings

    return settings


def _max_retries() -> int:
    return max(0, int(getattr(_settings(), "LEX_TASK_MAX_RETRIES", 4)))


def _scan_interval() -> int:
    return max(1, int(getattr(_settings(), "LEX_TASK_SUPERVISOR_SCAN_INTERVAL", 10)))


def _requeue_grace_seconds() -> int:
    return max(1, int(getattr(_settings(), "LEX_TASK_REQUEUE_GRACE_SECONDS", 60)))


def _default_queue() -> str:
    s = _settings()
    return getattr(s, "CELERY_TASK_DEFAULT_QUEUE", None) or "celery"


def _get_app():
    from lex.lex_app.celery import app

    return app


def _requeue(app, task_id: str, payload: Dict[str, Any]) -> None:
    """Re-dispatch ``task_id`` with an incremented retry count.

    Persistence is deliberately split around the dispatch so a broker outage
    never burns the retry budget:

    1. :func:`registry.grant_grace` — only delays the next dead detection;
    2. ``app.send_task`` — the actual re-dispatch (may raise if broker down);
    3. :func:`registry.persist_payload` — commits the incremented ``retries``
       count, but only *after* the message is on the broker.

    If step 2 raises, ``retries`` is never persisted: the grace window simply
    delays detection and the next pass retries without consuming a slot.
    """
    incremented = dict(payload)
    incremented["retries"] = int(incremented.get("retries", 0)) + 1
    queue = incremented.get("queue") or _default_queue()

    # Grant the grace window *before* dispatching so the task can never be
    # double-requeued if the next scan races the new worker's first heartbeat.
    registry.grant_grace(task_id, _requeue_grace_seconds())

    app.send_task(
        incremented["name"],
        args=incremented.get("args") or (),
        kwargs=incremented.get("kwargs") or {},
        task_id=task_id,
        queue=queue,
    )

    # Only now that the message is actually on the broker do we commit the
    # incremented retry count — a failed send_task above would have raised.
    registry.persist_payload(task_id, incremented)
    payload = incremented
    logger.warning(
        "Celery recovery: requeued dead task %s (%s) attempt %s/%s on queue %s",
        task_id,
        payload["name"],
        payload["retries"],
        _max_retries(),
        queue,
    )


def _extract_calculation_models(args: Any) -> List[Any]:
    """Mirror CallbackTask: pull CalculationModel instances out of task args."""
    from lex.core.models.CalculationModel import CalculationModel

    if not args:
        return []
    first = args[0]
    candidates = first if isinstance(first, (list, tuple)) else [first]
    return [c for c in candidates if isinstance(c, CalculationModel)]


def _calculation_id_of(payload: Dict[str, Any]) -> Optional[str]:
    """Extract the calculation_id carried in the task's re-dispatch kwargs.

    Tasks stamp it at ``payload["kwargs"]["context"]["calculation_id"]``.
    Returns ``None`` if any layer is missing (best-effort, never raises).
    """
    try:
        return (payload.get("kwargs") or {}).get("context", {}).get("calculation_id")
    except Exception:
        return None


def _abort_calculation_rows(payload: Dict[str, Any], reason: str) -> None:
    """Flip any stuck IN_PROGRESS rows for this task to ABORTED."""
    from lex.core.models.CalculationModel import CalculationModel

    _finalize_calculation_rows(payload, CalculationModel.ABORTED, reason)


def _cancel_calculation_rows(payload: Dict[str, Any], reason: str) -> None:
    """Flip any stuck IN_PROGRESS rows for this task to CANCELLED.

    Used when a dead task's calculation was cancelled by the user: we do not
    requeue, we finalize the stuck rows as CANCELLED (not ABORTED).
    """
    from lex.core.models.CalculationModel import CalculationModel

    _finalize_calculation_rows(payload, CalculationModel.CANCELLED, reason)


def _finalize_calculation_rows(payload: Dict[str, Any], target_status: str, reason: str) -> None:
    """Flip any stuck IN_PROGRESS rows for this task to ``target_status``."""
    from lex.core.signals import update_calculation_status
    from lex.core.models.CalculationModel import CalculationModel

    for instance in _extract_calculation_models(payload.get("args")):
        try:
            model_class = instance.__class__
            model_class._default_manager.filter(
                pk=instance.pk, is_calculated=CalculationModel.IN_PROGRESS
            ).update(is_calculated=target_status, error_message=reason)
            instance.is_calculated = target_status
            update_calculation_status(instance, exception_details=reason)
        except Exception:
            logger.warning(
                "Celery recovery: failed to finalize row %s as %s",
                instance, target_status, exc_info=True,
            )


def _is_cancelled(calc_id: Optional[str]) -> bool:
    """Best-effort check of the cluster-wide cancellation marker.

    Defensive on every front: an import error (module missing) or a Redis
    failure degrades to ``False`` so the caller falls back to the normal
    requeue path rather than wrongly finalizing a live calculation.
    """
    if not calc_id:
        return False
    try:
        from lex.core.cancellation import cluster_cancel_index

        return bool(cluster_cancel_index.is_cancelled(calc_id))
    except Exception:
        logger.debug("Celery recovery: cancellation check failed for %s", calc_id)
        return False


def _finalize_cancelled(task_id: str, payload: Dict[str, Any]) -> None:
    """A dead task's calculation was cancelled: finalize rows, stop tracking.

    Mirrors :func:`_give_up` but uses CANCELLED (not ABORTED) and does NOT
    write a FAILURE result — the user asked to cancel, not a budget failure.
    """
    _cancel_calculation_rows(payload, "Calculation cancelled by user")
    registry.deregister(task_id)
    logger.info(
        "Celery recovery: dead task %s belonged to a cancelled calculation; "
        "finalized CANCELLED instead of requeueing",
        task_id,
    )


def _give_up(app, task_id: str, payload: Dict[str, Any]) -> None:
    """Budget exhausted: record FAILURE, abort the row, stop tracking."""
    retries = int(payload.get("retries", 0))
    exc = MaxRequeueExceeded(task_id, retries)
    try:
        app.backend.mark_as_failure(task_id, exc)
    except Exception:
        logger.warning(
            "Celery recovery: mark_as_failure failed for %s", task_id, exc_info=True
        )
    _abort_calculation_rows(payload, str(exc))
    registry.deregister(task_id)
    logger.error(
        "Celery recovery: gave up on task %s after %s requeues; marked ABORTED",
        task_id,
        retries,
    )


def scan_and_recover(app=None) -> Dict[str, int]:
    """Run one recovery pass. Returns counters for observability/tests."""
    app = app or _get_app()
    stats = {
        "checked": 0,
        "alive": 0,
        "requeued": 0,
        "gave_up": 0,
        "orphaned": 0,
        "cancelled": 0,
        "skipped_locked": 0,
    }

    for task_id in registry.list_tracked():
        stats["checked"] += 1
        if registry.is_alive(task_id):
            stats["alive"] += 1
            continue

        payload = registry.get_payload(task_id)
        if payload is None:
            # Heartbeat gone and payload gone: nothing to act on. Drop it.
            registry.deregister(task_id)
            stats["orphaned"] += 1
            continue

        # Multi-supervisor safety: claim this dead task before acting on it.
        # If another replica already holds the lock, skip it this pass — that
        # replica will requeue/finalize it. Best-effort: a Redis failure
        # returns False, so we conservatively skip rather than double-act.
        if not registry.try_acquire_recovery_lock(task_id, _requeue_grace_seconds()):
            stats["skipped_locked"] += 1
            continue

        # Never requeue a user-cancelled calculation: finalize it CANCELLED.
        if _is_cancelled(_calculation_id_of(payload)):
            _finalize_cancelled(task_id, payload)
            stats["cancelled"] += 1
            continue

        if int(payload.get("retries", 0)) < _max_retries():
            try:
                _requeue(app, task_id, payload)
                stats["requeued"] += 1
            except Exception:
                logger.warning(
                    "Celery recovery: requeue dispatch failed for %s", task_id, exc_info=True
                )
        else:
            _give_up(app, task_id, payload)
            stats["gave_up"] += 1

    if stats["checked"]:
        logger.info("Celery recovery sweep: %s", stats)
    return stats


@shared_task(name="lex.lex_app.celery_recovery.supervisor.sweep_dead_workers")
def sweep_dead_workers() -> Dict[str, int]:
    """Celery-beat entrypoint: one recovery pass (see module docstring)."""
    return scan_and_recover()


_stop = threading.Event()


def run_forever(interval: Optional[int] = None) -> None:
    """Always-on supervisor loop (for a dedicated recovery process)."""
    interval = interval or _scan_interval()
    app = _get_app()
    logger.info("Celery recovery supervisor loop started (interval=%ss)", interval)
    _stop.clear()
    while not _stop.wait(interval):
        try:
            scan_and_recover(app)
        except Exception:
            logger.exception("Celery recovery: scan pass failed")


def stop_forever() -> None:
    """Signal :func:`run_forever` to exit (used by tests/shutdown)."""
    _stop.set()
