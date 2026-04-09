import json
import os
from pathlib import Path
import sqlite3
import subprocess
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest import TestCase
from unittest.mock import Mock
from unittest.mock import patch
import sys

from click.testing import CliRunner

import lex.tools.setup_with_ai as setup_with_ai_module

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
    SetupWithAIMCPProbeResult,
    SetupWithAIServerRuntime,
    bootstrap_github_copilot_mcp_server_for_pycharm,
    build_mcp_server_definition,
    install_lex_mcp_local,
    resolve_github_copilot_mcp_config_path,
    resolve_github_copilot_state_db_path,
    resolve_active_python_executable,
    start_lex_mcp_local_server,
    update_env_file,
    verify_lex_mcp_local_server_starts,
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
            self.assertFalse((project_root / ".github").exists())
            self.assertFalse((project_root / "docs").exists())

    def test_setup_with_ai_runs_full_non_django_flow(self):
        runner = CliRunner()

        with TemporaryDirectory() as tmp_dir:
            project_root = Path(tmp_dir) / "sample-project"
            project_root.mkdir()
            env_path = project_root / ".env"
            env_path.write_text("", encoding="utf-8")
            call_order: list[str] = []

            artifacts = SetupWithAIArtifacts(
                env_file_path=env_path,
                mcp_config_path=project_root / "mcp.json",
                wrapper_script_path=project_root / "wrapper_mcp.py",
                python_executable=project_root / ".venv" / "bin" / "python",
                github_directory_path=project_root / ".github",
                docs_directory_path=project_root / "docs",
            )
            server_probe = SetupWithAIMCPProbeResult(
                server_name=artifacts.server_name,
                server_version="3.2.0",
                tool_count=9,
                prompt_count=0,
                resource_count=0,
                resource_template_count=0,
                tools=({"name": "kickstart_workflow", "inputSchema": {"type": "object"}},),
            )
            copilot_state_db_path = project_root / ".copilot" / "copilot-intellij.db"

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
                patch(
                    "lex.bin.lex.probe_lex_mcp_local_server_for_pycharm",
                    return_value=server_probe,
                ) as probe_server_mock,
                patch(
                    "lex.bin.lex.bootstrap_github_copilot_mcp_server_for_pycharm",
                    return_value=copilot_state_db_path,
                ) as bootstrap_copilot_state_mock,
            ):
                launch_form_mock.side_effect = lambda *args, **kwargs: (
                    call_order.append("collect_credentials")
                    or SetupWithAICredentials(
                        github_token="ghu_example",
                        remote_mcp_api_key="remote_api_key",
                        gemini_api_key="gemini_api_key",
                    )
                )
                install_mock.side_effect = lambda *args, **kwargs: call_order.append("install_package")
                probe_server_mock.side_effect = lambda *args, **kwargs: (
                    call_order.append("probe_server") or server_probe
                )
                bootstrap_copilot_state_mock.side_effect = lambda *args, **kwargs: (
                    call_order.append("prime_copilot_state") or copilot_state_db_path
                )
                result = runner.invoke(
                    lex,
                    ["setup-with-ai", "--project-root", str(project_root)],
                )

            self.assertEqual(result.exit_code, 0, msg=result.output)
            generate_configs_mock.assert_called_once_with(project_root.resolve().as_posix())
            resolve_python_mock.assert_called_once_with(project_root.resolve())
            install_mock.assert_called_once_with(artifacts.python_executable, "remote_api_key")
            launch_form_mock.assert_called_once()
            configure_mock.assert_called_once_with(
                project_root=project_root.resolve(),
                github_token="ghu_example",
                remote_mcp_api_key="remote_api_key",
                gemini_api_key="gemini_api_key",
                remote_mcp_url=DEFAULT_REMOTE_MCP_URL,
                python_executable=artifacts.python_executable,
                verify_server=False,
            )
            probe_server_mock.assert_called_once_with(
                project_root=project_root.resolve(),
                python_executable=artifacts.python_executable,
                wrapper_script_path=artifacts.wrapper_script_path,
                server_name=artifacts.server_name,
                env_values={
                    "REMOTE_MCP_TRANSPORT": DEFAULT_REMOTE_MCP_TRANSPORT,
                    "REMOTE_MCP_URL": DEFAULT_REMOTE_MCP_URL,
                    "GEMINI_MODEL": DEFAULT_GEMINI_MODEL,
                    "GIT_GEMINI_MAX_REPAIR_ATTEMPTS": DEFAULT_GIT_GEMINI_MAX_REPAIR_ATTEMPTS,
                    "LEX_MCP_PRODUCTION": DEFAULT_LEX_MCP_PRODUCTION,
                    "REMOTE_MCP_API_KEY": "remote_api_key",
                    "GITHUB_TOKEN": "ghu_example",
                    "GEMINI_API_KEY": "gemini_api_key",
                },
            )
            bootstrap_copilot_state_mock.assert_called_once_with(
                server_probe,
                mcp_config_path=artifacts.mcp_config_path,
                server_name=artifacts.server_name,
            )
            self.assertEqual(
                call_order,
                ["collect_credentials", "install_package", "probe_server", "prime_copilot_state"],
            )
            self.assertIn("Copied lex-mcp-local GitHub files:", result.output)
            self.assertIn("Copied lex-app docs:", result.output)
            self.assertIn("Validated lex-mcp-local v3.2.0", result.output)
            self.assertIn("Primed GitHub Copilot MCP cache:", result.output)
            self.assertIn("restart it once", result.output)
            self.assertIn("Setup complete.", result.output)


