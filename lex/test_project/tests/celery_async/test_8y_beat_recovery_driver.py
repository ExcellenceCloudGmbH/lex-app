"""Embedded-beat recovery driver — schedule wiring, queue isolation, entrypoint.

Intent
------
Worker recovery can run under two interchangeable drivers: the always-on
``recovery-supervisor`` pod (an in-process ``scan_and_recover`` loop) or the
embedded-beat ``lex-recovery-beat`` pod (a Celery worker with ``-B`` that fires
the existing ``sweep_dead_workers`` task on a DB-driven schedule visible in the
Django admin). The beat driver only stays *correct* on the KEDA scale-to-0
cluster if three wiring invariants hold, and each is silent when broken:

1. **The beat schedule must name the actually-registered sweep task.** If the
   schedule string drifts from the ``@shared_task`` name, beat enqueues a task
   no worker has registered and recovery silently never runs.
2. **The sweep must ride a dedicated ``recovery`` queue, never the main
   KEDA-watched queue.** The recovery pod consumes ``-Q recovery`` itself, so the
   scan runs in-process; if the sweep landed on the main queue it would pollute
   the very Redis list KEDA scales on and force a cold-start every interval.
3. **Recovered tasks must go back to their *original* (main) queue, never
   ``recovery``.** That outward routing is what lets KEDA scale real workers
   while the recovery pod stays isolated; a recovered task looping back to
   ``recovery`` would be consumed by the singleton pod and never drive scaling.

These are pure-logic surfaces (settings dict, frozenset membership, the argv the
entrypoint hands ``worker_main``, and ``_requeue``'s queue selection), so the
broker, Redis, and Celery itself are never contacted.

Cluster 8y — scenarios 8.116–8.122. Type: U.
Covers: lex/lex_app/celery_recovery/entrypoint.py,
        lex/lex_app/settings.py (CELERY_BEAT_SCHEDULE),
        lex/lex_app/celery_recovery/supervisor.py (_requeue routing).
Run: python -m lex pytest lex/test_project/tests/celery_async/test_8y_beat_recovery_driver.py -v
"""

from __future__ import annotations

import types
from unittest import mock

import pytest
from django.conf import settings as dj_settings
from django.test import SimpleTestCase

from lex.lex_app.celery_recovery import entrypoint, heartbeat, registry, supervisor

pytestmark = pytest.mark.celery_async

_SWEEP_TASK_NAME = "lex.lex_app.celery_recovery.supervisor.sweep_dead_workers"
_RECOVERY_QUEUE = "recovery"
_SCHEDULE_KEY = "lex-celery-recovery-sweep"


def _fake_app():
    """A stand-in Celery app that records ``send_task`` calls without a broker."""
    backend = types.SimpleNamespace(mark_as_failure=mock.Mock())
    return types.SimpleNamespace(send_task=mock.Mock(), backend=backend)


class TestCluster08y_BeatScheduleWiring(SimpleTestCase):
    """Cluster 8y: the beat schedule must name the registered sweep task and
    route it to an isolated ``recovery`` queue, off the KEDA scaling signal."""

    def test_08_116_schedule_task_name_matches_registered_sweep(self):
        """
        Scenario 8.116: the beat schedule references the registered sweep task.
        Given: ``sweep_dead_workers`` is registered under its dotted task name.
        When:  the ``lex-celery-recovery-sweep`` beat entry is read.
        Then:  its ``task`` equals that registered name, so beat enqueues a task
               a worker actually has — otherwise recovery silently never fires.
        """
        registered_name = supervisor.sweep_dead_workers.name
        self.assertEqual(
            registered_name,
            _SWEEP_TASK_NAME,
            msg="the registered sweep task name is the contract the schedule binds to",
        )

        schedule = getattr(dj_settings, "CELERY_BEAT_SCHEDULE", {})
        entry = schedule.get(_SCHEDULE_KEY)
        if entry is None:
            self.skipTest(
                "LEX_TASK_RECOVERY_ENABLED is false — recovery beat schedule not populated"
            )
        self.assertEqual(
            entry["task"],
            registered_name,
            msg="beat schedule must enqueue the registered sweep task, not a stale path",
        )

    def test_08_117_registered_sweep_excluded_from_heartbeat_tracking(self):
        """
        Scenario 8.117: the sweep is excluded from heartbeat tracking.
        Given: the recovery pod runs the sweep itself every interval.
        When:  the heartbeat handlers' untracked-task set is inspected.
        Then:  the sweep task name is present — so the pod does not register and
               deregister a heartbeat for its own sweep on every tick (needless
               Redis churn, and a self-referential entry the scan could trip on).
        """
        self.assertIn(
            supervisor.sweep_dead_workers.name,
            heartbeat._UNTRACKED_TASK_NAMES,
            msg="the sweep must never be heartbeat-tracked — it is the scanner, not the scanned",
        )

    def test_08_118_sweep_routed_to_dedicated_recovery_queue(self):
        """
        Scenario 8.118: the sweep is routed to the dedicated ``recovery`` queue.
        Given: the recovery pod consumes only ``-Q recovery``.
        When:  the beat entry's ``options`` are read.
        Then:  ``queue`` is ``recovery`` so the sweep is self-consumed by the pod
               and never reaches the main KEDA-watched list.
        """
        schedule = getattr(dj_settings, "CELERY_BEAT_SCHEDULE", {})
        entry = schedule.get(_SCHEDULE_KEY)
        if entry is None:
            self.skipTest(
                "LEX_TASK_RECOVERY_ENABLED is false — recovery beat schedule not populated"
            )
        self.assertEqual(
            entry.get("options", {}).get("queue"),
            _RECOVERY_QUEUE,
            msg="sweep must target the dedicated 'recovery' queue, not the main queue",
        )

    def test_08_119_sweep_queue_differs_from_main_default_queue(self):
        """
        Scenario 8.119: the recovery queue is distinct from the main queue.
        Given: KEDA scales workers off the main default queue's Redis list length.
        When:  the sweep's target queue is compared to the main default queue.
        Then:  they differ — so a periodic sweep can never inflate the autoscaling
               signal and trigger spurious cold-starts.
        """
        schedule = getattr(dj_settings, "CELERY_BEAT_SCHEDULE", {})
        entry = schedule.get(_SCHEDULE_KEY)
        if entry is None:
            self.skipTest(
                "LEX_TASK_RECOVERY_ENABLED is false — recovery beat schedule not populated"
            )
        self.assertNotEqual(
            entry.get("options", {}).get("queue"),
            getattr(dj_settings, "CELERY_TASK_DEFAULT_QUEUE", "celery"),
            msg="the recovery sweep queue must be distinct from the main KEDA-watched queue",
        )


