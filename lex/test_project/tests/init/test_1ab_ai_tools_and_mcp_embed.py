"""AI tooling shims and MCP embed URL utilities — regression tests.

Intent
------

Four source files were added / refactored as part of the client-admin-role
change set and landed without paired test coverage:

* ``lex/tools/setup_with_ai.py`` — pure helper functions consumed by the
  ``lex setup-with-ai`` wizard: ``DEFAULT_LEX_MCP_MODE``,
  ``SUPPORTED_MCP_MODES``, ``_installed_mode_roster``,
  ``normalize_mcp_mode``, ``resolve_submitted_mcp_mode``,
  ``_resolve_environment_alias``.

* ``lex/tools/mcp_mode_invoke.py`` — compatibility shim that re-exports
  ``invoke_switch_to_mode``, ``InvokeSwitchResult``, and
  ``SUPPORTED_MCP_MODES`` from ``lex_mcp.mode_switch``.  Customers who
  pinned ``from lex.tools.mcp_mode_invoke import …`` in their own code
  must not be broken.

* ``lex/tools/verify_ai_assets.py`` — compatibility shim that re-exports
  everything from ``lex_mcp.ai_assets`` via ``__getattr__`` / star import.

* ``lex/mcp_server/tools/embed.py`` — pure URL-resolution and
  classification helpers used to build the ``lex_embed_view`` MCP tool:
  ``_resolve_frontend_url``, ``_csp_origins``, ``_classify_path``,
  ``_build_title``, ``_build_embed_url``.

All test classes in this file exercise the *customer-visible contract*
derived from the module docstrings, not the implementation.  The three
shim modules are loaded inside each test via isolated ``sys.modules``
injection so the tests pass even when ``lex_mcp`` / ``fastmcp`` are not
installed in the test environment.

Cluster 1ab — scenarios 1.223–1.248.  Type: U.
Run: python -m lex pytest lex/test_project/tests/init/test_1ab_ai_tools_and_mcp_embed.py -v
"""

from __future__ import annotations

import importlib
import os
import sys
import types
import unittest
from typing import Any
from unittest import TestCase
from unittest.mock import patch

import pytest

pytestmark = pytest.mark.init

# ---------------------------------------------------------------------------
# Helpers for isolated module loading
# ---------------------------------------------------------------------------


def _make_lex_mcp_mode_switch_mock() -> types.ModuleType:
    """Minimal ``lex_mcp.mode_switch`` stand-in."""
    mod = types.ModuleType("lex_mcp.mode_switch")

    class InvokeSwitchResult:
        pass

    def invoke_switch_to_mode(mode: str) -> InvokeSwitchResult:
        return InvokeSwitchResult()

    mod.InvokeSwitchResult = InvokeSwitchResult
    mod.invoke_switch_to_mode = invoke_switch_to_mode
    mod.sentinel_attr = "from_mock"
    return mod


def _make_lex_mcp_payload_mock() -> types.ModuleType:
    mod = types.ModuleType("lex_mcp.payload")
    mod.MODE_TO_PACKAGE = {"brief": "pkg-a", "forward": "pkg-b", "edit": "pkg-c"}
    return mod


def _make_lex_mcp_ai_assets_mock() -> types.ModuleType:
    mod = types.ModuleType("lex_mcp.ai_assets")
    mod.verify_ai_assets = lambda: "ok"
    mod.sentinel_attr = "ai_assets_mock"
    return mod