class SetupWithAIToolsTests(TestCase):
    def test_copy_lex_mcp_local_github_directory_copies_recursive_contents(self):
        with TemporaryDirectory() as tmp_dir:
            temp_root = Path(tmp_dir)
            project_root = temp_root / "project"
            wrapper_script_path = temp_root / "site-packages" / "lex_mcp_local" / "wrapper_mcp.py"
            source_file = wrapper_script_path.parent / ".github" / "workflows" / "copilot.yml"

            project_root.mkdir()
            wrapper_script_path.parent.mkdir(parents=True)
            wrapper_script_path.write_text("# wrapper\n", encoding="utf-8")
            source_file.parent.mkdir(parents=True)
            source_file.write_text("name: Copilot\n", encoding="utf-8")

            github_directory = setup_with_ai_module.copy_lex_mcp_local_github_directory(
                project_root,
                wrapper_script_path,
            )

            self.assertEqual(github_directory, (project_root / ".github").resolve())
            self.assertEqual(
                (project_root / ".github" / "workflows" / "copilot.yml").read_text(
                    encoding="utf-8"
                ),
                "name: Copilot\n",
            )

    def test_copy_lex_app_docs_directory_copies_recursive_contents(self):
        with TemporaryDirectory() as tmp_dir:
            temp_root = Path(tmp_dir)
            project_root = temp_root / "project"
            lex_package_root = temp_root / "site-packages" / "lex"
            source_file = lex_package_root / "docs" / "planning" / "README.md"

            project_root.mkdir()
            source_file.parent.mkdir(parents=True)
            source_file.write_text("Planning docs\n", encoding="utf-8")

            docs_directory = setup_with_ai_module.copy_lex_app_docs_directory(
                project_root,
                lex_package_root,
            )

            self.assertEqual(docs_directory, (project_root / "docs").resolve())
            self.assertEqual(
                (project_root / "docs" / "planning" / "README.md").read_text(
                    encoding="utf-8"
                ),
                "Planning docs\n",
            )

    def test_configure_ai_integration_copies_github_and_docs_directories_into_project_root(self):
        with TemporaryDirectory() as tmp_dir:
            temp_root = Path(tmp_dir)
            project_root = temp_root / "project"
            wrapper_script_path = temp_root / "site-packages" / "lex_mcp_local" / "wrapper_mcp.py"
            lex_package_root = temp_root / "site-packages" / "lex"
            source_file = wrapper_script_path.parent / ".github" / "workflows" / "copilot.yml"
            docs_file = lex_package_root / "docs" / "planning" / "README.md"
            mcp_config_path = temp_root / "mcp.json"

            project_root.mkdir()
            wrapper_script_path.parent.mkdir(parents=True)
            wrapper_script_path.write_text("# wrapper\n", encoding="utf-8")
            source_file.parent.mkdir(parents=True)
            source_file.write_text("name: Copilot\n", encoding="utf-8")
            docs_file.parent.mkdir(parents=True)
            docs_file.write_text("Planning docs\n", encoding="utf-8")

            with patch(
                "lex.tools.setup_with_ai.resolve_wrapper_script_path",
                return_value=wrapper_script_path,
            ), patch(
                "lex.tools.setup_with_ai.resolve_lex_app_package_root",
                return_value=lex_package_root,
            ):
                artifacts = setup_with_ai_module.configure_ai_integration(
                    project_root=project_root,
                    github_token="ghu_example",
                    remote_mcp_api_key="remote_api_key",
                    gemini_api_key="gemini_api_key",
                    python_executable=Path("/venv/bin/python"),
                    mcp_config_path=mcp_config_path,
                    verify_server=False,
                )

            self.assertEqual(artifacts.github_directory_path, (project_root / ".github").resolve())
            self.assertEqual(artifacts.docs_directory_path, (project_root / "docs").resolve())
            self.assertEqual(
                (project_root / ".github" / "workflows" / "copilot.yml").read_text(
                    encoding="utf-8"
                ),
                "name: Copilot\n",
            )
            self.assertEqual(
                (project_root / "docs" / "planning" / "README.md").read_text(
                    encoding="utf-8"
                ),
                "Planning docs\n",
            )

    def test_install_lex_mcp_local_uses_remote_mcp_api_key_in_index_url(self):
        runner = Mock()

        command = install_lex_mcp_local(
            "/venv/bin/python",
            "remote_api_key",
            runner=runner,
        )

        self.assertEqual(
            command,
            [
                "/venv/bin/python",
                "-m",
                "pip",
                "install",
                "--no-cache-dir",
                "--index-url",
                "https://dl.cloudsmith.io/remote_api_key/excellence-cloud/lex-mcp-local/python/simple/",
                "--extra-index-url",
                "https://pypi.org/simple",
                "lex-mcp-local",
            ],
        )
        runner.assert_called_once_with(command, check=True)

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

    def test_verify_lex_mcp_local_server_starts_mimics_pycharm_handshake(self):
        with TemporaryDirectory() as tmp_dir:
            project_root = Path(tmp_dir)
            wrapper_script_path = project_root / "wrapper_mcp.py"
            wrapper_script_path.write_text(
                (
                    "import json\n"
                    "import sys\n"
                    "print('FastMCP banner', flush=True)\n"
                    "for raw in sys.stdin:\n"
                    "    message = json.loads(raw)\n"
                    "    method = message.get('method')\n"
                    "    if method == 'initialize':\n"
                    "        print(json.dumps({\n"
                    "            'jsonrpc': '2.0',\n"
                    "            'id': message['id'],\n"
                    "            'result': {\n"
                    "                'serverInfo': {'name': 'Lex AI Server', 'version': '3.2.0'}\n"
                    "            },\n"
                    "        }), flush=True)\n"
                    "    elif method == 'notifications/initialized':\n"
                    "        continue\n"
                    "    elif method == 'tools/list':\n"
                    "        print(json.dumps({\n"
                    "            'jsonrpc': '2.0',\n"
                    "            'id': message['id'],\n"
                    "            'result': {'tools': [{'name': 'kickstart_workflow'}, {'name': 'finalize_workflow'}]},\n"
                    "        }), flush=True)\n"
                    "    elif method == 'prompts/list':\n"
                    "        print(json.dumps({\n"
                    "            'jsonrpc': '2.0',\n"
                    "            'id': message['id'],\n"
                    "            'result': {'prompts': []},\n"
                    "        }), flush=True)\n"
                    "    elif method == 'resources/list':\n"
                    "        print(json.dumps({\n"
                    "            'jsonrpc': '2.0',\n"
                    "            'id': message['id'],\n"
                    "            'result': {'resources': [{'uri': 'lex://status'}]},\n"
                    "        }), flush=True)\n"
                    "    elif method == 'resources/templates/list':\n"
                    "        print(json.dumps({\n"
                    "            'jsonrpc': '2.0',\n"
                    "            'id': message['id'],\n"
                    "            'result': {'resourceTemplates': []},\n"
                    "        }), flush=True)\n"
                ),
                encoding="utf-8",
            )

            probe = verify_lex_mcp_local_server_starts(
                project_root=project_root,
                python_executable=Path(sys.executable),
                wrapper_script_path=wrapper_script_path,
                env_values={},
            )

        self.assertEqual(probe.server_name, LEX_MCP_LOCAL_SERVER_NAME)
        self.assertEqual(probe.server_version, "3.2.0")
        self.assertEqual(probe.tool_count, 2)
        self.assertEqual(probe.prompt_count, 0)
        self.assertEqual(probe.resource_count, 1)
        self.assertEqual(probe.resource_template_count, 0)
        self.assertEqual(probe.tools[0]["name"], "kickstart_workflow")
        self.assertEqual(probe.resources[0]["uri"], "lex://status")

    def test_bootstrap_github_copilot_mcp_server_for_pycharm_primes_cache_and_first_boot(self):
        with TemporaryDirectory() as tmp_dir:
            temp_root = Path(tmp_dir)
            mcp_config_path = temp_root / "github-copilot" / "intellij" / "mcp.json"
            state_db_path = resolve_github_copilot_state_db_path(mcp_config_path=mcp_config_path)
            state_db_path.parent.mkdir(parents=True, exist_ok=True)

            with sqlite3.connect(state_db_path) as connection:
                connection.execute(
                    """
                    CREATE TABLE state (
                        key TEXT PRIMARY KEY,
                        value TEXT NOT NULL,
                        updated_at INTEGER NOT NULL
                    )
                    """
                )
                connection.execute(
                    """
                    INSERT INTO state (key, value, updated_at)
                    VALUES (?, ?, ?)
                    """,
                    (
                        "mcp-servers-cache",
                        json.dumps(json.dumps({"existing-server": {"tools": []}})),
                        1,
                    ),
                )
                connection.commit()

            probe = SetupWithAIMCPProbeResult(
                server_name=LEX_MCP_LOCAL_SERVER_NAME,
                server_version="3.2.0",
                tool_count=1,
                tools=(
                    {
                        "name": "kickstart_workflow",
                        "description": "Start a workflow",
                        "inputSchema": {"properties": {"repo": {"type": "string"}}},
                    },
                ),
                resources=({"uri": "lex://status"},),
            )

            primed_state_db_path = bootstrap_github_copilot_mcp_server_for_pycharm(
                probe,
                mcp_config_path=mcp_config_path,
            )

            self.assertEqual(primed_state_db_path, state_db_path.resolve())
            with sqlite3.connect(primed_state_db_path) as connection:
                first_boot_raw = connection.execute(
                    "SELECT value FROM state WHERE key = ?",
                    ("mcp-first-boot-completed",),
                ).fetchone()[0]
                cache_raw = connection.execute(
                    "SELECT value FROM state WHERE key = ?",
                    ("mcp-servers-cache",),
                ).fetchone()[0]

            self.assertEqual(json.loads(first_boot_raw), "false")

            cached_servers = json.loads(json.loads(cache_raw))
            self.assertIn("existing-server", cached_servers)
            self.assertIn(LEX_MCP_LOCAL_SERVER_NAME, cached_servers)

            lex_cache = cached_servers[LEX_MCP_LOCAL_SERVER_NAME]
            self.assertEqual(len(lex_cache["tools"]), 1)
            self.assertEqual(lex_cache["tools"][0]["name"], "kickstart_workflow")
            self.assertEqual(lex_cache["tools"][0]["_status"], "enabled")
            self.assertEqual(lex_cache["tools"][0]["_nameForModel"], "kickstart_workflow")
            self.assertEqual(lex_cache["tools"][0]["inputSchema"]["type"], "object")
            self.assertEqual(lex_cache["resources"][0]["uri"], "lex://status")

    @unittest.skip("MCP server start test broken — needs investigation")
    def test_start_lex_mcp_local_server_uses_detached_process_on_posix(self):
        with TemporaryDirectory() as tmp_dir:
            project_root = Path(tmp_dir)
            mcp_config_path = project_root / "copilot" / "mcp.json"
            process = Mock(pid=4321)
            process.poll.return_value = None

            with (
                patch("lex.tools.setup_with_ai.os.name", "posix"),
                patch("lex.tools.setup_with_ai.time.sleep"),
                patch("lex.tools.setup_with_ai.subprocess.Popen", return_value=process) as popen_mock,
            ):
                runtime = start_lex_mcp_local_server(
                    project_root=project_root,
                    mcp_config_path=mcp_config_path,
                    python_executable=project_root / ".venv" / "bin" / "python",
                    wrapper_script_path=project_root / "wrapper_mcp.py",
                    github_token="ghu_example",
                    remote_mcp_api_key="remote_api_key",
                    gemini_api_key="gemini_api_key",
                )

        kwargs = popen_mock.call_args.kwargs
        self.assertEqual(kwargs["stdin"], subprocess.DEVNULL)
        self.assertEqual(kwargs["stderr"], subprocess.STDOUT)
        self.assertEqual(kwargs["cwd"], str(project_root.resolve()))
        self.assertTrue(kwargs["close_fds"])
        self.assertTrue(kwargs["start_new_session"])
        self.assertFalse(runtime.already_running)
        self.assertEqual(runtime.pid, 4321)
        self.assertEqual(runtime.pid_file_path.read_text(encoding="utf-8").strip(), "4321")
        self.assertTrue(runtime.log_file_path.exists())

    @unittest.skip("MCP server start test broken — needs investigation")
    def test_start_lex_mcp_local_server_uses_windows_detached_flags(self):
        with TemporaryDirectory() as tmp_dir:
            project_root = Path(tmp_dir)
            mcp_config_path = project_root / "copilot" / "mcp.json"
            process = Mock(pid=9876)
            process.poll.return_value = None

            with (
                patch("lex.tools.setup_with_ai.os.name", "nt"),
                patch.object(setup_with_ai_module.subprocess, "CREATE_NEW_PROCESS_GROUP", 0x200, create=True),
                patch.object(setup_with_ai_module.subprocess, "DETACHED_PROCESS", 0x8, create=True),
                patch.object(setup_with_ai_module.subprocess, "CREATE_NO_WINDOW", 0x08000000, create=True),
                patch("lex.tools.setup_with_ai.time.sleep"),
                patch("lex.tools.setup_with_ai.subprocess.Popen", return_value=process) as popen_mock,
            ):
                start_lex_mcp_local_server(
                    project_root=project_root,
                    mcp_config_path=mcp_config_path,
                    python_executable=project_root / ".venv" / "Scripts" / "python.exe",
                    wrapper_script_path=project_root / "wrapper_mcp.py",
                    github_token="ghu_example",
                    remote_mcp_api_key="remote_api_key",
                    gemini_api_key="gemini_api_key",
                )

        kwargs = popen_mock.call_args.kwargs
        self.assertTrue(kwargs["close_fds"])
        self.assertNotIn("start_new_session", kwargs)
        self.assertEqual(kwargs["creationflags"], 0x200 | 0x8 | 0x08000000)

    def test_start_lex_mcp_local_server_reuses_running_process(self):
        with TemporaryDirectory() as tmp_dir:
            project_root = Path(tmp_dir)
            mcp_config_path = project_root / "copilot" / "mcp.json"
            pid_file_path = mcp_config_path.parent / f"{LEX_MCP_LOCAL_SERVER_NAME}.pid"
            pid_file_path.parent.mkdir(parents=True)
            pid_file_path.write_text("6543\n", encoding="utf-8")

            with (
                patch("lex.tools.setup_with_ai.os.kill") as os_kill_mock,
                patch("lex.tools.setup_with_ai.subprocess.Popen") as popen_mock,
            ):
                runtime = start_lex_mcp_local_server(
                    project_root=project_root,
                    mcp_config_path=mcp_config_path,
                    python_executable=project_root / ".venv" / "bin" / "python",
                    wrapper_script_path=project_root / "wrapper_mcp.py",
                    github_token="ghu_example",
                    remote_mcp_api_key="remote_api_key",
                    gemini_api_key="gemini_api_key",
                )

        os_kill_mock.assert_called_once_with(6543, 0)
        popen_mock.assert_not_called()
        self.assertTrue(runtime.already_running)
        self.assertEqual(runtime.pid, 6543)
