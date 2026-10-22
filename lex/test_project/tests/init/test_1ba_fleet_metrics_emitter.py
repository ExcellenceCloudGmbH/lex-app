"""Cluster 1ba: the fleet-metrics event emitter -- gating, bounding, delivery.

Intent
------
lex-app pushes a small stream of fleet-metrics events (a heartbeat, and from
task 12 a ``calculation_finished`` per root run) to the Instance Controller's
ingest endpoint. The emitter is the one piece every one of those events flows
through, so its contract is the whole safety net: an event producer (a
request handler, a calculation finaliser, Django startup) must never notice
whether the controller is up, slow, or unreachable.

A regression here is not "a metric is missing" -- it is "a customer request
hangs because the controller is down" or "a calculation crashes because
POSTing a usage event raised". That is why ``emit()`` never raises, a full
queue drops the *oldest* event rather than blocking or raising on the
newest, and a dropped batch is logged at WARNING throttled to once an hour,
never per-batch (a paused controller would otherwise fill the log in
minutes). ``enabled()`` is the same three-variable gate the removed version
reporter used (commit 0db88ad9) and ``LexAPI.send_email`` still uses, so
local dev, CI and pytest runs never queue or send anything -- every test
below has to opt in explicitly by setting all three variables.

Cluster 1ba -- scenarios 1.455-1.466, 1.475-1.476. Type: U.
Covers: lex/lex_app/fleet_metrics/emitter.py.
Run: python -m lex pytest lex/test_project/tests/init/test_1ba_fleet_metrics_emitter.py -v
"""

from __future__ import annotations

import importlib
from queue import Empty
from unittest.mock import MagicMock, patch

import pytest
import requests
from django.test import SimpleTestCase

from lex.lex_app.fleet_metrics import emitter

pytestmark = pytest.mark.init

#: The three variables that gate a deployed instance (source-plan.md 3.1),
#: mirroring ``LexAPI.send_email``'s own gate.
_DEPLOYED_ENV = {
    "DEPLOYMENT_ENVIRONMENT": "prod",
    "DOMAIN_BASE": "example-instance.lit.example.com",
    "LEX_API_KEY": "test-api-key-deadbeef",
}


class _NoOpThread:
    """A stand-in for threading.Thread that never actually runs a background
    loop -- tests that only care about queuing/gating behaviour use this so
    no real 30 s drain loop or heartbeat sleep is ever started."""

    def __init__(self, *args, **kwargs):
        self._alive = False

    def start(self):
        self._alive = True

    def is_alive(self):
        return self._alive


class TestCluster01ba_EmitterGating(SimpleTestCase):
    """Cluster 1ba: enabled() and emit()'s disabled/no-op behaviour."""

    def setUp(self) -> None:
        super().setUp()
        emitter._reset_for_tests()

    def tearDown(self) -> None:
        emitter._reset_for_tests()
        super().tearDown()

    # -- 1.455 -----------------------------------------------------
    def test_1_455_disabled_without_any_of_the_three_variables(self) -> None:
        """
        Scenario 1.455: outside a deployed instance, nothing is enabled.
        Given: none of DEPLOYMENT_ENVIRONMENT, DOMAIN_BASE, LEX_API_KEY are set
        When: enabled() is asked
        Then: it is False -- local dev, CI and pytest runs never phone home.
        """
        with patch.dict("os.environ", {k: "" for k in _DEPLOYED_ENV}, clear=False):
            self.assertFalse(
                emitter.enabled(),
                "enabled() must be False when none of the three gating "
                "variables are set.",
            )

    # -- 1.456 -----------------------------------------------------
    def test_1_456_disabled_with_only_some_variables_set(self) -> None:
        """
        Scenario 1.456: all three are required -- each one missing on its own
        still disables, not just one of them.
        Given: the other two of DEPLOYMENT_ENVIRONMENT, DOMAIN_BASE,
               LEX_API_KEY set, each variable in turn left empty
        When: enabled() is asked
        Then: it is False in every case -- a gate that only checks one or two
              of the three (for example, a dev box with a copied DOMAIN_BASE
              and LEX_API_KEY but no DEPLOYMENT_ENVIRONMENT) must not pass.
        """
        for missing in _DEPLOYED_ENV:
            with self.subTest(missing=missing):
                partial = dict(_DEPLOYED_ENV)
                partial[missing] = ""
                with patch.dict("os.environ", partial, clear=False):
                    self.assertFalse(
                        emitter.enabled(),
                        f"enabled() must require all three variables; it was "
                        f"True with only {missing} missing.",
                    )

    # -- 1.457 -----------------------------------------------------
    def test_1_457_enabled_with_all_three_variables_set(self) -> None:
        """
        Scenario 1.457: a deployed instance is enabled.
        Given: DEPLOYMENT_ENVIRONMENT, DOMAIN_BASE and LEX_API_KEY all set
        When: enabled() is asked
        Then: it is True.
        """
        with patch.dict("os.environ", _DEPLOYED_ENV, clear=False):
            self.assertTrue(
                emitter.enabled(),
                "enabled() must be True once all three variables are set.",
            )

    # -- 1.458 -----------------------------------------------------
    def test_1_458_emit_is_a_no_op_when_disabled(self) -> None:
        """
        Scenario 1.458: emit() queues nothing and starts no thread when disabled.
        Given: none of the three variables are set
        When: emit() is called
        Then: the queue stays empty and no drain thread is constructed -- a
              disabled instance must never start background work.
        """
        with patch.dict("os.environ", {k: "" for k in _DEPLOYED_ENV}, clear=False), \
             patch.object(emitter.threading, "Thread") as thread_cls:
            emitter.emit({"type": "heartbeat"})

        self.assertEqual(
            emitter._queue.qsize(), 0,
            "A disabled emitter must not queue the event.",
        )
        thread_cls.assert_not_called()

    # -- 1.459 -----------------------------------------------------
    def test_1_459_emit_never_raises_even_when_queueing_fails(self) -> None:
        """
        Scenario 1.459: emit() swallows an internal failure instead of raising.
        Given: a deployed instance, but the drain-thread machinery itself
               raises (simulating an unexpected internal error)
        When: emit() is called
        Then: no exception escapes -- a calculation or request path calling
              emit() must never be the thing that crashes.
        """
        with patch.dict("os.environ", _DEPLOYED_ENV, clear=False), \
             patch.object(emitter, "_ensure_drain_thread", side_effect=RuntimeError("boom")):
            try:
                emitter.emit({"type": "heartbeat"})
            except Exception as exc:  # pragma: no cover - the assertion is the point
                self.fail(f"emit() must never raise; it raised {exc!r}")


