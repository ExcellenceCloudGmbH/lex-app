"""Cluster 1y: IDE-aware ``lex setup`` run configuration generation.

Intent
------
New projects must receive runnable IDE configurations without requiring users
to translate the framework's PyCharm commands by hand.  When setup has a clear
IDE marker it should generate that IDE's format; when the process environment
cannot identify one unambiguously it must generate both.  The VS Code launch
entries must preserve every command, argument, environment variable, working
directory, ``.env`` load, and interactive worker-count prompt exposed by the
PyCharm configurations.

Scenarios: 1.203–1.210.
"""

from __future__ import annotations

import json
import os
import shlex
import tempfile
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch
from xml.etree import ElementTree

from click.testing import CliRunner

import pytest

pytestmark = pytest.mark.init


_IDE_MARKER_NAMES = {
    "IDEA_INITIAL_DIRECTORY",
    "PYCHARM_HOSTED",
    "TERMINAL_EMULATOR",
    "TERM_PROGRAM",
    "VSCODE_CWD",
    "VSCODE_INJECTION",
    "VSCODE_IPC_HOOK",
    "VSCODE_IPC_HOOK_CLI",
    "VSCODE_PID",
}

_EXPECTED_CONFIG_NAMES = {
    "Init",
    "Setup With AI",
    "Start",
    "Flower",
    "Celery Workers",
    "Make migrations",
    "Migrate",
    "Streamlit",
    "Create DB",
    "Flush DB",
}


