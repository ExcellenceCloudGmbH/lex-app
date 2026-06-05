"""
Unit tests for in-framework worker self-termination
(``lex/lex_app/celery.py``).

What is tested:
  * ``_warm_shutdown_if_idle`` schedules exactly one SIGTERM timer iff the
    worker has no active/reserved task other than the excluded ids.
  * The ``_shutdown_scheduled`` guard prevents a second timer being armed.
  * The ``task_revoked`` cancel fast-path and the idle watchdog call the
    helper, and both respect the non-local + feature-enabled gates.

How it runs:
  Django and real celery are configured under ``lex test``; we import the
  real module and patch ``celery_mod.worker_state``, ``threading.Timer``
  and ``os.kill`` so no signal is actually delivered.
"""
import os
import types
from unittest import mock

from django.test import SimpleTestCase

from lex.lex_app import celery as celery_mod


def _fake_request(task_id):
    return types.SimpleNamespace(id=task_id)


def _patch_state(active_ids=(), reserved_ids=()):
    """Return a context manager patching celery_mod.worker_state."""
    fake_state = types.SimpleNamespace(
        active_requests=[_fake_request(i) for i in active_ids],
        reserved_requests=[_fake_request(i) for i in reserved_ids],
    )
    return mock.patch.object(celery_mod, "worker_state", fake_state)


class WarmShutdownIfIdleTests(SimpleTestCase):
    def setUp(self):
        # Module-level guard must start clean for every test.
        celery_mod._shutdown_scheduled = False

    def test_schedules_sigterm_when_idle(self):
        with _patch_state(active_ids=(), reserved_ids=()):
            with mock.patch.object(celery_mod.threading, "Timer") as timer:
                result = celery_mod._warm_shutdown_if_idle()
        self.assertTrue(result["shutting_down"])
        timer.assert_called_once()

    def test_no_shutdown_when_other_task_active(self):
        with _patch_state(active_ids=("other-task",)):
            with mock.patch.object(celery_mod.threading, "Timer") as timer:
                result = celery_mod._warm_shutdown_if_idle()
        self.assertFalse(result["shutting_down"])
        self.assertEqual(result["pending_count"], 1)
        timer.assert_not_called()

    def test_excluded_task_id_is_ignored(self):
        with _patch_state(active_ids=("task-123",)):
            with mock.patch.object(celery_mod.threading, "Timer") as timer:
                result = celery_mod._warm_shutdown_if_idle(
                    exclude_task_ids={"task-123"}
                )
        self.assertTrue(result["shutting_down"])
        timer.assert_called_once()

    def test_guard_prevents_second_timer(self):
        with _patch_state(active_ids=(), reserved_ids=()):
            with mock.patch.object(celery_mod.threading, "Timer") as timer:
                first = celery_mod._warm_shutdown_if_idle()
                second = celery_mod._warm_shutdown_if_idle()
        self.assertTrue(first["shutting_down"])
        self.assertTrue(second.get("already_scheduled"))
        timer.assert_called_once()


class IdleShutdownConfigTests(SimpleTestCase):
    def test_enabled_defaults_true(self):
        with mock.patch.dict("os.environ", {}, clear=False):
            os.environ.pop("LEX_WORKER_IDLE_SHUTDOWN_ENABLED", None)
            self.assertTrue(celery_mod._idle_shutdown_enabled())

    def test_enabled_can_be_disabled(self):
        with mock.patch.dict(
            "os.environ", {"LEX_WORKER_IDLE_SHUTDOWN_ENABLED": "false"}, clear=False
        ):
            self.assertFalse(celery_mod._idle_shutdown_enabled())

    def test_seconds_defaults_to_30(self):
        with mock.patch.dict("os.environ", {}, clear=False):
            os.environ.pop("LEX_WORKER_IDLE_SHUTDOWN_SECONDS", None)
            self.assertEqual(celery_mod._idle_shutdown_seconds(), 30.0)

    def test_seconds_reads_env(self):
        with mock.patch.dict(
            "os.environ", {"LEX_WORKER_IDLE_SHUTDOWN_SECONDS": "12"}, clear=False
        ):
            self.assertEqual(celery_mod._idle_shutdown_seconds(), 12.0)

    def test_seconds_falls_back_on_garbage(self):
        with mock.patch.dict(
            "os.environ", {"LEX_WORKER_IDLE_SHUTDOWN_SECONDS": "notanumber"}, clear=False
        ):
            self.assertEqual(celery_mod._idle_shutdown_seconds(), 30.0)


