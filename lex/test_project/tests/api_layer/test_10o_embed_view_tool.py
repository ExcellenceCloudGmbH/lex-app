"""MCP embed-view tool — URL construction and view-type classification.

Intent
------
``lex/mcp_server/tools/embed.py`` implements the **MCP Apps** embed pattern:
it returns a structured ``embed_url`` (plus narration text for the model) so
that a compliant MCP Apps host can render the React frontend inside an iframe
widget rather than just displaying a plain URL.  The customer-visible contract
is:

* ``_classify_path`` maps URL segments to a *view type* (list, create, detail,
  edit, custom) that describes what kind of React screen the user will see.
* ``_build_embed_url`` always appends ``embed=true`` and the ``#embed``
  fragment, and honours every optional toggle (hide_toolbar, hide_actions,
  hide_actions_column) and redirect overrides.
* ``_resolve_frontend_url`` respects the documented priority order: explicit
  setting > ``REACT_APP_URL`` env var > ``LEX_FRONTEND_URL`` env var >
  localhost fallback.
* ``_csp_origins`` always includes the frontend origin and appends any
  ``EMBED_EXTRA_CSP_ORIGINS`` without duplicates.
* The async tool handler returns two ``TextContent`` blocks: a narration for
  the model (which must *not* contain the auth token), and a JSON block with
  ``embed_url`` (which *does* contain the auth token so the widget can
  bootstrap an authenticated iframe session).

Cluster 10o — scenarios 10.72–10.80. Type: U.
Covers: ``lex/mcp_server/tools/embed.py``.
Run: python -m lex pytest lex/test_project/tests/api_layer/test_10o_embed_view_tool.py -v
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
import types
import unittest
import urllib.parse
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

pytestmark = pytest.mark.api_layer

# ---------------------------------------------------------------------------
# Bootstrap: stub the modules embed.py needs that are NOT installed in the
# test environment (lex.mcp_server.config / .registry / .context, fastmcp).
# We create minimal fakes so the module can be imported and its pure helpers
# tested without a live MCP server or the full lex-mcp-local stack.
#
# ``lex/mcp_server/`` has no __init__.py files, so the directory is not a
# normal Python package.  We use importlib.util.spec_from_file_location to
# load embed.py by path and inject it under the dotted name it expects.
# ---------------------------------------------------------------------------

_EMBED_PATH = (
    Path(__file__).resolve().parents[3]
    / "mcp_server" / "tools" / "embed.py"
)


def _make_pkg(name: str) -> types.ModuleType:
    m = types.ModuleType(name)
    m.__path__ = []   # marks it as a package
    m.__package__ = name
    return m


def _build_stub_modules() -> dict[str, types.ModuleType]:
    """Return a dict of fake modules to inject into sys.modules."""
    stubs: dict[str, types.ModuleType] = {}

    # lex.mcp_server namespace (namespace packages, no __init__) --------------
    stubs["lex.mcp_server"] = _make_pkg("lex.mcp_server")
    stubs["lex.mcp_server.tools"] = _make_pkg("lex.mcp_server.tools")

    config_mod = types.ModuleType("lex.mcp_server.config")
    config_mod.mcp_setting = MagicMock(return_value=None)
    stubs["lex.mcp_server.config"] = config_mod

    registry_mod = types.ModuleType("lex.mcp_server.registry")
    registry_mod.get_container = MagicMock(side_effect=Exception("no container"))
    registry_mod.container_is_writable = MagicMock(return_value=False)
    stubs["lex.mcp_server.registry"] = registry_mod

    context_mod = types.ModuleType("lex.mcp_server.context")
    _principal = MagicMock()
    _principal.access_token = None
    context_mod.current_principal = MagicMock(return_value=_principal)
    stubs["lex.mcp_server.context"] = context_mod

    # fastmcp stubs -----------------------------------------------------------
    fastmcp_pkg = _make_pkg("fastmcp")
    fastmcp_pkg.FastMCP = MagicMock()
    stubs["fastmcp"] = fastmcp_pkg

    resources_mod = types.ModuleType("fastmcp.resources")
    resources_mod.FunctionResource = MagicMock()
    stubs["fastmcp.resources"] = resources_mod

    tools_mod = types.ModuleType("fastmcp.tools")
    tools_mod.Tool = MagicMock()
    stubs["fastmcp.tools"] = tools_mod

    # mcp.types ---------------------------------------------------------------
    mcp_pkg = _make_pkg("mcp")
    stubs["mcp"] = mcp_pkg

    mcp_types_mod = types.ModuleType("mcp.types")

    class _TextContent:
        def __init__(self, *, type: str, text: str) -> None:
            self.type = type
            self.text = text

    mcp_types_mod.TextContent = _TextContent
    mcp_types_mod.ToolAnnotations = MagicMock()
    stubs["mcp.types"] = mcp_types_mod

    return stubs


_STUBS = _build_stub_modules()
_ORIG_MODULES: dict[str, Any] = {}
_EMBED_MODULE: Any = None


def setUpModule() -> None:  # noqa: N802
    global _EMBED_MODULE
    for name, mod in _STUBS.items():
        _ORIG_MODULES[name] = sys.modules.get(name)
        sys.modules[name] = mod

    # Load embed.py by path (no __init__.py in its directory).
    spec = importlib.util.spec_from_file_location(
        "lex.mcp_server.tools.embed",
        str(_EMBED_PATH),
    )
    _EMBED_MODULE = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    sys.modules["lex.mcp_server.tools.embed"] = _EMBED_MODULE
    spec.loader.exec_module(_EMBED_MODULE)  # type: ignore[union-attr]


def tearDownModule() -> None:  # noqa: N802
    sys.modules.pop("lex.mcp_server.tools.embed", None)
    for name, orig in _ORIG_MODULES.items():
        if orig is None:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = orig


def _embed() -> Any:
    """Return the already-loaded embed module."""
    return _EMBED_MODULE


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_qs(url: str) -> dict[str, list[str]]:
    return urllib.parse.parse_qs(urllib.parse.urlparse(url).query)


def _fragment(url: str) -> str:
    return urllib.parse.urlparse(url).fragment


# ===========================================================================
# Cluster 10o — embed-view tool unit tests
# ===========================================================================


class TestCluster10o_ClassifyPath(unittest.TestCase):
    """Cluster 10o: _classify_path maps URL segment patterns to view types."""

    def _classify(self, *segments: str) -> str:
        return _embed()._classify_path(list(segments))

    # 10.72 ------------------------------------------------------------------
    def test_10_72_empty_segments_returns_custom(self) -> None:
        """
        Scenario 10.72: empty path segments → custom
        Given: no path segments
        When: _classify_path([]) is called
        Then: returns "custom"
        """
        self.assertEqual(self._classify(), "custom")

    # 10.73 ------------------------------------------------------------------
    def test_10_73_single_segment_returns_list(self) -> None:
        """
        Scenario 10.73: single segment (resource only) → list view
        Given: ["quarter"]
        When: _classify_path is called
        Then: returns "list"
        """
        self.assertEqual(self._classify("quarter"), "list")

    # 10.74 ------------------------------------------------------------------
    def test_10_74_segment_create_returns_create(self) -> None:
        """
        Scenario 10.74: resource/create → create view
        Given: ["quarter", "create"]
        When: _classify_path is called
        Then: returns "create"
        """
        self.assertEqual(self._classify("quarter", "create"), "create")

    # 10.75 ------------------------------------------------------------------
    def test_10_75_segment_numeric_id_returns_detail(self) -> None:
        """
        Scenario 10.75: resource/42 → detail view
        Given: ["quarter", "42"]
        When: _classify_path is called
        Then: returns "detail"
        """
        self.assertEqual(self._classify("quarter", "42"), "detail")

    def test_10_75b_segment_uuid_id_returns_detail(self) -> None:
        """UUID ids are also recognised as detail."""
        self.assertEqual(
            self._classify("quarter", "550e8400-e29b-41d4-a716-446655440000"),
            "detail",
        )

    # 10.76 ------------------------------------------------------------------
    def test_10_76_segment_id_edit_returns_edit(self) -> None:
        """
        Scenario 10.76: resource/{id}/edit → edit view
        Given: ["quarter", "42", "edit"]
        When: _classify_path is called
        Then: returns "edit"
        """
        self.assertEqual(self._classify("quarter", "42", "edit"), "edit")

    def test_10_76b_show_id_returns_detail(self) -> None:
        """resource/show/{id} → detail view."""
        self.assertEqual(self._classify("quarter", "show", "42"), "detail")

    def test_10_76c_unknown_two_segment_returns_custom(self) -> None:
        """resource/something-else → custom (not a known pattern)."""
        self.assertEqual(self._classify("quarter", "something-else"), "custom")


class TestCluster10o_BuildEmbedUrl(unittest.TestCase):
    """Cluster 10o: _build_embed_url produces well-formed URLs."""

    def setUp(self) -> None:
        # Patch mcp_setting to return None so _resolve_frontend_url falls
        # back to the environment / localhost.
        self._patcher = patch.object(
            _embed(), "mcp_setting", return_value=None
        )
        self._patcher.start()

    def tearDown(self) -> None:
        self._patcher.stop()

    def _build(self, path: str = "/quarter", **kwargs) -> str:
        with patch.dict("os.environ", {"REACT_APP_URL": "http://test.example"}, clear=False):
            return _embed()._build_embed_url(path, **kwargs)

    # 10.77 ------------------------------------------------------------------
    def test_10_77_embed_param_always_present(self) -> None:
        """
        Scenario 10.77: embed=true query param is always added
        Given: any path
        When: _build_embed_url is called with defaults
        Then: the URL contains embed=true
        """
        url = self._build("/quarter")
        params = _parse_qs(url)
        self.assertEqual(params.get("embed"), ["true"])

    def test_10_77b_embed_fragment_always_present(self) -> None:
        """#embed fragment is always appended."""
        url = self._build("/quarter")
        self.assertIn("embed", _fragment(url))

    # 10.78 ------------------------------------------------------------------
    def test_10_78_hide_toolbar_toggle(self) -> None:
        """
        Scenario 10.78: hide_toolbar=True adds hide_toolbar=true to params
        Given: hide_toolbar=True
        When: _build_embed_url is called
        Then: URL contains hide_toolbar=true
        """
        url = self._build("/quarter", hide_toolbar=True)
        self.assertEqual(_parse_qs(url).get("hide_toolbar"), ["true"])

    def test_10_78b_hide_toolbar_false_omits_param(self) -> None:
        """hide_toolbar=False (default) must not add the param."""
        url = self._build("/quarter", hide_toolbar=False)
        self.assertNotIn("hide_toolbar", _parse_qs(url))

    def test_10_78c_hide_actions_toggle(self) -> None:
        """hide_actions=True adds hide_actions=true."""
        url = self._build("/quarter", hide_actions=True)
        self.assertEqual(_parse_qs(url).get("hide_actions"), ["true"])

    def test_10_78d_hide_actions_column_toggle(self) -> None:
        """hide_actions_column=True adds hide_actions_column=true."""
        url = self._build("/quarter", hide_actions_column=True)
        self.assertEqual(_parse_qs(url).get("hide_actions_column"), ["true"])

    def test_10_78e_redirect_after_param(self) -> None:
        """redirect_after is forwarded as a query param."""
        url = self._build("/quarter/create", redirect_after="/dashboard")
        self.assertIn("/dashboard", urllib.parse.unquote(url))

    def test_10_78f_extra_params_forwarded(self) -> None:
        """extra_params dict is merged into the query string."""
        url = self._build("/quarter", extra_params={"foo": "bar"})
        self.assertEqual(_parse_qs(url).get("foo"), ["bar"])