class TestCluster01ba_BoundedQueue(SimpleTestCase):
    """Cluster 1ba: the 1,000-event bounded queue drops the oldest."""

    def setUp(self) -> None:
        super().setUp()
        emitter._reset_for_tests()

    def tearDown(self) -> None:
        emitter._reset_for_tests()
        super().tearDown()

    # -- 1.460 -----------------------------------------------------
    def test_1_460_full_queue_drops_the_oldest_event(self) -> None:
        """
        Scenario 1.460: a queue at capacity drops the oldest, not the newest.
        Given: the queue is enabled and already holds exactly MAX_QUEUE_SIZE events
        When: 5 more events are emitted
        Then: the queue still holds MAX_QUEUE_SIZE events, and they are the 5
              oldest-dropped followed by the rest -- the most recent fleet
              state is always what is kept, never silently discarded in favour
              of stale events.
        """
        with patch.dict("os.environ", _DEPLOYED_ENV, clear=False), \
             patch.object(emitter, "_ensure_drain_thread"):
            total = emitter.MAX_QUEUE_SIZE + 5
            for i in range(total):
                emitter.emit({"type": "heartbeat", "seq": i})

            self.assertEqual(
                emitter._queue.qsize(), emitter.MAX_QUEUE_SIZE,
                "The queue must never exceed MAX_QUEUE_SIZE.",
            )
            kept = []
            while True:
                try:
                    kept.append(emitter._queue.get_nowait()["seq"])
                except Empty:
                    break

        self.assertEqual(
            kept[0], 5,
            "The 5 oldest events (seq 0-4) must have been dropped to make "
            f"room; the oldest kept event should be seq 5, got seq {kept[0]}.",
        )
        self.assertEqual(
            kept[-1], total - 1,
            "The newest emitted event must always be kept.",
        )


