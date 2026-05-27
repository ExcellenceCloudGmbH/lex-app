"""
Cluster 3c: Standard lifecycle hook ordering.

Intent (from django-lifecycle + LexModel internals):

    CREATE flow: BEFORE_CREATE → BEFORE_SAVE → (save) → AFTER_SAVE → AFTER_CREATE
    UPDATE flow: BEFORE_UPDATE → BEFORE_SAVE → (save) → AFTER_SAVE → AFTER_UPDATE

A record's first save is a create (fires CREATE hooks exactly once);
subsequent saves are updates (fire UPDATE hooks). Customer code that
uses ``AFTER_CREATE`` for one-time side effects (file processing,
welcome emails, …) relies on this contract.

Scenario numbering matches
docs/test-plan/test-clusters.md#3-validation-hooks.
"""

from __future__ import annotations

import unittest

from lex.tests.e2e._e2e_test_case import E2ETestCase

from .models import ALL_MODELS, HookOrderItem

import pytest

pytestmark = pytest.mark.validation_hooks


class TestCluster03c_HookOrdering(E2ETestCase):
    """Standard lifecycle hook ordering."""

    e2e_models = ALL_MODELS

    def setUp(self) -> None:
        super().setUp()
        HookOrderItem.hook_log.clear()

    # -- 3.5 -----------------------------------------------------------
    def test_3_5_create_hook_ordering(self) -> None:
        """
        Scenario 3.5: Create hook ordering.

        Intent: BEFORE_CREATE → BEFORE_SAVE → (save) → AFTER_SAVE → AFTER_CREATE
        """
        HookOrderItem(name="alpha").save()
        log = HookOrderItem.hook_log

        for required in ("BEFORE_CREATE", "BEFORE_SAVE",
                         "AFTER_SAVE", "AFTER_CREATE"):
            self.assertIn(
                required, log,
                f"{required} hook must fire on create; got: {log!r}",
            )

        self.assertLess(
            log.index("BEFORE_CREATE"), log.index("BEFORE_SAVE"),
            "BEFORE_CREATE must run before BEFORE_SAVE",
        )
        self.assertLess(
            log.index("BEFORE_SAVE"), log.index("AFTER_SAVE"),
            "BEFORE_SAVE must run before AFTER_SAVE",
        )
        self.assertLess(
            log.index("AFTER_SAVE"), log.index("AFTER_CREATE"),
            "AFTER_SAVE must run before AFTER_CREATE",
        )

    # -- 3.6 -----------------------------------------------------------
    def test_3_6_update_hook_ordering(self) -> None:
        """
        Scenario 3.6: Update hook ordering.

        Intent: BEFORE_UPDATE → BEFORE_SAVE → (save) → AFTER_SAVE → AFTER_UPDATE
        """
        item = HookOrderItem.objects.create(name="bravo")
        HookOrderItem.hook_log.clear()

        item.name = "bravo-updated"
        item.save()
        log = HookOrderItem.hook_log

        for required in ("BEFORE_UPDATE", "BEFORE_SAVE",
                         "AFTER_SAVE", "AFTER_UPDATE"):
            self.assertIn(
                required, log,
                f"{required} hook must fire on update; got: {log!r}",
            )

        self.assertLess(
            log.index("BEFORE_UPDATE"), log.index("BEFORE_SAVE"),
            "BEFORE_UPDATE must run before BEFORE_SAVE",
        )
        self.assertLess(
            log.index("BEFORE_SAVE"), log.index("AFTER_SAVE"),
            "BEFORE_SAVE must run before AFTER_SAVE",
        )
        self.assertLess(
            log.index("AFTER_SAVE"), log.index("AFTER_UPDATE"),
            "AFTER_SAVE must run before AFTER_UPDATE",
        )

    # -- 3.6b ----------------------------------------------------------
    def test_3_6b_update_does_not_trigger_create_hooks(self) -> None:
        """
        On update, the CREATE-specific hooks must not fire again.

        Intent: each record's first save is a create (fires CREATE hooks
        exactly once); subsequent saves are updates. This matters for
        customer code that uses ``AFTER_CREATE`` for one-time side
        effects like file processing.
        """
        item = HookOrderItem.objects.create(name="charlie")
        HookOrderItem.hook_log.clear()

        item.name = "charlie-updated"
        item.save()
        log = HookOrderItem.hook_log

        self.assertNotIn(
            "BEFORE_CREATE", log,
            "BEFORE_CREATE must NOT fire on a subsequent save",
        )
        self.assertNotIn(
            "AFTER_CREATE", log,
            "AFTER_CREATE must NOT fire on a subsequent save — "
            "this would re-run one-time customer logic",
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
