"""
Cluster 3b: ``post_validation`` — rollback semantics.

Intent (from docs/features/data-pipeline/lifecycle hooks.md and
docs/reference/LexModel Internals.md):

    ``post_validation()`` runs inside an AFTER_SAVE hook. Raising from
    it must trigger a **rollback** to the pre-save snapshot:
      - the DB row is restored to its pre-save state (or removed, if
        the save was a create),
      - the caller receives the exception,
      - the framework does not silently swallow the failure.

Scenario numbering matches
docs/test-plan/test-clusters.md#3-validation-hooks.
"""

from __future__ import annotations

import unittest

from lex.tests.e2e._e2e_test_case import E2ETestCase

from .models import ALL_MODELS, PostValidatedItem


class TestCluster03b_PostValidation(E2ETestCase):
    """post_validation rollback semantics."""

    e2e_models = ALL_MODELS

    # -- 3.3 -----------------------------------------------------------
    def test_3_3_post_validation_passes_saves_record(self) -> None:
        """Scenario 3.3: ``post_validation`` passes → record saved, history created."""
        item = PostValidatedItem(name="pv-ok", value=5)
        item.save()

        self.assertTrue(
            PostValidatedItem.objects.filter(pk=item.pk).exists(),
            "Record must persist when post_validation passes",
        )
        self.assertGreaterEqual(
            item.history.count(), 1,
            "At least one history row must exist after a successful save",
        )

    # -- 3.4 -----------------------------------------------------------
    def test_3_4_post_validation_failure_raises_and_rolls_back(self) -> None:
        """
        Scenario 3.4: ``post_validation`` raises → record rolled back.

        If post_validation declares the new state invalid, the DB must
        be restored to the *pre-save* state (or the record must not
        exist, if the save was a create). Either outcome satisfies the
        customer contract: "the caller sees no invalid state in the DB".
        """
        item = PostValidatedItem(name="pv-bad", value=-1)

        with self.assertRaises(
            Exception,
            msg="post_validation failure must propagate to the caller",
        ):
            item.save()

        rows = PostValidatedItem.objects.filter(name="pv-bad")
        self.assertFalse(
            rows.filter(value=-1).exists(),
            "DB must NEVER retain the invalid state that post_validation rejected",
        )

    # -- 3.8 -----------------------------------------------------------
    def test_3_8_post_validation_failure_on_update_restores_previous_values(self) -> None:
        """
        Scenario 3.8: Rollback restores field values.

        When post_validation rejects an UPDATE, the DB must be restored
        to the record's pre-save field values — not a blank slate.
        """
        item = PostValidatedItem.objects.create(name="pv-original", value=10)

        item.value = -5
        with self.assertRaises(Exception):
            item.save()

        fresh = PostValidatedItem.objects.get(pk=item.pk)
        self.assertEqual(
            fresh.value, 10,
            "After a post_validation failure on update, the record must "
            "be restored to its pre-save state (value=10), not left at "
            "the rejected value (value=-5)",
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
