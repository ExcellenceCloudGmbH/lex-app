"""
Cluster 1u: setup-with-ai MCP mode contract.

Intent
------

``lex setup-with-ai`` is the first-touch configuration surface for the
Lex MCP server. If its mode picker drifts behind the actual server mode
registry, users can select a workflow in ``lex ai-dashboard`` that the
initial setup screen silently drops back to ``forward``. That is a
customer-visible configuration bug: the wrong tool surface boots even
though the user picked a different mode.

This batch pins the four public contracts Session 80 changes:

1. the mode normalizer accepts every current lex-mcp-local mode name and
   falls back safely for unknown values;
2. unified-server ``mcp.json`` args carry the chosen mode;
3. legacy-wrapper args still forward the chosen mode positionally;
4. the setup form renders a selectable card for every supported mode.

All scenarios are pure-Python and run without DB, browser, or subprocess
work. Scenario numbering picks up at **1.171** (1t ended at 1.170).
"""

from __future__ import annotations

from pathlib import Path
from unittest import TestCase, mock

import pytest

from lex.tools import setup_with_ai

pytestmark = pytest.mark.init


class TestCluster01u_SetupWithAiMcpModes(TestCase):
    """Cluster 1u: keep setup-with-ai aligned with the MCP server modes."""

    def test_1_171_normalize_accepts_every_supported_mode(self):
        """Scenario 1.171: every supported mode round-trips unchanged.

        Given: the current lex-mcp-local mode registry
        When: setup-with-ai normalizes a submitted mode value
        Then: every supported mode is accepted verbatim
        """
        for mode in setup_with_ai.SUPPORTED_MCP_MODES:
            with self.subTest(mode=mode):
                self.assertEqual(
                    setup_with_ai.normalize_mcp_mode(mode),
                    mode,
                    f"supported mode {mode!r} must round-trip unchanged",
                )

    def test_1_172_normalize_defaults_unknown_values_to_forward(self):
        """Scenario 1.172: unknown or blank mode values fall back safely.

        Given: malformed or stale mode values
        When: setup-with-ai normalizes them
        Then: the fallback is the documented default ``forward``
        """
        for raw in (None, "", "  ", "router_master", "FORWARDER"):
            with self.subTest(raw=raw):
                self.assertEqual(
                    setup_with_ai.normalize_mcp_mode(raw),
                    setup_with_ai.DEFAULT_LEX_MCP_MODE,
                    f"unsupported value {raw!r} must fall back to forward",
                )

    @mock.patch("lex.tools.setup_with_ai._has_unified_mcp_entry_point", return_value=True)
    def test_1_173_unified_server_args_include_selected_mode(self, _has_unified):
        """Scenario 1.173: unified ``lex_mcp.server`` args carry the mode.

        Given: a lex-mcp-local install with the unified entry point
        When: setup-with-ai writes the ``mcp.json`` server args
        Then: ``--mode <selected>`` is present for newer servers
        """
        args = setup_with_ai.resolve_mcp_server_args(
            Path("/tmp/python"),
            "review",
        )
        self.assertEqual(
            args,
            ["-m", "lex_mcp.server", "--mode", "review"],
            "unified-server args must carry the selected MCP mode",
        )

    @mock.patch("lex.tools.setup_with_ai.resolve_wrapper_script_path", return_value=Path("/tmp/wrapper_mcp.py"))
    @mock.patch("lex.tools.setup_with_ai._has_unified_mcp_entry_point", return_value=False)
    def test_1_174_legacy_wrapper_args_still_forward_selected_mode(
        self,
        _has_unified,
        _resolve_wrapper,
    ):
        """Scenario 1.174: legacy wrapper path forwards the selected mode.

        Given: an older lex-mcp-local install without ``lex_mcp.server``
        When: setup-with-ai builds the fallback wrapper command
        Then: the chosen mode is passed as the positional wrapper argument
        """
        args = setup_with_ai.resolve_mcp_server_args(
            Path("/tmp/python"),
            "mvp_completion",
        )
        self.assertEqual(
            args,
            ["/tmp/wrapper_mcp.py", "mvp_completion"],
            "legacy wrapper args must still forward the selected MCP mode",
        )

    def test_1_175_setup_form_renders_all_supported_mode_cards(self):
        """Scenario 1.175: the setup form exposes every supported mode.

        Given: the initial setup page HTML
        When: the form is rendered
        Then: each supported mode has a selectable card and the hidden
        payload defaults to the documented default mode
        """
        html = setup_with_ai._build_setup_form_html(
            state="state-123",
            project_root=Path("/tmp/project"),
            env_file_path=Path("/tmp/project/.env"),
        )

        for card in setup_with_ai.MCP_MODE_CARD_DEFS:
            mode = card["value"]
            with self.subTest(mode=mode):
                self.assertIn(
                    f'data-mode="{mode}"',
                    html,
                    f"setup form must render a card for mode {mode!r}",
                )
                self.assertIn(
                    f'name="mcp_mode_select" value="{mode}"',
                    html,
                    f"setup form must render a radio input for mode {mode!r}",
                )

        self.assertIn(
            'name="mcp_mode" id="mcpModeInput" value="forward"',
            html,
            "setup form hidden mode input must default to forward",
        )
