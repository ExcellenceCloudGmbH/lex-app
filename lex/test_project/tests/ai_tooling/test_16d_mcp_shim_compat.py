"""
Cluster 16d: MCP compatibility shims (`lex.tools.mcp_mode_invoke`,
`lex.tools.verify_ai_assets`).

Intent
------

Both ``lex/tools/mcp_mode_invoke.py`` and ``lex/tools/verify_ai_assets.py``
are, per their own module docstrings, pure **compatibility shims**: the real
implementation moved to the ``lex_mcp`` package shipped by the separate
``lex-mcp-local`` distribution (installed by ``lex setup-with-ai`` / kept
current by ``lex ai-update``). The shim's job is exactly two things:

1. When ``lex-mcp-local`` is missing or too old to provide the module, raise
   an ``ImportError`` that names the concrete recovery action (``lex
   ai-update`` or ``lex setup-with-ai``) instead of a bare
   ``ModuleNotFoundError`` traceback that gives an operator no next step.
2. When ``lex-mcp-local`` **is** present, transparently re-export its public
   surface — including names accessed dynamically via ``__getattr__``/
   ``__dir__`` — so legacy call sites (``from lex.tools.mcp_mode_invoke
   import invoke_switch_to_mode``) keep working unchanged.

``lex_mcp`` is a separate PyPI package and is deliberately **not** a
dependency of ``lex-app`` itself (see AGENTS.md), so it is genuinely absent
in this test environment — scenario 1 below exercises the real, unmocked
failure path. Scenario 2 installs a minimal fake ``lex_mcp`` package (the
only reasonable way to exercise the "package present" branch without
depending on the actual third-party distribution) and reloads the shim
against it.

Scenario numbering continues from 16c (16.44-16.54) at 16.55.
"""
from __future__ import annotations

import sys
import types
import unittest
from contextlib import contextmanager

import pytest

pytestmark = pytest.mark.ai_tooling


def _evict(*module_names: str) -> None:
    for name in module_names:
        sys.modules.pop(name, None)


@contextmanager
def _fresh_import(*shim_module_names: str):
    """Evict cached shim modules so the next ``import`` re-executes them
    against whatever ``lex_mcp`` state is currently installed."""
    _evict(*shim_module_names)
    try:
        yield
    finally:
        _evict(*shim_module_names)


@contextmanager
def _install_fake_lex_mcp():
    """Install a minimal, real (non-``lex-app``) ``lex_mcp`` package so the
    "package present" branch of both shims can run for real."""
    saved = {
        key: sys.modules.get(key)
        for key in (
            "lex_mcp",
            "lex_mcp.mode_switch",
            "lex_mcp.payload",
            "lex_mcp.ai_assets",
        )
    }

    pkg = types.ModuleType("lex_mcp")
    pkg.__path__ = []
    sys.modules["lex_mcp"] = pkg

    payload_mod = types.ModuleType("lex_mcp.payload")
    payload_mod.MODE_TO_PACKAGE = {
        "brief": "lex_mcp.brief",
        "forward": "lex_mcp.forward",
    }
    sys.modules["lex_mcp.payload"] = payload_mod

    class InvokeSwitchResult:
        def __init__(self, mode: str):
            self.mode = mode

    def invoke_switch_to_mode(mode: str) -> InvokeSwitchResult:
        return InvokeSwitchResult(mode)

    mode_switch_mod = types.ModuleType("lex_mcp.mode_switch")
    mode_switch_mod.InvokeSwitchResult = InvokeSwitchResult
    mode_switch_mod.invoke_switch_to_mode = invoke_switch_to_mode
    #: An attribute NOT re-exported by name in the shim's ``from ... import``
    #: statement -- only reachable through ``__getattr__`` delegation.
    mode_switch_mod.INTERNAL_ONLY_MARKER = "delegated-via-getattr"
    sys.modules["lex_mcp.mode_switch"] = mode_switch_mod

    def verify_ai_assets(*args, **kwargs):
        return {"ok": True}

    ai_assets_mod = types.ModuleType("lex_mcp.ai_assets")
    ai_assets_mod.verify_ai_assets = verify_ai_assets
    ai_assets_mod.__all__ = ["verify_ai_assets"]
    ai_assets_mod.INTERNAL_ONLY_MARKER = "delegated-via-getattr"
    sys.modules["lex_mcp.ai_assets"] = ai_assets_mod

    try:
        yield
    finally:
        for key, value in saved.items():
            if value is None:
                sys.modules.pop(key, None)
            else:
                sys.modules[key] = value


