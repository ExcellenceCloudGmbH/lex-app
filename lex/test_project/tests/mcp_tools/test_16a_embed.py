"""Cluster 16a — MCP embed-view tool: URL building and path classification.

Intent (from lex/mcp_server/tools/embed.py):

    The ``lex_embed_view`` MCP tool builds a fully-qualified embed URL for
    the React frontend and returns structured content that compliant MCP Apps
    hosts can render inside an iframe widget.

    Key behaviours under test:

    * ``_classify_path`` maps URL segments to the correct view type
      (list / create / detail / edit / custom).
    * ``_build_title`` produces human-readable titles from a resource name
      and/or a container's ``verbose_name``.
    * ``_resolve_frontend_url`` respects the four-level priority chain:
      configured setting → REACT_APP_URL → LEX_FRONTEND_URL → localhost fallback.
    * ``_csp_origins`` always includes the frontend origin and deduplicates
      extra origins.
    * ``_build_embed_url`` always appends ``embed=true`` and the ``#embed``
      fragment, and surfaces optional visibility/redirect parameters.
"""

from __future__ import annotations

import importlib
import os
import sys
import types
import unittest.mock as mock

import pytest

pytestmark = pytest.mark.mcp_tools


# ---------------------------------------------------------------------------
# Module-level setup: stub out unavailable packages before importing embed.
# ---------------------------------------------------------------------------

def _import_embed():
    """Import lex/mcp_server/tools/embed.py with all external deps stubbed.

    The mcp_server directory has no __init__.py files so it cannot be imported
    as a regular package.  We load the source file directly via
    ``importlib.util.spec_from_file_location`` after injecting stub modules for
    every unavailable dependency (``mcp.*`` and ``lex.mcp_server.*``).
    """
    import importlib.util as _ilu
    from pathlib import Path as _Path

    # File is at lex/test_project/tests/mcp_tools/test_16a_embed.py
    # parents[3] = lex/
    embed_path = (
        _Path(__file__).resolve().parents[3]
        / "mcp_server" / "tools" / "embed.py"
    )

    # Build stub modules for unavailable deps.
    config_mod = types.ModuleType("lex.mcp_server.config")
    config_mod.mcp_setting = mock.MagicMock(return_value=None)
    registry_mod = types.ModuleType("lex.mcp_server.registry")
    registry_mod.container_is_writable = mock.MagicMock(return_value=True)
    registry_mod.get_container = mock.MagicMock(return_value=None)

    mcp_pkg = types.ModuleType("mcp")
    mcp_server = types.ModuleType("mcp.server")
    mcp_fastmcp = types.ModuleType("mcp.server.fastmcp")
    mcp_fastmcp.FastMCP = mock.MagicMock()
    mcp_resources = types.ModuleType("mcp.server.fastmcp.resources")
    mcp_resources.FunctionResource = mock.MagicMock()
    mcp_types = types.ModuleType("mcp.types")
    mcp_types.TextContent = mock.MagicMock()
    mcp_types.ToolAnnotations = mock.MagicMock()

    mcp_server_pkg = types.ModuleType("lex.mcp_server")
    mcp_server_pkg.__path__ = []
    mcp_tools_pkg = types.ModuleType("lex.mcp_server.tools")
    mcp_tools_pkg.__path__ = []

    stubs = {
        "lex.mcp_server": mcp_server_pkg,
        "lex.mcp_server.tools": mcp_tools_pkg,
        "lex.mcp_server.config": config_mod,
        "lex.mcp_server.registry": registry_mod,
        "mcp": mcp_pkg,
        "mcp.server": mcp_server,
        "mcp.server.fastmcp": mcp_fastmcp,
        "mcp.server.fastmcp.resources": mcp_resources,
        "mcp.types": mcp_types,
    }
    prev = {name: sys.modules.get(name) for name in stubs}
    for name, mod in stubs.items():
        sys.modules[name] = mod

    sys.modules.pop("lex.mcp_server.tools.embed", None)

    try:
        spec = _ilu.spec_from_file_location("lex.mcp_server.tools.embed", embed_path)
        embed_mod = _ilu.module_from_spec(spec)
        sys.modules["lex.mcp_server.tools.embed"] = embed_mod
        spec.loader.exec_module(embed_mod)
    finally:
        for name, orig in prev.items():
            if orig is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = orig

    return embed_mod, config_mod


_embed, _config_mod = _import_embed()
_classify_path = _embed._classify_path
_build_title = _embed._build_title
_resolve_frontend_url = _embed._resolve_frontend_url
_csp_origins = _embed._csp_origins
_build_embed_url = _embed._build_embed_url


