from __future__ import absolute_import

import logging
import os
import signal
import threading
from typing import Optional

from celery import Celery
from celery.app.control import Control
from celery.signals import task_postrun
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


@Panel.register
def lex_shutdown_if_idle(panel, completed_task_id=None):
    """
    Remote-control command that runs in the worker's MainProcess.

    Checks the canonical request bookkeeping (``celery.worker.state``) and
    only triggers a warm shutdown if no other task is active or reserved
    on this worker. The just-completed ``completed_task_id`` is subtracted
    because, depending on timing, MainProcess may not have processed the
    pool's "task ready" message yet when this command arrives.

    Returns a small dict for observability (visible via ``celery inspect``
    style replies when ``reply=True``).
    """
    try:
        active_ids = {getattr(req, "id", None) for req in worker_state.active_requests}
        reserved_ids = {getattr(req, "id", None) for req in worker_state.reserved_requests}
    except Exception:  # pragma: no cover - defensive
        logger.exception("lex_shutdown_if_idle: failed to read worker state")
        return {"shutting_down": False, "error": "state_unavailable"}

    pending = (active_ids | reserved_ids) - {None}
    if completed_task_id:
        pending.discard(completed_task_id)

    if pending:
        return {
            "shutting_down": False,
            "pending_count": len(pending),
            "pending": sorted(pending),
        }

    # Worker is idle. Schedule a SIGTERM on a short delay so we can return
    # the reply (and let the broadcast machinery finish) before the warm
    # shutdown begins. Celery's MainProcess handles SIGTERM as a graceful
    # shutdown (finish in-flight tasks, then exit) — there are none here.
    def _terminate():
        try:
            os.kill(os.getpid(), signal.SIGTERM)
        except Exception:  # pragma: no cover - defensive
            logger.exception("lex_shutdown_if_idle: SIGTERM failed")

    threading.Timer(0.05, _terminate).start()
    return {"shutting_down": True}


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
