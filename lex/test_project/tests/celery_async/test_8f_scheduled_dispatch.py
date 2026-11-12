"""The scheduler decides when a scheduled job runs, and runs each slot exactly once.

Intent: a lex instance runs work on a schedule with no extra pod and no Celery
beat. Schedules are rows; the served backend's clock calls ``run_due_jobs(now)``
every few seconds. The contract this pins: cron expressions mean what cron means,
in the schedule's own time zone, with one predictable rule across daylight-saving
changes (a schedule fires at most once per wall-clock time); a due slot is claimed
by exactly one clock and runs once; a slot whose previous run is still going is
skipped, not stacked; slots missed while nothing was running collapse into one run
when they are recent and are recorded as missed when they are not; a one-off job
runs once; and with Celery on, a fired job's run goes to a worker like a Calculate
click's. A regression here double-runs jobs, runs them at the wrong hour twice a
year, piles up runs after an outage, or silently never runs them.
Cluster 8f — scenarios 8.167–8.182. Type: U + E.
Covers: lex/scheduling/cron.py, lex/scheduling/dispatch.py, lex/scheduling/models.py
(next-slot maintenance).
Run: python -m lex pytest lex/test_project/tests/celery_async/test_8f_scheduled_dispatch.py -v
"""

from __future__ import annotations

import os
import threading
from contextlib import nullcontext
from datetime import datetime, timedelta, timezone as dt_timezone
from unittest.mock import Mock, patch
from zoneinfo import ZoneInfo

import pytest
from django.db import connection
from django.test import SimpleTestCase, override_settings
from django.utils import timezone

from lex.core.models.CalculationModel import CalculationModel
from lex.scheduling.cron import CronError, CronSchedule, resolve_timezone
from lex.scheduling.dispatch import run_due_jobs
from lex.scheduling.models import ScheduledJob
from lex.test_project.tests._scheduling_support import ScheduledJobTestCase
from lex.test_project.tests.calculations.models import ScheduledTargetCalc

pytestmark = pytest.mark.celery_async

UTC = dt_timezone.utc
BERLIN = ZoneInfo("Europe/Berlin")


def _berlin(*args) -> datetime:
    return datetime(*args, tzinfo=BERLIN)


def _slots(expression: str, after: datetime, count: int, tz=UTC) -> list:
    """The next ``count`` firing instants, as local wall-clock strings."""
    schedule = CronSchedule.parse(expression)
    out, moment = [], after
    for _ in range(count):
        moment = schedule.next_after(moment, tz)
        out.append(moment.astimezone(tz).strftime("%Y-%m-%d %H:%M %Z"))
    return out


