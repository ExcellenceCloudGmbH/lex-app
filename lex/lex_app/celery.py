from __future__ import absolute_import

import os
import logging

from celery import Celery

# set the default Django settings module for the 'celery' program.
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'lex_app.settings')

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

# Load task modules from all registered Django app configs.
from django.apps import apps
app.autodiscover_tasks(lambda: [n.name for n in apps.get_app_configs()])

# Configuration validation function
def validate_celery_redis_config():
    """
    Validate that Celery is properly configured with Redis.
    This function can be called during startup to ensure configuration is correct.
    """
    logger = logging.getLogger(__name__)
    
    try:
        # Check broker connection
        broker_url = app.conf.broker_url
        result_backend = app.conf.result_backend
        
        if not broker_url.startswith('redis://'):
            logger.warning(f"Celery broker is not using Redis: {broker_url}")
            return False
            
        if not result_backend.startswith('redis://'):
            logger.warning(f"Celery result backend is not using Redis: {result_backend}")
            return False
            
        logger.info(f"Celery Redis configuration validated - Broker: {broker_url}, Backend: {result_backend}")
        return True
        
    except Exception as e:
        logger.error(f"Failed to validate Celery Redis configuration: {e}")
        return False

# Validate configuration on import (optional - can be disabled in production)
if os.getenv('CELERY_VALIDATE_CONFIG', 'True').lower() == 'true':
    validate_celery_redis_config()