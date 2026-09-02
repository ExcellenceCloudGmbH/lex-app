"""
Cluster 1ab: ``lex-mcp-local`` onboarding shims and cold-start fallbacks.

Intent
------

``lex setup-with-ai`` installs the separate ``lex-mcp-local`` distribution and
then hands most AI-onboarding behaviour to it, so a new capability reaches a
customer through ``lex ai-update`` rather than a ``lex-app`` release (see
``docs/reference/CLI Commands.md`` § AI Commands). Two source files in
``lex/tools`` are now pure compatibility shims for that split:

* ``lex/tools/mcp_mode_invoke.py`` re-exports
  ``lex_mcp.mode_switch.invoke_switch_to_mode`` /
  ``InvokeSwitchResult`` and derives ``SUPPORTED_MCP_MODES`` from
  ``lex_mcp.payload.MODE_TO_PACKAGE``.
* ``lex/tools/verify_ai_assets.py`` re-exports ``lex_mcp.ai_assets`` in full.

Both raise an actionable ``ImportError`` when ``lex-mcp-local`` is missing or
too old to provide the module -- the docs are explicit that this is the
customer-visible contract: *"If a command in this table reports that
lex-mcp-local is not installed at all, run `lex setup-with-ai`."* A shim that
raised a bare ``ModuleNotFoundError`` instead would leave the customer with no
next step.

``lex/tools/setup_with_ai.py`` keeps a single value it cannot delegate at
runtime: the mode roster has to render in the setup form *before*
lex-mcp-local is known to be installed (installing it is what the form does).
Its own docstring states the contract precisely: derive the roster from the
installed package whenever possible, and fall back to a pinned literal only
on a cold start -- "this table sat at six modes for three releases while the
server shipped nine" is the regression this guards against. The form also
gates a user's raw mode pick behind an acknowledgement field before letting a
non-default pick through (``resolve_submitted_mcp_mode``), which is worth its
own regression coverage since the browser-side ``disabled`` attribute is only
a hint, not enforcement.

``lex/mcp_server/tools/embed.py`` (the fourth file in this batch) cannot be
imported at all in this checkout: it depends on ``lex.mcp_server.config`` /
``lex.mcp_server.registry``, neither of which exist yet (the rest of
``lex/mcp_server`` lives on an unmerged branch -- see ``AGENTS.md``, "One trap
in `lex/mcp_server`"). Its specific regression in this change (the vendored
``mcp.server.fastmcp`` import and the FastMCP-1.0 ``add_tool(fn, name=...)``
call shape) is already covered generically, without importing the module, by
the existing static AST-based
``lex/tests/unit/infra/test_mcp_server_sdk_compat.py``. No new test is added
for it here to avoid duplicating that mechanism.

None of these scenarios need ``lex-mcp-local`` installed -- the "not
installed" paths are exercised against the real (absent) package, and the
"installed" paths inject a minimal fake module via ``sys.modules`` rather than
depending on the separate distribution being present in this environment.

Cluster 1ab -- scenarios 1.223-1.229. Type: U (pure Python, no DB).
Covers: lex/tools/mcp_mode_invoke.py, lex/tools/verify_ai_assets.py,
lex/tools/setup_with_ai.py.
Run: python -m lex pytest lex/test_project/tests/init/test_1ab_ai_onboarding_shims.py -v
"""

from __future__ import annotations

import importlib
import sys
import types
import unittest
from types import SimpleNamespace

from django.test import SimpleTestCase

import pytest

pytestmark = pytest.mark.init


def _purge(*module_names: str) -> None:
    for name in module_names:
        sys.modules.pop(name, None)


