"""Cluster 15f — Hierarchy preservation across the .save() boundary.

Scenarios 15.19 – 15.21. Pins the EXPECTED behaviour: every
CalculationModel's calculate() should run with itself on top of the
model context stack, so its LexLogger calls produce a dedicated
CalculationLog row with parent_log pointing at the calling model.

The framework currently does NOT push self in
CalculationModel.execute_calculation_sync (CalculationModel.py has zero
model_logging_context usages). As a result, inner CalcModel.save()
collapses its log text onto the outer model's row via the
_persist_message get_or_create key (CalculationLog.py:114).

All three tests are marked @unittest.expectedFailure. Fix lives in
CalculationModel.execute_calculation_sync: wrap `func()` in
`model_logging_context(self)`. When that lands, these flip to
unexpected-success and the markers can be removed.
"""
from __future__ import annotations

import unittest

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

    @unittest.expectedFailure
    def test_15_19_two_level_calcmodel_chain(self):
        """15.19: LogRootCalc.calculate() does LogInnerSavedCalc().save().
        Inner emits 'inner x' via LexLogger. Expected: 2 rows total —
        root row (parent_log IS NULL) + inner row (content_type=
        LogInnerSavedCalc, object_id=inner.pk, parent_log=root_row).

        Currently fails: execute_calculation_sync does not push inner
        onto the stack. Inner's log resolves current=root, parent=None,
        so 'inner x' gets appended to root's row via get_or_create.
        Net effect: 1 row (root) with text containing both 'root ...'
        AND 'inner x' — hierarchy lost.
        """
        root = self._save_root(child_mode="calcmodel_chain", units_csv="x")
        root_row = self.assert_log_row(root, parent=None, contains="root root-calcmodel_chain")

        inner = LogInnerSavedCalc.objects.get(name="x")
        self.assert_log_row(inner, parent=root_row, contains="inner x")
        self.assert_total_rows(2)

    @unittest.expectedFailure
    def test_15_20_three_level_calcmodel_chain(self):
        """15.20: LogRootCalc → LogMiddleSavedCalc → LogInnerSavedCalc,
        every transition is a direct .save() (no .create() fan-out).
        Expected: 3 rows, each parented to its immediate ancestor.

        Currently fails: neither middle nor inner pushes itself. Every
        log emitted from middle's calculate() AND inner's calculate()
        collapses onto root's row. Net effect: 1 row with three
        appended log lines, no parent_log chain.
        """
        root = self._save_root(child_mode="calcmodel_chain_3", units_csv="x")
        root_row = self.assert_log_row(root, parent=None)

        middle = LogMiddleSavedCalc.objects.get(name="x")
        middle_row = self.assert_log_row(middle, parent=root_row, contains="middle x")

        inner = LogInnerSavedCalc.objects.get(name="inner-from-x")
        self.assert_log_row(inner, parent=middle_row, contains="inner inner-from-x")
        self.assert_total_rows(3)

    @unittest.expectedFailure
    def test_15_21_fanout_then_inner_calcmodel_save(self):
        """15.21: LogRootCalc → LogChildWithInnerSave.create() (fan-out)
        → inside each child's calculate(): LogInnerSavedCalc().save().
        Expected: 3 rows — root (parent_log IS NULL) + mixin child
        (parent_log=root_row) + inner CalcModel (parent_log=child_row,
        content_type=LogInnerSavedCalc).

        Currently fails: the .create() fan-out correctly pushes the
        mixin child (line 887), but the inner CalcModel's save() does
        not push itself. Inner's 'inner from-u1' collapses onto the
        mixin child's row. Net effect: 2 rows (root + child), where the
        child row's calculation_log contains BOTH 'child u1' AND
        'inner from-u1'.

        This is the mixed-boundary case: hierarchy is preserved up to
        the Mixin layer but lost at the inner CalcModel layer.
        """
        root = self._save_root(child_mode="fanout_then_calcmodel", units_csv="u1")
        root_row = self.assert_log_row(root, parent=None)

        child = LogChildWithInnerSave.objects.get(unit="u1")
        child_row = self.assert_log_row(child, parent=root_row, contains="child u1")

        inner = LogInnerSavedCalc.objects.get(name="from-u1")
        self.assert_log_row(inner, parent=child_row, contains="inner from-u1")
        self.assert_total_rows(3)
