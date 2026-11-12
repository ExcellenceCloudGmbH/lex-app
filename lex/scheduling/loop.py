"""The scheduler's clock: a daemon thread in the backend that serves the app.

Every instance has exactly one backend, and it is always on unless the instance
is paused or scaled to zero. So the clock lives there — no extra pod, no Celery
beat — and runs :func:`lex.scheduling.dispatch.run_due_jobs` every
``LEX_SCHEDULER_TICK_SECONDS``.

:func:`start_scheduler_for_served_backend` is called from ``lex_app/asgi.py``,
which only the process serving the ASGI application imports: the uvicorn
reloader's parent, Celery workers, management commands and test runs never
start a clock. It is on by default; ``LEX_SCHEDULER_ENABLED=false`` turns it off
and says so at WARNING, because a scheduler that is silently off looks exactly
like a scheduler with nothing to do.

Claiming makes extra clocks harmless (``uvicorn --workers N``, a
``lex run_scheduler`` next to a backend): each due slot is still run once.
"""

from __future__ import annotations

import logging
import threading
from typing import Callable, Optional

from django.conf import settings
from django.db import close_old_connections

logger = logging.getLogger(__name__)

__all__ = ["SchedulerLoop", "scheduler_enabled", "start_scheduler_for_served_backend", "stop_scheduler"]

_loop: Optional["SchedulerLoop"] = None
_loop_lock = threading.Lock()


def scheduler_enabled() -> bool:
    return bool(getattr(settings, "LEX_SCHEDULER_ENABLED", True))


def tick_seconds() -> float:
    return max(1.0, float(getattr(settings, "LEX_SCHEDULER_TICK_SECONDS", 30)))


class SchedulerLoop(threading.Thread):
    """Sync the code-declared schedules once, then run a tick every ``interval`` seconds."""

    def __init__(self, interval: float, *, tick: Optional[Callable] = None, sync: Optional[Callable] = None):
        super().__init__(name="lex-scheduler", daemon=True)
        self.interval = interval
        self._stop_event = threading.Event()
        self._tick = tick
        self._sync = sync
        self.ticks = 0

    def stop(self) -> None:
        self._stop_event.set()

    def _run_tick(self) -> None:
        from lex.scheduling.dispatch import run_due_jobs

        (self._tick or run_due_jobs)()

    def _run_sync(self) -> None:
        from lex.scheduling.registry import sync_code_jobs

        (self._sync or sync_code_jobs)()

    def _guarded(self, step: Callable[[], None], what: str) -> None:
        # A long-lived thread has no request cycle to close its connection; one
        # that died while the loop waited would fail every later pass.
        close_old_connections()
        try:
            step()
        except Exception:  # noqa: BLE001 - the clock must survive any one pass
            logger.exception("Scheduler %s failed; the next tick tries again.", what)
        finally:
            close_old_connections()

    def run(self) -> None:
        logger.info("Scheduler started: checking for due jobs every %ss.", self.interval)
        self._guarded(self._run_sync, "sync of code-declared schedules")
        while not self._stop_event.is_set():
            self._guarded(self._run_tick, "tick")
            self.ticks += 1
            self._stop_event.wait(self.interval)
        logger.info("Scheduler stopped.")


def _serving_backend() -> bool:
    from lex.lex_app.apps import running_in_uvicorn

    return running_in_uvicorn()


def start_scheduler_for_served_backend(*, force: bool = False) -> Optional[SchedulerLoop]:
    """Start the clock in this process if it serves the app; return it, or ``None``.

    Idempotent. ``force`` skips the "is this the served backend" check (tests).
    """
    global _loop
    if not force and not _serving_backend():
        return None
    if not scheduler_enabled():
        logger.warning(
            "Scheduler disabled (LEX_SCHEDULER_ENABLED=false): no scheduled job will run on this instance."
        )
        return None
    with _loop_lock:
        if _loop is not None and _loop.is_alive():
            return _loop
        _loop = SchedulerLoop(tick_seconds())
        _loop.start()
        return _loop


def stop_scheduler(timeout: float = 5.0) -> None:
    """Stop the clock started by :func:`start_scheduler_for_served_backend`, if any."""
    global _loop
    with _loop_lock:
        loop, _loop = _loop, None
    if loop is not None:
        loop.stop()
        loop.join(timeout)
