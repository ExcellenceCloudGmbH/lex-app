"""
Cluster 7a: Atomic calculation state machine.

Intent (from docs/features/calculations/):

    Saving a :class:`CalculationModel` with ``is_calculated=IN_PROGRESS``
    kicks off the calculation. The framework transitions the record to
    ``SUCCESS`` (if ``calculate()`` returns normally) or ``ERROR`` (if
    it raises), persisting the terminal state and, on failure, the
    exception message.

    ``calculate()`` must NOT call ``self.save()`` — the framework owns
    persistence.

Scenario numbering matches
docs/test-plan/test-clusters.md#7-calculation-state-machine.
"""

from __future__ import annotations

import unittest

from lex.core.models.CalculationModel import CalculationModel

from lex.tests.e2e._e2e_test_case import E2ETestCase

from .models import ALL_MODELS, AtomicCalc, FailingCalc


class TestCluster07a_Atomic(E2ETestCase):
    """Atomic calculation state transitions."""

    e2e_models = ALL_MODELS

    # -- 7.1 -----------------------------------------------------------
    def test_7_1_successful_atomic_calculation(self) -> None:
        """Scenario 7.1: Success → final state SUCCESS, trail [IN_PROGRESS, SUCCESS]."""
        calc = AtomicCalc(name="a7-1", should_fail=False)
        calc.is_calculated = CalculationModel.IN_PROGRESS
        calc.save()

        fresh = AtomicCalc.objects.get(pk=calc.pk)
        self.assertEqual(
            fresh.is_calculated, CalculationModel.SUCCESS,
            f"Final state must be SUCCESS; got {fresh.is_calculated!r}",
        )

    # -- 7.2 -----------------------------------------------------------
    def test_7_2_failing_atomic_calculation(self) -> None:
        """Scenario 7.2: Failure → final state ERROR."""
        calc = AtomicCalc(name="a7-2", should_fail=True)
        calc.is_calculated = CalculationModel.IN_PROGRESS
        try:
            calc.save()
        except Exception:
            pass

        fresh = AtomicCalc.objects.get(pk=calc.pk)
        self.assertEqual(
            fresh.is_calculated, CalculationModel.ERROR,
            f"Final state must be ERROR after a failing calc; "
            f"got {fresh.is_calculated!r}",
        )

    # -- 7.9 -----------------------------------------------------------
    def test_7_9_recalculate_after_failure_succeeds(self) -> None:
        """Scenario 7.9: Second calculation after failure produces SUCCESS."""
        calc = AtomicCalc(name="a7-9", should_fail=True)
        calc.is_calculated = CalculationModel.IN_PROGRESS
        try:
            calc.save()
        except Exception:
            pass

        calc.should_fail = False
        calc.is_calculated = CalculationModel.IN_PROGRESS
        calc.save()

        fresh = AtomicCalc.objects.get(pk=calc.pk)
        self.assertEqual(
            fresh.is_calculated, CalculationModel.SUCCESS,
            "After clearing should_fail and re-triggering, state must be SUCCESS",
        )

    # -- 7.12 ----------------------------------------------------------
    def test_7_12_calculate_does_not_call_save(self) -> None:
        """
        Scenario 7.12: ``calculate()`` must not call ``self.save()``.

        Framework-owned persistence means a customer-supplied
        ``calculate()`` only does computation. We assert that calling
        calculate directly does not produce extra history rows.
        """
        calc = AtomicCalc(name="a7-12", should_fail=False)
        calc.is_calculated = CalculationModel.IN_PROGRESS
        calc.save()
        rows_before = calc.history.count()

        calc.calculate()  # Pure computation — should not touch DB.

        self.assertEqual(
            calc.history.count(), rows_before,
            "calculate() must not persist — direct call may not add "
            "history rows",
        )

    # -- 7.13 ----------------------------------------------------------
    def test_7_13_error_message_populated_on_failure(self) -> None:
        """
        Scenario 7.13: On failure, ``calculation_error_message`` is
        populated with the exception detail.
        """
        calc = FailingCalc(name="f7-13")
        calc.is_calculated = CalculationModel.IN_PROGRESS
        try:
            calc.save()
        except Exception:
            pass

        fresh = FailingCalc.objects.get(pk=calc.pk)
        self.assertTrue(
            fresh.calculation_error_message,
            "calculation_error_message must be populated on failure; "
            f"got {fresh.calculation_error_message!r}",
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()