class TestCluster10o_ResolveFrontendUrl(unittest.TestCase):
    """Cluster 10o: _resolve_frontend_url respects the documented priority."""

    # 10.79 ------------------------------------------------------------------
    def test_10_79_explicit_setting_wins(self) -> None:
        """
        Scenario 10.79: FRONTEND_BASE_URL setting takes priority over env vars
        Given: mcp_setting returns a URL
        When: _resolve_frontend_url is called
        Then: the configured URL is returned (no env var inspection)
        """
        with patch.object(_embed(), "mcp_setting", return_value="https://configured.example"):
            url = _embed()._resolve_frontend_url()
        self.assertEqual(url, "https://configured.example")

    def test_10_79b_react_app_url_env_var(self) -> None:
        """REACT_APP_URL env var is used when no setting is configured."""
        with patch.object(_embed(), "mcp_setting", return_value=None):
            with patch.dict("os.environ", {"REACT_APP_URL": "https://react.example"}, clear=False):
                url = _embed()._resolve_frontend_url()
        self.assertEqual(url, "https://react.example")

    def test_10_79c_lex_frontend_url_fallback(self) -> None:
        """LEX_FRONTEND_URL is used when REACT_APP_URL is absent."""
        env = {"LEX_FRONTEND_URL": "https://lex.example"}
        with patch.object(_embed(), "mcp_setting", return_value=None):
            with patch.dict("os.environ", env, clear=False):
                # Remove REACT_APP_URL if present so the fallback is reached.
                with patch.dict("os.environ", {"REACT_APP_URL": ""}, clear=False):
                    url = _embed()._resolve_frontend_url()
        self.assertEqual(url, "https://lex.example")

    def test_10_79d_localhost_last_resort(self) -> None:
        """http://localhost:8000 is used when no setting or env var is present."""
        with patch.object(_embed(), "mcp_setting", return_value=None):
            with patch.dict("os.environ", {}, clear=False):
                # Unset both env vars so the fallback kicks in.
                import os
                orig_react = os.environ.pop("REACT_APP_URL", None)
                orig_lex = os.environ.pop("LEX_FRONTEND_URL", None)
                try:
                    url = _embed()._resolve_frontend_url()
                finally:
                    if orig_react is not None:
                        os.environ["REACT_APP_URL"] = orig_react
                    if orig_lex is not None:
                        os.environ["LEX_FRONTEND_URL"] = orig_lex
        self.assertEqual(url, "http://localhost:8000")


