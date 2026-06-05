from __future__ import absolute_import

import logging
import os
import signal
import threading
import time
from typing import Optional

from celery import Celery
from celery.app.control import Control
from celery.signals import task_postrun, task_revoked, worker_ready, worker_shutting_down
from celery.worker import state as worker_state
from celery.worker.control import Panel
from django.apps import apps

# set the default Django settings module for the 'celery' program.
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'lex_app.settings')

logger = logging.getLogger(__name__)

app = Celery('lex_app')

# Using a string here means the worker doesn't have to serialize
# the configuration object to child processes.
# - namespace='CELERY' means all celery-related configuration keys
#   should have a `CELERY_` prefix.
app.config_from_object('django.conf:settings', namespace='CELERY')
# app.conf.update(
#     task_serializer='dill',
#     accept_content='dill',
# )

app.autodiscover_tasks(lambda: [n.name for n in apps.get_app_configs()])


# Module-level guard so at most one SIGTERM timer is ever armed per process.
_shutdown_lock = threading.Lock()
_shutdown_scheduled = False


def _read_pending_task_ids(exclude_task_ids=()):
    """
    Return the set of task ids this worker still owns (active or reserved),
    minus ``exclude_task_ids``. Runs in the worker MainProcess. Raises if the
    canonical ``celery.worker.state`` bookkeeping cannot be read.
    """
    active_ids = {getattr(req, "id", None) for req in worker_state.active_requests}
    reserved_ids = {getattr(req, "id", None) for req in worker_state.reserved_requests}
    pending = (active_ids | reserved_ids) - {None}
    pending -= set(exclude_task_ids)
    return pending


def _warm_shutdown_if_idle(exclude_task_ids=()):
    """
    Schedule a single graceful SIGTERM (warm shutdown) iff this worker has no
    active or reserved task other than ``exclude_task_ids``. MainProcess-only.

    Idempotent: once a shutdown is scheduled, ``_shutdown_scheduled`` makes
    subsequent calls no-ops so stacked SIGTERM timers can never be armed.

    Returns a small dict for observability.
    """
    global _shutdown_scheduled
    try:
        pending = _read_pending_task_ids(exclude_task_ids)
    except Exception:  # pragma: no cover - defensive
        logger.exception("_warm_shutdown_if_idle: failed to read worker state")
        return {"shutting_down": False, "error": "state_unavailable"}

    if pending:
        return {
            "shutting_down": False,
            "pending_count": len(pending),
            "pending": sorted(pending),
        }

    with _shutdown_lock:
        if _shutdown_scheduled:
            return {"shutting_down": True, "already_scheduled": True}
        _shutdown_scheduled = True

    def _terminate():
        try:
            os.kill(os.getpid(), signal.SIGTERM)
        except Exception:  # pragma: no cover - defensive
            logger.exception("_warm_shutdown_if_idle: SIGTERM failed")

    threading.Timer(0.05, _terminate).start()
    return {"shutting_down": True}


@Panel.register
def lex_shutdown_if_idle(panel, completed_task_id=None):
    """
    Remote-control command (MainProcess) used by the existing ``task_postrun``
    broadcast path. Thin wrapper over ``_warm_shutdown_if_idle`` that excludes
    the just-completed task id (MainProcess may not have processed the pool's
    "task ready" message yet when this command arrives).
    """
    exclude = {completed_task_id} if completed_task_id else set()
    return _warm_shutdown_if_idle(exclude_task_ids=exclude)


def _is_non_local_deployment_target() -> bool:
    """
    Return True when worker auto-shutdown should be active.

    Treat an unset/empty deployment target the same as local execution so the
    behavior stays opt-in for deployed environments.
    """
    deployment_target = os.getenv("DEPLOYMENT_TARGET", "").strip().lower()
    return deployment_target not in {"", "local"}


def _get_worker_hostname(task) -> Optional[str]:
    request = getattr(task, "request", None)
    hostname = getattr(request, "hostname", None)
    return hostname or None


@task_postrun.connect
def shutdown_worker_after_task_completion(
    sender=None,
    task_id=None,
    task=None,
    args=None,
    kwargs=None,
    **extra,
):
    """
    Request a warm shutdown of the worker that completed this task — but only
    if that worker has no other active or reserved tasks.

    The decision is made in the worker's MainProcess via the
    ``lex_shutdown_if_idle`` remote-control command (see above), because the
    child fork running this signal handler does not have an accurate view of
    sibling pool workers or of the consumer's reserved queue.

    Safe under any ``--concurrency`` setting and under ``prefetch-multiplier``
    > 1, because the MainProcess-side check considers both ``active_requests``
    and ``reserved_requests`` before terminating.
    """
    if not _is_non_local_deployment_target() or task is None:
        return

    hostname = _get_worker_hostname(task)
    app_instance = getattr(task, "app", None)
    if hostname is None or app_instance is None:
        return

    try:
        logger.info(
            "Requesting idle-shutdown of Celery worker %s after task %s",
            hostname,
            task_id,
        )
        app_instance.control.broadcast(
            "lex_shutdown_if_idle",
            arguments={"completed_task_id": task_id},
            destination=[hostname],
            reply=False,
        )
    except Exception:
        logger.exception(
            "Failed to request idle-shutdown for worker %s after task %s",
            hostname,
            task_id,
        )

# Configuration validation function
def validate_celery_redis_config():
    """
    Validate that Celery is properly configured with Redis.
    This function can be called during startup to ensure configuration is correct.
    """
    try:
        # Check broker connection
        broker_url = app.conf.broker_url
        result_backend = app.conf.result_backend
        
        if not broker_url.startswith('redis://'):
            logger.debug(f"Celery broker is not using Redis: {broker_url}")
            return False
            
        if not result_backend.startswith('redis://'):
            logger.debug(f"Celery result backend is not using Redis: {result_backend}")
            return False
            
        logger.info(f"Celery Redis configuration validated - Broker: {broker_url}, Backend: {result_backend}")
        return True
        
    except Exception as e:
        logger.error(f"Failed to validate Celery Redis configuration: {e}")
        return False

# Validate configuration on import (optional - can be disabled in production)
if os.getenv('CELERY_VALIDATE_CONFIG', 'True').lower() == 'true':
    validate_celery_redis_config()
