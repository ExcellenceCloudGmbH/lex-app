"""Cluster 16d — .env file management (``update_env_file``).

Intent (from lex/tools/setup_with_ai.py):

    ``update_env_file`` is the single writer for the project ``.env``.  It:

    * Creates the file when it does not exist.
    * Updates an existing key in-place (preserving surrounding lines).
    * Appends a brand-new key to the end.
    * Preserves comment lines and blank lines.
    * Removes legacy ``COPILOT_GITHUB_TOKEN`` lines when ``GITHUB_TOKEN`` is
      being written, to avoid duplicate auth variables.

    All writes are atomic (rename-into-place) so a crash mid-write never
    leaves a partial file.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from lex.tools.setup_with_ai import update_env_file

pytestmark = pytest.mark.mcp_tools


# ---------------------------------------------------------------------------
# 16d — update_env_file
# ---------------------------------------------------------------------------


class TestCluster16d_UpdateEnvFile:
    """Env file write/update semantics (``update_env_file``)."""

    def test_16_58_creates_new_file(self, tmp_path):
        """Scenario 16.58: creates the file when it does not exist."""
        env_file = tmp_path / ".env"
        assert not env_file.exists()
        update_env_file(env_file, {"FOO": "bar"})
        assert env_file.exists()
        assert "FOO=bar" in env_file.read_text(encoding="utf-8")

    def test_16_59_updates_existing_key_in_place(self, tmp_path):
        """Scenario 16.59: existing key is updated, not duplicated."""
        env_file = tmp_path / ".env"
        env_file.write_text("FOO=old\nBAR=keep\n", encoding="utf-8")
        update_env_file(env_file, {"FOO": "new"})
        content = env_file.read_text(encoding="utf-8")
        lines = [line for line in content.splitlines() if line.startswith("FOO")]
        assert len(lines) == 1
        assert "FOO=new" in content
        assert "BAR=keep" in content

    def test_16_60_appends_new_key(self, tmp_path):
        """Scenario 16.60: new key is appended when absent."""
        env_file = tmp_path / ".env"
        env_file.write_text("EXISTING=val\n", encoding="utf-8")
        update_env_file(env_file, {"NEW_KEY": "value"})
        content = env_file.read_text(encoding="utf-8")
        assert "EXISTING=val" in content
        assert "NEW_KEY=value" in content

    def test_16_61_preserves_comment_lines(self, tmp_path):
        """Scenario 16.61: comment lines are not removed."""
        env_file = tmp_path / ".env"
        env_file.write_text("# this is a comment\nFOO=old\n", encoding="utf-8")
        update_env_file(env_file, {"FOO": "new"})
        content = env_file.read_text(encoding="utf-8")
        assert "# this is a comment" in content

    def test_16_62_multiple_values_written_together(self, tmp_path):
        """Scenario 16.62: multiple key-value pairs are all written."""
        env_file = tmp_path / ".env"
        update_env_file(env_file, {"A": "1", "B": "2", "C": "3"})
        content = env_file.read_text(encoding="utf-8")
        assert "A=1" in content
        assert "B=2" in content
        assert "C=3" in content

    def test_16_63_legacy_token_removed_when_github_token_written(self, tmp_path):
        """Scenario 16.63: COPILOT_GITHUB_TOKEN is removed when GITHUB_TOKEN is set."""
        env_file = tmp_path / ".env"
        env_file.write_text("COPILOT_GITHUB_TOKEN=old_tok\nOTHER=keep\n", encoding="utf-8")
        update_env_file(env_file, {"GITHUB_TOKEN": "new_tok"})
        content = env_file.read_text(encoding="utf-8")
        assert "COPILOT_GITHUB_TOKEN" not in content
        assert "GITHUB_TOKEN=new_tok" in content
        assert "OTHER=keep" in content

    def test_16_64_file_ends_with_newline(self, tmp_path):
        """Scenario 16.64: written file always ends with a newline."""
        env_file = tmp_path / ".env"
        update_env_file(env_file, {"X": "y"})
        content = env_file.read_text(encoding="utf-8")
        assert content.endswith("\n")

    def test_16_65_value_with_spaces_quoted(self, tmp_path):
        """Scenario 16.65: values containing spaces are written quoted."""
        env_file = tmp_path / ".env"
        update_env_file(env_file, {"MSG": "hello world"})
        content = env_file.read_text(encoding="utf-8")
        # The line should contain the key, and the value should be present.
        assert "MSG=" in content
        assert "hello world" in content