# --------------------------------------------------------------------------- #
# 1.223 / 1.225 -- actionable ImportError when lex-mcp-local is absent
# --------------------------------------------------------------------------- #
class TestCluster01ab_ShimsWithoutLexMcpLocal(SimpleTestCase):
    """Both shims fail loudly, with a next step, when lex-mcp-local is gone.

    lex_mcp is genuinely not installed in this environment, so these
    scenarios exercise the real absent-package path rather than a mock.
    """

    def setUp(self) -> None:
        super().setUp()
        _purge("lex.tools.mcp_mode_invoke", "lex.tools.verify_ai_assets")
        self.addCleanup(
            _purge, "lex.tools.mcp_mode_invoke", "lex.tools.verify_ai_assets"
        )

    def test_1_223_mcp_mode_invoke_names_both_recovery_commands(self) -> None:
        """
        Scenario 1.223: mcp_mode_invoke shim, lex-mcp-local absent.
        Given: lex_mcp is not importable (real state of this environment).
        When: lex.tools.mcp_mode_invoke is imported.
        Then: the ImportError names both recovery commands documented in
              "CLI Commands.md" (`lex ai-update` for a stale install, and
              `lex setup-with-ai` for none at all) so the operator always has
              a next step regardless of which situation they are in.
        """
        with self.assertRaises(ImportError) as ctx:
            importlib.import_module("lex.tools.mcp_mode_invoke")
        message = str(ctx.exception)
        self.assertIn(
            "lex ai-update", message,
            "missing the upgrade path for a stale lex-mcp-local install",
        )
        self.assertIn(
            "lex setup-with-ai", message,
            "missing the install path for no lex-mcp-local at all",
        )

    def test_1_225_verify_ai_assets_names_both_recovery_commands(self) -> None:
        """
        Scenario 1.225: verify_ai_assets shim, lex-mcp-local absent.
        Given: lex_mcp is not importable.
        When: lex.tools.verify_ai_assets is imported.
        Then: same actionable-ImportError contract as 1.223 -- the two shims
              must not drift into different failure messages for the same
              underlying situation.
        """
        with self.assertRaises(ImportError) as ctx:
            importlib.import_module("lex.tools.verify_ai_assets")
        message = str(ctx.exception)
        self.assertIn("lex ai-update", message)
        self.assertIn("lex setup-with-ai", message)


