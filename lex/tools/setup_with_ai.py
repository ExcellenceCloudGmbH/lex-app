from __future__ import annotations

import html
import json
import os
import queue
import re
import secrets
import shutil
import signal
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import webbrowser
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Mapping
from urllib.parse import parse_qs, quote, urlparse

from lex.tools._setup_ai_github import (
    DeviceCode,
    DeviceFlowError,
    DeviceFlowUnavailable,
    is_device_flow_available,
    poll_device_flow,
    start_device_flow,
)
from lex.tools._setup_ai_state import (
    SetupWithAILastUsed,
    load_last_used,
    save_last_used,
)
from lex.tools._setup_ai_validation import (
    validate_github_token,
    validate_remote_mcp_key,
)

DEFAULT_REMOTE_MCP_URL = "https://mcp.excellence-cloud.de/mcp"
DEFAULT_REMOTE_MCP_TRANSPORT = "http"
DEFAULT_LEX_MCP_PRODUCTION = "false"
DEFAULT_LEX_MCP_MODE = "forward"
GITHUB_TOKEN_URL = "https://github.com/settings/tokens/new?description=Full+Classic+PAT&scopes=repo,workflow,admin:org,admin:repo_hook,user,project,admin:enterprise,read:enterprise,manage_runners:enterprise,read:audit_log,write:network_configurations,manage_billing:copilot"
GITHUB_COPILOT_MCP_FIRST_BOOT_COMPLETED_KEY = "mcp-first-boot-completed"
GITHUB_COPILOT_MCP_SERVERS_CACHE_KEY = "mcp-servers-cache"
GITHUB_COPILOT_STATE_DB_NAME = "copilot-intellij.db"
LEX_MCP_LOCAL_SERVER_NAME = "lex-mcp-local"
LEGACY_LEX_MCP_SERVER_NAMES = ("lex-mcp-wrapper",)
# Directories inside the lex-mcp-local package root that are copied into the
# consumer project root on every setup / update.  Extend this tuple when a new
# version of lex-mcp-local ships additional directories that belong in the project.
LEX_MCP_LOCAL_EMBEDDED_DIRECTORY_NAMES: tuple[str, ...] = (".github",)
# Directories inside the lex-app package that are copied into the project root.
LEX_APP_EMBEDDED_DIRECTORY_NAMES: tuple[str, ...] = ("docs",)
LEX_MCP_LOCAL_INSTALL_COMMAND_SUFFIX = (
    "--no-cache-dir",
    "--extra-index-url",
    "https://pypi.org/simple",
    "lex-mcp-local",
)
_SAFE_UNQUOTED_ENV_VALUE_RE = re.compile(r"^[A-Za-z0-9._:/@+-]*$")
LEGACY_GITHUB_TOKEN_ENV_NAMES = ("COPILOT_GITHUB_TOKEN",)
LEGACY_GEMINI_ENV_NAMES = ("GEMINI_API_KEY", "GEMINI_MODEL", "GIT_GEMINI_MAX_REPAIR_ATTEMPTS")
LEGACY_GEMINI_MCP_ENV_KEYS = frozenset({"GEMINI_API_KEY", "GEMINI_MODEL", "GIT_GEMINI_MAX_REPAIR_ATTEMPTS"})

# Minimum lex-mcp-local version that ships the unified lex_mcp.server entry
# point and backward-mode support.
MINIMUM_DUAL_MODE_VERSION = "1.0.0"


class SetupWithAIError(RuntimeError):
    pass


@dataclass(frozen=True)
class SetupWithAIUpdateResult:
    version: str
    env_keys_removed: tuple[str, ...] = ()
    mcp_env_keys_removed: tuple[str, ...] = ()
    env_file_path: Path | None = None
    mcp_config_path: Path | None = None
    package_upgraded: bool = False
    artifact_directories_copied: tuple[Path, ...] = ()
    server_restarted: bool = False


@dataclass(frozen=True)
class SetupWithAICredentials:
    github_token: str
    remote_mcp_api_key: str
    mcp_mode: str = "forward"


@dataclass(frozen=True)
class SetupWithAIArtifacts:
    env_file_path: Path
    mcp_config_path: Path
    wrapper_script_path: Path
    python_executable: Path
    server_name: str = LEX_MCP_LOCAL_SERVER_NAME
    github_directory_path: Path | None = None
    docs_directory_path: Path | None = None


@dataclass(frozen=True)
class SetupWithAIServerRuntime:
    pid: int
    log_file_path: Path
    pid_file_path: Path
    already_running: bool = False


@dataclass(frozen=True)
class SetupWithAIMCPProbeResult:
    server_name: str
    server_version: str | None = None
    tool_count: int = 0
    prompt_count: int = 0
    resource_count: int = 0
    resource_template_count: int = 0
    tools: tuple[dict[str, Any], ...] = ()
    prompts: tuple[dict[str, Any], ...] = ()
    resources: tuple[dict[str, Any], ...] = ()
    resource_templates: tuple[dict[str, Any], ...] = ()


def build_lex_mcp_local_install_command(
    python_executable: str | os.PathLike[str],
    remote_mcp_api_key: str,
    *,
    upgrade: bool = False,
) -> list[str]:
    entitlement_token = remote_mcp_api_key.strip()
    if not entitlement_token:
        raise SetupWithAIError("Lex MCP Access Key is required to install lex-mcp-local.")

    index_url = (
        "https://dl.cloudsmith.io/"
        f"{quote(entitlement_token, safe='')}/"
        "excellence-cloud/lex-mcp-local/python/simple/"
    )
    cmd = [
        str(python_executable),
        "-m",
        "pip",
        "install",
    ]
    if upgrade:
        cmd.append("--upgrade")
    cmd += [
        LEX_MCP_LOCAL_INSTALL_COMMAND_SUFFIX[0],
        "--index-url",
        index_url,
        *LEX_MCP_LOCAL_INSTALL_COMMAND_SUFFIX[1:],
    ]
    return cmd


def install_lex_mcp_local(
    python_executable: str | os.PathLike[str],
    remote_mcp_api_key: str,
    runner=subprocess.run,
    *,
    upgrade: bool = False,
) -> list[str]:
    command = build_lex_mcp_local_install_command(python_executable, remote_mcp_api_key, upgrade=upgrade)
    runner(command, check=True)
    return command


def _python_candidates_for_venv(venv_root: Path) -> list[Path]:
    return [
        venv_root / "Scripts" / "python.exe",
        venv_root / "Scripts" / "python",
        venv_root / "bin" / "python",
        venv_root / "bin" / "python3",
    ]


def resolve_active_python_executable(
    project_root: Path,
    env: Mapping[str, str] | None = None,
) -> Path:
    env_map = dict(os.environ if env is None else env)
    candidate_roots: list[Path] = []

    virtual_env_root = env_map.get("VIRTUAL_ENV")
    if virtual_env_root:
        candidate_roots.append(Path(virtual_env_root).expanduser())

    for local_dir_name in (".venv", "venv"):
        local_root = project_root / local_dir_name
        if local_root.is_dir():
            candidate_roots.append(local_root)

    conda_prefix = env_map.get("CONDA_PREFIX")
    if conda_prefix:
        candidate_roots.append(Path(conda_prefix).expanduser())

    seen_roots: set[Path] = set()
    for root in candidate_roots:
        resolved_root = root.resolve()
        if resolved_root in seen_roots:
            continue
        seen_roots.add(resolved_root)
        for candidate in _python_candidates_for_venv(root):
            if candidate.is_file():
                return Path(os.path.abspath(candidate))

    path_python = shutil.which("python") or shutil.which("python3")
    if path_python:
        return Path(os.path.abspath(path_python))

    sys_python = Path(sys.executable).expanduser().resolve()
    if sys_python.is_file():
        return sys_python

    raise SetupWithAIError("Could not determine a Python interpreter for the active virtual environment.")


