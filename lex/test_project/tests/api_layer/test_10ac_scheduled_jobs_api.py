"""Scheduled jobs are managed through the same REST API, and the same UI, as any model.

Intent: a person creates, edits, switches off and runs schedules in the generic
app, with no frontend work: ``ScheduledJob`` is registered like any model, it is
in the sidebar next to the audit and calculation logs, and its ▶ button runs the
job now. Saving a schedule through the API is where its definition is checked:
every mistake answers 400 naming the field, so the form shows it next to the
input and nothing half-valid is stored; the save records who the job runs as;
and a change to the schedule moves its next slot. A schedule declared in code can
be switched off here but not rewritten. A regression here stores schedules that
can never run, runs a job as someone who never saved it, or lets the UI and the
code fight over a declared schedule.
Cluster 10ac — scenarios 10.175–10.183. Type: E.
Covers: lex/scheduling/models.py (definition validation, ``next_run_at``,
``run_as``), lex/api/views/model_entries/One.py (create / update / calculate
through the shared start path), lex/utilities/config/generic_app_config.py
(sidebar structure), lex/core/models/CalculationModel.py
(``reset_calculation_on_edit``).
Run: python -m lex pytest lex/test_project/tests/api_layer/test_10ac_scheduled_jobs_api.py -v
"""

from __future__ import annotations

import json
import threading
from datetime import timedelta

import pytest
from django.contrib.auth.models import User
from django.urls import reverse
from django.utils import timezone
from rest_framework import status

from lex.core.models.CalculationModel import CalculationModel
from lex.lex_app.tests.auth import create_sign_in
from lex.scheduling.dispatch import run_due_jobs
from lex.scheduling.models import ScheduledJob
from lex.test_project.tests._scheduling_support import TARGET_LABEL, ScheduledJobTestCase
from lex.test_project.tests.calculations.models import (
    SCHEDULE_TARGET_MODELS,
    KeepsStatusOnEditCalc,
    ScheduledTargetCalc,
)

pytestmark = pytest.mark.api_layer

JOB = "scheduledjob"