class TestCluster01ba_BatchingAndDelivery(SimpleTestCase):
    """Cluster 1ba: batch size, retry-then-drop, and the WARNING throttle."""

    def setUp(self) -> None:
        super().setUp()
        emitter._reset_for_tests()

    def tearDown(self) -> None:
        emitter._reset_for_tests()
        super().tearDown()

    def _fill_queue(self, count: int) -> None:
        for i in range(count):
            emitter._queue.put_nowait({"type": "heartbeat", "seq": i})

    # -- 1.461 -----------------------------------------------------
    def test_1_461_batches_are_capped_at_100_events(self) -> None:
        """
        Scenario 1.461: a single batch never exceeds BATCH_SIZE (100) events.
        Given: 250 events already queued
        When: one batch is collected
        Then: it holds exactly 100 -- the documented "batches of at most 100"
              -- and a second collection drains the next 100, leaving 50.
        """
        self._fill_queue(250)

        first = emitter._collect_batch()
        self.assertEqual(len(first), 100, "A batch must be capped at BATCH_SIZE.")
        second = emitter._collect_batch()
        self.assertEqual(len(second), 100, "The next batch must also be capped at 100.")
        self.assertEqual(
            emitter._queue.qsize(), 50,
            "250 queued minus two full batches of 100 must leave 50.",
        )

    # -- 1.462 -----------------------------------------------------
    def test_1_462_network_failure_is_retried_once_then_dropped(self) -> None:
        """
        Scenario 1.462: a failing endpoint gets exactly one retry, then the
        batch is dropped and logged at WARNING -- never raised.
        Given: requests.post always raises a network error
        When: one drain cycle (_drain_once) runs over a queued batch
        Then: requests.post is called exactly twice (the attempt and the one
              retry, 5 s apart) and no exception escapes _drain_once.
        """
        self._fill_queue(3)

        with patch.dict("os.environ", _DEPLOYED_ENV, clear=False), \
             patch.object(emitter.time, "sleep") as sleep, \
             patch.object(
                 emitter.requests, "post",
                 side_effect=requests.ConnectionError("down"),
             ) as post, \
             patch.object(emitter.logger, "warning") as warning:
            emitter._drain_once()

        self.assertEqual(
            post.call_count, 2,
            "A failing endpoint must be retried exactly once (attempt + 1 "
            f"retry), got {post.call_count} call(s).",
        )
        sleep.assert_called_once_with(emitter.RETRY_DELAY_SECONDS)
        self.assertGreaterEqual(
            warning.call_count, 1,
            "A batch dropped after the retry must be logged at WARNING, "
            "never silently discarded.",
        )

    # -- 1.463 -----------------------------------------------------
    def test_1_463_non_2xx_response_is_treated_as_a_failure(self) -> None:
        """
        Scenario 1.463: a non-2xx response is retried the same way a network
        error is -- the controller being unhealthy must not raise either.
        Given: requests.post returns HTTP 500 both times
        When: one drain cycle runs
        Then: requests.post is called twice, matching the network-failure path.
        """
        self._fill_queue(1)
        response = MagicMock(ok=False, status_code=500)

        with patch.dict("os.environ", _DEPLOYED_ENV, clear=False), \
             patch.object(emitter.time, "sleep"), \
             patch.object(emitter.requests, "post", return_value=response) as post:
            emitter._drain_once()

        self.assertEqual(post.call_count, 2, "A 500 response must also get the one retry.")

    # -- 1.464 -----------------------------------------------------
    def test_1_464_dropped_batch_warning_is_throttled_to_once_per_hour(self) -> None:
        """
        Scenario 1.464: note_failure() throttles a given label's WARNING to at
        most once per hour, regardless of how many times it fires.
        Given: the same failure label, reported 3 times back-to-back
        When: note_failure(label, exc) is called each time
        Then: logger.warning is called exactly once -- a persistent outage
              (a down controller retried every drain tick) must not flood the
              log with one WARNING per batch.
        """
        with patch.object(emitter.logger, "warning") as warning:
            for _ in range(3):
                emitter.note_failure("emitter.post", RuntimeError("down"))

        self.assertEqual(
            warning.call_count, 1,
            "note_failure() must throttle repeated calls for the same label "
            f"to once per hour; got {warning.call_count} calls for 3 reports.",
        )

    # -- 1.465 -----------------------------------------------------
    def test_1_465_one_daemon_drain_thread_is_reused(self) -> None:
        """
        Scenario 1.465: repeated emit() calls share a single daemon thread.
        Given: a deployed instance
        When: emit() is called twice
        Then: exactly one Thread is constructed, it is a daemon, and it
              targets _drain_loop -- a second background thread per process
              would mean every event gets posted twice.
        """
        with patch.dict("os.environ", _DEPLOYED_ENV, clear=False), \
             patch.object(emitter.threading, "Thread") as thread_cls:
            thread_cls.return_value = MagicMock()
            emitter.emit({"type": "heartbeat", "seq": 1})
            emitter.emit({"type": "heartbeat", "seq": 2})

        thread_cls.assert_called_once()
        kwargs = thread_cls.call_args.kwargs
        self.assertIs(kwargs.get("target"), emitter._drain_loop)
        self.assertTrue(kwargs.get("daemon"), "The drain thread must be a daemon thread.")