# --------------------------------------------------------------------------- #
# 1.224 / 1.226 -- re-export + delegate once lex-mcp-local is present
# --------------------------------------------------------------------------- #
class TestCluster01ab_ShimsWithFakeLexMcpLocal(SimpleTestCase):
    """The shims re-export the real implementation and forward unknown
    attribute access to it, once the underlying package is importable.

    A minimal fake ``lex_mcp`` package is injected via ``sys.modules`` --
    this is the shim's own true external boundary (a separately released
    distribution), not an internal lex-app module, so mocking it here is
    the correct place to stop.
    """

    def setUp(self) -> None:
        super().setUp()
        _purge(
            "lex.tools.mcp_mode_invoke",
            "lex.tools.verify_ai_assets",
            "lex_mcp",
            "lex_mcp.mode_switch",
            "lex_mcp.payload",
            "lex_mcp.ai_assets",
        )
        self.addCleanup(
            _purge,
            "lex.tools.mcp_mode_invoke",
            "lex.tools.verify_ai_assets",
            "lex_mcp",
            "lex_mcp.mode_switch",
            "lex_mcp.payload",
            "lex_mcp.ai_assets",
        )

    def _install_fake_mode_switch(self):
        fake_pkg = types.ModuleType("lex_mcp")
        fake_pkg.__path__ = []  # mark as a package for submodule imports

        fake_payload = types.ModuleType("lex_mcp.payload")
        fake_payload.MODE_TO_PACKAGE = {
            "brief": "lex_mcp_brief",
            "forward": "lex_mcp_local",
        }

        class InvokeSwitchResult(SimpleNamespace):
            pass

        def invoke_switch_to_mode(mode: str) -> InvokeSwitchResult:
            return InvokeSwitchResult(mode=mode, ok=True)

        fake_mode_switch = types.ModuleType("lex_mcp.mode_switch")
        fake_mode_switch.InvokeSwitchResult = InvokeSwitchResult
        fake_mode_switch.invoke_switch_to_mode = invoke_switch_to_mode
        fake_mode_switch.some_other_helper = lambda: "delegated"

        sys.modules["lex_mcp"] = fake_pkg
        sys.modules["lex_mcp.payload"] = fake_payload
        sys.modules["lex_mcp.mode_switch"] = fake_mode_switch
        return fake_mode_switch

    def _install_fake_ai_assets(self):
        fake_pkg = types.ModuleType("lex_mcp")
        fake_pkg.__path__ = []

        fake_ai_assets = types.ModuleType("lex_mcp.ai_assets")
        fake_ai_assets.__all__ = ["verify_ai_assets"]

        def verify_ai_assets(*args, **kwargs):
            return "verified"

        fake_ai_assets.verify_ai_assets = verify_ai_assets
        fake_ai_assets.internal_only_helper = lambda: "internal"

        sys.modules["lex_mcp"] = fake_pkg
        sys.modules["lex_mcp.ai_assets"] = fake_ai_assets
        return fake_ai_assets

    def test_1_224_mcp_mode_invoke_reexports_and_delegates(self) -> None:
        """
        Scenario 1.224: mcp_mode_invoke shim, lex-mcp-local present.
        Given: a fake lex_mcp.mode_switch / lex_mcp.payload providing the
               real primitives.
        When: lex.tools.mcp_mode_invoke is imported and used.
        Then: invoke_switch_to_mode / InvokeSwitchResult are the exact
              objects from lex_mcp.mode_switch (no re-implementation),
              SUPPORTED_MCP_MODES is derived from MODE_TO_PACKAGE, and an
              attribute not explicitly re-exported still resolves via
              __getattr__ delegation (so the shim never goes stale for new
              lex_mcp attributes).
        """
        fake_mode_switch = self._install_fake_mode_switch()

        shim = importlib.import_module("lex.tools.mcp_mode_invoke")

        self.assertIs(
            shim.invoke_switch_to_mode, fake_mode_switch.invoke_switch_to_mode,
            "shim re-implemented rather than re-exported invoke_switch_to_mode",
        )
        self.assertIs(shim.InvokeSwitchResult, fake_mode_switch.InvokeSwitchResult)
        self.assertEqual(
            tuple(shim.SUPPORTED_MCP_MODES), ("brief", "forward"),
            "SUPPORTED_MCP_MODES must be derived from lex_mcp.payload.MODE_TO_PACKAGE",
        )
        self.assertEqual(
            shim.some_other_helper(), "delegated",
            "__getattr__ must forward attributes not explicitly re-exported",
        )
        self.assertIn(
            "invoke_switch_to_mode", shim.__dir__(),
            "__dir__ must include names delegated from the underlying module",
        )

    def test_1_226_verify_ai_assets_reexports_and_delegates(self) -> None:
        """
        Scenario 1.226: verify_ai_assets shim, lex-mcp-local present.
        Given: a fake lex_mcp.ai_assets providing verify_ai_assets plus an
               attribute not in its __all__.
        When: lex.tools.verify_ai_assets is imported and used.
        Then: verify_ai_assets works end-to-end through the shim, and an
              attribute outside __all__ still resolves via __getattr__
              delegation -- the same "never goes stale" contract as the
              mode-switch shim.
        """
        fake_ai_assets = self._install_fake_ai_assets()

        shim = importlib.import_module("lex.tools.verify_ai_assets")

        self.assertEqual(shim.verify_ai_assets(), "verified")
        self.assertEqual(
            shim.internal_only_helper(), "internal",
            "__getattr__ must forward attributes outside __all__",
        )
        self.assertIn("verify_ai_assets", shim.__dir__())


