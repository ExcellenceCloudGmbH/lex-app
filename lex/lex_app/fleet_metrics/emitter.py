"""Posts fleet-metrics events to the Instance Controller's ingest endpoint.

Every lex-app instance pushes a small stream of events -- a heartbeat at
startup and every hour, and one ``calculation_finished`` per root run -- to
``https://{DOMAIN_BASE}/api/fleet-metrics/events/``, authenticated with the
instance's existing ``LEX_API_KEY``. Delivery must never slow down or break
whatever triggered the event: a request handler, a calculation, or Django
startup itself. That is this module's whole job --

* ``emit(event)`` queues an event and never raises, even when the queue is
  full, the drain thread cannot be started, or fleet metrics are disabled;
* a bounded ``queue.Queue(1000)`` drops the oldest event once it is full, so
  the most recent fleet state is always what (eventually) ships;
* one daemon thread wakes every 30 seconds and posts everything queued, in
  batches of at most 100 events, each with a 10 second timeout and one retry
  after 5 seconds; a batch that fails both is dropped with a WARNING --
  throttled to at most once per hour per failure label so a down controller
  cannot flood the log -- and the rest waits for the next tick;
* only a 2xx answer counts as delivered, and redirects are never followed;
* ``flush(timeout)`` is a synchronous, best-effort drain bounded by
  ``timeout`` seconds total, for a Celery worker that is about to receive
  SIGTERM and cannot wait for the next scheduled drain tick;
* ``enabled()`` is true only on a deployed instance -- the same
  ``DEPLOYMENT_ENVIRONMENT`` + ``DOMAIN_BASE`` + ``LEX_API_KEY`` gate
  ``_is_deployed_instance`` used in the removed version reporter (commit
  0db88ad9) -- so local dev, CI and pytest runs never queue or send anything;
* a forked child process (calculation events are emitted from Celery worker
  processes) gets a fresh queue, fresh locks and a cleared drain-thread
  singleton via ``os.register_at_fork``, so it never inherits a lock some
  other, now-nonexistent thread was holding, a ``Thread`` object that falsely
  reports ``is_alive()``, or events the parent is already about to send.

``guard()`` and ``note_failure()`` are the "never let fleet-metrics code break
its caller" primitives: swallow the exception, log it throttled.
"""

from __future__ import annotations

import contextlib
import logging
import os
import threading
import time
from queue import Empty, Full, Queue
from typing import Any, Dict, Iterator, List, Optional

import requests

logger = logging.getLogger(__name__)

#: The bounded in-memory queue; the oldest event is dropped once it is full.
MAX_QUEUE_SIZE = 1000
#: At most this many events per POST.
BATCH_SIZE = 100
#: How often the daemon drain thread wakes up to post what is queued.
DRAIN_INTERVAL_SECONDS = 30.0
#: Per-HTTP-attempt timeout.
REQUEST_TIMEOUT_SECONDS = 10.0
#: Delay before the one retry on a failed send.
RETRY_DELAY_SECONDS = 5.0
#: A dropped batch (or any other guarded failure) is logged at WARNING at
#: most this often, per failure label.
WARNING_INTERVAL_SECONDS = 3600.0

_queue: "Queue[Dict[str, Any]]" = Queue(maxsize=MAX_QUEUE_SIZE)

_drain_thread: Optional[threading.Thread] = None
_drain_thread_lock = threading.Lock()

_last_warning_at: Dict[str, float] = {}
_warning_lock = threading.Lock()


@contextlib.contextmanager
def guard(label: str) -> Iterator[None]:
    """Run a block that must never be allowed to raise into its caller.

    Any exception is swallowed and reported through :func:`note_failure`
    instead of propagating -- used anywhere fleet-metrics code runs on a path
    that must never affect the caller (emitting an event must never break a
    request or a calculation).
    """
    try:
        yield
    except Exception as exc:  # noqa: BLE001 - this is the guard's whole job
        note_failure(label, exc)


