"""MCP embed tool URL/title helpers, mode-resolution utilities, and asset verification.

Intent: the MCP server ships an ``lex_embed_view`` tool that builds fully-qualified
embed URLs for the React frontend and a UI widget that renders them inside an iframe
inside any compliant MCP Apps host.  Mode resolution helpers (``resolve_active_mcp_mode``,
``_read_env_file_value``, override-file read) and the ``update_env_file`` writer are the
backbone of ``lex ai-verify`` / the AI-dashboard.  A regression in URL construction
(missing ``embed`` param, wrong fragment, wrong frontend base), in mode resolution
precedence (override file ignored, .env not parsed), or in env-file writing (existing
keys not updated, trailing newline omitted) breaks the agentic workflow immediately.

Cluster 1ab — scenarios 1.223–1.237. Type: U.
Covers:
  lex/mcp_server/tools/embed.py   (_resolve_frontend_url, _csp_origins, _classify_path,
                                    _build_title, _build_embed_url)
  lex/tools/setup_with_ai.py      (normalize_mcp_mode, normalize_ai_environments,
                                    update_env_file)
  lex/tools/verify_ai_assets.py   (_read_env_file_value, _read_override_mode,
                                    _read_mode_from_mcp_json, resolve_active_mcp_mode,
                                    verify_directory, DirectoryVerificationResult)
  lex/tools/mcp_mode_invoke.py    (_normalise_mode, InvokeSwitchResult,
                                    invoke_switch_to_mode fallback path)
Run: python -m lex pytest lex/test_project/tests/init/test_1ab_mcp_embed_and_mode_tools.py -v
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest
from django.test import SimpleTestCase

pytestmark = pytest.mark.init


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


# ---------------------------------------------------------------------------
# Cluster 1ab — MCP embed URL helpers (embed.py)
# ---------------------------------------------------------------------------


class TestCluster01ab_McpEmbedAndModeTools(SimpleTestCase):
    """Cluster 1ab: MCP embed URL helpers, mode tools, env-file I/O, and asset verification."""

    # ── 1.223 ──────────────────────────────────────────────────────────────
    def test_1_223_resolve_frontend_url_uses_setting_first(self) -> None:
        """
        Scenario 1.223: _resolve_frontend_url returns the MCP_SERVER setting when set.

        Given: ``MCP_SERVER["FRONTEND_BASE_URL"]`` is configured to a custom URL
        When: ``_resolve_frontend_url()`` is called
        Then: it returns that URL (stripped of trailing slash), ignoring env vars
        """
        from lex.mcp_server.tools import embed

        with patch.object(embed, "mcp_setting", side_effect=lambda k: "https://my.app.io/" if k == "FRONTEND_BASE_URL" else None):
            result = embed._resolve_frontend_url()

        self.assertEqual(
            result, "https://my.app.io",
            msg="_resolve_frontend_url must return the configured FRONTEND_BASE_URL (trailing slash stripped)",
        )

    # ── 1.224 ──────────────────────────────────────────────────────────────
    def test_1_224_resolve_frontend_url_falls_back_to_env_then_default(self) -> None:
        """
        Scenario 1.224: _resolve_frontend_url falls back through env vars then to localhost.

        Given: no MCP_SERVER setting, REACT_APP_URL is set, LEX_FRONTEND_URL is also set
        When: ``_resolve_frontend_url()`` is called
        Then: REACT_APP_URL wins (higher priority than LEX_FRONTEND_URL)
        And: when REACT_APP_URL is absent, LEX_FRONTEND_URL is used
        And: when both are absent, ``http://localhost:8000`` is returned
        """
        from lex.mcp_server.tools import embed

        no_setting = patch.object(embed, "mcp_setting", return_value=None)

        # Sub-case 1: both present — REACT_APP_URL wins.
        with no_setting, patch.dict(
            "os.environ",
            {"REACT_APP_URL": "https://react.example.com", "LEX_FRONTEND_URL": "https://lex.example.com"},
            clear=True,
        ):
            result = embed._resolve_frontend_url()
        self.assertEqual(
            result, "https://react.example.com",
            msg="REACT_APP_URL must take priority over LEX_FRONTEND_URL",
        )

        # Sub-case 2: only LEX_FRONTEND_URL present — it is the fallback.
        with no_setting, patch.dict(
            "os.environ",
            {"LEX_FRONTEND_URL": "https://lex.example.com"},
            clear=True,
        ):
            result2 = embed._resolve_frontend_url()
        self.assertEqual(
            result2, "https://lex.example.com",
            msg="LEX_FRONTEND_URL is used when REACT_APP_URL is absent",
        )

        # Sub-case 3: neither variable present — falls back to http://localhost:8000.
        with no_setting, patch.dict("os.environ", {}, clear=True):
            result3 = embed._resolve_frontend_url()
        self.assertEqual(
            result3, "http://localhost:8000",
            msg="fallback must be http://localhost:8000 when no env var is set",
        )

    # ── 1.225 ──────────────────────────────────────────────────────────────
    def test_1_225_classify_path_routing_rules(self) -> None:
        """
        Scenario 1.225: _classify_path classifies path segments by URL shape.

        Given: normalised path segment lists
        When: ``_classify_path(segments)`` is called for each
        Then: the correct view-type label is returned for every case
        """
        from lex.mcp_server.tools.embed import _classify_path

        self.assertEqual(_classify_path([]), "custom", msg="empty path → custom")
        self.assertEqual(_classify_path(["quarter"]), "list", msg="/{resource} → list")
        self.assertEqual(_classify_path(["quarter", "create"]), "create", msg="/{resource}/create → create")
        self.assertEqual(_classify_path(["quarter", "42"]), "detail", msg="/{resource}/{id} → detail")
        self.assertEqual(_classify_path(["quarter", "abc12345-0000-0000-0000-000000000000"]), "detail", msg="UUID id → detail")
        self.assertEqual(_classify_path(["quarter", "42", "edit"]), "edit", msg="/{resource}/{id}/edit → edit")
        self.assertEqual(_classify_path(["quarter", "show", "99"]), "detail", msg="/{resource}/show/{id} → detail")
        self.assertEqual(_classify_path(["quarter", "something-else"]), "custom", msg="unknown shape → custom")

    # ── 1.226 ──────────────────────────────────────────────────────────────
    def test_1_226_build_embed_url_core_params(self) -> None:
        """
        Scenario 1.226: _build_embed_url adds embed=true, #embed fragment, and optional toggles.

        Given: a frontend at http://localhost:8000 and a path /quarter
        When: ``_build_embed_url`` is called with hide_toolbar=True and an extra param
        Then: the returned URL contains embed=true, the #embed fragment, hide_toolbar=true,
              and the extra param; the base URL is correctly prepended.
        """
        from lex.mcp_server.tools import embed

        with patch.object(embed, "mcp_setting", return_value=None), \
             patch.dict("os.environ", {}, clear=True):
            url = embed._build_embed_url(
                "/quarter",
                hide_toolbar=True,
                extra_params={"my_key": "my_val"},
            )

        self.assertIn("embed=true", url, msg="URL must include embed=true")
        self.assertIn("#embed", url, msg="URL must include #embed fragment")
        self.assertIn("hide_toolbar=true", url, msg="hide_toolbar toggle must appear in query")
        self.assertIn("my_key=my_val", url, msg="extra_params must be included in URL")
        self.assertTrue(url.startswith("http://localhost:8000"), msg="URL must start with the frontend base")

    # ── 1.227 ──────────────────────────────────────────────────────────────
    def test_1_227_build_title_formats_human_readable_string(self) -> None:
        """
        Scenario 1.227: _build_title generates a human-readable title from resource + view_type.

        Given: a resource name and a view_type label
        When: ``_build_title(resource, view_type, container=None)`` is called
        Then: the result contains the resource name (title-cased) and the view-type label
        """
        from lex.mcp_server.tools.embed import _build_title

        title = _build_title("quarter", "list", None)
        self.assertIn("Quarter", title, msg="resource must appear title-cased in the title")
        self.assertIn("List View", title, msg="view-type label must appear in the title")

        title_create = _build_title("budget_item", "create", None)
        self.assertIn("Create Form", title_create, msg="create label must appear")

    # ── 1.228 ──────────────────────────────────────────────────────────────
    def test_1_228_csp_origins_always_includes_frontend(self) -> None:
        """
        Scenario 1.228: _csp_origins includes the frontend origin and any extra origins.

        Given: FRONTEND_BASE_URL = https://app.example.com and EMBED_EXTRA_CSP_ORIGINS = [https://auth.example.com]
        When: ``_csp_origins()`` is called
        Then: the list contains https://app.example.com (scheme://host, no path) and https://auth.example.com
        """
        from lex.mcp_server.tools import embed

        def _setting(key: str):
            if key == "FRONTEND_BASE_URL":
                return "https://app.example.com/some/path/"
            if key == "EMBED_EXTRA_CSP_ORIGINS":
                return ["https://auth.example.com"]
            return None

        with patch.object(embed, "mcp_setting", side_effect=_setting):
            origins = embed._csp_origins()

        self.assertIn(
            "https://app.example.com", origins,
            msg="CSP origins must include scheme://host of the frontend (path stripped)",
        )
        self.assertIn(
            "https://auth.example.com", origins,
            msg="extra CSP origins from EMBED_EXTRA_CSP_ORIGINS must be included",
        )

    # ── 1.229 ──────────────────────────────────────────────────────────────
    def test_1_229_normalize_mcp_mode_accepts_valid_and_defaults_invalid(self) -> None:
        """
        Scenario 1.229: normalize_mcp_mode returns a known mode or the default for unknowns.

        Given: various raw mode strings
        When: ``normalize_mcp_mode(mode)`` is called
        Then: known modes are returned as-is (lowercased); unknown / empty values fall back to
              the default (forward).
        """
        from lex.tools.setup_with_ai import normalize_mcp_mode

        self.assertEqual(normalize_mcp_mode("forward"), "forward", msg="'forward' is a valid mode")
        self.assertEqual(normalize_mcp_mode("BACKWARD"), "backward", msg="mode matching is case-insensitive")
        self.assertEqual(normalize_mcp_mode("edit"), "edit", msg="'edit' is a valid mode")
        self.assertEqual(normalize_mcp_mode("unknown_mode"), "forward", msg="unknown mode falls back to 'forward'")
        self.assertEqual(normalize_mcp_mode(None), "forward", msg="None falls back to 'forward'")
        self.assertEqual(normalize_mcp_mode(""), "forward", msg="empty string falls back to 'forward'")

    # ── 1.230 ──────────────────────────────────────────────────────────────
    def test_1_230_normalize_ai_environments_expands_all_and_dedupes(self) -> None:
        """
        Scenario 1.230: normalize_ai_environments expands "all" and deduplicates entries.

        Given: the string "all" as environments input (registry unavailable)
        When: ``normalize_ai_environments("all")`` is called
        Then: every supported environment key appears exactly once in the result
        And: passing a comma-separated string parses into individual keys
        And: duplicates are removed while preserving order
        """
        from lex.tools.setup_with_ai import (
            normalize_ai_environments,
            SUPPORTED_AI_ENVIRONMENTS,
        )

        # Ensure the registry is not available so the local code path runs.
        with patch("lex.tools.setup_with_ai._environment_registry", return_value=None):
            result_all = normalize_ai_environments("all")

        for env_key in SUPPORTED_AI_ENVIRONMENTS:
            self.assertIn(env_key, result_all, msg=f"'all' must expand to include {env_key!r}")
        self.assertEqual(
            len(result_all), len(set(result_all)),
            msg="normalize_ai_environments must not produce duplicates",
        )

        # Comma-separated string parsing
        with patch("lex.tools.setup_with_ai._environment_registry", return_value=None):
            result_csv = normalize_ai_environments("pycharm-copilot,vscode-copilot")
        self.assertEqual(
            result_csv, ("pycharm-copilot", "vscode-copilot"),
            msg="comma-separated string must parse into individual env keys",
        )

    # ── 1.231 ──────────────────────────────────────────────────────────────
    def test_1_231_update_env_file_adds_and_updates_keys(self) -> None:
        """
        Scenario 1.231: update_env_file adds new keys and updates existing ones in-place.

        Given: a .env file with one existing key (EXISTING=old_value)
        When: ``update_env_file(path, {"EXISTING": "new_value", "NEW_KEY": "hello"})`` is called
        Then: EXISTING is updated to new_value (in its original line position)
        And: NEW_KEY=hello is appended
        And: the file ends with a newline
        And: comments and blank lines are preserved
        """
        from lex.tools.setup_with_ai import update_env_file

        with tempfile.TemporaryDirectory() as tmpdir:
            env_path = Path(tmpdir) / ".env"
            env_path.write_text("# project config\nEXISTING=old_value\n\n", encoding="utf-8")

            update_env_file(env_path, {"EXISTING": "new_value", "NEW_KEY": "hello"})

            content = env_path.read_text(encoding="utf-8")

        self.assertIn("EXISTING=new_value", content, msg="existing key must be updated in place")
        self.assertIn("NEW_KEY=hello", content, msg="new key must be appended")
        self.assertNotIn("EXISTING=old_value", content, msg="old value must be replaced, not left in file")
        self.assertIn("# project config", content, msg="comments must be preserved")
        self.assertTrue(content.endswith("\n"), msg="env file must end with a newline")

    # ── 1.232 ──────────────────────────────────────────────────────────────
    def test_1_232_read_env_file_value_parses_quoted_and_unquoted(self) -> None:
        """
        Scenario 1.232: _read_env_file_value extracts values including quoted strings.

        Given: a .env file with plain, double-quoted, and single-quoted values plus comments
        When: ``_read_env_file_value(path, key)`` is called for each key
        Then: the raw string value (without quotes) is returned; comments and absent keys return None
        """
        from lex.tools.verify_ai_assets import _read_env_file_value

        env_content = (
            "# this is a comment\n"
            "LEX_MCP_MODE=forward\n"
            'QUOTED_KEY="backward"\n'
            "SINGLE='edit'\n"
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            env_path = Path(tmpdir) / ".env"
            env_path.write_text(env_content, encoding="utf-8")

            self.assertEqual(
                _read_env_file_value(env_path, "LEX_MCP_MODE"), "forward",
                msg="unquoted value must be returned as-is",
            )
            self.assertEqual(
                _read_env_file_value(env_path, "QUOTED_KEY"), "backward",
                msg="double-quoted value must be unquoted",
            )
            self.assertEqual(
                _read_env_file_value(env_path, "SINGLE"), "edit",
                msg="single-quoted value must be unquoted",
            )
            self.assertIsNone(
                _read_env_file_value(env_path, "ABSENT_KEY"),
                msg="absent key must return None",
            )

        # Non-existent file returns None
        self.assertIsNone(
            _read_env_file_value(Path("/nonexistent/.env"), "LEX_MCP_MODE"),
            msg="non-existent file must return None",
        )

    # ── 1.233 ──────────────────────────────────────────────────────────────
    def test_1_233_resolve_active_mcp_mode_precedence(self) -> None:
        """
        Scenario 1.233: resolve_active_mcp_mode respects the documented precedence chain.

        Given: different combinations of explicit_mode, override file, .env, and process env
        When: ``resolve_active_mcp_mode(project_root, ...)`` is called
        Then: explicit_mode > override-file > project .env > process env > default
        And: the returned ``source`` string identifies where the value came from
        """
        from lex.tools.verify_ai_assets import resolve_active_mcp_mode, MODE_OVERRIDE_FILE

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)

            # 1) explicit_mode wins over everything
            mode, source = resolve_active_mcp_mode(root, explicit_mode="backward")
            self.assertEqual(mode, "backward", msg="explicit_mode must win")
            self.assertEqual(source, "cli", msg="source must be 'cli' for explicit mode")

            # 2) .env is read when no explicit and no override
            (root / ".env").write_text("LEX_MCP_MODE=edit\n", encoding="utf-8")
            with patch("lex.tools.verify_ai_assets._read_override_mode", return_value=None):
                mode2, source2 = resolve_active_mcp_mode(root)
            self.assertEqual(mode2, "edit", msg=".env must be the source when no override")
            self.assertEqual(source2, "project-dotenv", msg="source must be 'project-dotenv'")

            # 3) Default (forward) when nothing is configured
            empty_root = Path(tmpdir) / "empty"
            empty_root.mkdir()
            import os as _os
            saved = _os.environ.pop("LEX_MCP_MODE", None)
            try:
                with patch("lex.tools.verify_ai_assets._read_override_mode", return_value=None):
                    mode3, source3 = resolve_active_mcp_mode(empty_root)
            finally:
                if saved is not None:
                    _os.environ["LEX_MCP_MODE"] = saved
            self.assertEqual(mode3, "forward", msg="default mode must be 'forward'")
            self.assertEqual(source3, "default", msg="source must be 'default'")

    # ── 1.234 ──────────────────────────────────────────────────────────────
    def test_1_234_verify_directory_restores_missing_file(self) -> None:
        """
        Scenario 1.234: verify_directory copies missing files from source to destination.

        Given: a source directory containing one file and an empty destination
        When: ``verify_directory(project_root, source_dir, directory_name)`` is called
        Then: the file is copied to the destination directory
        And: the result's restored_files contains the relative path of the copied file
        And: result.ok is False (because a file was restored)
        """
        from lex.tools.verify_ai_assets import verify_directory

        with tempfile.TemporaryDirectory() as tmpdir:
            source = Path(tmpdir) / "source" / "docs"
            source.mkdir(parents=True)
            (source / "readme.md").write_text("hello", encoding="utf-8")

            project_root = Path(tmpdir) / "project"
            project_root.mkdir()

            result = verify_directory(project_root, source, "docs")

        self.assertEqual(
            len(result.restored_files), 1,
            msg="one missing file must be recorded as restored",
        )
        self.assertEqual(
            result.restored_files[0], Path("readme.md"),
            msg="the relative path of the restored file must be 'readme.md'",
        )
        self.assertFalse(
            result.ok,
            msg="result.ok must be False when files were restored",
        )

    # ── 1.235 ──────────────────────────────────────────────────────────────
    def test_1_235_normalise_mode_rejects_unsupported_modes(self) -> None:
        """
        Scenario 1.235: _normalise_mode raises ValueError for modes not in SUPPORTED_MCP_MODES.

        Given: an unsupported mode string (e.g. "turbo")
        When: ``_normalise_mode("turbo")`` is called
        Then: ValueError is raised mentioning the unsupported mode
        And: valid modes pass through (lower-cased)
        """
        from lex.tools.mcp_mode_invoke import _normalise_mode, SUPPORTED_MCP_MODES

        with self.assertRaises(ValueError, msg="_normalise_mode must reject unsupported modes"):
            _normalise_mode("turbo")

        for mode in SUPPORTED_MCP_MODES:
            self.assertEqual(
                _normalise_mode(mode.upper()), mode,
                msg=f"_normalise_mode must accept {mode!r} case-insensitively",
            )

    # ── 1.236 ──────────────────────────────────────────────────────────────
    def test_1_236_invoke_switch_result_ok_reflects_errors(self) -> None:
        """
        Scenario 1.236: InvokeSwitchResult.ok is False when errors are present.

        Given: an InvokeSwitchResult with no errors
        When: .ok is checked
        Then: it is True
        And: when errors is non-empty, .ok is False
        """
        from lex.tools.mcp_mode_invoke import InvokeSwitchResult

        result_ok = InvokeSwitchResult(target_mode="forward")
        self.assertTrue(result_ok.ok, msg="InvokeSwitchResult.ok must be True with no errors")

        result_err = InvokeSwitchResult(target_mode="forward", errors=("something went wrong",))
        self.assertFalse(result_err.ok, msg="InvokeSwitchResult.ok must be False when errors are set")

    # ── 1.237 ──────────────────────────────────────────────────────────────
    def test_1_237_invoke_switch_to_mode_fallback_path_when_lex_mcp_absent(self) -> None:
        """
        Scenario 1.237: invoke_switch_to_mode records a fallback-strategy error when lex_mcp is absent
        and the ai_dashboard helpers are also unavailable, rather than raising.

        Given: lex_mcp.mode_switch is not installed (ImportError)
        And: lex.tools.ai_dashboard is also not importable (ImportError)
        When: ``invoke_switch_to_mode("forward", ...)`` is called
        Then: a result is returned (no exception is raised)
        And: result.strategy is not "lex_mcp" (the canonical path was skipped)
        And: result.errors is non-empty (both paths failed gracefully)
        """
        from lex.tools.mcp_mode_invoke import invoke_switch_to_mode

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            mcp_json = root / "mcp.json"
            mcp_json.write_text("{}", encoding="utf-8")

            import sys
            with patch.dict(
                sys.modules,
                {"lex_mcp.mode_switch": None, "lex.tools.ai_dashboard": None},  # type: ignore[dict-item]
            ):
                result = invoke_switch_to_mode(
                    "forward",
                    project_root=root,
                    mcp_config_path=mcp_json,
                    stop_server=False,
                )

        self.assertNotEqual(
            result.strategy, "lex_mcp",
            msg="strategy must not be 'lex_mcp' when that package is absent",
        )
        self.assertTrue(
            result.errors,
            msg="errors must be non-empty when both sync paths failed",
        )