class TestCluster08f_CronSchedule(SimpleTestCase):
    """Cluster 8d: cron expressions, evaluated in a time zone."""

    # -- 8.167 ---------------------------------------------------------
    def test_8_167_weekday_morning_schedule_skips_the_weekend(self):
        """
        Scenario 8.167: ``0 6 * * 1-5`` fires at 06:00 on weekdays only
        Given: the expression for "06:00, Monday to Friday" in Europe/Berlin
        When: the next slots after Friday 07:00 are computed
        Then: they are Monday to Thursday at 06:00 local time — the weekend is skipped
        """
        self.assertEqual(
            _slots("0 6 * * 1-5", _berlin(2026, 11, 6, 7, 0), 4, BERLIN),
            [
                "2026-11-09 06:00 CET",
                "2026-11-10 06:00 CET",
                "2026-11-11 06:00 CET",
                "2026-11-12 06:00 CET",
            ],
            "A weekday schedule must fire on the next four weekdays at 06:00 local time.",
        )

    # -- 8.168 ---------------------------------------------------------
    def test_8_168_steps_ranges_lists_names_and_macros_mean_what_cron_means(self):
        """
        Scenario 8.168: the classic cron syntax is understood
        Given: expressions using steps, ranges with steps, lists, names, macros and 7 for Sunday
        When: their next slots are computed
        Then: each fires exactly when Vixie cron would
        """
        after = datetime(2026, 11, 10, 10, 7, tzinfo=UTC)  # a Tuesday
        cases = {
            "*/15 * * * *": ["2026-11-10 10:15 UTC", "2026-11-10 10:30 UTC"],
            "5/20 10 * * *": ["2026-11-10 10:25 UTC", "2026-11-10 10:45 UTC"],
            "0 9-17/4 * * *": ["2026-11-10 13:00 UTC", "2026-11-10 17:00 UTC"],
            "0 8,20 * * *": ["2026-11-10 20:00 UTC", "2026-11-11 08:00 UTC"],
            "0 12 * JAN,JUL mon": ["2027-01-04 12:00 UTC", "2027-01-11 12:00 UTC"],
            "@hourly": ["2026-11-10 11:00 UTC", "2026-11-10 12:00 UTC"],
            "@monthly": ["2026-12-01 00:00 UTC", "2027-01-01 00:00 UTC"],
        }
        for expression, expected in cases.items():
            with self.subTest(expression=expression):
                self.assertEqual(
                    _slots(expression, after, 2),
                    expected,
                    f"{expression!r} must fire when cron would.",
                )
        self.assertEqual(
            _slots("0 0 * * 7", after, 2),
            _slots("0 0 * * 0", after, 2),
            "7 and 0 must both mean Sunday.",
        )

    # -- 8.169 ---------------------------------------------------------
    def test_8_169_day_fields_combine_with_or_only_when_both_are_restricted(self):
        """
        Scenario 8.169: day-of-month and day-of-week follow cron's OR rule
        Given: "the 1st, or any Monday" and "the 1st, every day of the week"
        When: their next slots after Sunday 1 November 2026 noon are computed
        Then: the first fires on Mondays and on the 1st; the second only on the 1st
        """
        after = datetime(2026, 11, 1, 12, 0, tzinfo=UTC)
        self.assertEqual(
            _slots("0 9 1 * MON", after, 3),
            ["2026-11-02 09:00 UTC", "2026-11-09 09:00 UTC", "2026-11-16 09:00 UTC"],
            "With both day fields restricted, a day matching either must fire.",
        )
        self.assertEqual(
            _slots("0 9 1 * *", after, 2),
            ["2026-12-01 09:00 UTC", "2027-01-01 09:00 UTC"],
            "With day-of-week unrestricted, only the day of month counts.",
        )

    # -- 8.170 ---------------------------------------------------------
    def test_8_170_an_unusable_expression_is_refused_with_its_reason(self):
        """
        Scenario 8.170: a bad expression never becomes a schedule
        Given: expressions that are empty, have the wrong number of fields, are out of
               range, run backwards, step by zero, can never match, or name nothing
        When: they are parsed
        Then: each is refused with a message that says what is wrong
        """
        cases = {
            "": "required",
            "* * * *": "five fields",
            "0 0 * * * *": "Six fields",
            "61 * * * *": "out of range",
            "5-1 * * * *": "runs backwards",
            "*/0 * * * *": "not a valid step",
            "0 0 30 2 *": "never fires",
            "@reboot": "not supported",
            "@sometimes": "not a known macro",
            "0 0 * * FOO": "not a valid day of week",
        }
        for expression, reason in cases.items():
            with self.subTest(expression=expression):
                with self.assertRaises(CronError, msg=f"{expression!r} must be refused") as caught:
                    CronSchedule.parse(expression)
                self.assertIn(reason, str(caught.exception), f"The refusal of {expression!r} must say why.")

    # -- 8.171 ---------------------------------------------------------
    def test_8_171_a_time_skipped_by_spring_forward_fires_once_at_the_jump(self):
        """
        Scenario 8.171: daylight saving starts — the skipped hour does not lose a run
        Given: 02:30 daily, and every 30 minutes, in Europe/Berlin, around 29 March 2026
               (clocks jump from 02:00 to 03:00)
        When: their slots through the change are computed
        Then: 02:30 fires once at 03:00 CEST that day, and the half-hourly schedule fires
              once at 03:00 for the skipped hour instead of three times
        """
        self.assertEqual(
            _slots("30 2 * * *", _berlin(2026, 3, 28, 12, 0), 2, BERLIN),
            ["2026-03-29 03:00 CEST", "2026-03-30 02:30 CEST"],
            "A daily 02:30 must still run on the spring-forward day, at the jump.",
        )
        self.assertEqual(
            _slots("*/30 * * * *", _berlin(2026, 3, 29, 1, 0), 3, BERLIN),
            ["2026-03-29 01:30 CET", "2026-03-29 03:00 CEST", "2026-03-29 03:30 CEST"],
            "Slots inside the skipped hour must collapse into one run at the jump.",
        )

    # -- 8.172 ---------------------------------------------------------
    def test_8_172_a_time_repeated_by_fall_back_fires_only_the_first_time(self):
        """
        Scenario 8.172: daylight saving ends — the repeated hour does not double-run
        Given: 02:30 daily, and every 30 minutes, in Europe/Berlin, around 25 October 2026
               (clocks go back from 03:00 to 02:00)
        When: their slots through the change are computed
        Then: 02:30 fires once (at 02:30 CEST), and the half-hourly schedule does not run
              the repeated 02:00 and 02:30 a second time
        """
        self.assertEqual(
            _slots("30 2 * * *", _berlin(2026, 10, 24, 12, 0), 2, BERLIN),
            ["2026-10-25 02:30 CEST", "2026-10-26 02:30 CET"],
            "A daily 02:30 must run once on the fall-back day.",
        )
        self.assertEqual(
            _slots("*/30 * * * *", _berlin(2026, 10, 25, 1, 40), 4, BERLIN),
            ["2026-10-25 02:00 CEST", "2026-10-25 02:30 CEST", "2026-10-25 03:00 CET", "2026-10-25 03:30 CET"],
            "The repeated wall-clock hour must not fire a second time.",
        )

    # -- 8.173 ---------------------------------------------------------
    def test_8_173_an_unknown_time_zone_is_refused(self):
        """
        Scenario 8.173: only real time zones are accepted
        Given: a misspelt zone name and an empty one
        When: they are resolved
        Then: both are refused with a message naming an example of a valid zone
        """
        for name in ("Europe/Berlinn", ""):
            with self.subTest(name=name):
                with self.assertRaises(CronError) as caught:
                    resolve_timezone(name)
                self.assertIn("Europe/Berlin", str(caught.exception), "The refusal must show a valid example.")
        self.assertEqual(str(resolve_timezone("UTC")), "UTC", "A real zone must resolve.")