def resolve_wrapper_script_path(python_executable: Path) -> Path:
    script = (
        "import importlib.util, sys; "
        "spec = importlib.util.find_spec('lex_mcp_local.wrapper_mcp'); "
        "sys.stdout.write(spec.origin if spec and spec.origin else '')"
    )
    try:
        result = subprocess.run(
            [str(python_executable), "-c", script],
            check=True,
            capture_output=True,
            text=True,
        )
    except subprocess.CalledProcessError as exc:
        raise SetupWithAIError(
            f"Could not locate lex_mcp_local.wrapper_mcp using interpreter {python_executable}."
        ) from exc

    origin = result.stdout.strip()
    if not origin:
        raise SetupWithAIError(
            f"Could not locate lex_mcp_local.wrapper_mcp in interpreter {python_executable}."
        )
    return Path(origin).resolve()


def _resolve_lex_mcp_local_embedded_directory(
    wrapper_script_path: Path,
    directory_name: str,
) -> Path | None:
    package_root = wrapper_script_path.resolve().parent
    candidate = package_root / directory_name
    if candidate.is_dir():
        return candidate
    return None


def _copy_directory_into_project_root(
    project_root: Path,
    source_directory: Path | None,
) -> Path | None:
    if source_directory is None:
        return None

    destination_directory = Path(project_root).resolve() / source_directory.name
    try:
        shutil.copytree(source_directory, destination_directory, dirs_exist_ok=True)
    except OSError as exc:
        raise SetupWithAIError(
            f"Could not copy {source_directory} into {destination_directory}."
        ) from exc

    return destination_directory


def copy_lex_mcp_local_github_directory(
    project_root: Path,
    wrapper_script_path: Path,
) -> Path | None:
    source_directory = _resolve_lex_mcp_local_embedded_directory(
        wrapper_script_path,
        ".github",
    )
    return _copy_directory_into_project_root(project_root, source_directory)


def copy_lex_mcp_local_directories(
    project_root: Path,
    wrapper_script_path: Path,
    directory_names: tuple[str, ...] = LEX_MCP_LOCAL_EMBEDDED_DIRECTORY_NAMES,
) -> tuple[Path, ...]:
    """Copy every embedded directory from the lex-mcp-local package into *project_root*.

    Returns a tuple of the destination paths that were actually copied (directories
    not present in the installed package are silently skipped).
    """
    copied: list[Path] = []
    for name in directory_names:
        source = _resolve_lex_mcp_local_embedded_directory(wrapper_script_path, name)
        dest = _copy_directory_into_project_root(project_root, source)
        if dest is not None:
            copied.append(dest)
    return tuple(copied)


def resolve_lex_app_package_root(python_executable: Path) -> Path | None:
    fallback_package_root = Path(__file__).resolve().parents[1]
    script = (
        "import importlib.util, os, sys; "
        "spec = importlib.util.find_spec('lex'); "
        "locations = list(spec.submodule_search_locations or []) if spec else []; "
        "location = locations[0] if locations else (os.path.dirname(spec.origin) if spec and spec.origin else ''); "
        "sys.stdout.write(location)"
    )
    try:
        result = subprocess.run(
            [str(python_executable), "-c", script],
            check=True,
            capture_output=True,
            text=True,
        )
    except subprocess.CalledProcessError:
        return fallback_package_root if fallback_package_root.is_dir() else None

    package_root = result.stdout.strip()
    if not package_root:
        return fallback_package_root if fallback_package_root.is_dir() else None
    return Path(package_root).resolve()


def copy_lex_app_docs_directory(
    project_root: Path,
    lex_package_root: Path | None,
) -> Path | None:
    if lex_package_root is None:
        return None

    source_directory = lex_package_root.resolve() / "docs"
    if not source_directory.is_dir():
        return None

    project_root_resolved = Path(project_root).resolve()
    if project_root_resolved in source_directory.parents:
        return None

    return _copy_directory_into_project_root(project_root, source_directory)


def resolve_github_copilot_mcp_config_path(
    env: Mapping[str, str] | None = None,
    home: Path | None = None,
) -> Path:
    env_map = dict(os.environ if env is None else env)
    home_dir = Path.home() if home is None else Path(home)

    if os.name == "nt":
        local_app_data = env_map.get("LOCALAPPDATA")
        base_dir = Path(local_app_data) if local_app_data else home_dir / "AppData" / "Local"
        return base_dir / "github-copilot" / "intellij" / "mcp.json"

    return home_dir / ".config" / "github-copilot" / "intellij" / "mcp.json"


def resolve_github_copilot_state_db_path(
    mcp_config_path: Path | None = None,
    env: Mapping[str, str] | None = None,
    home: Path | None = None,
) -> Path:
    if mcp_config_path is not None:
        return Path(mcp_config_path).resolve().parent.parent / GITHUB_COPILOT_STATE_DB_NAME

    env_map = dict(os.environ if env is None else env)
    home_dir = Path.home() if home is None else Path(home)

    if os.name == "nt":
        local_app_data = env_map.get("LOCALAPPDATA")
        base_dir = Path(local_app_data) if local_app_data else home_dir / "AppData" / "Local"
        return base_dir / "github-copilot" / GITHUB_COPILOT_STATE_DB_NAME

    return home_dir / ".config" / "github-copilot" / GITHUB_COPILOT_STATE_DB_NAME


def build_ai_env_values(
    github_token: str,
    remote_mcp_api_key: str,
    remote_mcp_url: str = DEFAULT_REMOTE_MCP_URL,
    mcp_mode: str = DEFAULT_LEX_MCP_MODE,
) -> dict[str, str]:
    return {
        "REMOTE_MCP_TRANSPORT": DEFAULT_REMOTE_MCP_TRANSPORT,
        "REMOTE_MCP_URL": remote_mcp_url,
        "LEX_MCP_PRODUCTION": DEFAULT_LEX_MCP_PRODUCTION,
        "REMOTE_MCP_API_KEY": remote_mcp_api_key,
        "GITHUB_TOKEN": github_token,
        "LEX_MCP_MODE": mcp_mode,
    }


def _build_process_env(
    env_values: Mapping[str, str],
    base_env: Mapping[str, str] | None = None,
) -> dict[str, str]:
    process_env = dict(os.environ if base_env is None else base_env)
    process_env.update({key: str(value) for key, value in env_values.items()})
    return process_env


def _resolve_lex_mcp_local_runtime_paths(
    mcp_config_path: Path,
    server_name: str = LEX_MCP_LOCAL_SERVER_NAME,
) -> tuple[Path, Path]:
    runtime_root = Path(mcp_config_path).resolve().parent
    return (
        runtime_root / f"{server_name}.pid",
        runtime_root / f"{server_name}.log",
    )


def _read_pid_file(pid_file_path: Path) -> int | None:
    if not pid_file_path.exists():
        return None
    try:
        return int(pid_file_path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None


def _is_process_running(pid: int) -> bool:
    if pid <= 0:
        return False

    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _build_detached_popen_kwargs() -> dict[str, Any]:
    kwargs: dict[str, Any] = {"close_fds": True}
    if os.name == "nt":
        kwargs["creationflags"] = (
            getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            | getattr(subprocess, "DETACHED_PROCESS", 0)
            | getattr(subprocess, "CREATE_NO_WINDOW", 0)
        )
    else:
        kwargs["start_new_session"] = True
    return kwargs


def _read_log_tail(log_file_path: Path, max_chars: int = 4000) -> str:
    if not log_file_path.exists():
        return ""

    try:
        content = log_file_path.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return ""

    if len(content) > max_chars:
        content = content[-max_chars:]
    return content


def _format_env_value(value: str) -> str:
    text = str(value)
    if _SAFE_UNQUOTED_ENV_VALUE_RE.fullmatch(text):
        return text
    return json.dumps(text)


def _atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=path.parent,
        prefix=f"{path.name}.",
        suffix=".tmp",
        delete=False,
    ) as handle:
        handle.write(content)
        tmp_path = Path(handle.name)
    tmp_path.replace(path)


def update_env_file(env_file_path: Path, values: Mapping[str, str]) -> None:
    existing_lines = []
    if env_file_path.exists():
        existing_lines = env_file_path.read_text(encoding="utf-8").splitlines()

    output_lines: list[str] = []
    line_positions: dict[str, int] = {}
    keys_to_remove = set(LEGACY_GITHUB_TOKEN_ENV_NAMES)

    for line in existing_lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            output_lines.append(line)
            continue
        key, _ = stripped.split("=", 1)
        if key in keys_to_remove and "GITHUB_TOKEN" in values:
            continue
        line_positions.setdefault(key, len(output_lines))
        output_lines.append(line)

    for key, value in values.items():
        formatted_line = f"{key}={_format_env_value(value)}"
        if key in line_positions:
            output_lines[line_positions[key]] = formatted_line
        else:
            output_lines.append(formatted_line)

    content = "\n".join(output_lines)
    if content:
        content += "\n"
    _atomic_write_text(env_file_path, content)


