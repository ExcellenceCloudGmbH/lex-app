from __future__ import annotations

import tempfile
import json
import os
import time
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
    """Reading the one-shot override marker.

    Patched at ``lex_mcp.mode_switch.OVERRIDE_FILE``, which is now the single
    definition of where the marker lives. There used to be three copies of that
    path -- here, in ai_assets, and in mode_switch -- and patching only this
    one meant these tests read the developer's real ``~/.lex-mcp`` instead of
    their fixture, so they reported whatever mode that machine last switched to.
    """

    def _override_at(self, path: Path):
        from lex_mcp import mode_switch

        return mock.patch.object(mode_switch, "OVERRIDE_FILE", path)

    def test_reads_json_override_payload(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            override_path = Path(tmp) / "mode-override"
            override_path.write_text('{"mode": "review"}', encoding="utf-8")

            with self._override_at(override_path):
                payload = ai_dashboard._read_override_file()

            self.assertEqual(payload, {"mode": "review"})

    def test_reads_plaintext_override_payload(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            override_path = Path(tmp) / "mode-override"
            override_path.write_text("edit", encoding="utf-8")

            with self._override_at(override_path):
                payload = ai_dashboard._read_override_file()

            self.assertEqual(payload, {"mode": "edit"})

    def test_an_override_for_another_project_is_not_read(self) -> None:
        """The reported bug: one project's dashboard switch changed them all."""
        from lex_mcp import mode_switch

        with tempfile.TemporaryDirectory() as tmp:
            override_path = Path(tmp) / "mode-override"
            mine = Path(tmp) / "mine"
            theirs = Path(tmp) / "theirs"
            mine.mkdir()
            theirs.mkdir()
            override_path.write_text(
                json.dumps({"mode": "review", "project_root": str(theirs)}),
                encoding="utf-8",
            )

            with self._override_at(override_path):
                self.assertIsNone(ai_dashboard._read_override_file(mine))
                self.assertEqual(
                    ai_dashboard._read_override_file(theirs).get("mode"), "review"
                )

    def test_a_stale_override_is_ignored(self) -> None:
        """It is a handoff. One still pending an hour later was never collected."""
        from lex_mcp import mode_switch

        with tempfile.TemporaryDirectory() as tmp:
            override_path = Path(tmp) / "mode-override"
            override_path.write_text(
                json.dumps(
                    {
                        "mode": "review",
                        "written_at": time.time()
                        - (mode_switch.OVERRIDE_TTL_SECONDS + 60),
                    }
                ),
                encoding="utf-8",
            )

            with self._override_at(override_path):
                self.assertIsNone(ai_dashboard._read_override_file())


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
        # No ``strategy=``: the field was removed from InvokeSwitchResult, and
        # this fixture kept passing it -- so the test could not even build its
        # own input and errored out with a TypeError. That is why it failed to
        # catch the dashboard still reading ``switch_result.strategy``, which
        # made every real mode change report "Failed to switch mode".
        from lex.tools.mcp_mode_invoke import InvokeSwitchResult
        return InvokeSwitchResult(
            target_mode=mode,
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

            with mock.patch("lex_mcp.ai_dashboard.verify_ai_assets", return_value=verify_result) as verify_mock, \
                 mock.patch("lex_mcp.mode_switch.invoke_switch_to_mode",
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

            from lex_mcp import mode_switch

            # Patched at mode_switch, the single definition of the override
            # path. The two constants this used to redirect were duplicates,
            # and the read went through neither of them.
            with mock.patch("lex_mcp.ai_dashboard.verify_ai_assets", return_value=verify_result) as verify_mock, \
                 mock.patch.object(mode_switch, "OVERRIDE_DIR", override_dir), \
                 mock.patch.object(mode_switch, "OVERRIDE_FILE", override_file), \
                 mock.patch("lex_mcp.mode_switch.invoke_switch_to_mode") as invoke_mock:
                successes, errors = ai_dashboard._handle_save(form, root, env_path, mcp_path)

            self.assertFalse(errors)
            self.assertTrue(any("Mode saved as forward" in msg for msg in successes))
            verify_mock.assert_called_once()
            invoke_mock.assert_not_called()


class InvokeSwitchToModeTests(unittest.TestCase):
    def test_switch_writes_override_env_and_mcp_json(self) -> None:
        """One implementation, in lex-mcp-local, next to the primitives it drives.

        This used to assert on a ``strategy`` field naming which of two
        implementations had run. The second one reimplemented the primitives
        inside lex-app for the case where ``lex_mcp.mode_switch`` could not be
        imported -- but it reached into ``lex.tools.ai_dashboard``, itself a
        shim over lex-mcp-local, so the situation it covered could not arise.
        It went with the move; there is nothing left to pick between.
        """
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

            from lex_mcp import mode_switch

            override_file = Path(tmp) / ".lex-mcp" / "mode-override"

            # Two things this touches outside the temp directory unless it is
            # boxed in. The override marker lives at a fixed path under the
            # real $HOME. And mode sync deliberately rewrites the *cwd's*
            # ``.env`` as well as the project's -- correct in production, where
            # cwd is the project, and destructive here, where cwd is this
            # repository and that .env is checked in.
            previous_cwd = os.getcwd()
            os.chdir(tmp)
            try:
                with mock.patch.object(mode_switch, "OVERRIDE_DIR", override_file.parent), \
                     mock.patch.object(mode_switch, "OVERRIDE_FILE", override_file):
                    result = mcp_mode_invoke.invoke_switch_to_mode(
                        "forward",
                        project_root=root,
                        mcp_config_path=mcp_path,
                        source_tool="unittest",
                        stop_server=False,
                    )
            finally:
                os.chdir(previous_cwd)

            self.assertTrue(result.override_written, result.errors)
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
                return InvokeSwitchResult(target_mode=target_mode)

            # Patched where the implementation now lives: verification imports
            # it from lex_mcp.mode_switch, so patching the lex-app shim would
            # leave the real one running and this test asserting on nothing.
            with mock.patch("lex_mcp.mode_switch.invoke_switch_to_mode", side_effect=fake_invoke) as invoke_mock, \
                 mock.patch("lex_mcp.ai_assets.resolve_active_mcp_mode", return_value=("forward", "project-dotenv")), \
                 mock.patch("lex_mcp.ai_assets.resolve_active_python_executable", return_value=Path("python")), \
                 mock.patch("lex_mcp.ai_assets._resolve_package_directory", return_value=None), \
                 mock.patch("lex_mcp.ai_assets.resolve_lex_app_package_root", return_value=None):
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

            with mock.patch("lex_mcp.mode_switch.invoke_switch_to_mode") as invoke_mock, \
                 mock.patch("lex_mcp.ai_assets.resolve_active_mcp_mode", return_value=("forward", "project-dotenv")), \
                 mock.patch("lex_mcp.ai_assets.resolve_active_python_executable", return_value=Path("python")), \
                 mock.patch("lex_mcp.ai_assets._resolve_package_directory", return_value=None), \
                 mock.patch("lex_mcp.ai_assets.resolve_lex_app_package_root", return_value=None), \
                 mock.patch("lex_mcp.ai_assets._read_override_mode", return_value=None):
                verify_module.verify_ai_assets(
                    project_root=root,
                    mode=None,
                    align_mcp_mode=True,
                    mcp_config_path=mcp_path,
                )

            invoke_mock.assert_not_called()
