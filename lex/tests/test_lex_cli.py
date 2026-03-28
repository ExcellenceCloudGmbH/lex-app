from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock
from unittest.mock import patch

from click.testing import CliRunner

from generate_pycharm_configs import (
    CELERY_WORKER_COUNT_PROMPT,
    _build_celery_workers_parameters,
    generate_pycharm_configs,
)
from lex.bin.lex import build_celery_worker_command, build_flower_command, lex


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

    def test_build_celery_worker_command_skips_threads_pool_off_macos(self):
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