def get_installed_lex_mcp_local_version(
    python_executable: Path,
) -> str | None:
    """Return the installed ``lex-mcp-local`` version, or ``None``."""
    script = (
        "import importlib.metadata; "
        "print(importlib.metadata.version('lex-mcp-local'))"
    )
    try:
        result = subprocess.run(
            [str(python_executable), "-c", script],
            capture_output=True, text=True, check=True, timeout=10,
        )
        return result.stdout.strip() or None
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError):
        return None


def _has_unified_mcp_entry_point(python_executable: Path) -> bool:
    """Return ``True`` if the unified ``lex_mcp.server`` module is importable.

    ``lex-mcp-local >= 1.0.0`` ships the unified ``lex_mcp`` package with
    a ``server`` module.  Older releases only have ``lex_mcp_local.wrapper_mcp``.
    """
    script = (
        "import importlib.util, sys; "
        "spec = importlib.util.find_spec('lex_mcp.server'); "
        "sys.stdout.write('1' if spec else '0')"
    )
    try:
        result = subprocess.run(
            [str(python_executable), "-c", script],
            capture_output=True, text=True, check=True, timeout=10,
        )
        return result.stdout.strip() == "1"
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError):
        return False


def resolve_mcp_server_args(
    python_executable: Path,
    mcp_mode: str = DEFAULT_LEX_MCP_MODE,
) -> list[str]:
    """Return the ``args`` list for the mcp.json server definition.

    * If ``lex_mcp.server`` is importable (>= 1.0.0), uses the unified
      entry point: ``["-m", "lex_mcp.server", "--mode", <mode>]``.
    * Otherwise falls back to the legacy wrapper script path:
      ``["<path/to/wrapper_mcp.py>"]``.
    """
    if _has_unified_mcp_entry_point(python_executable):
        return ["-m", "lex_mcp.server", "--mode", mcp_mode]
    wrapper_path = resolve_wrapper_script_path(python_executable)
    return [str(wrapper_path)]


def build_mcp_server_definition(
    python_executable: Path,
    github_token: str,
    remote_mcp_api_key: str,
    remote_mcp_url: str = DEFAULT_REMOTE_MCP_URL,
    mcp_mode: str = DEFAULT_LEX_MCP_MODE,
) -> dict:
    return {
        "type": "stdio",
        "command": str(python_executable),
        "args": resolve_mcp_server_args(python_executable, mcp_mode),
        "env": build_ai_env_values(
            github_token=github_token,
            remote_mcp_api_key=remote_mcp_api_key,
            remote_mcp_url=remote_mcp_url,
            mcp_mode=mcp_mode,
        ),
    }


def write_github_copilot_mcp_config(
    mcp_config_path: Path,
    server_definition: Mapping[str, object],
    server_name: str = LEX_MCP_LOCAL_SERVER_NAME,
) -> None:
    config: dict[str, object]
    if mcp_config_path.exists():
        raw_text = mcp_config_path.read_text(encoding="utf-8")
        parsed = None
        try:
            parsed = json.loads(raw_text)
        except json.JSONDecodeError:
            pass

        if parsed is None or not isinstance(parsed, dict):
            reason = (
                "is not valid JSON" if parsed is None
                else "does not contain a JSON object"
            )
            print(
                f"\nWarning: GitHub Copilot MCP config {reason}: {mcp_config_path}\n"
                f"The file will be overwritten with a fresh configuration.\n"
            )
            answer = input("Proceed? [y/N]: ").strip().lower()
            if answer not in ("y", "yes"):
                raise SetupWithAIError(
                    "Aborted: user declined to overwrite invalid mcp.json"
                )
            config = {}
        else:
            config = parsed
    else:
        config = {}

    servers = config.get("servers", {})
    if not isinstance(servers, dict):
        print(
            f"\nWarning: GitHub Copilot MCP config 'servers' value is not an object: {mcp_config_path}\n"
            f"The 'servers' section will be reset.\n"
        )
        answer = input("Proceed? [y/N]: ").strip().lower()
        if answer not in ("y", "yes"):
            raise SetupWithAIError(
                "Aborted: user declined to overwrite invalid mcp.json"
            )
        servers = {}

    for legacy_name in LEGACY_LEX_MCP_SERVER_NAMES:
        if legacy_name != server_name:
            servers.pop(legacy_name, None)

    servers[server_name] = dict(server_definition)
    config["servers"] = servers

    _atomic_write_text(mcp_config_path, json.dumps(config, indent=2) + "\n")


def _coerce_mcp_inventory_items(
    items: list[Any],
    *,
    method: str,
    result_key: str,
    server_name: str,
) -> tuple[dict[str, Any], ...]:
    normalized: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            raise SetupWithAIError(
                f"{server_name} returned an invalid {result_key} item for {method}: {item!r}"
            )
        normalized.append(json.loads(json.dumps(item)))
    return tuple(normalized)


def _normalize_github_copilot_cached_tool(tool: Mapping[str, Any]) -> dict[str, Any]:
    name = tool.get("name")
    if not isinstance(name, str) or not name.strip():
        raise SetupWithAIError(f"Cannot cache an MCP tool without a name: {tool!r}")

    description = tool.get("description")
    input_schema = tool.get("inputSchema")
    annotations = tool.get("annotations")

    normalized_schema: dict[str, Any]
    if isinstance(input_schema, dict):
        normalized_schema = json.loads(json.dumps(input_schema))
    else:
        normalized_schema = {}

    if not isinstance(normalized_schema.get("properties"), dict):
        normalized_schema["properties"] = {}
    if not isinstance(normalized_schema.get("type"), str) or not normalized_schema["type"]:
        normalized_schema["type"] = "object"

    normalized_tool: dict[str, Any] = {
        "name": name,
        "description": description if isinstance(description, str) else "",
        "inputSchema": normalized_schema,
        "_status": "enabled",
        "_nameForModel": name,
    }
    if isinstance(annotations, dict):
        normalized_tool["annotations"] = json.loads(json.dumps(annotations))
    return normalized_tool


def build_github_copilot_mcp_server_cache_entry(
    probe_result: SetupWithAIMCPProbeResult,
) -> dict[str, list[dict[str, Any]]]:
    return {
        "tools": [
            _normalize_github_copilot_cached_tool(tool)
            for tool in probe_result.tools
        ],
        "resources": [json.loads(json.dumps(resource)) for resource in probe_result.resources],
        "resourceTemplates": [
            json.loads(json.dumps(resource_template))
            for resource_template in probe_result.resource_templates
        ],
        "prompts": [json.loads(json.dumps(prompt)) for prompt in probe_result.prompts],
    }


