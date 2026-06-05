"""Cluster 2i: PATCH-with-cancel REST endpoint for in-progress calculations.

Intent
------
The user-facing abort button issues ``PATCH /<model>/<pk>/?...`` with
``{"cancel": "true"}`` (mirroring the existing ``calculate=true``
trigger pattern) and expects:

* **202 Accepted** when the row was IN_PROGRESS and Celery-cancellable
  — with a report body listing the revoked task IDs.
* **409 Conflict** when the row is not in a cancellable state — either
  because it already terminated (SUCCESS / ERROR / CANCELLED) or because
  no Celery task is registered (sync-dispatched calc; nothing to
  revoke). The body still carries the report so the UI can show a
  precise reason.
* **No side effects on other fields** — cancel is a control-plane
  PATCH and must not silently apply other request-body keys to the row.

A regression in any of the above leaves the user with a button that
*looks* like it worked but didn't, or one that silently corrupts the
row alongside the cancel. Both are worse than no button.

Cluster 2i — scenarios 2.93–2.96. Type: E (APITestCase via
``E2ETestCase`` — drives the real DRF URL router so the per-pk view
binding, content negotiation, and PATCH branching are all exercised
end-to-end).
Covers: ``lex/api/views/model_entries/One.py`` — the new cancel
short-circuit in ``OneModelEntry.update``.
Run: python -m lex pytest lex/test_project/tests/crud_api/test_2i_cancel_endpoint.py -v
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from rest_framework import status

from lex.core.models.CalculationModel import CalculationModel
from lex.core.signals.ActiveCalculationStateStore import ActiveCalculationStateStore
from lex.test_project.tests._e2e_test_case import E2ETestCase

from ..calculations.models import ALL_MODELS, ATOMIC, AtomicCalc

pytestmark = pytest.mark.crud_api


def _record_id(instance) -> str:
    return f"{instance._meta.model_name}_{instance.pk}"


class TestCluster02i_CancelCalculationEndpoint(E2ETestCase):
    """Cluster 2i: PATCH ``cancel=true`` cancel-endpoint contract."""

    e2e_models = ALL_MODELS
    # ``E2ETestCase`` patches ``mark_in_progress`` to a no-op by default
    # (keeps unrelated happy-path tests deterministic). We need the real
    # implementation here so the state-store entry actually lands and
    # ``set_task_id`` can attach the Celery task handle the cancel
    # endpoint reads. Without this, 2.93 / 2.96 fall through to
    # ``sync_calculation_not_cancellable`` because ``cancel()`` sees no
    # registered task_id and returns HTTP 409. (Same fix as cluster 7n
    # in Session 68.)
    e2e_unpatch = {"mark_in_progress"}

    def setUp(self):
        super().setUp()
        ActiveCalculationStateStore.clear_all()
        self._revoke_patch = patch(
            "lex.core.models.CalculationModel.CalculationModel._revoke_celery_task"
        )
        self.revoke_mock = self._revoke_patch.start()

    def tearDown(self):
        self._revoke_patch.stop()
        ActiveCalculationStateStore.clear_all()
        super().tearDown()

    # ------------------------------------------------------------------
    # 2.93 — happy path: PATCH cancel=true on IN_PROGRESS Celery calc → 202
    # ------------------------------------------------------------------
    def test_02_93_patch_cancel_true_on_in_progress_celery_calc_returns_202(self):
        """
        Scenario 2.93: PATCH ``{"cancel":"true"}`` on a Celery-cancellable row.
        Given: an AtomicCalc at IN_PROGRESS with a registered task_id.
        When:  PATCH ../<pk>/?... with body {"cancel": "true"}.
        Then:  HTTP 202; body reports cancelled=True with the revoked
               task_id; row in DB persists is_calculated=CANCELLED.
        """
        calc = AtomicCalc.objects.create(name="cancel-via-rest")
        calc.is_calculated = CalculationModel.IN_PROGRESS
        calc.save(skip_hooks=True)
        ActiveCalculationStateStore.mark_in_progress(
            record_id=_record_id(calc),
            calculation_id="calc-rest-1",
            record=str(calc),
            model_label=calc._meta.label_lower,
            record_pk=calc.pk,
        )
        ActiveCalculationStateStore.set_task_id(_record_id(calc), "task-rest-1")

        response = self.client.patch(
            self.url_detail(ATOMIC, calc.pk),
            data={"cancel": "true"},
            format="json",
        )

        self.assertEqual(
            response.status_code,
            status.HTTP_202_ACCEPTED,
            msg=f"expected 202 on successful cancel, got {response.status_code}: {response.data!r}",
        )
        self.assertTrue(response.data.get("cancelled"))
        self.assertEqual(response.data.get("status"), CalculationModel.CANCELLED)
        self.assertEqual(response.data.get("revoked_tasks"), ["task-rest-1"])
        self.revoke_mock.assert_called_once_with("task-rest-1")
        calc.refresh_from_db()
        self.assertEqual(calc.is_calculated, CalculationModel.CANCELLED)

    # ------------------------------------------------------------------
    # 2.94 — PATCH cancel=true on terminal state → 409
    # ------------------------------------------------------------------
    def test_02_94_patch_cancel_true_on_terminal_state_returns_409(self):
        """
        Scenario 2.94: PATCH cancel=true on a row that already finished.
        Given: a row at SUCCESS.
        When:  PATCH cancel=true.
        Then:  HTTP 409 with cancellable=False, reason=not_in_progress;
               no revoke fired; row unchanged.
        """
        calc = AtomicCalc.objects.create(name="already-done")
        calc.is_calculated = CalculationModel.SUCCESS
        calc.save(skip_hooks=True)

        response = self.client.patch(
            self.url_detail(ATOMIC, calc.pk),
            data={"cancel": "true"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assertFalse(response.data.get("cancelled"))
        self.assertFalse(response.data.get("cancellable"))
        self.assertEqual(response.data.get("reason"), "not_in_progress")
        self.revoke_mock.assert_not_called()
        calc.refresh_from_db()
        self.assertEqual(calc.is_calculated, CalculationModel.SUCCESS)

    # ------------------------------------------------------------------
    # 2.95 — PATCH cancel=true on sync calc (no task_id) → 409, clear reason
    # ------------------------------------------------------------------
    def test_02_95_patch_cancel_true_on_sync_calc_returns_409_with_reason(self):
        """
        Scenario 2.95: PATCH cancel=true on an IN_PROGRESS sync calc.
        Given: row at IN_PROGRESS but no task_id registered (sync path).
        When:  PATCH cancel=true.
        Then:  HTTP 409 with reason=sync_calculation_not_cancellable so
               the UI can show "this calculation is not cancellable"
               instead of "abort succeeded".
        """
        calc = AtomicCalc.objects.create(name="sync-running")
        calc.is_calculated = CalculationModel.IN_PROGRESS
        calc.save(skip_hooks=True)
        ActiveCalculationStateStore.mark_in_progress(
            record_id=_record_id(calc),
            calculation_id="calc-sync-rest",
            record=str(calc),
            model_label=calc._meta.label_lower,
            record_pk=calc.pk,
        )
        # No set_task_id — sync path leaves it empty.

        response = self.client.patch(
            self.url_detail(ATOMIC, calc.pk),
            data={"cancel": "true"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(response.data.get("reason"), "sync_calculation_not_cancellable")
        self.revoke_mock.assert_not_called()
        calc.refresh_from_db()
        self.assertEqual(
            calc.is_calculated,
            CalculationModel.IN_PROGRESS,
            msg="non-cancellable sync calc must remain IN_PROGRESS after a refused cancel",
        )

    # ------------------------------------------------------------------
    # 2.96 — cancel=true does NOT apply other fields from the request body
    # ------------------------------------------------------------------
    def test_02_96_cancel_does_not_apply_sibling_fields_from_body(self):
        """
        Scenario 2.96: cancel is control-plane only — no field writes.
        Given: a row at IN_PROGRESS with name='original' and task_id.
        When:  PATCH {"cancel": "true", "name": "should-not-stick"}.
        Then:  HTTP 202, row persists CANCELLED, ``name`` is still
               'original' — the cancel short-circuit returns before any
               serializer.save() runs.
        """
        calc = AtomicCalc.objects.create(name="original")
        calc.is_calculated = CalculationModel.IN_PROGRESS
        calc.save(skip_hooks=True)
        ActiveCalculationStateStore.mark_in_progress(
            record_id=_record_id(calc),
            calculation_id="calc-rest-noop",
            record=str(calc),
            model_label=calc._meta.label_lower,
            record_pk=calc.pk,
        )
        ActiveCalculationStateStore.set_task_id(_record_id(calc), "task-noop")

        response = self.client.patch(
            self.url_detail(ATOMIC, calc.pk),
            data={"cancel": "true", "name": "should-not-stick"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)
        calc.refresh_from_db()
        self.assertEqual(
            calc.name,
            "original",
            msg="cancel PATCH must not write sibling fields from the body",
        )
        self.assertEqual(calc.is_calculated, CalculationModel.CANCELLED)

