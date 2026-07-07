"""Cluster 15g — object-less heading frames in the calculation log tree.

Intent: `model_logging_context` maintains the parent/child log hierarchy shown
in the frontend execution tree (docs/features/processing/logging.md). Heading
frames extend that intent: a plain string argument creates a table-of-contents
style node that groups nested LexLogger output under a title WITHOUT needing a
backing model instance. A regression here silently flattens or mis-parents
customer log trees, or resurrects rows for sections that never logged.
Cluster 15g — scenarios 15.22–15.31. Type: I.
Covers: lex/audit_logging/utils/ModelContext.py,
        lex/audit_logging/utils/ContextResolver.py,
        lex/audit_logging/models/CalculationLog.py,
        lex/audit_logging/utils/DataModels.py.
Run: python -m lex pytest lex/test_project/tests/calculation_logging/test_15g_heading_context.py -v
"""
from __future__ import annotations

import pytest

from lex.audit_logging.handlers.LexLogger import LexLogger
from lex.audit_logging.models.CalculationLog import CalculationLog
from lex.audit_logging.utils.ContextResolver import ContextResolver
from lex.audit_logging.utils.ModelContext import (
    LogHeading,
    _model_context,
    model_logging_context,
)

from . import _CalcLogTestCase
from .models import LogRootCalc

pytestmark = pytest.mark.calculation_logging


class _HeadingTestCase(_CalcLogTestCase):
    """Shared helpers for the 15g scenarios."""

    def _make_root(self, name: str = "r") -> LogRootCalc:
        # Same pattern as 15a: bulk_create skips save() so the calculation
        # state machine never fires — these scenarios control LexLogger
        # invocation manually.
        root = LogRootCalc(name=name, child_mode="log_only", units_csv="")
        LogRootCalc.objects.bulk_create([root])
        return LogRootCalc.objects.get(name=name)

    def _heading_rows(self, title: str) -> list[CalculationLog]:
        return list(
            CalculationLog.objects.filter(
                calculationId=self.calc_id, heading=title
            )
        )

    def assert_heading_row(self, title: str, *, parent=None) -> CalculationLog:
        """Assert exactly one heading row with `title` exists and return it.

        Heading rows must carry the title in `heading` and have NO generic
        object reference — content_type and object_id are both NULL.
        """
        rows = self._heading_rows(title)
        self.assertEqual(
            len(rows), 1,
            f"Expected exactly 1 heading row titled {title!r}, got {len(rows)}.",
        )
        row = rows[0]
        self.assertIsNone(
            row.content_type_id,
            f"Heading row {title!r} must not reference a content_type.",
        )
        self.assertIsNone(
            row.object_id,
            f"Heading row {title!r} must not reference an object_id.",
        )
        if parent is None:
            self.assertIsNone(
                row.parent_log_id,
                f"Expected heading {title!r} to be a root node, "
                f"got parent_log_id={row.parent_log_id}.",
            )
        else:
            self.assertEqual(
                row.parent_log_id, parent.id,
                f"Expected heading {title!r} parented to row {parent.id}, "
                f"got {row.parent_log_id}.",
            )
        return row


class TestCluster15g_HeadingFrames(_HeadingTestCase):
    """Cluster 15g: string arguments become LogHeading stack frames."""

    def test_15_22_string_pushes_heading_frame(self):
        """
        Scenario 15.22: a plain string pushes a LogHeading frame that pops on exit.
        Given: an empty model context stack
        When: entering model_logging_context("Data preparation")
        Then: the top frame is a LogHeading carrying the title, and the stack
              is restored when the block exits
        """
        stack = _model_context.get()["model_context"]._stack
        depth_before = len(stack)
        with model_logging_context("Data preparation"):
            frame = stack[-1]
            self.assertIsInstance(
                frame, LogHeading,
                f"Expected a LogHeading frame on top of the stack, got {type(frame)}.",
            )
            self.assertEqual(
                frame.title, "Data preparation",
                "The LogHeading frame must carry the string as its title.",
            )
        self.assertEqual(
            len(stack), depth_before,
            "The heading frame must be popped when the with-block exits.",
        )

    def test_15_23_invalid_type_still_rejected(self):
        """
        Scenario 15.23: non-model, non-string arguments still raise TypeError.
        Given: an int (neither a Django model instance nor a heading string)
        When: entering model_logging_context(42)
        Then: TypeError is raised and nothing is pushed onto the stack
        """
        stack = _model_context.get()["model_context"]._stack
        depth_before = len(stack)
        with self.assertRaises(TypeError, msg="An int must be rejected."):
            with model_logging_context(42):
                pass  # pragma: no cover — must not be reached
        self.assertEqual(
            len(stack), depth_before,
            "A rejected argument must leave the stack untouched.",
        )