def _load_mcp_mode_invoke() -> types.ModuleType:
    """Import ``lex.tools.mcp_mode_invoke`` with a fake ``lex_mcp``."""
    lex_mcp_pkg = types.ModuleType("lex_mcp")
    mode_switch = _make_lex_mcp_mode_switch_mock()
    payload = _make_lex_mcp_payload_mock()
    lex_mcp_pkg.mode_switch = mode_switch
    lex_mcp_pkg.payload = payload

    overrides = {
        "lex_mcp": lex_mcp_pkg,
        "lex_mcp.mode_switch": mode_switch,
        "lex_mcp.payload": payload,
    }
    saved = {k: sys.modules.pop(k, None) for k in list(overrides) + ["lex.tools.mcp_mode_invoke"]}
    try:
        sys.modules.update(overrides)
        mod = importlib.import_module("lex.tools.mcp_mode_invoke")
        return mod
    finally:
        for k, v in saved.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v
        sys.modules.pop("lex.tools.mcp_mode_invoke", None)


def _load_verify_ai_assets() -> types.ModuleType:
    """Import ``lex.tools.verify_ai_assets`` with a fake ``lex_mcp``."""
    lex_mcp_pkg = types.ModuleType("lex_mcp")
    ai_assets = _make_lex_mcp_ai_assets_mock()
    lex_mcp_pkg.ai_assets = ai_assets

    overrides = {
        "lex_mcp": lex_mcp_pkg,
        "lex_mcp.ai_assets": ai_assets,
    }
    saved = {k: sys.modules.pop(k, None) for k in list(overrides) + ["lex.tools.verify_ai_assets"]}
    try:
        sys.modules.update(overrides)
        mod = importlib.import_module("lex.tools.verify_ai_assets")
        return mod
    finally:
        for k, v in saved.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v
        sys.modules.pop("lex.tools.verify_ai_assets", None)