def note_failure(label: str, exc: Optional[BaseException] = None) -> None:
    """Log a WARNING for ``label``, at most once per hour per label.

    Fleet-metrics delivery failures (a paused controller, a network blip) are
    expected in the wild; an unthrottled ``logger.warning`` in a 30-second
    drain loop would flood the log within a day of an outage.
    """
    now = time.monotonic()
    with _warning_lock:
        last = _last_warning_at.get(label)
        # ``time.monotonic()``'s reference point is unspecified -- it can
        # legitimately be a small number early in a process's life (for
        # example, shortly after a container starts), so "no previous
        # warning" has to be its own sentinel (``None``), never ``0.0``, or
        # the very first warning for a label could be mistaken for one that
        # just fired.
        if last is not None and now - last < WARNING_INTERVAL_SECONDS:
            return
        _last_warning_at[label] = now
    if exc is not None:
        logger.warning("fleet-metrics: %s failed: %s", label, exc)
    else:
        logger.warning("fleet-metrics: %s failed", label)


def enabled() -> bool:
    """True only when this is a deployed instance.

    Mirrors ``_is_deployed_instance`` from the removed version reporter
    (commit 0db88ad9) and the gate ``LexAPI.send_email`` uses: local dev, CI
    and pytest runs have none of the three variables set, so nothing is ever
    queued or sent outside a real customer instance.
    """
    return bool(
        os.getenv("DEPLOYMENT_ENVIRONMENT")
        and os.getenv("DOMAIN_BASE")
        and os.getenv("LEX_API_KEY")
    )


def emit(event: Dict[str, Any]) -> None:
    """Queue ``event`` for delivery. Never raises, even when disabled or full.

    A full queue drops the oldest queued event, never the new one being
    added, so the most recent fleet state is always what eventually ships.
    Disabled instances never queue anything and never start the drain thread.
    """
    try:
        if not enabled():
            return
        _ensure_drain_thread()
        try:
            _queue.put_nowait(event)
        except Full:
            with contextlib.suppress(Empty):
                _queue.get_nowait()
            with contextlib.suppress(Full):
                _queue.put_nowait(event)
    except Exception as exc:  # noqa: BLE001 - emit() must never raise
        note_failure("emitter.emit", exc)


def flush(timeout: float = 5.0) -> None:
    """Best-effort synchronous drain, bounded by ``timeout`` seconds total.

    Used by the Celery worker (lex/lex_app/celery_tasks.py) right before it
    shuts down, so a worker's event leaves before ``SIGTERM`` --
    the daemon thread's next scheduled 30 s drain tick would otherwise be too
    late. Never raises.
    """
    with guard("emitter.flush"):
        deadline = time.monotonic() + max(timeout, 0.0)
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return
            # The time check happens *before* dequeuing: once the budget is
            # spent we must not pull a batch off the queue and then abandon
            # it unsent -- it has to stay queued for the next drain tick.
            batch = _collect_batch()
            if not batch:
                return
            _send(batch, min(REQUEST_TIMEOUT_SECONDS, remaining))


def _ensure_drain_thread() -> None:
    """Start the single daemon drain thread, if it is not already running."""
    global _drain_thread
    with _drain_thread_lock:
        if _drain_thread is not None and _drain_thread.is_alive():
            return
        _drain_thread = threading.Thread(
            target=_drain_loop, name="fleet-metrics-drain", daemon=True
        )
        _drain_thread.start()


def _drain_loop() -> None:
    """The daemon thread body: wake every 30 s and post whatever is queued."""
    while True:
        time.sleep(DRAIN_INTERVAL_SECONDS)
        with guard("emitter.drain"):
            _drain()


def _drain() -> None:
    """Post batches until the queue is empty or a batch is dropped.

    Stopping at the first dropped batch leaves the rest queued for the next
    tick, so a down controller costs one attempt and one retry per tick, not
    one per queued batch.
    """
    while _drain_once():
        pass


