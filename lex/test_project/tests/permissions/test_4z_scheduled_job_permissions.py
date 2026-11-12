"""A schedule can never do more than the person who saved it may do.

Intent: Keycloak permissions only exist inside a request, so a scheduled run —
which has none — cannot be checked when it fires. The framework therefore checks
when the schedule is saved: saving a schedule that calculates a target answers
400 unless the person saving may calculate that target (the same checks the
Calculate button passes), for one record and for "every record" alike; and the
run then acts as that person. Schedules themselves keep the framework's default
permissions, so only people granted access to ``scheduling.ScheduledJob`` can
create them. Code (scripts, the ``@lex_job`` declarations) is trusted, as it is
everywhere else. A regression here lets anyone with schedule access calculate
records they could never calculate themselves.
Cluster 4z — scenarios 4.159–4.162. Type: E.
Covers: lex/scheduling/models.py (``_calculate_denial``, ``validate_definition``).
Run: python -m lex pytest lex/test_project/tests/permissions/test_4z_scheduled_job_permissions.py -v
"""

from __future__ import annotations

import json

import pytest
from rest_framework import status

from lex.scheduling.models import ScheduledJob
from lex.test_project.tests._scheduling_support import ScheduledJobTestCase
from lex.test_project.tests.calculations.models import ScheduledDeniedCalc

pytestmark = pytest.mark.permissions

DENIED_LABEL = ScheduledDeniedCalc._meta.label


class TestCluster04z_ScheduledJobPermissions(ScheduledJobTestCase):
    """Cluster 4s: who may schedule what."""

    def _create(self, **fields):
        payload = {
            "name": "sneaky",
            "cron": "0 6 * * *",
            "timezone": "UTC",
            "target_type": ScheduledJob.TARGET_CALCULATION,
            "target_model": DENIED_LABEL,
        }
        payload.update(fields)
        return self.client.post(self.url_create("scheduledjob"), data=payload, format="json")

    # -- 4.159 ---------------------------------------------------------
    def test_4_159_scheduling_a_record_the_caller_may_not_calculate_is_refused(self):
        """
        Scenario 4.159: one record
        Given: a person with access to schedules, and a record they may not edit
        When: they save a schedule that calculates that record
        Then: the API answers 400 on the target and stores nothing
        """
        record = ScheduledDeniedCalc.objects.create(name="locked")
        with self.scheduled_jobs_allowed():
            response = self._create(target_record_id=str(record.pk))

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST, f"Must be refused: {response.data!r}")
        self.assertIn("target_model", response.data, "The refusal must be on the target.")
        self.assertIn("may not calculate", json.dumps(response.data), "The refusal must say why.")
        self.assertFalse(ScheduledJob.objects.exists(), "No schedule may be stored.")

    # -- 4.160 ---------------------------------------------------------
    def test_4_160_scheduling_every_record_of_a_forbidden_model_is_refused(self):
        """
        Scenario 4.160: every record
        Given: a person with access to schedules, and a model whose records they may not edit
        When: they save a schedule that calculates every record of it
        Then: the API answers 400 on the target and stores nothing
        """
        with self.scheduled_jobs_allowed():
            response = self._create(name="sneaky-all")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST, f"Must be refused: {response.data!r}")
        self.assertIn("target_model", response.data, "The refusal must be on the target.")
        self.assertFalse(ScheduledJob.objects.exists(), "No schedule may be stored.")

    # -- 4.161 ---------------------------------------------------------
    def test_4_161_code_may_schedule_any_target(self):
        """
        Scenario 4.161: code is trusted
        Given: no request (a script, a data migration, an ``@lex_job`` declaration)
        When: code creates a schedule for the same forbidden model
        Then: it is stored and scheduled
        """
        job = self.make_job("from-code", target_model=DENIED_LABEL)

        self.assertIsNotNone(job.next_run_at, "A schedule created in code must be scheduled.")

    # -- 4.162 ---------------------------------------------------------
    def test_4_162_without_access_to_schedules_nobody_can_create_one(self):
        """
        Scenario 4.162: schedules keep the framework's default permissions
        Given: a person who was not granted access to ``scheduling.ScheduledJob``
        When: they try to create a schedule for a target they may calculate
        Then: the API refuses it and stores nothing
        """
        target = self.make_target()
        response = self._create(
            name="no-access",
            target_model="lex_app.ScheduledTargetCalc",
            target_record_id=str(target.pk),
        )

        self.assertIn(
            response.status_code,
            (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN),
            f"Creating a schedule without access must be refused, got {response.status_code}.",
        )
        self.assertFalse(ScheduledJob.objects.exists(), "No schedule may be stored.")
