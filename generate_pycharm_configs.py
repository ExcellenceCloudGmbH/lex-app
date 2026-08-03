#!/usr/bin/env python3
"""Generate IDE run configurations for LEX projects.

The module name is retained for compatibility with existing imports.  The
``generate_run_configs`` entry point now selects PyCharm or VS Code when the
calling IDE can be identified and safely falls back to generating both.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from xml.sax.saxutils import escape

from lex.tools.project_root import find_project_root  # shared utility


DEFAULT_CONFIG_ENVS = {"PYTHONUNBUFFERED": "1"}
CELERY_WORKER_COUNT_PROMPT = "$Prompt:Worker count:1$"
VSCODE_WORKER_COUNT_INPUT = "${input:lexWorkerCount}"
VSCODE_CONFIG_PREFIX = "LEX: "

PYCHARM_IDE = "pycharm"
VSCODE_IDE = "vscode"

RUN_CONFIGS = (
    {
        "pycharm_filename": "Init.run.xml",
        "name": "Init",
        "args": ("init",),
    },
    {
        "pycharm_filename": "Setup_With_AI.run.xml",
        "name": "Setup With AI",
        "args": ("setup-with-ai",),
    },
    {
        "pycharm_filename": "Start.run.xml",
        "name": "Start",
        "args": (
            "start",
            "--reload",
            "--loop",
            "asyncio",
            "lex_app.asgi:application",
        ),
    },
    {
        "pycharm_filename": "Flower.run.xml",
        "name": "Flower",
        "args": ("flower",),
    },
    {
        "pycharm_filename": "Celery_Worker.run.xml",
        "name": "Celery Workers",
        "args": ("celery-workers", CELERY_WORKER_COUNT_PROMPT),
        "envs": {
            "IS_RUNNING_IN_CELERY": "true",
            "CELERY_ACTIVE": "true",
        },
    },
    {
        "pycharm_filename": "Make_migrations.run.xml",
        "name": "Make migrations",
        "args": ("makemigrations",),
    },
    {
        "pycharm_filename": "Migrate.run.xml",
        "name": "Migrate",
        "args": ("migrate",),
    },
    {
        "pycharm_filename": "Streamlit.run.xml",
        "name": "Streamlit",
        "args": ("streamlit", "run", "streamlit_app.py"),
    },
    {
        "pycharm_filename": "Create_DB.run.xml",
        "name": "Create DB",
        "args": ("create_db",),
    },
    {
        "pycharm_filename": "Flush_DB.run.xml",
        "name": "Flush DB",
        "args": ("flush",),
    },
)


def _resolve_project_root(project_root=None) -> Path:
    """Resolve a caller-supplied path or the execution directory."""
    start = project_root or os.getcwd()
    return Path(find_project_root(start)).resolve()


def _render_envs(envs):
    return "\n".join(
        f'      <env name="{escape(name)}" value="{escape(value)}" />'
        for name, value in envs.items()
    )


def _build_celery_workers_parameters():
    return f"celery-workers {CELERY_WORKER_COUNT_PROMPT}"


def _pycharm_parameters(config) -> str:
    if config["name"] == "Celery Workers":
        return _build_celery_workers_parameters()
    return " ".join(config["args"])


def _vscode_args(config) -> list[str]:
    return [
        VSCODE_WORKER_COUNT_INPUT if arg == CELERY_WORKER_COUNT_PROMPT else arg
        for arg in config["args"]
    ]


def detect_ide(environ=None) -> str | None:
    """Return the IDE indicated by process environment markers.

    Integrated terminals expose useful markers, but there is no universal IDE
    signal.  Missing or conflicting markers intentionally return ``None`` so
    callers can generate configurations for both supported IDEs.
    """
    env = os.environ if environ is None else environ

    term_program = env.get("TERM_PROGRAM", "").strip().lower()
    vscode_detected = term_program == "vscode" or any(
        env.get(name)
        for name in (
            "VSCODE_CWD",
            "VSCODE_INJECTION",
            "VSCODE_IPC_HOOK",
            "VSCODE_IPC_HOOK_CLI",
            "VSCODE_PID",
        )
    )

    terminal_emulator = env.get("TERMINAL_EMULATOR", "").strip().lower()
    pycharm_detected = (
        bool(env.get("PYCHARM_HOSTED"))
        or "jetbrains" in terminal_emulator
        or bool(env.get("IDEA_INITIAL_DIRECTORY"))
    )

    if vscode_detected == pycharm_detected:
        return None
    return VSCODE_IDE if vscode_detected else PYCHARM_IDE


def generate_pycharm_configs(project_root=None):
    """Generate the legacy PyCharm ``.run/*.run.xml`` file set."""
    root = _resolve_project_root(project_root)
    runconfigs_dir = root / ".run"
    runconfigs_dir.mkdir(parents=True, exist_ok=True)

    project_name = root.name
    env_file_path = root / ".env"
    env_files_option = (
        f'<option name="ENV_FILES" value="{escape(str(env_file_path))}" />'
        if env_file_path.exists()
        else '<option name="ENV_FILES" value="" />'
    )

    print(f"Generating PyCharm run configurations in: {runconfigs_dir}")
    print(f"Project name: {project_name}")
    print(f"Project root: {root}")

    for config in RUN_CONFIGS:
        envs = {**DEFAULT_CONFIG_ENVS, **config.get("envs", {})}
        parameters = escape(_pycharm_parameters(config))
        content = f"""<component name="ProjectRunConfigurationManager">
  <configuration default="false" name="{escape(config['name'])}" type="PythonConfigurationType" factoryName="Python">
    <module name="{escape(project_name)}" />
    {env_files_option}
    <option name="INTERPRETER_OPTIONS" value="" />
    <option name="PARENT_ENVS" value="true" />
    <envs>
{_render_envs(envs)}
    </envs>
    <option name="SDK_HOME" value="" />
    <option name="WORKING_DIRECTORY" value="{escape(str(root))}" />
    <option name="IS_MODULE_SDK" value="true" />
    <option name="ADD_CONTENT_ROOTS" value="true" />
    <option name="ADD_SOURCE_ROOTS" value="true" />
    <EXTENSION ID="PythonCoverageRunConfigurationExtension" runner="coverage.py" />
    <option name="SCRIPT_NAME" value="lex" />
    <option name="PARAMETERS" value="{parameters}" />
    <option name="SHOW_COMMAND_LINE" value="false" />
    <option name="EMULATE_TERMINAL" value="false" />
    <option name="MODULE_MODE" value="true" />
    <option name="REDIRECT_INPUT" value="false" />
    <option name="INPUT_FILE" value="" />
    <method v="2" />
  </configuration>
</component>"""
        path = runconfigs_dir / config["pycharm_filename"]
        path.write_text(content, encoding="utf-8")
        print(f"[OK] Generated: {config['pycharm_filename']}")

    print("\nPyCharm run configurations generated successfully!")
    if env_file_path.exists():
        print(f"[OK] Configurations will use .env file: {env_file_path}")
    else:
        print(f"[WARN] No .env file found at {env_file_path}")
        print("  Create one if you need environment variables for your project.")


def _jsonc_to_json(source: str) -> str:
    """Remove JSON-with-comments syntax accepted by VS Code.

    VS Code permits line comments, block comments, and trailing commas in
    ``launch.json``.  Supporting those forms lets setup merge configurations
    without discarding an existing file merely because it is JSONC.
    """
    without_comments: list[str] = []
    index = 0
    in_string = False
    escaped = False

    while index < len(source):
        char = source[index]
        next_char = source[index + 1] if index + 1 < len(source) else ""

        if in_string:
            without_comments.append(char)
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            index += 1
            continue

        if char == '"':
            in_string = True
            without_comments.append(char)
            index += 1
            continue

        if char == "/" and next_char == "/":
            index += 2
            while index < len(source) and source[index] not in "\r\n":
                index += 1
            continue

        if char == "/" and next_char == "*":
            index += 2
            while index + 1 < len(source):
                if source[index] == "*" and source[index + 1] == "/":
                    index += 2
                    break
                if source[index] in "\r\n":
                    without_comments.append(source[index])
                index += 1
            continue

        without_comments.append(char)
        index += 1

    cleaned = "".join(without_comments)
    without_trailing_commas: list[str] = []
    index = 0
    in_string = False
    escaped = False

    while index < len(cleaned):
        char = cleaned[index]
        if in_string:
            without_trailing_commas.append(char)
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            index += 1
            continue

        if char == '"':
            in_string = True
            without_trailing_commas.append(char)
            index += 1
            continue

        if char == ",":
            lookahead = index + 1
            while lookahead < len(cleaned) and cleaned[lookahead].isspace():
                lookahead += 1
            if lookahead < len(cleaned) and cleaned[lookahead] in "]}":
                index += 1
                continue

        without_trailing_commas.append(char)
        index += 1

    return "".join(without_trailing_commas)


def _read_existing_launch_config(launch_path: Path) -> dict:
    if not launch_path.exists():
        return {"version": "0.2.0", "configurations": []}

    try:
        launch_config = json.loads(
            _jsonc_to_json(launch_path.read_text(encoding="utf-8"))
        )
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(
            f"Cannot update {launch_path}: the existing file is not valid "
            "JSON/JSONC. Fix it in VS Code and run setup again."
        ) from exc

    if not isinstance(launch_config, dict):
        raise ValueError(
            f"Cannot update {launch_path}: its top-level value must be an object."
        )
    if not isinstance(launch_config.get("configurations", []), list):
        raise ValueError(
            f"Cannot update {launch_path}: 'configurations' must be an array."
        )
    if not isinstance(launch_config.get("inputs", []), list):
        raise ValueError(
            f"Cannot update {launch_path}: 'inputs' must be an array."
        )
    return launch_config


def _build_vscode_config(config, env_file_exists: bool) -> dict:
    launch_config = {
        "name": f"{VSCODE_CONFIG_PREFIX}{config['name']}",
        "type": "debugpy",
        "request": "launch",
        "module": "lex",
        "args": _vscode_args(config),
        "cwd": "${workspaceFolder}",
        "console": "integratedTerminal",
        "env": {**DEFAULT_CONFIG_ENVS, **config.get("envs", {})},
    }
    if env_file_exists:
        launch_config["envFile"] = "${workspaceFolder}/.env"
    return launch_config


def generate_vscode_configs(project_root=None):
    """Generate or merge VS Code launch configurations."""
    root = _resolve_project_root(project_root)
    vscode_dir = root / ".vscode"
    vscode_dir.mkdir(parents=True, exist_ok=True)
    launch_path = vscode_dir / "launch.json"

    launch_config = _read_existing_launch_config(launch_path)
    generated_config_names = {
        f"{VSCODE_CONFIG_PREFIX}{config['name']}" for config in RUN_CONFIGS
    }
    existing_configs = [
        config
        for config in launch_config.get("configurations", [])
        if not (
            isinstance(config, dict)
            and config.get("name") in generated_config_names
        )
    ]
    generated_configs = [
        _build_vscode_config(config, (root / ".env").exists())
        for config in RUN_CONFIGS
    ]

    existing_inputs = [
        item
        for item in launch_config.get("inputs", [])
        if not (
            isinstance(item, dict)
            and item.get("id") == "lexWorkerCount"
        )
    ]

    launch_config.setdefault("version", "0.2.0")
    launch_config["configurations"] = existing_configs + generated_configs
    launch_config["inputs"] = existing_inputs + [
        {
            "id": "lexWorkerCount",
            "type": "promptString",
            "description": "Worker count",
            "default": "1",
        }
    ]

    launch_path.write_text(
        json.dumps(launch_config, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(f"Generating VS Code run configurations in: {launch_path}")
    for config in RUN_CONFIGS:
        print(f"[OK] Generated: {VSCODE_CONFIG_PREFIX}{config['name']}")
    print("\nVS Code run configurations generated successfully!")
    if (root / ".env").exists():
        print(f"[OK] Configurations will use .env file: {root / '.env'}")
    else:
        print(f"[WARN] No .env file found at {root / '.env'}")
        print("  Create one if you need environment variables for your project.")


def generate_run_configs(project_root=None, environ=None) -> tuple[str, ...]:
    """Generate run configurations for the detected IDE or both when unknown."""
    root = _resolve_project_root(project_root)
    detected_ide = detect_ide(environ)

    if detected_ide == PYCHARM_IDE:
        print("Detected PyCharm/JetBrains IDE environment.")
        generate_pycharm_configs(root)
        return (PYCHARM_IDE,)

    if detected_ide == VSCODE_IDE:
        print("Detected Visual Studio Code environment.")
        generate_vscode_configs(root)
        return (VSCODE_IDE,)

    print(
        "IDE environment could not be determined unambiguously; "
        "generating PyCharm and VS Code configurations."
    )
    generate_pycharm_configs(root)
    generate_vscode_configs(root)
    return (PYCHARM_IDE, VSCODE_IDE)


def generate_run_configs_cli() -> None:
    """Console-script wrapper that exits successfully after generation."""
    generate_run_configs()


if __name__ == "__main__":
    import argparse
    import sys

    parser = argparse.ArgumentParser(
        description="Generate IDE run configurations for lex-app projects"
    )
    parser.add_argument(
        "-p",
        "--project-root",
        help="Project root directory (default: execution directory)",
    )
    args = parser.parse_args()
    try:
        generate_run_configs(args.project_root)
    except Exception as exc:
        print(f"Error generating configurations: {exc}")
        sys.exit(1)
