from __future__ import absolute_import

import logging
import os
from typing import Optional

from celery import Celery
from celery.app.control import Control
from celery.signals import task_postrun
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

# Connect the worker-recovery signal handlers. Idempotent and gated by
# LEX_TASK_RECOVERY_ENABLED, so this is safe on every Celery-loading process
# (web, worker, beat, eager-test). See docs/celery-worker-recovery/plan.md.
try:
    from lex.lex_app import celery_recovery
    celery_recovery.enable()
except Exception:
    logger.exception("Failed to enable lex celery recovery system; continuing without it")


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
    Shut down only the worker that completed the task in non-local deployments.

    Workers in this project run with concurrency/prefetch set to 1, so once the
    current task finishes there is no additional in-flight work reserved on that
    worker. Targeting the current hostname avoids broadcasting shutdown to other
    workers that may still be busy.
    """
    if not _is_non_local_deployment_target() or task is None:
        return

    hostname = _get_worker_hostname(task)
    app_instance = getattr(task, "app", None)
    if hostname is None or app_instance is None:
        return

    try:
        logger.info(
            "Shutting down Celery worker %s after task %s completed",
            hostname,
            task_id,
        )
        Control(app=app_instance).shutdown(destination=[hostname])
    except Exception:
        logger.exception(
            "Failed to shut down Celery worker %s after task %s",
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
