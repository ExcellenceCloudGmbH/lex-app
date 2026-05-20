"""
Worker-side heartbeat and task lifecycle bookkeeping.

Responsibilities:

- ``HeartbeatThread`` — daemon thread that periodically writes
  ``lex:wrk:<hostname>`` and refreshes the ``last_hb_iso`` field on every
  in-flight task hash.
- Celery signal handlers:
    * ``task_prerun``  — writes the full ``lex:task:<task_id>`` envelope so the
      supervisor can re-publish the task without help from Celery's internal
      ``unacked`` store, then registers the task as in-flight.
    * ``task_postrun`` — deletes the tracking hash and unregisters the task.
      A normal exception still hits ``task_postrun`` so the supervisor will
      *not* requeue it; that path is handled by ``CallbackTask.on_failure``.
    * ``worker_ready`` — starts the heartbeat thread.
    * ``worker_shutting_down`` — stops the heartbeat thread and removes the
      worker key. We do **not** clean up in-flight task hashes on shutdown —
      if the worker is going down while a task is mid-flight, the supervisor
      should detect and requeue it.

The module is import-safe with or without Celery; signal connections happen
only when :func:`connect_signal_handlers` is called from
:func:`lex.lex_app.celery_recovery.enable`.
"""
from __future__ import annotations

import base64
import logging
import os
import pickle
import threading
from datetime import datetime, timezone
from typing import Any, Iterable, Optional, Set

from .redis_client import get_client
from .redis_keys import task_key, worker_key

logger = logging.getLogger(__name__)


# Default knobs — re-read from env on every use so tests can patch them.
def _heartbeat_interval() -> int:
    return int(os.getenv("LEX_TASK_HEARTBEAT_INTERVAL", "5"))


def _hb_ttl_multiplier() -> int:
    return int(os.getenv("LEX_TASK_HB_TTL_MULTIPLIER", "3"))


def _max_retries_default() -> int:
    return int(os.getenv("LEX_TASK_MAX_RETRIES", "4"))


def _ttl_seconds() -> int:
    return _heartbeat_interval() * _hb_ttl_multiplier()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _pickle_b64(obj: Any) -> bytes:
    """Pickle ``obj`` and base64-encode it for safe storage in a Redis hash field."""
    return base64.b64encode(pickle.dumps(obj))


def _unpickle_b64(blob: bytes) -> Any:
    """Inverse of :func:`_pickle_b64`. Used by the supervisor when re-publishing."""
    return pickle.loads(base64.b64decode(blob))


# ---------------------------------------------------------------------------
# In-flight task registry — populated by task_prerun, drained by task_postrun.
# Heartbeat thread reads this to know which lex:task:<id> hashes to refresh.
# ---------------------------------------------------------------------------