# ---------------------------------------------------------------------------
# 16a — _classify_path
# ---------------------------------------------------------------------------


class TestCluster16a_ClassifyPath:
    """Path-segment → view-type classification (``_classify_path``)."""

    def test_16_1_empty_segments_is_custom(self):
        """Scenario 16.1: empty segment list → 'custom'."""
        assert _classify_path([]) == "custom"

    def test_16_2_single_segment_is_list(self):
        """Scenario 16.2: one segment → 'list'."""
        assert _classify_path(["orders"]) == "list"

    def test_16_3_resource_create_is_create(self):
        """Scenario 16.3: /resource/create → 'create'."""
        assert _classify_path(["orders", "create"]) == "create"

    def test_16_4_resource_numeric_id_is_detail(self):
        """Scenario 16.4: /resource/42 → 'detail'."""
        assert _classify_path(["orders", "42"]) == "detail"

    def test_16_5_resource_uuid_is_detail(self):
        """Scenario 16.5: /resource/<uuid> → 'detail'."""
        assert _classify_path(["orders", "123e4567-e89b-12d3-a456-426614174000"]) == "detail"

    def test_16_6_resource_id_edit_is_edit(self):
        """Scenario 16.6: /resource/42/edit → 'edit'."""
        assert _classify_path(["orders", "42", "edit"]) == "edit"

    def test_16_7_resource_show_id_is_detail(self):
        """Scenario 16.7: /resource/show/42 → 'detail'."""
        assert _classify_path(["orders", "show", "42"]) == "detail"

    def test_16_8_unknown_pattern_is_custom(self):
        """Scenario 16.8: unrecognised pattern → 'custom'."""
        assert _classify_path(["orders", "foo", "bar"]) == "custom"

    def test_16_9_two_non_id_segments_is_custom(self):
        """Scenario 16.9: two non-ID, non-'create' segments → 'custom'."""
        assert _classify_path(["orders", "filter"]) == "custom"


# ---------------------------------------------------------------------------
# 16a — _build_title
# ---------------------------------------------------------------------------


class TestCluster16a_BuildTitle:
    """Title generation (``_build_title``)."""

    def test_16_10_resource_only_title_cased(self):
        """Scenario 16.10: resource name → title-cased resource + label suffix."""
        title = _build_title("my_orders", "list", None)
        assert "My Orders" in title
        assert "List View" in title
        assert "(embedded)" in title

    def test_16_11_container_with_verbose_name(self):
        """Scenario 16.11: container with verbose_name → verbose_name used."""
        container = mock.MagicMock()
        container.verbose_name = "Customer Order"
        title = _build_title("orders", "detail", container)
        assert "Customer Order" in title
        assert "Detail View" in title

    def test_16_12_container_without_verbose_name_falls_back_to_resource(self):
        """Scenario 16.12: container but no verbose_name → resource name used."""
        container = mock.MagicMock()
        container.verbose_name = None
        title = _build_title("invoices", "create", container)
        assert "Invoices" in title
        assert "Create Form" in title

    def test_16_13_no_resource_no_container_is_application(self):
        """Scenario 16.13: no resource and no container → 'Application'."""
        title = _build_title(None, "custom", None)
        assert "Application" in title

    def test_16_14_unknown_view_type_falls_back_to_view(self):
        """Scenario 16.14: unrecognised view_type → 'View' suffix."""
        title = _build_title("items", "unknown_type", None)
        assert "View" in title


# ---------------------------------------------------------------------------
# 16a — _resolve_frontend_url
# ---------------------------------------------------------------------------


class TestCluster16a_ResolveFrontendUrl:
    """Frontend URL resolution priority chain (``_resolve_frontend_url``)."""

    def test_16_15_configured_setting_wins(self):
        """Scenario 16.15: FRONTEND_BASE_URL setting returns that value."""
        _embed.mcp_setting = mock.MagicMock(return_value="https://app.example.com/")
        try:
            url = _resolve_frontend_url()
        finally:
            _embed.mcp_setting = mock.MagicMock(return_value=None)
        assert url == "https://app.example.com"

    def test_16_16_react_app_url_env_var(self):
        """Scenario 16.16: REACT_APP_URL env var used when no setting."""
        _embed.mcp_setting = mock.MagicMock(return_value=None)
        with mock.patch.dict(os.environ, {"REACT_APP_URL": "http://react.local:3000"}):
            url = _resolve_frontend_url()
        assert url == "http://react.local:3000"

    def test_16_17_lex_frontend_url_env_var_fallback(self):
        """Scenario 16.17: LEX_FRONTEND_URL used when REACT_APP_URL absent."""
        _embed.mcp_setting = mock.MagicMock(return_value=None)
        env = {k: v for k, v in os.environ.items() if k not in ("REACT_APP_URL", "LEX_FRONTEND_URL")}
        env["LEX_FRONTEND_URL"] = "http://lex.local:8080"
        with mock.patch.dict(os.environ, env, clear=True):
            url = _resolve_frontend_url()
        assert url == "http://lex.local:8080"

    def test_16_18_default_localhost_fallback(self):
        """Scenario 16.18: no setting, no env vars → http://localhost:8000."""
        _embed.mcp_setting = mock.MagicMock(return_value=None)
        env = {k: v for k, v in os.environ.items() if k not in ("REACT_APP_URL", "LEX_FRONTEND_URL")}
        with mock.patch.dict(os.environ, env, clear=True):
            url = _resolve_frontend_url()
        assert url == "http://localhost:8000"


