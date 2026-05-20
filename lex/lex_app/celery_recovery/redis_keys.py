"""
Single source of truth for the Redis key namespace used by the recovery system.

All keys are namespaced under ``lex:`` to keep them out of Celery's transport
keys (which use ``unacked`` / ``unacked_index`` plus the ``global_keyprefix``
from ``CELERY_BROKER_TRANSPORT_OPTIONS``).
"""
from __future__ import annotations

NAMESPACE = "lex"


def worker_key(hostname: str) -> str:
    """Liveness key for a Celery worker. Set with a short TTL by the heartbeat."""
    return f"{NAMESPACE}:wrk:{hostname}"


def worker_scan_pattern() -> str:
    """SCAN pattern matching every worker liveness key."""
    return f"{NAMESPACE}:wrk:*"


def task_key(task_id: str) -> str:
    """Tracking hash for an in-flight task. Owned by ``task_prerun`` / ``task_postrun``."""
    return f"{NAMESPACE}:task:{task_id}"


def task_lock_key(task_id: str) -> str:
    """Supervisor lease key — prevents two supervisor replicas acting on the same task."""
    return f"{NAMESPACE}:task:{task_id}:lock"


def task_scan_pattern() -> str:
    """SCAN pattern that matches every task tracking hash (excludes locks)."""
    # Locks share the ``:task:`` prefix but end with ``:lock``; the supervisor
    # filters them out explicitly. SCAN MATCH is glob, not regex, so we keep a
    # broad pattern and post-filter.
    return f"{NAMESPACE}:task:*"


__all__ = [
    "NAMESPACE",
    "worker_key",
    "worker_scan_pattern",
    "task_key",
    "task_lock_key",
    "task_scan_pattern",
]