class TestCluster08f_SchedulerTick(ScheduledJobTestCase):
    """Cluster 8d: ``run_due_jobs`` claims each due slot once and decides what it does."""

    NOW = datetime(2030, 1, 15, 6, 0, 30, tzinfo=UTC)

    # -- 8.174 ---------------------------------------------------------
    def test_8_174_a_due_job_runs_and_moves_to_its_next_slot(self):
        """
        Scenario 8.174: the tick starts what is due and only that
        Given: a daily 06:00 job due 30 seconds ago, and one not due until tomorrow
        When: the tick runs
        Then: the due job runs its target once, records when it ran, and its next slot
              is tomorrow 06:00; the other job is untouched
        """
        target = self.make_target()
        due = self.make_due(self.make_job("due", target_record_id=str(target.pk)), self.NOW - timedelta(seconds=30))
        later = self.make_due(
            self.make_job("later", target_record_id=str(target.pk)), self.NOW + timedelta(days=1)
        )

        report = run_due_jobs(self.NOW)
        self.wait_for(report)

        self.assertEqual(report.fired, ["due"], "Exactly the due job must be started.")
        due.refresh_from_db()
        later.refresh_from_db()
        self.assertEqual(due.last_run_at, self.NOW, "The run's start must be recorded on the job.")
        self.assertEqual(
            due.next_run_at,
            datetime(2030, 1, 16, 6, 0, tzinfo=UTC),
            "After a run, the next slot must be the following 06:00.",
        )
        self.assertEqual(later.next_run_at, self.NOW + timedelta(days=1), "A job not yet due must be left alone.")
        self.assertIsNone(later.last_run_at, "A job not yet due must not run.")
        self.assertEqual(ScheduledTargetCalc.calls, 1, "The target must be calculated exactly once.")

    # -- 8.175 ---------------------------------------------------------
    def test_8_175_a_disabled_job_never_runs(self):
        """
        Scenario 8.175: switching a job off stops it
        Given: a job created switched off, whose next slot is then forced into the past
        When: the tick runs
        Then: it has no next slot of its own, and it does not run
        """
        job = self.make_job("off", enabled=False)
        self.assertIsNone(job.next_run_at, "A disabled job must not have a next slot.")
        self.make_due(job, self.NOW - timedelta(minutes=1))

        report = run_due_jobs(self.NOW)

        self.assertEqual(report.fired, [], "A disabled job must never be started.")
        self.assertEqual(ScheduledTargetCalc.calls, 0, "Nothing must have been calculated.")

    # -- 8.176 ---------------------------------------------------------
    def test_8_176_two_clocks_at_the_same_instant_run_a_slot_once(self):
        """
        Scenario 8.176: claiming makes firing exactly-once
        Given: one due job, and two clocks (threads with their own database
               connections) ticking at the same instant
        When: both tick concurrently
        Then: the job is started by exactly one of them, and its target runs once
        """
        target = self.make_target()
        self.make_due(self.make_job("once", target_record_id=str(target.pk)), self.NOW - timedelta(seconds=5))
        barrier = threading.Barrier(2)
        reports = []

        def clock():
            try:
                barrier.wait(timeout=5)
                reports.append(run_due_jobs(self.NOW))
            finally:
                connection.close()

        threads = [threading.Thread(target=clock) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=20)
        for report in reports:
            self.wait_for(report)

        fired = [name for report in reports for name in report.fired]
        self.assertEqual(fired, ["once"], f"Exactly one clock must start the slot; got {fired!r}.")
        self.assertEqual(ScheduledTargetCalc.calls, 1, "The target must run once, not once per clock.")

    # -- 8.177 ---------------------------------------------------------
    def test_8_177_a_slot_is_skipped_while_the_previous_run_is_still_going(self):
        """
        Scenario 8.177: runs never stack
        Given: a job whose run is still in progress (its target is held open)
        When: its next slot comes due and the tick runs
        Then: the slot is skipped and the job says why; its next slot moves on; once the
              first run ends it settles in SUCCESS, the skip note and the moved slot survive
              the end of the run, and the target was calculated once
        """
        target = self.make_target()
        job = self.make_due(self.make_job("long", cron="*/5 * * * *", target_record_id=str(target.pk)), self.NOW)
        ScheduledTargetCalc.gate = threading.Event()

        first = run_due_jobs(self.NOW)
        self.assertEqual(first.fired, ["long"], "The first slot must start a run.")
        job.refresh_from_db()
        self.assertEqual(job.is_calculated, CalculationModel.IN_PROGRESS, "The run must be in progress.")

        later = self.NOW + timedelta(minutes=5)
        self.make_due(job, later)
        second = run_due_jobs(later + timedelta(seconds=1))
        self.assertEqual(second.skipped, ["long"], "A slot that comes due mid-run must be skipped.")
        job.refresh_from_db()
        self.assertIn("still in progress", job.status_message, "The job must say why the slot did not run.")
        next_slot = datetime(2030, 1, 15, 6, 10, tzinfo=UTC)
        self.assertEqual(job.next_run_at, next_slot, "The skipped job must move to its next slot.")

        ScheduledTargetCalc.gate.set()
        self.wait_for(first)
        self.assertEqual(self.wait_settled(job), CalculationModel.SUCCESS, "The first run must finish normally.")
        job.refresh_from_db()
        self.assertIn(
            "still in progress", job.status_message, "The run ending must not erase the clock's note."
        )
        self.assertEqual(job.next_run_at, next_slot, "The run ending must not move the slot back.")
        self.assertEqual(ScheduledTargetCalc.calls, 1, "The target must have run once, not once per slot.")

    # -- 8.178 ---------------------------------------------------------
    @override_settings(LEX_SCHEDULER_MISFIRE_GRACE_SECONDS=3600)
    def test_8_178_recently_missed_slots_collapse_into_one_run(self):
        """
        Scenario 8.178: catching up after a short outage runs once, not once per slot
        Given: a job every 10 minutes whose slot is 50 minutes overdue (five missed
               slots, all inside the one-hour grace window)
        When: the tick runs
        Then: the job runs once, and its next slot is the first 10-minute mark after now
        """
        target = self.make_target()
        job = self.make_due(
            self.make_job("catchup", cron="*/10 * * * *", target_record_id=str(target.pk)),
            self.NOW - timedelta(minutes=50),
        )

        report = run_due_jobs(self.NOW)
        self.wait_for(report)

        self.assertEqual(report.fired, ["catchup"], "Recently missed slots must still run.")
        job.refresh_from_db()
        self.assertEqual(job.next_run_at, datetime(2030, 1, 15, 6, 10, tzinfo=UTC), "Missed slots must not be replayed.")
        self.assertEqual(ScheduledTargetCalc.calls, 1, "Five missed slots must cost one run.")

    # -- 8.179 ---------------------------------------------------------
    @override_settings(LEX_SCHEDULER_MISFIRE_GRACE_SECONDS=3600)
    def test_8_179_a_slot_later_than_the_grace_window_is_recorded_as_missed(self):
        """
        Scenario 8.179: a long outage does not wake up and run stale work
        Given: a daily job whose slot is two hours overdue, with a one-hour grace window
        When: the tick runs
        Then: it does not run; it says which slot was missed and why; its next slot is
              tomorrow's
        """
        job = self.make_due(self.make_job("stale"), self.NOW - timedelta(hours=2))

        report = run_due_jobs(self.NOW)

        self.assertEqual(report.missed, ["stale"], "A slot beyond the grace window must be recorded as missed.")
        self.assertEqual(report.fired, [], "It must not run.")
        job.refresh_from_db()
        self.assertIn("Missed the run due at 2030-01-15 04:00", job.status_message, "The missed slot must be named.")
        self.assertEqual(job.next_run_at, datetime(2030, 1, 16, 6, 0, tzinfo=UTC), "The next slot must be tomorrow's.")
        self.assertEqual(job.is_calculated, CalculationModel.NOT_CALCULATED, "No run may have started.")

    # -- 8.180 ---------------------------------------------------------
    def test_8_180_a_one_off_job_runs_once(self):
        """
        Scenario 8.180: "run at" means once
        Given: a job with no cron expression and a 'run at' time an hour from now
        When: the tick runs just after that time, and again later
        Then: its next slot was that time; it runs once; afterwards it has no next slot
              and the later tick does not run it again
        """
        target = self.make_target()
        run_at = (timezone.now() + timedelta(hours=1)).replace(microsecond=0)
        job = self.make_job("once-off", run_at=run_at, target_record_id=str(target.pk))
        self.assertEqual(job.next_run_at, run_at, "A one-off job's only slot is its 'run at' time.")

        report = run_due_jobs(run_at + timedelta(seconds=1))
        self.wait_for(report)
        again = run_due_jobs(run_at + timedelta(minutes=5))

        self.assertEqual(report.fired, ["once-off"], "The one-off slot must run.")
        self.assertEqual(again.fired, [], "A one-off job must not run a second time.")
        job.refresh_from_db()
        self.assertIsNone(job.next_run_at, "After its run a one-off job has no next slot.")
        self.assertEqual(ScheduledTargetCalc.calls, 1, "The target must have run once.")

    # -- 8.181 ---------------------------------------------------------
    def test_8_181_with_celery_on_a_fired_job_runs_on_a_worker(self):
        """
        Scenario 8.181: the Celery route — scheduled runs go where clicked runs go
        Given: Celery is on, and a job is due
        When: the tick fires it
        Then: the job's run is handed to a Celery worker (``calc_and_save`` with the job)
              instead of running in the backend, so nothing is calculated in-process
        """
        from lex.lex_app import celery_tasks

        target = self.make_target()
        job = self.make_due(self.make_job("on-worker", target_record_id=str(target.pk)), self.NOW)
        task = Mock()
        task.id = "task-8-168"

        with patch.dict(os.environ, {"CELERY_ACTIVE": "true"}), patch.object(
            ScheduledJob, "should_use_celery", return_value=True
        ), patch.object(celery_tasks.calc_and_save, "delay", return_value=task) as delay, patch(
            "lex.lex_app.celery_tasks._celery_is_active", return_value=True
        ), patch(
            "lex.lex_app.celery_tasks.allow_join_result", return_value=nullcontext()
        ):
            report = run_due_jobs(self.NOW)
            self.wait_for(report)

        self.assertEqual(report.fired, ["on-worker"], "The due job must be fired.")
        delay.assert_called_once()
        dispatched_models = delay.call_args.args[0]
        self.assertEqual(
            [type(m) for m in dispatched_models], [ScheduledJob], "The worker must be handed the job itself."
        )
        self.assertEqual(dispatched_models[0].pk, job.pk, "The worker must be handed this job.")
        self.assertEqual(ScheduledTargetCalc.calls, 0, "With Celery on, nothing may run in the backend.")

    # -- 8.182 ---------------------------------------------------------
    def test_8_182_a_stale_copy_saved_after_a_run_does_not_bring_its_slot_back(self):
        """
        Scenario 8.182: an edit from an old copy cannot make a slot run twice
        Given: a copy of a due job loaded before the clock runs its slot
        When: the clock runs the slot, and the old copy is then renamed and saved
        Then: the rename is stored; the slot stays where the clock moved it and the
              run's status stays; a tick at the same instant runs nothing more
        """
        target = self.make_target()
        job = self.make_due(self.make_job("stale-copy", target_record_id=str(target.pk)), self.NOW)
        stale = ScheduledJob.objects.get(pk=job.pk)

        report = run_due_jobs(self.NOW)
        self.wait_for(report)
        self.wait_settled(job)
        stale.name = "renamed"
        stale.save()

        job.refresh_from_db()
        self.assertEqual(job.name, "renamed", "The rename must be stored.")
        self.assertEqual(
            job.next_run_at,
            datetime(2030, 1, 16, 6, 0, tzinfo=UTC),
            "An old copy must not write back the slot the clock already ran.",
        )
        self.assertEqual(job.is_calculated, CalculationModel.SUCCESS, "An old copy must not reset the run's status.")
        self.assertEqual(run_due_jobs(self.NOW + timedelta(minutes=1)).fired, [], "The slot must not run again.")
        self.assertEqual(ScheduledTargetCalc.calls, 1, "The target must have run once.")