def _load_embed_module() -> types.ModuleType:
    """Load ``lex/mcp_server/tools/embed.py`` with all external deps mocked.

    The directory tree has no ``__init__.py`` files so it is not a regular
    package; we use ``importlib.util.spec_from_file_location`` to load the
    file directly while injecting the dependency stubs into ``sys.modules``.
    """
    import importlib.util
    from pathlib import Path

    embed_path = (
        Path(__file__).resolve().parents[3]
        / "mcp_server" / "tools" / "embed.py"
    )

    # Build minimal fastmcp stubs
    fastmcp_mod = types.ModuleType("fastmcp")
    fastmcp_mod.FastMCP = type("FastMCP", (), {})

    fastmcp_resources = types.ModuleType("fastmcp.resources")
    fastmcp_resources.FunctionResource = type("FunctionResource", (), {})

    fastmcp_tools = types.ModuleType("fastmcp.tools")
    fastmcp_tools.Tool = type("Tool", (), {})

    mcp_mod = sys.modules.get("mcp") or types.ModuleType("mcp")
    mcp_types = types.ModuleType("mcp.types")
    mcp_types.TextContent = type("TextContent", (), {})
    mcp_types.ToolAnnotations = type("ToolAnnotations", (), {})

    config_mod = types.ModuleType("lex.mcp_server.config")
    config_mod.mcp_setting = lambda key: None
    registry_mod = types.ModuleType("lex.mcp_server.registry")
    registry_mod.container_is_writable = lambda c: False
    registry_mod.get_container = lambda name: None

    overrides = {
        "fastmcp": fastmcp_mod,
        "fastmcp.resources": fastmcp_resources,
        "fastmcp.tools": fastmcp_tools,
        "mcp": mcp_mod,
        "mcp.types": mcp_types,
        "lex.mcp_server.config": config_mod,
        "lex.mcp_server.registry": registry_mod,
    }
    saved = {k: sys.modules.pop(k, None) for k in overrides}
    try:
        sys.modules.update(overrides)
        spec = importlib.util.spec_from_file_location(
            "lex.mcp_server.tools.embed", embed_path
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    finally:
        for k, v in saved.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v


# ---------------------------------------------------------------------------
# 1.223–1.233  setup_with_ai — pure functions
# ---------------------------------------------------------------------------
from lex.tools.setup_with_ai import (  # noqa: E402
    DEFAULT_LEX_MCP_MODE,
    MODE_OVERRIDE_FIELD,
    SUPPORTED_MCP_MODES,
    _installed_mode_roster,
    _resolve_environment_alias,
    normalize_mcp_mode,
    resolve_submitted_mcp_mode,
)


class TestCluster01ab_SetupWithAIPureFunctions(TestCase):
    """``lex/tools/setup_with_ai.py`` — customer-visible pure-function contract.

    Scenario 1.223–1.233.
    """

    def test_1_223_default_mcp_mode_is_brief(self):
        """1.223: ``DEFAULT_LEX_MCP_MODE`` must be ``"brief"``.

        Scenario 1.223
        Given the module is imported
        When ``DEFAULT_LEX_MCP_MODE`` is read
        Then its value is ``"brief"``
        """
        self.assertEqual(DEFAULT_LEX_MCP_MODE, "brief")

    def test_1_224_supported_mcp_modes_non_empty_and_contains_brief(self):
        """1.224: ``SUPPORTED_MCP_MODES`` must be a non-empty tuple containing ``"brief"``.

        Scenario 1.224
        Given the module is imported (lex-mcp-local may or may not be installed)
        When ``SUPPORTED_MCP_MODES`` is read
        Then it is a non-empty tuple and ``"brief"`` is always present
        """
        self.assertIsInstance(SUPPORTED_MCP_MODES, tuple)
        self.assertGreater(len(SUPPORTED_MCP_MODES), 0)
        self.assertIn("brief", SUPPORTED_MCP_MODES)

    def test_1_225_installed_mode_roster_returns_none_without_lex_mcp(self):
        """1.225: ``_installed_mode_roster`` returns ``None`` when ``lex_mcp`` is missing.

        Scenario 1.225
        Given ``lex_mcp.ai_setup`` is not importable
        When ``_installed_mode_roster()`` is called
        Then ``None`` is returned (cold-start fallback path)
        """
        saved = sys.modules.pop("lex_mcp", None)
        saved_setup = sys.modules.pop("lex_mcp.ai_setup", None)
        try:
            result = _installed_mode_roster()
            # When lex_mcp is absent the function must return None so the caller
            # falls back to _FALLBACK_MCP_MODES.
            self.assertIsNone(result)
        finally:
            if saved is not None:
                sys.modules["lex_mcp"] = saved
            if saved_setup is not None:
                sys.modules["lex_mcp.ai_setup"] = saved_setup

    def test_1_226_normalize_known_mode_returned_unchanged(self):
        """1.226: A known mode is returned verbatim (lower-cased).

        Scenario 1.226
        Given ``"forward"`` is a recognised MCP mode
        When ``normalize_mcp_mode("forward")`` is called
        Then ``"forward"`` is returned
        """
        known = SUPPORTED_MCP_MODES[0]
        self.assertEqual(normalize_mcp_mode(known), known)

    def test_1_227_normalize_unknown_mode_returns_default(self):
        """1.227: An unrecognised mode string returns the default.

        Scenario 1.227
        Given ``"not_a_real_mode"`` is not in ``SUPPORTED_MCP_MODES``
        When ``normalize_mcp_mode("not_a_real_mode")`` is called
        Then ``DEFAULT_LEX_MCP_MODE`` (``"brief"``) is returned
        """
        result = normalize_mcp_mode("not_a_real_mode")
        self.assertEqual(result, DEFAULT_LEX_MCP_MODE)

    def test_1_228_normalize_none_returns_default(self):
        """1.228: ``None`` input yields the default mode.

        Scenario 1.228
        Given the caller passes ``None``
        When ``normalize_mcp_mode(None)`` is called
        Then ``DEFAULT_LEX_MCP_MODE`` is returned
        """
        self.assertEqual(normalize_mcp_mode(None), DEFAULT_LEX_MCP_MODE)

    def test_1_229_resolve_submitted_default_mode_always_accepted(self):
        """1.229: Submitting the default mode requires no acknowledgement.

        Scenario 1.229
        Given form_data contains ``mcp_mode=brief`` with no acknowledgement
        When ``resolve_submitted_mcp_mode`` is called
        Then ``"brief"`` is returned (the default never needs the gate)
        """
        form_data = {"mcp_mode": ["brief"]}
        self.assertEqual(resolve_submitted_mcp_mode(form_data), "brief")

    def test_1_230_resolve_submitted_non_default_without_ack_falls_back(self):
        """1.230: A non-default mode without acknowledgement reverts to the default.

        Scenario 1.230
        Given form_data requests a non-default mode without ``MODE_OVERRIDE_FIELD``
        When ``resolve_submitted_mcp_mode`` is called
        Then ``DEFAULT_LEX_MCP_MODE`` is returned instead of the submitted mode
        """
        # Pick a non-default mode that exists in the supported set
        non_default = next(
            (m for m in SUPPORTED_MCP_MODES if m != DEFAULT_LEX_MCP_MODE), None
        )
        if non_default is None:
            self.skipTest("SUPPORTED_MCP_MODES contains only the default mode")
        form_data = {"mcp_mode": [non_default]}
        result = resolve_submitted_mcp_mode(form_data)
        self.assertEqual(result, DEFAULT_LEX_MCP_MODE)

    def test_1_231_resolve_submitted_non_default_with_ack_accepted(self):
        """1.231: A non-default mode WITH acknowledgement is accepted as-is.

        Scenario 1.231
        Given form_data requests a non-default mode and includes the acknowledgement
        When ``resolve_submitted_mcp_mode`` is called
        Then the submitted mode is returned unchanged
        """
        non_default = next(
            (m for m in SUPPORTED_MCP_MODES if m != DEFAULT_LEX_MCP_MODE), None
        )
        if non_default is None:
            self.skipTest("SUPPORTED_MCP_MODES contains only the default mode")
        form_data = {
            "mcp_mode": [non_default],
            MODE_OVERRIDE_FIELD: ["1"],
        }
        result = resolve_submitted_mcp_mode(form_data)
        self.assertEqual(result, non_default)

    def test_1_232_resolve_environment_alias_known_name(self):
        """1.232: A known environment alias is resolved to its canonical key.

        Scenario 1.232
        Given ``"cursor"`` is a known alias for some canonical environment key
        When ``_resolve_environment_alias("cursor")`` is called
        Then a non-None string is returned
        """
        result = _resolve_environment_alias("cursor")
        self.assertIsNotNone(result)
        self.assertIsInstance(result, str)

    def test_1_233_resolve_environment_alias_unknown_returns_none(self):
        """1.233: An unknown environment alias returns ``None``.

        Scenario 1.233
        Given ``"totally_unknown_ide_xyz"`` is not a registered alias
        When ``_resolve_environment_alias("totally_unknown_ide_xyz")`` is called
        Then ``None`` is returned
        """
        result = _resolve_environment_alias("totally_unknown_ide_xyz")
        self.assertIsNone(result)


# ---------------------------------------------------------------------------
# 1.234–1.237  mcp_mode_invoke shim
# ---------------------------------------------------------------------------


class TestCluster01ab_McpModeInvokeShim(TestCase):
    """``lex/tools/mcp_mode_invoke.py`` — shim re-export contract.

    Scenario 1.234–1.237.
    """

    def setUp(self):
        self.mod = _load_mcp_mode_invoke()

    def test_1_234_invoke_switch_result_re_exported(self):
        """1.234: ``InvokeSwitchResult`` is accessible on the shim module.

        Scenario 1.234
        Given ``lex_mcp.mode_switch`` is importable
        When ``from lex.tools.mcp_mode_invoke import InvokeSwitchResult`` is executed
        Then a class object is returned (not ``AttributeError``)
        """
        cls = getattr(self.mod, "InvokeSwitchResult", None)
        self.assertIsNotNone(cls, "InvokeSwitchResult must be re-exported by the shim")

    def test_1_235_supported_mcp_modes_derived_from_mode_to_package(self):
        """1.235: ``SUPPORTED_MCP_MODES`` is derived from the mock's ``MODE_TO_PACKAGE``.

        Scenario 1.235
        Given ``lex_mcp.payload.MODE_TO_PACKAGE`` contains ``{"brief", "forward", "edit"}``
        When ``SUPPORTED_MCP_MODES`` on the shim is read
        Then it is a tuple of those exact keys
        """
        modes = getattr(self.mod, "SUPPORTED_MCP_MODES", None)
        self.assertIsNotNone(modes)
        self.assertIsInstance(modes, tuple)
        # All keys from our mock payload must appear
        expected = {"brief", "forward", "edit"}
        self.assertEqual(set(modes), expected)

    def test_1_236_getattr_delegates_to_impl(self):
        """1.236: ``__getattr__`` on the shim delegates unknown names to ``_impl``.

        Scenario 1.236
        Given the shim defines ``__getattr__`` that calls ``getattr(_impl, name)``
        When an attribute present on the mock (``sentinel_attr``) is accessed
        Then the mock's value is returned
        """
        value = getattr(self.mod, "sentinel_attr", None)
        self.assertEqual(value, "from_mock")

    def test_1_237_dir_delegates_to_impl(self):
        """1.237: ``__dir__`` on the shim returns the impl's namespace.

        Scenario 1.237
        Given the shim defines ``__dir__``
        When ``dir(mod)`` is called on the shim
        Then the result is a list and includes names from the mock impl
        """
        names = self.mod.__dir__()
        self.assertIsInstance(names, list)
        self.assertIn("sentinel_attr", names)


# ---------------------------------------------------------------------------
# 1.238–1.239  verify_ai_assets shim
# ---------------------------------------------------------------------------


class TestCluster01ab_VerifyAiAssetsShim(TestCase):
    """``lex/tools/verify_ai_assets.py`` — shim re-export contract.

    Scenario 1.238–1.239.
    """

    def setUp(self):
        self.mod = _load_verify_ai_assets()

    def test_1_238_getattr_delegates_to_impl(self):
        """1.238: ``__getattr__`` on the shim returns attributes from the mock impl.

        Scenario 1.238
        Given the shim wraps ``lex_mcp.ai_assets``
        When ``shim.sentinel_attr`` is accessed
        Then the value from the mock impl is returned
        """
        value = getattr(self.mod, "sentinel_attr", None)
        self.assertEqual(value, "ai_assets_mock")

    def test_1_239_dir_delegates_to_impl(self):
        """1.239: ``__dir__`` on the shim returns the impl's public namespace.

        Scenario 1.239
        Given the shim defines ``__dir__``
        When ``dir(shim)`` is called
        Then the result is a list and includes names exported by the mock impl
        """
        names = self.mod.__dir__()
        self.assertIsInstance(names, list)
        self.assertIn("sentinel_attr", names)


# ---------------------------------------------------------------------------
# 1.240–1.248  lex.mcp_server.tools.embed — URL / classification helpers
# ---------------------------------------------------------------------------


class TestCluster01ab_EmbedHelpers(TestCase):
    """``lex/mcp_server/tools/embed.py`` — pure URL utilities.

    Scenario 1.240–1.248.
    """

    def setUp(self):
        self.embed = _load_embed_module()

    # -- _classify_path -------------------------------------------------------

    def test_1_240_classify_path_single_segment_is_list(self):
        """1.240: A single-segment path is classified as ``"list"``.

        Scenario 1.240
        Given segments = ["users"]
        When ``_classify_path`` is called
        Then ``"list"`` is returned
        """
        self.assertEqual(self.embed._classify_path(["users"]), "list")

    def test_1_241_classify_path_create_segment(self):
        """1.241: A path ending in ``create`` is classified as ``"create"``.

        Scenario 1.241
        Given segments = ["orders", "create"]
        When ``_classify_path`` is called
        Then ``"create"`` is returned
        """
        self.assertEqual(self.embed._classify_path(["orders", "create"]), "create")

    def test_1_242_classify_path_numeric_id_is_detail(self):
        """1.242: A numeric second segment indicates a ``"detail"`` view.

        Scenario 1.242
        Given segments = ["orders", "42"]
        When ``_classify_path`` is called
        Then ``"detail"`` is returned
        """
        self.assertEqual(self.embed._classify_path(["orders", "42"]), "detail")

    def test_1_243_classify_path_id_edit_is_edit(self):
        """1.243: ``/{resource}/{id}/edit`` is classified as ``"edit"``.

        Scenario 1.243
        Given segments = ["orders", "42", "edit"]
        When ``_classify_path`` is called
        Then ``"edit"`` is returned
        """
        self.assertEqual(self.embed._classify_path(["orders", "42", "edit"]), "edit")

    # -- _build_title ---------------------------------------------------------

    def test_1_244_build_title_with_container(self):
        """1.244: ``_build_title`` uses the container's ``verbose_name``.

        Scenario 1.244
        Given a container with ``verbose_name = "order"``
        When ``_build_title("orders", "list", container)`` is called
        Then the title includes the container's verbose name and view type label
        """
        container = types.SimpleNamespace(verbose_name="order")
        title = self.embed._build_title("orders", "list", container)
        self.assertIn("Order", title)
        self.assertIn("List View", title)

    def test_1_245_build_title_without_container(self):
        """1.245: ``_build_title`` falls back to the resource slug when no container.

        Scenario 1.245
        Given ``container=None`` and resource ``"user_accounts"``
        When ``_build_title("user_accounts", "detail", None)`` is called
        Then the title contains a human-readable form of ``"user_accounts"``
        """
        title = self.embed._build_title("user_accounts", "detail", None)
        self.assertIn("Detail View", title)
        self.assertIn("User Accounts", title)

    # -- _resolve_frontend_url ------------------------------------------------

    def test_1_246_resolve_frontend_url_defaults_to_localhost(self):
        """1.246: ``_resolve_frontend_url`` falls back to ``http://localhost:8000``.

        Scenario 1.246
        Given no config and no ``REACT_APP_URL`` / ``LEX_FRONTEND_URL`` env vars
        When ``_resolve_frontend_url()`` is called
        Then ``"http://localhost:8000"`` is returned
        """
        with patch.dict(os.environ, {}, clear=False):
            for var in ("REACT_APP_URL", "LEX_FRONTEND_URL"):
                os.environ.pop(var, None)
            url = self.embed._resolve_frontend_url()
        self.assertEqual(url, "http://localhost:8000")

    def test_1_247_resolve_frontend_url_uses_react_app_url_env(self):
        """1.247: ``_resolve_frontend_url`` prefers ``REACT_APP_URL`` over the fallback.

        Scenario 1.247
        Given ``REACT_APP_URL`` is set to ``"https://app.example.com"``
        When ``_resolve_frontend_url()`` is called
        Then ``"https://app.example.com"`` is returned
        """
        with patch.dict(os.environ, {"REACT_APP_URL": "https://app.example.com"}):
            url = self.embed._resolve_frontend_url()
        self.assertEqual(url, "https://app.example.com")

    # -- _build_embed_url -----------------------------------------------------

    def test_1_248_build_embed_url_appends_embed_param(self):
        """1.248: ``_build_embed_url`` always injects ``?embed=true`` into the URL.

        Scenario 1.248
        Given ``REACT_APP_URL`` points to ``"http://localhost:8000"``
        When ``_build_embed_url("/orders")`` is called
        Then the returned URL contains ``embed=true``
        """
        with patch.dict(os.environ, {"REACT_APP_URL": "http://localhost:8000"}):
            url = self.embed._build_embed_url("/orders")
        self.assertIn("embed=true", url)
        self.assertIn("/orders", url)
