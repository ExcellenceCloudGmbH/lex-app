"""The scheduler comes up by itself in the served backend, and only there.

Intent: every lex instance gets a working scheduler with no configuration and no
extra pod. The clock starts in the process that serves the app — never in the
uvicorn reloader's parent, a Celery worker, a management command or a test run —
and is on unless someone turns it off, which is logged as a WARNING because a
clock that is silently off looks exactly like one with nothing to do. It
survives a failing pass. Developers ship schedules with their code
(``@lex_job(schedule=...)``): at startup each becomes a schedule row kept in step
with the code, without overriding the switch a user flipped, and is switched off
when its declaration is removed. ``lex run_scheduler --once`` does the same work
on demand. ``ScheduledJob`` itself is registered like any model and kept out of
bitemporal history. A regression here leaves instances with no scheduler, two
clocks in the wrong processes, declared schedules that drift from the code, or a
history table growing on every run.
Cluster 1bk — scenarios 1.619–1.629. Type: U + E.
Covers: lex/scheduling/loop.py, lex/scheduling/registry.py, lex/scheduling/apps.py,
lex/scheduling/management/commands/run_scheduler.py, lex/lex_app/settings.py
(``LEX_SCHEDULER_*``), lex/lex_app/asgi.py, lex/utilities/config/generic_app_config.py.
Run: python -m lex pytest lex/test_project/tests/init/test_1bk_scheduler_bootstrap.py -v
"""

from __future__ import annotations

import io
import os
import threading
import time
from datetime import timedelta
from unittest.mock import patch

import pytest
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.core.management import call_command
from django.test import SimpleTestCase, override_settings
from django.utils import timezone

from lex.core.models.CalculationModel import CalculationModel
from lex.scheduling.loop import SchedulerLoop, start_scheduler_for_served_backend, stop_scheduler
from lex.scheduling.models import ScheduledJob
from lex.scheduling.registry import get_job, lex_job, sync_code_jobs, unregister
from lex.test_project.tests._scheduling_support import CALLS, ScheduledJobTestCase
from lex.test_project.tests.calculations.models import ScheduledTargetCalc

pytestmark = pytest.mark.init


