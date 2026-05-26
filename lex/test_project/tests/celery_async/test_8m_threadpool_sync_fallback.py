"""
Cluster 8m: Sync-fallback threadpool contract for calculations.

The new threadpool approach moves HTTP-202 handoff to the API layer.
Once ``calculate_hook`` reaches synchronous fallback, calculation code must
execute on the caller thread (no nested re-submit into another pool).
"""

from __future__ import annotations

import threading
import unittest
from types import MethodType

from lex.core.models.CalculationModel import CalculationModel
from lex.tests.e2e._e2e_test_case import E2ETestCase

from .models import CelerySyncCalc


class TestCluster08m_ThreadpoolSyncFallback(E2ETestCase):
    """Scenario 8.72 — sync fallback stays on the caller thread."""

    e2e_models = [CelerySyncCalc]

    def test_8_72_sync_fallback_executes_on_the_request_thread(self) -> None:
        calc = CelerySyncCalc.objects.create(name="threadpool-8-72")
        caller_thread_id = threading.get_ident()
        observed_thread_ids: list[int] = []

        def track_calculate_thread(_self) -> None:
            observed_thread_ids.append(threading.get_ident())

        calc.calculate = MethodType(track_calculate_thread, calc)
        calc.is_calculated = CalculationModel.IN_PROGRESS
        calc.save()
        calc.refresh_from_db()

        self.assertEqual(
            calc.is_calculated,
            CalculationModel.SUCCESS,
            "Synchronous fallback must still complete with SUCCESS when calculate() succeeds.",
        )
        self.assertEqual(
            observed_thread_ids,
            [caller_thread_id],
            "Synchronous fallback must execute on the same thread that triggered save().",
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