class TestCluster01ba_Flush(SimpleTestCase):
    """Cluster 1ba: flush(timeout) is synchronous and time-bounded."""

    def setUp(self) -> None:
        super().setUp()
        emitter._reset_for_tests()

    def tearDown(self) -> None:
        emitter._reset_for_tests()
        super().tearDown()

    # -- 1.466 -----------------------------------------------------
    def test_1_466_flush_never_waits_past_its_timeout_budget(self) -> None:
        """
        Scenario 1.466: flush(timeout) stops once its time budget is spent,
        even with more batches still queued -- a Celery worker calling
        flush(5) right before SIGTERM must not be held open indefinitely.
        Given: 150 events queued (two batches: 100 then 50) and a fake clock
               that shows the whole budget already spent by the time the
               second batch's remaining-time check runs
        When: flush(0.05) runs
        Then: _send is called exactly once, for the first batch only -- the
              second batch is left for the next drain tick rather than
              spending more of the shrinking SIGTERM window.
        """
        for i in range(150):
            emitter._queue.put_nowait({"type": "heartbeat", "seq": i})

        # monotonic() is read 3 times: the deadline, the 1st batch's
        # remaining-time check (budget still open), the 2nd batch's (spent).
        clock = iter([0.0, 0.0, 10.0])
        with patch.object(emitter.time, "monotonic", side_effect=lambda: next(clock)), \
             patch.object(emitter, "_send", return_value=True) as send:
            emitter.flush(0.05)

        self.assertEqual(
            send.call_count, 1,
            "flush() must stop once its timeout budget is spent, not drain "
            f"every queued batch; _send was called {send.call_count} time(s).",
        )
        self.assertEqual(
            emitter._queue.qsize(), 50,
            "The second (50-event) batch must still be queued for the next "
            "drain tick, not dropped.",
        )


class TestCluster01ba_ForkSafety(SimpleTestCase):
    """Cluster 1ba: a forked child process never inherits dead-thread/lock state.

    Task 12 emits from Celery worker processes, which can be forked rather
    than spawned. A forked child keeps only the thread that called fork() --
    every other thread the parent had simply stops existing there, taking
    any lock it held with it (held forever in the child) and leaving behind
    Thread objects that can still report ``is_alive() == True`` even though
    nothing is actually running.
    """

    def setUp(self) -> None:
        super().setUp()
        emitter._reset_for_tests()

    def tearDown(self) -> None:
        emitter._reset_for_tests()
        super().tearDown()

    # -- 1.475 -----------------------------------------------------
    def test_1_475_module_registers_an_at_fork_reset_handler(self) -> None:
        """
        Scenario 1.475: the module wires its reset into os.register_at_fork.
        Given: os.register_at_fork is available (POSIX)
        When: the emitter module is (re)imported
        Then: it registers _after_fork_in_child to run after a fork, in the
              child -- the only hook that runs before any other code in a
              freshly forked process.
        """
        with patch("os.register_at_fork") as register:
            importlib.reload(emitter)

        register.assert_called_once()
        kwargs = register.call_args.kwargs
        self.assertIs(
            kwargs.get("after_in_child"), emitter._after_fork_in_child,
            "The module must register _after_fork_in_child as the "
            "after_in_child hook.",
        )

    # -- 1.476 -----------------------------------------------------
    def test_1_476_after_fork_in_child_resets_queue_locks_and_thread(self) -> None:
        """
        Scenario 1.476: _after_fork_in_child() gives the child a clean slate.
        Given: a queued event, a stand-in "drain thread", and the current
               lock objects, as a forked child would inherit them
        When: _after_fork_in_child() runs (what os.register_at_fork calls in
              the child right after fork())
        Then: the queue is replaced by a fresh, empty one (the parent's
              already-queued events are not duplicated by the child), the
              drain-thread singleton is cleared so the child's next emit()
              starts its own thread instead of trusting an inherited one,
              and both locks are fresh objects -- never ones a now-gone
              thread could have been holding.
        """
        emitter._queue.put_nowait({"type": "heartbeat"})
        old_queue = emitter._queue
        old_drain_lock = emitter._drain_thread_lock
        old_warning_lock = emitter._warning_lock
        emitter._drain_thread = object()  # stand-in for an inherited Thread

        emitter._after_fork_in_child()

        self.assertIsNot(emitter._queue, old_queue, "The queue must be replaced.")
        self.assertEqual(emitter._queue.qsize(), 0, "The new queue must start empty.")
        self.assertIsNone(
            emitter._drain_thread,
            "The drain-thread singleton must be cleared, not left pointing "
            "at the parent's (now-nonexistent) thread.",
        )
        self.assertIsNot(
            emitter._drain_thread_lock, old_drain_lock,
            "The drain-thread lock must be a fresh object.",
        )
        self.assertIsNot(
            emitter._warning_lock, old_warning_lock,
            "The warning-throttle lock must be a fresh object.",
        )
