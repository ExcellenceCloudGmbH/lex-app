"""Cluster 1z — verify_ai_assets mode resolution + AI-tools compatibility shims.

Intent
------

``lex/tools/verify_ai_assets.py`` is the pre-flight asset verification tool
that ensures the active MCP mode's ``.github`` directory matches the canonical
version shipped with the mode's package.  Its key public surface is
``resolve_active_mcp_mode``, which determines the active mode from a strict
six-level precedence chain (CLI arg > override file > project .env > mcp.json
> process env > default ``forward``).  A drift in that chain produces
silent mode mismatches — the wrong tool surface boots, the wrong AI workflow
runs, and incorrect asset directories get applied to the project.

``lex/tools/ai_dashboard.py`` and ``lex/tools/ai_faq.py`` are compatibility
shims: thin re-export facades that delegate to the ``lex_mcp`` package
(shipped by ``lex-mcp-local``).  The contract these shims expose is:

* When ``lex_mcp`` is **not** installed, importing the shim must raise
  ``ImportError`` with an actionable message pointing the user to
  ``lex setup-with-ai`` — not a bare ``ModuleNotFoundError``.
* When ``lex_mcp`` IS installed, attribute look-ups and ``dir()`` must
  delegate transparently to the underlying implementation module via
  ``__getattr__`` and ``__dir__``.

A regression in either surface would either silently run the wrong AI mode
or give developers an unhelpful "no module" error that doesn't mention the
remediation step.

Cluster 1z — scenarios 1.211–1.229. Type: U.
Covers:
  lex/tools/verify_ai_assets.py
    (_read_env_file_value, resolve_active_mcp_mode, _read_mode_from_mcp_json,
     _read_override_mode, verify_directory, DirectoryVerificationResult)
  lex/tools/ai_dashboard.py  (shim contract)
  lex/tools/ai_faq.py        (shim contract)
Run: python -m lex pytest lex/test_project/tests/init/test_1z_verify_ai_assets_and_shims.py -v
"""

from __future__ import annotations

import importlib
import json
import sys
import types
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase, mock

import pytest

from lex.tools.verify_ai_assets import (
    ALL_MCP_MODES,
    DEFAULT_MCP_MODE,
    DirectoryVerificationResult,
    MCP_MODE_PACKAGE,
    VerifyAIAssetsResult,
    SetupWithAIError,
    _read_env_file_value,
    _read_mode_from_mcp_json,
    _read_override_mode,
    resolve_active_mcp_mode,
    verify_directory,
)

