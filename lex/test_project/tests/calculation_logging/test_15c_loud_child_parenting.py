"""
Cluster 15c — loud child parenting (15.7–15.8).

A combinatoric child that calls ``LexLogger().log()`` produces a child
``CalculationLog`` row whose ``parent_log`` points at the root's row.
``CalculatedModelMixin._process_models_synchronously`` does the
``with model_logging_context(model):`` push automatically — the
LIFO stack is what drives the parent linkage.
"""
from __future__ import annotations

from . import _CalcLogTestCase
from .models import LogLoudChild, LogRootCalc


class TestCluster15c_LoudChildParenting(_CalcLogTestCase):
    """Loud child rows must parent to the root row."""

    # -- 15.7 ----------------------------------------------------------
    def test_15_7_loud_child_row_parent_is_root(self):
        """``child_mode="loud"`` produces two rows; the loud child's
        ``parent_log_id`` equals the root row's ``id``.
        """
        root = LogRootCalc(name="r7", child_mode="loud", units_csv="u1")
        self.drive_root(root)

        root_row = self.assert_log_row(root, parent=None)
        child = LogLoudChild.objects.get(unit="u1")
        self.assert_log_row(child, parent=root_row)
        self.assert_total_rows(2)

    # -- 15.8 ----------------------------------------------------------
    def test_15_8_loud_child_row_contains_unit_message(self):
        """The child row must contain the unit-specific message
        emitted by ``LogLoudChild.calculate()`` (``"loud u1"``), and
        the root row must contain the root-side opening line.
        """
        root = LogRootCalc(name="r8", child_mode="loud", units_csv="u1")
        self.drive_root(root)

        root_row = self.assert_log_row(root, parent=None, contains="root r8")
        child = LogLoudChild.objects.get(unit="u1")
        self.assert_log_row(child, parent=root_row, contains="loud u1")

