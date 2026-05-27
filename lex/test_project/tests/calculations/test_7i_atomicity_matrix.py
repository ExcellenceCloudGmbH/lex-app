"""
Cluster 7i: Parent/child atomicity matrix.

The baseline hierarchy tests cover a few representative propagation
paths. This file exhaustively covers the two-dimensional atomicity
matrix customers can create in real projects:

* atomic parent -> atomic child
* atomic parent -> non-atomic child
* non-atomic parent -> atomic child
* non-atomic parent -> non-atomic child

For every parent/child atomicity pairing we assert all four outcome
combinations: both pass, child fails, parent fails after child passes,
and both fail. The important contracts are:

* child failures always propagate to the parent;
* non-atomic parent failures after a successful child leave that child at
  SUCCESS;
* atomic parent failures after a successful child roll back that child
  because it was created inside the parent's atomic transaction.

Scenario numbering extends Cluster 7 at 7.32-7.47.
"""

from __future__ import annotations

import unittest

from django.core.exceptions import ObjectDoesNotExist
from lex.core.models.CalculationModel import CalculationModel
from lex.tests.e2e._e2e_test_case import E2ETestCase

from .models import (
    ALL_MODELS,
    AtomicParentAtomicChildMatrixCalc,
    AtomicParentNonAtomicChildMatrixCalc,
    ChildCalc,
    NonAtomicChildCalc,
    NonAtomicParentAtomicChildMatrixCalc,
    NonAtomicParentNonAtomicChildMatrixCalc,
)

import pytest

pytestmark = pytest.mark.calculations


