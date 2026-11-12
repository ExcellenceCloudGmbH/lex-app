"""Every scheduled run leaves the same audit trail as a run someone started by hand.

Intent: the compliance trail must answer "who ran what, when, and how did it end"
for scheduled work too. A run started by the scheduler opens an audit entry of
the same shape a Calculate click opens — an 'update' of the job, under the run's
own calculation id — authored by the person the schedule runs as (or
"Scheduler" for a schedule declared in code), saying it was started by the
schedule; and the framework closes it as success or failure like any other run.
A regression here makes nightly jobs invisible in the audit log, attributes them
to nobody, or leaves their entries pending forever.
Cluster 6v — scenarios 6.173–6.175. Type: E.
Covers: lex/scheduling/dispatch.py (``fire``, ``_open_run_audit``,
``scheduled_run_request``), lex/audit_logging/utils/calculation_audit.py
(the opener's ``trigger`` survives the terminal snapshot).
Run: python -m lex pytest lex/test_project/tests/audit_logging/test_6v_scheduled_run_audit.py -v
"""

from __future__ import annotations

from datetime import datetime, timezone as dt_timezone

import pytest

from lex.audit_logging.models.AuditLog import AuditLog
from lex.audit_logging.models.AuditLogStatus import AuditLogStatus
from lex.core.models.CalculationModel import CalculationModel
from lex.scheduling.dispatch import run_due_jobs
from lex.scheduling.models import ScheduledJob
from lex.test_project.tests._scheduling_support import ScheduledJobTestCase

pytestmark = pytest.mark.audit_logging

NOW = datetime(2030, 1, 15, 6, 0, 5, tzinfo=dt_timezone.utc)


class TestCluster06v_ScheduledRunAudit(ScheduledJobTestCase):
    """Cluster 6u: the audit entry of a scheduled run."""

    # Observe the real finalisation instead of the default no-op mock.
    e2e_unpatch = {"ensure_terminal_calculation_audit"}

    def _fire(self, job: ScheduledJob) -> ScheduledJob:
        self.make_due(job, NOW)
        report = run_due_jobs(NOW)
        self.assertEqual(report.fired, [job.name], "The job must fire.")
        self.wait_for(report)
        self.wait_settled(job)
        return job

    def _entry(self, job: ScheduledJob) -> AuditLog:
        entries = AuditLog.objects.filter(resource="scheduledjob", object_id=job.pk)
        self.assertEqual(entries.count(), 1, f"One audit entry per run expected, got {list(entries.values())!r}.")
        return entries.get()

    # -- 6.173 ---------------------------------------------------------
    def test_6_173_a_scheduled_run_is_audited_as_its_run_as_user(self):
        """
        Scenario 6.173: the run is on record, under the person it runs as
        Given: a schedule saved by a person (it runs as them)
        When: it fires and its run succeeds
        Then: one 'update' audit entry for the job exists, authored by that person,
              marked as started by the schedule (which the final snapshot keeps),
              under a click-shaped calculation id, and finalised as success
        """
        target = self.make_target()
        job = self.make_job("audited", target_record_id=str(target.pk))
        ScheduledJob.objects.filter(pk=job.pk).update(run_as=self.user)

        self._fire(job)

        entry = self._entry(job)
        self.assertEqual(entry.action, "update", "A run is audited as an update of the job, as a click's run is.")
        self.assertEqual(entry.author, str(self.user), "The entry must name the person the schedule runs as.")
        trigger = entry.payload.get("trigger") or {}
        self.assertEqual(trigger.get("by"), "schedule", "The entry must say the schedule started the run.")
        self.assertEqual(trigger.get("job"), "audited", "The entry must name the schedule.")
        self.assertEqual(
            entry.payload.get("is_calculated"),
            CalculationModel.SUCCESS,
            "The entry must still carry the run's final snapshot of the job.",
        )
        self.assertTrue(
            entry.calculation_id.startswith(f"scheduledjob_{job.pk}_update_"),
            f"The run must have a click-shaped calculation id, got {entry.calculation_id!r}.",
        )
        status = AuditLogStatus.objects.get(audit_log=entry)
        self.assertEqual(status.status, "success", "A successful run must be finalised as success.")

    # -- 6.174 ---------------------------------------------------------
    def test_6_174_a_failed_scheduled_run_is_finalised_as_failure(self):
        """
        Scenario 6.174: a failure is on record too
        Given: a schedule whose target fails
        When: it fires
        Then: its audit entry is finalised as failure, with the traceback
        """
        target = self.make_target(should_fail=True)
        job = self._fire(self.make_job("audited-failure", target_record_id=str(target.pk)))

        job.refresh_from_db()
        self.assertEqual(job.is_calculated, CalculationModel.ERROR, "The run must have failed.")
        status = AuditLogStatus.objects.get(audit_log=self._entry(job))
        self.assertEqual(status.status, "failure", "A failed run must be finalised as failure.")
        self.assertIn("failing on purpose", status.error_traceback or "", "The failure must carry its traceback.")

    # -- 6.175 ---------------------------------------------------------
    def test_6_175_a_schedule_declared_in_code_runs_as_the_scheduler(self):
        """
        Scenario 6.175: a run nobody saved is still attributed
        Given: a schedule with no run-as user (one declared in code)
        When: it fires
        Then: its audit entry is authored by "Scheduler", never left blank
        """
        job = self._fire(
            self.make_job(
                "code-job",
                target_type=ScheduledJob.TARGET_FUNCTION,
                function_name="tests.record_call",
                managed_by_code=True,
            )
        )

        self.assertEqual(self._entry(job).author, "Scheduler", "A run without a run-as user is the scheduler's.")
