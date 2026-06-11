"""
Unit tests for the Celery worker-recovery subsystem
(``lex/lex_app/celery_recovery/``).

What is tested:
  * ``registry`` encodes/decodes the re-dispatch payload losslessly.
  * ``supervisor.scan_and_recover`` leaves live tasks alone, requeues a dead
    task under budget (same task_id, incremented retry count), and gives up
    once the bounded budget is exhausted (FAILURE result + row abort).
  * A dead task whose payload has vanished is dropped rather than requeued.

How it runs:
  Pure logic — Redis and Celery are never contacted. ``registry`` access and
  the row-abort side effect are patched, and a fake Celery app records
  ``send_task`` / ``backend.mark_as_failure`` calls.
"""

import types
from unittest import mock

from django.test import SimpleTestCase

from lex.lex_app.celery_recovery import heartbeat, registry, supervisor
from lex.lex_app.celery_recovery.exceptions import MaxRequeueExceeded


def _fake_app():
    backend = types.SimpleNamespace(mark_as_failure=mock.Mock())
    return types.SimpleNamespace(send_task=mock.Mock(), backend=backend)


class RegistryPayloadCodecTests(SimpleTestCase):
    def test_encode_decode_round_trip(self):
        payload = {
            "name": "calc_and_save",
            "args": ([1, 2, 3],),
            "kwargs": {"context": {"calculation_id": "c1"}},
            "queue": "inst-q",
            "retries": 2,
        }
        restored = registry._decode(registry._encode(payload))
        self.assertEqual(restored, payload)