def _drain_once() -> bool:
    """Post one batch (if any), with one retry after 5 s. True if delivered."""
    batch = _collect_batch()
    if not batch:
        return False
    if _send(batch, REQUEST_TIMEOUT_SECONDS):
        return True
    time.sleep(RETRY_DELAY_SECONDS)
    if _send(batch, REQUEST_TIMEOUT_SECONDS):
        return True
    note_failure(
        "emitter.post",
        RuntimeError(f"dropped {len(batch)} fleet-metrics event(s) after one retry"),
    )
    return False


def _collect_batch(max_items: int = BATCH_SIZE) -> List[Dict[str, Any]]:
    """Pull up to ``max_items`` events off the queue without blocking."""
    batch: List[Dict[str, Any]] = []
    while len(batch) < max_items:
        try:
            batch.append(_queue.get_nowait())
        except Empty:
            break
    return batch


def _send(batch: List[Dict[str, Any]], timeout: float) -> bool:
    """One POST attempt for ``batch``. Returns success; never raises.

    Only a 2xx answer is a delivery. Redirects are not followed: a redirect
    (to a login page, say) means the ingest endpoint never saw the batch.
    """
    url = f"https://{os.getenv('DOMAIN_BASE')}/api/fleet-metrics/events/"
    headers = {
        "Authorization": f"Api-Key {os.getenv('LEX_API_KEY')}",
        "Content-Type": "application/json",
    }
    try:
        response = requests.post(
            url, json={"events": batch}, headers=headers, timeout=timeout,
            allow_redirects=False,
        )
    except requests.RequestException as exc:
        note_failure("emitter.post.network", exc)
        return False
    if 200 <= response.status_code < 300:
        return True
    note_failure("emitter.post.status", RuntimeError(f"status {response.status_code}"))
    return False


def _after_fork_in_child() -> None:
    """Give a forked child process fresh queue, locks and drain-thread state.

    A forked child starts as a byte-for-byte copy of its parent's memory, but
    only the thread that called ``fork()`` continues to run there -- every
    other thread simply stops existing. Three things inherited from the
    parent are therefore unsafe to keep:

    * ``_queue`` and its internal lock: if some other thread held that lock
      at fork time, it stays held forever in the child, since the thread
      that would release it is gone -- the next ``emit()`` in the child
      deadlocks.
    * ``_drain_thread``: the inherited ``Thread`` object can keep reporting
      ``is_alive() == True`` even though no such thread is actually running
      in the child, which would make :func:`_ensure_drain_thread` believe a
      drain thread already exists and never start one there.
    * Any events already queued in the parent: left in place, the child
      would also drain and post them, double-sending whatever the parent is
      about to send itself.

    ``_drain_thread_lock`` and ``_warning_lock`` are replaced for the same
    held-forever-if-another-thread-held-it reason as the queue's lock.
    """
    global _queue, _drain_thread, _drain_thread_lock, _warning_lock
    _queue = Queue(maxsize=MAX_QUEUE_SIZE)
    _drain_thread = None
    _drain_thread_lock = threading.Lock()
    _warning_lock = threading.Lock()


if hasattr(os, "register_at_fork"):
    # POSIX only; Windows has no fork(). A forked worker process (a Celery
    # child) must never inherit a dead drain thread or a lock some other,
    # now-nonexistent thread was holding.
    os.register_at_fork(after_in_child=_after_fork_in_child)


def _reset_for_tests() -> None:
    """Drop every queued event and reset the drain thread + warning throttle.

    Test-only: lets each test start from a clean slate despite the module's
    process-lifetime globals (the queue, the drain-thread singleton, the
    warning throttle).
    """
    global _drain_thread
    with contextlib.suppress(Empty):
        while True:
            _queue.get_nowait()
    with _drain_thread_lock:
        _drain_thread = None
    with _warning_lock:
        _last_warning_at.clear()
