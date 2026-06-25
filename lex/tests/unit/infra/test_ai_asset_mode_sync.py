from __future__ import annotations

import tempfile
import json
from pathlib import Path
from types import SimpleNamespace
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


class HandleSaveModeSyncTests(unittest.TestCase):
    def _write_mode_mcp_json(self, path: Path, mode: str) -> None:
        payload = {
            "servers": {
                "lex-mcp-local": {
                    "type": "stdio",
                    "command": "python",
                    "args": ["-m", "lex_mcp.server", "--mode", mode],
                    "env": {"LEX_MCP_MODE": mode},
                },
            },
        }
        path.write_text(json.dumps(payload), encoding="utf-8")

    def test_save_syncs_mcp_json_to_selected_mode_and_runs_verify(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env_path = root / ".env"
            mcp_path = root / "mcp.json"

            env_path.write_text("LEX_MCP_MODE=forward\n", encoding="utf-8")
            self._write_mode_mcp_json(mcp_path, "backward")

            verify_result = SimpleNamespace(restored_files=(), removed_files=())
            form = {
                "mcp_mode": ["forward"],
                "github_token": [""],
                "remote_mcp_api_key": [""],
                "remote_mcp_url": [""],
            }

            with mock.patch.object(ai_dashboard, "verify_ai_assets", return_value=verify_result) as verify_mock, \
                 mock.patch.object(ai_dashboard, "_stop_mcp_server", return_value=False), \
                 mock.patch.object(ai_dashboard, "_invalidate_copilot_mcp_cache", return_value=False), \
                 mock.patch.object(ai_dashboard, "_write_mode_override"):
                successes, errors = ai_dashboard._handle_save(form, root, env_path, mcp_path)

            self.assertFalse(errors)
            self.assertTrue(any("Mode changed to forward" in msg for msg in successes))
            verify_mock.assert_called_once_with(project_root=root, mode="forward")

            updated = json.loads(mcp_path.read_text(encoding="utf-8"))
            server = updated["servers"]["lex-mcp-local"]
            mode_index = server["args"].index("--mode") + 1
            self.assertEqual(server["args"][mode_index], "forward")
            self.assertEqual(server["env"].get("LEX_MCP_MODE"), "forward")

    def test_save_with_same_mode_still_runs_verify_without_restart(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env_path = root / ".env"
            mcp_path = root / "mcp.json"

            env_path.write_text("LEX_MCP_MODE=forward\n", encoding="utf-8")
            self._write_mode_mcp_json(mcp_path, "forward")

            verify_result = SimpleNamespace(restored_files=(), removed_files=())
            form = {
                "mcp_mode": ["forward"],
                "github_token": [""],
                "remote_mcp_api_key": [""],
                "remote_mcp_url": [""],
            }

            with mock.patch.object(ai_dashboard, "verify_ai_assets", return_value=verify_result) as verify_mock, \
                 mock.patch.object(ai_dashboard, "_stop_mcp_server", return_value=False) as stop_mock, \
                 mock.patch.object(ai_dashboard, "_invalidate_copilot_mcp_cache", return_value=False) as cache_mock, \
                 mock.patch.object(ai_dashboard, "_write_mode_override") as override_mock:
                successes, errors = ai_dashboard._handle_save(form, root, env_path, mcp_path)

            self.assertFalse(errors)
            self.assertTrue(any("Mode saved as forward" in msg for msg in successes))
            verify_mock.assert_called_once_with(project_root=root, mode="forward")
            stop_mock.assert_not_called()
            cache_mock.assert_not_called()
            override_mock.assert_not_called()