class TestCluster10ac_ScheduledJobsApi(ScheduledJobTestCase):
    """Cluster 10ac: the REST surface of ``ScheduledJob``."""

    e2e_models = SCHEDULE_TARGET_MODELS + [KeepsStatusOnEditCalc]

    def setUp(self):
        super().setUp()
        self._allowed = self.scheduled_jobs_allowed()
        self._allowed.__enter__()
        self.target = self.make_target()

    def tearDown(self):
        self._allowed.__exit__(None, None, None)
        super().tearDown()

    def _payload(self, **overrides):
        payload = {
            "name": "nightly",
            "cron": "0 6 * * *",
            "timezone": "UTC",
            "target_type": ScheduledJob.TARGET_CALCULATION,
            "target_model": TARGET_LABEL,
            "target_record_id": str(self.target.pk),
        }
        payload.update(overrides)
        return payload

    def _create(self, **overrides):
        return self.client.post(self.url_create(JOB), data=self._payload(**overrides), format="json")

    def _patch(self, job, **data):
        return self.client.patch(self.url_detail(JOB, job.pk), data=data, format="json")

    # -- 10.175 --------------------------------------------------------
    def test_10_175_creating_a_schedule_sets_its_next_slot_and_who_it_runs_as(self):
        """
        Scenario 10.175: a schedule created in the app is ready to run
        Given: a person allowed to manage schedules
        When: they create a daily 06:00 UTC schedule for a record through the API
        Then: the API answers 201; the stored schedule's next slot is the next 06:00
              UTC, and it runs as the person who created it
        """
        response = self._create()

        self.assertEqual(response.status_code, status.HTTP_201_CREATED, f"Create must succeed: {response.data!r}")
        job = ScheduledJob.objects.get(name="nightly")
        now = timezone.now()
        expected = now.replace(hour=6, minute=0, second=0, microsecond=0)
        if expected <= now:
            expected += timedelta(days=1)
        self.assertEqual(job.next_run_at, expected, "The next slot must be the next 06:00 UTC.")
        self.assertEqual(job.run_as, self.user, "A schedule must run as the person who saved it.")
        self.assertTrue(job.enabled, "A new schedule is on.")

    # -- 10.176 --------------------------------------------------------
    def test_10_176_every_definition_mistake_answers_400_naming_the_field(self):
        """
        Scenario 10.176: nothing half-valid is stored
        Given: create requests that each get one thing wrong
        When: they are sent
        Then: each answers 400 with the offending field and a reason, and no schedule
              is stored
        """
        cases = {
            "bad cron": ({"cron": "0 25 * * *"}, "cron", "out of range"),
            "seconds field": ({"cron": "0 0 6 * * *"}, "cron", "Six fields"),
            "no schedule": ({"cron": ""}, "cron", "Give a cron expression"),
            "both schedules": (
                {"run_at": (timezone.now() + timedelta(days=1)).isoformat()},
                "cron",
                "not both",
            ),
            "past run_at": ({"cron": "", "run_at": "2020-01-01T00:00:00Z"}, "run_at", "future"),
            "time zone": ({"timezone": "Mars/Olympus"}, "timezone", "not a known time zone"),
            "unknown model": ({"target_model": "lex_app.NoSuchModel"}, "target_model", "There is no model"),
            "not a calculation": ({"target_model": "auth.User"}, "target_model", "not a calculation model"),
            "itself": ({"target_model": "scheduling.ScheduledJob"}, "target_model", "cannot run another schedule"),
            "missing record": ({"target_record_id": "987654"}, "target_record_id", "There is no"),
            "unknown function": (
                {"target_type": ScheduledJob.TARGET_FUNCTION, "function_name": "no.such.job"},
                "function_name",
                "No function is registered",
            ),
        }
        for label, (overrides, field, reason) in cases.items():
            with self.subTest(case=label):
                response = self._create(name=f"bad-{label}", **overrides)
                self.assertEqual(
                    response.status_code,
                    status.HTTP_400_BAD_REQUEST,
                    f"{label}: must answer 400, got {response.status_code} {getattr(response, 'data', None)!r}",
                )
                self.assertIn(field, response.data, f"{label}: the 400 must name {field!r}: {response.data!r}")
                self.assertIn(reason, json.dumps(response.data), f"{label}: the 400 must say why.")
        self.assertFalse(ScheduledJob.objects.exists(), "No invalid schedule may be stored.")

    # -- 10.177 --------------------------------------------------------
    def test_10_177_editing_the_schedule_moves_its_next_slot_and_its_run_as(self):
        """
        Scenario 10.177: an edit takes effect from the next slot
        Given: a daily 06:00 schedule created by one person
        When: a second person changes it to 18:30, then switches it off, then on again
        Then: the next slot follows each change (none while off), and the schedule now
              runs as the second person, who saved it last
        """
        self.assertEqual(self._create().status_code, status.HTTP_201_CREATED)
        job = ScheduledJob.objects.get(name="nightly")

        other = User.objects.create_user(username="second_editor", password="pw")
        # lex-app 2.5: a sign-in is a server-side OIDCSession, as for the first editor (E2ETestCase).
        create_sign_in(self.client, other)

        response = self._patch(job, cron="30 18 * * *")
        self.assertEqual(response.status_code, status.HTTP_200_OK, f"Edit must succeed: {response.data!r}")
        job.refresh_from_db()
        self.assertEqual(
            (job.next_run_at.astimezone(timezone.get_fixed_timezone(0)).hour, job.next_run_at.minute),
            (18, 30),
            "The next slot must follow the new expression.",
        )
        self.assertEqual(job.run_as, other, "The schedule must now run as the person who saved it last.")

        self._patch(job, enabled=False)
        job.refresh_from_db()
        self.assertIsNone(job.next_run_at, "A schedule switched off has no next slot.")

        self._patch(job, enabled=True)
        job.refresh_from_db()
        self.assertIsNotNone(job.next_run_at, "Switched on again, it must be scheduled again.")
        self.assertGreater(job.next_run_at, timezone.now(), "…from now on, not replaying what it missed.")

    # -- 10.178 --------------------------------------------------------
    def test_10_178_the_calculate_button_runs_the_job_now(self):
        """
        Scenario 10.178: ▶ means "run now"
        Given: a schedule whose next slot is tomorrow
        When: its Calculate button is pressed (PATCH ``calculate=true``)
        Then: the target is calculated now, the job settles in SUCCESS, and its next
              slot is unchanged — running it now does not use up a slot
        """
        self.assertEqual(self._create().status_code, status.HTTP_201_CREATED)
        job = ScheduledJob.objects.get(name="nightly")
        slot = job.next_run_at

        response = self._patch(job, calculate="true", is_calculated=True)

        self.assertIn(
            response.status_code,
            (status.HTTP_200_OK, status.HTTP_202_ACCEPTED),
            f"Run now must be accepted: {getattr(response, 'data', None)!r}",
        )
        self.assertEqual(self.wait_settled(job), CalculationModel.SUCCESS, "The job's run must succeed.")
        self.target.refresh_from_db()
        self.assertEqual(self.target.is_calculated, CalculationModel.SUCCESS, "The target must be calculated.")
        self.assertEqual(ScheduledTargetCalc.calls, 1, "Run now must run the target once.")
        job.refresh_from_db()
        self.assertEqual(job.next_run_at, slot, "Run now must not move the next slot.")

    # -- 10.179 --------------------------------------------------------
    def test_10_179_a_schedule_declared_in_code_can_be_switched_off_but_not_rewritten(self):
        """
        Scenario 10.179: the code owns a declared schedule
        Given: a schedule declared in code with @lex_job(schedule=...)
        When: someone changes its cron expression through the API, then switches it off
        Then: the cron change answers 400 pointing at the code; switching it off works
        """
        job = ScheduledJob.objects.create(
            name="tests.record_call",
            cron="0 3 * * *",
            timezone="UTC",
            target_type=ScheduledJob.TARGET_FUNCTION,
            function_name="tests.record_call",
            managed_by_code=True,
        )

        refused = self._patch(job, cron="0 4 * * *")
        self.assertEqual(refused.status_code, status.HTTP_400_BAD_REQUEST, "Rewriting a declared schedule must fail.")
        self.assertIn("@lex_job", json.dumps(refused.data), "The refusal must point at the code.")
        job.refresh_from_db()
        self.assertEqual(job.cron, "0 3 * * *", "The declared schedule must be unchanged.")

        allowed = self._patch(job, enabled=False)
        self.assertEqual(allowed.status_code, status.HTTP_200_OK, f"Switching it off must work: {allowed.data!r}")
        job.refresh_from_db()
        self.assertFalse(job.enabled, "The declared schedule must now be off.")

    # -- 10.180 --------------------------------------------------------
    def test_10_180_a_bare_model_name_is_stored_as_its_label(self):
        """
        Scenario 10.180: people may type the model's name
        Given: a create request naming the target model without its app label
        When: it is sent
        Then: it is accepted and stored as 'app_label.ModelName'
        """
        response = self._create(target_model="scheduledtargetcalc")

        self.assertEqual(response.status_code, status.HTTP_201_CREATED, f"Create must succeed: {response.data!r}")
        self.assertEqual(
            ScheduledJob.objects.get(name="nightly").target_model,
            TARGET_LABEL,
            "A bare model name must be stored as its label.",
        )

    # -- 10.181 --------------------------------------------------------
    def test_10_181_scheduled_jobs_are_in_the_sidebar(self):
        """
        Scenario 10.181: the app shows schedules without frontend work
        Given: the framework's model structure
        When: the frontend loads it (GET api/model-structure)
        Then: it lists the scheduled-job model, so the generic UI gets its pages
        """
        response = self.client.get(reverse("process_admin_rest_api:model-structure"))

        self.assertEqual(response.status_code, status.HTTP_200_OK, "The model structure must load.")
        self.assertIn('"scheduledjob"', json.dumps(response.data), "The sidebar must list scheduled jobs.")

    # -- 10.182 --------------------------------------------------------
    def test_10_182_an_edit_during_a_run_keeps_the_run_and_its_overlap_guard(self):
        """
        Scenario 10.182: editing a running schedule in the app does not hide the run
        Given: a schedule whose run is in progress (its target is held open)
        When: someone changes its cron expression in the app, and a slot then comes due
        Then: the edit is accepted and moves the next slot; the job is still shown as
              running, so the due slot is skipped instead of starting a second run;
              when the run ends the job settles in SUCCESS and the target ran once
        """
        self.assertEqual(self._create(cron="*/5 * * * *").status_code, status.HTTP_201_CREATED)
        job = ScheduledJob.objects.get(name="nightly")
        ScheduledTargetCalc.gate = threading.Event()
        started = run_due_jobs(job.next_run_at)
        self.assertEqual(started.fired, ["nightly"], "The job must start.")

        response = self._patch(job, cron="*/10 * * * *")
        self.assertEqual(response.status_code, status.HTTP_200_OK, f"The edit must be accepted: {response.data!r}")
        job.refresh_from_db()
        self.assertEqual(job.cron, "*/10 * * * *", "The edit must be stored.")
        self.assertEqual(
            job.is_calculated, CalculationModel.IN_PROGRESS, "The edit must not reset the running job's status."
        )

        overlapping = run_due_jobs(job.next_run_at)
        self.assertEqual(overlapping.skipped, ["nightly"], "A slot due mid-run must be skipped, not stacked.")

        ScheduledTargetCalc.gate.set()
        self.wait_for(started)
        self.assertEqual(self.wait_settled(job), CalculationModel.SUCCESS, "The run must finish normally.")
        self.assertEqual(ScheduledTargetCalc.calls, 1, "The target must have run once.")

    # -- 10.183 --------------------------------------------------------
    def test_10_183_a_calculation_model_can_keep_its_status_through_edits(self):
        """
        Scenario 10.183: ``reset_calculation_on_edit = False`` is honoured by the app
        Given: a calculated record of a model that opts out of the edit reset, and a
               calculated record of an ordinary model
        When: each is edited through the API
        Then: the opted-out record keeps SUCCESS; the ordinary one is reset to
              NOT_CALCULATED, as before
        """
        keeps = KeepsStatusOnEditCalc.objects.create(name="keeps")
        resets = self.make_target("resets")
        KeepsStatusOnEditCalc.objects.filter(pk=keeps.pk).update(is_calculated=CalculationModel.SUCCESS)
        ScheduledTargetCalc.objects.filter(pk=resets.pk).update(is_calculated=CalculationModel.SUCCESS)

        kept = self.client.patch(self.url_detail("keepsstatusoneditcalc", keeps.pk), data={"name": "x"}, format="json")
        reset = self.client.patch(self.url_detail("scheduledtargetcalc", resets.pk), data={"name": "y"}, format="json")

        self.assertEqual(kept.status_code, status.HTTP_200_OK, f"The edit must succeed: {kept.data!r}")
        self.assertEqual(reset.status_code, status.HTTP_200_OK, f"The edit must succeed: {reset.data!r}")
        keeps.refresh_from_db()
        resets.refresh_from_db()
        self.assertEqual(keeps.is_calculated, CalculationModel.SUCCESS, "The opted-out record must keep its status.")
        self.assertEqual(kept.data.get("is_calculated"), CalculationModel.SUCCESS, "…and the answer must say so.")
        self.assertEqual(
            resets.is_calculated, CalculationModel.NOT_CALCULATED, "An ordinary record must still be reset."
        )
