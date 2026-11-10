"""Shared fixtures for the scheduled-job tests (clusters 1bk, 4z, 6v, 7x, 8f, 10ac).

``ScheduledJob`` is a framework model with its own migration, so its table is in
the test database already; the targets it calculates live in
``calculations/models.py`` and are created per class by :class:`E2ETestCase`.
"""

from __future__ import annotations

import time
from contextlib import contextmanager
from datetime import datetime
from unittest.mock import patch


from lex.core.models.CalculationModel import CalculationModel
from lex.core.models.LexModel import PermissionResult
from lex.scheduling.models import ScheduledJob
from lex.scheduling.registry import lex_job
from lex.test_project.tests._e2e_test_case import E2ETestCase
from lex.test_project.tests.calculations.models import (
    SCHEDULE_TARGET_MODELS,
    ScheduledTargetCalc,
)

SETTLE_TIMEOUT_S = 15.0
POLL_S = 0.05

TARGET_LABEL = ScheduledTargetCalc._meta.label  # "lex_app.ScheduledTargetCalc"

#: Calls of the registered test functions: (job name, kwargs).
CALLS: list = []


@lex_job("tests.record_call")
def record_call(**kwargs):
    """A registered function that remembers how it was called."""
    CALLS.append(("tests.record_call", dict(kwargs)))


@lex_job("tests.explode")
def explode(**kwargs):
    """A registered function that always fails."""
    CALLS.append(("tests.explode", dict(kwargs)))
    raise RuntimeError("tests.explode failing on purpose")


class ScheduledJobTestCase(E2ETestCase):
    """E2E base for tests that create schedules and drive the scheduler's tick."""

    e2e_models = SCHEDULE_TARGET_MODELS

    def setUp(self):
        super().setUp()
        CALLS.clear()
        ScheduledTargetCalc.gate = None
        ScheduledTargetCalc.calls = 0

    def tearDown(self):
        # Never leave a run parked on the gate past its test.
        if ScheduledTargetCalc.gate is not None:
            ScheduledTargetCalc.gate.set()
        ScheduledTargetCalc.gate = None
        super().tearDown()

    # ── builders ──────────────────────────────────────────────────────

    def make_target(self, name="target", **fields) -> ScheduledTargetCalc:
        return ScheduledTargetCalc.objects.create(name=name, **fields)

    def make_job(self, name="job", **fields) -> ScheduledJob:
        """A schedule created in code (no request), due-able by moving ``next_run_at``."""
        defaults = {"cron": "0 6 * * *", "timezone": "UTC"}
        if fields.get("target_type", ScheduledJob.TARGET_CALCULATION) == ScheduledJob.TARGET_CALCULATION:
            defaults["target_model"] = TARGET_LABEL
        if "run_at" in fields:
            defaults.pop("cron")
        defaults.update(fields)
        return ScheduledJob.objects.create(name=name, **defaults)

    def make_due(self, job: ScheduledJob, at: datetime) -> ScheduledJob:
        """Put a job's next slot at ``at`` the way the clock leaves it (a plain update)."""
        ScheduledJob.objects.filter(pk=job.pk).update(next_run_at=at)
        job.refresh_from_db()
        return job

    # ── waiting ───────────────────────────────────────────────────────

    def wait_settled(self, record, timeout=SETTLE_TIMEOUT_S) -> str:
        """The state ``record`` lands in once its run leaves IN_PROGRESS."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            record.refresh_from_db()
            if record.is_calculated not in (CalculationModel.IN_PROGRESS, CalculationModel.NOT_CALCULATED):
                return record.is_calculated
            time.sleep(POLL_S)
        self.fail(
            f"{type(record).__name__} pk={record.pk} never finished a run within {timeout:.0f}s; "
            f"last is_calculated={record.is_calculated!r}."
        )

    def wait_for(self, report):
        """Wait for every run a tick started."""
        for future in report.futures:
            future.result(timeout=SETTLE_TIMEOUT_S)

    @contextmanager
    def scheduled_jobs_allowed(self):
        """The test user has been granted access to schedules (an admin's Keycloak grant).

        ``ScheduledJob`` keeps the framework's default: Keycloak scopes on the
        ``scheduling.ScheduledJob`` resource. The test user has none, so this
        stands in for the grant.
        """
        allow = PermissionResult.allow_all("granted for the test")
        with patch.object(ScheduledJob, "permission_read", lambda self, uc: allow), patch.object(
            ScheduledJob, "permission_edit", lambda self, uc: allow
        ), patch.object(ScheduledJob, "permission_create", lambda self, uc: True), patch.object(
            ScheduledJob, "permission_delete", lambda self, uc: True
        ), patch.object(ScheduledJob, "permission_list", lambda self, uc: True):
            yield