class TestCluster10o_CspOrigins(unittest.TestCase):
    """Cluster 10o: _csp_origins always includes the frontend origin."""

    def test_10_79e_frontend_origin_always_included(self) -> None:
        """The frontend origin is always the first entry in CSP origins."""
        with patch.object(_embed(), "mcp_setting", side_effect=lambda k: {
            "FRONTEND_BASE_URL": "https://app.example",
            "EMBED_EXTRA_CSP_ORIGINS": None,
        }.get(k)):
            origins = _embed()._csp_origins()
        self.assertIn("https://app.example", origins)

    def test_10_79f_extra_origins_appended(self) -> None:
        """EMBED_EXTRA_CSP_ORIGINS are appended without duplicating the base."""
        with patch.object(_embed(), "mcp_setting", side_effect=lambda k: {
            "FRONTEND_BASE_URL": "https://app.example",
            "EMBED_EXTRA_CSP_ORIGINS": ["https://auth.example", "https://app.example"],
        }.get(k)):
            origins = _embed()._csp_origins()
        # The base URL must not appear twice.
        self.assertEqual(origins.count("https://app.example"), 1)
        self.assertIn("https://auth.example", origins)


def _ctx_stub() -> types.ModuleType:
    """Return the context stub module so tests can patch current_principal."""
    return sys.modules["lex.mcp_server.context"]


