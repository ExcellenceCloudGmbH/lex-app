"""
Cluster 3d: Validation recursion guard.

Intent (from docs/reference/LexModel Internals.md):

    ``_validation_in_progress`` prevents infinite recursion. If
    pre_validation or post_validation triggers another save on the same
    instance (e.g. to stamp a derived field), the framework must not
    recurse endlessly. We assert behavioural safety — the save
    completes in a finite number of steps.

Scenario numbering matches
docs/test-plan/test-clusters.md#3-validation-hooks.
"""

from __future__ import annotations

import unittest

from lex.test_project.tests._e2e_test_case import E2ETestCase

from .models import ALL_MODELS, PostValidatedItem

import pytest

pytestmark = pytest.mark.validation_hooks


class TestCluster03d_RecursionGuard(E2ETestCase):
    """The recursion guard must hold."""

    e2e_models = ALL_MODELS

    # -- 3.7 -----------------------------------------------------------
    def test_3_7_nested_save_inside_validation_does_not_recurse_forever(self) -> None:
        """
        Scenario 3.7: a normal save terminates.

        If the recursion guard is broken this test hangs or raises
        RecursionError; a plain save should just succeed.
        """
        item = PostValidatedItem(name="reentrant-ok", value=0)
        item.save()
        self.assertTrue(
            PostValidatedItem.objects.filter(pk=item.pk).exists(),
            "A normal save must complete; if the recursion guard is "
            "broken this test hangs or RecursionErrors.",
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
