"""
Celery worker-recovery package.

Detects dead Celery workers via a per-task heartbeat in Redis and requeues
their in-flight task back to the broker, bounded by ``LEX_TASK_MAX_RETRIES``.
When the budget is exceeded the supervisor writes a ``FAILURE`` to the result
backend so the parent's ``AsyncResult.get()`` raises and the parent
``CalculationModel`` transitions to ``ERROR``.

Public API:
    ``enable()`` — connect signal handlers and start the heartbeat thread on a
    Celery worker process. Safe to call multiple times.

See ``docs/celery-worker-recovery/plan.md`` for the full design.
"""
from __future__ import annotations

import logging
import os
import threading

logger = logging.getLogger(__name__)

# Module-level flag so enable() is idempotent.
_enabled: bool = False
_enable_lock = threading.Lock()


def _is_recovery_enabled() -> bool:
    """Read the master switch from the environment.

    Settings module also computes this, but reading the env directly keeps the
    package importable without Django being configured (matters for tests).
    """
    return os.getenv("LEX_TASK_RECOVERY_ENABLED", "true").lower() == "true"


def enable() -> bool:
    """Activate the recovery system in the current process.

    Returns ``True`` if the system was activated, ``False`` if it was already
    active or is disabled via ``LEX_TASK_RECOVERY_ENABLED=false``.

    Connects the Celery signal handlers in :mod:`heartbeat`. The heartbeat
    thread itself starts on the ``worker_ready`` signal, so calling
    :func:`enable` on a non-worker process is safe and effectively a no-op.
    """
    global _enabled
    if not _is_recovery_enabled():
        logger.debug("LEX_TASK_RECOVERY_ENABLED=false — recovery system not activated")
        return False
    with _enable_lock:
        if _enabled:
            return False
        from .heartbeat import connect_signal_handlers
        connect_signal_handlers()
        _enabled = True
        logger.info("lex celery recovery system enabled")
        return True


def is_enabled() -> bool:
    """Return True if :func:`enable` has activated the system in this process."""
    return _enabled


__all__ = ["enable", "is_enabled"]
