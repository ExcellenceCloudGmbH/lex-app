"""Cluster 16b — MCP mode resolution and asset verification.

Intent (from lex/tools/verify_ai_assets.py):

    ``resolve_active_mcp_mode`` implements a six-level priority chain so the
    correct LEX MCP mode is always known, even during the brief auto-restart
    window after a mode switch.  The precedence is:

    1. explicit CLI ``--mode`` argument
    2. one-shot override file ``~/.lex-mcp/mode-override``
    3. ``LEX_MCP_MODE`` in the project ``.env``
    4. ``mcp.json`` / IDE config files
    5. process environment ``LEX_MCP_MODE``
    6. default ``forward``

    ``verify_directory`` restores missing or drifted files from a source
    directory into the project root, and prunes stale files in managed
    subdirectories during a mode switch.

    ``_read_env_file_value`` parses KEY=value lines (supporting quoted values
    and skipping comments).
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from lex.tools.verify_ai_assets import (
    ALL_MCP_MODES,
    DEFAULT_MCP_MODE,
    _read_env_file_value,
    _read_override_mode,
    resolve_active_mcp_mode,
    verify_directory,
)
from lex.tools.setup_with_ai import SetupWithAIError

pytestmark = pytest.mark.mcp_tools


# ---------------------------------------------------------------------------
# 16b — _read_env_file_value
# ---------------------------------------------------------------------------


class TestCluster16b_ReadEnvFileValue:
    """Env-file key/value reader (``_read_env_file_value``)."""

    def test_16_28_key_found_plain_value(self, tmp_path):
        """Scenario 16.28: plain KEY=value line is parsed correctly."""
        env_file = tmp_path / ".env"
        env_file.write_text("FOO=bar\nBAZ=qux\n", encoding="utf-8")
        assert _read_env_file_value(env_file, "FOO") == "bar"

    def test_16_29_key_not_found_returns_none(self, tmp_path):
        """Scenario 16.29: missing key returns None."""
        env_file = tmp_path / ".env"
        env_file.write_text("FOO=bar\n", encoding="utf-8")
        assert _read_env_file_value(env_file, "MISSING") is None

    def test_16_30_quoted_double_value_stripped(self, tmp_path):
        """Scenario 16.30: double-quoted value has quotes stripped."""
        env_file = tmp_path / ".env"
        env_file.write_text('MODE="backward"\n', encoding="utf-8")
        assert _read_env_file_value(env_file, "MODE") == "backward"

    def test_16_31_quoted_single_value_stripped(self, tmp_path):
        """Scenario 16.31: single-quoted value has quotes stripped."""
        env_file = tmp_path / ".env"
        env_file.write_text("MODE='edit'\n", encoding="utf-8")
        assert _read_env_file_value(env_file, "MODE") == "edit"

    def test_16_32_comment_lines_skipped(self, tmp_path):
        """Scenario 16.32: lines starting with # are ignored."""
        env_file = tmp_path / ".env"
        env_file.write_text("# FOO=should_not_be_read\nFOO=actual\n", encoding="utf-8")
        assert _read_env_file_value(env_file, "FOO") == "actual"

    def test_16_33_missing_file_returns_none(self, tmp_path):
        """Scenario 16.33: non-existent file returns None."""
        assert _read_env_file_value(tmp_path / "nonexistent.env", "FOO") is None


# ---------------------------------------------------------------------------
# 16b — resolve_active_mcp_mode
# ---------------------------------------------------------------------------


