"""
Cluster 16b: `lex setup-with-ai` — MCP mode + AI-environment normalization.

Intent
------

``lex/tools/setup_with_ai.py`` backs the ``lex setup-with-ai`` browser form.
Two pieces of that form are pure, well-isolated logic that decide what the
setup actually configures — both explicitly written to be tested at module
level (see their docstrings):

* ``resolve_submitted_mcp_mode`` — the mode grid on the form ships with its
  radios ``disabled`` in the browser, but that is cosmetic only: the form is
  served over loopback HTTP, so a hand-built POST (or a stale cached page, or
  a browser with JS off) can still submit a non-default mode with no
  acknowledgement checkbox. The *real* gate is this function: a submitted
  mode is only honoured when accompanied by the acknowledgement field
  (``MODE_OVERRIDE_FIELD``); otherwise the submission silently reverts to
  ``DEFAULT_LEX_MCP_MODE`` ("brief") rather than being refused. Getting this
  wrong either locks users out of every mode but the default, or lets an
  unacknowledged/forged submission install a mode the user never confirmed.

* ``normalize_ai_environments`` / ``_resolve_environment_alias`` — accepts a
  comma/space-separated string or an iterable of AI-environment names,
  resolves aliases (``vscode`` -> ``vscode-copilot``, underscores and hyphens
  interchangeable), expands the literal ``all``, dedupes while preserving
  order, and (the customer-visible contract) *raises* on an unrecognised
  name by default rather than silently substituting the default environment
  — a customer who asks for ``claude-code`` and mistypes it must be told,
  not silently handed a Copilot-only setup. ``strict=False`` exists
  specifically for reading back already-persisted configuration, where an
  unrecognised value (e.g. written by a newer/older release) must degrade to
  the default instead of crashing.

These tests exercise the local-mirror code path: ``lex_mcp`` (shipped by
``lex-mcp-local``) is not installed in this environment, so
``_environment_registry()`` returns ``None`` and every call below falls
through to the hard-coded ``AI_ENVIRONMENT_ALIASES`` / ``SUPPORTED_MCP_MODES``
tables baked into this module — exactly the "cold start, before lex-mcp-local
is installed" scenario the module's own comments describe.

Scenario numbering continues from 16a (16.1–16.28) at 16.29.
"""
from __future__ import annotations

import unittest

import pytest

from lex.tools import setup_with_ai

pytestmark = pytest.mark.ai_tooling


# ---------------------------------------------------------------------
# 16.29–16.33 — normalize_mcp_mode / resolve_submitted_mcp_mode
# ---------------------------------------------------------------------
class TestCluster16f_McpModeOverrideGate(unittest.TestCase):
    """The acknowledgement gate that guards non-default MCP modes."""

    def test_16_29_unknown_mode_normalizes_to_default(self):
        self.assertEqual(
            setup_with_ai.normalize_mcp_mode("not-a-real-mode"),
            setup_with_ai.DEFAULT_LEX_MCP_MODE,
        )

    def test_16_30_known_mode_passes_through_normalize(self):
        self.assertEqual(setup_with_ai.normalize_mcp_mode("edit"), "edit")

    def test_16_31_submitted_non_default_mode_without_ack_reverts_to_default(self):
        """16.31: a hand-built POST for ``edit`` with no acknowledgement field
        must NOT install ``edit`` — it silently reverts to the default."""
        form = {"mcp_mode": ["edit"]}
        self.assertEqual(
            setup_with_ai.resolve_submitted_mcp_mode(form),
            setup_with_ai.DEFAULT_LEX_MCP_MODE,
        )

    def test_16_32_submitted_non_default_mode_with_ack_is_honoured(self):
        form = {
            "mcp_mode": ["edit"],
            setup_with_ai.MODE_OVERRIDE_FIELD: ["on"],
        }
        self.assertEqual(setup_with_ai.resolve_submitted_mcp_mode(form), "edit")

    def test_16_33_blank_acknowledgement_value_is_treated_as_not_acknowledged(self):
        """16.33: the field being *present* is not enough — it must carry a
        non-blank value (whitespace-only counts as absent, per ``.strip()``)."""
        form = {
            "mcp_mode": ["edit"],
            setup_with_ai.MODE_OVERRIDE_FIELD: ["   "],
        }
        self.assertEqual(
            setup_with_ai.resolve_submitted_mcp_mode(form),
            setup_with_ai.DEFAULT_LEX_MCP_MODE,
        )

    def test_16_34_submitting_the_default_mode_never_needs_acknowledgement(self):
        form = {"mcp_mode": [setup_with_ai.DEFAULT_LEX_MCP_MODE]}
        self.assertEqual(
            setup_with_ai.resolve_submitted_mcp_mode(form),
            setup_with_ai.DEFAULT_LEX_MCP_MODE,
        )


# ---------------------------------------------------------------------
# 16.35–16.42 — normalize_ai_environments / _resolve_environment_alias
# ---------------------------------------------------------------------
class TestCluster16g_AiEnvironmentNormalization(unittest.TestCase):
    """Alias resolution, ``all`` expansion, dedup, and the strict/lenient
    contract for unrecognised environment names."""

    def test_16_35_canonical_name_resolves_to_itself(self):
        self.assertEqual(
            setup_with_ai._resolve_environment_alias("vscode-copilot"),
            "vscode-copilot",
        )

    def test_16_36_alias_with_underscore_and_hyphen_both_resolve(self):
        self.assertEqual(
            setup_with_ai._resolve_environment_alias("vs_code"), "vscode-copilot"
        )
        self.assertEqual(
            setup_with_ai._resolve_environment_alias("vs-code"), "vscode-copilot"
        )

    def test_16_37_unknown_alias_resolves_to_none(self):
        self.assertIsNone(setup_with_ai._resolve_environment_alias("not-a-real-ide"))

    def test_16_38_comma_and_space_separated_string_is_split_and_deduped(self):
        result = setup_with_ai.normalize_ai_environments("vscode, claude vscode")
        self.assertEqual(result, ("vscode-copilot", "claude-code"))

    def test_16_39_all_expands_to_every_supported_environment_in_order(self):
        result = setup_with_ai.normalize_ai_environments("all")
        self.assertEqual(result, setup_with_ai.SUPPORTED_AI_ENVIRONMENTS)

    def test_16_40_unknown_name_raises_setupwithaierror_by_default(self):
        with self.assertRaises(setup_with_ai.SetupWithAIError) as ctx:
            setup_with_ai.normalize_ai_environments("not-a-real-ide")
        self.assertIn("not-a-real-ide", str(ctx.exception))
        for env in setup_with_ai.SUPPORTED_AI_ENVIRONMENTS:
            self.assertIn(env, str(ctx.exception))

    def test_16_41_unknown_name_degrades_to_default_when_lenient(self):
        """16.41: reading back persisted config must not crash on drift."""
        result = setup_with_ai.normalize_ai_environments(
            "not-a-real-ide", strict=False
        )
        self.assertEqual(result, (setup_with_ai.DEFAULT_AI_ENVIRONMENT,))

    def test_16_42_none_input_falls_back_to_default_environment(self):
        result = setup_with_ai.normalize_ai_environments(None)
        self.assertEqual(result, (setup_with_ai.DEFAULT_AI_ENVIRONMENT,))

    def test_16_43_iterable_input_is_accepted_alongside_strings(self):
        result = setup_with_ai.normalize_ai_environments(["cursor", "codex"])
        self.assertEqual(result, ("cursor", "codex"))


if __name__ == "__main__":
    unittest.main()
