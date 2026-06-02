"""Cluster 15a — LexLogger builder API and content shape.

Scenarios 15.1 – 15.6. These tests do NOT go through CalculationModel.save()
or CalculatedModelMixin.create(); they exercise LexLogger directly under
a manual model_logging_context wrapper.
"""
from __future__ import annotations

import pandas as pd

from lex.audit_logging.handlers.LexLogger import LexLogger
from lex.audit_logging.utils.ModelContext import model_logging_context

from . import _CalcLogTestCase
from .models import LogRootCalc

import pytest

pytestmark = pytest.mark.calculation_logging


class TestCluster15a_BuilderAPI(_CalcLogTestCase):
    """LexLogger builder methods produce the right CalculationLog content."""

    def _make_root(self) -> LogRootCalc:
        # Save with child_mode='none' so calculate() never fires here —
        # we control LexLogger invocation manually in each test.
        # Bypass calculate_hook via skip_hooks=True? No — Rule #2 of the
        # test plan forbids it. Instead, save with is_calculated already
        # set to SUCCESS so the state machine treats it as a no-op create.
        root = LogRootCalc(name="r", child_mode="log_only", units_csv="")
        # Use the model's direct DB write — we are NOT testing the state
        # machine here, only the builder API. We bypass save() entirely
        # via QuerySet.create() on a CalculationModel sub-query would
        # still fire signals — so use bulk_create which skips save().
        LogRootCalc.objects.bulk_create([root])
        return LogRootCalc.objects.get(name="r")

    def test_15_1_add_text_persists_row(self):
        """15.1: add_text("hello").log() under model_logging_context(root)
        persists exactly one row with the text and the root's
        content_type/object_id, parent_log_id IS NULL.
        """
        root = self._make_root()
        with model_logging_context(root):
            LexLogger().add_text("hello").log()
        self.assert_log_row(root, parent=None, contains="hello")
        self.assert_total_rows(1)

    def test_15_2_chained_builder_combines(self):
        """15.2: add_heading + add_text + add_table all appear in the
        single row's calculation_log, in chain order, pipe-delimited
        markdown table included.
        """
        root = self._make_root()
        with model_logging_context(root):
            (
                LexLogger()
                .add_heading("H", level=2)
                .add_text("body")
                .add_table(["a", "b"], [["1", "2"], ["3", "4"]])
                .log()
            )
        row = self.assert_log_row(root)
        body = row.calculation_log
        self.assertIn("## H", body)
        self.assertIn("body", body)
        self.assertIn("| a | b |", body)
        self.assertIn("| 1 | 2 |", body)
        # Order check: heading before body before table
        self.assertLess(body.index("## H"), body.index("body"))
        self.assertLess(body.index("body"), body.index("| a | b |"))

    def test_15_3_add_dataframe_renders_markdown(self):
        """15.3: add_dataframe(df).log() renders the DataFrame as a
        markdown table matching df.to_markdown(index=False).
        """
        root = self._make_root()
        df = pd.DataFrame({"q": ["Q1", "Q2"], "rev": [100, 200]})
        with model_logging_context(root):
            LexLogger().add_dataframe(df).log()
        expected = df.to_markdown(index=False)
        row = self.assert_log_row(root)
        self.assertIn(expected, row.calculation_log)

    def test_15_4_buffer_resets_after_log(self):
        """15.4: LexLogger is a singleton; .log() clears its buffer.
        Two sequential .add_text(...).log() calls produce a single row
        (per 15.5 contract) whose body has 'second' but the SECOND log
        call's contribution must NOT include 'first' again.
        """
        root = self._make_root()
        with model_logging_context(root):
            LexLogger().add_text("first").log()
            LexLogger().add_text("second").log()
        row = self.assert_log_row(root)
        # Body contains both because of row coalescing (15.5)…
        self.assertIn("first", row.calculation_log)
        self.assertIn("second", row.calculation_log)
        # …but 'first' appears EXACTLY ONCE. If the buffer didn't
        # reset, the second log call would re-emit 'first' too and
        # we'd see it twice.
        self.assertEqual(
            row.calculation_log.count("first"), 1,
            f"Expected 'first' to appear exactly once (buffer should "
            f"have cleared after first .log() call); got "
            f"{row.calculation_log.count('first')} occurrences in "
            f"{row.calculation_log!r}.",
        )

    def test_15_5_row_coalescing_under_same_context(self):
        """15.5: Two .log() calls under the SAME model_logging_context
        produce exactly ONE CalculationLog row whose calculation_log
        carries both messages. Pins the get_or_create semantics of
        _persist_message.
        """
        root = self._make_root()
        with model_logging_context(root):
            LexLogger().add_text("alpha").log()
            LexLogger().add_text("beta").log()
        self.assert_total_rows(1)
        row = self.assert_log_row(root)
        self.assertIn("alpha", row.calculation_log)
        self.assertIn("beta", row.calculation_log)

    def test_15_6_forgetting_log_persists_nothing(self):
        """15.6: Building a chain without calling .log() leaves the DB
        untouched.
        """
        root = self._make_root()
        with model_logging_context(root):
            # Note: no .log()
            LexLogger().add_text("nothing here").add_heading("not saved")
        self.assert_total_rows(0)
        # Defensive: drain the singleton buffer so it doesn't leak to
        # the next test (LexLogger is a LexSingleton).
        LexLogger().content = []