class _InFlightRegistry:
    """Thread-safe set of currently-running task IDs in this worker process.

    Concurrency is normally 1 in deployed envs (per
    ``CELERY_WORKER_PREFETCH_MULTIPLIER=1`` plus the per-task pod shutdown in
    ``lex/lex_app/celery.py``), so this set holds 0 or 1 entries at a time.
    The lock is still warranted for the local-dev and pool=threads cases.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._ids: Set[str] = set()
        self._hostname: Optional[str] = None

    def add(self, task_id: str) -> None:
        with self._lock:
            self._ids.add(task_id)

    def remove(self, task_id: str) -> None:
        with self._lock:
            self._ids.discard(task_id)

    def snapshot(self) -> Iterable[str]:
        with self._lock:
            return tuple(self._ids)

    def set_hostname(self, hostname: str) -> None:
        with self._lock:
            self._hostname = hostname

    def hostname(self) -> Optional[str]:
        with self._lock:
            return self._hostname


_registry = _InFlightRegistry()


def get_registry() -> _InFlightRegistry:
    """Return the in-flight registry (exposed for tests)."""
    return _registry


# ---------------------------------------------------------------------------
# Heartbeat thread.
# ---------------------------------------------------------------------------

class HeartbeatThread(threading.Thread):
    """Periodically writes worker liveness + refreshes in-flight task heartbeats.

    The thread is created in :func:`start_heartbeat`. Use :func:`stop_heartbeat`
    to signal it to exit.
    """

    def __init__(self, registry: _InFlightRegistry) -> None:
        super().__init__(name="lex-celery-heartbeat", daemon=True)
        self._registry = registry
        self._stop = threading.Event()

    def stop(self) -> None:
        self._stop.set()

    def run(self) -> None:
        interval = _heartbeat_interval()
        logger.info("Heartbeat thread started (interval=%ss)", interval)
        while not self._stop.is_set():
            try:
                self._tick()
            except Exception:
                # Never crash the heartbeat thread — supervisor will see the
                # missed heartbeat and act, which is the correct behavior.
                logger.exception("Heartbeat tick failed (will retry)")
            self._stop.wait(timeout=_heartbeat_interval())
        logger.info("Heartbeat thread stopped")

    def _tick(self) -> None:
        hostname = self._registry.hostname()
        if not hostname:
            return  # worker_ready hasn't run yet
        client = get_client()
        ttl = _ttl_seconds()
        client.set(worker_key(hostname), b"1", ex=ttl)
        now = _now_iso()

        # Build the set of task_ids we need to refresh. There are two sources
        # because of the prefork-fork boundary:
        #
        # * ``_registry`` — populated by ``task_prerun`` in *this* process.
        #   Under the solo pool (and in tests) this is the only source we
        #   need.
        # * Redis scan filtered by hostname — under prefork, ``task_prerun``
        #   ran in a child process, so the MainProcess registry is empty
        #   even while the child is executing a task. Reading the envelope's
        #   ``hostname`` field is the only way for the MainProcess heartbeat
        #   thread to know which task hashes it owns and should refresh.
        #
        # We unify both sources so the same heartbeat tick refreshes
        # everything this worker is responsible for, regardless of which
        # process the prerun signal fired in.
        task_ids = set(self._registry.snapshot())
        try:
            host_bytes = hostname.encode("utf-8")
            for raw_key in client.scan_iter(match="lex:task:*"):
                if isinstance(raw_key, str):
                    raw_key = raw_key.encode("utf-8")
                if raw_key.endswith(b":lock"):
                    continue
                envelope_host = client.hget(raw_key, b"hostname")
                if envelope_host == host_bytes:
                    # b"lex:task:<id>" → "<id>"
                    parts = raw_key.split(b":", 2)
                    if len(parts) == 3:
                        task_ids.add(parts[2].decode("utf-8", errors="replace"))
        except Exception:
            # If the scan fails (Redis hiccup), fall back to whatever's in the
            # in-process registry. Don't let scan failure starve heartbeat
            # refreshes entirely.
            logger.debug(
                "Heartbeat: redis scan failed; refreshing in-process registry only",
                exc_info=True,
            )

        for task_id in task_ids:
            client.hset(task_key(task_id), "last_hb_iso", now)
            client.expire(task_key(task_id), ttl * 4)
            # The hash TTL is intentionally generous (4× the worker TTL) so the
            # supervisor still has the payload to re-publish even if the
            # worker has been dead for a while before the next sweep.


_heartbeat_thread: Optional[HeartbeatThread] = None
_heartbeat_lock = threading.Lock()


def start_heartbeat(hostname: str) -> None:
    """Start the heartbeat thread for this worker. Idempotent."""
    global _heartbeat_thread
    with _heartbeat_lock:
        _registry.set_hostname(hostname)
        if _heartbeat_thread is not None and _heartbeat_thread.is_alive():
            return
        _heartbeat_thread = HeartbeatThread(_registry)
        _heartbeat_thread.start()


def stop_heartbeat() -> None:
    """Stop the heartbeat thread and delete this worker's liveness key. Idempotent."""
    global _heartbeat_thread
    with _heartbeat_lock:
        if _heartbeat_thread is not None:
            _heartbeat_thread.stop()
            _heartbeat_thread = None
        hostname = _registry.hostname()
        if hostname:
            try:
                get_client().delete(worker_key(hostname))
            except Exception:
                logger.warning("Could not clean up worker key on shutdown", exc_info=True)


def is_heartbeat_running() -> bool:
    """For tests and operator visibility."""
    return _heartbeat_thread is not None and _heartbeat_thread.is_alive()


# ---------------------------------------------------------------------------
# Signal handlers — connected by connect_signal_handlers().
# ---------------------------------------------------------------------------

