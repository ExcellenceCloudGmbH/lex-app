"""Cluster 15f — Hierarchy preservation across the .save() boundary.

Scenarios 15.19 – 15.21. Every CalculationModel's calculate() runs with
itself on top of the model context stack, so its LexLogger calls produce a
dedicated CalculationLog row with parent_log pointing at the calling model.
docs/calculations/logging.md (Nested Calculations) promises this nesting;
these scenarios check that the framework provides it for a CalculationModel
saved directly inside another calculation, without the caller wrapping the
save in model_logging_context itself.

From 2026-05-12 to the fix for BUG-055 the three scenarios were marked
@unittest.expectedFailure: on the synchronous route
(calculate_hook → _run_in_calculation_executor → execute_calculation_sync)
nothing pushed the inner model, so its lines were appended to the calling
model's row. calculate_hook now runs a synchronous calculation inside
CalculationModel.child_calculation_context, which skips a model that is
already on top (the root, pushed by the API or by ``_save_root``).
"""
from __future__ import annotations

from . import _CalcLogTestCase
from .models import (
    LogChildWithInnerSave,
    LogInnerSavedCalc,
    LogMiddleSavedCalc,
)

import pytest

pytestmark = pytest.mark.calculation_logging


class TestCluster15f_HierarchyAcrossSave(_CalcLogTestCase):
    """parent_log chains correctly even when an inner CalculationModel
    is saved directly (no .create() fan-out wrapper)."""

    def test_15_19_two_level_calcmodel_chain(self):
        """15.19: LogRootCalc.calculate() does LogInnerSavedCalc().save().
        Inner emits 'inner x' via LexLogger. Expected: 2 rows total —
        root row (parent_log IS NULL) + inner row (content_type=
        LogInnerSavedCalc, object_id=inner.pk, parent_log=root_row).

        Without the inner's own frame, 'inner x' was appended to the
        root's row: 1 row holding both lines, hierarchy lost.
        """
        root = self._save_root(child_mode="calcmodel_chain", units_csv="x")
        root_row = self.assert_log_row(root, parent=None, contains="root root-calcmodel_chain")

        inner = LogInnerSavedCalc.objects.get(name="x")
        self.assert_log_row(inner, parent=root_row, contains="inner x")
        self.assert_total_rows(2)

    def test_15_20_three_level_calcmodel_chain(self):
        """15.20: LogRootCalc → LogMiddleSavedCalc → LogInnerSavedCalc,
        every transition is a direct .save() (no .create() fan-out).
        Expected: 3 rows, each parented to its immediate ancestor.

        Without their own frames, the lines of the middle and the inner
        both collapsed onto the root's row: 1 row, no parent_log chain.
        """
        root = self._save_root(child_mode="calcmodel_chain_3", units_csv="x")
        root_row = self.assert_log_row(root, parent=None)

        middle = LogMiddleSavedCalc.objects.get(name="x")
        middle_row = self.assert_log_row(middle, parent=root_row, contains="middle x")

        inner = LogInnerSavedCalc.objects.get(name="inner-from-x")
        self.assert_log_row(inner, parent=middle_row, contains="inner inner-from-x")
        self.assert_total_rows(3)

    def test_15_21_fanout_then_inner_calcmodel_save(self):
        """15.21: LogRootCalc → LogChildWithInnerSave.create() (fan-out)
        → inside each child's calculate(): LogInnerSavedCalc().save().
        Expected: 3 rows — root (parent_log IS NULL) + mixin child
        (parent_log=root_row) + inner CalcModel (parent_log=child_row,
        content_type=LogInnerSavedCalc).

        This is the mixed-boundary case. The .create() fan-out pushes the
        mixin child (child_calculation_context), and the inner
        CalculationModel's synchronous run pushes itself on top of it.
        Without the second push, 'inner from-u1' was appended to the
        mixin child's row.
        """
        root = self._save_root(child_mode="fanout_then_calcmodel", units_csv="u1")
        root_row = self.assert_log_row(root, parent=None)

        child = LogChildWithInnerSave.objects.get(unit="u1")
        child_row = self.assert_log_row(child, parent=root_row, contains="child u1")

        inner = LogInnerSavedCalc.objects.get(name="from-u1")
        self.assert_log_row(inner, parent=child_row, contains="inner from-u1")
        self.assert_total_rows(3)
