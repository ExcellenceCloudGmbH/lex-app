"""
Cluster 5h: History suppression toolkit — beyond `skip_history_when_saving`.

Intent (from docs/features/tracking/history.md +
docs/features/tracking/bitemporal history.md):

    The framework documents FIVE distinct knobs to suppress history
    creation. Cluster 5.5 covers exactly one (``skip_history_when_saving``).
    The remaining four are customer-facing and dark:

      5.62 ``obj.save_without_historical_record()``    — single-save toggle
      5.63 ``untrack()`` / ``track()``                  — sticky between saves
      5.64 ``Model.objects.bulk_create(..., skip_history=True)``
      5.65 ``Model.objects.bulk_create(...)``           — default still tracks
      5.66 ``with suspend_bitemporal(): obj.save()``    — full bitemporal off

Scenario numbering matches docs/test-plan/test-clusters.md § 5h.
"""

from __future__ import annotations

import unittest

from lex.tests.e2e._e2e_test_case import E2ETestCase

from .models import ALL_MODELS, HistSimpleItem


class TestCluster05h_SuppressionToolkit(E2ETestCase):
    """The four documented knobs beyond ``skip_history_when_saving``."""

    e2e_models = ALL_MODELS

    # -- 5.62 ----------------------------------------------------------
    def test_5_62_save_without_historical_record_suppresses_one_save(self) -> None:
        """
        Scenario 5.62: ``obj.save_without_historical_record()`` writes
        the row but does NOT append a history row for that save; the
        next normal ``.save()`` resumes history (single-save toggle, not
        sticky).
        """
        item = HistSimpleItem.objects.create(name="s5-62", value=1)
        baseline = item.history.count()

        item.value = 2
        item.save_without_historical_record()

        self.assertEqual(
            item.history.count(), baseline,
            "save_without_historical_record() must not append a history row "
            "for that save — count unchanged",
        )

        item.value = 3
        item.save()
        self.assertEqual(
            item.history.count(), baseline + 1,
            "Subsequent normal save() must resume history — proves the "
            "toggle is single-save, not sticky",
        )

    # -- 5.63 ----------------------------------------------------------
    def test_5_63_untrack_then_track_toggles_history(self) -> None:
        """
        Scenario 5.63: ``untrack()`` is sticky — the next ``save()``
        produces no history row. ``track()`` re-enables history.
        """
        item = HistSimpleItem.objects.create(name="s5-63", value=1)
        baseline = item.history.count()

        item.untrack()
        item.value = 2
        item.save()
        self.assertEqual(
            item.history.count(), baseline,
            "untrack() must suppress the next save's history row "
            "(skip_history_when_saving sticky flag)",
        )

        item.track()
        item.value = 3
        item.save()
        self.assertEqual(
            item.history.count(), baseline + 1,
            "track() must re-enable history — the sticky flag is cleared",
        )
        self.assertEqual(
            item.history.first().history_type, "~",
            "Re-enabled history row must be typed '~' (changed)",
        )

    # -- 5.64 ----------------------------------------------------------
    def test_5_64_bulk_create_with_skip_history(self) -> None:
        """
        Scenario 5.64: ``Model.objects.bulk_create(objs,
        skip_history=True)`` persists every row but creates zero
        history rows. A subsequent ``.save()`` on one of those rows
        produces a ``~`` row (the instance must not be permanently
        untracked).
        """
        objs = [
            HistSimpleItem(name="s5-64-a", value=1),
            HistSimpleItem(name="s5-64-b", value=2),
            HistSimpleItem(name="s5-64-c", value=3),
        ]
        HistSimpleItem.objects.bulk_create(objs, skip_history=True)

        # All 3 persisted
        names = set(
            HistSimpleItem.objects.filter(
                name__in=["s5-64-a", "s5-64-b", "s5-64-c"]
            ).values_list("name", flat=True)
        )
        self.assertEqual(
            names, {"s5-64-a", "s5-64-b", "s5-64-c"},
            "bulk_create must persist every row regardless of skip_history",
        )

        # Zero history rows for these
        history_count = HistSimpleItem.history.filter(
            name__in=["s5-64-a", "s5-64-b", "s5-64-c"]
        ).count()
        self.assertEqual(
            history_count, 0,
            "bulk_create(skip_history=True) must produce zero history "
            "rows; got %d" % history_count,
        )

        # Subsequent .save() resumes history
        first = HistSimpleItem.objects.get(name="s5-64-a")
        first.value = 99
        first.save()
        self.assertGreaterEqual(
            HistSimpleItem.history.filter(name="s5-64-a").count(), 1,
            "Subsequent save() on a bulk-created row must produce a "
            "history row — bulk_create must not leave the instance "
            "permanently untracked",
        )

    # -- 5.65 ----------------------------------------------------------
    def test_5_65_bulk_create_without_skip_history_still_tracks(self) -> None:
        """
        Scenario 5.65: ``bulk_create`` *without* ``skip_history`` must
        still produce a history row per object — proves the default
        bulk-path behaviour is unchanged.
        """
        objs = [
            HistSimpleItem(name="s5-65-a", value=1),
            HistSimpleItem(name="s5-65-b", value=2),
        ]
        HistSimpleItem.objects.bulk_create(objs)

        count = HistSimpleItem.history.filter(
            name__in=["s5-65-a", "s5-65-b"]
        ).count()
        self.assertGreaterEqual(
            count, 2,
            "Default bulk_create must still track history (one row per "
            "object); got %d" % count,
        )

    # -- 5.66 ----------------------------------------------------------
    # @unittest.expectedFailure  # BUG-020: docs reference a `suspend_bitemporal()` CM that does not exist yet — only the lower-level guards are exposed today
    # def test_5_66_suspend_bitemporal_context_manager(self) -> None:
    #     """
    #     Scenario 5.66: ``with suspend_bitemporal(): obj.save()`` must
    #     run a bare ``UPDATE/INSERT`` — zero L1 history rows, zero L2
    #     meta rows — and the next save outside the block must produce
    #     the full chain again. Currently expected to fail because the
    #     public ``suspend_bitemporal()`` CM is not exposed; the lower-
    #     level guards (``suppress_main_table_sync`` etc., already
    #     covered by 9.7-9.10) are the only path today.
    #     """
    #     from lex.core.services.bitemporal_signals import suspend_bitemporal
    #
    #     item = HistSimpleItem.objects.create(name="s5-66", value=1)
    #     baseline = item.history.count()
    #
    #     with suspend_bitemporal():
    #         item.value = 2
    #         item.save()
    #
    #     self.assertEqual(
    #         item.history.count(), baseline,
    #         "suspend_bitemporal() must produce zero L1 history rows "
    #         "for the wrapped save",
    #     )
    #
    #     # And outside the block, history resumes
    #     item.value = 3
    #     item.save()
    #     self.assertEqual(
    #         item.history.count(), baseline + 1,
    #         "After the block exits, the full bitemporal chain must run "
    #         "again — the suspension is scoped, not sticky",
    #     )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()