class ScanAndRecoverTests(SimpleTestCase):
    def setUp(self):
        # Default settings stubs so the supervisor's helpers are deterministic.
        # The recovery lock and cancellation gate are stubbed to their
        # "act normally" values (lock acquired, not cancelled) so the core
        # requeue/give-up behaviour is exercised; tests that target those two
        # gates override these stubs explicitly.
        self._patches = [
            mock.patch.object(supervisor, "_max_retries", return_value=4),
            mock.patch.object(supervisor, "_requeue_grace_seconds", return_value=60),
            mock.patch.object(supervisor, "_default_queue", return_value="default-q"),
            mock.patch.object(registry, "try_acquire_recovery_lock", return_value=True),
            mock.patch.object(supervisor, "_is_cancelled", return_value=False),
        ]
        for p in self._patches:
            p.start()

    def tearDown(self):
        for p in self._patches:
            p.stop()

    def test_live_task_is_left_alone(self):
        app = _fake_app()
        with mock.patch.object(registry, "list_tracked", return_value=["t-alive"]), \
             mock.patch.object(registry, "is_alive", return_value=True), \
             mock.patch.object(registry, "get_payload") as get_payload:
            stats = supervisor.scan_and_recover(app)
        self.assertEqual(stats["alive"], 1)
        self.assertEqual(stats["requeued"], 0)
        app.send_task.assert_not_called()
        get_payload.assert_not_called()

    def test_dead_task_under_budget_is_requeued_same_id(self):
        app = _fake_app()
        payload = {
            "name": "calc_and_save",
            "args": ((),),
            "kwargs": {},
            "queue": "inst-q",
            "retries": 1,
        }
        with mock.patch.object(registry, "list_tracked", return_value=["t-dead"]), \
             mock.patch.object(registry, "is_alive", return_value=False), \
             mock.patch.object(registry, "get_payload", return_value=payload), \
             mock.patch.object(registry, "grant_grace") as grant_grace, \
             mock.patch.object(registry, "persist_payload") as persist_payload:
            stats = supervisor.scan_and_recover(app)

        self.assertEqual(stats["requeued"], 1)
        # Same task_id reused so the parent's AsyncResult still resolves.
        _, kwargs = app.send_task.call_args
        self.assertEqual(kwargs["task_id"], "t-dead")
        self.assertEqual(kwargs["queue"], "inst-q")
        # Grace granted before dispatch; incremented retries persisted after.
        grant_grace.assert_called_once()
        saved_payload = persist_payload.call_args[0][1]
        self.assertEqual(saved_payload["retries"], 2)

    def test_dead_task_without_payload_is_dropped(self):
        app = _fake_app()
        with mock.patch.object(registry, "list_tracked", return_value=["t-orphan"]), \
             mock.patch.object(registry, "is_alive", return_value=False), \
             mock.patch.object(registry, "get_payload", return_value=None), \
             mock.patch.object(registry, "deregister") as deregister:
            stats = supervisor.scan_and_recover(app)
        self.assertEqual(stats["orphaned"], 1)
        deregister.assert_called_once_with("t-orphan")
        app.send_task.assert_not_called()

    def test_exhausted_budget_marks_failure_and_aborts(self):
        app = _fake_app()
        payload = {
            "name": "calc_and_save",
            "args": ((),),
            "kwargs": {},
            "queue": "inst-q",
            "retries": 4,  # == max, so the next detection gives up
        }
        with mock.patch.object(registry, "list_tracked", return_value=["t-dead"]), \
             mock.patch.object(registry, "is_alive", return_value=False), \
             mock.patch.object(registry, "get_payload", return_value=payload), \
             mock.patch.object(registry, "deregister") as deregister, \
             mock.patch.object(supervisor, "_abort_calculation_rows") as abort_rows:
            stats = supervisor.scan_and_recover(app)

        self.assertEqual(stats["gave_up"], 1)
        app.send_task.assert_not_called()
        deregister.assert_called_once_with("t-dead")
        abort_rows.assert_called_once()
        exc = app.backend.mark_as_failure.call_args[0][1]
        self.assertIsInstance(exc, MaxRequeueExceeded)

    def test_cancelled_calculation_is_finalized_not_requeued(self):
        """Fix A: a dead task whose calculation was cancelled is finalized
        CANCELLED (row finalize + deregister), never requeued."""
        app = _fake_app()
        payload = {
            "name": "calc_and_save",
            "args": ((),),
            "kwargs": {"context": {"calculation_id": "calc-99"}},
            "queue": "inst-q",
            "retries": 1,  # under budget — would normally requeue
        }
        with mock.patch.object(registry, "list_tracked", return_value=["t-dead"]), \
             mock.patch.object(registry, "is_alive", return_value=False), \
             mock.patch.object(registry, "get_payload", return_value=payload), \
             mock.patch.object(supervisor, "_is_cancelled", return_value=True), \
             mock.patch.object(registry, "deregister") as deregister, \
             mock.patch.object(supervisor, "_cancel_calculation_rows") as cancel_rows:
            stats = supervisor.scan_and_recover(app)

        self.assertEqual(stats["cancelled"], 1)
        self.assertEqual(stats["requeued"], 0)
        app.send_task.assert_not_called()
        cancel_rows.assert_called_once()
        deregister.assert_called_once_with("t-dead")

    def test_calculation_id_of_reads_nested_context(self):
        """Fix A helper: calculation_id is read from kwargs.context."""
        payload = {"kwargs": {"context": {"calculation_id": "calc-7"}}}
        self.assertEqual(supervisor._calculation_id_of(payload), "calc-7")
        # Missing layers degrade to None rather than raising.
        self.assertIsNone(supervisor._calculation_id_of({"kwargs": {}}))
        self.assertIsNone(supervisor._calculation_id_of({}))

    def test_dead_task_skipped_when_lock_not_acquired(self):
        """Fix B: when another supervisor holds the recovery lock, this pass
        skips the task entirely — no requeue, counted under skipped_locked."""
        app = _fake_app()
        payload = {
            "name": "calc_and_save",
            "args": ((),),
            "kwargs": {},
            "queue": "inst-q",
            "retries": 1,
        }
        with mock.patch.object(registry, "list_tracked", return_value=["t-dead"]), \
             mock.patch.object(registry, "is_alive", return_value=False), \
             mock.patch.object(registry, "get_payload", return_value=payload), \
             mock.patch.object(registry, "try_acquire_recovery_lock", return_value=False):
            stats = supervisor.scan_and_recover(app)

        self.assertEqual(stats["skipped_locked"], 1)
        self.assertEqual(stats["requeued"], 0)
        app.send_task.assert_not_called()

    def test_requeue_does_not_persist_retries_when_dispatch_fails(self):
        """Fix D: if send_task raises (broker down), the retry budget is NOT
        consumed — grant_grace ran but persist_payload did not."""
        app = _fake_app()
        app.send_task.side_effect = RuntimeError("broker down")
        payload = {
            "name": "calc_and_save",
            "args": ((),),
            "kwargs": {},
            "queue": "inst-q",
            "retries": 1,
        }
        with mock.patch.object(registry, "grant_grace") as grant_grace, \
             mock.patch.object(registry, "persist_payload") as persist_payload:
            with self.assertRaises(RuntimeError):
                supervisor._requeue(app, "t-dead", payload)

        grant_grace.assert_called_once()
        # Budget not burned: incremented payload never persisted.
        persist_payload.assert_not_called()
        # Original payload retries unchanged (we built a copy internally).
        self.assertEqual(payload["retries"], 1)


class TaskRevokedHandlerTests(SimpleTestCase):
    def test_on_task_revoked_deregisters_request_id(self):
        """Fix E: a revoked task is deregistered by its request id so the
        supervisor can never later requeue it."""
        request = types.SimpleNamespace(id="t-revoked")
        with mock.patch.object(registry, "deregister") as deregister:
            heartbeat.on_task_revoked(request=request)
        deregister.assert_called_once_with("t-revoked")

    def test_on_task_revoked_without_id_is_noop(self):
        """No request id (or no request) → best-effort no-op, never raises."""
        with mock.patch.object(registry, "deregister") as deregister:
            heartbeat.on_task_revoked(request=None)
            heartbeat.on_task_revoked(request=types.SimpleNamespace(id=None))
        deregister.assert_not_called()


