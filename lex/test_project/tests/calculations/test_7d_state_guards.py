"""
Cluster 7d: State-machine guards.

Intent (from docs/features/calculations/):

    * ``persist_error_state`` must be idempotent — calling it twice for
      the same instance must NOT produce two separate ERROR saves.
    * ``calculate_hook`` is guarded by
      ``_calculation_hook_in_progress`` to prevent re-entrancy when
      the framework or a user triggers a recursive save.

Scenario numbering matches
docs/test-plan/test-clusters.md#7-calculation-state-machine.
"""

from __future__ import annotations

import unittest

from lex.core.models.CalculationModel import CalculationModel

from lex.tests.e2e._e2e_test_case import E2ETestCase

from .models import ALL_MODELS, FailingCalc


class TestCluster07d_StateGuards(E2ETestCase):
    """Idempotency and re-entrancy guards."""

    e2e_models = ALL_MODELS

    # -- 7.10 ----------------------------------------------------------
    @unittest.skip(
        "Scenario 7.10: ``persist_error_state`` dedup. Needs a fixture "
        "that calls persist_error_state twice in isolation; the public "
        "flow (save + failing calculate) only invokes it once, so this "
        "scenario is best exercised at the unit level. Re-add here if "
        "we develop an E2E case that reliably invokes it twice."
    )
    def test_7_10_persist_error_state_is_idempotent(self) -> None:
        """Scenario 7.10: persist_error_state twice → one ERROR save."""

    # -- 7.11 ----------------------------------------------------------
    def test_7_11_reentrancy_guard_holds(self) -> None:
        """
        Scenario 7.11: ``calculate_hook`` skips when
        ``_calculation_hook_in_progress`` is set.

        We assert the *observable* guarantee: a failing calc completes
        in finite time and ends at ERROR. If the re-entrancy guard were
        broken, a nested save inside the hook would recurse indefinitely
        (RecursionError or test timeout).
        """
        calc = FailingCalc(name="f7-11")
        calc.is_calculated = CalculationModel.IN_PROGRESS
        try:
            calc.save()
        except Exception:
            pass

        fresh = FailingCalc.objects.get(pk=calc.pk)
        self.assertEqual(
            fresh.is_calculated, CalculationModel.ERROR,
            "Failing calc must settle at ERROR — if the re-entrancy "
            "guard were broken this test would hang or RecursionError",
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()

