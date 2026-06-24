"""
Cluster 3e: pre-validation snapshot lifecycle.

Intent (from docs/features/data-pipeline/lifecycle hooks.md and
docs/reference/LexModel Internals.md):

    The pre-validation snapshot is an *internal rollback buffer*: LexModel
    captures a full copy of every field in the BEFORE_SAVE hook so that a
    failing ``post_validation`` can restore the record to its pre-save state
    (see Cluster 3b). That buffer exists **only** to serve a rollback.

    Therefore its lifecycle, stated as customer-observable contract, is:
      - while a save is in flight it must be available, so a post_validation
        failure can still roll the record back (Cluster 3b proves this end to
        end); and
      - once the save has *succeeded*, rollback can no longer happen, so the
        buffer must be released rather than pinned on the live instance for the
        rest of its lifetime. On a large calculate-all this buffer is a second
        full-field copy per row; holding it after success is pure memory
        overhead with no behavioural purpose.

    This sub-cluster pins both ends of that contract: the buffer is released
    after a successful save, and releasing it does NOT weaken the rollback the
    buffer exists to provide.

Scenario numbering matches
docs/test-plan/test-clusters.md#3-validation-hooks.
"""

from __future__ import annotations

import unittest

from lex.test_project.tests._e2e_test_case import E2ETestCase

from .models import ALL_MODELS, PostValidatedItem

import pytest

pytestmark = pytest.mark.validation_hooks


class TestCluster03e_SnapshotLifecycle(E2ETestCase):
    """The pre-validation snapshot must outlive a save only until it succeeds."""

    e2e_models = ALL_MODELS

    # -- 3.9 -----------------------------------------------------------
    def test_3_9_snapshot_released_after_successful_create(self) -> None:
        """
        Scenario 3.9: snapshot released after a successful save.

        Given a record whose post_validation passes,
        When it is saved (create) and again (update),
        Then the internal pre-validation snapshot is not retained on the
        instance afterwards — it is only an in-flight rollback buffer, so a
        successful save must leave nothing pinned.
        """
        item = PostValidatedItem(name="snap-ok", value=5)
        item.save()

        self.assertTrue(
            PostValidatedItem.objects.filter(pk=item.pk).exists(),
            "Record must persist when post_validation passes",
        )
        self.assertIsNone(
            getattr(item, "_pre_validation_snapshot", None),
            "After a successful create the rollback buffer must be released, "
            "not pinned on the live instance",
        )

        # A second successful save (update) must likewise leave nothing pinned.
        item.value = 7
        item.save()
        self.assertIsNone(
            getattr(item, "_pre_validation_snapshot", None),
            "After a successful update the rollback buffer must be released",
        )

    # -- 3.10 ----------------------------------------------------------
    def test_3_10_snapshot_release_does_not_break_rollback(self) -> None:
        """
        Scenario 3.10: releasing the buffer on success does not weaken rollback.

        Given a record that was first saved successfully (which releases the
            buffer),
        When a later update is rejected by post_validation,
        Then the record is still rolled back to its last-good field values —
        the buffer is re-captured for each save, so releasing it after the
        previous success must not leave the failing save with nothing to
        restore from.
        """
        item = PostValidatedItem.objects.create(name="snap-rollback", value=10)
        # The successful create above released the buffer (Scenario 3.9).
        self.assertIsNone(
            getattr(item, "_pre_validation_snapshot", None),
            "Precondition: successful create released the buffer",
        )

        item.value = -5
        with self.assertRaises(
            Exception,
            msg="post_validation failure must still propagate to the caller",
        ):
            item.save()

        fresh = PostValidatedItem.objects.get(pk=item.pk)
        self.assertEqual(
            fresh.value, 10,
            "Rollback must still restore the pre-save value (10) even though "
            "the buffer is released after each successful save",
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