def _wait_until(predicate, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return False


class TestCluster01bk_SchedulerClock(SimpleTestCase):
    """Cluster 1ay: where and whether the clock runs."""

    def tearDown(self):
        stop_scheduler()
        super().tearDown()

    # -- 1.619 ---------------------------------------------------------
    def test_1_619_the_clock_starts_only_in_the_served_backend(self):
        """
        Scenario 1.619: one clock, in the process that serves the app
        Given: this process, which is a test run, not the uvicorn-served backend
               (importing ``lex_app.asgi`` here started nothing)
        When: the clock is asked to start as the backend asks, and then as if this
              were the served backend
        Then: the first start does nothing; the second starts one clock thread, a
              repeated start returns that same thread, and it stops on request
        """
        import atexit

        from lex.lex_app import asgi  # the import the served backend does

        try:  # as in 1u: importing the module must not leave its shutdown hook behind
            atexit.unregister(asgi.on_server_shutdown)
        except ValueError:
            pass

        self.assertIsNone(start_scheduler_for_served_backend(), "Outside the served backend no clock may start.")
        self.assertFalse(
            any(thread.name == "lex-scheduler" for thread in threading.enumerate()),
            "Importing the ASGI module in a non-serving process must not start a clock.",
        )

        with patch.object(SchedulerLoop, "_run_tick"), patch.object(SchedulerLoop, "_run_sync"):
            started = start_scheduler_for_served_backend(force=True)
            self.assertIsNotNone(started, "In the served backend the clock must start.")
            self.assertTrue(started.is_alive(), "The clock thread must be running.")
            self.assertIs(start_scheduler_for_served_backend(force=True), started, "Starting twice must not add a clock.")
            stop_scheduler()
            self.assertFalse(started.is_alive(), "The clock must stop on request.")

    # -- 1.620 ---------------------------------------------------------
    @override_settings(LEX_SCHEDULER_ENABLED=False)
    def test_1_620_switching_the_clock_off_is_said_out_loud(self):
        """
        Scenario 1.620: off is a visible choice
        Given: ``LEX_SCHEDULER_ENABLED=false``
        When: the served backend starts the clock
        Then: no clock starts, and a WARNING says that no scheduled job will run
        """
        with self.assertLogs("lex.scheduling.loop", level="WARNING") as logs:
            self.assertIsNone(start_scheduler_for_served_backend(force=True), "A disabled clock must not start.")
        self.assertIn("no scheduled job will run", " ".join(logs.output), "The warning must say what it means.")

    # -- 1.621 ---------------------------------------------------------
    def test_1_621_the_scheduler_is_on_by_default(self):
        """
        Scenario 1.621: absence of configuration is not absence of function
        Given: no ``LEX_SCHEDULER_*`` environment variables
        When: settings load
        Then: the scheduler is on, ticks every 30 s and lets a slot run up to an hour late
        """
        if any(name.startswith("LEX_SCHEDULER_") for name in os.environ):
            self.skipTest("LEX_SCHEDULER_* is set in this environment; the defaults are not observable.")
        self.assertIs(settings.LEX_SCHEDULER_ENABLED, True, "The scheduler must be on by default.")
        self.assertEqual(settings.LEX_SCHEDULER_TICK_SECONDS, 30, "The default tick is 30 seconds.")
        self.assertEqual(settings.LEX_SCHEDULER_MISFIRE_GRACE_SECONDS, 3600, "The default grace is one hour.")

    # -- 1.622 ---------------------------------------------------------
    def test_1_622_the_clock_survives_a_failing_pass(self):
        """
        Scenario 1.622: one bad pass does not stop scheduling
        Given: a clock whose first two ticks raise
        When: it runs
        Then: it logs each failure and keeps ticking
        """
        calls = []

        def tick():
            calls.append(1)
            if len(calls) <= 2:
                raise RuntimeError("database went away")

        clock = SchedulerLoop(0.01, tick=tick, sync=lambda: None)
        with self.assertLogs("lex.scheduling.loop", level="ERROR") as logs:
            clock.start()
            self.assertTrue(_wait_until(lambda: len(calls) >= 4), "The clock must keep ticking after failures.")
            clock.stop()
            clock.join(2)
        self.assertGreaterEqual(len(logs.output), 2, "Each failing pass must be logged.")


class TestCluster01bk_CodeDeclaredSchedules(ScheduledJobTestCase):
    """Cluster 1ay: schedules shipped with the code, and the command that runs them."""

    def setUp(self):
        super().setUp()
        self._declared = []

    def tearDown(self):
        for name in self._declared:
            unregister(name)
        super().tearDown()

    def _declare(self, name, func=None, **options):
        lex_job(name, **options)(func or (lambda **kwargs: CALLS.append((name, kwargs))))
        self._declared.append(name)

    # -- 1.623 ---------------------------------------------------------
    def test_1_623_a_declared_schedule_becomes_a_schedule_row(self):
        """
        Scenario 1.623: ``@lex_job(schedule=...)`` ships a schedule
        Given: a function declared with a schedule, a time zone and arguments
        When: the code-declared schedules are synced (as the clock does on start)
        Then: a schedule with that name exists, marked as declared in code, running
              that function with those arguments, switched on and scheduled
        """
        self._declare("tests.declared", schedule="15 3 * * *", timezone="Europe/Berlin", kwargs={"days": 30})

        report = sync_code_jobs()

        self.assertEqual(report.created, ["tests.declared"], "The declared schedule must be created.")
        job = ScheduledJob.objects.get(name="tests.declared")
        self.assertTrue(job.managed_by_code, "It must be marked as declared in code.")
        self.assertEqual(
            (job.target_type, job.function_name, job.cron, job.timezone, job.kwargs),
            (ScheduledJob.TARGET_FUNCTION, "tests.declared", "15 3 * * *", "Europe/Berlin", {"days": 30}),
            "It must run the declared function on the declared schedule.",
        )
        self.assertTrue(job.enabled, "A declared schedule starts switched on.")
        self.assertIsNotNone(job.next_run_at, "A declared schedule must be scheduled.")
        self.assertIsNone(job.run_as, "Nobody saved it, so it runs as nobody (the scheduler).")

    # -- 1.624 ---------------------------------------------------------
    def test_1_624_a_changed_declaration_updates_the_row_but_keeps_the_users_switch(self):
        """
        Scenario 1.624: the code owns the schedule, the user owns the switch
        Given: a declared schedule that a user switched off
        When: the declaration's cron expression changes and the schedules are synced
        Then: the row has the new expression and is still switched off
        """
        self._declare("tests.moving", schedule="0 1 * * *")
        sync_code_jobs()
        ScheduledJob.objects.filter(name="tests.moving").update(enabled=False, next_run_at=None)

        unregister("tests.moving")
        self._declare("tests.moving", schedule="0 2 * * *")
        report = sync_code_jobs()

        self.assertEqual(report.updated, ["tests.moving"], "The changed declaration must be applied.")
        job = ScheduledJob.objects.get(name="tests.moving")
        self.assertEqual(job.cron, "0 2 * * *", "The row must follow the code.")
        self.assertFalse(job.enabled, "The user's switch must be kept.")

    # -- 1.625 ---------------------------------------------------------
    def test_1_625_a_removed_declaration_switches_its_schedule_off(self):
        """
        Scenario 1.625: removing the decorator stops the schedule, visibly
        Given: a declared schedule that was synced
        When: its declaration is removed and the schedules are synced; then it is
              declared again
        Then: it is switched off with a message saying why (not deleted); declared
              again, it is switched back on
        """
        self._declare("tests.dropped", schedule="0 4 * * *")
        sync_code_jobs()

        unregister("tests.dropped")
        removed = sync_code_jobs()
        job = ScheduledJob.objects.get(name="tests.dropped")
        self.assertEqual(removed.disabled, ["tests.dropped"], "The orphaned schedule must be switched off.")
        self.assertFalse(job.enabled, "It must be off.")
        self.assertIsNone(job.next_run_at, "It must not be scheduled.")
        self.assertIn("removed from the code", job.status_message, "It must say why it is off.")

        self._declare("tests.dropped", schedule="0 4 * * *")
        sync_code_jobs()
        job.refresh_from_db()
        self.assertTrue(job.enabled, "Declared again, it must be switched back on.")
        self.assertEqual(job.status_message, "", "The removal note must be cleared.")

    # -- 1.626 ---------------------------------------------------------
    def test_1_626_a_schedule_created_in_the_app_is_never_taken_over(self):
        """
        Scenario 1.626: the code does not overwrite a person's schedule
        Given: a schedule a person created, whose name a declaration then also uses
        When: the schedules are synced
        Then: the person's schedule is unchanged and the conflict is reported
        """
        self.make_job(
            "tests.clash", target_type=ScheduledJob.TARGET_FUNCTION, function_name="tests.record_call", cron="0 9 * * *"
        )
        self._declare("tests.clash", schedule="0 5 * * *")

        report = sync_code_jobs()

        self.assertEqual(report.conflicts, ["tests.clash"], "The clash must be reported.")
        job = ScheduledJob.objects.get(name="tests.clash")
        self.assertEqual((job.cron, job.managed_by_code), ("0 9 * * *", False), "The person's schedule must stay as is.")

    # -- 1.627 ---------------------------------------------------------
    def test_1_627_a_job_name_belongs_to_one_function(self):
        """
        Scenario 1.627: registration refuses ambiguity and bad declarations
        Given: a registered job name
        When: a different function registers the same name; the same function
              registers again (a module imported twice); a declaration with an
              unusable schedule is made
        Then: the first is refused naming both functions, the second is accepted, the
              third is refused with the cron reason
        """
        def original():
            return None

        def impostor():
            return None

        self._declare("tests.unique", func=original)
        with self.assertRaises(ImproperlyConfigured) as clash:
            lex_job("tests.unique")(impostor)
        self.assertIn("impostor", str(clash.exception), "The refusal must name the second function.")
        lex_job("tests.unique")(original)  # the same function again: fine
        self.assertIs(get_job("tests.unique").func, original, "The registration must still be the original.")

        with self.assertRaises(ImproperlyConfigured) as bad:
            lex_job("tests.bad", schedule="0 0 31 2 *")(original)
        self.assertIn("never fires", str(bad.exception), "A bad declaration must say why.")
        self.assertIsNone(get_job("tests.bad"), "A refused declaration must not be registered.")

    # -- 1.628 ---------------------------------------------------------
    def test_1_628_run_scheduler_once_syncs_and_runs_what_is_due(self):
        """
        Scenario 1.628: ``lex run_scheduler --once``
        Given: a declared schedule not yet synced, and a schedule that is due
        When: ``run_scheduler --once`` runs
        Then: the declared schedule exists afterwards; the due schedule's run has
              finished (the command waited for it) and its target was calculated
        """
        self._declare("tests.via_command", schedule="0 6 * * *")
        target = self.make_target()
        due = self.make_due(
            self.make_job("due-now", target_record_id=str(target.pk)), timezone.now() - timedelta(minutes=1)
        )
        out = io.StringIO()

        call_command("run_scheduler", "--once", stdout=out)

        self.assertTrue(ScheduledJob.objects.filter(name="tests.via_command").exists(), "The command must sync.")
        due.refresh_from_db()
        self.assertEqual(due.is_calculated, CalculationModel.SUCCESS, "The command must wait for the run it started.")
        self.assertEqual(ScheduledTargetCalc.calls, 1, "The due schedule's target must have run.")
        self.assertIn("fired=['due-now']", out.getvalue(), "The command must report what it started.")

    # -- 1.629 ---------------------------------------------------------
    def test_1_629_scheduled_jobs_are_registered_and_kept_out_of_history(self):
        """
        Scenario 1.629: the model is wired like any model, minus history
        Given: the framework after startup
        When: its model registry is inspected
        Then: ``ScheduledJob`` is registered for the API/UI, and has no bitemporal
              history (every run rewrites the row; history would grow without bound)
        """
        from lex.process_admin.settings import processAdminSite

        self.assertIn(ScheduledJob, processAdminSite.registered_models, "ScheduledJob must be registered.")
        self.assertFalse(hasattr(ScheduledJob, "history"), "ScheduledJob must not be history-tracked.")
