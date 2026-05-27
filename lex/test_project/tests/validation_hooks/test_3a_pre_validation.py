"""
Cluster 3a: ``pre_validation`` — save cancellation semantics.

Intent (from docs/features/data-pipeline/lifecycle hooks.md and
docs/reference/LexModel Internals.md):

    ``pre_validation()`` is called first, inside a BEFORE_SAVE hook.
    Raising any exception from it must **cancel the save entirely**:
      - no DB row is created,
      - no history row is created,
      - the original record (if any) is left untouched.

Scenario numbering matches
docs/test-plan/test-clusters.md#3-validation-hooks.
"""

from __future__ import annotations

import unittest

from lex.tests.e2e._e2e_test_case import E2ETestCase

from .models import ALL_MODELS, PreValidatedItem

import pytest

pytestmark = pytest.mark.validation_hooks


class TestCluster03a_PreValidation(E2ETestCase):
    """pre_validation cancels the save."""

    e2e_models = ALL_MODELS

    # -- 3.1 -----------------------------------------------------------
    def test_3_1_pre_validation_passes_saves_record(self) -> None:
        """
        Scenario 3.1: ``pre_validation`` passes.

        A record whose ``pre_validation`` does not raise saves normally
        and produces exactly one history row.
        """
        item = PreValidatedItem(name="ok", value=1)
        item.save()

        self.assertTrue(
            PreValidatedItem.objects.filter(pk=item.pk).exists(),
            "Record must be persisted when pre_validation passes",
        )
        self.assertEqual(
            item.history.count(), 1,
            "Exactly one history row must exist for a successful create",
        )

    # -- 3.2 -----------------------------------------------------------
    def test_3_2_pre_validation_failure_cancels_save(self) -> None:
        """
        Scenario 3.2: ``pre_validation`` raises → save cancelled.

        The caller receives an exception, and neither a DB row NOR a
        history row is created. Customers must be able to rely on
        ``pre_validation`` as a hard precondition.
        """
        item = PreValidatedItem(name="FORBIDDEN", value=1)

        with self.assertRaises(
            Exception,
            msg="pre_validation failure must propagate to the caller",
        ):
            item.save()

        self.assertFalse(
            PreValidatedItem.objects.filter(name="FORBIDDEN").exists(),
            "No DB row may exist when pre_validation cancels the save",
        )
        self.assertEqual(
            PreValidatedItem.history.filter(name="FORBIDDEN").count(),
            0,
            "No history row may exist for a pre_validation-rejected save",
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
