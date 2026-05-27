"""Cluster 15d — Three-level chain (root → middle → grandchild).

Scenarios 15.14 – 15.15. Pins that parent_log is the IMMEDIATE stack
parent at each level, not the root.
"""
from __future__ import annotations

from . import _CalcLogTestCase
from .models import LogGrandchildCalc, LogMiddleCombinatoric

import pytest

pytestmark = pytest.mark.calculation_logging


class TestCluster15d_ThreeLevelChain(_CalcLogTestCase):
    """parent_log chains correctly across multiple hops."""

    def test_15_14_three_level_parent_log_chains(self):
        """15.14: 3 rows; grandchild.parent_log_id == middle.id;
        middle.parent_log_id == root.id; root.parent_log_id IS NULL.
        All three share the same calculationId.
        """
        root = self._save_root(child_mode="three_level", units_csv="u1")
        root_row = self.assert_log_row(root, parent=None, contains="root root-three")

        middle = LogMiddleCombinatoric.objects.get(unit="u1")
        middle_row = self.assert_log_row(
            middle, parent=root_row, contains="middle u1",
        )

        grandchild = LogGrandchildCalc.objects.get(unit="u1")
        self.assert_log_row(
            grandchild, parent=middle_row, contains="grandchild u1",
        )

        self.assert_total_rows(3)

    def test_15_15_parent_log_is_immediate_not_root(self):
        """15.15: grandchild.parent_log_id MUST equal middle.id and
        MUST NOT equal root.id. Pins that ContextResolver.resolve()
        reads model_ctx.parent (top-1), not get_root().
        """
        root = self._save_root(child_mode="three_level", units_csv="u1")
        root_row = self.assert_log_row(root, parent=None)

        middle = LogMiddleCombinatoric.objects.get(unit="u1")
        middle_row = self.assert_log_row(middle, parent=root_row)

        grandchild = LogGrandchildCalc.objects.get(unit="u1")
        grandchild_row = self.assert_log_row(grandchild, parent=middle_row)

        self.assertNotEqual(
            grandchild_row.parent_log_id, root_row.id,
            "grandchild_row.parent_log_id MUST NOT equal root_row.id — "
            "if it does, ContextResolver is using get_root() instead of "
            "model_ctx.parent and the tree is flat instead of nested.",
        )
