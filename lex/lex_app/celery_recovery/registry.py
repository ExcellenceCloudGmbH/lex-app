"""Redis-backed registry of in-flight tasks for worker recovery.

This is the single source of truth the supervisor reads to decide which
tasks to requeue. For every task a worker is running we keep three keys
(see :mod:`redis_keys`):

* ``index``        — a SET of all tracked task_ids (cheap to enumerate);
* ``payload:<id>`` — the base64(pickle) re-dispatch payload + retry count;
* ``hb:<id>``      — a short-TTL liveness marker the worker refreshes.

When a worker dies abruptly nobody refreshes ``hb:<id>``; it expires while
the task_id is still in ``index`` with a live payload. That divergence —
"tracked but no heartbeat" — is exactly how the supervisor recognises a
dead worker without ever relying on Celery's ``visibility_timeout`` (which
is deliberately ``inf`` in settings).

Every operation is best-effort: it degrades to a silent no-op when Redis
is unavailable or recovery is disabled, so local/sync/test execution is
unaffected.
"""

from __future__ import annotations

import base64
import logging
import pickle
from typing import Any, Dict, List, Optional

from . import redis_keys

logger = logging.getLogger(__name__)

_client = None
_client_initialised = False

# Payload lives long enough to outlast the longest expected task so the
# supervisor can always read it after a death. Bounded so a leaked entry
# (e.g. worker SIGKILLed between requeue and re-registration) self-cleans.
_DEFAULT_PAYLOAD_TTL = 86400  # 24h, matches the pod activeDeadline.


def _settings():
    from django.conf import settings

    return settings


def _enabled_by_config() -> bool:
    s = _settings()
    if not getattr(s, "CELERY_ACTIVE", False):
        return False
    return bool(getattr(s, "LEX_TASK_RECOVERY_ENABLED", True))


def _heartbeat_ttl() -> int:
    s = _settings()
    interval = int(getattr(s, "LEX_TASK_HEARTBEAT_INTERVAL", 5))
    multiplier = int(getattr(s, "LEX_TASK_HB_TTL_MULTIPLIER", 3))
    return max(1, interval * multiplier)


def _payload_ttl() -> int:
    return int(getattr(_settings(), "LEX_TASK_PAYLOAD_TTL_SECONDS", _DEFAULT_PAYLOAD_TTL))


def _get_client():
    """Lazily build a redis-py client from the Celery broker URL.

    Returns ``None`` (so callers no-op) whenever recovery is disabled or
    the client cannot be constructed.
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
            "Celery recovery: redis client unavailable; recovery is inert",
            exc_info=True,
        )
        _client = None
    return _client


def reset_client_cache() -> None:
    """Drop the cached client. Used by tests after changing settings."""
    global _client, _client_initialised
    _client = None
    _client_initialised = False


def _encode(payload: Dict[str, Any]) -> str:
    return base64.b64encode(pickle.dumps(payload)).decode("ascii")


def _decode(raw: str) -> Dict[str, Any]:
    return pickle.loads(base64.b64decode(raw))


def register(
    task_id: str,
    name: str,
    args: Any,
    kwargs: Any,
    queue: Optional[str],
) -> None:
    """Track ``task_id`` as in-flight and stamp its first heartbeat.

    Idempotent across requeues: if a payload already exists (the supervisor
    re-dispatched this same task_id), its accumulated ``retries`` count is
    preserved rather than reset to zero.
    """
    if not task_id:
        return
    client = _get_client()
    if client is None:
        return
    try:
        retries = 0
        existing = client.get(redis_keys.payload_key(task_id))
        if existing:
            try:
                retries = int(_decode(existing).get("retries", 0))
            except Exception:
                retries = 0
        payload = {
            "name": name,
            "args": args,
            "kwargs": kwargs,
            "queue": queue,
            "retries": retries,
        }
        # Single round trip for the payload/index/heartbeat writes, and the
        # SADD result tells us whether the id is NEW to the index — only then
        # is it mirrored onto the in-flight LIST (KEDA scale signal), so a
        # requeue re-register of a still-tracked id never double-counts.
        pipe = client.pipeline()
        pipe.set(redis_keys.payload_key(task_id), _encode(payload), ex=_payload_ttl())
        pipe.sadd(redis_keys.index_key(), task_id)
        pipe.set(redis_keys.heartbeat_key(task_id), "1", ex=_heartbeat_ttl())
        results = pipe.execute()
        if results[1]:  # SADD returned 1 → newly tracked
            client.lpush(redis_keys.inflight_list_key(), task_id)
    except Exception:
        logger.warning("Celery recovery: register failed for %s", task_id, exc_info=True)


def refresh_heartbeat(task_id: str) -> None:
    """Re-stamp the liveness marker for a still-running task.

    Also extends the payload key's TTL so a task that outlives the default
    payload TTL (24h) keeps a recoverable payload for as long as it is
    alive. Only the *expiry* is bumped — never the payload value, which the
    supervisor may have rewritten with an incremented ``retries`` count.
    """
    if not task_id:
        return
    client = _get_client()
    if client is None:
        return
    try:
        client.set(redis_keys.heartbeat_key(task_id), "1", ex=_heartbeat_ttl())
        client.expire(redis_keys.payload_key(task_id), _payload_ttl())
    except Exception:
        logger.debug("Celery recovery: heartbeat refresh failed for %s", task_id)


def deregister(task_id: str) -> None:
    """Stop tracking ``task_id`` (it reached a terminal state)."""
    if not task_id:
        return
    client = _get_client()
    if client is None:
        return
    try:
        client.srem(redis_keys.index_key(), task_id)
        client.delete(redis_keys.payload_key(task_id))
        client.delete(redis_keys.heartbeat_key(task_id))
        # Drain every occurrence from the in-flight LIST mirror (count 0 =
        # all) so the KEDA scale signal returns to zero with the index.
        client.lrem(redis_keys.inflight_list_key(), 0, task_id)
    except Exception:
        logger.warning("Celery recovery: deregister failed for %s", task_id, exc_info=True)


def reconcile_inflight_list() -> int:
    """Rebuild the in-flight LIST mirror from current index-SET membership.

    Rollout / crash safety: a pod that starts with a non-empty index — tasks
    registered before the list existed (mid-cutover), or a leaked entry from
    a crash between the SET and LIST writes — must expose the true amount of
    in-flight work to KEDA. Called once at recovery-supervisor startup.

    Returns the number of entries in the rebuilt list (0 on any failure —
    best-effort like every registry operation).
    """
    client = _get_client()
    if client is None:
        return 0
    try:
        members = list(client.smembers(redis_keys.index_key()) or [])
        pipe = client.pipeline()
        pipe.delete(redis_keys.inflight_list_key())
        if members:
            pipe.lpush(redis_keys.inflight_list_key(), *members)
        pipe.execute()
        return len(members)
    except Exception:
        logger.warning("Celery recovery: reconcile_inflight_list failed", exc_info=True)
        return 0


def list_tracked() -> List[str]:
    """Return every task_id currently tracked for recovery."""
    client = _get_client()
    if client is None:
        return []
    try:
        return list(client.smembers(redis_keys.index_key()) or [])
    except Exception:
        logger.warning("Celery recovery: list_tracked failed", exc_info=True)
        return []


def is_alive(task_id: str) -> bool:
    """True while the worker is still refreshing this task's heartbeat."""
    client = _get_client()
    if client is None:
        return True  # fail safe: never declare dead when Redis is unreadable
    try:
        return bool(client.exists(redis_keys.heartbeat_key(task_id)))
    except Exception:
        logger.warning("Celery recovery: is_alive check failed for %s", task_id, exc_info=True)
        return True


