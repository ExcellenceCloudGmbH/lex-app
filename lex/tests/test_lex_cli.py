import os
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock
from unittest.mock import patch
import sys

from click.testing import CliRunner

from generate_pycharm_configs import (
    CELERY_WORKER_COUNT_PROMPT,
    _build_celery_workers_parameters,
    generate_pycharm_configs,
)
from lex.bin.lex import build_celery_worker_command, build_flower_command, lex
from lex.tools.setup_with_ai import (
    DEFAULT_GEMINI_MODEL,
    DEFAULT_GIT_GEMINI_MAX_REPAIR_ATTEMPTS,
    DEFAULT_LEX_MCP_PRODUCTION,
    DEFAULT_REMOTE_MCP_TRANSPORT,
    DEFAULT_REMOTE_MCP_URL,
    LEX_MCP_LOCAL_SERVER_NAME,
    SetupWithAICredentials,
    SetupWithAIArtifacts,
    build_mcp_server_definition,
    resolve_github_copilot_mcp_config_path,
    resolve_active_python_executable,
    update_env_file,
    write_github_copilot_mcp_config,
)


class GeneratePyCharmConfigsTests(TestCase):
    def test_build_celery_workers_parameters_prompts_for_worker_count(self):
        self.assertEqual(
            _build_celery_workers_parameters(),
            f"celery-workers {CELERY_WORKER_COUNT_PROMPT}",
        )

    def test_generate_configs_includes_flower_run_configuration(self):
        with TemporaryDirectory() as tmp_dir:
            project_root = Path(tmp_dir) / "sample-project"
            project_root.mkdir()
            (project_root / "pyproject.toml").write_text(
                "[project]\nname='sample-project'\n",
                encoding="utf-8",
            )

            generate_pycharm_configs(project_root)

            flower_config = (project_root / ".run" / "Flower.run.xml").read_text(
                encoding="utf-8"
            )
            self.assertIn('name="Flower"', flower_config)
            self.assertIn('<option name="PARAMETERS" value="flower" />', flower_config)

    def test_generate_configs_includes_setup_with_ai_run_configuration(self):
        with TemporaryDirectory() as tmp_dir:
            project_root = Path(tmp_dir) / "sample-project"
            project_root.mkdir()
            (project_root / "pyproject.toml").write_text(
                "[project]\nname='sample-project'\n",
                encoding="utf-8",
            )

            generate_pycharm_configs(project_root)

            setup_with_ai_config = (project_root / ".run" / "Setup_With_AI.run.xml").read_text(
                encoding="utf-8"
            )
            self.assertIn('name="Setup With AI"', setup_with_ai_config)
            self.assertIn(
                '<option name="PARAMETERS" value="setup-with-ai" />',
                setup_with_ai_config,
            )

    def test_generate_configs_includes_prompted_celery_worker_configuration(self):
        with TemporaryDirectory() as tmp_dir:
            project_root = Path(tmp_dir) / "sample-project"
            project_root.mkdir()
            (project_root / "pyproject.toml").write_text(
                "[project]\nname='sample-project'\n",
                encoding="utf-8",
            )

            generate_pycharm_configs(project_root)

            worker_config = (project_root / ".run" / "Celery_Worker.run.xml").read_text(
                encoding="utf-8"
            )
            self.assertIn('name="Celery Workers"', worker_config)
            self.assertIn(
                (
                    '<option name="PARAMETERS" value="celery-workers '
                    f'{CELERY_WORKER_COUNT_PROMPT}" />'
                ),
                worker_config,
            )
            self.assertIn(
                '<env name="IS_RUNNING_IN_CELERY" value="true" />', worker_config
            )
            self.assertIn('<env name="CELERY_ACTIVE" value="true" />', worker_config)