pytestmark = pytest.mark.init


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def _write_env(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")


# ===========================================================================
# Section A — _read_env_file_value
# ===========================================================================


class TestCluster01z_ReadEnvFileValue(TestCase):
    """Cluster 1z: .env file reader used by mode resolution."""

    def test_1_211_reads_plain_value(self) -> None:
        """
        Scenario 1.211: a bare KEY=VALUE pair is read correctly.

        Given: a .env file containing LEX_MCP_MODE=forward
        When: _read_env_file_value is called for LEX_MCP_MODE
        Then: returns "forward"
        """
        with TemporaryDirectory() as tmp:
            env_file = Path(tmp) / ".env"
            _write_env(env_file, "LEX_MCP_MODE=forward\n")
            result = _read_env_file_value(env_file, "LEX_MCP_MODE")
            self.assertEqual(result, "forward", "plain VALUE must be returned verbatim")

    def test_1_212_reads_double_quoted_value(self) -> None:
        """
        Scenario 1.212: double-quoted values have the surrounding quotes stripped.

        Given: LEX_MCP_MODE="backward"
        When: _read_env_file_value is called
        Then: returns "backward" (no surrounding quotes)
        """
        with TemporaryDirectory() as tmp:
            env_file = Path(tmp) / ".env"
            _write_env(env_file, 'LEX_MCP_MODE="backward"\n')
            result = _read_env_file_value(env_file, "LEX_MCP_MODE")
            self.assertEqual(result, "backward", "surrounding double quotes must be stripped")

    def test_1_213_ignores_comment_lines(self) -> None:
        """
        Scenario 1.213: comment lines (# prefix) are skipped.

        Given: a .env file with a comment line followed by the real KEY
        When: _read_env_file_value is called
        Then: returns the real value, not the comment text
        """
        with TemporaryDirectory() as tmp:
            env_file = Path(tmp) / ".env"
            _write_env(env_file, "# LEX_MCP_MODE=comment\nLEX_MCP_MODE=edit\n")
            result = _read_env_file_value(env_file, "LEX_MCP_MODE")
            self.assertEqual(result, "edit", "commented-out keys must be skipped")

    def test_1_214_returns_none_for_missing_key(self) -> None:
        """
        Scenario 1.214: a key absent from the .env returns None.

        Given: a .env without the requested key
        When: _read_env_file_value is called for that key
        Then: returns None
        """
        with TemporaryDirectory() as tmp:
            env_file = Path(tmp) / ".env"
            _write_env(env_file, "OTHER_KEY=value\n")
            result = _read_env_file_value(env_file, "LEX_MCP_MODE")
            self.assertIsNone(result, "missing key must return None")

    def test_1_215_returns_none_for_missing_file(self) -> None:
        """
        Scenario 1.215: a non-existent .env returns None (not an exception).

        Given: a path to a .env that does not exist
        When: _read_env_file_value is called
        Then: returns None gracefully
        """
        result = _read_env_file_value(Path("/nonexistent/.env"), "LEX_MCP_MODE")
        self.assertIsNone(result, "missing .env file must return None, not raise")


# ===========================================================================
# Section B — resolve_active_mcp_mode
# ===========================================================================


class TestCluster01z_ResolveActiveMcpMode(TestCase):
    """Cluster 1z: resolve_active_mcp_mode precedence chain."""

    def test_1_216_explicit_mode_takes_highest_priority(self) -> None:
        """
        Scenario 1.216: explicit_mode overrides everything else.

        Given: process env sets LEX_MCP_MODE=backward
        When: explicit_mode="forward" is passed
        Then: the resolved mode is "forward" from "cli"
        """
        with TemporaryDirectory() as tmp:
            mode, source = resolve_active_mcp_mode(
                Path(tmp),
                explicit_mode="forward",
                env={"LEX_MCP_MODE": "backward"},
            )
            self.assertEqual(mode, "forward", "explicit_mode must override env")
            self.assertEqual(source, "cli", "source must be 'cli' for explicit mode")

    def test_1_217_dotenv_overrides_process_env(self) -> None:
        """
        Scenario 1.217: LEX_MCP_MODE in .env beats the process environment.

        Given: .env has LEX_MCP_MODE=edit and process env has LEX_MCP_MODE=review
        When: resolve_active_mcp_mode is called without explicit_mode
        Then: resolved mode is "edit" from "project-dotenv"
        """
        with TemporaryDirectory() as tmp:
            env_file = Path(tmp) / ".env"
            _write_env(env_file, "LEX_MCP_MODE=edit\n")
            mode, source = resolve_active_mcp_mode(
                Path(tmp),
                env={"LEX_MCP_MODE": "review"},
            )
            self.assertEqual(mode, "edit", ".env must override process env")
            self.assertEqual(source, "project-dotenv", "source must be 'project-dotenv'")

    def test_1_218_process_env_used_when_no_dotenv(self) -> None:
        """
        Scenario 1.218: process env (or passed env dict) used when .env absent.

        Given: no .env exists; process env has LEX_MCP_MODE=review
        When: resolve_active_mcp_mode is called
        Then: resolved mode is "review" from "process-env"
        """
        with TemporaryDirectory() as tmp:
            mode, source = resolve_active_mcp_mode(
                Path(tmp),
                env={"LEX_MCP_MODE": "review"},
            )
            self.assertEqual(mode, "review", "process env mode must be used when no .env")
            self.assertEqual(source, "process-env", "source must be 'process-env'")

    def test_1_219_default_is_forward_when_nothing_set(self) -> None:
        """
        Scenario 1.219: the last-resort default is "forward".

        Given: no .env, no env override, no override file, no mcp.json
        When: resolve_active_mcp_mode is called in an empty temp directory
        Then: resolved mode is "forward" from "default"
        """
        with TemporaryDirectory() as tmp:
            mode, source = resolve_active_mcp_mode(
                Path(tmp),
                env={},
            )
            self.assertEqual(mode, DEFAULT_MCP_MODE, "default mode must be 'forward'")
            self.assertEqual(source, "default", "source must be 'default'")

    def test_1_220_invalid_explicit_mode_raises(self) -> None:
        """
        Scenario 1.220: an unrecognised explicit mode raises SetupWithAIError.

        Given: explicit_mode is not in ALL_MCP_MODES
        When: resolve_active_mcp_mode is called
        Then: SetupWithAIError is raised with the invalid mode name
        """
        with TemporaryDirectory() as tmp:
            with self.assertRaises(SetupWithAIError) as ctx:
                resolve_active_mcp_mode(
                    Path(tmp),
                    explicit_mode="nonexistent_mode",
                )
            self.assertIn(
                "nonexistent_mode",
                str(ctx.exception),
                "error message must identify the bad mode",
            )


# ===========================================================================
# Section C — _read_mode_from_mcp_json
# ===========================================================================


class TestCluster01z_ReadModeFromMcpJson(TestCase):
    """Cluster 1z: mcp.json mode extraction for servers/mcpServers blocks."""

    def test_1_221_reads_mode_from_args(self) -> None:
        """
        Scenario 1.221: --mode arg in the lex-mcp-local server entry is read.

        Given: mcp.json with a lex-mcp-local server entry containing --mode backward
        When: _read_mode_from_mcp_json is called
        Then: returns "backward"
        """
        with TemporaryDirectory() as tmp:
            mcp_path = Path(tmp) / "mcp.json"
            config = {
                "mcpServers": {
                    "lex-mcp-local": {
                        "command": "python",
                        "args": ["-m", "lex_mcp.server", "--mode", "backward"],
                    }
                }
            }
            mcp_path.write_text(json.dumps(config), encoding="utf-8")
            result = _read_mode_from_mcp_json(mcp_path)
            self.assertEqual(result, "backward", "--mode arg in mcp.json must be read")

    def test_1_222_reads_mode_from_env_block(self) -> None:
        """
        Scenario 1.222: LEX_MCP_MODE in the server's env block is a fallback.

        Given: mcp.json with an env block containing LEX_MCP_MODE=edit
        When: _read_mode_from_mcp_json is called
        Then: returns "edit"
        """
        with TemporaryDirectory() as tmp:
            mcp_path = Path(tmp) / "mcp.json"
            config = {
                "servers": {
                    "lex-mcp-local": {
                        "command": "python",
                        "args": [],
                        "env": {"LEX_MCP_MODE": "edit"},
                    }
                }
            }
            mcp_path.write_text(json.dumps(config), encoding="utf-8")
            result = _read_mode_from_mcp_json(mcp_path)
            self.assertEqual(result, "edit", "LEX_MCP_MODE in env block must be read")

    def test_1_223_returns_none_for_missing_file(self) -> None:
        """
        Scenario 1.223: absent mcp.json returns None without raising.

        Given: a path to a non-existent mcp.json
        When: _read_mode_from_mcp_json is called
        Then: returns None
        """
        result = _read_mode_from_mcp_json(Path("/nonexistent/mcp.json"))
        self.assertIsNone(result, "missing mcp.json must return None")


# ===========================================================================
# Section D — DirectoryVerificationResult.ok + verify_directory
# ===========================================================================


class TestCluster01z_VerifyDirectory(TestCase):
    """Cluster 1z: verify_directory skips when source unavailable."""

    def test_1_224_skips_when_source_directory_is_none(self) -> None:
        """
        Scenario 1.224: verify_directory returns a skipped result when the
        source package is not installed (source_directory=None).

        Given: source_directory is None (package not installed)
        When: verify_directory is called
        Then: result has skipped_reason set and ok is False
        """
        with TemporaryDirectory() as tmp:
            result = verify_directory(
                Path(tmp),
                source_directory=None,
                directory_name=".github",
            )
            self.assertIsNotNone(
                result.skipped_reason, "missing source must produce a skipped_reason"
            )
            self.assertFalse(
                result.ok,
                "a skipped result must not report ok=True (source is unavailable)",
            )

    def test_1_225_all_files_match_yields_ok_result(self) -> None:
        """
        Scenario 1.225: verify_directory is ok when every file matches.

        Given: a source directory whose files are already present and identical
               in the destination
        When: verify_directory is called
        Then: result.ok is True, restored_files is empty
        """
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            src = tmp_path / "pkg_source" / "docs"
            dst = tmp_path / "project" / "docs"
            src.mkdir(parents=True)
            dst.mkdir(parents=True)

            content = b"# LEX docs\n"
            (src / "readme.md").write_bytes(content)
            (dst / "readme.md").write_bytes(content)

            result = verify_directory(
                tmp_path / "project",
                source_directory=src,
                directory_name="docs",
            )
            self.assertTrue(
                result.ok,
                "matching source and destination must yield ok=True",
            )
            self.assertEqual(
                len(result.restored_files),
                0,
                "no files should be restored when everything matches",
            )


# ===========================================================================
# Section E — AI-tools compatibility shim contracts
# ===========================================================================


class TestCluster01z_AiShimContracts(TestCase):
    """Cluster 1z: ai_dashboard and ai_faq shims raise helpfully when
    lex_mcp is not installed, and delegate cleanly when it is."""

    def _import_shim_with_mocked_lex_mcp(self, shim_module_path: str, sub_module: str):
        """Import a shim module with a fake lex_mcp in sys.modules.

        Returns the freshly-imported shim module.
        """
        fake_lex_mcp = types.ModuleType("lex_mcp")
        fake_sub = types.ModuleType(f"lex_mcp.{sub_module}")
        fake_sub.SENTINEL_ATTR = "sentinel"

        # Remove any previously cached version of the shim so we get a clean import.
        for key in list(sys.modules.keys()):
            if key == shim_module_path or key.startswith(shim_module_path + "."):
                del sys.modules[key]

        with mock.patch.dict(
            "sys.modules",
            {
                "lex_mcp": fake_lex_mcp,
                f"lex_mcp.{sub_module}": fake_sub,
            },
        ):
            shim = importlib.import_module(shim_module_path)
            return shim, fake_sub

    def test_1_226_ai_dashboard_shim_raises_when_lex_mcp_absent(self) -> None:
        """
        Scenario 1.226: importing lex.tools.ai_dashboard raises ImportError with
        an actionable message when lex_mcp is not installed.

        Given: lex_mcp is absent from sys.modules
        When: lex.tools.ai_dashboard is imported
        Then: ImportError is raised; the error message mentions `lex setup-with-ai`
        """
        # Remove cached shim so the import runs fresh.
        for key in list(sys.modules.keys()):
            if "lex.tools.ai_dashboard" in key:
                del sys.modules[key]

        with mock.patch.dict("sys.modules", {"lex_mcp": None, "lex_mcp.ai_dashboard": None}):
            with self.assertRaises(ImportError) as ctx:
                importlib.import_module("lex.tools.ai_dashboard")

        self.assertIn(
            "lex setup-with-ai",
            str(ctx.exception),
            "ImportError message must mention `lex setup-with-ai` for actionable guidance",
        )

    def test_1_227_ai_faq_shim_raises_when_lex_mcp_absent(self) -> None:
        """
        Scenario 1.227: importing lex.tools.ai_faq raises ImportError with an
        actionable message when lex_mcp is not installed.

        Given: lex_mcp is absent from sys.modules
        When: lex.tools.ai_faq is imported
        Then: ImportError is raised; the error message mentions `lex setup-with-ai`
        """
        for key in list(sys.modules.keys()):
            if "lex.tools.ai_faq" in key:
                del sys.modules[key]

        with mock.patch.dict("sys.modules", {"lex_mcp": None, "lex_mcp.ai_faq": None}):
            with self.assertRaises(ImportError) as ctx:
                importlib.import_module("lex.tools.ai_faq")

        self.assertIn(
            "lex setup-with-ai",
            str(ctx.exception),
            "ImportError message must mention `lex setup-with-ai` for actionable guidance",
        )

    def test_1_228_ai_dashboard_shim_getattr_delegates_when_installed(self) -> None:
        """
        Scenario 1.228: __getattr__ on the ai_dashboard shim delegates to the
        underlying lex_mcp.ai_dashboard module.

        Given: lex_mcp is installed (mocked)
        When: an attribute is accessed on lex.tools.ai_dashboard
        Then: the attribute value comes from the underlying implementation module
        """
        for key in list(sys.modules.keys()):
            if "lex.tools.ai_dashboard" in key:
                del sys.modules[key]

        shim, fake_sub = self._import_shim_with_mocked_lex_mcp(
            "lex.tools.ai_dashboard", "ai_dashboard"
        )

        # The shim's __getattr__ must proxy unknown names to the impl.
        result = shim.__getattr__("SENTINEL_ATTR")
        self.assertEqual(
            result,
            "sentinel",
            "__getattr__ on the dashboard shim must delegate to the impl module",
        )

    def test_1_229_ai_faq_shim_dir_delegates_when_installed(self) -> None:
        """
        Scenario 1.229: __dir__ on the ai_faq shim delegates to the underlying
        lex_mcp.ai_faq module so that IDE completions reflect the real API.

        Given: lex_mcp is installed (mocked)
        When: dir() is called on the lex.tools.ai_faq module
        Then: the result matches dir(lex_mcp.ai_faq) (i.e. the shim delegates)
        """
        for key in list(sys.modules.keys()):
            if "lex.tools.ai_faq" in key:
                del sys.modules[key]

        shim, fake_sub = self._import_shim_with_mocked_lex_mcp(
            "lex.tools.ai_faq", "ai_faq"
        )

        # The shim's __dir__ must proxy to the impl module's dir().
        shim_dir = shim.__dir__()
        expected_dir = dir(fake_sub)
        self.assertEqual(
            shim_dir,
            expected_dir,
            "__dir__ on the faq shim must delegate to the impl module",
        )
