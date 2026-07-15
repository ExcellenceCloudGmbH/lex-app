"""Cluster 8z — dispatch-time recovery claims, queue-verified recovery, age-gated startup reset, boot watchdog.

Intent: the recovery subsystem's promise is "no calculation gets stuck or
lost, whatever dies". Incident 2026-07-14 (instance 1410) showed four gaps in
that promise, all rooted in one asymmetry: a row goes ``IN_PROGRESS`` at
DISPATCH, but recovery ownership only began at task START (``task_prerun``).
A task whose worker pod was still Pending (full cluster, node-group max) was
invisible to the registry, so a recovery-beat pod restart blind-aborted its
healthy, merely-queued row — while the idle watchdog, armed only on
``worker_ready``, could not reap workers that booted into the evicted broker.

The hardening under test:
- ``registry.claim_dispatched`` — ownership starts at dispatch
  (``CallbackTask.apply_async`` is the single choke point), with
  ``status="dispatched"``, ``claimed_at``, NO heartbeat, written NX so a
  racing ``task_prerun`` registration is never clobbered;
- the supervisor's dispatched lane — a claim is left alone while its message
  is still on (or possibly on) the broker queue; only a verifiably vanished
  message (Redis flushed/evicted) is requeued, so a same-task-id
  double-dispatch is impossible;
- the startup reset's age-gate — young untracked rows are spared even when
  the registry is unreadable (blind-abort degradation);
- the boot watchdog — armed on ``worker_init`` so "never became ready" is
  itself a termination condition.
Cluster 8z — scenarios 8.145–8.156. Type: U (+ one E sweep class).
Covers: lex/lex_app/celery_recovery/registry.py (claim_dispatched,
        task_id_in_queue), lex/lex_app/celery_recovery/supervisor.py
        (dispatched lane), lex/lex_app/celery_tasks.py
        (CallbackTask.apply_async), lex/process_admin/utils/
        model_registration.py (age-gate), lex/lex_app/celery.py
        (boot watchdog).
Run: python -m lex pytest lex/test_project/tests/celery_async/test_8z_dispatch_claim_and_boot_guard.py -v
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from datetime import timedelta
from types import SimpleNamespace
from unittest import mock

import nest_asyncio
import pytest
from asgiref.sync import sync_to_async
from django.db import connections
from django.test import SimpleTestCase

from lex.core.models.CalculationModel import CalculationModel
from lex.core.models.LexModel import lex_datetime_now
from lex.lex_app.celery_recovery import registry, supervisor
from lex.process_admin.utils.model_registration import ModelRegistration
from lex.test_project.tests._e2e_test_case import E2ETestCase

from .models import CelerySyncCalc

pytestmark = pytest.mark.celery_async


class TestCluster08z_DispatchClaim(SimpleTestCase):
    """Cluster 8z: ownership starts at dispatch, without racing prerun."""

    def setUp(self):
        registry.reset_client_cache()
        self.addCleanup(registry.reset_client_cache)

    def _client(self, *, set_returns=True):
        client = mock.MagicMock()
        client.set.return_value = set_returns
        client.get.return_value = None
        return client

    def test_8_145_claim_writes_dispatched_payload_without_heartbeat(self):
        """
        Scenario 8.145: a dispatch claim is a tracked payload with
        status='dispatched', a claimed_at stamp, and NO heartbeat key.
        Given: an enabled registry with a mocked redis client
        When: claim_dispatched(task_id, ...) runs
        Then: the payload key is written NX with status/claimed_at, the id is
              indexed, and the heartbeat key is never touched — the broker
              message, not a heartbeat, is the liveness story
        """
        client = self._client()
        with mock.patch.object(registry, "_get_client", return_value=client):
            registry.claim_dispatched("tid-1", "calc_and_save", ("a",), {"k": 1}, "celery")

        from lex.lex_app.celery_recovery import redis_keys

        set_calls = client.set.call_args_list
        self.assertEqual(len(set_calls), 1, "Exactly one SET (the payload, NX) is expected.")
        args, kwargs = set_calls[0]
        self.assertEqual(
            args[0], redis_keys.payload_key("tid-1"),
            "The single SET must target the payload key.",
        )
        self.assertTrue(kwargs.get("nx"), "The claim must be written NX — never clobber prerun.")
        payload = registry._decode(args[1])
        self.assertEqual(payload["status"], "dispatched", "Claims carry status=dispatched.")
        self.assertLessEqual(
            abs(payload["claimed_at"] - time.time()), 5,
            "claimed_at must be a fresh epoch stamp.",
        )
        client.sadd.assert_called_once()
        heartbeat_sets = [
            c for c in set_calls if c[0][0] == redis_keys.heartbeat_key("tid-1")
        ]
        self.assertEqual(
            heartbeat_sets, [],
            "A claim must NOT stamp a heartbeat — a missing heartbeat proves "
            "nothing for a queued task.",
        )

    def test_8_146_claim_never_clobbers_existing_payload(self):
        """
        Scenario 8.146: when a payload already exists (prerun won the race, or
        a supervisor requeue owns it), the claim leaves it untouched.
        Given: redis SET NX returns False (key exists)
        When: claim_dispatched runs
        Then: the id is still indexed, but the existing payload survives
        """
        client = self._client(set_returns=False)
        with mock.patch.object(registry, "_get_client", return_value=client):
            registry.claim_dispatched("tid-1", "calc_and_save", (), {}, None)

        client.sadd.assert_called_once()
        # Only one SET attempt (the NX one) — no unconditional overwrite.
        self.assertEqual(
            client.set.call_count, 1,
            "A lost NX race must not be followed by an unconditional SET.",
        )

    def test_8_147_prerun_register_upgrades_claim_to_running(self):
        """
        Scenario 8.147: task_prerun's register() upgrades a claim in place.
        Given: an existing dispatched payload with an accumulated retry count
        When: register() runs for the same task id
        Then: the stored payload has status='running' and the retries survive
        """
        existing = registry._encode(
            {"name": "t", "args": (), "kwargs": {}, "queue": None,
             "retries": 2, "status": "dispatched", "claimed_at": 1.0}
        )
        client = self._client()
        client.get.return_value = existing
        with mock.patch.object(registry, "_get_client", return_value=client):
            registry.register("tid-1", "t", (), {}, None)

        payload_write = client.set.call_args_list[0]
        payload = registry._decode(payload_write[0][1])
        self.assertEqual(
            payload["status"], "running",
            "prerun registration must upgrade the claim to running.",
        )
        self.assertEqual(
            payload["retries"], 2,
            "The requeue budget must survive the dispatched→running upgrade.",
        )

    def test_8_148_apply_async_claims_after_dispatch(self):
        """
        Scenario 8.148: CallbackTask.apply_async claims the task AFTER the
        message reached the broker, and skips untracked task names.
        Given: a CallbackTask whose base dispatch returns a task id
        When: apply_async runs for a normal task and for the recovery sweep
        Then: claim_dispatched is called once with the dispatch's identity for
              the normal task, and never for the untracked sweep task
        """
        from celery import Task

        from lex.lex_app.celery_tasks import CallbackTask

        fake_result = SimpleNamespace(id="tid-99")
        with mock.patch.object(
            Task, "apply_async", return_value=fake_result
        ), mock.patch.object(registry, "claim_dispatched") as claim:
            task = CallbackTask()
            task.name = "calc_and_save"
            result = task.apply_async(args=(1,), kwargs={"a": 2}, queue="celery")
            self.assertIs(result, fake_result, "The dispatch result must pass through.")
            claim.assert_called_once_with(
                task_id="tid-99",
                name="calc_and_save",
                args=(1,),
                kwargs={"a": 2},
                queue="celery",
            )

            claim.reset_mock()
            sweep = CallbackTask()
            sweep.name = "lex.lex_app.celery_recovery.supervisor.sweep_dead_workers"
            sweep.apply_async(args=(), kwargs={})
            claim.assert_not_called()

    def test_8_149_queue_introspection_finds_ids_and_degrades_safely(self):
        """
        Scenario 8.149: task_id_in_queue reads the broker list definitively
        when it can and returns None (assume-queued) when it cannot.
        Given: a queue holding protocol-v2 and correlation-id messages
        When: probing for present ids, absent ids, and with a broken client
        Then: True / False / None respectively
        """
        message_v2 = json.dumps({"headers": {"id": "tid-1"}, "properties": {}})
        message_v1 = json.dumps({"headers": {}, "properties": {"correlation_id": "tid-2"}})
        client = mock.MagicMock()
        client.lrange.return_value = [message_v2, message_v1, "not-json"]
        with mock.patch.object(registry, "_get_client", return_value=client):
            self.assertIs(registry.task_id_in_queue("tid-1", "celery"), True)
            self.assertIs(registry.task_id_in_queue("tid-2", "celery"), True)
            self.assertIs(registry.task_id_in_queue("tid-3", "celery"), False)

        broken = mock.MagicMock()
        broken.lrange.side_effect = RuntimeError("redis down")
        with mock.patch.object(registry, "_get_client", return_value=broken):
            self.assertIsNone(
                registry.task_id_in_queue("tid-1", "celery"),
                "An unreadable queue must be 'unknown', never 'absent' — "
                "callers must not double-dispatch on uncertainty.",
            )


class TestCluster08z_SupervisorDispatchedLane(SimpleTestCase):
    """Cluster 8z: the scan leaves queued claims alone, recovers vanished ones."""

    def _scan(self, payload, *, in_queue, lock_ok=True):
        """Run one scan pass over a single dispatched claim."""
        app = mock.MagicMock()
        app.AsyncResult.return_value.ready.return_value = False
        patches = [
            mock.patch.object(supervisor.registry, "list_tracked", return_value=["tid-1"]),
            mock.patch.object(supervisor.registry, "is_alive", return_value=False),
            mock.patch.object(supervisor.registry, "get_payload", return_value=payload),
            mock.patch.object(supervisor.registry, "task_id_in_queue", return_value=in_queue),
            mock.patch.object(
                supervisor.registry, "try_acquire_recovery_lock", return_value=lock_ok
            ) ,
            mock.patch.object(supervisor.registry, "grant_grace"),
            mock.patch.object(supervisor.registry, "persist_payload"),
            mock.patch.object(supervisor.registry, "deregister"),
            mock.patch.object(supervisor, "_is_cancelled", return_value=False),
            mock.patch.object(supervisor, "_rows_already_settled", return_value=False),
            mock.patch.object(supervisor, "_abort_calculation_rows"),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        stats = supervisor.scan_and_recover(app)
        return stats, app

    def _claim(self, *, age_seconds, retries=0):
        return {
            "name": "calc_and_save",
            "args": (),
            "kwargs": {},
            "queue": "celery",
            "retries": retries,
            "status": "dispatched",
            "claimed_at": time.time() - age_seconds,
        }

    def test_8_150_young_claim_is_left_alone_without_locking(self):
        """
        Scenario 8.150: a claim within the dispatch grace is not touched.
        Given: a dispatched claim younger than LEX_TASK_DISPATCH_GRACE_SECONDS
        When: the scan runs
        Then: counted dispatched_waiting; nothing dispatched; the recovery
              lock is never taken (a waiting claim must not burn lock windows)
        """
        stats, app = self._scan(self._claim(age_seconds=1), in_queue=False)
        self.assertEqual(stats["dispatched_waiting"], 1)
        self.assertEqual(stats["requeued"], 0)
        app.send_task.assert_not_called()
        supervisor.registry.try_acquire_recovery_lock.assert_not_called()

    def test_8_151_queued_claim_is_left_alone_beyond_grace(self):
        """
        Scenario 8.151: past the grace, a claim whose message is still on the
        broker queue is a scheduling backlog — never a recovery case.
        Given: an old dispatched claim; task_id_in_queue → True
        When: the scan runs
        Then: dispatched_waiting; no requeue (same-task-id double dispatch
              would run the work twice)
        """
        stats, app = self._scan(self._claim(age_seconds=10_000), in_queue=True)
        self.assertEqual(stats["dispatched_waiting"], 1)
        app.send_task.assert_not_called()

    def test_8_152_unreadable_queue_never_double_dispatches(self):
        """
        Scenario 8.152: queue unreadable → 'unknown' → treated as queued.
        Given: an old dispatched claim; task_id_in_queue → None
        When: the scan runs
        Then: dispatched_waiting; no requeue — uncertainty must never produce
              a second message
        """
        stats, app = self._scan(self._claim(age_seconds=10_000), in_queue=None)
        self.assertEqual(stats["dispatched_waiting"], 1)
        app.send_task.assert_not_called()

    def test_8_153_vanished_claim_is_requeued_with_same_id(self):
        """
        Scenario 8.153: the message verifiably vanished (broker flushed or
        evicted) — the one case where recovery must act, and the same-task-id
        requeue is safe because no duplicate can exist.
        Given: an old dispatched claim under budget; task_id_in_queue → False
        When: the scan runs
        Then: requeued once with the original task id; the persisted payload
              keeps status=dispatched with a refreshed claim clock
        """
        stats, app = self._scan(self._claim(age_seconds=10_000, retries=0), in_queue=False)
        self.assertEqual(stats["requeued"], 1)
        app.send_task.assert_called_once()
        self.assertEqual(
            app.send_task.call_args.kwargs.get("task_id"), "tid-1",
            "Recovery must reuse the task id so blocked waiters still resolve.",
        )
        persisted = supervisor.registry.persist_payload.call_args[0][1]
        self.assertEqual(persisted["status"], "dispatched")
        self.assertLessEqual(
            abs(persisted["claimed_at"] - time.time()), 5,
            "The requeued claim must get a fresh grace window.",
        )

    def test_8_154_vanished_claim_beyond_budget_gives_up(self):
        """
        Scenario 8.154: a vanished claim that exhausted the retry budget is
        finalized, exactly like a dead running task.
        Given: an old dispatched claim with retries == LEX_TASK_MAX_RETRIES;
               task_id_in_queue → False
        When: the scan runs
        Then: gave_up; the row-finalize path (ABORTED) is invoked and the
              task is deregistered
        """
        retries = supervisor._max_retries()
        stats, app = self._scan(
            self._claim(age_seconds=10_000, retries=retries), in_queue=False
        )
        self.assertEqual(stats["gave_up"], 1)
        app.send_task.assert_not_called()
        supervisor._abort_calculation_rows.assert_called_once()
        supervisor.registry.deregister.assert_called_once_with("tid-1")


class TestCluster08z_StartupAgeGate(E2ETestCase):
    """Cluster 8z: the startup reset spares young untracked rows."""

    e2e_models = [CelerySyncCalc]

    def tearDown(self):
        # Same asgiref executor-connection cleanup as 8x (see that module).
        try:
            nest_asyncio.apply()
            asyncio.get_event_loop().run_until_complete(
                sync_to_async(connections.close_all)()
            )
        except Exception:  # pragma: no cover — best-effort connection cleanup
            pass
        super().tearDown()

    def _make_in_progress(self, name, *, age_seconds=0):
        row = CelerySyncCalc.objects.create(name=name)
        row.is_calculated = CalculationModel.IN_PROGRESS
        row.save(skip_hooks=True)
        if age_seconds:
            backdated = lex_datetime_now() - timedelta(seconds=age_seconds)
            CelerySyncCalc.objects.filter(pk=row.pk).update(edited_at=backdated)
        return row

    def _run_sweep(self, *, min_age=None):
        env = {"CALLED_FROM_START_COMMAND": "1"}
        if min_age is not None:
            env["LEX_STARTUP_ABORT_MIN_AGE_SECONDS"] = str(min_age)
        with mock.patch.dict(os.environ, env):
            ModelRegistration._handle_calculation_model_reset(
                CelerySyncCalc, tracked_record_ids=set(),
            )

    def _status(self, row):
        return (
            CelerySyncCalc.objects.filter(pk=row.pk)
            .values_list("is_calculated", flat=True)
            .first()
        )

    def test_8_155_age_gate_spares_young_rows_and_aborts_old_ones(self):
        """
        Scenario 8.155: with tracking unavailable (empty ownership set — the
        blind-abort degradation), the sweep spares young rows and still
        aborts genuinely old ones; gate=0 restores the legacy abort-all.
        Given: one fresh IN_PROGRESS row and one backdated 2h
        When: the sweep runs with the default gate, then with gate=0
        Then: young survives / old aborts; with gate=0 the young row aborts too
        """
        young = self._make_in_progress("young")
        old = self._make_in_progress("old", age_seconds=7200)

        self._run_sweep()
        self.assertEqual(
            self._status(young), CalculationModel.IN_PROGRESS,
            "A young untracked row is more likely queued than orphaned — the "
            "startup reset must spare it (the FYF_2026_26 incident class).",
        )
        self.assertEqual(
            self._status(old), CalculationModel.ABORTED,
            "Rows older than the gate keep the legacy orphan-abort behavior.",
        )

        self._run_sweep(min_age=0)
        self.assertEqual(
            self._status(young), CalculationModel.ABORTED,
            "Gate=0 must restore the legacy blind-abort semantics exactly.",
        )


class TestCluster08z_BootWatchdog(SimpleTestCase):
    """Cluster 8z: 'never became ready' is itself a termination condition."""

    def setUp(self):
        from lex.lex_app import celery as worker

        self.worker = worker
        # Reset module state between scenarios.
        self.worker._worker_became_ready.clear()
        if self.worker._boot_timer is not None:
            self.worker._boot_timer.cancel()
        self.worker._boot_timer = None
        self.addCleanup(self._cleanup)

    def _cleanup(self):
        if self.worker._boot_timer is not None:
            self.worker._boot_timer.cancel()
        self.worker._boot_timer = None
        self.worker._worker_became_ready.clear()

    def test_8_156_boot_watchdog_arms_fires_and_disarms(self):
        """
        Scenario 8.156: the boot watchdog is armed on worker_init, terminates
        a worker that never becomes ready, and is disarmed by worker_ready.
        Given: a non-local deployment with the watchdog enabled
        When: worker_init fires, then either the timeout elapses or
              worker_ready arrives first
        Then: fire-without-ready requests a warm shutdown; ready-first makes
              the fire a no-op and cancels the timer
        """
        env = {"DEPLOYMENT_TARGET": "prod", "LEX_WORKER_IDLE_SHUTDOWN_ENABLED": "true"}
        with mock.patch.dict(os.environ, env):
            self.worker.arm_boot_watchdog()
            self.assertIsNotNone(
                self.worker._boot_timer,
                "worker_init must arm the boot watchdog in deployed environments.",
            )

            # Timeout elapses before the worker ever became ready.
            with mock.patch.object(self.worker, "_warm_shutdown_if_idle") as shutdown:
                self.worker._boot_watchdog_fire()
                shutdown.assert_called_once()

            # worker_ready arrived: the fire must become a no-op.
            self.worker._worker_became_ready.set()
            with mock.patch.object(self.worker, "_warm_shutdown_if_idle") as shutdown:
                self.worker._boot_watchdog_fire()
                shutdown.assert_not_called()

        # Disabled environments never arm.
        self._cleanup()
        with mock.patch.dict(os.environ, {"DEPLOYMENT_TARGET": "local"}):
            self.worker.arm_boot_watchdog()
            self.assertIsNone(
                self.worker._boot_timer,
                "Local execution must not self-terminate.",
            )