class TestCluster07i_AtomicityMatrix(E2ETestCase):
    """Exhaustive parent/child atomicity and failure propagation matrix."""

    e2e_models = ALL_MODELS

    def _run_matrix_case(
        self,
        *,
        parent_cls,
        child_cls,
        name: str,
        parent_should_fail: bool,
        child_should_fail: bool,
        expected_parent_state: str,
        expected_child_state: str | None,
    ) -> None:
        parent = parent_cls(
            name=name,
            parent_should_fail=parent_should_fail,
            child_should_fail=child_should_fail,
        )
        parent.is_calculated = CalculationModel.IN_PROGRESS
        try:
            parent.save()
        except Exception:
            # Failing calculations surface through the save path; the
            # contract under test is the persisted terminal state.
            pass

        parent.refresh_from_db()
        child = None
        try:
            child = child_cls.objects.get(name=f"{name}-child")
        except ObjectDoesNotExist:
            if expected_child_state is not None:
                raise

        self.assertEqual(
            parent.is_calculated,
            expected_parent_state,
            f"{parent_cls.__name__} parent final state drifted for "
            f"parent_should_fail={parent_should_fail}, "
            f"child_should_fail={child_should_fail}.",
        )
        if expected_child_state is None:
            self.assertIsNone(
                child,
                "Successful child should be rolled back when an atomic parent "
                "fails after creating it.",
            )
        else:
            self.assertIsNotNone(child, "Child must exist before state assertion.")
            self.assertEqual(
                child.is_calculated,
                expected_child_state,
                f"{child_cls.__name__} child final state drifted for "
                f"parent_should_fail={parent_should_fail}, "
                f"child_should_fail={child_should_fail}.",
            )

        if parent_should_fail:
            self.assertTrue(
                parent.calculation_error_message,
                "A parent that fails after the child must persist an error message.",
            )
        if child_should_fail:
            self.assertIsNotNone(child, "Failed child must persist ERROR details.")
            self.assertTrue(
                child.calculation_error_message,
                "A child that fails must persist its own error message.",
            )

    # -- atomic parent -> atomic child ---------------------------------

    def test_7_32_atomic_parent_atomic_child_both_pass(self) -> None:
        """Scenario 7.32: atomic parent + atomic child, both pass."""
        self._run_matrix_case(
            parent_cls=AtomicParentAtomicChildMatrixCalc,
            child_cls=ChildCalc,
            name="m7-32",
            parent_should_fail=False,
            child_should_fail=False,
            expected_parent_state=CalculationModel.SUCCESS,
            expected_child_state=CalculationModel.SUCCESS,
        )

    def test_7_33_atomic_parent_atomic_child_child_fails(self) -> None:
        """Scenario 7.33: atomic parent + atomic child, child fails."""
        self._run_matrix_case(
            parent_cls=AtomicParentAtomicChildMatrixCalc,
            child_cls=ChildCalc,
            name="m7-33",
            parent_should_fail=False,
            child_should_fail=True,
            expected_parent_state=CalculationModel.ERROR,
            expected_child_state=CalculationModel.ERROR,
        )

    def test_7_34_atomic_parent_atomic_child_parent_fails(self) -> None:
        """Scenario 7.34: atomic parent failure rolls back successful atomic child."""
        self._run_matrix_case(
            parent_cls=AtomicParentAtomicChildMatrixCalc,
            child_cls=ChildCalc,
            name="m7-34",
            parent_should_fail=True,
            child_should_fail=False,
            expected_parent_state=CalculationModel.ERROR,
            expected_child_state=None,
        )

    def test_7_35_atomic_parent_atomic_child_both_fail(self) -> None:
        """Scenario 7.35: atomic parent + atomic child, both configured to fail."""
        self._run_matrix_case(
            parent_cls=AtomicParentAtomicChildMatrixCalc,
            child_cls=ChildCalc,
            name="m7-35",
            parent_should_fail=True,
            child_should_fail=True,
            expected_parent_state=CalculationModel.ERROR,
            expected_child_state=CalculationModel.ERROR,
        )

    # -- atomic parent -> non-atomic child ------------------------------

    def test_7_36_atomic_parent_non_atomic_child_both_pass(self) -> None:
        """Scenario 7.36: atomic parent + non-atomic child, both pass."""
        self._run_matrix_case(
            parent_cls=AtomicParentNonAtomicChildMatrixCalc,
            child_cls=NonAtomicChildCalc,
            name="m7-36",
            parent_should_fail=False,
            child_should_fail=False,
            expected_parent_state=CalculationModel.SUCCESS,
            expected_child_state=CalculationModel.SUCCESS,
        )

    def test_7_37_atomic_parent_non_atomic_child_child_fails(self) -> None:
        """Scenario 7.37: atomic parent + non-atomic child, child fails."""
        self._run_matrix_case(
            parent_cls=AtomicParentNonAtomicChildMatrixCalc,
            child_cls=NonAtomicChildCalc,
            name="m7-37",
            parent_should_fail=False,
            child_should_fail=True,
            expected_parent_state=CalculationModel.ERROR,
            expected_child_state=CalculationModel.ERROR,
        )

    def test_7_38_atomic_parent_non_atomic_child_parent_fails(self) -> None:
        """Scenario 7.38: atomic parent failure rolls back successful non-atomic child."""
        self._run_matrix_case(
            parent_cls=AtomicParentNonAtomicChildMatrixCalc,
            child_cls=NonAtomicChildCalc,
            name="m7-38",
            parent_should_fail=True,
            child_should_fail=False,
            expected_parent_state=CalculationModel.ERROR,
            expected_child_state=None,
        )

    def test_7_39_atomic_parent_non_atomic_child_both_fail(self) -> None:
        """Scenario 7.39: atomic parent + non-atomic child, both fail."""
        self._run_matrix_case(
            parent_cls=AtomicParentNonAtomicChildMatrixCalc,
            child_cls=NonAtomicChildCalc,
            name="m7-39",
            parent_should_fail=True,
            child_should_fail=True,
            expected_parent_state=CalculationModel.ERROR,
            expected_child_state=CalculationModel.ERROR,
        )

    # -- non-atomic parent -> atomic child ------------------------------

    def test_7_40_non_atomic_parent_atomic_child_both_pass(self) -> None:
        """Scenario 7.40: non-atomic parent + atomic child, both pass."""
        self._run_matrix_case(
            parent_cls=NonAtomicParentAtomicChildMatrixCalc,
            child_cls=ChildCalc,
            name="m7-40",
            parent_should_fail=False,
            child_should_fail=False,
            expected_parent_state=CalculationModel.SUCCESS,
            expected_child_state=CalculationModel.SUCCESS,
        )

    def test_7_41_non_atomic_parent_atomic_child_child_fails(self) -> None:
        """Scenario 7.41: non-atomic parent + atomic child, child fails."""
        self._run_matrix_case(
            parent_cls=NonAtomicParentAtomicChildMatrixCalc,
            child_cls=ChildCalc,
            name="m7-41",
            parent_should_fail=False,
            child_should_fail=True,
            expected_parent_state=CalculationModel.ERROR,
            expected_child_state=CalculationModel.ERROR,
        )

    def test_7_42_non_atomic_parent_atomic_child_parent_fails(self) -> None:
        """Scenario 7.42: non-atomic parent fails after atomic child passes."""
        self._run_matrix_case(
            parent_cls=NonAtomicParentAtomicChildMatrixCalc,
            child_cls=ChildCalc,
            name="m7-42",
            parent_should_fail=True,
            child_should_fail=False,
            expected_parent_state=CalculationModel.ERROR,
            expected_child_state=CalculationModel.SUCCESS,
        )

    def test_7_43_non_atomic_parent_atomic_child_both_fail(self) -> None:
        """Scenario 7.43: non-atomic parent + atomic child, both fail."""
        self._run_matrix_case(
            parent_cls=NonAtomicParentAtomicChildMatrixCalc,
            child_cls=ChildCalc,
            name="m7-43",
            parent_should_fail=True,
            child_should_fail=True,
            expected_parent_state=CalculationModel.ERROR,
            expected_child_state=CalculationModel.ERROR,
        )

    # -- non-atomic parent -> non-atomic child -------------------------

    def test_7_44_non_atomic_parent_non_atomic_child_both_pass(self) -> None:
        """Scenario 7.44: non-atomic parent + non-atomic child, both pass."""
        self._run_matrix_case(
            parent_cls=NonAtomicParentNonAtomicChildMatrixCalc,
            child_cls=NonAtomicChildCalc,
            name="m7-44",
            parent_should_fail=False,
            child_should_fail=False,
            expected_parent_state=CalculationModel.SUCCESS,
            expected_child_state=CalculationModel.SUCCESS,
        )

    def test_7_45_non_atomic_parent_non_atomic_child_child_fails(self) -> None:
        """Scenario 7.45: non-atomic parent + non-atomic child, child fails."""
        self._run_matrix_case(
            parent_cls=NonAtomicParentNonAtomicChildMatrixCalc,
            child_cls=NonAtomicChildCalc,
            name="m7-45",
            parent_should_fail=False,
            child_should_fail=True,
            expected_parent_state=CalculationModel.ERROR,
            expected_child_state=CalculationModel.ERROR,
        )

    def test_7_46_non_atomic_parent_non_atomic_child_parent_fails(self) -> None:
        """Scenario 7.46: non-atomic parent fails after non-atomic child passes."""
        self._run_matrix_case(
            parent_cls=NonAtomicParentNonAtomicChildMatrixCalc,
            child_cls=NonAtomicChildCalc,
            name="m7-46",
            parent_should_fail=True,
            child_should_fail=False,
            expected_parent_state=CalculationModel.ERROR,
            expected_child_state=CalculationModel.SUCCESS,
        )

    def test_7_47_non_atomic_parent_non_atomic_child_both_fail(self) -> None:
        """Scenario 7.47: non-atomic parent + non-atomic child, both fail."""
        self._run_matrix_case(
            parent_cls=NonAtomicParentNonAtomicChildMatrixCalc,
            child_cls=NonAtomicChildCalc,
            name="m7-47",
            parent_should_fail=True,
            child_should_fail=True,
            expected_parent_state=CalculationModel.ERROR,
            expected_child_state=CalculationModel.ERROR,
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()