class TestCluster10o_EmbedViewInner(unittest.IsolatedAsyncioTestCase):
    """Cluster 10o: _embed_view_inner returns the right content blocks."""

    # current_principal is imported *inside* _embed_view_inner (lazy import),
    # so we must patch it on the context stub module rather than on embed.

    def setUp(self) -> None:
        embed = _embed()
        self._mcp_setting_patcher = patch.object(
            embed, "mcp_setting",
            side_effect=lambda k: "http://front.example" if k == "FRONTEND_BASE_URL" else None,
        )
        self._get_container_patcher = patch.object(
            embed, "get_container", side_effect=Exception("no container"),
        )
        self._mcp_setting_patcher.start()
        self._get_container_patcher.start()

        # Default principal: no token.
        _principal = MagicMock()
        _principal.access_token = None
        _ctx_stub().current_principal = MagicMock(return_value=_principal)

    def tearDown(self) -> None:
        self._mcp_setting_patcher.stop()
        self._get_container_patcher.stop()

    # 10.80 ------------------------------------------------------------------
    async def test_10_80_two_content_blocks_returned(self) -> None:
        """
        Scenario 10.80: _embed_view_inner returns [narration, JSON] blocks
        Given: a simple path with no auth token
        When: _embed_view_inner("quarter") is called
        Then: two TextContent blocks are returned; the second parses as JSON
              with an embed_url key
        """
        result = await _embed()._embed_view_inner(path="quarter")

        self.assertEqual(len(result), 2)
        narration_block, json_block = result
        self.assertEqual(narration_block.type, "text")
        # The second block must be valid JSON with embed_url.
        payload = json.loads(json_block.text)
        self.assertIn("embed_url", payload)
        self.assertIn("view_type", payload)

    async def test_10_80b_narration_does_not_contain_auth_token(self) -> None:
        """The narration text block must not expose the auth_token."""
        _principal = MagicMock()
        _principal.access_token = "secret-bearer-xyz"
        _ctx_stub().current_principal = MagicMock(return_value=_principal)

        result = await _embed()._embed_view_inner(path="quarter")

        narration = result[0].text
        self.assertNotIn("secret-bearer-xyz", narration)

    async def test_10_80c_structured_url_contains_auth_token(self) -> None:
        """The structured JSON block embed_url DOES contain the auth_token for the widget."""
        _principal = MagicMock()
        _principal.access_token = "secret-bearer-xyz"
        _ctx_stub().current_principal = MagicMock(return_value=_principal)

        result = await _embed()._embed_view_inner(path="quarter")

        payload = json.loads(result[1].text)
        self.assertIn("secret-bearer-xyz", payload["embed_url"])

    async def test_10_80d_view_type_in_structured(self) -> None:
        """view_type in the JSON reflects _classify_path output for the given path."""
        result = await _embed()._embed_view_inner(path="quarter")
        payload = json.loads(result[1].text)
        self.assertEqual(payload["view_type"], "list")

    async def test_10_80e_resource_in_structured(self) -> None:
        """resource field in JSON is the first path segment."""
        result = await _embed()._embed_view_inner(path="quarter/42/edit")
        payload = json.loads(result[1].text)
        self.assertEqual(payload["resource"], "quarter")
        self.assertEqual(payload["view_type"], "edit")
