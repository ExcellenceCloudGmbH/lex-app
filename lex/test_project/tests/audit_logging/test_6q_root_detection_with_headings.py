"""Cluster 6q — root-calculation detection ignores heading frames.

Intent: `_is_root_calculation` decides whether an instance owns the top-level
AuditLog for a calculation by comparing it against the model context stack.
Heading frames (`model_logging_context("...")`) are pure presentation — TOC
nodes in the log tree — so they must never change which calculation is
considered the root. A regression here would make a heading-wrapped root
calculation look nested (losing its terminal audit status) or promote a
nested child to root.
Cluster 6q — scenarios 6.117–6.118. Type: U.
Covers: lex/audit_logging/utils/calculation_audit.py (_is_root_calculation).
Run: python -m lex pytest lex/test_project/tests/audit_logging/test_6q_root_detection_with_headings.py -v
"""
from __future__ import annotations

import pytest
from django.test import SimpleTestCase

from lex.audit_logging.models.CalculationLog import CalculationLog
from lex.audit_logging.utils.calculation_audit import _is_root_calculation
from lex.audit_logging.utils.ModelContext import LogHeading, ModelContext

pytestmark = pytest.mark.audit_logging


class TestCluster06q_RootDetectionWithHeadings(SimpleTestCase):
    """Cluster 6q: heading frames are transparent to root detection."""

    # Unsaved instances with explicit pks — _is_root_calculation compares
    # type + pk only, so no DB is needed (type U).
    def setUp(self):
        self.root = CalculationLog(pk=1)
        self.child = CalculationLog(pk=2)

    def test_6_117_headings_do_not_demote_the_root(self):
        """
        Scenario 6.117: heading frames around or above the root model leave its
        root-calculation status intact.
        Given: stacks [Heading, root], [root, Heading] and [root] for instance=root
        When: _is_root_calculation(root) runs against each stack
        Then: all three report True — presentation frames never demote the root
        """
        plain = ModelContext([self.root])
        heading_below = ModelContext([LogHeading("Setup"), self.root])
        heading_on_top = ModelContext([self.root, LogHeading("Phase 1")])

        self.assertTrue(
            _is_root_calculation(self.root, model_context=plain),
            "Baseline: the sole model on the stack is the root calculation.",
        )
        self.assertTrue(
            _is_root_calculation(self.root, model_context=heading_below),
            "A heading opened before the root model must not demote it.",
        )
        self.assertTrue(
            _is_root_calculation(self.root, model_context=heading_on_top),
            "A heading opened inside the root's own calculation must not "
            "demote it.",
        )

    def test_6_118_nested_model_below_heading_stays_non_root(self):
        """
        Scenario 6.118: a model nested underneath a heading is still detected
        as a nested (non-root) calculation.
        Given: stack [root, Heading, child]
        When: _is_root_calculation(child) runs
        Then: False — the heading between root and child must not hide the
              nesting; and the root itself is also non-root while the child is
              the current model
        """
        stack = ModelContext([self.root, LogHeading("Phase 1"), self.child])

        self.assertFalse(
            _is_root_calculation(self.child, model_context=stack),
            "A child below a heading is still a nested calculation.",
        )
        self.assertFalse(
            _is_root_calculation(self.root, model_context=stack),
            "While a child is the current model, the root instance is not the "
            "currently-executing calculation.",
        )
