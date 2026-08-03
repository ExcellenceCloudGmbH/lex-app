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

    def _make_switch_result(self, mode: str):
        from lex.tools.mcp_mode_invoke import InvokeSwitchResult
        return InvokeSwitchResult(
            target_mode=mode,
            strategy="lex_mcp",
            override_written=True,
            server_stopped=False,
        )

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
                 mock.patch("lex.tools.mcp_mode_invoke.invoke_switch_to_mode",
                            return_value=self._make_switch_result("forward")) as invoke_mock:
                successes, errors = ai_dashboard._handle_save(form, root, env_path, mcp_path)

            self.assertFalse(errors)
            self.assertTrue(any("Mode changed to forward" in msg for msg in successes))
            invoke_mock.assert_called_once()
            kwargs = invoke_mock.call_args.kwargs
            self.assertEqual(invoke_mock.call_args.args[0], "forward")
            self.assertEqual(kwargs["project_root"], root)
            self.assertEqual(kwargs["mcp_config_path"], mcp_path)
            verify_mock.assert_called_once()
            v_kwargs = verify_mock.call_args.kwargs
            self.assertEqual(v_kwargs["project_root"], root)
            self.assertEqual(v_kwargs["mode"], "forward")
            self.assertTrue(v_kwargs.get("align_mcp_mode"))

    def test_save_with_same_mode_still_runs_verify_without_invoke(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env_path = root / ".env"
            mcp_path = root / "mcp.json"
            override_dir = root / ".lex-mcp"
            override_file = override_dir / "mode-override"

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
                 mock.patch.object(ai_dashboard, "MODE_OVERRIDE_DIR", override_dir), \
                 mock.patch.object(ai_dashboard, "MODE_OVERRIDE_FILE", override_file), \
                 mock.patch("lex.tools.mcp_mode_invoke.invoke_switch_to_mode") as invoke_mock:
                successes, errors = ai_dashboard._handle_save(form, root, env_path, mcp_path)

            self.assertFalse(errors)
            self.assertTrue(any("Mode saved as forward" in msg for msg in successes))
            verify_mock.assert_called_once()
            invoke_mock.assert_not_called()


class InvokeSwitchToModeTests(unittest.TestCase):
    def test_fallback_path_writes_override_env_and_mcp_json(self) -> None:
        from lex.tools import mcp_mode_invoke

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env_path = root / ".env"
            mcp_path = root / "mcp.json"
            env_path.write_text("LEX_MCP_MODE=backward\n", encoding="utf-8")
            mcp_path.write_text(
                json.dumps(
                    {
                        "servers": {
                            "lex-mcp-local": {
                                "type": "stdio",
                                "command": "python",
                                "args": ["-m", "lex_mcp.server", "--mode", "backward"],
                                "env": {"LEX_MCP_MODE": "backward"},
                            },
                        },
                    }
                ),
                encoding="utf-8",
            )

            override_dir = Path(tmp) / ".lex-mcp"
            override_file = override_dir / "mode-override"

            with mock.patch.dict("sys.modules", {"lex_mcp.mode_switch": None}), \
                 mock.patch.object(ai_dashboard, "MODE_OVERRIDE_DIR", override_dir), \
                 mock.patch.object(ai_dashboard, "MODE_OVERRIDE_FILE", override_file), \
                 mock.patch.object(ai_dashboard, "_invalidate_copilot_mcp_cache", return_value=False), \
                 mock.patch.object(ai_dashboard, "_stop_mcp_server", return_value=False):
                result = mcp_mode_invoke.invoke_switch_to_mode(
                    "forward",
                    project_root=root,
                    mcp_config_path=mcp_path,
                    source_tool="unittest",
                )

            self.assertIn(result.strategy, {"fallback", "lex_mcp"})
            self.assertTrue(result.override_written)
            updated = json.loads(mcp_path.read_text(encoding="utf-8"))
            server = updated["servers"]["lex-mcp-local"]
            mode_index = server["args"].index("--mode") + 1
            self.assertEqual(server["args"][mode_index], "forward")
            self.assertEqual(server["env"].get("LEX_MCP_MODE"), "forward")

    def test_rejects_unknown_mode(self) -> None:
        from lex.tools import mcp_mode_invoke
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                mcp_mode_invoke.invoke_switch_to_mode(
                    "banana",
                    project_root=Path(tmp),
                    mcp_config_path=Path(tmp) / "mcp.json",
                )


class VerifyAlignsMcpModeTests(unittest.TestCase):
    def test_align_invokes_switch_when_runtime_disagrees_with_env(self) -> None:
        from lex.tools import verify_ai_assets as verify_module

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env_path = root / ".env"
            mcp_path = root / "mcp.json"
            env_path.write_text('LEX_MCP_MODE="forward"\n', encoding="utf-8")
            mcp_path.write_text(
                json.dumps(
                    {
                        "servers": {
                            "lex-mcp-local": {
                                "args": ["-m", "lex_mcp.server", "--mode", "backward"],
                                "env": {"LEX_MCP_MODE": "backward"},
                            }
                        }
                    }
                ),
                encoding="utf-8",
            )

            captured: dict = {}

            def fake_invoke(target_mode, **kwargs):
                captured["target_mode"] = target_mode
                captured.update(kwargs)
                from lex.tools.mcp_mode_invoke import InvokeSwitchResult
                return InvokeSwitchResult(target_mode=target_mode, strategy="lex_mcp")

            with mock.patch("lex.tools.mcp_mode_invoke.invoke_switch_to_mode", side_effect=fake_invoke) as invoke_mock, \
                 mock.patch.object(verify_module, "resolve_active_mcp_mode", return_value=("forward", "project-dotenv")), \
                 mock.patch.object(verify_module, "resolve_active_python_executable", return_value=Path("python")), \
                 mock.patch.object(verify_module, "_resolve_package_directory", return_value=None), \
                 mock.patch.object(verify_module, "resolve_lex_app_package_root", return_value=None):
                result = verify_module.verify_ai_assets(
                    project_root=root,
                    mode=None,
                    align_mcp_mode=True,
                    mcp_config_path=mcp_path,
                )

            invoke_mock.assert_called_once()
            self.assertEqual(captured["target_mode"], "forward")
            self.assertEqual(result.mode, "forward")

    def test_align_does_not_invoke_when_runtime_matches_env(self) -> None:
        from lex.tools import verify_ai_assets as verify_module

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env_path = root / ".env"
            mcp_path = root / "mcp.json"
            env_path.write_text('LEX_MCP_MODE="forward"\n', encoding="utf-8")
            mcp_path.write_text(
                json.dumps(
                    {
                        "servers": {
                            "lex-mcp-local": {
                                "args": ["-m", "lex_mcp.server", "--mode", "forward"],
                                "env": {"LEX_MCP_MODE": "forward"},
                            }
                        }
                    }
                ),
                encoding="utf-8",
            )

            with mock.patch("lex.tools.mcp_mode_invoke.invoke_switch_to_mode") as invoke_mock, \
                 mock.patch.object(verify_module, "resolve_active_mcp_mode", return_value=("forward", "project-dotenv")), \
                 mock.patch.object(verify_module, "resolve_active_python_executable", return_value=Path("python")), \
                 mock.patch.object(verify_module, "_resolve_package_directory", return_value=None), \
                 mock.patch.object(verify_module, "resolve_lex_app_package_root", return_value=None), \
                 mock.patch.object(verify_module, "_read_override_mode", return_value=None):
                verify_module.verify_ai_assets(
                    project_root=root,
                    mode=None,
                    align_mcp_mode=True,
                    mcp_config_path=mcp_path,
                )

            invoke_mock.assert_not_called()
