"""
Cluster 5a: Basic history — create / update / delete / skip.

Intent (from docs/features/history/):

    Every ``.save()`` on a :class:`LexModel` produces exactly one
    history row. ``history_type`` reflects the operation:
      ``"+"`` create, ``"~"`` change, ``"-"`` delete.
    ``skip_history_when_saving = True`` suppresses that one save's
    history row (and nothing else).

Scenario numbering matches
docs/test-plan/test-clusters.md#5-history--bitemporal.
"""

from __future__ import annotations

import unittest

from lex.test_project.tests._e2e_test_case import E2ETestCase

from .models import ALL_MODELS, HistSimpleItem

import pytest

pytestmark = pytest.mark.history


class TestCluster05a_BasicHistory(E2ETestCase):
    """Basic history trail contract."""

    e2e_models = ALL_MODELS

    # -- 5.1 -----------------------------------------------------------
    def test_5_1_create_produces_single_plus_history_row(self) -> None:
        """Scenario 5.1: Create → 1 history row, ``history_type == "+"``."""
        item = HistSimpleItem(name="s5-1", value=1)
        item.save()

        self.assertEqual(
            item.history.count(), 1,
            "Exactly one history row must exist after create",
        )
        self.assertEqual(
            item.history.first().history_type, "+",
            "First history row must be typed '+' (created)",
        )

    # -- 5.2 -----------------------------------------------------------
    def test_5_2_update_adds_tilde_history_row(self) -> None:
        """Scenario 5.2: Update → new history row with ``history_type == "~"``."""
        item = HistSimpleItem.objects.create(name="s5-2", value=1)
        item.value = 2
        item.save()

        self.assertEqual(
            item.history.count(), 2,
            "One history row per save: create + update = 2",
        )
        latest = item.history.first()  # simple_history returns newest first
        self.assertEqual(
            latest.history_type, "~",
            "Latest history row after update must be typed '~' (changed)",
        )

    # -- 5.3 -----------------------------------------------------------
    def test_5_3_delete_adds_minus_history_row(self) -> None:
        """Scenario 5.3: Delete → new history row with ``history_type == "-"``."""
        item = HistSimpleItem.objects.create(name="s5-3", value=1)
        pk = item.pk
        item.delete()

        rows = HistSimpleItem.history.filter(id=pk).order_by("-history_id")
        self.assertGreaterEqual(
            rows.count(), 2,
            "A deleted record must have create + delete history rows",
        )
        self.assertEqual(
            rows.first().history_type, "-",
            "Latest history row after delete must be typed '-'",
        )

    # -- 5.4 -----------------------------------------------------------
    def test_5_4_multiple_updates_produce_ordered_history(self) -> None:
        """Scenario 5.4: Multiple updates → rows ordered by ``history_id``."""
        item = HistSimpleItem.objects.create(name="s5-4", value=1)
        item.value = 2
        item.save()
        item.value = 3
        item.save()

        rows = list(item.history.order_by("history_id"))
        self.assertEqual(len(rows), 3, "3 saves ⇒ 3 history rows")
        ids = [r.history_id for r in rows]
        self.assertEqual(
            ids, sorted(ids),
            "History rows must be ordered ascending by history_id",
        )

    # -- 5.5 -----------------------------------------------------------
    def test_5_5_skip_history_when_saving_suppresses_one_row(self) -> None:
        """
        Scenario 5.5: ``skip_history_when_saving = True`` → no history row
        for that save.
        """
        item = HistSimpleItem.objects.create(name="s5-5", value=1)
        start = item.history.count()

        item.value = 42
        item.skip_history_when_saving = True
        item.save()

        self.assertEqual(
            item.history.count(), start,
            "skip_history_when_saving must suppress exactly that save's "
            "history row (count unchanged)",
        )

    # -- 5.10 ----------------------------------------------------------
    def test_5_10_concurrent_edits_produce_distinct_rows(self) -> None:
        """
        Scenario 5.10: Two rapid saves → two distinct history entries.

        We run the two saves serially in the same thread — the contract
        is that each ``save()`` yields its own row, not that Django
        itself handles concurrency.
        """
        item = HistSimpleItem.objects.create(name="s5-10", value=1)
        item.value = 2
        item.save()
        item.value = 3
        item.save()

        ids = {r.history_id for r in item.history.all()}
        self.assertEqual(
            len(ids), 3,
            "Three distinct history rows required (create + 2 updates)",
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()



