"""A scheduled job's run is an ordinary calculation, and does what the job targets.

Intent: firing a scheduled job starts the job's own calculation exactly as the
Calculate button starts one, and that calculation does the job's work — calculate
a target record (the documented parent → child pattern), calculate every record of
a model, or call a function registered with ``@lex_job``. The state machine is the
one users already know: the job and its target land in SUCCESS or ERROR, the
error is kept on the job, the run is in the job's calculation log. A run over
every record is not one transaction: one failing record does not stop the others
or undo their results, and closed records are left alone. A regression here makes
scheduled work fail silently, report success for a failed target, or roll back a
night's worth of results because one record failed.
Cluster 7x — scenarios 7.269–7.278. Type: E.
Covers: lex/scheduling/models.py (``ScheduledJob.calculate``), lex/scheduling/dispatch.py
(``fire``), lex/core/calculation_start.py.
Run: python -m lex pytest lex/test_project/tests/calculations/test_7x_scheduled_job_runs.py -v
"""

from __future__ import annotations

import threading
from datetime import datetime, timezone as dt_timezone

import pytest

from lex.audit_logging.models.CalculationLog import CalculationLog
from lex.core.models.CalculationModel import CalculationModel
from lex.scheduling.dispatch import run_due_jobs
from lex.scheduling.models import ScheduledJob
from lex.scheduling.registry import lex_job, unregister
from lex.test_project.tests._scheduling_support import CALLS, ScheduledJobTestCase

from .models import ScheduledTargetCalc

pytestmark = pytest.mark.calculations

NOW = datetime(2030, 1, 15, 6, 0, 10, tzinfo=dt_timezone.utc)