class TestCluster15g_HeadingPersistence(_HeadingTestCase):
    """Cluster 15g: heading rows persist lazily and parent correctly."""

    def test_15_24_lazy_heading_row_on_first_log(self):
        """
        Scenario 15.24: the heading row is created on the first LexLogger flush
        inside the block, parented to the enclosing model's row.
        Given: a root model context wrapping a heading context
        When: LexLogger logs inside the heading block
        Then: exactly one heading row exists with heading set, content_type and
              object_id NULL, parent_log = the root model's row, and the row
              carries the logged markdown
        """
        root = self._make_root()
        with model_logging_context(root):
            LexLogger().add_text("root text").log()
            with model_logging_context("Phase 1"):
                LexLogger().add_text("phase text").log()

        root_row = self.assert_log_row(root, parent=None, contains="root text")
        phase = self.assert_heading_row("Phase 1", parent=root_row)
        self.assertIn(
            "phase text", phase.calculation_log,
            "The heading row must accumulate the log text emitted inside its block.",
        )

    def test_15_25_silent_heading_persists_nothing(self):
        """
        Scenario 15.25: a heading block that never logs produces no row.
        Given: a root model context wrapping a heading context
        When: the heading block exits without any LexLogger flush
        Then: no CalculationLog row exists for the heading title
        """
        root = self._make_root()
        with model_logging_context(root):
            LexLogger().add_text("root text").log()
            with model_logging_context("Silent phase"):
                pass

        self.assertEqual(
            self._heading_rows("Silent phase"), [],
            "A heading whose block emits no logs must not appear in the tree.",
        )

    def test_15_26_heading_chain_created_when_inner_logs_first(self):
        """
        Scenario 15.26: logging deep inside nested headings materializes the
        whole unpersisted heading chain, outermost first.
        Given: root model → heading "Outer" → heading "Inner", nothing logged yet
        When: LexLogger flushes inside "Inner" only
        Then: both heading rows exist, chained root row ← Outer ← Inner; Outer
              exists purely as a TOC node with no log text of its own
        """
        root = self._make_root()
        with model_logging_context(root):
            LexLogger().add_text("root text").log()
            with model_logging_context("Outer"):
                with model_logging_context("Inner"):
                    LexLogger().add_text("deep first").log()

        root_row = self.assert_log_row(root, parent=None)
        outer = self.assert_heading_row("Outer", parent=root_row)
        inner = self.assert_heading_row("Inner", parent=outer)
        self.assertIn(
            "deep first", inner.calculation_log,
            "The innermost heading row must carry the flushed text.",
        )
        self.assertEqual(
            (outer.calculation_log or "").strip(), "",
            "A chain-created heading that never logged directly must stay an "
            "empty TOC node.",
        )

    def test_15_27_reentering_same_heading_reuses_node(self):
        """
        Scenario 15.27: re-entering the same title under the same parent reuses
        the node instead of duplicating it.
        Given: two separate with-blocks titled "Phase 1" under the same root
        When: both blocks log via LexLogger
        Then: exactly one "Phase 1" row exists containing both messages
        """
        root = self._make_root()
        with model_logging_context(root):
            LexLogger().add_text("root text").log()
            with model_logging_context("Phase 1"):
                LexLogger().add_text("first visit").log()
            with model_logging_context("Phase 1"):
                LexLogger().add_text("second visit").log()

        root_row = self.assert_log_row(root, parent=None)
        phase = self.assert_heading_row("Phase 1", parent=root_row)
        self.assertIn(
            "first visit", phase.calculation_log,
            "The reused heading row must keep the first block's text.",
        )
        self.assertIn(
            "second visit", phase.calculation_log,
            "The reused heading row must append the second block's text.",
        )

    def test_15_28_same_title_different_parents_distinct(self):
        """
        Scenario 15.28: the same title under different parents yields distinct nodes.
        Given: two root models, each wrapping a heading titled "Validation"
        When: both heading blocks log via LexLogger
        Then: two "Validation" rows exist with different parent_log ids
        """
        a = self._make_root("a")
        b = self._make_root("b")
        for m in (a, b):
            with model_logging_context(m):
                LexLogger().add_text(f"start {m.name}").log()
                with model_logging_context("Validation"):
                    LexLogger().add_text(f"validating {m.name}").log()

        rows = self._heading_rows("Validation")
        self.assertEqual(
            len(rows), 2,
            f"Expected one 'Validation' node per parent, got {len(rows)}.",
        )
        self.assertEqual(
            len({r.parent_log_id for r in rows}), 2,
            "The two 'Validation' nodes must hang off different parents.",
        )

    def test_15_29_model_nested_under_heading(self):
        """
        Scenario 15.29: a model context nested inside a heading parents the
        model's row to the heading row.
        Given: root model → heading "Phase 3" → second model context
        When: LexLogger flushes inside the inner model context
        Then: the inner model's row has parent_log = the heading row, and the
              heading row (lazily created) hangs off the root row
        """
        root = self._make_root()
        nested = self._make_root("nested")
        with model_logging_context(root):
            LexLogger().add_text("root text").log()
            with model_logging_context("Phase 3"):
                with model_logging_context(nested):
                    LexLogger().add_text("nested model text").log()

        root_row = self.assert_log_row(root, parent=None)
        phase = self.assert_heading_row("Phase 3", parent=root_row)
        nested_row = self.assert_log_row(
            nested, parent=phase, contains="nested model text"
        )
        self.assertIsNone(
            nested_row.heading,
            "A model-backed row must never carry a heading title.",
        )

    def test_15_30_routing_records_skip_headings(self):
        """
        Scenario 15.30: cache/WebSocket routing records come from the nearest
        real model — headings only shape the tree, never the routing.
        Given: root model → heading "Phase" as the current context
        When: ContextResolver.resolve() runs
        Then: current_record and root_record both point at the root model;
              current_model/content_type are None (the heading node itself has
              no object identity)
        """
        root = self._make_root()
        with model_logging_context(root):
            with model_logging_context("Phase"):
                info = ContextResolver.resolve()

        expected_record = f"logrootcalc_{root.pk}"
        self.assertEqual(
            info.current_record, expected_record,
            "Live-log routing must target the nearest real model, not the heading.",
        )
        self.assertEqual(
            info.root_record, expected_record,
            "Root routing must skip heading frames.",
        )
        self.assertIsNone(
            info.current_model,
            "With a heading on top of the stack the current node is the "
            "heading itself, so current_model must be None.",
        )
        self.assertIsNone(
            info.content_type,
            "A heading frame has no content_type.",
        )

    def test_15_31_str_surfaces_heading_as_tree_title(self):
        """
        Scenario 15.31: the tree serializer titles nodes with str(row) — heading
        rows must surface their title, model rows keep the default string.
        Given: a persisted heading row and a persisted model row
        When: str() is taken of each
        Then: the heading row renders its title; the model row renders the
              Django default the frontend recognizes and relabels
        """
        root = self._make_root()
        with model_logging_context(root):
            LexLogger().add_text("root text").log()
            with model_logging_context("Data preparation"):
                LexLogger().add_text("phase text").log()

        root_row = self.assert_log_row(root)
        phase = self.assert_heading_row("Data preparation", parent=root_row)
        self.assertEqual(
            str(phase), "Data preparation",
            "Heading rows must render their title as the tree node label.",
        )
        self.assertEqual(
            str(root_row), f"CalculationLog object ({root_row.pk})",
            "Model-backed rows must keep the default string the frontend "
            "relabels as 'Consolidated log' / 'Section N'.",
        )
