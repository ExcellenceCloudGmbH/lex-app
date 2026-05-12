"""
Cluster 15e — three-level grandchild parenting (15.12–15.13).

Drives the three-level chain root → middle → grandchild. Pins the LIFO
``ModelContext`` invariant: each level's ``CalculationLog`` row parents
to the level immediately above it, so the resulting parent chain
mirrors the call tree.
"""
from __future__ import annotations

from . import _CalcLogTestCase
from .models import LogGrandchildCalc, LogMiddleCombinatoric, LogRootCalc


class TestCluster15e_ThreeLevel(_CalcLogTestCase):
    """root → middle → grandchild parent chain."""

    # -- 15.12 ---------------------------------------------------------
    def test_15_12_three_level_writes_three_rows(self):
        """All three levels log, so total rows = 3."""
        root = LogRootCalc(name="r12", child_mode="three_level", units_csv="u1")
        self.drive_root(root)

        self.assert_total_rows(3)
        self.assert_log_row(root, parent=None)
        middle = LogMiddleCombinatoric.objects.get(unit="u1")
        gc = LogGrandchildCalc.objects.get(unit="u1")
        # parent assertions delegated to 15.13
        self.assertIsNotNone(middle)
        self.assertIsNotNone(gc)

    # -- 15.13 ---------------------------------------------------------
    def test_15_13_three_level_parent_chain_middle_then_grandchild(self):
        """``middle.parent_log == root_row`` AND
        ``grandchild.parent_log == middle_row`` — the LIFO stack drives
        a correct two-link parent chain.
        """
        root = LogRootCalc(name="r13", child_mode="three_level", units_csv="u1")
        self.drive_root(root)

        root_row = self.assert_log_row(root, parent=None)
        middle = LogMiddleCombinatoric.objects.get(unit="u1")
        middle_row = self.assert_log_row(middle, parent=root_row, contains="middle u1")

        gc = LogGrandchildCalc.objects.get(unit="u1")
        gc_row = self.assert_log_row(gc, parent=middle_row, contains="grandchild u1")

        # Explicit parent_log_id chain assertion (regression guard).
        self.assertEqual(middle_row.parent_log_id, root_row.id)
        self.assertEqual(gc_row.parent_log_id, middle_row.id)

