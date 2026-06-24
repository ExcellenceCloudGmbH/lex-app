from __future__ import annotations

import tempfile
from pathlib import Path
from unittest import mock
import unittest

from lex.tools import ai_dashboard
from lex.tools.verify_ai_assets import verify_directory


class VerifyDirectoryModeManagedPruningTests(unittest.TestCase):
    def _write(self, path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def test_prunes_stale_files_from_mode_managed_github_subdirs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project_root = root / "project"
            source_root = root / "source"
            source_github = source_root / ".github"

            # Destination starts with stale files from the previous mode.
            self._write(project_root / ".github" / "agents" / "old.agent.md", "old")
            self._write(
                project_root / ".github" / "instructions" / "old.instructions.md",
                "old",
            )
            self._write(
                project_root / ".github" / "workflows" / "custom.yml",
                "keep-me",
            )

            # Active mode source contains only the new agent file.
            self._write(source_github / "agents" / "new.agent.md", "new")

            result = verify_directory(
                project_root=project_root,
                source_directory=source_github,
                directory_name=".github",
                prune_extra_relative_dirs=("agents", "instructions", "prompts"),
            )

            self.assertIn(Path("agents/new.agent.md"), result.restored_files)
            self.assertIn(Path("agents/old.agent.md"), result.removed_files)
            self.assertIn(
                Path("instructions/old.instructions.md"),
                result.removed_files,
            )
            self.assertFalse((project_root / ".github" / "agents" / "old.agent.md").exists())
            self.assertFalse(
                (project_root / ".github" / "instructions" / "old.instructions.md").exists()
            )
            # Non-managed folders remain untouched.
            self.assertTrue((project_root / ".github" / "workflows" / "custom.yml").exists())

    def test_does_not_prune_when_no_managed_dirs_are_configured(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project_root = root / "project"
            source_root = root / "source"
            source_github = source_root / ".github"

            self._write(project_root / ".github" / "agents" / "old.agent.md", "old")
            self._write(source_github / "agents" / "new.agent.md", "new")

            result = verify_directory(
                project_root=project_root,
                source_directory=source_github,
                directory_name=".github",
            )

            self.assertEqual(result.removed_files, ())
            self.assertTrue((project_root / ".github" / "agents" / "old.agent.md").exists())


class ReadOverrideFileTests(unittest.TestCase):
    def test_reads_json_override_payload(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            override_path = Path(tmp) / "mode-override"
            override_path.write_text('{"mode": "review"}', encoding="utf-8")

            with mock.patch.object(ai_dashboard, "MODE_OVERRIDE_FILE", override_path):
                payload = ai_dashboard._read_override_file()

            self.assertEqual(payload, {"mode": "review"})

    def test_reads_plaintext_override_payload(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            override_path = Path(tmp) / "mode-override"
            override_path.write_text("edit", encoding="utf-8")

            with mock.patch.object(ai_dashboard, "MODE_OVERRIDE_FILE", override_path):
                payload = ai_dashboard._read_override_file()

            self.assertEqual(payload, {"mode": "edit"})
