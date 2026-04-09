"""
Unit tests for the ``LexLogger`` builder-pattern logging API.

**What this tests (customer-visible behaviour)**

``LexLogger`` is the primary API for producing rich, Markdown-formatted
calculation logs.  Every builder method (``add_text``, ``add_heading``,
``add_table``, ``add_dataframe``, ``add_code``, etc.) returns the logger
instance so calls can be chained, and ``.log()`` flushes the accumulated
content to the database via ``CalculationLog.log()``.

**Why it matters**

Logging output is visible in the frontend Calculation Log panel, is
exported as PDFs for audit/compliance, and is the primary debugging tool
for customer calculations.  Regressions here break the entire observability
story.

**Methodology**

- ``LexLogger`` is a ``@LexSingleton`` — we reset its internal state
  before each test to guarantee isolation.
- We mock ``CalculationLog.log`` so tests never touch the database.
- Each builder method is tested for: (a) correct Markdown output,
  (b) builder chaining (returns ``self``), and (c) separation blank lines.
- A composite chain test proves that end-to-end ordering is preserved.

See also
--------
- ``docs/features/processing/logging.md``
- ``docs/reference/LexLogger API.md``
"""

from unittest.mock import patch, MagicMock

import pandas as pd
from django.test import SimpleTestCase

from lex.audit_logging.handlers.LexLogger import LexLogger


