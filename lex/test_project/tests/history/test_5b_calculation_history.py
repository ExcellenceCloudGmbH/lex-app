"""
Cluster 5b: Calculation history trail.

Intent (from docs/features/calculations/ + docs/features/history/):

    A calculation save moves ``is_calculated`` through
    ``NOT_CALCULATED → IN_PROGRESS → SUCCESS|ERROR``. Each transition
    is a distinct save, and therefore each transition produces its own
    history row. A failed *atomic* calculation must NOT erase the
    IN_PROGRESS history row — the audit trail of what was attempted is
    a customer-visible contract.

Scenario numbering matches
docs/test-plan/test-clusters.md#5-history--bitemporal.

Note:
    Scenarios 5.6 / 5.7 / 5.8 depend on the full calculation pipeline
    (calculate_hook, operation_context, cache manager). E2ETestCase
    already patches the async-y boundaries (WebSocket, cache,
    ActiveCalculationStateStore, ensure_terminal_calculation_audit),
    which is the documented customer-observable setup.
"""

from __future__ import annotations

import unittest

from lex.core.models.CalculationModel import CalculationModel

from lex.tests.e2e._e2e_test_case import E2ETestCase

from .models import ALL_MODELS, HistAtomicCalc


class TestCluster05b_CalculationHistory(E2ETestCase):
    """Calculation lifecycle produces a full history trail."""

    e2e_models = ALL_MODELS

    # -- 5.6 -----------------------------------------------------------
    def test_5_6_success_history_trail(self) -> None:
        """
        Scenario 5.6: Successful calculation → history contains
        IN_PROGRESS followed by SUCCESS.

        Asserts the *observable* trail: the history table records at
        least one IN_PROGRESS row and one SUCCESS row, in that order.
        """
        calc = HistAtomicCalc(name="c5-6", should_fail=False)
        calc.is_calculated = CalculationModel.IN_PROGRESS
        calc.save()

        states = [h.is_calculated for h in calc.history.order_by("history_id")]
        self.assertIn(
            CalculationModel.IN_PROGRESS, states,
            f"IN_PROGRESS must appear in history; got {states}",
        )
        self.assertIn(
            CalculationModel.SUCCESS, states,
            f"SUCCESS must appear in history; got {states}",
        )
        self.assertLess(
            states.index(CalculationModel.IN_PROGRESS),
            states.index(CalculationModel.SUCCESS),
            "IN_PROGRESS must appear before SUCCESS in history order",
        )

    # -- 5.7 -----------------------------------------------------------
    def test_5_7_failure_history_trail(self) -> None:
        """
        Scenario 5.7: Failing calculation → history contains
        IN_PROGRESS followed by ERROR.
        """
        calc = HistAtomicCalc(name="c5-7", should_fail=True)
        calc.is_calculated = CalculationModel.IN_PROGRESS
        try:
            calc.save()
        except Exception:
            pass  # calculate() raises on purpose; we assert on history

        states = [h.is_calculated for h in calc.history.order_by("history_id")]
        self.assertIn(
            CalculationModel.ERROR, states,
            f"ERROR must appear in history after failure; got {states}",
        )

    # -- 5.8 -----------------------------------------------------------
    def test_5_8_in_progress_history_survives_failed_atomic_calc(self) -> None:
        """
        Scenario 5.8: IN_PROGRESS row must survive a failed atomic calc.

        Originally tracked as BUG-001 ("atomic rollback erases
        IN_PROGRESS history"). Under the current framework this
        assertion passes — the IN_PROGRESS row is persisted before the
        calculate-hook's atomic block, so a rollback no longer wipes
        it. If regressed, both IN_PROGRESS and ERROR would be missing.
        """
        calc = HistAtomicCalc(name="c5-8", should_fail=True)
        calc.is_calculated = CalculationModel.IN_PROGRESS
        try:
            calc.save()
        except Exception:
            pass

        states = [h.is_calculated for h in calc.history.order_by("history_id")]
        # Both transitions must be recorded — a single IN_PROGRESS row
        # without a matching ERROR would mean the failure's audit is
        # lost; a single ERROR row without IN_PROGRESS is BUG-001.
        self.assertIn(
            CalculationModel.IN_PROGRESS, states,
            "BUG-001: IN_PROGRESS history row must survive a failed "
            "atomic calculation — customer needs the 'we tried' audit "
            f"record. Current states: {states}",
        )
        self.assertIn(
            CalculationModel.ERROR, states,
            f"Terminal ERROR row must also be recorded. States: {states}",
        )
        self.assertLess(
            states.index(CalculationModel.IN_PROGRESS),
            states.index(CalculationModel.ERROR),
            "IN_PROGRESS must precede ERROR in the history trail",
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()





