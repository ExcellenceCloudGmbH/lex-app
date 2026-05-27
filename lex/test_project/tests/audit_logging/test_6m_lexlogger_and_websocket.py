"""
Sub-cluster 6m — LexLogger markdown builder + WebSocketHandler dispatch.

PR-7 audit-utils tier, third batch. Two small handler files in
`lex/audit_logging/handlers/` that no other test currently pins:

* ``lex/audit_logging/handlers/LexLogger.py`` — the customer-facing
  Markdown builder used inside `CalculatedModel.calculate()` bodies.
  Pattern is `LexLogger().add_text(...).add_table(...).log()`. Each
  builder method appends to an internal `content` list and returns
  `self` so the chain composes; `log()` joins with newlines, calls
  `CalculationLog.log(final_output)`, then resets `content`. The
  output is rendered as Markdown in the per-record Calculation-Log
  Tab UI — every formatting drift would visibly break that view.

* ``lex/audit_logging/handlers/WebSocketHandler.py`` — Python-logging
  handler that re-broadcasts each emitted record to the matching
  Channels group (so the Calculation-Log Tab live-updates while the
  worker is still running). Three contracts: (a) records without a
  `calculation_record` extra are silently dropped (no broadcast =
  no leak), (b) records WITH it are dispatched as
  `{"type": "calculation_log_real_time", "payload": <formatted>}`
  to group `f"{calc_record}"`, (c) any exception inside emit is
  swallowed via `self.handleError(record)` so logging never crashes
  the calling thread.

Note: ``ConsoleHandler.py`` is in the EXCLUDE list (NOTES_TODO §2)
and intentionally not tested.

LexLogger is `@LexSingleton`, so `content` persists across the whole
process — every test resets it in `setUp()` to keep scenarios
order-independent.

All scenarios are `SimpleTestCase` (no DB, no Channels broker).
Channel-layer dispatch is observed via `mock.patch` on
`sync_channel_group_send`. CalculationLog persistence is short-circuited
by patching `CalculationLog.log` so the builder's join logic is
asserted on the captured argument.

Scenario IDs 6.123 – 6.140.

Run with:
    lex test lex.test_project.tests.audit_logging.test_6m_lexlogger_and_websocket \\
        --verbosity=2 --noinput --keepdb
"""
from __future__ import annotations

import logging
from types import SimpleNamespace
from unittest import mock

import pandas as pd
from django.test import SimpleTestCase

from lex.audit_logging.handlers.LexLogger import LexLogger, LexLogLevel
from lex.audit_logging.handlers.WebSocketHandler import WebSocketHandler

import pytest

pytestmark = pytest.mark.audit_logging


# ---------------------------------------------------------------------------
# 1) LexLogLevel constants — pinned so the public log-level surface stays stable
# ---------------------------------------------------------------------------


class TestCluster06m_LexLogLevelConstants(SimpleTestCase):
    """`LexLogLevel` mirrors stdlib `logging` numeric levels for the
    customer-facing severity scale. A regression that swapped values
    would silently re-route every log line to the wrong severity bucket
    in the operator dashboard."""

    def test_6_123_log_levels_match_stdlib_logging(self):
        self.assertEqual(LexLogLevel.DEBUG, logging.DEBUG)
        self.assertEqual(LexLogLevel.INFO, logging.INFO)
        self.assertEqual(LexLogLevel.WARNING, logging.WARNING)
        self.assertEqual(LexLogLevel.ERROR, logging.ERROR)
        self.assertEqual(LexLogLevel.CRITICAL, logging.CRITICAL)
        self.assertEqual(
            LexLogLevel.VERBOSE, 5,
            "VERBOSE is a Lex-specific level below DEBUG (5 < 10) — operators "
            "filter on the numeric value, drift to e.g. 0 would let DEBUG "
            "filtering accidentally hide VERBOSE output too.",
        )


# ---------------------------------------------------------------------------
# 2) LexLogger builder methods — markdown shape per method
# ---------------------------------------------------------------------------


