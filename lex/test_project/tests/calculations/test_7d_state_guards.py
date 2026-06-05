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
from lex.test_project.tests._e2e_test_case import E2ETestCase

from .models import ALL_MODELS, FailingCalc

import pytest

pytestmark = pytest.mark.calculations


class TestCluster07d_StateGuards(E2ETestCase):
    """Idempotency and re-entrancy guards."""

    e2e_models = ALL_MODELS

    # -- 7.10 ----------------------------------------------------------
    def test_7_10_persist_error_state_is_idempotent(self) -> None:
        """
        Scenario 7.10: ``persist_error_state`` is idempotent — calling
        it twice for the same instance does not produce two separate
        ERROR saves.

        Intent (from the ``_has_persisted_terminal_state`` / terminal
        persistence registry): once an instance has been recorded as
        having settled at its terminal ERROR state, a subsequent call
        must be a no-op. Without this guard, retry paths (state
        machine → error callback → celery task result handler) would
        each re-save the row and duplicate history rows.

        We drive it directly through the public static method with a
        freshly-created instance in IN_PROGRESS, then call it a second
        time and verify the row was only updated once.
        """
        # Put a fresh calc in IN_PROGRESS with no calculate() running,
        # so we can trigger persist_error_state manually and observe
        # the save-count.
        calc = FailingCalc(name="pe7-10")
        calc.is_calculated = CalculationModel.IN_PROGRESS
        # skip_hooks=True keeps the hook pipeline out of the way so
        # we test persist_error_state in isolation.
        calc.save(skip_hooks=True)

        self.assertEqual(
            calc.is_calculated, CalculationModel.IN_PROGRESS,
            "Sanity: instance must be IN_PROGRESS before the first call.",
        )

        # First call: transitions IN_PROGRESS → ERROR.
        first = CalculationModel.persist_error_state([calc])
        self.assertEqual(
            len(first), 1,
            "First call must persist the ERROR state and return the "
            "instance.",
        )
        calc.refresh_from_db()
        self.assertEqual(
            calc.is_calculated, CalculationModel.ERROR,
            "After the first persist_error_state, the row must be at ERROR.",
        )
        history_count_after_first = calc.history.count()

        # Second call: must be a no-op — no extra save, no extra history row.
        second = CalculationModel.persist_error_state([calc])
        self.assertEqual(
            len(second), 1,
            "Second call still returns the instance (already-persisted "
            "fast-path), so callers can treat the result uniformly.",
        )
        calc.refresh_from_db()
        self.assertEqual(
            calc.is_calculated, CalculationModel.ERROR,
            "State must still be ERROR after the redundant call.",
        )
        self.assertEqual(
            calc.history.count(), history_count_after_first,
            "Idempotent contract: a second persist_error_state call "
            "must NOT produce an additional history row. "
            f"Expected {history_count_after_first}, got "
            f"{calc.history.count()}.",
        )

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