class TestCluster01y_IdeRunConfigurations(TestCase):
    """``lex setup`` selects an IDE safely and emits equivalent launchers."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        self.project_root = Path(self._tmpdir.name).resolve()

        project_root_patcher = patch(
            "lex.bin.lex.find_project_root",
            return_value=self.project_root.as_posix(),
        )
        project_root_patcher.start()
        self.addCleanup(project_root_patcher.stop)

    def _invoke_setup(self, markers: dict[str, str]) -> object:
        """Invoke setup with host IDE markers removed and explicit markers set."""
        from lex.bin.lex import setup

        controlled_environment = dict(os.environ)
        for name in _IDE_MARKER_NAMES:
            controlled_environment.pop(name, None)
        controlled_environment.update(markers)

        with patch.dict(os.environ, controlled_environment, clear=True):
            return CliRunner().invoke(setup, [], catch_exceptions=False)

    def _read_launch_config(self) -> dict:
        return json.loads(
            (self.project_root / ".vscode" / "launch.json").read_text(
                encoding="utf-8"
            )
        )

    # -- 1.203 ---------------------------------------------------------
    def test_1_203_vscode_marker_generates_vscode_only(self) -> None:
        """
        Scenario 1.203: VS Code integrated-terminal detection.

        Given: Setup runs with VS Code's TERM_PROGRAM marker.
        When:  The user invokes ``lex setup``.
        Then:  VS Code launch configurations are generated without a new
               PyCharm ``.run`` directory.
        """
        result = self._invoke_setup({"TERM_PROGRAM": "vscode"})

        self.assertEqual(
            result.exit_code,
            0,
            f"VS Code setup must exit cleanly; output={result.output!r}",
        )
        self.assertTrue(
            (self.project_root / ".vscode" / "launch.json").is_file(),
            "A detected VS Code session must receive .vscode/launch.json",
        )
        self.assertFalse(
            (self.project_root / ".run").exists(),
            "A clearly detected VS Code session must not create PyCharm files",
        )
        self.assertIn(
            ".vscode/launch.json:",
            result.output,
            "Setup output must report the generated VS Code launch file",
        )

    # -- 1.204 ---------------------------------------------------------
    def test_1_204_pycharm_marker_generates_pycharm_only(self) -> None:
        """
        Scenario 1.204: PyCharm integrated-terminal detection.

        Given: Setup runs in a JetBrains terminal.
        When:  The user invokes ``lex setup``.
        Then:  PyCharm run files are generated without a new VS Code file.
        """
        result = self._invoke_setup(
            {"TERMINAL_EMULATOR": "JetBrains-JediTerm"}
        )

        self.assertEqual(
            result.exit_code,
            0,
            f"PyCharm setup must exit cleanly; output={result.output!r}",
        )
        self.assertTrue(
            (self.project_root / ".run" / "Start.run.xml").is_file(),
            "A detected PyCharm session must receive .run configurations",
        )
        self.assertFalse(
            (self.project_root / ".vscode").exists(),
            "A clearly detected PyCharm session must not create VS Code files",
        )
        self.assertIn(
            ".run:",
            result.output,
            "Setup output must report the generated PyCharm directory",
        )

    # -- 1.205 ---------------------------------------------------------
    def test_1_205_unknown_ide_generates_both_formats(self) -> None:
        """
        Scenario 1.205: No reliable IDE marker.

        Given: Setup runs from a neutral shell with no IDE environment marker.
        When:  The user invokes ``lex setup``.
        Then:  Both PyCharm and VS Code configurations are generated.
        """
        result = self._invoke_setup({})

        self.assertEqual(
            result.exit_code,
            0,
            f"Neutral-shell setup must exit cleanly; output={result.output!r}",
        )
        self.assertTrue(
            (self.project_root / ".run" / "Start.run.xml").is_file(),
            "Unknown IDE setup must include the PyCharm fallback",
        )
        self.assertTrue(
            (self.project_root / ".vscode" / "launch.json").is_file(),
            "Unknown IDE setup must include the VS Code fallback",
        )
        self.assertIn(
            "could not be determined unambiguously",
            result.output,
            "Setup must explain why it generated both IDE formats",
        )

    # -- 1.206 ---------------------------------------------------------
    def test_1_206_conflicting_ide_markers_generate_both_formats(self) -> None:
        """
        Scenario 1.206: Conflicting inherited IDE markers.

        Given: The setup process contains both VS Code and JetBrains markers.
        When:  The user invokes ``lex setup``.
        Then:  Detection does not guess; both formats are generated.
        """
        result = self._invoke_setup(
            {
                "TERM_PROGRAM": "vscode",
                "TERMINAL_EMULATOR": "JetBrains-JediTerm",
            }
        )

        self.assertEqual(
            result.exit_code,
            0,
            f"Conflicting-marker setup must exit cleanly; output={result.output!r}",
        )
        self.assertTrue(
            (self.project_root / ".run" / "Start.run.xml").is_file(),
            "Conflicting IDE markers must retain PyCharm compatibility",
        )
        self.assertTrue(
            (self.project_root / ".vscode" / "launch.json").is_file(),
            "Conflicting IDE markers must retain VS Code compatibility",
        )

    # -- 1.207 ---------------------------------------------------------
    def test_1_207_vscode_launchers_match_every_pycharm_command(self) -> None:
        """
        Scenario 1.207: Cross-IDE run-configuration parity.

        Given: Neutral-shell setup has generated both IDE formats.
        When:  The generated PyCharm XML and VS Code launch JSON are compared.
        Then:  Every launcher has the same module command, arguments,
               environment, cwd, .env behavior, and worker-count prompt.
        """
        result = self._invoke_setup({})
        self.assertEqual(
            result.exit_code,
            0,
            f"Parity scaffold must succeed; output={result.output!r}",
        )

        pycharm_configs = {}
        for run_file in (self.project_root / ".run").glob("*.run.xml"):
            root = ElementTree.parse(run_file).getroot()
            configuration = root.find(".//configuration")
            self.assertIsNotNone(
                configuration,
                f"{run_file.name} must contain a configuration element",
            )
            options = {
                option.attrib["name"]: option.attrib.get("value", "")
                for option in configuration.findall("option")
            }
            envs = {
                env.attrib["name"]: env.attrib["value"]
                for env in configuration.findall("./envs/env")
            }
            pycharm_configs[configuration.attrib["name"]] = {
                "options": options,
                "env": envs,
            }

        launch_config = self._read_launch_config()
        vscode_configs = {
            config["name"].removeprefix("LEX: "): config
            for config in launch_config["configurations"]
            if config["name"].startswith("LEX: ")
        }

        self.assertEqual(
            set(pycharm_configs),
            _EXPECTED_CONFIG_NAMES,
            "PyCharm must expose the complete documented setup command set",
        )
        self.assertEqual(
            set(vscode_configs),
            _EXPECTED_CONFIG_NAMES,
            "VS Code must expose exactly the same setup command set",
        )

        for name in sorted(_EXPECTED_CONFIG_NAMES):
            with self.subTest(configuration=name):
                pycharm = pycharm_configs[name]
                vscode = vscode_configs[name]
                parameters = pycharm["options"]["PARAMETERS"]
                expected_args = (
                    ["celery-workers", "${input:lexWorkerCount}"]
                    if name == "Celery Workers"
                    else shlex.split(parameters)
                )

                self.assertEqual(
                    pycharm["options"]["SCRIPT_NAME"],
                    "lex",
                    f"{name}: PyCharm must launch the lex module",
                )
                self.assertEqual(
                    vscode["module"],
                    "lex",
                    f"{name}: VS Code must launch the same lex module",
                )
                self.assertEqual(
                    vscode["args"],
                    expected_args,
                    f"{name}: VS Code arguments must match PyCharm parameters",
                )
                self.assertEqual(
                    vscode["env"],
                    pycharm["env"],
                    f"{name}: per-command environment variables must match",
                )
                self.assertEqual(
                    pycharm["options"]["WORKING_DIRECTORY"],
                    self.project_root.as_posix(),
                    f"{name}: PyCharm cwd must be the project root",
                )
                self.assertEqual(
                    vscode["cwd"],
                    "${workspaceFolder}",
                    f"{name}: VS Code cwd must resolve to the project root",
                )
                self.assertEqual(
                    pycharm["options"]["ENV_FILES"],
                    (self.project_root / ".env").as_posix(),
                    f"{name}: PyCharm must load the generated .env",
                )
                self.assertEqual(
                    vscode["envFile"],
                    "${workspaceFolder}/.env",
                    f"{name}: VS Code must load the generated .env",
                )
                self.assertEqual(
                    vscode["type"],
                    "debugpy",
                    f"{name}: VS Code must use the supported Python debugger type",
                )
                self.assertEqual(
                    vscode["console"],
                    "integratedTerminal",
                    f"{name}: interactive commands must run in a terminal",
                )

        worker_inputs = {
            item["id"]: item for item in launch_config.get("inputs", [])
        }
        self.assertEqual(
            worker_inputs["lexWorkerCount"]["type"],
            "promptString",
            "Celery Workers must ask for its worker count in VS Code",
        )
        self.assertEqual(
            worker_inputs["lexWorkerCount"]["default"],
            "1",
            "VS Code's worker-count prompt must retain PyCharm's default of 1",
        )

    # -- 1.208 ---------------------------------------------------------
    def test_1_208_vscode_merge_preserves_user_jsonc_entries(self) -> None:
        """
        Scenario 1.208: Existing user launch configuration.

        Given: A JSONC launch file contains a custom configuration and input.
        When:  VS Code setup regenerates the LEX entries.
        Then:  User-owned entries remain unchanged and LEX entries are added.
        """
        vscode_dir = self.project_root / ".vscode"
        vscode_dir.mkdir()
        launch_path = vscode_dir / "launch.json"
        launch_path.write_text(
            """{
  // This user configuration must survive setup.
  "version": "0.2.0",
  "configurations": [
    {
      "name": "LEX: Custom tool",
      "type": "debugpy",
      "request": "launch",
      "module": "custom_tool",
    },
  ],
  "inputs": [
    {
      "id": "customInput",
      "type": "promptString",
      "description": "Custom value",
    },
  ],
}
""",
            encoding="utf-8",
        )

        result = self._invoke_setup({"TERM_PROGRAM": "vscode"})
        self.assertEqual(
            result.exit_code,
            0,
            f"JSONC merge must succeed; output={result.output!r}",
        )

        launch_config = self._read_launch_config()
        custom_configs = [
            config
            for config in launch_config["configurations"]
            if config.get("name") == "LEX: Custom tool"
        ]
        generated_configs = [
            config
            for config in launch_config["configurations"]
            if config.get("name")
            in {f"LEX: {name}" for name in _EXPECTED_CONFIG_NAMES}
        ]
        input_ids = {item["id"] for item in launch_config["inputs"]}

        self.assertEqual(
            custom_configs,
            [
                {
                    "name": "LEX: Custom tool",
                    "type": "debugpy",
                    "request": "launch",
                    "module": "custom_tool",
                }
            ],
            "Setup must preserve an existing user launch configuration",
        )
        self.assertEqual(
            len(generated_configs),
            len(_EXPECTED_CONFIG_NAMES),
            "Setup must merge the complete LEX launcher set",
        )
        self.assertEqual(
            input_ids,
            {"customInput", "lexWorkerCount"},
            "Setup must preserve user inputs while adding the LEX prompt",
        )

    # -- 1.209 ---------------------------------------------------------
    def test_1_209_vscode_generation_is_content_idempotent(self) -> None:
        """
        Scenario 1.209: Repeated VS Code setup.

        Given: VS Code launch configurations already came from ``lex setup``.
        When:  The user runs the same setup command again.
        Then:  The launch file is byte-identical with no duplicate entries.
        """
        first_result = self._invoke_setup({"TERM_PROGRAM": "vscode"})
        self.assertEqual(
            first_result.exit_code,
            0,
            f"Initial VS Code setup must succeed; output={first_result.output!r}",
        )
        launch_path = self.project_root / ".vscode" / "launch.json"
        first_content = launch_path.read_text(encoding="utf-8")

        second_result = self._invoke_setup({"TERM_PROGRAM": "vscode"})
        self.assertEqual(
            second_result.exit_code,
            0,
            f"Repeated VS Code setup must succeed; output={second_result.output!r}",
        )
        second_content = launch_path.read_text(encoding="utf-8")
        launch_config = json.loads(second_content)

        self.assertEqual(
            second_content,
            first_content,
            "Repeated setup must produce a byte-identical VS Code launch file",
        )
        self.assertEqual(
            len(launch_config["configurations"]),
            len(_EXPECTED_CONFIG_NAMES),
            "Repeated setup must not duplicate generated launch entries",
        )
        self.assertEqual(
            len(launch_config["inputs"]),
            1,
            "Repeated setup must not duplicate the worker-count prompt",
        )

    # -- 1.210 ---------------------------------------------------------
    def test_1_210_standalone_generator_uses_auto_detection_and_exits_cleanly(
        self,
    ) -> None:
        """
        Scenario 1.210: Standalone configuration generator.

        Given: The console-script wrapper runs without an IDE marker.
        When:  ``lex-generate-configs`` delegates to auto generation.
        Then:  Both formats are written and the wrapper returns success.
        """
        from generate_pycharm_configs import generate_run_configs_cli

        controlled_environment = dict(os.environ)
        for name in _IDE_MARKER_NAMES:
            controlled_environment.pop(name, None)

        with (
            patch.dict(os.environ, controlled_environment, clear=True),
            patch(
                "generate_pycharm_configs._resolve_project_root",
                return_value=self.project_root,
            ),
        ):
            result = generate_run_configs_cli()

        self.assertIsNone(
            result,
            "The console-script wrapper must return None so setuptools exits 0",
        )
        self.assertTrue(
            (self.project_root / ".run" / "Start.run.xml").is_file(),
            "The standalone unknown-IDE fallback must generate PyCharm files",
        )
        self.assertTrue(
            (self.project_root / ".vscode" / "launch.json").is_file(),
            "The standalone unknown-IDE fallback must generate VS Code files",
        )
