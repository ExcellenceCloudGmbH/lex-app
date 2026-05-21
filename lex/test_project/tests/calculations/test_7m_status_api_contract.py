"""
Cluster 7m: Calculation status API contract.

Intent:

    The frontend must receive canonical calculation status values from
    the API (`NOT_CALCULATED`, `IN_PROGRESS`, `SUCCESS`, `ERROR`,
    `ABORTED`) and never a humanized boolean label like ``"No"``.
"""

from __future__ import annotations

import unittest

from lex.core.models.CalculationModel import CalculationModel
from lex.tests.e2e._e2e_test_case import E2ETestCase
from rest_framework import status

from .models import ALL_MODELS, ATOMIC, FAILING, AtomicCalc, FailingCalc


class TestCluster07m_StatusApiContract(E2ETestCase):
    """REST serialization contract for ``is_calculated``."""

    e2e_models = ALL_MODELS

    def _detail_status(self, model_name: str, pk: int) -> str:
        resp = self.client.get(self.url_detail(model_name, pk))
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        return resp.data["is_calculated"]

    # -- 7.166 ---------------------------------------------------------
    def test_7_166_detail_never_serializes_is_calculated_as_no(self) -> None:
        """
        Scenario 7.166: API detail returns canonical status constants and
        never the UI-breaking string ``"No"``.
        """
        allowed = {
            CalculationModel.NOT_CALCULATED,
            CalculationModel.IN_PROGRESS,
            CalculationModel.SUCCESS,
            CalculationModel.ERROR,
            CalculationModel.ABORTED,
        }

        atomic = AtomicCalc.objects.create(name="s7-166-atomic", should_fail=False)
        not_calculated_status = self._detail_status(ATOMIC, atomic.pk)
        self.assertEqual(
            not_calculated_status,
            CalculationModel.NOT_CALCULATED,
            "Fresh calculation rows must serialize as NOT_CALCULATED.",
        )

        atomic.is_calculated = CalculationModel.IN_PROGRESS
        atomic.save()
        atomic.refresh_from_db()
        self.assertEqual(
            atomic.is_calculated,
            CalculationModel.SUCCESS,
            "Saving IN_PROGRESS on AtomicCalc should settle at SUCCESS in this fixture.",
        )
        success_status = self._detail_status(ATOMIC, atomic.pk)
        self.assertEqual(
            success_status,
            CalculationModel.SUCCESS,
            "Successful calculations must serialize as SUCCESS.",
        )

        failing = FailingCalc.objects.create(name="s7-166-failing")
        failing.is_calculated = CalculationModel.IN_PROGRESS
        try:
            failing.save()
        except Exception:
            pass
        failing.refresh_from_db()
        self.assertEqual(
            failing.is_calculated,
            CalculationModel.ERROR,
            "Saving IN_PROGRESS on FailingCalc should settle at ERROR in this fixture.",
        )
        error_status = self._detail_status(FAILING, failing.pk)
        self.assertEqual(
            error_status,
            CalculationModel.ERROR,
            "Failing calculations must serialize as ERROR.",
        )

        for observed in (
            not_calculated_status,
            success_status,
            error_status,
        ):
            self.assertIn(
                observed,
                allowed,
                f"is_calculated must stay within canonical states, got {observed!r}.",
            )
            self.assertNotEqual(
                observed,
                "No",
                'API must never serialize calculation state as literal "No".',
            )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
