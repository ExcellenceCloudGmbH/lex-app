"""Worker-side heartbeat: proof-of-life for in-flight tasks.

While a task runs, a small daemon thread re-stamps that task's Redis
heartbeat key every ``LEX_TASK_HEARTBEAT_INTERVAL`` seconds. The key's TTL
is a multiple of the interval, so a missed refresh (the worker process was
SIGKILLed, OOM-killed, or the pod was evicted) lets the key expire and the
supervisor can prove the task is dead.

The handlers are wired by :func:`celery_recovery.enable`; they are inert
unless recovery is enabled (the registry itself no-ops when disabled).
"""

from __future__ import annotations

import logging
import threading
from typing import Dict, Optional, Tuple

from . import registry

logger = logging.getLogger(__name__)

# task_id -> (thread, stop_event)
_threads: Dict[str, Tuple[threading.Thread, threading.Event]] = {}
_threads_lock = threading.Lock()

# Tasks that must never be tracked/requeued by the recovery machinery
# itself (requeuing the sweep would be pointless and could self-amplify).
_UNTRACKED_TASK_NAMES = frozenset({"lex.lex_app.celery_recovery.supervisor.sweep_dead_workers"})


def _settings():
    from django.conf import settings

    return settings


def _heartbeat_interval() -> int:
    return max(1, int(getattr(_settings(), "LEX_TASK_HEARTBEAT_INTERVAL", 5)))


def _queue_of(task) -> Optional[str]:
    """Best-effort: the routing key this task arrived on, for re-dispatch."""
    request = getattr(task, "request", None)
    delivery_info = getattr(request, "delivery_info", None) or {}
    return delivery_info.get("routing_key")


def _beat_loop(task_id: str, stop_event: threading.Event, interval: int) -> None:
    """Refresh the heartbeat every ``interval`` seconds until stopped."""
    while not stop_event.wait(interval):
        try:
            registry.refresh_heartbeat(task_id)
        except Exception:  # pragma: no cover - defensive
            logger.debug("Celery recovery: heartbeat loop refresh failed for %s", task_id)


def on_task_prerun(sender=None, task_id=None, task=None, args=None, kwargs=None, **extra):
    """Register the task and start refreshing its heartbeat."""
    name = getattr(task, "name", None) or getattr(sender, "name", None)
    if not task_id or name in _UNTRACKED_TASK_NAMES:
        return
    try:
        registry.register(
            task_id=task_id,
            name=name,
            args=args,
            kwargs=kwargs,
            queue=_queue_of(task),
        )
    except Exception:
        logger.warning("Celery recovery: prerun register failed for %s", task_id, exc_info=True)
        return

    interval = _heartbeat_interval()
    stop_event = threading.Event()
    thread = threading.Thread(
        target=_beat_loop,
        args=(task_id, stop_event, interval),
        name=f"lex-recovery-hb-{task_id[:8]}",
        daemon=True,
    )
    with _threads_lock:
        # Defensive: never leak a prior thread for the same id (requeue reuse).
        prior = _threads.pop(task_id, None)
        if prior is not None:
            prior[1].set()
        _threads[task_id] = (thread, stop_event)
    thread.start()


def on_task_postrun(sender=None, task_id=None, task=None, args=None, kwargs=None, **extra):
    """Stop the heartbeat and deregister the now-terminal task."""
    name = getattr(task, "name", None) or getattr(sender, "name", None)
    if not task_id or name in _UNTRACKED_TASK_NAMES:
        return
    with _threads_lock:
        entry = _threads.pop(task_id, None)
    if entry is not None:
        entry[1].set()
    try:
        registry.deregister(task_id)
    except Exception:
        logger.warning("Celery recovery: postrun deregister failed for %s", task_id, exc_info=True)


def on_task_revoked(sender=None, request=None, **extra):
    """Revoke fast-path: drop a cancelled task from the recovery registry.

    When a task is revoked (typically a user cancellation) we stop its local
    heartbeat thread and deregister it immediately so the supervisor can
    never later detect it as "dead" and requeue it. Best-effort: never raise.
    """
    task_id = getattr(request, "id", None)
    if not task_id:
        return
    with _threads_lock:
        entry = _threads.pop(task_id, None)
    if entry is not None:
        entry[1].set()
    try:
        registry.deregister(task_id)
    except Exception:
        logger.warning("Celery recovery: revoked deregister failed for %s", task_id, exc_info=True)