# ---------------------------------------------------------------------------
# 16a — _csp_origins
# ---------------------------------------------------------------------------


class TestCluster16a_CspOrigins:
    """CSP origin list building (``_csp_origins``)."""

    def test_16_19_frontend_origin_always_included(self):
        """Scenario 16.19: frontend origin always present in result."""
        _embed.mcp_setting = mock.MagicMock(return_value=None)
        env = {k: v for k, v in os.environ.items() if k not in ("REACT_APP_URL", "LEX_FRONTEND_URL")}
        with mock.patch.dict(os.environ, env, clear=True):
            origins = _csp_origins()
        assert "http://localhost:8000" in origins

    def test_16_20_extra_csp_origins_appended(self):
        """Scenario 16.20: EMBED_EXTRA_CSP_ORIGINS are appended."""
        def _setting(key):
            if key == "EMBED_EXTRA_CSP_ORIGINS":
                return ["https://auth.example.com"]
            return None
        _embed.mcp_setting = _setting
        env = {k: v for k, v in os.environ.items() if k not in ("REACT_APP_URL", "LEX_FRONTEND_URL")}
        with mock.patch.dict(os.environ, env, clear=True):
            origins = _csp_origins()
        assert "https://auth.example.com" in origins
        _embed.mcp_setting = mock.MagicMock(return_value=None)

    def test_16_21_duplicate_origins_suppressed(self):
        """Scenario 16.21: duplicate extras are de-duplicated."""
        def _setting(key):
            if key == "EMBED_EXTRA_CSP_ORIGINS":
                return ["http://localhost:8000", "http://localhost:8000"]
            return None
        _embed.mcp_setting = _setting
        env = {k: v for k, v in os.environ.items() if k not in ("REACT_APP_URL", "LEX_FRONTEND_URL")}
        with mock.patch.dict(os.environ, env, clear=True):
            origins = _csp_origins()
        assert origins.count("http://localhost:8000") == 1
        _embed.mcp_setting = mock.MagicMock(return_value=None)


# ---------------------------------------------------------------------------
# 16a — _build_embed_url
# ---------------------------------------------------------------------------


class TestCluster16a_BuildEmbedUrl:
    """Embed URL construction (``_build_embed_url``)."""

    def _url_for(self, path, **kwargs):
        _embed.mcp_setting = mock.MagicMock(return_value=None)
        env = {k: v for k, v in os.environ.items() if k not in ("REACT_APP_URL", "LEX_FRONTEND_URL")}
        with mock.patch.dict(os.environ, env, clear=True):
            return _build_embed_url(path, **kwargs)

    def test_16_22_embed_param_always_set(self):
        """Scenario 16.22: embed=true query param is always present."""
        url = self._url_for("/orders")
        assert "embed=true" in url

    def test_16_23_embed_fragment_always_set(self):
        """Scenario 16.23: #embed fragment is always appended."""
        url = self._url_for("/orders")
        assert url.endswith("#embed") or "#embed" in url

    def test_16_24_hide_toolbar_param(self):
        """Scenario 16.24: hide_toolbar=True adds hide_toolbar=true param."""
        url = self._url_for("/orders", hide_toolbar=True)
        assert "hide_toolbar=true" in url

    def test_16_25_redirect_after_param(self):
        """Scenario 16.25: redirect_after is passed through."""
        url = self._url_for("/orders/create", redirect_after="/orders")
        assert "redirect_after" in url

    def test_16_26_extra_params_included(self):
        """Scenario 16.26: extra_params dict values appear in URL."""
        url = self._url_for("/orders", extra_params={"foo": "bar"})
        assert "foo=bar" in url

    def test_16_27_path_without_leading_slash_normalised(self):
        """Scenario 16.27: path without leading slash gets one added."""
        url = self._url_for("orders")
        assert "/orders" in url