class TaskRevokedFastPathTests(SimpleTestCase):
    def test_calls_helper_excluding_revoked_id_when_non_local(self):
        request = _fake_request("revoked-1")
        with mock.patch.object(celery_mod, "_is_non_local_deployment_target",
                               return_value=True):
            with mock.patch.object(celery_mod, "_idle_shutdown_enabled",
                                   return_value=True):
                with mock.patch.object(celery_mod, "_warm_shutdown_if_idle") as helper:
                    celery_mod.shutdown_worker_after_task_revoked(request=request)
        helper.assert_called_once_with(exclude_task_ids={"revoked-1"})

    def test_noop_when_local(self):
        request = _fake_request("revoked-1")
        with mock.patch.object(celery_mod, "_is_non_local_deployment_target",
                               return_value=False):
            with mock.patch.object(celery_mod, "_warm_shutdown_if_idle") as helper:
                celery_mod.shutdown_worker_after_task_revoked(request=request)
        helper.assert_not_called()

    def test_noop_when_feature_disabled(self):
        request = _fake_request("revoked-1")
        with mock.patch.object(celery_mod, "_is_non_local_deployment_target",
                               return_value=True):
            with mock.patch.object(celery_mod, "_idle_shutdown_enabled",
                                   return_value=False):
                with mock.patch.object(celery_mod, "_warm_shutdown_if_idle") as helper:
                    celery_mod.shutdown_worker_after_task_revoked(request=request)
        helper.assert_not_called()

    def test_calls_helper_with_empty_exclude_when_request_has_no_id(self):
        request = types.SimpleNamespace()  # a revoke context with no .id
        with mock.patch.object(celery_mod, "_is_non_local_deployment_target",
                               return_value=True):
            with mock.patch.object(celery_mod, "_idle_shutdown_enabled",
                                   return_value=True):
                with mock.patch.object(celery_mod, "_warm_shutdown_if_idle") as helper:
                    celery_mod.shutdown_worker_after_task_revoked(request=request)
        helper.assert_called_once_with(exclude_task_ids=set())


class IdleWatchdogTests(SimpleTestCase):
    def setUp(self):
        celery_mod._shutdown_scheduled = False
        celery_mod._watchdog_stop.clear()

    def _clock(self, values):
        """A fake monotonic() that yields successive values then holds the last."""
        seq = list(values)

        def _monotonic():
            return seq.pop(0) if len(seq) > 1 else seq[0]

        return _monotonic

    def test_shuts_down_after_idle_timeout(self):
        # Idle the whole time; clock jumps past the 30s timeout on poll 2.
        sleeps = []

        def fake_sleep(interval):
            sleeps.append(interval)
            if len(sleeps) > 5:  # safety valve so a bug can't hang the suite
                celery_mod._watchdog_stop.set()

        with _patch_state(active_ids=(), reserved_ids=()):
            with mock.patch.object(celery_mod, "_warm_shutdown_if_idle") as helper:
                celery_mod._idle_watchdog_loop(
                    timeout_seconds=30,
                    poll_interval=5,
                    monotonic=self._clock([100, 100, 140]),
                    sleep=fake_sleep,
                )
        helper.assert_called_once_with()

    def test_activity_refreshes_last_active(self):
        # Busy on first poll (resets last_active), then idle but not yet past
        # timeout -> no shutdown; loop bounded by the stop event.
        calls = {"n": 0}

        def fake_sleep(interval):
            calls["n"] += 1
            if calls["n"] >= 2:
                celery_mod._watchdog_stop.set()

        busy_then_idle = [
            types.SimpleNamespace(
                active_requests=[_fake_request("t1")], reserved_requests=[]
            ),
            types.SimpleNamespace(active_requests=[], reserved_requests=[]),
        ]

        def _read(exclude_task_ids=()):
            state = busy_then_idle[min(calls["n"], 1)]
            ids = {getattr(r, "id", None) for r in state.active_requests}
            return ids - {None}

        with mock.patch.object(celery_mod, "_read_pending_task_ids", side_effect=_read):
            with mock.patch.object(celery_mod, "_warm_shutdown_if_idle") as helper:
                celery_mod._idle_watchdog_loop(
                    timeout_seconds=30,
                    poll_interval=5,
                    monotonic=self._clock([0, 1, 2]),
                    sleep=fake_sleep,
                )
        helper.assert_not_called()

    def test_worker_ready_starts_thread_when_non_local(self):
        with mock.patch.object(celery_mod, "_is_non_local_deployment_target",
                               return_value=True):
            with mock.patch.object(celery_mod, "_idle_shutdown_enabled",
                                   return_value=True):
                with mock.patch.object(celery_mod.threading, "Thread") as thread_cls:
                    celery_mod.start_idle_watchdog()
        thread_cls.assert_called_once()
        self.assertTrue(thread_cls.call_args.kwargs.get("daemon"))

    def test_worker_ready_noop_when_local(self):
        with mock.patch.object(celery_mod, "_is_non_local_deployment_target",
                               return_value=False):
            with mock.patch.object(celery_mod.threading, "Thread") as thread_cls:
                celery_mod.start_idle_watchdog()
        thread_cls.assert_not_called()

    def test_worker_shutting_down_sets_stop_event(self):
        celery_mod._watchdog_stop.clear()
        celery_mod.stop_idle_watchdog()
        self.assertTrue(celery_mod._watchdog_stop.is_set())
