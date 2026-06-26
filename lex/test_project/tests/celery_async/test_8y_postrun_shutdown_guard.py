"""Post-task warm-shutdown must honour the idle-shutdown master switch.

Intent: ``LEX_WORKER_IDLE_SHUTDOWN_ENABLED`` is the single master switch that
turns *all* worker self-termination off. The embedded-beat recovery pod
(``celery_beat_recovery.yaml``) sets it to ``false`` precisely because that pod
runs ``celery worker -B``: it is idle by design between scheduled sweeps and
must stay up so beat can keep firing the recovery schedule. The chart comment
promises the flag disables "both the watchdog and post-task warm shutdown".

The ``task_postrun`` handler ``shutdown_worker_after_task_completion`` broadcasts
a warm-shutdown request after every completed task so KEDA ScaledJob workers
terminate once their one task is done. If that handler ignores the master
switch, the recovery-beat pod warm-shuts-down after its first sweep, killing
beat with it and crash-looping the pod — exactly the regression observed in
prod (sweep fires, then ``worker: Warm shutdown`` / ``beat: Shutting down``).
A regression here silently breaks the whole recovery/future-edit scheduler.

Cluster 8y — scenarios 8.125–8.128. Type: U.
Covers: lex/lex_app/celery.py (shutdown_worker_after_task_completion).
Run: python -m lex pytest lex/test_project/tests/celery_async/test_8y_postrun_shutdown_guard.py -v
"""

import types
from unittest import mock

import pytest
from django.test import SimpleTestCase

import lex.lex_app.celery as celery_mod

pytestmark = pytest.mark.celery_async


def _fake_task(hostname="celery@recovery-beat-pod"):
    """A task stand-in exposing the surfaces the postrun handler reads.

    The handler needs ``task.request.hostname`` (via ``_get_worker_hostname``)
    and ``task.app.control.broadcast``; nothing else from the real Celery task.
    """
    control = mock.Mock()
    app = types.SimpleNamespace(control=control)
    request = types.SimpleNamespace(hostname=hostname)
    return types.SimpleNamespace(request=request, app=app), control


class TestCluster08y_PostrunShutdownGuard(SimpleTestCase):
    """Cluster 8y: the post-task warm shutdown respects the idle-shutdown switch."""

    def test_8_125_noop_when_idle_shutdown_disabled(self):
        """
        Scenario 8.125: master switch off ⇒ no post-task shutdown broadcast.
        Given: a non-local deployment target where LEX_WORKER_IDLE_SHUTDOWN_ENABLED=false.
        When: a task completes and task_postrun fires.
        Then: no warm-shutdown broadcast is sent, so the recovery-beat pod survives.
        """
        task, control = _fake_task()
        with mock.patch.object(celery_mod, "_is_non_local_deployment_target",
                               return_value=True):
            with mock.patch.object(celery_mod, "_idle_shutdown_enabled",
                                   return_value=False):
                celery_mod.shutdown_worker_after_task_completion(
                    task_id="sweep-1", task=task)
        self.assertFalse(
            control.broadcast.called,
            "post-task warm shutdown fired despite the master switch being off — "
            "this kills beat on the recovery pod",
        )

    def test_8_126_broadcasts_when_enabled_and_non_local(self):
        """
        Scenario 8.126: switch on + non-local ⇒ post-task shutdown still broadcasts.
        Given: a non-local deployment target with the idle-shutdown switch on.
        When: a task completes and task_postrun fires.
        Then: the warm-shutdown is broadcast to this worker (KEDA scale-to-zero path).
        """
        task, control = _fake_task("celery@keda-worker")
        with mock.patch.object(celery_mod, "_is_non_local_deployment_target",
                               return_value=True):
            with mock.patch.object(celery_mod, "_idle_shutdown_enabled",
                                   return_value=True):
                celery_mod.shutdown_worker_after_task_completion(
                    task_id="job-1", task=task)
        control.broadcast.assert_called_once()
        _, kwargs = control.broadcast.call_args
        self.assertEqual(
            kwargs.get("destination"), ["celery@keda-worker"],
            "warm-shutdown must target the worker that completed the task",
        )
        self.assertEqual(
            kwargs.get("arguments"), {"completed_task_id": "job-1"},
            "the completed task id must be excluded from the idle check",
        )

    def test_8_127_noop_when_local_deployment(self):
        """
        Scenario 8.127: local deployment target ⇒ never self-terminates.
        Given: a local/dev deployment target (idle-shutdown irrelevant).
        When: a task completes and task_postrun fires.
        Then: no broadcast — local runs must never warm-shutdown the worker.
        """
        task, control = _fake_task()
        with mock.patch.object(celery_mod, "_is_non_local_deployment_target",
                               return_value=False):
            celery_mod.shutdown_worker_after_task_completion(
                task_id="sweep-1", task=task)
        self.assertFalse(
            control.broadcast.called,
            "post-task warm shutdown fired on a local deployment target",
        )

    def test_8_128_noop_when_task_missing(self):
        """
        Scenario 8.128: no task in the signal payload ⇒ safe no-op.
        Given: a non-local, switch-on environment but task=None.
        When: task_postrun fires without a task (defensive path).
        Then: the handler returns without raising and broadcasts nothing.
        """
        with mock.patch.object(celery_mod, "_is_non_local_deployment_target",
                               return_value=True):
            with mock.patch.object(celery_mod, "_idle_shutdown_enabled",
                                   return_value=True):
                # Must not raise even though no task object is supplied.
                celery_mod.shutdown_worker_after_task_completion(
                    task_id="sweep-1", task=None)
