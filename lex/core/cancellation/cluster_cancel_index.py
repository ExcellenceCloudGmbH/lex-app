"""Cluster-wide cancellation index backed by Redis.

Additive, best-effort sidecar to the per-process
``ActiveCalculationStateStore``. It records each calculation node's
Celery ``task_id`` under a tree keyed by ``calculation_id`` so the
backend's ``CalculationModel.cancel`` can discover and revoke every
descendant task regardless of which worker pod registered it, and
exposes a cooperative "cancelled" marker a late-booting worker checks
at task start.

Every operation degrades to a silent no-op when Redis is unavailable,
``CELERY_ACTIVE`` is off, or ``LEX_CLUSTER_CANCEL_ENABLED`` is false, so
local/sync/test execution is unaffected. The keys are namespaced with
the instance identifier (matching Celery's broker ``global_keyprefix``)
so instances sharing a Redis never collide.
"""

from __future__ import annotations

import logging
import os
from typing import Dict, Optional

logger = logging.getLogger(__name__)

_client = None
_client_initialised = False


def _settings():
    from django.conf import settings

    return settings


def _enabled_by_config() -> bool:
    s = _settings()
    if not getattr(s, "CELERY_ACTIVE", False):
        return False
    return bool(getattr(s, "LEX_CLUSTER_CANCEL_ENABLED", True))


def _tree_ttl() -> int:
    return int(getattr(_settings(), "LEX_CLUSTER_CANCEL_TREE_TTL_SECONDS", 14400))


def _marker_ttl() -> int:
    return int(getattr(_settings(), "LEX_CLUSTER_CANCEL_MARKER_TTL_SECONDS", 3600))


def _key_prefix() -> str:
    return f"{os.getenv('INSTANCE_RESOURCE_IDENTIFIER', 'celery')}:"


def _tree_key(calculation_id: str) -> str:
    return f"{_key_prefix()}lex:calc:tree:{calculation_id}"


def _marker_key(calculation_id: str) -> str:
    return f"{_key_prefix()}lex:calc:cancelled:{calculation_id}"


def _get_client():
    """Lazily build a redis-py client from the Celery broker URL.

    Returns ``None`` (so callers no-op) whenever the index is disabled
    by config or the client cannot be constructed.
    """
    global _client, _client_initialised
    if not _enabled_by_config():
        return None
    if _client_initialised:
        return _client
    _client_initialised = True
    try:
        import redis

        url = getattr(_settings(), "CELERY_BROKER_URL", None)
        _client = redis.from_url(url, decode_responses=True) if url else None
    except Exception:
        logger.warning(
            "Cluster cancel index: redis client unavailable; "
            "falling back to signals-only cancellation",
            exc_info=True,
        )
        _client = None
    return _client


def reset_client_cache() -> None:
    """Drop the cached client. Used by tests after changing settings."""
    global _client, _client_initialised
    _client = None
    _client_initialised = False


def register_task(
    calculation_id: Optional[str], record_id: str, task_id: Optional[str]
) -> None:
    """Record ``record_id -> task_id`` in the tree for ``calculation_id``."""
    if not calculation_id or not record_id or not task_id:
        return
    client = _get_client()
    if client is None:
        return
    try:
        key = _tree_key(calculation_id)
        client.hset(key, record_id, str(task_id))
        client.expire(key, _tree_ttl())
    except Exception:
        logger.warning(
            "Cluster cancel index: register_task failed for %s", record_id,
            exc_info=True,
        )


def unregister_task(calculation_id: Optional[str], record_id: str) -> None:
    """Remove ``record_id`` from the tree (node reached a terminal state)."""
    if not calculation_id or not record_id:
        return
    client = _get_client()
    if client is None:
        return
    try:
        client.hdel(_tree_key(calculation_id), record_id)
    except Exception:
        logger.warning(
            "Cluster cancel index: unregister_task failed for %s", record_id,
            exc_info=True,
        )


def get_tree(calculation_id: Optional[str]) -> Dict[str, str]:
    """Return ``{record_id: task_id}`` for every node in the tree."""
    if not calculation_id:
        return {}
    client = _get_client()
    if client is None:
        return {}
    try:
        return dict(client.hgetall(_tree_key(calculation_id)) or {})
    except Exception:
        logger.warning(
            "Cluster cancel index: get_tree failed for %s", calculation_id,
            exc_info=True,
        )
        return {}


def mark_cancelled(calculation_id: Optional[str]) -> None:
    """Set the cooperative cancelled marker (TTL-bounded)."""
    if not calculation_id:
        return
    client = _get_client()
    if client is None:
        return
    try:
        client.set(_marker_key(calculation_id), "1", ex=_marker_ttl())
    except Exception:
        logger.warning(
            "Cluster cancel index: mark_cancelled failed for %s", calculation_id,
            exc_info=True,
        )


def is_cancelled(calculation_id: Optional[str]) -> bool:
    """True when the cooperative cancelled marker is set."""
    if not calculation_id:
        return False
    client = _get_client()
    if client is None:
        return False
    try:
        return bool(client.exists(_marker_key(calculation_id)))
    except Exception:
        logger.warning(
            "Cluster cancel index: is_cancelled failed for %s", calculation_id,
            exc_info=True,
        )
        return False