class LexFlowerCommandTests(TestCase):
    def test_build_celery_worker_command_uses_threads_pool_on_macos(self):
        with patch("lex.bin.lex.platform.system", return_value="Darwin"):
            command = build_celery_worker_command(3)

        self.assertIn("-P", command)
        self.assertIn("threads", command)
        self.assertEqual(command[-2:], ["-n", "worker3@%h"])

    def test_build_celery_worker_command_uses_threads_pool_on_windows(self):
        with patch("lex.bin.lex.platform.system", return_value="Windows"):
            command = build_celery_worker_command(4)

        self.assertIn("-P", command)
        self.assertIn("threads", command)
        self.assertEqual(command[-2:], ["-n", "worker4@%h"])

    def test_build_celery_worker_command_skips_threads_pool_on_linux(self):
        with patch("lex.bin.lex.platform.system", return_value="Linux"):
            command = build_celery_worker_command(2)

        self.assertNotIn("-P", command)
        self.assertEqual(command[-2:], ["-n", "worker2@%h"])

    def test_celery_workers_starts_requested_number_of_workers(self):
        runner = CliRunner()

        processes = []
        for poll_result in (0, None, None):
            process = Mock()
            process.poll = Mock(return_value=poll_result)
            process.terminate = Mock()
            process.wait = Mock(return_value=0)
            process.kill = Mock()
            processes.append(process)

        with patch("lex.bin.lex.subprocess.Popen", side_effect=processes) as popen:
            result = runner.invoke(lex, ["celery-workers", "3"])

        self.assertEqual(result.exit_code, 0, msg=result.output)
        self.assertEqual(popen.call_count, 3)
        commands = [call.args[0] for call in popen.call_args_list]
        self.assertEqual(commands[0][-2:], ["-n", "worker1@%h"])
        self.assertEqual(commands[1][-2:], ["-n", "worker2@%h"])
        self.assertEqual(commands[2][-2:], ["-n", "worker3@%h"])
        for call in popen.call_args_list:
            self.assertEqual(call.kwargs["env"]["IS_RUNNING_IN_CELERY"], "true")
            self.assertEqual(call.kwargs["env"]["CELERY_ACTIVE"], "true")

    def test_build_flower_command_uses_project_defaults(self):
        settings = SimpleNamespace(
            FLOWER_ADDRESS="0.0.0.0",
            FLOWER_PORT=5566,
            FLOWER_URL_PREFIX="flower",
        )

        command = build_flower_command(settings, ["--inspect_timeout=5000"])

        self.assertEqual(command[:3], ["--app", "lex_app.celery:app", "flower"])
        self.assertIn("--address=0.0.0.0", command)
        self.assertIn("--port=5566", command)
        self.assertIn("--url_prefix=flower", command)
        self.assertEqual(command[-1], "--inspect_timeout=5000")

    def test_setup_writes_flower_defaults_into_env_template(self):
        runner = CliRunner()

        with TemporaryDirectory() as tmp_dir:
            project_root = Path(tmp_dir) / "sample-project"
            project_root.mkdir()
            (project_root / "pyproject.toml").write_text(
                "[project]\nname='sample-project'\n",
                encoding="utf-8",
            )

            result = runner.invoke(lex, ["setup", "--project-root", str(project_root)])

            self.assertEqual(result.exit_code, 0, msg=result.output)
            env_content = (project_root / ".env").read_text(encoding="utf-8")
            self.assertIn("FLOWER_ADDRESS=127.0.0.1", env_content)
            self.assertIn("FLOWER_PORT=5555", env_content)
            self.assertTrue((project_root / ".run" / "Flower.run.xml").exists())
            self.assertTrue((project_root / ".run" / "Celery_Worker.run.xml").exists())

    def test_setup_with_ai_runs_full_non_django_flow(self):
        runner = CliRunner()

        with TemporaryDirectory() as tmp_dir:
            project_root = Path(tmp_dir) / "sample-project"
            project_root.mkdir()
            env_path = project_root / ".env"
            env_path.write_text("", encoding="utf-8")

            artifacts = SetupWithAIArtifacts(
                env_file_path=env_path,
                mcp_config_path=project_root / "mcp.json",
                wrapper_script_path=project_root / "wrapper_mcp.py",
                python_executable=project_root / ".venv" / "bin" / "python",
            )

            with (
                patch("lex.bin.lex.ensure_env_file", return_value=(str(env_path), False)),
                patch("lex.bin.lex.generate_configs") as generate_configs_mock,
                patch(
                    "lex.bin.lex.resolve_active_python_executable",
                    return_value=artifacts.python_executable,
                ) as resolve_python_mock,
                patch("lex.bin.lex.install_lex_mcp_local") as install_mock,
                patch(
                    "lex.bin.lex.launch_setup_with_ai_form",
                    return_value=SetupWithAICredentials(
                        github_token="ghu_example",
                        remote_mcp_api_key="remote_api_key",
                        gemini_api_key="gemini_api_key",
                    ),
                ) as launch_form_mock,
                patch(
                    "lex.bin.lex.configure_ai_integration",
                    return_value=artifacts,
                ) as configure_mock,
            ):
                result = runner.invoke(
                    lex,
                    ["setup-with-ai", "--project-root", str(project_root)],
                )

            self.assertEqual(result.exit_code, 0, msg=result.output)
            generate_configs_mock.assert_called_once_with(project_root.resolve().as_posix())
            resolve_python_mock.assert_called_once_with(project_root.resolve())
            install_mock.assert_called_once_with(artifacts.python_executable)
            launch_form_mock.assert_called_once()
            configure_mock.assert_called_once_with(
                project_root=project_root.resolve(),
                github_token="ghu_example",
                remote_mcp_api_key="remote_api_key",
                gemini_api_key="gemini_api_key",
                remote_mcp_url=DEFAULT_REMOTE_MCP_URL,
                python_executable=artifacts.python_executable,
            )
            self.assertIn("Setup complete.", result.output)


