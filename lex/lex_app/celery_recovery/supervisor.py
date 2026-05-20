"""
Supervisor sweep — detects dead workers via stale heartbeats and re-publishes
their in-flight task back to the broker, preserving the original ``task_id``.

The supervisor is stateless. Each invocation of :func:`sweep_once` runs one
scan-and-act pass. It is intended to be invoked by the Celery beat task in
:mod:`lex.lex_app.celery_recovery.tasks` every
``LEX_TASK_SUPERVISOR_SCAN_INTERVAL`` seconds.

Phase 3 scope:

- Scan ``lex:task:*`` for stale ``last_hb_iso``.
- Skip tasks whose worker is still alive (worker key present).
- Acquire a per-task supervisor lease (``lex:task:<id>:lock``) so multiple
  supervisor replicas can't double-act.
- If ``attempt + 1 <= max_retries``: re-publish with ``app.send_task`` reusing
  the original ``task_id`` and a ``lex_attempt`` header so ``task_prerun``
  on the next worker records the right counter.
- If ``attempt + 1 > max_retries``: Phase 3 just logs and leaves the task
  alone. Phase 4 will inject a FAILURE into the result backend so the
  parent's ``AsyncResult.get()`` raises.

What this module deliberately does **not** do (yet):

- Touch Celery's internal ``unacked`` / ``unacked_index`` keys. With our
  ``visibility_timeout=float("inf")`` configuration nothing will ever
  rediscover those entries, so the leak is bounded by the number of dead
  workers and the result-backend TTL. Phase 6 will add best-effort cleanup
  after confirming the exact key names on a running staging Redis.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Iterable, Optional, Tuple

from .exceptions import MaxRequeueExceeded
from .redis_client import get_client
from .redis_keys import task_key, task_lock_key, task_scan_pattern, worker_key

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Knob accessors. Re-read on each call so tests can patch via the environment.
# ---------------------------------------------------------------------------

def _heartbeat_interval() -> int:
    return int(os.getenv("LEX_TASK_HEARTBEAT_INTERVAL", "5"))


def _hb_ttl_multiplier() -> int:
    return int(os.getenv("LEX_TASK_HB_TTL_MULTIPLIER", "3"))


def _stale_threshold_seconds() -> float:
    """Window after which a task with no heartbeat is considered orphaned."""
    return float(_heartbeat_interval() * _hb_ttl_multiplier())


def _max_retries_default() -> int:
    return int(os.getenv("LEX_TASK_MAX_RETRIES", "4"))


def _lock_ttl_seconds() -> int:
    """How long one supervisor instance holds the per-task lease."""
    return int(os.getenv("LEX_TASK_LOCK_TTL", "30"))


# ---------------------------------------------------------------------------
# Sweep entry point.
# ---------------------------------------------------------------------------

def sweep_once(now: Optional[datetime] = None) -> dict:
    """Run one scan-and-act pass.

    Returns a small summary dict useful for tests and log output:
    ``{"scanned": N, "stale": N, "requeued": N, "deferred_at_cap": N, "skipped": N}``.
    """
    if now is None:
        now = datetime.now(timezone.utc)

    client = get_client()
    summary = {
        "scanned": 0, "stale": 0, "requeued": 0, "deferred_at_cap": 0,
        "failed": 0, "skipped": 0,
    }

    for raw_key in _iter_task_keys(client):
        summary["scanned"] += 1
        task_id = _extract_task_id(raw_key)
        if not task_id:
            continue
        envelope = client.hgetall(raw_key)
        if not envelope:
            continue

        last_hb_iso = _as_str(envelope.get(b"last_hb_iso"))
        if not _is_stale(last_hb_iso, now):
            continue
        summary["stale"] += 1
        last_hb_age_s = _hb_age_seconds(last_hb_iso, now)

        # If the worker that owns this task is still alive, the heartbeat
        # thread may just be running slow. Skip — the next sweep will catch
        # it if the worker is actually dead.
        hostname = _as_str(envelope.get(b"hostname"))
        if hostname and client.exists(worker_key(hostname).encode("utf-8")):
            logger.warning(
                "lex_recovery action=skip reason=worker_alive_but_task_stale "
                "task_id=%s hostname=%s last_hb_age_s=%s",
                task_id, hostname, last_hb_age_s,
            )
            summary["skipped"] += 1
            continue

        if not _acquire_lock(client, task_id):
            summary["skipped"] += 1
            continue
        try:
            attempt = _as_int(envelope.get(b"attempt"), default=0)
            max_retries = _as_int(envelope.get(b"max_retries"), default=_max_retries_default())
            if attempt + 1 > max_retries:
                injected = _write_failure_to_backend(
                    client, task_id, attempt, max_retries, hostname, last_hb_age_s,
                )
                if injected:
                    summary["failed"] += 1
                else:
                    # Already terminal in the backend — nothing to do, but the
                    # hash cleanup still runs so future sweeps stop touching it.
                    summary["deferred_at_cap"] += 1
                continue

            new_attempt = attempt + 1
            _republish_task(client, task_id, envelope, new_attempt)
            summary["requeued"] += 1
            logger.info(
                "lex_recovery action=requeue reason=stale_heartbeat task_id=%s "
                "attempt=%s new_attempt=%s max_retries=%s "
                "previous_hostname=%s last_hb_age_s=%s",
                task_id, attempt, new_attempt, max_retries, hostname, last_hb_age_s,
            )
        except Exception:
            logger.exception("lex_recovery sweep action failed task_id=%s", task_id)
        finally:
            _release_lock(client, task_id)

    return summary


# ---------------------------------------------------------------------------
# Internals.
# ---------------------------------------------------------------------------

def _iter_task_keys(client) -> Iterable[bytes]:
    """Yield every ``lex:task:<id>`` key, skipping the ``:lock`` companions."""
    pattern = task_scan_pattern()
    for key in client.scan_iter(match=pattern):
        if isinstance(key, str):
            key = key.encode("utf-8")
        if key.endswith(b":lock"):
            continue
        yield key


def _extract_task_id(key: bytes) -> Optional[str]:
    text = key.decode("utf-8", errors="replace")
    parts = text.split(":", 2)
    if len(parts) != 3:
        return None
    return parts[2]


def _as_str(value) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def _as_int(value, default: int) -> int:
    s = _as_str(value)
    if s is None:
        return default
    try:
        return int(s)
    except ValueError:
        return default


def _is_stale(last_hb_iso: Optional[str], now: datetime) -> bool:
    if not last_hb_iso:
        # Missing heartbeat = treat as stale. Worker never wrote one, or the
        # field expired with the hash (we EXPIRE generously so this is rare).
        return True
    try:
        last = datetime.fromisoformat(last_hb_iso)
    except ValueError:
        return True
    if last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    age = (now - last).total_seconds()
    return age > _stale_threshold_seconds()


def _hb_age_seconds(last_hb_iso: Optional[str], now: datetime) -> Optional[float]:
    """Return the age of ``last_hb_iso`` in seconds, or ``None`` if unparsable.

    Used purely for log enrichment so DevOps can answer "how long was the
    worker silent before we acted". Returns ``None`` if the field is missing
    or malformed; callers must handle that.
    """
    if not last_hb_iso:
        return None
    try:
        last = datetime.fromisoformat(last_hb_iso)
    except ValueError:
        return None
    if last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    return round((now - last).total_seconds(), 3)


def _acquire_lock(client, task_id: str) -> bool:
    """SET NX EX — only one supervisor can act on a task at a time."""
    return bool(client.set(task_lock_key(task_id), b"1", ex=_lock_ttl_seconds(), nx=True))


def _release_lock(client, task_id: str) -> None:
    try:
        client.delete(task_lock_key(task_id))
    except Exception:
        logger.debug("lock release failed for task_id=%s", task_id, exc_info=True)


def _write_failure_to_backend(
    client, task_id: str, attempt: int, max_retries: int,
    hostname: Optional[str], last_hb_age_s: Optional[float] = None,
) -> bool:
    """Inject a terminal FAILURE for ``task_id`` so the parent's ``.get()`` raises.

    Returns ``True`` if we wrote the failure, ``False`` if the result was already
    in a terminal state (a worker beat us to it). In both cases we delete the
    envelope hash so the supervisor stops scanning the task.

    Idempotency notes:

    - We check ``backend.get_task_meta`` first. If the state is already SUCCESS
      / FAILURE / REVOKED we leave the backend untouched.
    - If the meta lookup itself fails, we still attempt ``mark_as_failure`` —
      better a duplicate failure than a silently-stuck parent.
    - The envelope hash is deleted last so we never lose track of the task on a
      mark_as_failure error.
    """
    from celery import current_app
    from celery import states

    exc = MaxRequeueExceeded(
        f"Task {task_id} exceeded {max_retries} retries after worker loss "
        f"(last_hostname={hostname}, attempt={attempt})",
        worker_hostname=hostname,
        attempt=attempt,
        task_id=task_id,
    )

    backend = current_app.backend
    already_terminal = False
    try:
        meta = backend.get_task_meta(task_id)
        state = (meta or {}).get("status")
        if state in states.READY_STATES:
            already_terminal = True
    except Exception:
        logger.debug(
            "lex_recovery backend.get_task_meta failed for task_id=%s; "
            "proceeding with mark_as_failure anyway", task_id, exc_info=True,
        )

    wrote = False
    if not already_terminal:
        try:
            backend.mark_as_failure(task_id, exc)
            wrote = True
            logger.warning(
                "lex_recovery action=mark_as_failure reason=max_retries_exceeded "
                "task_id=%s attempt=%s max_retries=%s previous_hostname=%s "
                "last_hb_age_s=%s",
                task_id, attempt, max_retries, hostname, last_hb_age_s,
            )
        except Exception:
            logger.exception(
                "lex_recovery mark_as_failure failed task_id=%s; envelope kept",
                task_id,
            )
            return False
    else:
        logger.info(
            "lex_recovery action=skip_failure_already_terminal task_id=%s "
            "attempt=%s max_retries=%s",
            task_id, attempt, max_retries,
        )

    # Drop the envelope so we don't keep scanning a task that's now finalised.
    try:
        client.delete(task_key(task_id))
    except Exception:
        logger.debug(
            "lex_recovery envelope cleanup failed task_id=%s", task_id, exc_info=True,
        )

    return wrote


def _republish_task(client, task_id: str, envelope: dict, new_attempt: int) -> None:
    """Re-publish the task to its original queue, reusing the task_id.

    Writes the bumped ``attempt`` back into the Redis hash *before* sending so
    that even if the ``lex_attempt`` header is dropped somewhere in the
    transport, the next ``task_prerun`` reads the right value when it
    overwrites the envelope.
    """
    from .heartbeat import _unpickle_b64

    task_name = _as_str(envelope.get(b"task_name")) or ""
    queue = _as_str(envelope.get(b"queue")) or None
    args = _unpickle_b64(envelope.get(b"args_b64") or b"") if envelope.get(b"args_b64") else ()
    kwargs = _unpickle_b64(envelope.get(b"kwargs_b64") or b"") if envelope.get(b"kwargs_b64") else {}

    # Persist the new attempt counter to the hash before send_task so
    # that the supervisor and the next worker agree on the count.
    client.hset(task_key(task_id), b"attempt", str(new_attempt).encode("utf-8"))

    from celery import current_app

    current_app.send_task(
        task_name,
        args=args,
        kwargs=kwargs,
        task_id=task_id,
        queue=queue or None,
        headers={"lex_attempt": new_attempt},
    )


__all__ = ["sweep_once"]