class RequeueRoutingInvariantTests(SimpleTestCase):
    """Recovered tasks must go to their original (main) queue, never the
    'recovery' queue. This is what lets KEDA scale real workers while the
    recovery pod stays isolated on -Q recovery."""

    def test_requeue_uses_payload_queue_not_recovery_queue(self):
        app = _fake_app()
        with mock.patch.object(supervisor, "_requeue_grace_seconds", return_value=60), \
             mock.patch.object(supervisor, "_default_queue", return_value="main-q"), \
             mock.patch.object(registry, "grant_grace"), \
             mock.patch.object(registry, "persist_payload"):
            supervisor._requeue(
                app,
                "task-1",
                {"name": "calc_and_save", "args": (), "kwargs": {},
                 "queue": "main-q", "retries": 0},
            )
        self.assertEqual(app.send_task.call_count, 1)
        _, called_kwargs = app.send_task.call_args
        self.assertEqual(called_kwargs["queue"], "main-q")
        self.assertNotEqual(called_kwargs["queue"], "recovery")

    def test_requeue_falls_back_to_default_main_queue_when_payload_lacks_queue(self):
        app = _fake_app()
        with mock.patch.object(supervisor, "_requeue_grace_seconds", return_value=60), \
             mock.patch.object(supervisor, "_default_queue", return_value="main-q"), \
             mock.patch.object(registry, "grant_grace"), \
             mock.patch.object(registry, "persist_payload"):
            supervisor._requeue(
                app,
                "task-2",
                {"name": "calc_and_save", "args": (), "kwargs": {}, "retries": 0},
            )
        _, called_kwargs = app.send_task.call_args
        self.assertEqual(called_kwargs["queue"], "main-q")


class BeatScheduleWiringTests(SimpleTestCase):
    """The beat schedule must reference the actually-registered sweep task,
    and that task must be excluded from heartbeat tracking. A drift here means
    beat enqueues a task name no worker has registered -> recovery silently
    never runs under the beat-driven driver."""

    def test_schedule_task_name_matches_registered_sweep(self):
        from django.conf import settings as dj_settings

        # The registered task name (source of truth) lives on the @shared_task.
        # This assertion is unconditional — the name must be correct regardless
        # of whether the beat schedule is populated.
        registered_name = supervisor.sweep_dead_workers.name
        self.assertEqual(
            registered_name,
            "lex.lex_app.celery_recovery.supervisor.sweep_dead_workers",
        )

        schedule = getattr(dj_settings, "CELERY_BEAT_SCHEDULE", {})
        entry = schedule.get("lex-celery-recovery-sweep")
        if entry is None:
            self.skipTest(
                "LEX_TASK_RECOVERY_ENABLED is false — recovery beat schedule not populated"
            )
        self.assertEqual(entry["task"], registered_name)

    def test_registered_sweep_is_excluded_from_heartbeat_tracking(self):
        self.assertIn(
            supervisor.sweep_dead_workers.name,
            heartbeat._UNTRACKED_TASK_NAMES,
        )

    def test_sweep_is_routed_to_dedicated_recovery_queue(self):
        from django.conf import settings as dj_settings

        entry = dj_settings.CELERY_BEAT_SCHEDULE["lex-celery-recovery-sweep"]
        self.assertEqual(
            entry["options"].get("queue"),
            "recovery",
            "sweep must target the dedicated 'recovery' queue, not the main "
            "KEDA-watched queue",
        )

    def test_sweep_queue_differs_from_main_default_queue(self):
        from django.conf import settings as dj_settings

        entry = dj_settings.CELERY_BEAT_SCHEDULE["lex-celery-recovery-sweep"]
        self.assertNotEqual(
            entry["options"].get("queue"),
            getattr(dj_settings, "CELERY_TASK_DEFAULT_QUEUE", "celery"),
            "the recovery sweep queue must be distinct from the main queue so "
            "it never pollutes the KEDA scaling signal",
        )


class RecoveryBeatEntrypointTests(SimpleTestCase):
    """lex-recovery-beat must launch an embedded-beat worker that consumes ONLY
    the 'recovery' queue with the django_celery_beat DatabaseScheduler."""

    def test_beat_main_invokes_worker_main_with_embedded_beat_on_recovery_queue(self):
        from lex.lex_app.celery_recovery import entrypoint
        from lex.lex_app import celery as celery_module

        with mock.patch.object(celery_module.app, "worker_main") as worker_main:
            entrypoint.beat_main(argv=[])

        self.assertEqual(worker_main.call_count, 1)
        (passed_argv,), _ = worker_main.call_args
        self.assertEqual(passed_argv[0], "worker")
        self.assertIn("-B", passed_argv)                 # embedded beat
        self.assertIn("-Q", passed_argv)
        q_index = passed_argv.index("-Q")
        self.assertEqual(passed_argv[q_index + 1], "recovery")
        self.assertIn("--scheduler", passed_argv)
        s_index = passed_argv.index("--scheduler")
        self.assertEqual(
            passed_argv[s_index + 1],
            "django_celery_beat.schedulers:DatabaseScheduler",
        )