def on_task_prerun(sender=None, task_id=None, task=None, args=None, kwargs=None, **_extra) -> None:
    """Record the task envelope and mark it in-flight.

    Reads the ``lex_attempt`` header set by the supervisor on requeue. If
    absent, the attempt counter is 0 (first delivery from the parent).
    """
    if not task_id or task is None:
        return
    try:
        # The request object on the bound task carries headers (including any
        # the supervisor injected when re-publishing) and the delivery tag we
        # need to clean up Celery's `unacked` store later.
        request = getattr(task, "request", None)
        headers = getattr(request, "headers", None) or {}
        attempt = int(headers.get("lex_attempt", 0))
        delivery_info = getattr(request, "delivery_info", None) or {}
        delivery_tag = delivery_info.get("delivery_tag")
        queue = delivery_info.get("routing_key") or getattr(request, "reply_to", "") or ""

        # Resolve the executing worker's hostname for the envelope.
        #
        # IMPORTANT: under Celery's prefork pool (the default), ``task_prerun``
        # fires inside a ForkPoolWorker child process that was forked from the
        # main worker process **before** ``worker_ready`` ran. The module-level
        # ``_registry`` in this child is therefore a stale copy with no
        # hostname set, and falling back to it would write an empty hostname
        # into the envelope. An empty hostname defeats the supervisor's
        # ``worker_alive_but_task_stale`` guard (``if hostname and …``) and
        # lets it requeue a task that's still actively running on a healthy
        # worker — observed as the same task_id being picked up by both
        # workers in a 2-worker chaos test.
        #
        # ``task.request.hostname`` is populated by Celery for every executing
        # task in whichever process is actually running it, so it's the
        # correct source regardless of pool implementation.
        request_hostname = getattr(request, "hostname", None) if request else None
        if not isinstance(request_hostname, str):
            # Defensive: in unit tests, a bare ``MagicMock()`` returns a
            # nested MagicMock here, not a string. Falling back to the
            # in-process registry (or an empty string) keeps the envelope
            # well-typed.
            request_hostname = None
        envelope_hostname = (request_hostname or _registry.hostname() or "")

        # Per-task override via @lex_shared_task(max_retries=N). Falls back to
        # the env default. Phase 5 wires the decorator surface; for now the
        # attribute lookup degrades gracefully.
        max_retries = getattr(task, "lex_max_retries", None)
        if max_retries is None:
            max_retries = _max_retries_default()

        envelope = {
            b"task_name": (getattr(task, "name", "") or "").encode("utf-8"),
            b"queue": (queue or "").encode("utf-8"),
            b"attempt": str(attempt).encode("utf-8"),
            b"max_retries": str(int(max_retries)).encode("utf-8"),
            b"delivery_tag": str(delivery_tag or "").encode("utf-8"),
            b"args_b64": _pickle_b64(args or ()),
            b"kwargs_b64": _pickle_b64(kwargs or {}),
            b"hostname": envelope_hostname.encode("utf-8"),
            b"last_hb_iso": _now_iso().encode("utf-8"),
        }
        client = get_client()
        client.hset(task_key(task_id), mapping=envelope)
        client.expire(task_key(task_id), _ttl_seconds() * 4)
        _registry.add(task_id)
        logger.debug(
            "task_prerun: registered task_id=%s attempt=%s max_retries=%s hostname=%s",
            task_id, attempt, max_retries, envelope_hostname,
        )
    except Exception:
        logger.exception("task_prerun bookkeeping failed for task_id=%s", task_id)


def on_task_postrun(sender=None, task_id=None, task=None, **_extra) -> None:
    """Drop the task hash. The supervisor will not touch tasks that postran."""
    if not task_id:
        return
    try:
        get_client().delete(task_key(task_id))
    except Exception:
        logger.warning("task_postrun cleanup failed for task_id=%s", task_id, exc_info=True)
    finally:
        _registry.remove(task_id)


def on_worker_ready(sender=None, **_extra) -> None:
    """Start the heartbeat thread when this worker comes up."""
    hostname = _resolve_hostname(sender)
    if not hostname:
        logger.warning("worker_ready fired without a resolvable hostname; heartbeat not started")
        return
    start_heartbeat(hostname)


def on_worker_shutting_down(sender=None, **_extra) -> None:
    """Stop the heartbeat thread when this worker is shutting down."""
    stop_heartbeat()


def _resolve_hostname(sender: Any) -> Optional[str]:
    """Extract the worker's celery hostname from the ``sender`` (a ``Consumer``)."""
    if sender is None:
        return None
    hostname = getattr(sender, "hostname", None)
    if hostname:
        return hostname
    # Some Celery versions pass the Worker, not the Consumer.
    inner = getattr(sender, "controller", None)
    if inner is not None:
        return getattr(inner, "hostname", None)
    return None


# ---------------------------------------------------------------------------
# Wiring helpers — called from celery_recovery.__init__.enable().
# ---------------------------------------------------------------------------

_signals_connected = False
_signals_lock = threading.Lock()


def connect_signal_handlers() -> None:
    """Connect Celery signal handlers. Idempotent."""
    global _signals_connected
    with _signals_lock:
        if _signals_connected:
            return
        from celery.signals import (
            task_prerun,
            task_postrun,
            worker_ready,
            worker_shutting_down,
        )
        task_prerun.connect(on_task_prerun, weak=False)
        task_postrun.connect(on_task_postrun, weak=False)
        worker_ready.connect(on_worker_ready, weak=False)
        worker_shutting_down.connect(on_worker_shutting_down, weak=False)
        _signals_connected = True
        logger.info("lex celery recovery signal handlers connected")


def disconnect_signal_handlers_for_tests() -> None:
    """Tear down signal connections. Tests only."""
    global _signals_connected
    with _signals_lock:
        if not _signals_connected:
            return
        from celery.signals import (
            task_prerun,
            task_postrun,
            worker_ready,
            worker_shutting_down,
        )
        task_prerun.disconnect(on_task_prerun)
        task_postrun.disconnect(on_task_postrun)
        worker_ready.disconnect(on_worker_ready)
        worker_shutting_down.disconnect(on_worker_shutting_down)
        _signals_connected = False


__all__ = [
    "HeartbeatThread",
    "start_heartbeat",
    "stop_heartbeat",
    "is_heartbeat_running",
    "on_task_prerun",
    "on_task_postrun",
    "on_worker_ready",
    "on_worker_shutting_down",
    "connect_signal_handlers",
    "disconnect_signal_handlers_for_tests",
    "get_registry",
]
