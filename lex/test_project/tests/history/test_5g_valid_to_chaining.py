"""
Cluster 5g: History ``valid_to`` chaining contract.

Intent (from docs/features/tracking/history.md +
docs/features/tracking/bitemporal history.md):

    History rows form a contiguous timeline. Row N's ``valid_to``
    must equal row N+1's ``valid_from`` so there are no gaps and no
    overlaps. The latest row's ``valid_to`` is ``NULL`` — the timeline
    is open-ended at the head.

Cluster 5.4 (``test_5_4``) only asserts ascending ``history_id`` —
the chaining half of its own docstring is not pinned. 5g closes that
gap. Scenario numbering matches docs/test-plan/test-clusters.md § 5g.
"""

from __future__ import annotations

import time
import unittest

from lex.test_project.tests._e2e_test_case import E2ETestCase

from .models import ALL_MODELS, HistSimpleItem

import pytest

pytestmark = pytest.mark.history


class TestCluster05g_ValidToChaining(E2ETestCase):
    """``valid_to`` of row N must equal ``valid_from`` of row N+1."""

    e2e_models = ALL_MODELS

    # -- 5.61 ----------------------------------------------------------
    def test_5_61_three_saves_chain_valid_to_to_valid_from(self) -> None:
        """
        Scenario 5.61: Three saves chain ``valid_to → valid_from``
        end-to-end and the latest row is open-ended.
        """
        item = HistSimpleItem.objects.create(name="s5-61", value=1)
        time.sleep(0.001)
        item.value = 2
        item.save()
        time.sleep(0.001)
        item.value = 3
        item.save()

        rows = list(item.history.order_by("history_id"))
        self.assertEqual(len(rows), 3, "Expected 3 history rows; got %d" % len(rows))

        self.assertEqual(
            rows[0].valid_to, rows[1].valid_from,
            "row[0].valid_to must equal row[1].valid_from — the timeline "
            "must be contiguous; got %r != %r" % (rows[0].valid_to, rows[1].valid_from),
        )
        self.assertEqual(
            rows[1].valid_to, rows[2].valid_from,
            "row[1].valid_to must equal row[2].valid_from — gap or overlap "
            "would break the bitemporal timeline; got %r != %r"
            % (rows[1].valid_to, rows[2].valid_from),
        )
        self.assertIsNone(
            rows[2].valid_to,
            "Latest row's valid_to must be NULL (timeline open-ended at "
            "head); got %r" % (rows[2].valid_to,),
        )

    # -- 5.61b ---------------------------------------------------------
    def test_5_61b_delete_closes_chain(self) -> None:
        """
        Scenario 5.61b: Delete closes the chain — the ``-`` row's
        ``valid_from`` matches the previous row's ``valid_to``, and the
        ``-`` row's ``valid_to`` is None.
        """
        item = HistSimpleItem.objects.create(name="s5-61b", value=1)
        time.sleep(0.001)
        item.value = 2
        item.save()
        pk = item.pk
        time.sleep(0.001)
        item.delete()

        rows = list(
            HistSimpleItem.history.filter(id=pk).order_by("history_id")
        )
        self.assertEqual(len(rows), 3, "Create + update + delete = 3 rows; got %d" % len(rows))
        self.assertEqual(rows[-1].history_type, "-", "Last row must be a delete")

        self.assertEqual(
            rows[1].valid_to, rows[2].valid_from,
            "Delete row's valid_from must equal preceding row's valid_to — "
            "the timeline must remain contiguous through deletion",
        )
        self.assertIsNone(
            rows[2].valid_to,
            "Delete row's valid_to must be NULL — there is no further "
            "history to chain into",
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()