# --------------------------------------------------------------------------- #
# 1.227 -- setup_with_ai cold-start fallback for the mode roster
# --------------------------------------------------------------------------- #
class TestCluster01ab_SetupWithAIColdStartFallback(SimpleTestCase):
    """The setup form's mode roster derives from the installed package when
    possible and only falls back to a pinned literal on a genuine cold
    start -- never the reverse, or a real lex-mcp-local release regresses
    silently back to the stale six-mode table this guards against.
    """

    def test_1_227_falls_back_to_pinned_roster_without_lex_mcp_local(self) -> None:
        """
        Scenario 1.227: cold start, lex-mcp-local not installed.
        Given: lex_mcp.ai_setup is genuinely unimportable (real environment
               state).
        When: setup_with_ai's module-level SUPPORTED_MCP_MODES /
              MODE_OVERRIDE_FIELD are read (already resolved at import time).
        Then: they equal the module's own pinned fallback constants, and the
              roster is non-empty -- the setup form must always have
              something to render even before lex-mcp-local exists.
        """
        from lex.tools import setup_with_ai as target

        self.assertEqual(
            target._installed_mode_roster(), None,
            "lex_mcp.ai_setup must be genuinely unavailable for this scenario",
        )
        self.assertEqual(
            target.SUPPORTED_MCP_MODES, target._FALLBACK_MCP_MODES,
            "cold start must render the pinned fallback roster",
        )
        self.assertEqual(
            target.MODE_OVERRIDE_FIELD, target._FALLBACK_MODE_OVERRIDE_FIELD,
        )
        self.assertIn("brief", target.SUPPORTED_MCP_MODES)


# --------------------------------------------------------------------------- #
# 1.228 / 1.229 -- setup form mode-picker gating
# --------------------------------------------------------------------------- #
class TestCluster01ab_SetupWithAIModeGating(SimpleTestCase):
    """``normalize_mcp_mode`` / ``resolve_submitted_mcp_mode`` are the gate
    behind the setup form's mode grid, which ships intentionally disabled:
    the browser-side ``disabled`` attribute is a hint, not enforcement, since
    the form is served over loopback HTTP and a hand-built POST bypasses it
    entirely.
    """

    def test_1_228_normalize_mcp_mode_rejects_unknown_and_normalises_case(self) -> None:
        """
        Scenario 1.228: normalize_mcp_mode input handling.
        Given: a blank/unknown mode string, and a valid one in mixed case
               with surrounding whitespace.
        When: normalize_mcp_mode is called.
        Then: unknown/blank falls back to the default mode; a valid one is
              lower-cased and trimmed rather than rejected outright.
        """
        from lex.tools.setup_with_ai import DEFAULT_LEX_MCP_MODE, normalize_mcp_mode

        self.assertEqual(normalize_mcp_mode(None), DEFAULT_LEX_MCP_MODE)
        self.assertEqual(normalize_mcp_mode(""), DEFAULT_LEX_MCP_MODE)
        self.assertEqual(
            normalize_mcp_mode("not-a-real-mode"), DEFAULT_LEX_MCP_MODE,
        )
        self.assertEqual(normalize_mcp_mode("  Forward  "), "forward")

    def test_1_229_unacknowledged_non_default_pick_reverts_to_default(self) -> None:
        """
        Scenario 1.229: resolve_submitted_mcp_mode override gate.
        Given: a submitted form picking a non-default mode.
        When: the override-acknowledgement field is absent/blank vs. present.
        Then: the pick is honoured only when acknowledged; otherwise the
              form reverts to the default mode (brief) so a hand-built POST
              or a stale cached page can never smuggle a non-default mode
              through past the disabled grid.
        """
        from lex.tools.setup_with_ai import (
            DEFAULT_LEX_MCP_MODE,
            MODE_OVERRIDE_FIELD,
            resolve_submitted_mcp_mode,
        )

        # No acknowledgement field at all.
        self.assertEqual(
            resolve_submitted_mcp_mode({"mcp_mode": ["forward"]}),
            DEFAULT_LEX_MCP_MODE,
        )
        # Acknowledgement field present but blank.
        self.assertEqual(
            resolve_submitted_mcp_mode(
                {"mcp_mode": ["forward"], MODE_OVERRIDE_FIELD: [""]}
            ),
            DEFAULT_LEX_MCP_MODE,
        )
        # Acknowledged: the non-default pick is honoured.
        self.assertEqual(
            resolve_submitted_mcp_mode(
                {"mcp_mode": ["forward"], MODE_OVERRIDE_FIELD: ["yes"]}
            ),
            "forward",
        )
        # A pick of the default mode itself never needs acknowledgement.
        self.assertEqual(
            resolve_submitted_mcp_mode({"mcp_mode": [DEFAULT_LEX_MCP_MODE]}),
            DEFAULT_LEX_MCP_MODE,
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