def _ensure_github_copilot_state_table(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS state (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at INTEGER NOT NULL
        )
        """
    )


def _encode_github_copilot_state_value(value: Any) -> str:
    if isinstance(value, (dict, list)):
        inner_value = json.dumps(value)
    else:
        inner_value = str(value)
    return json.dumps(inner_value)


def _decode_github_copilot_state_value(raw_value: str) -> Any:
    return json.loads(raw_value)


def _load_github_copilot_mcp_servers_cache(
    connection: sqlite3.Connection,
) -> dict[str, Any]:
    row = connection.execute(
        "SELECT value FROM state WHERE key = ?",
        (GITHUB_COPILOT_MCP_SERVERS_CACHE_KEY,),
    ).fetchone()
    if row is None:
        return {}

    raw_value = row[0]
    try:
        decoded_value = _decode_github_copilot_state_value(raw_value)
    except json.JSONDecodeError:
        return {}

    if isinstance(decoded_value, str):
        if not decoded_value:
            return {}
        try:
            decoded_value = json.loads(decoded_value)
        except json.JSONDecodeError:
            return {}

    if not isinstance(decoded_value, dict):
        return {}
    return json.loads(json.dumps(decoded_value))


def _write_github_copilot_state_value(
    connection: sqlite3.Connection,
    key: str,
    value: Any,
) -> None:
    connection.execute(
        """
        INSERT INTO state (key, value, updated_at)
        VALUES (?, ?, ?)
        ON CONFLICT(key) DO UPDATE SET
            value = excluded.value,
            updated_at = excluded.updated_at
        """,
        (
            key,
            _encode_github_copilot_state_value(value),
            int(time.time() * 1000),
        ),
    )


def bootstrap_github_copilot_mcp_server_for_pycharm(
    probe_result: SetupWithAIMCPProbeResult,
    *,
    mcp_config_path: Path | None = None,
    state_db_path: Path | None = None,
    server_name: str | None = None,
    env: Mapping[str, str] | None = None,
    home: Path | None = None,
) -> Path:
    target_server_name = server_name or probe_result.server_name
    target_state_db_path = (
        Path(state_db_path).resolve()
        if state_db_path is not None
        else resolve_github_copilot_state_db_path(
            mcp_config_path=mcp_config_path,
            env=env,
            home=home,
        ).resolve()
    )
    target_state_db_path.parent.mkdir(parents=True, exist_ok=True)

    cache_entry = build_github_copilot_mcp_server_cache_entry(probe_result)

    try:
        with sqlite3.connect(target_state_db_path) as connection:
            _ensure_github_copilot_state_table(connection)
            cached_servers = _load_github_copilot_mcp_servers_cache(connection)
            cached_servers[target_server_name] = cache_entry
            _write_github_copilot_state_value(
                connection,
                GITHUB_COPILOT_MCP_SERVERS_CACHE_KEY,
                cached_servers,
            )
            # Copilot only auto-starts managed MCP servers on its first boot path.
            _write_github_copilot_state_value(
                connection,
                GITHUB_COPILOT_MCP_FIRST_BOOT_COMPLETED_KEY,
                "false",
            )
            connection.commit()
    except sqlite3.Error as exc:
        raise SetupWithAIError(
            f"Could not prime GitHub Copilot's MCP cache at {target_state_db_path}."
        ) from exc

    return target_state_db_path


def probe_lex_mcp_local_server_for_pycharm(
    project_root: Path,
    python_executable: Path,
    wrapper_script_path: Path,
    env_values: Mapping[str, str],
    base_env: Mapping[str, str] | None = None,
    startup_timeout_seconds: float = 30.0,
    server_name: str = LEX_MCP_LOCAL_SERVER_NAME,
) -> SetupWithAIMCPProbeResult:
    process_env = _build_process_env(env_values, base_env=base_env)
    recent_output: list[str] = []
    # TextIO buffering can hide already-read responses from simple polling.
    line_queue: queue.Queue[str | None] = queue.Queue()

    def _append_output(line: str) -> None:
        stripped = line.strip()
        if not stripped:
            return
        recent_output.append(stripped)
        if len(recent_output) > 40:
            del recent_output[: len(recent_output) - 40]

    def _format_recent_output(limit: int = 12) -> str:
        if not recent_output:
            return "no recent output"
        return " | ".join(recent_output[-limit:])

    def _send_json(process: subprocess.Popen[str], payload: Mapping[str, Any]) -> None:
        if process.stdin is None:
            raise SetupWithAIError(f"{server_name} stdin is not available for MCP probing.")
        process.stdin.write(json.dumps(dict(payload)) + "\n")
        process.stdin.flush()

    def _reader_thread(stdout) -> None:  # type: ignore[no-untyped-def]
        try:
            for line in iter(stdout.readline, ""):
                line_queue.put(line)
        except Exception:
            pass
        finally:
            line_queue.put(None)

    def _read_response_for_id(expected_id: int, deadline: float) -> dict[str, Any] | None:
        while time.monotonic() < deadline:
            remaining = max(0.0, deadline - time.monotonic())
            try:
                line = line_queue.get(timeout=remaining)
            except queue.Empty:
                break

            if line is None:
                break

            _append_output(line)
            candidate = line.strip()
            if not candidate.startswith("{"):
                continue

            try:
                response = json.loads(candidate)
            except json.JSONDecodeError:
                continue

            if isinstance(response, dict) and response.get("id") == expected_id:
                return response

        return None

    def _read_inventory(
        process: subprocess.Popen[str],
        *,
        request_id: int,
        method: str,
        result_key: str,
        deadline: float,
    ) -> tuple[dict[str, Any], ...]:
        _send_json(
            process,
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "method": method,
                "params": {},
            },
        )
        response = _read_response_for_id(request_id, deadline)
        if response is None:
            return_code = process.poll()
            raise SetupWithAIError(
                f"{server_name} did not answer {method} during the PyCharm-style MCP refresh. "
                f"Process exit code: {return_code}. Recent output: {_format_recent_output()}"
            )
        if "error" in response:
            raise SetupWithAIError(
                f"{server_name} returned an MCP error for {method}: {response['error']}"
            )

        result = response.get("result", {})
        if not isinstance(result, dict):
            raise SetupWithAIError(
                f"{server_name} returned an invalid result for {method}: {result!r}"
            )

        inventory = result.get(result_key, [])
        if not isinstance(inventory, list):
            raise SetupWithAIError(
                f"{server_name} returned an invalid {result_key} payload for {method}: {inventory!r}"
            )
        return _coerce_mcp_inventory_items(
            inventory,
            method=method,
            result_key=result_key,
            server_name=server_name,
        )

    try:
        process = subprocess.Popen(
            [str(python_executable), str(wrapper_script_path)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            cwd=str(Path(project_root).resolve()),
            env=process_env,
        )
    except OSError as exc:
        raise SetupWithAIError(
            f"Could not launch {server_name} for the PyCharm-style MCP validation."
        ) from exc

    try:
        if process.stdout is None:
            raise SetupWithAIError(f"{server_name} stdout is not available for MCP probing.")

        reader = threading.Thread(target=_reader_thread, args=(process.stdout,), daemon=True)
        reader.start()

        deadline = time.monotonic() + startup_timeout_seconds
        _send_json(
            process,
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "lex-setup-with-ai", "version": "0.1.0"},
                },
            },
        )
        init_response = _read_response_for_id(1, deadline)
        if init_response is None:
            return_code = process.poll()
            raise SetupWithAIError(
                f"{server_name} never completed the MCP initialize handshake that PyCharm uses. "
                f"Process exit code: {return_code}. Recent output: {_format_recent_output()}"
            )
        # Reset deadline after each successful step so slow startup
        # does not starve subsequent inventory calls.
        deadline = time.monotonic() + startup_timeout_seconds
        if "error" in init_response:
            raise SetupWithAIError(
                f"{server_name} rejected the MCP initialize handshake: {init_response['error']}"
            )

        init_result = init_response.get("result", {})
        if not isinstance(init_result, dict):
            raise SetupWithAIError(
                f"{server_name} returned an invalid initialize result: {init_result!r}"
            )

        server_info = init_result.get("serverInfo", {})
        server_version = None
        if isinstance(server_info, dict):
            version_value = server_info.get("version")
            if isinstance(version_value, str) and version_value.strip():
                server_version = version_value.strip()

        _send_json(
            process,
            {
                "jsonrpc": "2.0",
                "method": "notifications/initialized",
                "params": {},
            },
        )

        tools = _read_inventory(
            process,
            request_id=2,
            method="tools/list",
            result_key="tools",
            deadline=deadline,
        )
        deadline = time.monotonic() + startup_timeout_seconds
        prompts = _read_inventory(
            process,
            request_id=3,
            method="prompts/list",
            result_key="prompts",
            deadline=deadline,
        )
        deadline = time.monotonic() + startup_timeout_seconds
        resources = _read_inventory(
            process,
            request_id=4,
            method="resources/list",
            result_key="resources",
            deadline=deadline,
        )
        deadline = time.monotonic() + startup_timeout_seconds
        resource_templates = _read_inventory(
            process,
            request_id=5,
            method="resources/templates/list",
            result_key="resourceTemplates",
            deadline=deadline,
        )

        return SetupWithAIMCPProbeResult(
            server_name=server_name,
            server_version=server_version,
            tool_count=len(tools),
            prompt_count=len(prompts),
            resource_count=len(resources),
            resource_template_count=len(resource_templates),
            tools=tools,
            prompts=prompts,
            resources=resources,
            resource_templates=resource_templates,
        )
    finally:
        if process.stdin is not None and not process.stdin.closed:
            process.stdin.close()
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()


def verify_lex_mcp_local_server_starts(
    project_root: Path,
    python_executable: Path,
    wrapper_script_path: Path,
    env_values: Mapping[str, str],
    base_env: Mapping[str, str] | None = None,
    startup_timeout_seconds: float = 30.0,
) -> SetupWithAIMCPProbeResult:
    return probe_lex_mcp_local_server_for_pycharm(
        project_root=project_root,
        python_executable=python_executable,
        wrapper_script_path=wrapper_script_path,
        env_values=env_values,
        base_env=base_env,
        startup_timeout_seconds=startup_timeout_seconds,
    )


def start_lex_mcp_local_server(
    project_root: Path,
    mcp_config_path: Path,
    python_executable: Path,
    wrapper_script_path: Path,
    github_token: str,
    remote_mcp_api_key: str,
    remote_mcp_url: str = DEFAULT_REMOTE_MCP_URL,
    *,
    env: Mapping[str, str] | None = None,
    server_name: str = LEX_MCP_LOCAL_SERVER_NAME,
    startup_timeout_seconds: float = 1.0,
) -> SetupWithAIServerRuntime:
    pid_file_path, log_file_path = _resolve_lex_mcp_local_runtime_paths(
        mcp_config_path,
        server_name=server_name,
    )
    pid_file_path.parent.mkdir(parents=True, exist_ok=True)

    existing_pid = _read_pid_file(pid_file_path)
    if existing_pid is not None and _is_process_running(existing_pid):
        return SetupWithAIServerRuntime(
            pid=existing_pid,
            log_file_path=log_file_path,
            pid_file_path=pid_file_path,
            already_running=True,
        )
    if pid_file_path.exists():
        pid_file_path.unlink()

    env_values = build_ai_env_values(
        github_token=github_token,
        remote_mcp_api_key=remote_mcp_api_key,
        remote_mcp_url=remote_mcp_url,
    )
    process_env = _build_process_env(env_values, base_env=env)

    with log_file_path.open("a", encoding="utf-8") as log_handle:
        process = subprocess.Popen(
            [str(python_executable), str(wrapper_script_path)],
            stdin=subprocess.DEVNULL,
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            cwd=str(Path(project_root).resolve()),
            env=process_env,
            **_build_detached_popen_kwargs(),
        )

        time.sleep(startup_timeout_seconds)

    return_code = process.poll()
    if return_code is not None:
        log_tail = _read_log_tail(log_file_path)
        detail = log_tail or f"exit code {return_code}"
        raise SetupWithAIError(
            "lex-mcp-local exited immediately after the background launch attempt. "
            f"Check {log_file_path}: {detail}"
        )

    _atomic_write_text(pid_file_path, f"{process.pid}\n")
    return SetupWithAIServerRuntime(
        pid=process.pid,
        log_file_path=log_file_path,
        pid_file_path=pid_file_path,
    )


def configure_ai_integration(
    project_root: Path,
    github_token: str,
    remote_mcp_api_key: str,
    remote_mcp_url: str = DEFAULT_REMOTE_MCP_URL,
    *,
    mcp_mode: str = DEFAULT_LEX_MCP_MODE,
    python_executable: Path | None = None,
    mcp_config_path: Path | None = None,
    env: Mapping[str, str] | None = None,
    home: Path | None = None,
    verify_server: bool = True,
) -> SetupWithAIArtifacts:
    python_path = (
        resolve_active_python_executable(Path(project_root), env=env)
        if python_executable is None
        else Path(os.path.abspath(python_executable))
    )
    wrapper_script_path = resolve_wrapper_script_path(python_path)
    github_directory_path = copy_lex_mcp_local_github_directory(
        Path(project_root),
        wrapper_script_path,
    )
    docs_directory_path = copy_lex_app_docs_directory(
        Path(project_root),
        resolve_lex_app_package_root(python_path),
    )
    env_values = build_ai_env_values(
        github_token=github_token,
        remote_mcp_api_key=remote_mcp_api_key,
        remote_mcp_url=remote_mcp_url,
        mcp_mode=mcp_mode,
    )

    env_file_path = (Path(project_root) / ".env").resolve()
    update_env_file(env_file_path, env_values)

    copilot_mcp_path = (
        resolve_github_copilot_mcp_config_path(env=env, home=home)
        if mcp_config_path is None
        else Path(mcp_config_path)
    ).resolve()

    server_definition = build_mcp_server_definition(
        python_executable=python_path,
        github_token=github_token,
        remote_mcp_api_key=remote_mcp_api_key,
        remote_mcp_url=remote_mcp_url,
        mcp_mode=mcp_mode,
    )
    write_github_copilot_mcp_config(copilot_mcp_path, server_definition)

    if verify_server:
        verify_lex_mcp_local_server_starts(
            project_root=Path(project_root),
            python_executable=python_path,
            wrapper_script_path=wrapper_script_path,
            env_values=env_values,
            base_env=env,
        )

    return SetupWithAIArtifacts(
        env_file_path=env_file_path,
        mcp_config_path=copilot_mcp_path,
        wrapper_script_path=wrapper_script_path,
        python_executable=python_path,
        github_directory_path=github_directory_path,
        docs_directory_path=docs_directory_path,
    )


def launch_setup_with_ai_form(
    project_root: Path,
    env_file_path: Path,
    reporter: Callable[[str], None] | None = None,
    timeout_seconds: int = 900,
    remote_mcp_url: str = DEFAULT_REMOTE_MCP_URL,
) -> SetupWithAICredentials:
    state = secrets.token_urlsafe(16)
    result: dict[str, SetupWithAICredentials] = {}
    submitted = threading.Event()
    report = reporter or (lambda message: None)

    last_used = load_last_used()

    # Server-side cache for the in-progress GitHub Device Flow. The browser
    # only ever sees an opaque session id, never the device_code itself.
    device_sessions: dict[str, DeviceCode] = {}
    device_lock = threading.Lock()

    def _json_response(handler: BaseHTTPRequestHandler, status: int, payload: dict) -> None:
        encoded = json.dumps(payload).encode("utf-8")
        handler.send_response(status)
        handler.send_header("Content-Type", "application/json; charset=utf-8")
        handler.send_header("Content-Length", str(len(encoded)))
        handler.send_header("Cache-Control", "no-store")
        handler.end_headers()
        handler.wfile.write(encoded)

    def _read_json_body(handler: BaseHTTPRequestHandler) -> dict:
        content_length = int(handler.headers.get("Content-Length", "0") or "0")
        if content_length <= 0:
            return {}
        raw = handler.rfile.read(content_length).decode("utf-8")
        if not raw:
            return {}
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        return payload if isinstance(payload, dict) else {}

    def _check_state(handler: BaseHTTPRequestHandler, payload: dict) -> bool:
        if payload.get("state") != state:
            _json_response(handler, HTTPStatus.FORBIDDEN, {"error": "state mismatch"})
            return False
        return True

    class SetupWithAIHandler(BaseHTTPRequestHandler):
        def _route_get(self) -> None:
            parsed = urlparse(self.path)
            path = parsed.path
            if path in ("", "/"):
                body = _build_setup_form_html(
                    state=state,
                    project_root=project_root,
                    env_file_path=env_file_path,
                    remote_mcp_url=remote_mcp_url,
                    last_used=last_used,
                    device_flow_available=is_device_flow_available(),
                )
                encoded = body.encode("utf-8")
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(encoded)))
                self.end_headers()
                self.wfile.write(encoded)
                return

            if path == "/api/state":
                _json_response(self, HTTPStatus.OK, {
                    "state": state,
                    "project_root": str(project_root),
                    "env_file_path": str(env_file_path),
                    "remote_mcp_url": remote_mcp_url,
                    "github_token_url": GITHUB_TOKEN_URL,
                    "device_flow_available": is_device_flow_available(),
                    "last_used": {
                        "mcp_mode": last_used.mcp_mode,
                        "remote_mcp_url": last_used.remote_mcp_url,
                        "prefer_pat": last_used.prefer_pat,
                    },
                })
                return

            if path == "/api/github/device/poll":
                qs = parse_qs(parsed.query)
                if qs.get("state", [""])[0] != state:
                    _json_response(self, HTTPStatus.FORBIDDEN, {"error": "state mismatch"})
                    return
                session_id = qs.get("session", [""])[0]
                with device_lock:
                    code = device_sessions.get(session_id)
                if code is None:
                    _json_response(self, HTTPStatus.NOT_FOUND, {"error": "no such device session"})
                    return
                try:
                    poll = poll_device_flow(code.device_code)
                except DeviceFlowUnavailable as exc:
                    _json_response(self, HTTPStatus.SERVICE_UNAVAILABLE, {"status": "unavailable", "error": str(exc)})
                    return
                except DeviceFlowError as exc:
                    _json_response(self, HTTPStatus.BAD_GATEWAY, {"status": "error", "error": str(exc)})
                    return
                response: dict = {"status": poll.status}
                if poll.status == "authorized":
                    # Hand the token back to the browser exactly once. The
                    # SPA stores it in memory only and POSTs it back with
                    # /api/submit. The handler clears the session immediately.
                    response["access_token"] = poll.access_token
                    response["scopes"] = list(poll.scopes)
                    with device_lock:
                        device_sessions.pop(session_id, None)
                elif poll.status == "slow_down":
                    response["interval"] = poll.interval
                elif poll.error:
                    response["error"] = poll.error
                _json_response(self, HTTPStatus.OK, response)
                return

            self.send_error(HTTPStatus.NOT_FOUND)

        def _route_post(self) -> None:
            path = urlparse(self.path).path
            if path == "/api/github/device/start":
                payload = _read_json_body(self)
                if not _check_state(self, payload):
                    return
                if not is_device_flow_available():
                    _json_response(self, HTTPStatus.SERVICE_UNAVAILABLE, {
                        "error": "GitHub Device Flow is not configured. Use the personal access token option.",
                    })
                    return
                try:
                    code = start_device_flow()
                except DeviceFlowUnavailable as exc:
                    _json_response(self, HTTPStatus.SERVICE_UNAVAILABLE, {"error": str(exc)})
                    return
                except DeviceFlowError as exc:
                    _json_response(self, HTTPStatus.BAD_GATEWAY, {"error": str(exc)})
                    return
                session_id = secrets.token_urlsafe(12)
                with device_lock:
                    device_sessions[session_id] = code
                public = code.to_public_dict()
                public["session"] = session_id
                _json_response(self, HTTPStatus.OK, public)
                return

            if path == "/api/validate/github-token":
                payload = _read_json_body(self)
                if not _check_state(self, payload):
                    return
                token = str(payload.get("github_token", "") or "").strip()
                validation = validate_github_token(token)
                _json_response(self, HTTPStatus.OK, validation.to_dict())
                return

            if path == "/api/validate/mcp-key":
                payload = _read_json_body(self)
                if not _check_state(self, payload):
                    return
                api_key = str(payload.get("remote_mcp_api_key", "") or "").strip()
                url = str(payload.get("remote_mcp_url", "") or remote_mcp_url).strip() or remote_mcp_url
                validation = validate_remote_mcp_key(url, api_key)
                _json_response(self, HTTPStatus.OK, validation.to_dict())
                return

            if path == "/submit" or path == "/api/submit":
                # Accept both JSON (from the SPA) and form-encoded (legacy).
                content_type = (self.headers.get("Content-Type") or "").lower()
                if "application/json" in content_type:
                    payload = _read_json_body(self)
                    if not _check_state(self, payload):
                        return
                    github_token = str(payload.get("github_token", "") or "").strip()
                    remote_mcp_api_key = str(payload.get("remote_mcp_api_key", "") or "").strip()
                    mcp_mode = str(payload.get("mcp_mode", "forward") or "forward").strip().lower()
                else:
                    content_length = int(self.headers.get("Content-Length", "0") or "0")
                    raw = self.rfile.read(content_length).decode("utf-8") if content_length > 0 else ""
                    form_data = parse_qs(raw, keep_blank_values=True)
                    if form_data.get("state", [""])[0] != state:
                        self.send_error(HTTPStatus.FORBIDDEN, "State mismatch")
                        return
                    github_token = form_data.get("github_token", [""])[0].strip()
                    remote_mcp_api_key = form_data.get("remote_mcp_api_key", [""])[0].strip()
                    mcp_mode = form_data.get("mcp_mode", ["forward"])[0].strip().lower()

                if mcp_mode not in ("forward", "backward"):
                    mcp_mode = "forward"

                if not github_token or not remote_mcp_api_key:
                    if "application/json" in content_type:
                        _json_response(self, HTTPStatus.BAD_REQUEST, {
                            "error": "Both GitHub token and Lex MCP Access Key are required.",
                        })
                    else:
                        body = _build_setup_form_html(
                            state=state,
                            project_root=project_root,
                            env_file_path=env_file_path,
                            remote_mcp_url=remote_mcp_url,
                            last_used=last_used,
                            device_flow_available=is_device_flow_available(),
                            error_message="Both fields are required.",
                        )
                        encoded = body.encode("utf-8")
                        self.send_response(HTTPStatus.BAD_REQUEST)
                        self.send_header("Content-Type", "text/html; charset=utf-8")
                        self.send_header("Content-Length", str(len(encoded)))
                        self.end_headers()
                        self.wfile.write(encoded)
                    return

                result["credentials"] = SetupWithAICredentials(
                    github_token=github_token,
                    remote_mcp_api_key=remote_mcp_api_key,
                    mcp_mode=mcp_mode,
                )

                # Persist non-secret choices for next run.
                try:
                    save_last_used(SetupWithAILastUsed(
                        mcp_mode=mcp_mode,
                        remote_mcp_url=remote_mcp_url,
                        last_project_root=str(project_root),
                        last_lex_mcp_local_version=last_used.last_lex_mcp_local_version,
                        prefer_pat=bool(payload.get("prefer_pat", False))
                            if "application/json" in content_type
                            else last_used.prefer_pat,
                    ))
                except OSError:
                    # Persistence failures must never break setup.
                    pass

                if "application/json" in content_type:
                    _json_response(self, HTTPStatus.OK, {
                        "ok": True,
                        "redirect": f"/done?state={quote(state)}",
                    })
                else:
                    body = _build_success_html(
                        env_file_path=env_file_path,
                        project_root=project_root,
                    )
                    encoded = body.encode("utf-8")
                    self.send_response(HTTPStatus.OK)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.send_header("Content-Length", str(len(encoded)))
                    self.end_headers()
                    self.wfile.write(encoded)
                submitted.set()
                return

            self.send_error(HTTPStatus.NOT_FOUND)

        def do_GET(self):
            parsed = urlparse(self.path)
            if parsed.path == "/done":
                qs = parse_qs(parsed.query)
                if qs.get("state", [""])[0] != state:
                    self.send_error(HTTPStatus.FORBIDDEN, "State mismatch")
                    return
                body = _build_success_html(
                    env_file_path=env_file_path,
                    project_root=project_root,
                )
                encoded = body.encode("utf-8")
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(encoded)))
                self.end_headers()
                self.wfile.write(encoded)
                return
            self._route_get()

        def do_POST(self):
            self._route_post()

        def log_message(self, format: str, *args) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), SetupWithAIHandler)
    server.daemon_threads = True
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()

    form_url = f"http://127.0.0.1:{server.server_port}/"
    report(f"Open this local setup page if the browser does not open automatically: {form_url}")
    try:
        opened = webbrowser.open(form_url, new=1, autoraise=True)
        if not opened:
            report("The browser could not be opened automatically. Paste the URL above into any browser.")
    except Exception as exc:
        report(f"Automatic browser launch failed: {exc}")
        report("Paste the local setup page URL into any browser to continue.")

    try:
        if not submitted.wait(timeout=timeout_seconds):
            raise SetupWithAIError(
                "Timed out waiting for the AI setup form to be submitted."
            )
    finally:
        server.shutdown()
        server.server_close()
        server_thread.join(timeout=5)

    credentials = result.get("credentials")
    if credentials is None:
        raise SetupWithAIError("AI setup form closed before credentials were submitted.")
    return credentials


# ---------------------------------------------------------------------------
# ai-update: versioned, incremental migration of an existing LEX AI setup
# ---------------------------------------------------------------------------

def remove_env_keys(env_file_path: Path, keys_to_remove: set[str]) -> tuple[str, ...]:
    """Remove matching lines from a dotenv file. Returns the keys actually removed."""
    if not env_file_path.exists():
        return ()

    existing_lines = env_file_path.read_text(encoding="utf-8").splitlines()
    output_lines: list[str] = []
    removed: list[str] = []

    for line in existing_lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            key, _ = stripped.split("=", 1)
            if key in keys_to_remove:
                removed.append(key)
                continue
        output_lines.append(line)

    if removed:
        content = "\n".join(output_lines)
        if content:
            content += "\n"
        _atomic_write_text(env_file_path, content)

    return tuple(removed)


def remove_mcp_server_env_keys(
    mcp_config_path: Path,
    keys_to_remove: set[str],
    server_name: str = LEX_MCP_LOCAL_SERVER_NAME,
) -> tuple[str, ...]:
    """Remove env keys from a specific server entry inside an mcp.json file.

    Returns the keys actually removed.
    """
    if not mcp_config_path.exists():
        return ()

    try:
        config = json.loads(mcp_config_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return ()

    if not isinstance(config, dict):
        return ()

    servers = config.get("servers", {})
    if not isinstance(servers, dict):
        return ()

    server_def = servers.get(server_name)
    if not isinstance(server_def, dict):
        return ()

    env_block = server_def.get("env")
    if not isinstance(env_block, dict):
        return ()

    removed: list[str] = []
    for key in keys_to_remove:
        if key in env_block:
            del env_block[key]
            removed.append(key)

    if removed:
        _atomic_write_text(mcp_config_path, json.dumps(config, indent=2) + "\n")

    return tuple(removed)


def apply_ai_update_0_2_1(
    project_root: Path,
    *,
    mcp_config_path: Path | None = None,
    env: Mapping[str, str] | None = None,
    home: Path | None = None,
    server_name: str = LEX_MCP_LOCAL_SERVER_NAME,
) -> SetupWithAIUpdateResult:
    """Migrate an existing LEX AI setup from 0.2.0 to 0.2.1.

    * Removes ``GEMINI_API_KEY``, ``GEMINI_MODEL`` and
      ``GIT_GEMINI_MAX_REPAIR_ATTEMPTS`` from the project ``.env``.
    * Removes the same keys from the server's ``env`` block inside
      GitHub Copilot's ``mcp.json``.
    """
    env_file_path = (Path(project_root) / ".env").resolve()
    copilot_mcp_path = (
        resolve_github_copilot_mcp_config_path(env=env, home=home)
        if mcp_config_path is None
        else Path(mcp_config_path)
    ).resolve()

    env_keys_removed = remove_env_keys(env_file_path, set(LEGACY_GEMINI_ENV_NAMES))
    mcp_keys_removed = remove_mcp_server_env_keys(
        copilot_mcp_path,
        LEGACY_GEMINI_MCP_ENV_KEYS,
        server_name=server_name,
    )

    return SetupWithAIUpdateResult(
        version="0.2.1",
        env_keys_removed=env_keys_removed,
        mcp_env_keys_removed=mcp_keys_removed,
        env_file_path=env_file_path,
        mcp_config_path=copilot_mcp_path,
    )


def _read_dotenv_value(env_file_path: Path, key: str) -> str | None:
    """Read a single value from a dotenv file. Returns *None* if not found."""
    if not env_file_path.exists():
        return None

    for line in env_file_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        k, v = stripped.split("=", 1)
        if k == key:
            # Strip surrounding quotes if present.
            if len(v) >= 2 and v[0] == v[-1] and v[0] in ('"', "'"):
                v = v[1:-1]
            return v
    return None


def _stop_lex_mcp_local_server(
    mcp_config_path: Path,
    server_name: str = LEX_MCP_LOCAL_SERVER_NAME,
    timeout_seconds: float = 5.0,
) -> bool:
    """Stop a running lex-mcp-local server. Returns True if a process was stopped."""
    pid_file_path, _log_file_path = _resolve_lex_mcp_local_runtime_paths(
        mcp_config_path,
        server_name=server_name,
    )

    pid = _read_pid_file(pid_file_path)
    if pid is None or not _is_process_running(pid):
        return False

    try:
        os.kill(pid, signal.SIGTERM)
    except OSError:
        return False

    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if not _is_process_running(pid):
            break
        time.sleep(0.1)
    else:
        # Force-kill if still alive after timeout.
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            pass

    if pid_file_path.exists():
        try:
            pid_file_path.unlink()
        except OSError:
            pass

    return True


def apply_ai_update_0_2_2(
    project_root: Path,
    *,
    mcp_config_path: Path | None = None,
    env: Mapping[str, str] | None = None,
    home: Path | None = None,
    server_name: str = LEX_MCP_LOCAL_SERVER_NAME,
    runner: Callable[..., Any] = subprocess.run,
) -> SetupWithAIUpdateResult:
    """Refresh all LEX AI artifacts.

    Run on every ``lex ai-update`` invocation (not just once):
    * Upgrades the ``lex-mcp-local`` pip package to the latest published version.
    * Re-copies ALL embedded directories from the upgraded ``lex-mcp-local``
      package (see ``LEX_MCP_LOCAL_EMBEDDED_DIRECTORY_NAMES``) into the project
      root so they stay in sync with each release.
    * Re-copies ALL embedded directories from the ``lex-app`` package (see
      ``LEX_APP_EMBEDDED_DIRECTORY_NAMES``) into the project root.
    * Stops the running MCP server so it picks up the new code on next launch.
    """
    env_file_path = (Path(project_root) / ".env").resolve()
    copilot_mcp_path = (
        resolve_github_copilot_mcp_config_path(env=env, home=home)
        if mcp_config_path is None
        else Path(mcp_config_path)
    ).resolve()

    python_executable = resolve_active_python_executable(project_root, env=env)

    remote_mcp_api_key = _read_dotenv_value(env_file_path, "REMOTE_MCP_API_KEY")
    if not remote_mcp_api_key:
        raise SetupWithAIError(
            "REMOTE_MCP_API_KEY not found in .env — cannot upgrade lex-mcp-local."
        )

    install_lex_mcp_local(python_executable, remote_mcp_api_key, runner=runner, upgrade=True)

    wrapper_script_path = resolve_wrapper_script_path(python_executable)
    copied: list[Path] = list(
        copy_lex_mcp_local_directories(project_root, wrapper_script_path)
    )

    lex_package_root = resolve_lex_app_package_root(python_executable)
    for dir_name in LEX_APP_EMBEDDED_DIRECTORY_NAMES:
        if dir_name == "docs":
            dest = copy_lex_app_docs_directory(project_root, lex_package_root)
        else:
            src = (lex_package_root / dir_name) if lex_package_root else None
            dest = _copy_directory_into_project_root(project_root, src if src and src.is_dir() else None)
        if dest is not None:
            copied.append(dest)

    server_stopped = _stop_lex_mcp_local_server(
        copilot_mcp_path, server_name=server_name,
    )

    return SetupWithAIUpdateResult(
        version="0.2.2",
        env_file_path=env_file_path,
        mcp_config_path=copilot_mcp_path,
        package_upgraded=True,
        artifact_directories_copied=tuple(copied),
        server_restarted=server_stopped,
    )


def _update_mcp_server_to_unified_entry(
    mcp_config_path: Path,
    server_name: str = LEX_MCP_LOCAL_SERVER_NAME,
    mode: str = DEFAULT_LEX_MCP_MODE,
) -> bool:
    """Migrate an mcp.json server entry from ``wrapper_mcp.py`` to ``lex_mcp.server``.

    Returns ``True`` if the config was modified.
    """
    if not mcp_config_path.exists():
        return False
    try:
        config = json.loads(mcp_config_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return False
    if not isinstance(config, dict):
        return False
    servers = config.get("servers", {})
    if not isinstance(servers, dict):
        return False
    server_def = servers.get(server_name)
    if not isinstance(server_def, dict):
        return False

    args = server_def.get("args", [])
    if isinstance(args, list) and "-m" in args and "lex_mcp.server" in args:
        return False  # already migrated

    server_def["args"] = ["-m", "lex_mcp.server", "--mode", mode]

    env_block = server_def.get("env", {})
    if isinstance(env_block, dict):
        env_block["LEX_MCP_MODE"] = mode
        server_def["env"] = env_block

    _atomic_write_text(mcp_config_path, json.dumps(config, indent=2) + "\n")
    return True


def apply_ai_update_1_0_0(
    project_root: Path,
    *,
    mcp_config_path: Path | None = None,
    env: Mapping[str, str] | None = None,
    home: Path | None = None,
    server_name: str = LEX_MCP_LOCAL_SERVER_NAME,
    runner: Callable[..., Any] = subprocess.run,
) -> SetupWithAIUpdateResult:
    """Migrate to the unified ``lex_mcp.server`` entry point (1.0.0).

    * Upgrades ``lex-mcp-local`` to the latest published version.
    * Updates the mcp.json server entry to use
      ``python -m lex_mcp.server --mode <mode>`` instead of the legacy
      ``wrapper_mcp.py`` script path.
    * Ensures ``LEX_MCP_MODE`` is present in ``.env`` (defaults to
      ``forward``).
    * Re-copies all embedded directories from both packages.
    * Stops the running MCP server so the new entry point is used on
      next launch.
    """
    env_file_path = (Path(project_root) / ".env").resolve()
    copilot_mcp_path = (
        resolve_github_copilot_mcp_config_path(env=env, home=home)
        if mcp_config_path is None
        else Path(mcp_config_path)
    ).resolve()

    python_executable = resolve_active_python_executable(project_root, env=env)

    remote_mcp_api_key = _read_dotenv_value(env_file_path, "REMOTE_MCP_API_KEY")
    if not remote_mcp_api_key:
        raise SetupWithAIError(
            "REMOTE_MCP_API_KEY not found in .env — cannot upgrade lex-mcp-local."
        )

    install_lex_mcp_local(
        python_executable, remote_mcp_api_key, runner=runner, upgrade=True,
    )

    # Resolve mode: prefer existing .env value, fall back to default.
    mcp_mode = _read_dotenv_value(env_file_path, "LEX_MCP_MODE") or DEFAULT_LEX_MCP_MODE

    # Migrate mcp.json from wrapper_mcp.py → lex_mcp.server, but only
    # if the unified entry point is actually installed.
    if _has_unified_mcp_entry_point(python_executable):
        _update_mcp_server_to_unified_entry(
            copilot_mcp_path, server_name=server_name, mode=mcp_mode,
        )

    # Ensure LEX_MCP_MODE is written to .env.
    if not _read_dotenv_value(env_file_path, "LEX_MCP_MODE"):
        update_env_file(env_file_path, {"LEX_MCP_MODE": mcp_mode})

    # Re-copy embedded directories.
    wrapper_script_path = resolve_wrapper_script_path(python_executable)
    copied: list[Path] = list(
        copy_lex_mcp_local_directories(project_root, wrapper_script_path)
    )
    lex_package_root = resolve_lex_app_package_root(python_executable)
    for dir_name in LEX_APP_EMBEDDED_DIRECTORY_NAMES:
        if dir_name == "docs":
            dest = copy_lex_app_docs_directory(project_root, lex_package_root)
        else:
            src = (lex_package_root / dir_name) if lex_package_root else None
            dest = _copy_directory_into_project_root(
                project_root, src if src and src.is_dir() else None,
            )
        if dest is not None:
            copied.append(dest)

    server_stopped = _stop_lex_mcp_local_server(
        copilot_mcp_path, server_name=server_name,
    )

    return SetupWithAIUpdateResult(
        version="1.0.0",
        env_file_path=env_file_path,
        mcp_config_path=copilot_mcp_path,
        package_upgraded=True,
        artifact_directories_copied=tuple(copied),
        server_restarted=server_stopped,
    )


def apply_ai_update_1_0_1(
    project_root: Path,
    *,
    mcp_config_path: Path | None = None,
    env: Mapping[str, str] | None = None,
    home: Path | None = None,
    server_name: str = LEX_MCP_LOCAL_SERVER_NAME,
    runner: Callable[..., Any] = subprocess.run,
) -> SetupWithAIUpdateResult:
    """Update the ``lex-mcp-local`` package code to the latest version (1.0.1).

    This is a code-only update:
    * Upgrades ``lex-mcp-local`` to the latest published version.
    * Stops the running MCP server so the new code is used on next launch.

    No environment variables, mcp.json structure, or embedded directories
    are changed.
    """
    env_file_path = (Path(project_root) / ".env").resolve()
    copilot_mcp_path = (
        resolve_github_copilot_mcp_config_path(env=env, home=home)
        if mcp_config_path is None
        else Path(mcp_config_path)
    ).resolve()

    python_executable = resolve_active_python_executable(project_root, env=env)

    remote_mcp_api_key = _read_dotenv_value(env_file_path, "REMOTE_MCP_API_KEY")
    if not remote_mcp_api_key:
        raise SetupWithAIError(
            "REMOTE_MCP_API_KEY not found in .env — cannot upgrade lex-mcp-local."
        )

    install_lex_mcp_local(
        python_executable, remote_mcp_api_key, runner=runner, upgrade=True,
    )

    server_stopped = _stop_lex_mcp_local_server(
        copilot_mcp_path, server_name=server_name,
    )

    return SetupWithAIUpdateResult(
        version="1.0.1",
        env_file_path=env_file_path,
        mcp_config_path=copilot_mcp_path,
        package_upgraded=True,
        server_restarted=server_stopped,
    )


# Ordered list of (target_version, migration_function) pairs.
# Each function accepts (project_root, **kwargs) and returns a
# SetupWithAIUpdateResult.  ``apply_ai_update`` runs them in sequence.
_AI_UPDATE_STEPS: list[tuple[str, Callable[..., SetupWithAIUpdateResult]]] = [
    ("0.2.1", apply_ai_update_0_2_1),
    ("0.2.2", apply_ai_update_0_2_2),
    ("1.0.0", apply_ai_update_1_0_0),
    ("1.0.1", apply_ai_update_1_0_1),
]


def apply_ai_update(
    project_root: Path,
    *,
    mcp_config_path: Path | None = None,
    env: Mapping[str, str] | None = None,
    home: Path | None = None,
    server_name: str = LEX_MCP_LOCAL_SERVER_NAME,
) -> list[SetupWithAIUpdateResult]:
    """Run every registered AI-setup migration step and return results."""
    results: list[SetupWithAIUpdateResult] = []
    for _version, step_fn in _AI_UPDATE_STEPS:
        result = step_fn(
            project_root,
            mcp_config_path=mcp_config_path,
            env=env,
            home=home,
            server_name=server_name,
        )
        results.append(result)
    return results


def _build_setup_form_html(
    *,
    state: str,
    project_root: Path,
    env_file_path: Path,
    remote_mcp_url: str = DEFAULT_REMOTE_MCP_URL,
    last_used: SetupWithAILastUsed | None = None,
    device_flow_available: bool = False,
    error_message: str | None = None,
) -> str:
    from lex.tools._setup_ai_templates import render_setup_wizard

    last_used = last_used or SetupWithAILastUsed()
    return render_setup_wizard(
        state=state,
        project_root=project_root,
        env_file_path=env_file_path,
        remote_mcp_url=remote_mcp_url,
        github_token_url=GITHUB_TOKEN_URL,
        server_name=LEX_MCP_LOCAL_SERVER_NAME,
        last_used_mcp_mode=last_used.mcp_mode,
        last_used_prefer_pat=last_used.prefer_pat,
        device_flow_available=device_flow_available,
        initial_error=error_message or "",
    )


def _build_success_html(*, env_file_path: Path, project_root: Path) -> str:
    from lex.tools._setup_ai_templates import render_success_page

    return render_success_page(
        project_root=project_root,
        env_file_path=env_file_path,
        server_name=LEX_MCP_LOCAL_SERVER_NAME,
    )