class TestCluster08y_RequeueRoutingInvariant(SimpleTestCase):
    """Cluster 8y: recovered tasks must flow outward to their original (main)
    queue, never back to the ``recovery`` queue the singleton pod consumes."""

    def test_08_120_requeue_uses_payload_queue_not_recovery_queue(self):
        """
        Scenario 8.120: a recovered task is re-dispatched to its own main queue.
        Given: a dead task whose payload records its original ``queue`` (main-q).
        When:  ``_requeue`` re-dispatches it.
        Then:  ``send_task`` targets ``main-q`` — and explicitly not ``recovery``
               — so KEDA sees the recovered work and the recovery pod does not
               re-consume it.
        """
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

        self.assertEqual(
            app.send_task.call_count, 1,
            msg="a recovered task must be re-dispatched exactly once",
        )
        _, called_kwargs = app.send_task.call_args
        self.assertEqual(
            called_kwargs["queue"], "main-q",
            msg="recovered task must go to its original main queue",
        )
        self.assertNotEqual(
            called_kwargs["queue"], _RECOVERY_QUEUE,
            msg="recovered task must NEVER loop back to the recovery queue",
        )

    def test_08_121_requeue_falls_back_to_default_main_queue(self):
        """
        Scenario 8.121: a recovered task with no recorded queue uses the default.
        Given: a dead task whose payload omits ``queue`` entirely.
        When:  ``_requeue`` re-dispatches it.
        Then:  it falls back to the framework's default (main) queue — never
               ``recovery`` — preserving the outward-routing invariant even when
               the original queue was not captured.
        """
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
        self.assertEqual(
            called_kwargs["queue"], "main-q",
            msg="missing payload queue must fall back to the default main queue, not recovery",
        )


class TestCluster08y_RecoveryBeatEntrypoint(SimpleTestCase):
    """Cluster 8y: ``lex-recovery-beat`` must launch an embedded-beat worker that
    consumes ONLY the ``recovery`` queue with the DatabaseScheduler."""

    def test_08_122_beat_main_launches_embedded_beat_on_recovery_queue(self):
        """
        Scenario 8.122: the entrypoint launches the correct worker invocation.
        Given: ``beat_main`` obtains the one canonical app via ``_get_app()``.
        When:  it is invoked.
        Then:  it calls ``app.worker_main`` once with an argv that starts a
               ``worker`` with embedded beat (``-B``), binds ``-Q recovery``, and
               selects the django_celery_beat ``DatabaseScheduler`` — the exact
               shape that makes the schedule admin-visible while staying isolated.
        """
        app = supervisor._get_app()
        with mock.patch.object(app, "worker_main") as worker_main:
            entrypoint.beat_main(argv=[])

        self.assertEqual(
            worker_main.call_count, 1,
            msg="beat_main must launch the worker exactly once",
        )
        (passed_argv,), _ = worker_main.call_args
        self.assertEqual(
            passed_argv[0], "worker",
            msg="argv must start a Celery worker",
        )
        self.assertIn("-B", passed_argv, msg="-B enables embedded beat")
        self.assertIn("-Q", passed_argv, msg="-Q binds the consumed queue")
        q_index = passed_argv.index("-Q")
        self.assertEqual(
            passed_argv[q_index + 1], _RECOVERY_QUEUE,
            msg="the worker must consume ONLY the dedicated recovery queue",
        )
        self.assertIn("--scheduler", passed_argv, msg="--scheduler selects the beat scheduler")
        s_index = passed_argv.index("--scheduler")
        self.assertEqual(
            passed_argv[s_index + 1],
            "django_celery_beat.schedulers:DatabaseScheduler",
            msg="the DB scheduler is what exposes the schedule in the Django admin",
        )