class TestCluster07x_ScheduledJobRuns(ScheduledJobTestCase):
    """Cluster 7v: what a fired scheduled job's calculation does."""

    def _run(self, job: ScheduledJob) -> ScheduledJob:
        """Make ``job`` due, tick, wait for its run, and return it settled."""
        self.make_due(job, NOW)
        report = run_due_jobs(NOW)
        self.assertEqual(report.fired, [job.name], f"The tick must fire {job.name!r}.")
        self.wait_for(report)
        self.wait_settled(job)
        job.refresh_from_db()
        return job

    # -- 7.269 ---------------------------------------------------------
    def test_7_269_a_run_calculates_its_target_and_both_settle_in_success(self):
        """
        Scenario 7.269: the job's run is the parent, the target the child
        Given: a job targeting one record of a calculation model
        When: the job fires
        Then: the target is calculated once and keeps its result; target and job both
              settle in SUCCESS; the job's calculation log names the job
        """
        target = self.make_target()
        job = self._run(self.make_job("nightly", target_record_id=str(target.pk)))

        target.refresh_from_db()
        self.assertEqual(job.is_calculated, CalculationModel.SUCCESS, "The job must settle in SUCCESS.")
        self.assertEqual(target.is_calculated, CalculationModel.SUCCESS, "The target must settle in SUCCESS.")
        self.assertEqual(target.runs, 1, "The target's calculate() result must be saved.")
        self.assertEqual(ScheduledTargetCalc.calls, 1, "The target must be calculated exactly once.")
        logged = " ".join(CalculationLog.objects.values_list("calculation_log", flat=True))
        self.assertIn("Scheduled job: nightly", logged, "The run must be in the job's calculation log.")

    # -- 7.270 ---------------------------------------------------------
    def test_7_270_a_failing_target_leaves_the_job_in_error_with_the_reason(self):
        """
        Scenario 7.270: the job reports its target's failure
        Given: a job targeting a record whose calculate() raises
        When: the job fires
        Then: the target and the job settle in ERROR, and the job keeps the target's error
        """
        target = self.make_target(should_fail=True)
        job = self._run(self.make_job("fails", target_record_id=str(target.pk)))

        target.refresh_from_db()
        self.assertEqual(target.is_calculated, CalculationModel.ERROR, "The failing target must be in ERROR.")
        self.assertEqual(job.is_calculated, CalculationModel.ERROR, "A job whose target failed must be in ERROR.")
        self.assertIn(
            "failing on purpose",
            job.calculation_error_message,
            "The job must keep the error that made its target fail.",
        )

    # -- 7.271 ---------------------------------------------------------
    def test_7_271_every_record_mode_runs_all_and_keeps_the_successes(self):
        """
        Scenario 7.271: one bad record does not cost the others
        Given: a job for every record of a model, where the middle record fails
        When: the job fires
        Then: all three are calculated; the two good ones keep SUCCESS and their results;
              the job settles in ERROR naming the failed record
        """
        good_a = self.make_target("a")
        bad = self.make_target("b", should_fail=True)
        good_c = self.make_target("c")
        job = self._run(self.make_job("all-records"))

        for record in (good_a, bad, good_c):
            record.refresh_from_db()
        self.assertEqual(ScheduledTargetCalc.calls, 3, "Every record must be calculated.")
        self.assertEqual(
            [good_a.is_calculated, good_c.is_calculated],
            [CalculationModel.SUCCESS, CalculationModel.SUCCESS],
            "Records that succeeded must keep SUCCESS although another record failed.",
        )
        self.assertEqual([good_a.runs, good_c.runs], [1, 1], "Their results must not be rolled back.")
        self.assertEqual(bad.is_calculated, CalculationModel.ERROR, "The failing record must be in ERROR.")
        self.assertEqual(job.is_calculated, CalculationModel.ERROR, "The job must report the failure.")
        self.assertIn("1 of 3", job.calculation_error_message, "The job must say how many failed.")
        self.assertIn(f"{bad.pk}:", job.calculation_error_message, "The job must name the failed record.")

    # -- 7.272 ---------------------------------------------------------
    def test_7_272_closed_records_are_left_alone(self):
        """
        Scenario 7.272: a scheduled run respects closed records
        Given: a job for every record, where one record is closed
        When: the job fires
        Then: the open record is calculated, the closed one keeps its status, and the
              job succeeds
        """
        open_record = self.make_target("open")
        closed = self.make_target("closed", closed=True)
        job = self._run(self.make_job("skip-closed"))

        open_record.refresh_from_db()
        closed.refresh_from_db()
        self.assertEqual(open_record.is_calculated, CalculationModel.SUCCESS, "The open record must be calculated.")
        self.assertEqual(
            closed.is_calculated, CalculationModel.NOT_CALCULATED, "A closed record must keep its status."
        )
        self.assertEqual(ScheduledTargetCalc.calls, 1, "Only the open record may be calculated.")
        self.assertEqual(job.is_calculated, CalculationModel.SUCCESS, "Skipping a closed record is not a failure.")

    # -- 7.273 ---------------------------------------------------------
    def test_7_273_a_function_target_is_called_with_its_arguments(self):
        """
        Scenario 7.273: a registered function runs as the job
        Given: a job targeting the registered function ``tests.record_call`` with arguments
        When: the job fires
        Then: the function is called once with exactly those arguments and the job succeeds
        """
        job = self._run(
            self.make_job(
                "digest",
                target_type=ScheduledJob.TARGET_FUNCTION,
                function_name="tests.record_call",
                kwargs={"recipients": ["ops@example.test"], "days": 7},
            )
        )

        self.assertEqual(
            CALLS,
            [("tests.record_call", {"recipients": ["ops@example.test"], "days": 7})],
            "The function must be called once, with the job's arguments.",
        )
        self.assertEqual(job.is_calculated, CalculationModel.SUCCESS, "The job must succeed.")

    # -- 7.274 ---------------------------------------------------------
    def test_7_274_a_failing_function_leaves_the_job_in_error(self):
        """
        Scenario 7.274: a function that raises fails the run
        Given: a job targeting a registered function that raises
        When: the job fires
        Then: the job settles in ERROR and keeps the function's error
        """
        job = self._run(
            self.make_job("boom", target_type=ScheduledJob.TARGET_FUNCTION, function_name="tests.explode")
        )

        self.assertEqual(job.is_calculated, CalculationModel.ERROR, "The job must be in ERROR.")
        self.assertIn("failing on purpose", job.calculation_error_message, "The function's error must be kept.")

    # -- 7.275 ---------------------------------------------------------
    def test_7_275_a_function_removed_from_the_code_fails_with_the_reason(self):
        """
        Scenario 7.275: the job outlives its function
        Given: a job for a registered function that is then removed from the code
        When: the job fires
        Then: the job settles in ERROR saying the function is no longer registered
        """
        lex_job("tests.short_lived")(lambda: None)
        job = self.make_job("orphan", target_type=ScheduledJob.TARGET_FUNCTION, function_name="tests.short_lived")
        unregister("tests.short_lived")

        job = self._run(job)

        self.assertEqual(job.is_calculated, CalculationModel.ERROR, "The job must be in ERROR.")
        self.assertIn(
            "No function is registered as 'tests.short_lived'",
            job.calculation_error_message,
            "The job must say which function is missing.",
        )

    # -- 7.276 ---------------------------------------------------------
    def test_7_276_a_deleted_target_record_fails_with_the_reason(self):
        """
        Scenario 7.276: the job outlives its record
        Given: a job targeting one record, which is then deleted
        When: the job fires
        Then: the job settles in ERROR saying the record no longer exists
        """
        target = self.make_target()
        job = self.make_job("gone", target_record_id=str(target.pk))
        target.delete()

        job = self._run(job)

        self.assertEqual(job.is_calculated, CalculationModel.ERROR, "The job must be in ERROR.")
        self.assertIn("no longer exists", job.calculation_error_message, "The job must say the record is gone.")

    # -- 7.277 ---------------------------------------------------------
    def test_7_277_a_successful_run_clears_the_previous_error(self):
        """
        Scenario 7.277: the error shown is the last run's
        Given: a job whose first run failed
        When: the cause is fixed and the job runs again
        Then: it settles in SUCCESS with no error message left from the failed run
        """
        target = self.make_target(should_fail=True)
        job = self._run(self.make_job("recovers", target_record_id=str(target.pk)))
        self.assertTrue(job.calculation_error_message, "The first run must leave its error.")

        ScheduledTargetCalc.objects.filter(pk=target.pk).update(should_fail=False)
        job = self._run(job)

        self.assertEqual(job.is_calculated, CalculationModel.SUCCESS, "The second run must succeed.")
        self.assertEqual(job.calculation_error_message, "", "The failed run's error must not outlive it.")

    # -- 7.278 ---------------------------------------------------------
    def test_7_278_an_edit_made_during_a_run_survives_the_end_of_the_run(self):
        """
        Scenario 7.278: the run's own save does not undo what people did meanwhile
        Given: a job whose run is in progress (its target is held open)
        When: someone switches the job off during the run, and the run then ends
        Then: the edit does not start another run; the job settles in SUCCESS and
              stays switched off, with no next slot — the copy of the job the run
              saves at its end does not write the old schedule back
        """
        target = self.make_target()
        job = self.make_due(self.make_job("edited-midway", target_record_id=str(target.pk)), NOW)
        ScheduledTargetCalc.gate = threading.Event()
        report = run_due_jobs(NOW)
        self.assertEqual(report.fired, ["edited-midway"], "The job must start.")

        edited = ScheduledJob.objects.get(pk=job.pk)
        self.assertEqual(edited.is_calculated, CalculationModel.IN_PROGRESS, "The run must be in progress.")
        edited.enabled = False
        edited.save()
        self.assertEqual(
            ScheduledJob.objects.get(pk=job.pk).is_calculated,
            CalculationModel.IN_PROGRESS,
            "Editing the schedule must not change the status of the run in progress.",
        )

        ScheduledTargetCalc.gate.set()
        self.wait_for(report)
        self.assertEqual(self.wait_settled(job), CalculationModel.SUCCESS, "The run must finish normally.")
        job.refresh_from_db()
        self.assertFalse(job.enabled, "Switching the job off during its run must stick.")
        self.assertIsNone(job.next_run_at, "A job switched off must keep having no next slot.")
        self.assertEqual(ScheduledTargetCalc.calls, 1, "The edit must not have started a second run.")