class TestCluster16b_ResolveActiveMcpMode:
    """Mode resolution priority chain (``resolve_active_mcp_mode``)."""

    def test_16_34_explicit_mode_wins(self, tmp_path):
        """Scenario 16.34: explicit_mode= overrides everything else."""
        mode, source = resolve_active_mcp_mode(tmp_path, explicit_mode="backward")
        assert mode == "backward"
        assert source == "cli"

    def test_16_35_default_mode_when_nothing_set(self, tmp_path):
        """Scenario 16.35: no override, no .env, no env var → default 'forward'."""
        env = {k: v for k, v in os.environ.items() if k != "LEX_MCP_MODE"}
        with patch("lex.tools.verify_ai_assets._read_override_mode", return_value=None), \
             patch.dict(os.environ, env, clear=True):
            mode, source = resolve_active_mcp_mode(tmp_path)
        assert mode == DEFAULT_MCP_MODE
        assert source == "default"

    def test_16_36_project_dotenv_used(self, tmp_path):
        """Scenario 16.36: LEX_MCP_MODE in project .env is resolved."""
        (tmp_path / ".env").write_text("LEX_MCP_MODE=edit\n", encoding="utf-8")
        env = {k: v for k, v in os.environ.items() if k != "LEX_MCP_MODE"}
        with patch("lex.tools.verify_ai_assets._read_override_mode", return_value=None), \
             patch.dict(os.environ, env, clear=True):
            mode, source = resolve_active_mcp_mode(tmp_path)
        assert mode == "edit"
        assert source == "project-dotenv"

    def test_16_37_override_file_beats_dotenv(self, tmp_path):
        """Scenario 16.37: override file has priority over project .env."""
        (tmp_path / ".env").write_text("LEX_MCP_MODE=forward\n", encoding="utf-8")
        with patch("lex.tools.verify_ai_assets._read_override_mode", return_value="review"):
            mode, source = resolve_active_mcp_mode(tmp_path)
        assert mode == "review"
        assert source == "override-file"

    def test_16_38_process_env_used_as_last_fallback(self, tmp_path):
        """Scenario 16.38: process env LEX_MCP_MODE is the last real fallback."""
        env = {k: v for k, v in os.environ.items()}
        env["LEX_MCP_MODE"] = "mvp_generator"
        with patch("lex.tools.verify_ai_assets._read_override_mode", return_value=None), \
             patch.dict(os.environ, env, clear=True):
            mode, source = resolve_active_mcp_mode(tmp_path)
        assert mode == "mvp_generator"
        assert source == "process-env"

    def test_16_39_unknown_explicit_mode_raises(self, tmp_path):
        """Scenario 16.39: unrecognised mode raises SetupWithAIError."""
        with pytest.raises(SetupWithAIError):
            resolve_active_mcp_mode(tmp_path, explicit_mode="not_a_real_mode")

    def test_16_40_all_valid_modes_accepted(self, tmp_path):
        """Scenario 16.40: all items in ALL_MCP_MODES are valid."""
        for mode_name in ALL_MCP_MODES:
            m, s = resolve_active_mcp_mode(tmp_path, explicit_mode=mode_name)
            assert m == mode_name, f"{mode_name} was not accepted"


# ---------------------------------------------------------------------------
# 16b — verify_directory
# ---------------------------------------------------------------------------


class TestCluster16b_VerifyDirectory:
    """File verification and restoration (``verify_directory``)."""

    def test_16_41_none_source_returns_skipped(self, tmp_path):
        """Scenario 16.41: source_directory=None results in a skipped result."""
        result = verify_directory(tmp_path, None, "docs")
        assert result.skipped_reason is not None

    def test_16_42_missing_file_is_restored(self, tmp_path):
        """Scenario 16.42: a file present in source but absent in dest is restored."""
        source_dir = tmp_path / "source_docs"
        source_dir.mkdir()
        (source_dir / "README.md").write_text("# docs", encoding="utf-8")

        project_root = tmp_path / "project"
        project_root.mkdir()

        result = verify_directory(project_root, source_dir, "source_docs")
        assert len(result.restored_files) == 1
        assert (project_root / "source_docs" / "README.md").is_file()

    def test_16_43_matching_file_leaves_ok_result(self, tmp_path):
        """Scenario 16.43: identical file already in dest → ok=True, nothing restored."""
        source_dir = tmp_path / "src"
        source_dir.mkdir()
        (source_dir / "file.txt").write_text("content", encoding="utf-8")

        project_root = tmp_path / "proj"
        project_root.mkdir()
        dest_dir = project_root / "src"
        dest_dir.mkdir()
        (dest_dir / "file.txt").write_text("content", encoding="utf-8")

        result = verify_directory(project_root, source_dir, "src")
        assert result.ok
        assert len(result.restored_files) == 0

    def test_16_44_drifted_file_is_restored(self, tmp_path):
        """Scenario 16.44: file with different content in dest is overwritten."""
        source_dir = tmp_path / "src"
        source_dir.mkdir()
        (source_dir / "file.txt").write_text("canonical", encoding="utf-8")

        project_root = tmp_path / "proj"
        project_root.mkdir()
        dest_dir = project_root / "src"
        dest_dir.mkdir()
        (dest_dir / "file.txt").write_text("stale content", encoding="utf-8")

        result = verify_directory(project_root, source_dir, "src")
        assert len(result.restored_files) == 1
        assert (dest_dir / "file.txt").read_text(encoding="utf-8") == "canonical"

    def test_16_45_skipped_reason_propagated(self, tmp_path):
        """Scenario 16.45: explicit skipped_reason is surfaced in result."""
        result = verify_directory(tmp_path, tmp_path / "src", "src", skipped_reason="not installed")
        assert result.skipped_reason == "not installed"
        assert not result.ok