# ---------------------------------------------------------------------
# 16.55–16.56 — real absence of lex_mcp: the helpful ImportError
# ---------------------------------------------------------------------
class TestCluster16k_ShimImportErrorWithoutLexMcp(unittest.TestCase):
    """``lex_mcp`` is genuinely not installed in this environment (it is not
    a dependency of ``lex-app`` — see AGENTS.md), so importing either shim
    module exercises the real failure path with no mocking at all."""

    def test_16_55_mcp_mode_invoke_names_the_recovery_actions(self):
        with _fresh_import("lex.tools.mcp_mode_invoke"):
            with self.assertRaises(ImportError) as ctx:
                import lex.tools.mcp_mode_invoke  # noqa: F401
        message = str(ctx.exception)
        self.assertIn("lex ai-update", message)
        self.assertIn("lex setup-with-ai", message)

    def test_16_56_verify_ai_assets_names_the_recovery_actions(self):
        with _fresh_import("lex.tools.verify_ai_assets"):
            with self.assertRaises(ImportError) as ctx:
                import lex.tools.verify_ai_assets  # noqa: F401
        message = str(ctx.exception)
        self.assertIn("lex ai-update", message)
        self.assertIn("lex setup-with-ai", message)


# ---------------------------------------------------------------------
# 16.57–16.61 — lex_mcp present: re-export + __getattr__/__dir__ delegation
# ---------------------------------------------------------------------
class TestCluster16l_ShimDelegationWithLexMcpPresent(unittest.TestCase):
    def test_16_57_mcp_mode_invoke_reexports_named_symbols(self):
        with _install_fake_lex_mcp(), _fresh_import("lex.tools.mcp_mode_invoke"):
            from lex.tools import mcp_mode_invoke

            result = mcp_mode_invoke.invoke_switch_to_mode("forward")
            self.assertIsInstance(result, mcp_mode_invoke.InvokeSwitchResult)
            self.assertEqual(result.mode, "forward")

    def test_16_58_supported_mcp_modes_is_derived_from_mode_to_package(self):
        """``SUPPORTED_MCP_MODES`` must reflect the installed package's mode
        roster, not a value hard-coded in the shim -- otherwise the shim
        drifts the moment the server adds/removes a mode (the exact bug the
        module docstring says motivated the move)."""
        with _install_fake_lex_mcp(), _fresh_import("lex.tools.mcp_mode_invoke"):
            from lex.tools import mcp_mode_invoke

            self.assertEqual(
                set(mcp_mode_invoke.SUPPORTED_MCP_MODES), {"brief", "forward"}
            )

    def test_16_59_mcp_mode_invoke_getattr_and_dir_delegate_to_impl(self):
        with _install_fake_lex_mcp(), _fresh_import("lex.tools.mcp_mode_invoke"):
            from lex.tools import mcp_mode_invoke

            self.assertEqual(
                mcp_mode_invoke.INTERNAL_ONLY_MARKER, "delegated-via-getattr"
            )
            self.assertIn("INTERNAL_ONLY_MARKER", dir(mcp_mode_invoke))
            with self.assertRaises(AttributeError):
                mcp_mode_invoke.this_attribute_does_not_exist_anywhere

    def test_16_60_verify_ai_assets_reexports_and_delegates(self):
        with _install_fake_lex_mcp(), _fresh_import("lex.tools.verify_ai_assets"):
            from lex.tools import verify_ai_assets as shim

            self.assertEqual(shim.verify_ai_assets(), {"ok": True})
            self.assertEqual(shim.INTERNAL_ONLY_MARKER, "delegated-via-getattr")
            self.assertIn("INTERNAL_ONLY_MARKER", dir(shim))

    def test_16_61_verify_ai_assets_unknown_attribute_raises_attribute_error(self):
        with _install_fake_lex_mcp(), _fresh_import("lex.tools.verify_ai_assets"):
            from lex.tools import verify_ai_assets as shim

            with self.assertRaises(AttributeError):
                shim.this_attribute_does_not_exist_anywhere


if __name__ == "__main__":
    unittest.main()
