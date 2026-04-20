"""
Cluster 7c: Hierarchy (parent → child, parent → mid → grandchild).

Intent (from docs/features/calculations/):

    A parent calc whose ``calculate()`` kicks off a child calc must
    honour the child's outcome. If the child ends in ERROR, the
    parent's own state transitions to ERROR too. If everything
    succeeds, everything is SUCCESS.

Scenario numbering matches
docs/test-plan/test-clusters.md#7-calculation-state-machine.
"""

from __future__ import annotations

import unittest

from lex.core.models.CalculationModel import CalculationModel

from lex.tests.e2e._e2e_test_case import E2ETestCase

from .models import ALL_MODELS, ChildCalc, GrandchildCalc, MidCalc, ParentCalc


class TestCluster07c_Hierarchy(E2ETestCase):
    """Parent/child calculation propagation."""

    e2e_models = ALL_MODELS

    # -- 7.5 -----------------------------------------------------------
    def test_7_5_parent_and_child_both_succeed(self) -> None:
        """Scenario 7.5: Parent success, child success → both SUCCESS."""
        parent = ParentCalc(name="p7-5", child_should_fail=False)
        parent.is_calculated = CalculationModel.IN_PROGRESS
        parent.save()

        parent.refresh_from_db()
        self.assertEqual(
            parent.is_calculated, CalculationModel.SUCCESS,
            f"Parent must end SUCCESS; got {parent.is_calculated!r}",
        )
        child = ChildCalc.objects.get(name="p7-5-child")
        self.assertEqual(
            child.is_calculated, CalculationModel.SUCCESS,
            f"Child must end SUCCESS; got {child.is_calculated!r}",
        )

    # -- 7.6 -----------------------------------------------------------
    def test_7_6_parent_success_child_fails_propagates(self) -> None:
        """Scenario 7.6: Parent success path, child fails → both ERROR."""
        parent = ParentCalc(name="p7-6", child_should_fail=True)
        parent.is_calculated = CalculationModel.IN_PROGRESS
        try:
            parent.save()
        except Exception:
            pass

        parent.refresh_from_db()
        child = ChildCalc.objects.get(name="p7-6-child")

        self.assertEqual(
            child.is_calculated, CalculationModel.ERROR,
            "Child must end ERROR when its own calculate() raises",
        )
        self.assertEqual(
            parent.is_calculated, CalculationModel.ERROR,
            "Parent must end ERROR when the child it spawned failed "
            "(error propagation)",
        )

    # -- 7.7 -----------------------------------------------------------
    @unittest.skip(
        "Scenario 7.7: Non-atomic parent + atomic child that fails. "
        "Needs a dedicated non-atomic ParentCalc variant with the same "
        "child-spawning logic; add once Cluster 7c surfaces a need to "
        "test non-atomic propagation distinct from atomic."
    )
    def test_7_7_non_atomic_parent_atomic_child_fails(self) -> None:
        """Scenario 7.7: Non-atomic parent, atomic child fails → both trail to ERROR."""

    # -- 7.8 -----------------------------------------------------------
    def test_7_8_three_level_hierarchy_grandchild_fails(self) -> None:
        """Scenario 7.8: 3-level, grandchild fails → error propagates up all levels."""
        mid = MidCalc(name="m7-8", grandchild_should_fail=True)
        mid.is_calculated = CalculationModel.IN_PROGRESS
        try:
            mid.save()
        except Exception:
            pass

        mid.refresh_from_db()
        gc = GrandchildCalc.objects.get(name="m7-8-gc")

        self.assertEqual(
            gc.is_calculated, CalculationModel.ERROR,
            "Grandchild must end ERROR",
        )
        self.assertEqual(
            mid.is_calculated, CalculationModel.ERROR,
            "Mid must end ERROR (propagation from grandchild)",
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()

