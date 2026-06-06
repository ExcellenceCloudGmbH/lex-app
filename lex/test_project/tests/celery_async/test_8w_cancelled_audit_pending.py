"""Cluster 8w: cancelled Celery calc must not leave pending audit status.

Intent: when a calculation is triggered via the public API, audit status starts
as ``pending`` and must reach a terminal state. If the operator cancels the
Celery task, the terminal audit status must be ``cancelled`` (not stuck
``pending``), so compliance timelines reflect what happened.
Cluster 8w — scenarios 8.90–8.90. Type: E.
Covers: ``lex/core/models/CalculationModel.py``, ``lex/api/views/model_entries/One.py``.
Run: python -m lex pytest lex/test_project/tests/celery_async/test_8w_cancelled_audit_pending.py -v
"""

from __future__ import annotations

import time
import unittest
from unittest.mock import MagicMock, patch

import pytest

from lex.audit_logging.models.AuditLog import AuditLog
from lex.audit_logging.models.AuditLogStatus import AuditLogStatus
from lex.core.models.CalculationModel import CalculationModel
from lex.core.signals.ActiveCalculationStateStore import ActiveCalculationStateStore
from lex.test_project.tests._e2e_test_case import E2ETestCase

from .models import ALL_MODELS, CELERY_SYNC, CelerySyncCalc

pytestmark = pytest.mark.celery_async


class TestCluster08w_CancelledAuditStatus(E2ETestCase):
    """Cluster 8w: cancel path must finalize pending calculation audits."""

    e2e_models = ALL_MODELS
    e2e_framework_models = [AuditLog, AuditLogStatus]
    e2e_unpatch = {"mark_in_progress"}

    _POLL_TIMEOUT_S = 5.0
    _POLL_INTERVAL_S = 0.05

    def _record_id(self, instance) -> str:
        return f"{instance._meta.model_name}_{instance.pk}"

    def _wait_for_task_registration(self, instance) -> str:
        deadline = time.monotonic() + self._POLL_TIMEOUT_S
        record_id = self._record_id(instance)
        while time.monotonic() < deadline:
            entry = ActiveCalculationStateStore.get_entry(record_id)
            task_id = entry.get("task_id")
            if task_id:
                return task_id
            time.sleep(self._POLL_INTERVAL_S)
        self.fail(
            f"Expected ActiveCalculationStateStore.task_id for {record_id} "
            f"within {self._POLL_TIMEOUT_S:.1f}s so cancel() can revoke it."
        )

    def _wait_for_audit_statuses(self, calc) -> list[str]:
        deadline = time.monotonic() + self._POLL_TIMEOUT_S
        statuses: list[str] = []
        while time.monotonic() < deadline:
            rows = AuditLog.objects.filter(resource=CELERY_SYNC, object_id=calc.pk)
            statuses = list(
                AuditLogStatus.objects.filter(audit_log__in=rows).values_list(
                    "status", flat=True
                )
            )
            if statuses:
                return statuses
            time.sleep(self._POLL_INTERVAL_S)
        self.fail(
            f"No AuditLogStatus rows found for {CELERY_SYNC} pk={calc.pk} "
            f"within {self._POLL_TIMEOUT_S:.1f}s."
        )

    @unittest.expectedFailure  # BUG-023: cancelled calculation leaves audit status pending in celery cancel path
    def test_08_90_cancelled_calc_finalizes_pending_audit_to_cancelled(self):
        """
        Scenario 8.90 (BUG-023): API-triggered Celery cancellation finalizes
        audit status to ``cancelled`` (not ``pending``).

        Given: a calculation started via ``PATCH {"calculate":"true"}`` on
               a Celery-dispatched path.
        When:  the same record is cancelled via ``PATCH {"cancel":"true"}``.
        Then:  audit statuses for that record include ``cancelled`` and do not
               include lingering ``pending`` entries.
        """
        AuditLog.objects.all().delete()
        ActiveCalculationStateStore.clear_all()

        calc = CelerySyncCalc.objects.create(name="cancel-audit-pending")
        fake_task_result = MagicMock(id="task-8w-cancel")

        with patch.object(
            CalculationModel, "should_use_celery", return_value=True
        ), patch(
            "lex.lex_app.celery_tasks.calc_and_save.delay",
            return_value=fake_task_result,
        ), patch(
            "lex.core.models.CalculationModel.CalculationModel._revoke_celery_task"
        ):
            trigger_resp = self.client.patch(
                self.url_detail(CELERY_SYNC, calc.pk),
                data={"calculate": "true"},
                format="json",
            )
            self.assertEqual(
                trigger_resp.status_code,
                202,
                f"calculate=true should return 202 for async dispatch; got {trigger_resp.status_code}",
            )

            task_id = self._wait_for_task_registration(calc)
            self.assertEqual(task_id, "task-8w-cancel")

            cancel_resp = self.client.patch(
                self.url_detail(CELERY_SYNC, calc.pk),
                data={"cancel": "true"},
                format="json",
            )
            self.assertEqual(
                cancel_resp.status_code,
                202,
                f"cancel=true should return 202 for cancellable task; got {cancel_resp.status_code}",
            )

        calc.refresh_from_db()
        self.assertEqual(
            calc.is_calculated,
            CalculationModel.CANCELLED,
            f"cancel=true must persist CANCELLED; got {calc.is_calculated!r}",
        )

        statuses = self._wait_for_audit_statuses(calc)
        self.assertIn(
            "cancelled",
            statuses,
            f"Cancelled calculation must finalize an audit status to 'cancelled'; got {statuses!r}",
        )
        self.assertNotIn(
            "pending",
            statuses,
            f"Cancelled calculation must not leave pending audit statuses behind; got {statuses!r}",
        )