class TestCluster06m_LexLoggerBuilders(SimpleTestCase):
    """Each builder appends to `content` and returns `self` for chaining.
    Tests pin the markdown shape — drift here would visibly break the
    per-record Calculation-Log Tab rendering."""

    def setUp(self):
        # @LexSingleton means content persists across the process —
        # always reset before asserting on shape so order-independence
        # holds.
        self.logger = LexLogger()
        self.logger.content = []

    def _content(self):
        return self.logger.content

    def test_6_124_add_text_appends_paragraph_and_separator(self):
        ret = self.logger.add_text("Hello, world.")
        self.assertIs(ret, self.logger, "Builder must return self for chaining.")
        self.assertEqual(
            self._content(), ["Hello, world.", ""],
            "add_text appends the text + a blank separator line so consecutive "
            "paragraphs render as separate paragraphs in Markdown.",
        )

    def test_6_125_add_heading_clamps_level_between_1_and_6(self):
        # Level 0 → clamp to 1; level 99 → clamp to 6. A regression that
        # let the raw `#` count through would produce malformed Markdown
        # that some renderers display as literal '#####...' text.
        self.logger.add_heading("Top", level=0)
        self.logger.add_heading("Sub", level=99)
        self.logger.add_heading("Mid", level=3)
        self.assertEqual(
            self._content(),
            ["# Top", "", "###### Sub", "", "### Mid", ""],
        )

    def test_6_126_add_list_ordered_vs_unordered(self):
        # Ordered → "1. foo / 2. bar"; unordered → "- foo / - bar".
        # Drift here breaks both UX surfaces (numbered task lists,
        # bulleted observations).
        self.logger.add_list(["foo", "bar"], ordered=False)
        self.logger.add_list(["foo", "bar"], ordered=True)
        self.assertEqual(
            self._content(),
            ["- foo", "- bar", "", "1. foo", "2. bar", ""],
        )

    def test_6_127_add_quote_prefixes_with_blockquote_marker(self):
        self.logger.add_quote("noted")
        self.assertEqual(self._content(), ["> noted", ""])

    def test_6_128_add_code_uses_fenced_block_with_optional_language(self):
        self.logger.add_code("print(1)", language="python")
        self.assertEqual(
            self._content(),
            ["```python", "print(1)", "```", ""],
        )
        self.logger.content = []
        # Empty language → still wraps in triple backticks (renderer's
        # default).
        self.logger.add_code("plain")
        self.assertEqual(self._content(), ["```", "plain", "```", ""])

    def test_6_129_add_link_and_image_use_markdown_syntax(self):
        self.logger.add_link("docs", "https://example.com")
        self.logger.add_image("alt", "https://example.com/x.png")
        self.assertEqual(
            self._content(),
            [
                "[docs](https://example.com)", "",
                "![alt](https://example.com/x.png)", "",
            ],
        )

    def test_6_130_add_horizontal_rule_appends_three_dashes(self):
        self.logger.add_horizontal_rule()
        self.assertEqual(self._content(), ["---", ""])

    def test_6_131_add_table_renders_header_separator_and_rows(self):
        self.logger.add_table(
            headers=["a", "b"],
            rows=[["1", "2"], ["3", "4"]],
        )
        self.assertEqual(
            self._content(),
            [
                "| a | b |",
                "| --- | --- |",
                "| 1 | 2 |",
                "| 3 | 4 |",
                "",
            ],
            "Table shape is fixed — '| col | col |' with a '| --- | --- |' "
            "separator. Skipping the separator row produces text not a table; "
            "missing the leading/trailing pipe breaks GitHub-flavoured Markdown.",
        )

    def test_6_132_add_dataframe_renders_via_to_markdown(self):
        df = pd.DataFrame({"a": [1, 2], "b": [3, 4]})
        self.logger.add_dataframe(df)
        # to_markdown output is one string — must land as a single content
        # entry plus a trailing blank separator. Substring asserts to stay
        # robust to small whitespace formatting between pandas versions.
        self.assertEqual(len(self._content()), 2)
        rendered = self._content()[0]
        # pandas pads the column names with spaces ("|   a |   b |") so
        # match on the column letter itself, not the leading pipe.
        self.assertRegex(rendered, r"\|\s*a\s*\|")
        self.assertRegex(rendered, r"\|\s*b\s*\|")
        self.assertEqual(self._content()[1], "")

    def test_6_133_add_raw_markdown_splits_on_newlines(self):
        # Customer hands a pre-built markdown blob — builder must split
        # on newlines so subsequent join() in log() doesn't double-newline
        # everything.
        self.logger.add_raw_markdown("line1\nline2\nline3")
        self.assertEqual(
            self._content(),
            ["line1", "line2", "line3", ""],
            "Each newline-delimited line becomes its own content entry; "
            "trailing blank separator appended once at the end.",
        )

    def test_6_134_chained_builders_compose_in_call_order(self):
        # The customer-facing pattern is
        # `LexLogger().add_heading(...).add_text(...).add_table(...).log()`.
        # Drift in either the chaining contract (must return self) or the
        # append order would break that one-liner.
        result = (
            self.logger
            .add_heading("Title", level=2)
            .add_text("Body.")
            .add_horizontal_rule()
        )
        self.assertIs(result, self.logger)
        self.assertEqual(
            self._content(),
            ["## Title", "", "Body.", "", "---", ""],
        )


# ---------------------------------------------------------------------------
# 3) LexLogger.log() — joins, persists, and resets
# ---------------------------------------------------------------------------


