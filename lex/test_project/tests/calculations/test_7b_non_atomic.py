"""
Cluster 7b: Non-atomic calculation state machine.

Intent (from docs/features/calculations/):

    ``is_atomic = False`` on a :class:`CalculationModel` opts that model
    out of the atomic save wrapper. The state-machine contract is
    identical to the atomic case — success → SUCCESS, failure → ERROR —
    but intermediate state changes are *not* transactionally
    coupled to the final outcome.

Scenario numbering matches
docs/test-plan/test-clusters.md#7-calculation-state-machine.
"""

from __future__ import annotations

import unittest

from lex.core.models.CalculationModel import CalculationModel
from lex.tests.e2e._e2e_test_case import E2ETestCase

from .models import ALL_MODELS, NonAtomicCalc


class TestCluster07b_NonAtomic(E2ETestCase):
    """Non-atomic calculation state transitions."""

    e2e_models = ALL_MODELS

    # -- 7.3 -----------------------------------------------------------
    def test_7_3_successful_non_atomic_calculation(self) -> None:
        """Scenario 7.3: Non-atomic success → final state SUCCESS."""
        calc = NonAtomicCalc(name="n7-3", should_fail=False)
        calc.is_calculated = CalculationModel.IN_PROGRESS
        calc.save()

        fresh = NonAtomicCalc.objects.get(pk=calc.pk)
        self.assertEqual(
            fresh.is_calculated, CalculationModel.SUCCESS,
            f"Final state must be SUCCESS; got {fresh.is_calculated!r}",
        )

    # -- 7.4 -----------------------------------------------------------
    def test_7_4_failing_non_atomic_calculation(self) -> None:
        """Scenario 7.4: Non-atomic failure → final state ERROR."""
        calc = NonAtomicCalc(name="n7-4", should_fail=True)
        calc.is_calculated = CalculationModel.IN_PROGRESS
        try:
            calc.save()
        except Exception:
            pass

        fresh = NonAtomicCalc.objects.get(pk=calc.pk)
        self.assertEqual(
            fresh.is_calculated, CalculationModel.ERROR,
            f"Final state must be ERROR; got {fresh.is_calculated!r}",
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()