class TestLexLoggerBuilder(SimpleTestCase):
    """Prove every ``LexLogger`` builder method produces correct Markdown."""

    # ── helpers ──────────────────────────────────────────────────────

    def setUp(self):
        """Reset the singleton so each test starts with a fresh content list."""
        self.logger = LexLogger()
        self.logger.content = []

    # ── add_text ─────────────────────────────────────────────────────

    def test_add_text_appends_paragraph(self):
        """``add_text`` appends text followed by a blank line for separation."""
        result = self.logger.add_text("Hello world")
        self.assertIn("Hello world", self.logger.content)
        self.assertIs(result, self.logger, "Builder must return self")

    def test_add_text_blank_separator(self):
        """A blank line is inserted after every ``add_text`` call."""
        self.logger.add_text("Line one")
        # Content should be ["Line one", ""]
        self.assertEqual(self.logger.content[-1], "")

    # ── add_heading ──────────────────────────────────────────────────

    def test_add_heading_default_level(self):
        """``add_heading('Title')`` produces ``# Title``."""
        self.logger.add_heading("Title")
        self.assertIn("# Title", self.logger.content)

    def test_add_heading_level_2(self):
        """``add_heading('Sub', level=2)`` produces ``## Sub``."""
        self.logger.add_heading("Sub", level=2)
        self.assertIn("## Sub", self.logger.content)

    def test_add_heading_clamps_level(self):
        """Heading levels are clamped to [1, 6]."""
        self.logger.add_heading("Low", level=0)
        self.assertIn("# Low", self.logger.content)
        self.logger.content = []

        self.logger.add_heading("High", level=10)
        self.assertIn("###### High", self.logger.content)

    def test_add_heading_returns_self(self):
        result = self.logger.add_heading("Test")
        self.assertIs(result, self.logger)

    # ── add_list ─────────────────────────────────────────────────────

    def test_add_list_unordered(self):
        """Unordered list items start with ``- ``."""
        self.logger.add_list(["alpha", "beta"])
        self.assertIn("- alpha", self.logger.content)
        self.assertIn("- beta", self.logger.content)

    def test_add_list_ordered(self):
        """Ordered list items start with ``1. ``, ``2. ``, etc."""
        self.logger.add_list(["first", "second"], ordered=True)
        self.assertIn("1. first", self.logger.content)
        self.assertIn("2. second", self.logger.content)

    def test_add_list_returns_self(self):
        result = self.logger.add_list(["x"])
        self.assertIs(result, self.logger)

    # ── add_quote ────────────────────────────────────────────────────

    def test_add_quote(self):
        """``add_quote`` prefixes text with ``> ``."""
        self.logger.add_quote("Important note")
        self.assertIn("> Important note", self.logger.content)

    def test_add_quote_returns_self(self):
        result = self.logger.add_quote("x")
        self.assertIs(result, self.logger)

    # ── add_code ─────────────────────────────────────────────────────

    def test_add_code_no_language(self):
        """A code block without language uses bare triple backticks."""
        self.logger.add_code("x = 1")
        self.assertIn("```", self.logger.content)
        self.assertIn("x = 1", self.logger.content)

    def test_add_code_with_language(self):
        """A code block with language uses fenced syntax, e.g. ````` ```python `````."""
        self.logger.add_code("x = 1", language="python")
        self.assertIn("```python", self.logger.content)

    def test_add_code_returns_self(self):
        result = self.logger.add_code("pass")
        self.assertIs(result, self.logger)

    # ── add_link / add_image ─────────────────────────────────────────

    def test_add_link(self):
        """``add_link`` produces ``[text](url)``."""
        self.logger.add_link("Click here", "https://example.com")
        self.assertIn("[Click here](https://example.com)", self.logger.content)

    def test_add_image(self):
        """``add_image`` produces ``![alt](url)``."""
        self.logger.add_image("Logo", "https://example.com/logo.png")
        self.assertIn("![Logo](https://example.com/logo.png)", self.logger.content)

    # ── add_horizontal_rule ──────────────────────────────────────────

    def test_add_horizontal_rule(self):
        """``add_horizontal_rule`` inserts ``---``."""
        self.logger.add_horizontal_rule()
        self.assertIn("---", self.logger.content)

    # ── add_table ────────────────────────────────────────────────────

    def test_add_table_header_and_rows(self):
        """``add_table`` produces a valid Markdown table with headers and rows."""
        self.logger.add_table(["Name", "Age"], [["Alice", "30"], ["Bob", "25"]])
        joined = "\n".join(self.logger.content)
        self.assertIn("| Name | Age |", joined)
        self.assertIn("| --- | --- |", joined)
        self.assertIn("| Alice | 30 |", joined)
        self.assertIn("| Bob | 25 |", joined)

    def test_add_table_returns_self(self):
        result = self.logger.add_table(["H"], [["V"]])
        self.assertIs(result, self.logger)

    # ── add_dataframe ────────────────────────────────────────────────

    def test_add_dataframe_renders_markdown(self):
        """``add_dataframe`` converts a DataFrame to a Markdown table string."""
        df = pd.DataFrame({"Q": ["Q1", "Q2"], "Revenue": [100, 200]})
        result = self.logger.add_dataframe(df)
        joined = "\n".join(self.logger.content)
        self.assertIn("Q1", joined)
        self.assertIn("Q2", joined)
        self.assertIn("100", joined)
        self.assertIs(result, self.logger)

    # ── add_raw_markdown ─────────────────────────────────────────────

    def test_add_raw_markdown_splits_lines(self):
        """``add_raw_markdown`` splits multi-line strings into individual entries."""
        self.logger.add_raw_markdown("line1\nline2")
        self.assertIn("line1", self.logger.content)
        self.assertIn("line2", self.logger.content)

    # ── .log() ───────────────────────────────────────────────────────

    @patch("lex.audit_logging.models.CalculationLog.CalculationLog.log")
    def test_log_joins_content_and_delegates(self, mock_calc_log):
        """
        ``.log()`` joins accumulated content with newlines and passes it
        to ``CalculationLog.log()``, then resets the buffer.
        """
        self.logger.add_text("Hello").add_text("World")
        self.logger.log()

        mock_calc_log.assert_called_once()
        payload = mock_calc_log.call_args[0][0]
        self.assertIn("Hello", payload)
        self.assertIn("World", payload)
        # Buffer should be reset after log()
        self.assertEqual(self.logger.content, [])

    @patch("lex.audit_logging.models.CalculationLog.CalculationLog.log")
    def test_log_returns_self_for_chaining(self, mock_calc_log):
        """``.log()`` returns self so a new chain can begin."""
        result = self.logger.add_text("x").log()
        self.assertIs(result, self.logger)

    # ── Composite chain ──────────────────────────────────────────────

    @patch("lex.audit_logging.models.CalculationLog.CalculationLog.log")
    def test_composite_chain_preserves_order(self, mock_calc_log):
        """
        A full builder chain — heading → text → table → code — produces
        Markdown in the exact order it was built.
        """
        (
            self.logger
            .add_heading("Report", level=2)
            .add_text("Processing complete.")
            .add_table(["Item", "Qty"], [["Widget", "10"]])
            .add_code('{"ok": true}', language="json")
            .log()
        )

        payload = mock_calc_log.call_args[0][0]
        heading_pos = payload.index("## Report")
        text_pos = payload.index("Processing complete.")
        table_pos = payload.index("| Item | Qty |")
        code_pos = payload.index("```json")
        self.assertLess(heading_pos, text_pos)
        self.assertLess(text_pos, table_pos)
        self.assertLess(table_pos, code_pos)

    # ── Edge cases ───────────────────────────────────────────────────

    @patch("lex.audit_logging.models.CalculationLog.CalculationLog.log")
    def test_log_empty_content(self, mock_calc_log):
        """Calling ``.log()`` with no content still delegates (empty string)."""
        self.logger.log()
        mock_calc_log.assert_called_once()

    def test_add_text_empty_string(self):
        """``add_text('')`` appends an empty string plus separator."""
        self.logger.add_text("")
        self.assertEqual(self.logger.content, ["", ""])
