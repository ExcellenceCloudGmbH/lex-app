"""Cluster 15b — Single-parent hierarchy, loud children.

Scenarios 15.7 – 15.10. Full pipeline: LogRootCalc.save() triggers
calculate_hook which fires calculate(); LogLoudChild.create() runs the
combinatorial expansion through calc_and_save_sync.
"""
from __future__ import annotations

from lex.audit_logging.models.CalculationLog import CalculationLog

from . import _CalcLogTestCase
from .models import LogLoudChild, LogRootCalc

import pytest

pytestmark = pytest.mark.calculation_logging


class TestCluster15b_LoudChildren(_CalcLogTestCase):
    """Loud children produce a CalculationLog row each, all chaining
    to the root's row via parent_log."""

    def test_15_7_root_logs_no_children(self):
        """15.7: Root emits LexLogger in its own calculate(), no
        children triggered. Exactly 1 row, parent_log_id IS NULL.
        """
        root = self._save_root(
            child_mode="log_only", units_csv="",
        )
        self.assert_log_row(root, parent=None, contains="root root-log_only")
        self.assert_total_rows(1)

    def test_15_8_root_plus_one_loud_child(self):
        """15.8: Root triggers LogLoudChild.create(unit=['u1']). Two
        rows: root (parent_log_id IS NULL) + 1 loud child (parent_log
        FK = root's row, content_type=LogLoudChild).
        """
        root = self._save_root(
            child_mode="loud", units_csv="u1",
        )
        loud = LogLoudChild.objects.get(unit="u1")
        root_row = self.assert_log_row(root, parent=None)
        self.assert_log_row(loud, parent=root_row, contains="loud u1")
        self.assert_total_rows(2)

    def test_15_9_root_plus_three_loud_children(self):
        """15.9: Three loud child instances each get their own row;
        all share parent_log = root_row.id; each row's object_id
        matches its instance.
        """
        root = self._save_root(
            child_mode="loud", units_csv="u1,u2,u3",
        )
        root_row = self.assert_log_row(root, parent=None)
        for unit in ("u1", "u2", "u3"):
            loud = LogLoudChild.objects.get(unit=unit)
            self.assert_log_row(loud, parent=root_row, contains=f"loud {unit}")
        self.assert_total_rows(4)

    def test_15_10_calculation_id_propagates(self):
        """15.10: All rows in the same operation share the same
        calculationId — operation_context propagates through the
        .create() → calc_and_save_sync boundary.
        """
        self._save_root(
            child_mode="loud", units_csv="u1,u2,u3",
        )
        ids = set(
            CalculationLog.objects.values_list("calculationId", flat=True)
        )
        self.assertEqual(
            ids, {self.calc_id},
            f"Expected every row to share calculationId={self.calc_id}, "
            f"found {ids}.",
        )