class SetupWithAIToolsTests(TestCase):
    def test_update_env_file_preserves_comments_and_replaces_existing_keys(self):
        with TemporaryDirectory() as tmp_dir:
            env_path = Path(tmp_dir) / ".env"
            env_path.write_text(
                "# existing comment\nCOPILOT_GITHUB_TOKEN=old_token\nKEEP_ME=yes\n",
                encoding="utf-8",
            )

            update_env_file(
                env_path,
                {
                    "REMOTE_MCP_TRANSPORT": DEFAULT_REMOTE_MCP_TRANSPORT,
                    "REMOTE_MCP_URL": DEFAULT_REMOTE_MCP_URL,
                    "GEMINI_MODEL": DEFAULT_GEMINI_MODEL,
                    "GIT_GEMINI_MAX_REPAIR_ATTEMPTS": DEFAULT_GIT_GEMINI_MAX_REPAIR_ATTEMPTS,
                    "LEX_MCP_PRODUCTION": DEFAULT_LEX_MCP_PRODUCTION,
                    "GITHUB_TOKEN": "new_token",
                    "REMOTE_MCP_API_KEY": "new_api_key",
                    "GEMINI_API_KEY": "gemini_api_key",
                },
            )

            env_content = env_path.read_text(encoding="utf-8")
            self.assertIn("# existing comment", env_content)
            self.assertIn(f"REMOTE_MCP_TRANSPORT={DEFAULT_REMOTE_MCP_TRANSPORT}", env_content)
            self.assertIn(f"REMOTE_MCP_URL={DEFAULT_REMOTE_MCP_URL}", env_content)
            self.assertIn(f"GEMINI_MODEL={DEFAULT_GEMINI_MODEL}", env_content)
            self.assertIn(
                f"GIT_GEMINI_MAX_REPAIR_ATTEMPTS={DEFAULT_GIT_GEMINI_MAX_REPAIR_ATTEMPTS}",
                env_content,
            )
            self.assertIn(f"LEX_MCP_PRODUCTION={DEFAULT_LEX_MCP_PRODUCTION}", env_content)
            self.assertIn("GITHUB_TOKEN=new_token", env_content)
            self.assertNotIn("COPILOT_GITHUB_TOKEN=", env_content)
            self.assertIn("KEEP_ME=yes", env_content)
            self.assertIn("REMOTE_MCP_API_KEY=new_api_key", env_content)
            self.assertIn("GEMINI_API_KEY=gemini_api_key", env_content)

    def test_write_github_copilot_mcp_config_replaces_legacy_alias(self):
        with TemporaryDirectory() as tmp_dir:
            mcp_path = Path(tmp_dir) / "mcp.json"
            mcp_path.write_text(
                (
                    '{\n'
                    '  "servers": {\n'
                    '    "com.atlassian/atlassian-mcp-server": {"type": "http"},\n'
                    '    "lex-mcp-wrapper": {"type": "stdio"}\n'
                    "  }\n"
                    "}\n"
                ),
                encoding="utf-8",
            )

            server_definition = build_mcp_server_definition(
                python_executable=Path("/venv/bin/python"),
                wrapper_script_path=Path("/venv/lib/python3.12/site-packages/lex_mcp_local/wrapper_mcp.py"),
                github_token="ghu_example",
                remote_mcp_api_key="remote_api_key",
                gemini_api_key="gemini_api_key",
            )

            write_github_copilot_mcp_config(mcp_path, server_definition)

            config = mcp_path.read_text(encoding="utf-8")
            self.assertIn('"com.atlassian/atlassian-mcp-server"', config)
            self.assertIn(f'"{LEX_MCP_LOCAL_SERVER_NAME}"', config)
            self.assertNotIn('"lex-mcp-wrapper"', config)
            self.assertIn('"command": "/venv/bin/python"', config)
            self.assertIn(f'"REMOTE_MCP_TRANSPORT": "{DEFAULT_REMOTE_MCP_TRANSPORT}"', config)
            self.assertIn(f'"REMOTE_MCP_URL": "{DEFAULT_REMOTE_MCP_URL}"', config)
            self.assertIn(f'"GEMINI_MODEL": "{DEFAULT_GEMINI_MODEL}"', config)
            self.assertIn(
                f'"GIT_GEMINI_MAX_REPAIR_ATTEMPTS": "{DEFAULT_GIT_GEMINI_MAX_REPAIR_ATTEMPTS}"',
                config,
            )
            self.assertIn(f'"LEX_MCP_PRODUCTION": "{DEFAULT_LEX_MCP_PRODUCTION}"', config)
            self.assertIn('"REMOTE_MCP_API_KEY": "remote_api_key"', config)
            self.assertIn('"GITHUB_TOKEN": "ghu_example"', config)
            self.assertNotIn('"COPILOT_GITHUB_TOKEN"', config)
            self.assertIn('"GEMINI_API_KEY": "gemini_api_key"', config)

    def test_resolve_github_copilot_mcp_config_path_uses_home_config_on_unix(self):
        with patch("lex.tools.setup_with_ai.os.name", "posix"):
            path = resolve_github_copilot_mcp_config_path(home=Path("/Users/example"))

        self.assertEqual(
            path,
            Path("/Users/example/.config/github-copilot/intellij/mcp.json"),
        )

    def test_resolve_active_python_executable_prefers_project_virtualenv(self):
        with TemporaryDirectory() as tmp_dir:
            project_root = Path(tmp_dir)
            python_path = project_root / ".venv" / "bin" / "python"
            python_path.parent.mkdir(parents=True)
            python_path.write_text("", encoding="utf-8")

            resolved = resolve_active_python_executable(project_root, env={})

        self.assertEqual(resolved, Path(os.path.abspath(python_path)))

    def test_resolve_active_python_executable_prefers_project_virtualenv_over_conda(self):
        with TemporaryDirectory() as tmp_dir:
            project_root = Path(tmp_dir)
            project_python = project_root / ".venv" / "bin" / "python"
            project_python.parent.mkdir(parents=True)
            project_python.write_text("", encoding="utf-8")

            conda_python = project_root / "conda-base" / "bin" / "python"
            conda_python.parent.mkdir(parents=True)
            conda_python.write_text("", encoding="utf-8")

            resolved = resolve_active_python_executable(
                project_root,
                env={"CONDA_PREFIX": str(conda_python.parent.parent)},
            )

        self.assertEqual(resolved, Path(os.path.abspath(project_python)))

    def test_resolve_active_python_executable_prefers_path_python_over_current_process(self):
        with TemporaryDirectory() as tmp_dir:
            project_root = Path(tmp_dir)
            shell_python = project_root / "shell-env" / "bin" / "python"
            shell_python.parent.mkdir(parents=True)
            shell_python.write_text("", encoding="utf-8")

            with patch("lex.tools.setup_with_ai.shutil.which", return_value=str(shell_python)):
                resolved = resolve_active_python_executable(project_root, env={})

        self.assertEqual(resolved, Path(os.path.abspath(shell_python)))
        self.assertNotEqual(resolved, Path(sys.executable).resolve())
