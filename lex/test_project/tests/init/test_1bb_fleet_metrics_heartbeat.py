"""Cluster 1bb: the hourly fleet-metrics heartbeat -- web-only, immediate first beat.

Intent
------
The controller tells a silent instance (nothing listening, maybe crashed)
apart from one that simply opted out of usage metrics (source-plan.md 3.3:
"the heartbeat's usage_enabled: false is how the Collection dashboard tells
an opted-out instance from a silent one"). That only works if every web
process -- and *only* web processes -- reliably sends a heartbeat at startup
and every hour after, carrying the switch's current value. A Celery worker
sending heartbeats would make the dashboard's per-instance count wrong (one
instance, several heartbeat sources); a web process that forgets its first
beat leaves a multi-minute blind spot right when an operator is most likely
to be watching (right after a deploy).

``start()`` is gated on ``running_in_uvicorn()`` (the web-process check
``lex.lex_app.apps`` already uses) and the emitter's own ``enabled()``, and
guards against scheduling a second thread in the same process the same way
the emitter guards its own drain thread.

Cluster 1bb -- scenarios 1.467-1.474. Type: U.
Covers: lex/lex_app/fleet_metrics/heartbeat.py.
Run: python -m lex pytest lex/test_project/tests/init/test_1bb_fleet_metrics_heartbeat.py -v
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest
from django.test import SimpleTestCase

from lex.lex_app.fleet_metrics import emitter, heartbeat

pytestmark = pytest.mark.init

_DEPLOYED_ENV = {
    "DEPLOYMENT_ENVIRONMENT": "prod",
    "DOMAIN_BASE": "example-instance.lit.example.com",
    "LEX_API_KEY": "test-api-key-deadbeef",
}


class TestCluster01bb_HeartbeatScheduling(SimpleTestCase):
    """Cluster 1bb: start() is web-process-only and once-per-process."""

    def setUp(self) -> None:
        super().setUp()
        emitter._reset_for_tests()
        heartbeat._reset_for_tests()

    def tearDown(self) -> None:
        emitter._reset_for_tests()
        heartbeat._reset_for_tests()
        super().tearDown()

    # -- 1.467 -----------------------------------------------------
    def test_1_467_no_op_outside_the_web_process(self) -> None:
        """
        Scenario 1.467: a Celery worker never gets a heartbeat thread.
        Given: a deployed instance, but running_in_uvicorn() is False (a
               worker process, or any process not started as the ASGI app)
        When: start() is called
        Then: it returns False and no Thread is constructed -- "Workers send
              none" (source-plan.md 3.3).
        """
        with patch.dict("os.environ", _DEPLOYED_ENV, clear=False), \
             patch("lex.lex_app.apps.running_in_uvicorn", return_value=False), \
             patch.object(heartbeat.threading, "Thread") as thread_cls:
            started = heartbeat.start()

        self.assertFalse(started, "start() must return False outside the web process.")
        thread_cls.assert_not_called()

    # -- 1.468 -----------------------------------------------------
    def test_1_468_no_op_when_the_emitter_is_not_enabled(self) -> None:
        """
        Scenario 1.468: the web process alone is not enough -- the emitter's
        own gate must also pass.
        Given: running_in_uvicorn() True, but none of the three fleet-metrics
               variables are set (emitter.enabled() is False)
        When: start() is called
        Then: it returns False and no Thread is constructed.
        """
        with patch.dict("os.environ", {k: "" for k in _DEPLOYED_ENV}, clear=False), \
             patch("lex.lex_app.apps.running_in_uvicorn", return_value=True), \
             patch.object(heartbeat.threading, "Thread") as thread_cls:
            started = heartbeat.start()

        self.assertFalse(started, "start() must return False when the emitter is disabled.")
        thread_cls.assert_not_called()

    # -- 1.469 -----------------------------------------------------
    def test_1_469_web_process_schedules_a_daemon_thread_and_beats_immediately(self) -> None:
        """
        Scenario 1.469: the happy path -- web process, emitter enabled.
        Given: running_in_uvicorn() True and the emitter enabled
        When: start() is called
        Then: it returns True, a daemon Thread targeting _run is constructed
              and started, and the first beat happens immediately -- not
              after waiting a full hour -- because _run() emits before its
              first sleep.
        """
        emitted = []
        with patch.dict("os.environ", _DEPLOYED_ENV, clear=False), \
             patch("lex.lex_app.apps.running_in_uvicorn", return_value=True), \
             patch.object(heartbeat, "time") as time_mod, \
             patch.object(emitter, "emit", side_effect=lambda event: emitted.append(event)):
            # Run _run() synchronously (as the real daemon thread would, but
            # inline) to prove the immediate-first-beat ordering, with sleep
            # raising after the first iteration so the infinite loop stops.
            time_mod.sleep.side_effect = StopIteration
            with self.assertRaises(StopIteration):
                heartbeat._run()

        self.assertEqual(len(emitted), 1, "The first heartbeat must fire before any sleep.")
        time_mod.sleep.assert_called_once_with(heartbeat.HEARTBEAT_INTERVAL_SECONDS)

        with patch.dict("os.environ", _DEPLOYED_ENV, clear=False), \
             patch("lex.lex_app.apps.running_in_uvicorn", return_value=True), \
             patch.object(heartbeat.threading, "Thread") as thread_cls:
            thread_cls.return_value = MagicMock()
            started = heartbeat.start()

        self.assertTrue(started, "start() must return True in the web process when enabled.")
        thread_cls.assert_called_once()
        kwargs = thread_cls.call_args.kwargs
        self.assertIs(kwargs.get("target"), heartbeat._run)
        self.assertTrue(kwargs.get("daemon"), "The heartbeat thread must be a daemon thread.")

    # -- 1.470 -----------------------------------------------------
    def test_1_470_second_call_in_the_same_process_is_guarded(self) -> None:
        """
        Scenario 1.470: start() is idempotent per process.
        Given: a first successful start() in this process
        When: start() is called again
        Then: it returns False and no second Thread is constructed -- a
              project whose ready() somehow ran twice must not double the
              fleet's heartbeat rate for that instance.
        """
        with patch.dict("os.environ", _DEPLOYED_ENV, clear=False), \
             patch("lex.lex_app.apps.running_in_uvicorn", return_value=True), \
             patch.object(heartbeat.threading, "Thread") as thread_cls:
            thread_cls.return_value = MagicMock()
            first = heartbeat.start()
            second = heartbeat.start()

        self.assertTrue(first)
        self.assertFalse(second, "A second start() in the same process must be a no-op.")
        thread_cls.assert_called_once()


class TestCluster01bb_HeartbeatPayload(SimpleTestCase):
    """Cluster 1bb: the heartbeat event body (source-plan.md 3.3)."""

    def setUp(self) -> None:
        super().setUp()
        heartbeat._reset_for_tests()

    def tearDown(self) -> None:
        heartbeat._reset_for_tests()
        super().tearDown()

    # -- 1.471 -----------------------------------------------------
    def test_1_471_payload_carries_the_documented_fields(self) -> None:
        """
        Scenario 1.471: the payload shape matches source-plan.md 3.3 exactly.
        Given: a known lex._version.__version__ and COMMIT_SHA
        When: build_payload() is called
        Then: type is "heartbeat", lex_app_version and commit_sha match, and
              process is always "web" (never "worker" -- heartbeat has no
              worker variant).
        """
        with patch.object(heartbeat, "__version__", "2.4.0"), \
             patch.dict("os.environ", {"COMMIT_SHA": "4d435046"}, clear=False):
            payload = heartbeat.build_payload()

        self.assertEqual(payload["type"], "heartbeat")
        self.assertEqual(payload["lex_app_version"], "2.4.0")
        self.assertEqual(payload["commit_sha"], "4d435046")
        self.assertEqual(payload["process"], "web")

    # -- 1.472 -----------------------------------------------------
    def test_1_472_occurred_at_is_an_aware_utc_timestamp(self) -> None:
        """
        Scenario 1.472: occurred_at is aware UTC, not a naive local timestamp.
        Given: no special setup
        When: build_payload() is called
        Then: occurred_at parses back to an aware datetime whose UTC offset
              is zero and whose ISO string ends in "+00:00" or "Z"
              (source-plan.md 3.2 -- "lex-app stamps every event with an
              aware UTC timestamp").
        """
        payload = heartbeat.build_payload()
        occurred_at = payload["occurred_at"]

        self.assertTrue(
            occurred_at.endswith("+00:00") or occurred_at.endswith("Z"),
            f"occurred_at must end in +00:00 or Z, got {occurred_at!r}",
        )
        parsed = datetime.fromisoformat(occurred_at.replace("Z", "+00:00"))
        self.assertIsNotNone(parsed.tzinfo, "occurred_at must be timezone-aware.")
        self.assertEqual(parsed.utcoffset(), timezone.utc.utcoffset(None))

    # -- 1.473 -----------------------------------------------------
    def test_1_473_usage_enabled_true_when_the_switch_is_on(self) -> None:
        """
        Scenario 1.473: usage_enabled reflects LEX_USAGE_METRICS=on.
        Given: LEX_USAGE_METRICS unset (default on, per source-plan.md 3.1)
        When: build_payload() is called
        Then: usage_enabled is True.
        """
        with patch.dict("os.environ", {"LEX_USAGE_METRICS": ""}, clear=False):
            payload = heartbeat.build_payload()
        self.assertTrue(payload["usage_enabled"])

    # -- 1.474 -----------------------------------------------------
    def test_1_474_usage_enabled_false_when_the_switch_is_off(self) -> None:
        """
        Scenario 1.474: usage_enabled reflects LEX_USAGE_METRICS=off.
        Given: LEX_USAGE_METRICS=off
        When: build_payload() is called
        Then: usage_enabled is False -- this is the one signal the Collection
              dashboard has to tell an opted-out instance from a silent one.
        """
        with patch.dict("os.environ", {"LEX_USAGE_METRICS": "off"}, clear=False):
            payload = heartbeat.build_payload()
        self.assertFalse(payload["usage_enabled"])