def get_payload(task_id: str) -> Optional[Dict[str, Any]]:
    """Return the re-dispatch payload for ``task_id`` (or ``None``)."""
    client = _get_client()
    if client is None:
        return None
    try:
        raw = client.get(redis_keys.payload_key(task_id))
        return _decode(raw) if raw else None
    except Exception:
        logger.warning("Celery recovery: get_payload failed for %s", task_id, exc_info=True)
        return None


def grant_grace(task_id: str, grace_seconds: int) -> None:
    """Grant a heartbeat grace window (re-stamp ``hb:<id>`` to a short TTL).

    Used right *before* a requeue dispatch: it delays the next dead-task
    detection long enough for the re-dispatched run to start and resume its
    own heartbeat, without touching the payload value. If the dispatch then
    fails, only this grace TTL was consumed — the retry budget (stored in the
    payload) is untouched, so the next pass retries without burning a slot.
    """
    if not task_id:
        return
    client = _get_client()
    if client is None:
        return
    try:
        client.set(redis_keys.heartbeat_key(task_id), "1", ex=max(1, grace_seconds))
    except Exception:
        logger.warning("Celery recovery: grant_grace failed for %s", task_id, exc_info=True)


def persist_payload(task_id: str, payload: Dict[str, Any]) -> None:
    """Write the re-dispatch payload value (with the standard payload TTL).

    Called *after* a successful requeue dispatch so the incremented
    ``retries`` count is only committed once the message is actually on the
    broker. Splitting this from :func:`grant_grace` is what keeps a failed
    dispatch from consuming the retry budget (see :func:`grant_grace`).
    """
    if not task_id:
        return
    client = _get_client()
    if client is None:
        return
    try:
        client.set(redis_keys.payload_key(task_id), _encode(payload), ex=_payload_ttl())
    except Exception:
        logger.warning("Celery recovery: persist_payload failed for %s", task_id, exc_info=True)


def record_requeue(task_id: str, payload: Dict[str, Any], grace_seconds: int) -> None:
    """Persist the incremented payload and grant a heartbeat grace window.

    Thin backward-compatible wrapper kept for any caller/test that still
    references the combined operation. New code should call
    :func:`grant_grace` (before dispatch) and :func:`persist_payload` (after
    dispatch) separately so a failed dispatch does not burn the retry budget.
    """
    grant_grace(task_id, grace_seconds)
    persist_payload(task_id, payload)


def try_acquire_recovery_lock(task_id: str, ttl_seconds: int) -> bool:
    """Acquire a short-lived per-task recovery lock (``SET ... NX EX``).

    Returns ``True`` only when *this* supervisor won the lock, so two
    supervisor replicas never act on the same dead task in the same window.
    Best-effort: returns ``False`` on any Redis error or when recovery is
    disabled, so a supervisor that cannot take the lock simply skips the task
    (which is the safe outcome — another replica may hold it).
    """
    if not task_id:
        return False
    client = _get_client()
    if client is None:
        return False
    try:
        lock_key = f"{redis_keys.key_prefix()}lex:recover:lock:{task_id}"
        return bool(client.set(lock_key, "1", nx=True, ex=max(1, ttl_seconds)))
    except Exception:
        logger.warning(
            "Celery recovery: try_acquire_recovery_lock failed for %s", task_id, exc_info=True
        )
        return False
