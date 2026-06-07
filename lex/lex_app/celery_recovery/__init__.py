"""Celery worker-recovery subsystem.

Detects abruptly-killed workers via expiring Redis heartbeats and requeues
their in-flight tasks under a bounded retry budget, writing a terminal
FAILURE/ABORTED state when the budget is exhausted. This is layered on top
of Celery deliberately, because ``visibility_timeout=float("inf")`` (see
settings) neutralises Celery's own Redis-transport redelivery — recovery
must never rely on ``visibility_timeout``.

Wire it from the Celery app module via :func:`enable`. Everything is gated
behind ``CELERY_ACTIVE`` and ``LEX_TASK_RECOVERY_ENABLED`` so local/sync/CI
runs are unaffected.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

_enabled = False


def _recovery_enabled() -> bool:
    from django.conf import settings

    if not getattr(settings, "CELERY_ACTIVE", False):
        return False
    return bool(getattr(settings, "LEX_TASK_RECOVERY_ENABLED", True))


def enable(app=None) -> bool:
    """Connect the heartbeat signal handlers. Idempotent; gated by config.

    Returns ``True`` when the handlers are now connected, ``False`` when
    recovery is disabled (so the caller can log accordingly).
    """
    global _enabled
    if _enabled:
        return True
    if not _recovery_enabled():
        logger.debug("Celery recovery disabled (CELERY_ACTIVE/LEX_TASK_RECOVERY_ENABLED)")
        return False

    from celery.signals import task_postrun, task_prerun, task_revoked

    from . import heartbeat

    task_prerun.connect(heartbeat.on_task_prerun, weak=False)
    task_postrun.connect(heartbeat.on_task_postrun, weak=False)
    task_revoked.connect(heartbeat.on_task_revoked, weak=False)
    _enabled = True
    logger.info("Celery worker-recovery heartbeat handlers connected")
    return True
