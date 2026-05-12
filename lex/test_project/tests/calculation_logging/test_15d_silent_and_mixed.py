"""
Cluster 15d — silent vs mixed siblings (15.9–15.11).

Production case: in a combinatorial fan-out under one root, some child
models log (``LexLogger().log()``) and some don't. The silent ones must
*run* (state changes commit) but produce **zero** ``CalculationLog``
rows — and the loud sibling's ``parent_log`` must still point at the
root, not at whichever silent sibling happened to be on top of the
LIFO stack last.
"""
from __future__ import annotations

from . import _CalcLogTestCase
from .models import LogLoudChild, LogRootCalc, LogSilentChild


class TestCluster15d_SilentAndMixed(_CalcLogTestCase):
    """Silent children produce no rows; mixed parents only the loud sibling."""

    # -- 15.9 ----------------------------------------------------------
    def test_15_9_silent_child_runs_but_writes_no_log_row(self):
        """``LogSilentChild.calculate()`` mutates state (``touched=True``)
        but never calls ``LexLogger`` — so the row count for this calc
        is just the root row, and the silent child has zero rows.
        """
        root = LogRootCalc(name="r9", child_mode="silent", units_csv="u1")
        self.drive_root(root)

        silent = LogSilentChild.objects.get(unit="u1")
        self.assertTrue(
            silent.touched,
            "Silent child must have run its calculate() (touched=True). "
            "If False, the calc was skipped, not just silent.",
        )
        self.assert_no_log_row(silent)
        self.assert_total_rows(1)
        self.assert_log_row(root, parent=None)

    # -- 15.10 ---------------------------------------------------------
    def test_15_10_mixed_writes_root_plus_loud_only(self):
        """``child_mode="mixed"`` spawns one loud + one silent child.
        Total rows = 2 (root + loud); silent child gets no row.
        """
        root = LogRootCalc(name="r10", child_mode="mixed", units_csv="u1")
        self.drive_root(root)

        root_row = self.assert_log_row(root, parent=None)
        loud = LogLoudChild.objects.get(unit="u1")
        silent = LogSilentChild.objects.get(unit="u1")
        self.assert_log_row(loud, parent=root_row, contains="loud u1")
        self.assert_no_log_row(silent)
        self.assert_total_rows(2)

    # -- 15.11 ---------------------------------------------------------
    def test_15_11_mixed_loud_child_parent_is_root_not_silent(self):
        """Regression guard: even though a silent sibling was pushed
        onto the LIFO stack between the root and the loud child's
        ``calculate()``, the loud child's row must parent to the
        **root**, not to a stale silent-sibling reference.
        """
        root = LogRootCalc(name="r11", child_mode="mixed", units_csv="u1")
        self.drive_root(root)

        root_row = self.assert_log_row(root, parent=None)
        loud = LogLoudChild.objects.get(unit="u1")
        loud_row = self.assert_log_row(loud, parent=root_row)
        self.assertEqual(
            loud_row.parent_log_id, root_row.id,
            f"Loud child's parent_log must be root_row.id={root_row.id}, "
            f"got {loud_row.parent_log_id}.",
        )

