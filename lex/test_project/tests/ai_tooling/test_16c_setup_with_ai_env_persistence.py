"""
Cluster 16c: `lex setup-with-ai` — `.env` file persistence.

Intent
------

``lex setup-with-ai`` writes credentials (GitHub token, remote MCP API key,
etc.) into the target project's ``.env`` file via ``update_env_file``. The
customer-visible contract, drawn from the functions' own logic and comments:

* Values are written unquoted when they are "safe" (alnum + a small set of
  URL/path-safe punctuation) and JSON-quoted otherwise — so a value
  containing a space, quote, or `#` round-trips instead of corrupting the
  file or being parsed as a comment / multiple tokens.
* Updating a key that already exists in the file replaces its value **in
  place** (same line position) rather than appending a duplicate — a second
  run of setup must not leave two conflicting definitions of the same key,
  where dotenv tooling's "last wins" behaviour would silently pick the wrong
  one depending on file layout.
* Comments and blank lines in an existing ``.env`` are preserved untouched.
* The legacy ``COPILOT_GITHUB_TOKEN`` key is retired in favour of
  ``GITHUB_TOKEN``: once a caller writes ``GITHUB_TOKEN``, any legacy key is
  dropped so the file doesn't carry two credentials that can drift apart.
* The write is atomic (``_atomic_write_text``): a crash mid-write must never
  leave a torn/partial ``.env`` behind, because that file also holds the
  Keycloak/DB credentials this project depends on to even start.

Scenario numbering continues from 16b (16.29-16.43) at 16.44.
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import pytest

from lex.tools import setup_with_ai

pytestmark = pytest.mark.ai_tooling


class _TempEnvFileTestCase(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        self.env_path = Path(self._tmpdir.name) / ".env"


# ---------------------------------------------------------------------
# 16.44–16.46 — _format_env_value
# ---------------------------------------------------------------------
class TestCluster16h_FormatEnvValue(unittest.TestCase):
    def test_16_44_safe_value_is_written_unquoted(self):
        self.assertEqual(
            setup_with_ai._format_env_value("ghp_abc123DEF-token_9"),
            "ghp_abc123DEF-token_9",
        )

    def test_16_45_value_with_whitespace_is_json_quoted(self):
        self.assertEqual(
            setup_with_ai._format_env_value("has space"), '"has space"'
        )

    def test_16_46_value_with_special_characters_round_trips_through_json(self):
        import json

        formatted = setup_with_ai._format_env_value('quote"here#and$more')
        self.assertEqual(json.loads(formatted), 'quote"here#and$more')


# ---------------------------------------------------------------------
# 16.47–16.52 — update_env_file / _read_dotenv_value round trip
# ---------------------------------------------------------------------
class TestCluster16i_UpdateEnvFileRoundTrip(_TempEnvFileTestCase):
    def test_16_47_writes_a_fresh_file_with_all_values(self):
        setup_with_ai.update_env_file(
            self.env_path, {"GITHUB_TOKEN": "abc123", "MCP_MODE": "brief"}
        )
        self.assertEqual(
            setup_with_ai._read_dotenv_value(self.env_path, "GITHUB_TOKEN"),
            "abc123",
        )
        self.assertEqual(
            setup_with_ai._read_dotenv_value(self.env_path, "MCP_MODE"), "brief"
        )

    def test_16_48_updating_an_existing_key_replaces_it_in_place(self):
        self.env_path.write_text(
            "# a comment\nGITHUB_TOKEN=old-value\nOTHER_KEY=unchanged\n",
            encoding="utf-8",
        )
        setup_with_ai.update_env_file(self.env_path, {"GITHUB_TOKEN": "new-value"})

        lines = self.env_path.read_text(encoding="utf-8").splitlines()
        self.assertEqual(lines[0], "# a comment")
        self.assertEqual(lines[1], "GITHUB_TOKEN=new-value")
        self.assertEqual(lines[2], "OTHER_KEY=unchanged")
        # No duplicate GITHUB_TOKEN line was appended.
        self.assertEqual(
            sum(1 for l in lines if l.startswith("GITHUB_TOKEN=")), 1
        )

    def test_16_49_blank_lines_and_comments_are_preserved(self):
        original = "# header\n\nGITHUB_TOKEN=abc\n\n# trailer\n"
        self.env_path.write_text(original, encoding="utf-8")
        setup_with_ai.update_env_file(self.env_path, {"GITHUB_TOKEN": "abc"})

        lines = self.env_path.read_text(encoding="utf-8").splitlines()
        self.assertIn("# header", lines)
        self.assertIn("# trailer", lines)
        self.assertIn("", lines)

    def test_16_50_legacy_github_token_key_is_dropped_when_github_token_written(self):
        self.env_path.write_text(
            "COPILOT_GITHUB_TOKEN=legacy-value\n", encoding="utf-8"
        )
        setup_with_ai.update_env_file(self.env_path, {"GITHUB_TOKEN": "new-value"})

        content = self.env_path.read_text(encoding="utf-8")
        self.assertNotIn("COPILOT_GITHUB_TOKEN", content)
        self.assertEqual(
            setup_with_ai._read_dotenv_value(self.env_path, "GITHUB_TOKEN"),
            "new-value",
        )

    def test_16_51_legacy_key_survives_untouched_when_github_token_not_written(self):
        """The legacy key is only removed as a side effect of writing its
        replacement — an unrelated update must not silently delete it."""
        self.env_path.write_text(
            "COPILOT_GITHUB_TOKEN=legacy-value\n", encoding="utf-8"
        )
        setup_with_ai.update_env_file(self.env_path, {"OTHER_KEY": "x"})

        self.assertEqual(
            setup_with_ai._read_dotenv_value(
                self.env_path, "COPILOT_GITHUB_TOKEN"
            ),
            "legacy-value",
        )

    def test_16_52_value_needing_quoting_round_trips_through_read_dotenv_value(self):
        setup_with_ai.update_env_file(
            self.env_path, {"REMOTE_MCP_API_KEY": "key with space"}
        )
        self.assertEqual(
            setup_with_ai._read_dotenv_value(self.env_path, "REMOTE_MCP_API_KEY"),
            "key with space",
        )

    def test_16_53_missing_key_and_missing_file_both_read_as_none(self):
        self.assertIsNone(
            setup_with_ai._read_dotenv_value(self.env_path, "NOPE")
        )
        setup_with_ai.update_env_file(self.env_path, {"SOME_KEY": "v"})
        self.assertIsNone(
            setup_with_ai._read_dotenv_value(self.env_path, "OTHER_MISSING_KEY")
        )


# ---------------------------------------------------------------------
# 16.54 — _atomic_write_text
# ---------------------------------------------------------------------
class TestCluster16j_AtomicWrite(_TempEnvFileTestCase):
    def test_16_54_creates_parent_directories_and_final_content(self):
        nested_path = self.env_path.parent / "nested" / "dir" / ".env"
        setup_with_ai._atomic_write_text(nested_path, "GITHUB_TOKEN=abc\n")

        self.assertTrue(nested_path.exists())
        self.assertEqual(
            nested_path.read_text(encoding="utf-8"), "GITHUB_TOKEN=abc\n"
        )
        # No leftover temp file from the atomic-rename dance.
        leftovers = [
            p for p in nested_path.parent.iterdir() if p.name != nested_path.name
        ]
        self.assertEqual(leftovers, [])


if __name__ == "__main__":
    unittest.main()
