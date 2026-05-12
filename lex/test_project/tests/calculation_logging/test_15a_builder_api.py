"""
Cluster 15a — LexLogger builder API and content shape.

End-to-end counterpart to the unit-level Cluster 6m: confirms each
``LexLogger`` builder method's markdown survives the
``LexLogger -> CalculationLog -> DB`` round-trip when called inside a
real ``CalculationModel.calculate()``.
"""
from __future__ import annotations

from . import _CalcLogTestCase
from .models import LogRootCalc


class TestCluster15a_BuilderAPI(_CalcLogTestCase):
    """Per-builder-method shape persisted to the CalculationLog row."""

    def test_smoke_setup_works(self):
        """Sanity: setUp seeded an AuditLog and a calculation_id."""
        self.assertTrue(self.calc_id.startswith("calc_15_"))
        self.assertEqual(self.audit_log.calculation_id, self.calc_id)

    # -- 15.1 ----------------------------------------------------------
    def test_15_1_root_add_text_persists_with_null_parent(self):
        """A single ``add_text().log()`` from a root calc writes one
        CalculationLog row whose ``parent_log_id`` is NULL (root has no
        parent on the LIFO model-context stack).
        """
        root = LogRootCalc(name="r1", child_mode="log_only")
        self.drive_root(root)

        self.assert_log_row(root, parent=None, contains="root r1")
        self.assert_total_rows(1)

    # -- 15.2 ----------------------------------------------------------
    def test_15_2_chained_builders_all_persist_in_one_row(self):
        """Every ``add_*`` builder method's markdown survives the round
        trip into a single CalculationLog row when chained before one
        ``.log()`` call.
        """
        root = LogRootCalc(name="ks", child_mode="kitchen_sink")
        self.drive_root(root)

        row = self.assert_log_row(root, parent=None)
        body = row.calculation_log

        # add_heading(level=2)
        self.assertIn("## KS-Heading", body)
        # add_text
        self.assertIn("ks-text", body)
        # add_list(ordered=True) — 1. / 2. prefix
        self.assertIn("1. alpha", body)
        self.assertIn("2. beta", body)
        # add_quote
        self.assertIn("> ks-quote", body)
        # add_code(language="python") — fenced block
        self.assertIn("```python", body)
        self.assertIn("x = 1", body)
        # add_link
        self.assertIn("[ks-link](https://example.test/ks)", body)
        # add_image
        self.assertIn("![ks-alt](https://example.test/img.png)", body)
        # add_horizontal_rule
        self.assertIn("---", body)
        # add_table — header + separator row
        self.assertIn("| h1 | h2 |", body)
        self.assertIn("| --- | --- |", body)
        self.assertIn("| v1 | v2 |", body)
        # add_dataframe — pandas to_markdown emits "col" header
        self.assertIn("col", body)
        self.assertIn("7", body)
        # add_raw_markdown — passes through as-is
        self.assertIn("**ks-bold**", body)

        # Plus the leading "root ks" from calculate()'s opening line.
        self.assertIn("root ks", body)
        self.assert_total_rows(1)

    # -- 15.3 ----------------------------------------------------------
    def test_15_3_two_log_calls_append_to_same_row(self):
        """Two ``.log()`` calls inside the same model context dedup to
        one CalculationLog row (per ``_get_or_create_locked``) and
        append both messages to ``calculation_log``.
        """
        root = LogRootCalc(name="dl", child_mode="double_log")
        self.drive_root(root)

        row = self.assert_log_row(root, parent=None)
        self.assertIn("first-line", row.calculation_log)
        self.assertIn("second-line", row.calculation_log)
        self.assert_total_rows(1)

    # -- 15.4 ----------------------------------------------------------
    def test_15_4_singleton_content_resets_between_log_calls(self):
        """``LexLogger.log()`` resets ``self.content = []`` after
        flushing — so the second ``.log()`` only emits the second
        message, not first+second concatenated.
        """
        root = LogRootCalc(name="dl2", child_mode="double_log")
        self.drive_root(root)

        body = self.assert_log_row(root, parent=None).calculation_log
        # If the singleton's buffer leaked, "first-line" would appear
        # twice (once from the first .log(), once again as a prefix to
        # the second .log()'s message).
        self.assertEqual(
            body.count("first-line"), 1,
            f"Expected 'first-line' to appear exactly once, got "
            f"{body.count('first-line')}. Body: {body!r}",
        )
        self.assertEqual(
            body.count("second-line"), 1,
            f"Expected 'second-line' to appear exactly once, got "
            f"{body.count('second-line')}. Body: {body!r}",
        )