class TestCluster06m_LexLoggerLog(SimpleTestCase):
    """`log()` is the terminal call — it joins `content` with newlines,
    forwards to `CalculationLog.log()`, and clears `content` so the next
    chain starts clean. A regression that skipped the reset would
    silently duplicate every prior log inside subsequent `.log()` calls
    in the same process (singleton-leakage class of bug)."""

    def setUp(self):
        self.logger = LexLogger()
        self.logger.content = []

    def test_6_135_log_joins_content_with_newlines_and_calls_calculation_log_log(self):
        self.logger.add_text("hello").add_text("world")
        with mock.patch(
            "lex.audit_logging.handlers.LexLogger.CalculationLog.log"
        ) as calc_log:
            ret = self.logger.log()
        self.assertIs(ret, self.logger)
        # Single positional arg = the joined string.
        calc_log.assert_called_once()
        joined = calc_log.call_args.args[0]
        self.assertIn(
            "hello", joined,
        )
        self.assertIn("world", joined)
        self.assertIn(
            "\n", joined,
            "log() must join with newlines — without that the renderer sees "
            "everything on one line.",
        )

    def test_6_136_log_resets_content_after_persisting(self):
        # The "next chain in same process starts clean" contract.
        # Without this, every singleton consumer would see everyone
        # else's accumulated lines on their first add_*() call.
        self.logger.add_text("first")
        with mock.patch(
            "lex.audit_logging.handlers.LexLogger.CalculationLog.log"
        ):
            self.logger.log()
        self.assertEqual(
            self.logger.content, [],
            "After log(), content MUST be empty — leaving it would re-log "
            "every prior line on the next .log() call (singleton leak).",
        )


# ---------------------------------------------------------------------------
# 4) WebSocketHandler — emit dispatch + safety contracts
# ---------------------------------------------------------------------------


def _make_record(msg="hi", **extras):
    """Build a logging.LogRecord with a formatted message and optional
    custom attributes (e.g. calculation_record / calculationId) attached
    via __dict__ (the handler reads them from there)."""
    record = logging.LogRecord(
        name="lex.test", level=logging.INFO, pathname=__file__, lineno=1,
        msg=msg, args=None, exc_info=None,
    )
    record.__dict__.update(extras)
    return record


class TestCluster06m_WebSocketHandlerEmit(SimpleTestCase):
    """`WebSocketHandler.emit` re-broadcasts each log record to the
    Channels group named by the record's `calculation_record` extra.
    Three contracts pinned: missing-extra → silent no-op (no broadcast),
    present extra → dispatch with documented payload shape, exception
    inside emit → swallowed via `handleError` (logging must never crash
    the calling thread)."""

    def setUp(self):
        self.handler = WebSocketHandler()
        # Minimal formatter so handler.format() returns the raw message.
        self.handler.setFormatter(logging.Formatter("%(message)s"))

    def test_6_137_no_calculation_record_extra_is_silent_no_op(self):
        # Without `calculation_record` the handler must skip dispatch
        # entirely — broadcasting to "" or None would either 500 the
        # channel layer or leak to the wrong group.
        record = _make_record("orphan log")
        with mock.patch(
            "lex.audit_logging.handlers.WebSocketHandler.sync_channel_group_send"
        ) as send:
            self.handler.emit(record)
        send.assert_not_called()

    def test_6_138_dispatches_documented_payload_shape_to_named_group(self):
        record = _make_record("hello", calculation_record="calc-42", calculationId="42")
        with mock.patch(
            "lex.audit_logging.handlers.WebSocketHandler.sync_channel_group_send"
        ) as send:
            self.handler.emit(record)
        send.assert_called_once_with(
            "calc-42",
            {"type": "calculation_log_real_time", "payload": "hello"},
        )

    def test_6_139_swallows_exception_via_handle_error(self):
        # If the channel layer is down, the handler must not propagate
        # — Python logging contract is "never crash the caller". A
        # regression that let the exception leak would crash whatever
        # thread emitted the log line (worker, web request, calc body).
        record = _make_record("boom", calculation_record="calc-1")
        with mock.patch(
            "lex.audit_logging.handlers.WebSocketHandler.sync_channel_group_send",
            side_effect=RuntimeError("channel down"),
        ), mock.patch.object(self.handler, "handleError") as handle_err:
            self.handler.emit(record)  # must NOT raise
        handle_err.assert_called_once_with(record)

    def test_6_140_falsy_calculation_record_is_treated_as_missing(self):
        # Empty string / None / 0 — all falsy, all must short-circuit.
        # Drift to truthiness check that allows e.g. "" through would
        # broadcast to a group named "" which Channels rejects with an
        # opaque error mid-request.
        for falsy_value in ("", None, 0, False):
            with self.subTest(value=falsy_value):
                record = _make_record("x", calculation_record=falsy_value)
                with mock.patch(
                    "lex.audit_logging.handlers.WebSocketHandler.sync_channel_group_send"
                ) as send:
                    self.handler.emit(record)
                send.assert_not_called()


