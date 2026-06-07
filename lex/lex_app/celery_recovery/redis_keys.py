"""Redis key builders for the worker-recovery subsystem.

All keys are namespaced with the instance identifier so multiple lex
instances sharing one Redis never collide. The prefix matches Celery's
broker ``global_keyprefix`` convention (``<instance>:``) used by
``cluster_cancel_index`` and the Celery transport options in settings.
"""

from __future__ import annotations

import os


def key_prefix() -> str:
    """Instance-scoped prefix (matches Celery's broker ``global_keyprefix``)."""
    return f"{os.getenv('INSTANCE_RESOURCE_IDENTIFIER', 'celery')}:"


def heartbeat_key(task_id: str) -> str:
    """Liveness marker refreshed by the running worker; TTL-bounded."""
    return f"{key_prefix()}lex:recover:hb:{task_id}"


def payload_key(task_id: str) -> str:
    """Re-dispatch payload (base64 pickle) for one in-flight task."""
    return f"{key_prefix()}lex:recover:task:{task_id}"


def index_key() -> str:
    """SET of every task_id currently tracked for recovery."""
    return f"{key_prefix()}lex:recover:index"
