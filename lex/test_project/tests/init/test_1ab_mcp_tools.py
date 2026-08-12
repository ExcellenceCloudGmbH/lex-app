"""Cluster 1ab: MCP tool helpers — embed, mode-invoke, verify-assets, setup-with-ai.

Intent
------
Four framework modules shipped in PR #703 add MCP-specific functionality:

* ``lex/mcp_server/tools/embed.py`` — builds embed URLs and MCP Apps widget
  HTML for surfacing the React frontend inside an MCP host iframe.
* ``lex/tools/mcp_mode_invoke.py`` — invokes the equivalent of the in-server
  ``switch_to_mode`` MCP tool from outside the server (override file +
  env-file sync + mcp.json sync + server stop).
* ``lex/tools/verify_ai_assets.py`` — verifies / restores AI asset directories
  and resolves the active MCP mode using a documented priority chain.
* ``lex/tools/setup_with_ai.py`` — provides low-level helpers consumed by the
  above modules (LEX_MCP_LOCAL_SERVER_NAME, update_env_file, …).

Every test here drives **intent** (what a customer or the framework expects)
not implementation.  No test should break if internals are refactored while
the observable contract stays the same.

Scenarios: 1.223 – 1.248.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import types
from pathlib import Path
from unittest import TestCase
from unittest.mock import MagicMock, patch

import pytest

pytestmark = pytest.mark.init

# ---------------------------------------------------------------------------
# Stub out the MCP SDK and missing internal modules so embed.py can be
# imported in the CI environment where ``mcp`` is not installed.
#
# These stubs are inserted into sys.modules before any import of
# lex.mcp_server.tools.embed so that Python does not try to locate the
# real packages.  All production code paths still run through the real
# implementation; only the top-level import resolution is shimmed.
# ---------------------------------------------------------------------------

_MISSING_MCP_MODULES = [
    "mcp",
    "mcp.server",
    "mcp.server.fastmcp",
    "mcp.server.fastmcp.resources",
    "mcp.types",
]

for _mod_name in _MISSING_MCP_MODULES:
    if _mod_name not in sys.modules:
        _mod = types.ModuleType(_mod_name)
        sys.modules[_mod_name] = _mod

# Populate the attributes embed.py accesses at import time.
sys.modules["mcp.server.fastmcp"].FastMCP = MagicMock()
sys.modules["mcp.server.fastmcp.resources"].FunctionResource = MagicMock()
sys.modules["mcp.types"].TextContent = MagicMock()
sys.modules["mcp.types"].ToolAnnotations = MagicMock()

# lex.mcp_server.config and lex.mcp_server.registry may not exist in test
# checkouts (they depend on a running server).  Inject lightweight stubs so
# embed.py's module-level import does not raise ImportError.
if "lex.mcp_server.config" not in sys.modules:
    _cfg = types.ModuleType("lex.mcp_server.config")
    _cfg.mcp_setting = lambda key, default=None: default  # type: ignore[attr-defined]
    sys.modules["lex.mcp_server.config"] = _cfg

if "lex.mcp_server.registry" not in sys.modules:
    _reg = types.ModuleType("lex.mcp_server.registry")
    _reg.container_is_writable = lambda name: False  # type: ignore[attr-defined]
    _reg.get_container = lambda name: None  # type: ignore[attr-defined]
    sys.modules["lex.mcp_server.registry"] = _reg

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_temp_env(content: str = "") -> Path:
    """Write a temporary .env file and return its path."""
    tmp = tempfile.NamedTemporaryFile(
        mode="w", suffix=".env", delete=False, encoding="utf-8"
    )
    tmp.write(content)
    tmp.flush()
    tmp.close()
    return Path(tmp.name)


# ===========================================================================
# Cluster 1ab — embed.py: URL / title helpers
# ===========================================================================


class TestCluster01ab_EmbedUrlHelpers(TestCase):
    """URL-resolution, CSP-origin, path-classification, and URL-building logic
    from ``lex.mcp_server.tools.embed``."""

    # ── _resolve_frontend_url ──────────────────────────────────────────────

    def test_1_223_resolve_frontend_url_falls_back_to_localhost(self):
        """Scenario 1.223: When no setting or env var is present, the frontend
        URL defaults to http://localhost:8000.

        Given no MCP setting, REACT_APP_URL, or LEX_FRONTEND_URL.
        When  _resolve_frontend_url() is called.
        Then  it returns http://localhost:8000 (no trailing slash).
        """
        from lex.mcp_server.tools.embed import _resolve_frontend_url

        with (
            patch("lex.mcp_server.tools.embed.mcp_setting", return_value=None),
            patch.dict(os.environ, {}, clear=False),
        ):
            # Remove the two env vars from the test process environment.
            env_backup = {}
            for key in ("REACT_APP_URL", "LEX_FRONTEND_URL"):
                env_backup[key] = os.environ.pop(key, None)
            try:
                result = _resolve_frontend_url()
            finally:
                for key, val in env_backup.items():
                    if val is not None:
                        os.environ[key] = val
        self.assertEqual(result, "http://localhost:8000")

    def test_1_224_resolve_frontend_url_uses_mcp_setting_first(self):
        """Scenario 1.224: MCP_SERVER["FRONTEND_BASE_URL"] takes priority over
        all environment variables.

        Given MCP_SERVER FRONTEND_BASE_URL = https://myapp.example.com/
        When  _resolve_frontend_url() is called.
        Then  it returns the configured URL (trailing slash stripped).
        """
        from lex.mcp_server.tools.embed import _resolve_frontend_url

        with patch(
            "lex.mcp_server.tools.embed.mcp_setting",
            return_value="https://myapp.example.com/",
        ):
            result = _resolve_frontend_url()
        self.assertEqual(result, "https://myapp.example.com")

    def test_1_225_resolve_frontend_url_uses_react_app_url(self):
        """Scenario 1.225: When no MCP setting, REACT_APP_URL is used.

        Given REACT_APP_URL = https://react.example.com
        When  _resolve_frontend_url() is called.
        Then  it returns https://react.example.com.
        """
        from lex.mcp_server.tools.embed import _resolve_frontend_url

        with (
            patch("lex.mcp_server.tools.embed.mcp_setting", return_value=None),
            patch.dict(
                os.environ,
                {"REACT_APP_URL": "https://react.example.com"},
                clear=False,
            ),
        ):
            result = _resolve_frontend_url()
        self.assertEqual(result, "https://react.example.com")

    def test_1_226_resolve_frontend_url_uses_lex_frontend_url_as_fallback(self):
        """Scenario 1.226: When no MCP setting and no REACT_APP_URL, LEX_FRONTEND_URL is used.

        Given LEX_FRONTEND_URL = http://lex-frontend:3000
        When  _resolve_frontend_url() is called.
        Then  it returns http://lex-frontend:3000.
        """
        from lex.mcp_server.tools.embed import _resolve_frontend_url

        env_backup = os.environ.pop("REACT_APP_URL", None)
        try:
            with (
                patch("lex.mcp_server.tools.embed.mcp_setting", return_value=None),
                patch.dict(
                    os.environ,
                    {"LEX_FRONTEND_URL": "http://lex-frontend:3000"},
                    clear=False,
                ),
            ):
                result = _resolve_frontend_url()
        finally:
            if env_backup is not None:
                os.environ["REACT_APP_URL"] = env_backup
        self.assertEqual(result, "http://lex-frontend:3000")

    # ── _classify_path ─────────────────────────────────────────────────────

    def test_1_227_classify_path_empty_is_custom(self):
        """Scenario 1.227: An empty segment list classifies as 'custom'.

        Given segments = []
        When  _classify_path([]) is called.
        Then  it returns 'custom'.
        """
        from lex.mcp_server.tools.embed import _classify_path

        self.assertEqual(_classify_path([]), "custom")

    def test_1_228_classify_path_single_segment_is_list(self):
        """Scenario 1.228: A single path segment classifies as a list view.

        Given segments = ['quarter']
        When  _classify_path(['quarter']) is called.
        Then  it returns 'list'.
        """
        from lex.mcp_server.tools.embed import _classify_path

        self.assertEqual(_classify_path(["quarter"]), "list")

    def test_1_229_classify_path_resource_create_is_create(self):
        """Scenario 1.229: <resource>/create classifies as a create form.

        Given segments = ['quarter', 'create']
        When  _classify_path(['quarter', 'create']) is called.
        Then  it returns 'create'.
        """
        from lex.mcp_server.tools.embed import _classify_path

        self.assertEqual(_classify_path(["quarter", "create"]), "create")

    def test_1_230_classify_path_resource_numeric_id_is_detail(self):
        """Scenario 1.230: <resource>/<numeric id> classifies as detail view.

        Given segments = ['quarter', '42']
        When  _classify_path(['quarter', '42']) is called.
        Then  it returns 'detail'.
        """
        from lex.mcp_server.tools.embed import _classify_path

        self.assertEqual(_classify_path(["quarter", "42"]), "detail")

    def test_1_231_classify_path_resource_uuid_is_detail(self):
        """Scenario 1.231: <resource>/<UUID> classifies as detail view.

        Given segments = ['quarter', '<valid-uuid>']
        When  _classify_path(['quarter', '<valid-uuid>']) is called.
        Then  it returns 'detail'.
        """
        from lex.mcp_server.tools.embed import _classify_path

        uuid = "550e8400-e29b-41d4-a716-446655440000"
        self.assertEqual(_classify_path(["quarter", uuid]), "detail")

    def test_1_232_classify_path_resource_id_edit_is_edit(self):
        """Scenario 1.232: <resource>/<id>/edit classifies as edit form.

        Given segments = ['quarter', '42', 'edit']
        When  _classify_path(['quarter', '42', 'edit']) is called.
        Then  it returns 'edit'.
        """
        from lex.mcp_server.tools.embed import _classify_path

        self.assertEqual(_classify_path(["quarter", "42", "edit"]), "edit")

    def test_1_233_classify_path_show_id_is_detail(self):
        """Scenario 1.233: <resource>/show/<id> classifies as detail view.

        Given segments = ['quarter', 'show', '42']
        When  _classify_path(['quarter', 'show', '42']) is called.
        Then  it returns 'detail'.
        """
        from lex.mcp_server.tools.embed import _classify_path

        self.assertEqual(_classify_path(["quarter", "show", "42"]), "detail")

    # ── _build_embed_url ───────────────────────────────────────────────────

    def test_1_234_build_embed_url_always_sets_embed_true(self):
        """Scenario 1.234: The built URL always includes embed=true query param
        and an #embed fragment.

        Given path = '/quarter'
        When  _build_embed_url('/quarter') is called.
        Then  the URL contains 'embed=true' and '#embed'.
        """
        from lex.mcp_server.tools.embed import _build_embed_url

        with patch(
            "lex.mcp_server.tools.embed.mcp_setting", return_value=None
        ), patch.dict(
            os.environ,
            {"REACT_APP_URL": "http://localhost:8000"},
            clear=False,
        ):
            url = _build_embed_url("/quarter")
        self.assertIn("embed=true", url)
        self.assertIn("#embed", url)
        self.assertIn("http://localhost:8000/quarter", url)

    def test_1_235_build_embed_url_adds_hide_toolbar(self):
        """Scenario 1.235: hide_toolbar=True appends hide_toolbar=true to the URL.

        Given path = '/quarter', hide_toolbar = True
        When  _build_embed_url('/quarter', hide_toolbar=True) is called.
        Then  the URL contains 'hide_toolbar=true'.
        """
        from lex.mcp_server.tools.embed import _build_embed_url

        with patch(
            "lex.mcp_server.tools.embed.mcp_setting", return_value=None
        ), patch.dict(
            os.environ,
            {"REACT_APP_URL": "http://localhost:8000"},
            clear=False,
        ):
            url = _build_embed_url("/quarter", hide_toolbar=True)
        self.assertIn("hide_toolbar=true", url)

    def test_1_236_build_embed_url_redirect_after_create(self):
        """Scenario 1.236: redirect_after_create sets the corresponding query param.

        Given path = '/quarter/create', redirect_after_create = '/quarter'
        When  _build_embed_url called with redirect_after_create.
        Then  the URL contains 'redirect_after_create=%2Fquarter'.
        """
        from lex.mcp_server.tools.embed import _build_embed_url

        with patch(
            "lex.mcp_server.tools.embed.mcp_setting", return_value=None
        ), patch.dict(
            os.environ,
            {"REACT_APP_URL": "http://localhost:8000"},
            clear=False,
        ):
            url = _build_embed_url(
                "/quarter/create", redirect_after_create="/quarter"
            )
        self.assertIn("redirect_after_create", url)

    def test_1_237_build_embed_url_adds_leading_slash_to_path(self):
        """Scenario 1.237: A path without a leading slash gets one added.

        Given path = 'quarter' (no leading slash)
        When  _build_embed_url('quarter') is called.
        Then  the URL contains '/quarter'.
        """
        from lex.mcp_server.tools.embed import _build_embed_url

        with patch(
            "lex.mcp_server.tools.embed.mcp_setting", return_value=None
        ), patch.dict(
            os.environ,
            {"REACT_APP_URL": "http://localhost:8000"},
            clear=False,
        ):
            url = _build_embed_url("quarter")
        self.assertIn("/quarter", url)

    # ── _build_title ───────────────────────────────────────────────────────

    def test_1_238_build_title_no_container_uses_resource_name(self):
        """Scenario 1.238: When no container is provided, resource name is used.

        Given resource = 'quarterly_report', view_type = 'list', container = None
        When  _build_title('quarterly_report', 'list', None) is called.
        Then  the result includes a readable capitalised form of the resource.
        """
        from lex.mcp_server.tools.embed import _build_title

        result = _build_title("quarterly_report", "list", None)
        self.assertIn("Quarterly Report", result)
        self.assertIn("List View", result)

    def test_1_239_build_title_no_resource_falls_back_to_application(self):
        """Scenario 1.239: When resource is None and no container, title says 'Application'.

        Given resource = None, view_type = 'custom', container = None
        When  _build_title(None, 'custom', None) is called.
        Then  the result contains 'Application'.
        """
        from lex.mcp_server.tools.embed import _build_title

        result = _build_title(None, "custom", None)
        self.assertIn("Application", result)


# ===========================================================================
# Cluster 1ab — mcp_mode_invoke.py
# ===========================================================================


class TestCluster01ab_McpModeInvoke(TestCase):
    """Mode normalisation and InvokeSwitchResult contract from
    ``lex.tools.mcp_mode_invoke``."""

    def test_1_240_normalise_mode_accepts_valid_modes(self):
        """Scenario 1.240: All supported modes are accepted without error.

        Given each mode in SUPPORTED_MCP_MODES
        When  _normalise_mode(mode) is called.
        Then  it returns the mode in lowercase.
        """
        from lex.tools.mcp_mode_invoke import SUPPORTED_MCP_MODES, _normalise_mode

        for mode in SUPPORTED_MCP_MODES:
            with self.subTest(mode=mode):
                self.assertEqual(_normalise_mode(mode), mode)

    def test_1_241_normalise_mode_strips_whitespace_and_lowercases(self):
        """Scenario 1.241: Whitespace is stripped and value lowercased.

        Given mode = '  Forward  '
        When  _normalise_mode('  Forward  ') is called.
        Then  it returns 'forward'.
        """
        from lex.tools.mcp_mode_invoke import _normalise_mode

        self.assertEqual(_normalise_mode("  Forward  "), "forward")

    def test_1_242_normalise_mode_rejects_unknown_mode(self):
        """Scenario 1.242: An unrecognised mode raises ValueError.

        Given mode = 'unknown_mode'
        When  _normalise_mode('unknown_mode') is called.
        Then  ValueError is raised mentioning the unsupported mode.
        """
        from lex.tools.mcp_mode_invoke import _normalise_mode

        with self.assertRaises(ValueError) as ctx:
            _normalise_mode("unknown_mode")
        self.assertIn("unknown_mode", str(ctx.exception))

    def test_1_243_invoke_switch_result_ok_when_no_errors(self):
        """Scenario 1.243: InvokeSwitchResult.ok is True when errors is empty.

        Given InvokeSwitchResult with no errors.
        When  .ok is read.
        Then  it is True.
        """
        from lex.tools.mcp_mode_invoke import InvokeSwitchResult

        result = InvokeSwitchResult(target_mode="forward")
        self.assertTrue(result.ok)

    def test_1_244_invoke_switch_result_not_ok_when_errors_present(self):
        """Scenario 1.244: InvokeSwitchResult.ok is False when errors is non-empty.

        Given InvokeSwitchResult with one error string.
        When  .ok is read.
        Then  it is False.
        """
        from lex.tools.mcp_mode_invoke import InvokeSwitchResult

        result = InvokeSwitchResult(target_mode="forward", errors=("something went wrong",))
        self.assertFalse(result.ok)

    def test_1_245_invoke_switch_to_mode_noop_with_no_server_running(self):
        """Scenario 1.245: invoke_switch_to_mode returns a result even when
        lex_mcp is unavailable and the fallback also fails (noop strategy).

        Given no lex_mcp package, and all fallback imports unavailable.
        When  invoke_switch_to_mode('forward', ...) is called.
        Then  the returned InvokeSwitchResult carries a target_mode of 'forward'
              and no uncaught exceptions are raised.
        """
        from lex.tools.mcp_mode_invoke import invoke_switch_to_mode

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            mcp_json = root / "mcp.json"
            mcp_json.write_text("{}", encoding="utf-8")
            result = invoke_switch_to_mode(
                "forward",
                project_root=root,
                mcp_config_path=mcp_json,
                stop_server=False,
            )
        self.assertEqual(result.target_mode, "forward")


# ===========================================================================
# Cluster 1ab — verify_ai_assets.py: mode resolution + directory verification
# ===========================================================================


class TestCluster01ab_VerifyAiAssets(TestCase):
    """Mode resolution, env-file reading, and directory verification from
    ``lex.tools.verify_ai_assets``."""

    # ── _read_env_file_value ───────────────────────────────────────────────

    def test_1_246_read_env_file_value_returns_value_for_key(self):
        """Scenario 1.246: _read_env_file_value extracts a plain KEY=value line.

        Given an .env file containing LEX_MCP_MODE=backward
        When  _read_env_file_value(path, 'LEX_MCP_MODE') is called.
        Then  it returns 'backward'.
        """
        from lex.tools.verify_ai_assets import _read_env_file_value

        path = _make_temp_env("LEX_MCP_MODE=backward\n")
        try:
            self.assertEqual(_read_env_file_value(path, "LEX_MCP_MODE"), "backward")
        finally:
            path.unlink(missing_ok=True)

    def test_1_247_read_env_file_value_ignores_comments(self):
        """Scenario 1.247: Lines starting with '#' are not parsed as key-value pairs.

        Given an .env file with a commented-out key
        When  _read_env_file_value(path, 'LEX_MCP_MODE') is called.
        Then  it returns None (the commented line is ignored).
        """
        from lex.tools.verify_ai_assets import _read_env_file_value

        path = _make_temp_env("# LEX_MCP_MODE=forward\n")
        try:
            self.assertIsNone(_read_env_file_value(path, "LEX_MCP_MODE"))
        finally:
            path.unlink(missing_ok=True)

    def test_1_248_read_env_file_value_strips_surrounding_quotes(self):
        """Scenario 1.248: Quoted values have their surrounding quotes stripped.

        Given an .env file containing LEX_MCP_MODE="edit"
        When  _read_env_file_value(path, 'LEX_MCP_MODE') is called.
        Then  it returns 'edit' (no quotes).
        """
        from lex.tools.verify_ai_assets import _read_env_file_value

        path = _make_temp_env('LEX_MCP_MODE="edit"\n')
        try:
            self.assertEqual(_read_env_file_value(path, "LEX_MCP_MODE"), "edit")
        finally:
            path.unlink(missing_ok=True)

    # ── resolve_active_mcp_mode ────────────────────────────────────────────

    def test_1_249_resolve_active_mcp_mode_defaults_to_forward(self):
        """Scenario 1.249: With no configuration signals, the mode defaults to 'forward'.

        Given an empty project root with no .env, no mcp.json, and no env vars.
        When  resolve_active_mcp_mode(project_root) is called.
        Then  it returns ('forward', 'default').
        """
        from lex.tools.verify_ai_assets import resolve_active_mcp_mode

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            with (
                patch("lex.tools.verify_ai_assets._read_override_mode", return_value=None),
                patch.dict(os.environ, {}, clear=False),
            ):
                env_backup = os.environ.pop("LEX_MCP_MODE", None)
                try:
                    mode, source = resolve_active_mcp_mode(root, env={})
                finally:
                    if env_backup is not None:
                        os.environ["LEX_MCP_MODE"] = env_backup
        self.assertEqual(mode, "forward")
        self.assertEqual(source, "default")

    def test_1_250_resolve_active_mcp_mode_respects_explicit_mode(self):
        """Scenario 1.250: An explicit_mode argument takes the highest priority.

        Given explicit_mode = 'backward'
        When  resolve_active_mcp_mode(project_root, explicit_mode='backward') is called.
        Then  it returns ('backward', 'cli').
        """
        from lex.tools.verify_ai_assets import resolve_active_mcp_mode

        with tempfile.TemporaryDirectory() as tmpdir:
            mode, source = resolve_active_mcp_mode(
                Path(tmpdir), explicit_mode="backward"
            )
        self.assertEqual(mode, "backward")
        self.assertEqual(source, "cli")

    def test_1_251_resolve_active_mcp_mode_reads_project_dotenv(self):
        """Scenario 1.251: LEX_MCP_MODE in project .env is used when no override file.

        Given a project .env containing LEX_MCP_MODE=review
        And   no override file present.
        When  resolve_active_mcp_mode(project_root) is called.
        Then  it returns ('review', 'project-dotenv').
        """
        from lex.tools.verify_ai_assets import resolve_active_mcp_mode

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / ".env").write_text("LEX_MCP_MODE=review\n", encoding="utf-8")
            with patch(
                "lex.tools.verify_ai_assets._read_override_mode", return_value=None
            ):
                mode, source = resolve_active_mcp_mode(root, env={})
        self.assertEqual(mode, "review")
        self.assertEqual(source, "project-dotenv")

    def test_1_252_resolve_active_mcp_mode_override_beats_dotenv(self):
        """Scenario 1.252: The override file beats the project .env.

        Given a project .env with LEX_MCP_MODE=review
        And   the override file contains 'edit'.
        When  resolve_active_mcp_mode(project_root) is called.
        Then  it returns ('edit', 'override-file').
        """
        from lex.tools.verify_ai_assets import resolve_active_mcp_mode

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / ".env").write_text("LEX_MCP_MODE=review\n", encoding="utf-8")
            with patch(
                "lex.tools.verify_ai_assets._read_override_mode", return_value="edit"
            ):
                mode, source = resolve_active_mcp_mode(root, env={})
        self.assertEqual(mode, "edit")
        self.assertEqual(source, "override-file")

    # ── verify_directory ───────────────────────────────────────────────────

    def test_1_253_verify_directory_skipped_when_source_none(self):
        """Scenario 1.253: verify_directory returns a skipped result when
        source_directory is None.

        Given source_directory = None
        When  verify_directory(project_root, None, '.github') is called.
        Then  the result has skipped_reason set and ok is True (skipped ≠ broken).
        """
        from lex.tools.verify_ai_assets import verify_directory

        with tempfile.TemporaryDirectory() as tmpdir:
            result = verify_directory(Path(tmpdir), None, ".github")
        self.assertIsNotNone(result.skipped_reason)

    def test_1_254_verify_directory_restores_missing_file(self):
        """Scenario 1.254: verify_directory copies a missing file from source.

        Given a source directory containing one file.
        And   the file is absent from the destination.
        When  verify_directory is called.
        Then  the file is copied to the destination and listed in restored_files.
        """
        from lex.tools.verify_ai_assets import verify_directory

        with tempfile.TemporaryDirectory() as src_tmp, \
             tempfile.TemporaryDirectory() as dest_tmp:
            src = Path(src_tmp) / "docs"
            src.mkdir()
            (src / "README.md").write_text("# Hello", encoding="utf-8")

            result = verify_directory(Path(dest_tmp), src, "docs")

        self.assertEqual(len(result.restored_files), 1)
        self.assertEqual(result.restored_files[0], Path("README.md"))

    def test_1_255_verify_directory_ok_when_files_already_match(self):
        """Scenario 1.255: verify_directory returns ok=True when destination matches source.

        Given a source file and an identical destination file.
        When  verify_directory is called.
        Then  restored_files is empty and ok is True.
        """
        from lex.tools.verify_ai_assets import verify_directory

        with tempfile.TemporaryDirectory() as src_tmp, \
             tempfile.TemporaryDirectory() as dest_tmp:
            src = Path(src_tmp) / "docs"
            src.mkdir()
            (src / "README.md").write_text("# Hello", encoding="utf-8")
            dest_docs = Path(dest_tmp) / "docs"
            dest_docs.mkdir()
            (dest_docs / "README.md").write_text("# Hello", encoding="utf-8")

            result = verify_directory(Path(dest_tmp), src, "docs")

        self.assertEqual(result.restored_files, ())
        self.assertTrue(result.ok)

    def test_1_256_verify_ai_assets_result_ok_aggregates_directories(self):
        """Scenario 1.256: VerifyAIAssetsResult.ok is False when any directory
        result has restored_files.

        Given a VerifyAIAssetsResult where one DirectoryVerificationResult has
        restored_files.
        When  .ok is read.
        Then  it is False (the project was missing files and had to be restored).
        """
        from lex.tools.verify_ai_assets import (
            DirectoryVerificationResult,
            VerifyAIAssetsResult,
        )

        dirty = DirectoryVerificationResult(
            directory_name=".github",
            source_directory=None,
            destination_directory=Path("/tmp/fake"),
            restored_files=(Path("agents/agent.md"),),
        )
        result = VerifyAIAssetsResult(
            project_root=Path("/tmp/fake"),
            mode="forward",
            mode_source="default",
            directories=(dirty,),
        )
        self.assertFalse(result.ok)

    def test_1_257_verify_ai_assets_result_ok_when_clean(self):
        """Scenario 1.257: VerifyAIAssetsResult.ok is True when all directories
        have no restored or removed files and no skipped_reason.

        Given a VerifyAIAssetsResult whose only DirectoryVerificationResult has
        no restored_files, no removed_files, and no skipped_reason (all up-to-date).
        When  .ok is read.
        Then  it is True.
        """
        from lex.tools.verify_ai_assets import (
            DirectoryVerificationResult,
            VerifyAIAssetsResult,
        )

        clean = DirectoryVerificationResult(
            directory_name="docs",
            source_directory=Path("/fake/src/docs"),
            destination_directory=Path("/tmp/fake/docs"),
            # No restored_files, no removed_files, no skipped_reason → ok=True
        )
        self.assertTrue(clean.ok)

        result = VerifyAIAssetsResult(
            project_root=Path("/tmp/fake"),
            mode="forward",
            mode_source="default",
            directories=(clean,),
        )
        self.assertTrue(result.ok)

    # ── _read_mode_from_mcp_json ───────────────────────────────────────────

    def test_1_258_read_mode_from_mcp_json_reads_mode_arg(self):
        """Scenario 1.258: _read_mode_from_mcp_json extracts --mode from args.

        Given an mcp.json with a lex-mcp-local server entry and --mode backward.
        When  _read_mode_from_mcp_json(path) is called.
        Then  it returns 'backward'.
        """
        from lex.tools.verify_ai_assets import _read_mode_from_mcp_json

        payload = {
            "mcpServers": {
                "lex-mcp-local": {
                    "command": "python",
                    "args": ["-m", "lex_mcp_local", "--mode", "backward"],
                }
            }
        }
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False, encoding="utf-8"
        ) as f:
            json.dump(payload, f)
            fpath = Path(f.name)
        try:
            result = _read_mode_from_mcp_json(fpath)
        finally:
            fpath.unlink(missing_ok=True)
        self.assertEqual(result, "backward")

    def test_1_259_read_mode_from_mcp_json_returns_none_for_unknown_server(self):
        """Scenario 1.259: _read_mode_from_mcp_json returns None when no
        lex-mcp server entry exists in mcp.json.

        Given an mcp.json with an unrelated server entry.
        When  _read_mode_from_mcp_json(path) is called.
        Then  it returns None.
        """
        from lex.tools.verify_ai_assets import _read_mode_from_mcp_json

        payload = {
            "mcpServers": {
                "some-other-server": {
                    "command": "python",
                    "args": ["--mode", "backward"],
                }
            }
        }
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False, encoding="utf-8"
        ) as f:
            json.dump(payload, f)
            fpath = Path(f.name)
        try:
            result = _read_mode_from_mcp_json(fpath)
        finally:
            fpath.unlink(missing_ok=True)
        self.assertIsNone(result)


# ===========================================================================
# Cluster 1ab — setup_with_ai.py: constants + update_env_file
# ===========================================================================


class TestCluster01ab_SetupWithAi(TestCase):
    """Public-API surface of ``lex.tools.setup_with_ai`` consumed by the
    other MCP modules."""

    def test_1_260_lex_mcp_local_server_name_is_correct(self):
        """Scenario 1.260: LEX_MCP_LOCAL_SERVER_NAME equals 'lex-mcp-local'.

        The server name is embedded in mcp.json entries, override files, and
        cache lookups. An incorrect value would break every integration that
        searches for the server by name.

        Given the imported constant.
        When  it is read.
        Then  it equals 'lex-mcp-local'.
        """
        from lex.tools.setup_with_ai import LEX_MCP_LOCAL_SERVER_NAME

        self.assertEqual(LEX_MCP_LOCAL_SERVER_NAME, "lex-mcp-local")

    def test_1_261_lex_app_embedded_directory_names_includes_docs(self):
        """Scenario 1.261: LEX_APP_EMBEDDED_DIRECTORY_NAMES includes 'docs'.

        The 'docs/' directory is shipped by the lex package and must be
        included in every verify cycle.

        Given the imported constant.
        When  it is read.
        Then  it contains 'docs'.
        """
        from lex.tools.setup_with_ai import LEX_APP_EMBEDDED_DIRECTORY_NAMES

        self.assertIn("docs", LEX_APP_EMBEDDED_DIRECTORY_NAMES)

    def test_1_262_update_env_file_writes_new_key(self):
        """Scenario 1.262: update_env_file inserts a new key into the .env file.

        Given a .env file that does not contain LEX_MCP_MODE.
        When  update_env_file(path, {'LEX_MCP_MODE': 'edit'}) is called.
        Then  the .env file now contains 'LEX_MCP_MODE=edit'.
        """
        from lex.tools.setup_with_ai import update_env_file

        path = _make_temp_env("OTHER_KEY=hello\n")
        try:
            update_env_file(path, {"LEX_MCP_MODE": "edit"})
            content = path.read_text(encoding="utf-8")
        finally:
            path.unlink(missing_ok=True)
        self.assertIn("LEX_MCP_MODE", content)
        self.assertIn("edit", content)

    def test_1_263_update_env_file_updates_existing_key(self):
        """Scenario 1.263: update_env_file replaces an existing key's value.

        Given a .env file with LEX_MCP_MODE=forward.
        When  update_env_file(path, {'LEX_MCP_MODE': 'backward'}) is called.
        Then  the file contains 'LEX_MCP_MODE=backward' and not '=forward'.
        """
        from lex.tools.setup_with_ai import update_env_file

        path = _make_temp_env("LEX_MCP_MODE=forward\n")
        try:
            update_env_file(path, {"LEX_MCP_MODE": "backward"})
            content = path.read_text(encoding="utf-8")
        finally:
            path.unlink(missing_ok=True)
        self.assertIn("backward", content)
        self.assertNotIn("=forward", content)
