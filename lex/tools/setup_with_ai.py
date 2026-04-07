from __future__ import annotations

import html
import json
import os
import queue
import re
import secrets
import shutil
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
from urllib.parse import parse_qs, quote


DEFAULT_REMOTE_MCP_URL = "https://mcp.excellence-cloud.de/mcp"
DEFAULT_REMOTE_MCP_TRANSPORT = "http"
DEFAULT_GEMINI_MODEL = "gemini-2.5-flash"
DEFAULT_GIT_GEMINI_MAX_REPAIR_ATTEMPTS = "3"
DEFAULT_LEX_MCP_PRODUCTION = "false"
GITHUB_FINE_GRAINED_TOKEN_URL = "https://github.com/settings/personal-access-tokens/new"
GITHUB_COPILOT_MCP_FIRST_BOOT_COMPLETED_KEY = "mcp-first-boot-completed"
GITHUB_COPILOT_MCP_SERVERS_CACHE_KEY = "mcp-servers-cache"
GITHUB_COPILOT_STATE_DB_NAME = "copilot-intellij.db"
LEX_MCP_LOCAL_SERVER_NAME = "lex-mcp-local"
LEGACY_LEX_MCP_SERVER_NAMES = ("lex-mcp-wrapper",)
LEX_MCP_LOCAL_INSTALL_COMMAND_SUFFIX = (
    "--no-cache-dir",
    "--extra-index-url",
    "https://pypi.org/simple",
    "lex-mcp-local",
)
_SAFE_UNQUOTED_ENV_VALUE_RE = re.compile(r"^[A-Za-z0-9._:/@+-]*$")
LEGACY_GITHUB_TOKEN_ENV_NAMES = ("COPILOT_GITHUB_TOKEN",)


class SetupWithAIError(RuntimeError):
    pass


@dataclass(frozen=True)
class SetupWithAICredentials:
    github_token: str
    remote_mcp_api_key: str
    gemini_api_key: str


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
) -> list[str]:
    entitlement_token = remote_mcp_api_key.strip()
    if not entitlement_token:
        raise SetupWithAIError("Remote MCP API key is required to install lex-mcp-local.")

    index_url = (
        "https://dl.cloudsmith.io/"
        f"{quote(entitlement_token, safe='')}/"
        "excellence-cloud/lex-mcp-local/python/simple/"
    )
    return [
        str(python_executable),
        "-m",
        "pip",
        "install",
        LEX_MCP_LOCAL_INSTALL_COMMAND_SUFFIX[0],
        "--index-url",
        index_url,
        *LEX_MCP_LOCAL_INSTALL_COMMAND_SUFFIX[1:],
    ]


def install_lex_mcp_local(
    python_executable: str | os.PathLike[str],
    remote_mcp_api_key: str,
    runner=subprocess.run,
) -> list[str]:
    command = build_lex_mcp_local_install_command(python_executable, remote_mcp_api_key)
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
    gemini_api_key: str,
    remote_mcp_url: str = DEFAULT_REMOTE_MCP_URL,
) -> dict[str, str]:
    return {
        "REMOTE_MCP_TRANSPORT": DEFAULT_REMOTE_MCP_TRANSPORT,
        "REMOTE_MCP_URL": remote_mcp_url,
        "GEMINI_MODEL": DEFAULT_GEMINI_MODEL,
        "GIT_GEMINI_MAX_REPAIR_ATTEMPTS": DEFAULT_GIT_GEMINI_MAX_REPAIR_ATTEMPTS,
        "LEX_MCP_PRODUCTION": DEFAULT_LEX_MCP_PRODUCTION,
        "REMOTE_MCP_API_KEY": remote_mcp_api_key,
        "GITHUB_TOKEN": github_token,
        "GEMINI_API_KEY": gemini_api_key,
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


def build_mcp_server_definition(
    python_executable: Path,
    wrapper_script_path: Path,
    github_token: str,
    remote_mcp_api_key: str,
    gemini_api_key: str,
    remote_mcp_url: str = DEFAULT_REMOTE_MCP_URL,
) -> dict:
    return {
        "type": "stdio",
        "command": str(python_executable),
        "args": [str(wrapper_script_path)],
        "env": build_ai_env_values(
            github_token=github_token,
            remote_mcp_api_key=remote_mcp_api_key,
            gemini_api_key=gemini_api_key,
            remote_mcp_url=remote_mcp_url,
        ),
    }


def write_github_copilot_mcp_config(
    mcp_config_path: Path,
    server_definition: Mapping[str, object],
    server_name: str = LEX_MCP_LOCAL_SERVER_NAME,
) -> None:
    config: dict[str, object]
    if mcp_config_path.exists():
        try:
            config = json.loads(mcp_config_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise SetupWithAIError(
                f"GitHub Copilot MCP config is not valid JSON: {mcp_config_path}"
            ) from exc
        if not isinstance(config, dict):
            raise SetupWithAIError(
                f"GitHub Copilot MCP config must contain a JSON object: {mcp_config_path}"
            )
    else:
        config = {}

    servers = config.get("servers", {})
    if not isinstance(servers, dict):
        raise SetupWithAIError(
            f"GitHub Copilot MCP config 'servers' value must be an object: {mcp_config_path}"
        )

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
    startup_timeout_seconds: float = 10.0,
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
        prompts = _read_inventory(
            process,
            request_id=3,
            method="prompts/list",
            result_key="prompts",
            deadline=deadline,
        )
        resources = _read_inventory(
            process,
            request_id=4,
            method="resources/list",
            result_key="resources",
            deadline=deadline,
        )
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
    startup_timeout_seconds: float = 10.0,
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
    gemini_api_key: str,
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
        gemini_api_key=gemini_api_key,
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
    gemini_api_key: str,
    remote_mcp_url: str = DEFAULT_REMOTE_MCP_URL,
    *,
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
        gemini_api_key=gemini_api_key,
        remote_mcp_url=remote_mcp_url,
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
        wrapper_script_path=wrapper_script_path,
        github_token=github_token,
        remote_mcp_api_key=remote_mcp_api_key,
        gemini_api_key=gemini_api_key,
        remote_mcp_url=remote_mcp_url,
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
) -> SetupWithAICredentials:
    state = secrets.token_urlsafe(16)
    result: dict[str, SetupWithAICredentials] = {}
    submitted = threading.Event()
    report = reporter or (lambda message: None)

    class SetupWithAIHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path not in {"", "/"}:
                self.send_error(HTTPStatus.NOT_FOUND)
                return

            body = _build_setup_form_html(
                state=state,
                project_root=project_root,
                env_file_path=env_file_path,
            )
            encoded = body.encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def do_POST(self):
            if self.path != "/submit":
                self.send_error(HTTPStatus.NOT_FOUND)
                return

            content_length = int(self.headers.get("Content-Length", "0"))
            payload = self.rfile.read(content_length).decode("utf-8")
            form_data = parse_qs(payload, keep_blank_values=True)

            if form_data.get("state", [""])[0] != state:
                self.send_error(HTTPStatus.FORBIDDEN, "State mismatch")
                return

            github_token = form_data.get("github_token", [""])[0].strip()
            remote_mcp_api_key = form_data.get("remote_mcp_api_key", [""])[0].strip()
            gemini_api_key = form_data.get("gemini_api_key", [""])[0].strip()

            if not github_token or not remote_mcp_api_key or not gemini_api_key:
                body = _build_setup_form_html(
                    state=state,
                    project_root=project_root,
                    env_file_path=env_file_path,
                    error_message="All three fields are required.",
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
                gemini_api_key=gemini_api_key,
            )
            body = _build_success_html(env_file_path=env_file_path)
            encoded = body.encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)
            submitted.set()

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


def _build_setup_form_html(
    *,
    state: str,
    project_root: Path,
    env_file_path: Path,
    error_message: str | None = None,
) -> str:
    error_block = ""
    if error_message:
        error_block = (
            f'<div class="error">{html.escape(error_message)}</div>'
        )

    permissions = [
        "Repository access: All repositories",
        "Actions: Read and write",
        "Administration: Read and write",
        "Artifact metadata: Read and write",
        "Codespaces: Read and write",
        "Commit statuses: Read and write",
        "Contents: Read and write",
        "Issues: Read and write",
        "Metadata: Read-only (required)",
        "Pages: Read and write",
        "Pull requests: Read and write",
        "Webhooks: Read and write",
        "Workflows: Read and write",
        "Copilot Chat: Read-only",
        "Copilot Editor Context: Read-only",
        "Copilot Requests: Read-only",
        "Models: Read-only",
    ]
    permission_items = "".join(
        f"<li>{html.escape(permission)}</li>" for permission in permissions
    )

    return f"""<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>LEX AI Setup</title>
    <style>
      :root {{
        color-scheme: light;
        --bg: #f5f1e8;
        --card: #fffdfa;
        --text: #1e1b18;
        --muted: #6c6258;
        --line: #d7c8b5;
        --accent: #a14d1c;
        --accent-strong: #7e350d;
        --error: #8b1e1e;
      }}
      * {{
        box-sizing: border-box;
      }}
      body {{
        margin: 0;
        font-family: "Avenir Next", "Segoe UI", sans-serif;
        color: var(--text);
        background:
          radial-gradient(circle at top right, rgba(161, 77, 28, 0.14), transparent 26rem),
          linear-gradient(180deg, #f8f3ea 0%, var(--bg) 100%);
      }}
      .shell {{
        max-width: 68rem;
        margin: 0 auto;
        padding: 2rem 1.25rem 3rem;
      }}
      .hero {{
        background: linear-gradient(135deg, rgba(161, 77, 28, 0.10), rgba(255, 255, 255, 0.92));
        border: 1px solid rgba(161, 77, 28, 0.25);
        border-radius: 1.5rem;
        padding: 1.5rem;
        box-shadow: 0 18px 40px rgba(73, 48, 26, 0.08);
      }}
      .hero h1 {{
        margin: 0 0 0.5rem;
        font-size: clamp(1.9rem, 3vw, 3rem);
      }}
      .hero p {{
        margin: 0.45rem 0;
        color: var(--muted);
        line-height: 1.5;
      }}
      .grid {{
        display: grid;
        grid-template-columns: 1.2fr 0.9fr;
        gap: 1.25rem;
        margin-top: 1.25rem;
      }}
      .panel {{
        background: var(--card);
        border: 1px solid var(--line);
        border-radius: 1.25rem;
        padding: 1.25rem;
        box-shadow: 0 10px 28px rgba(73, 48, 26, 0.06);
      }}
      .eyebrow {{
        margin: 0 0 0.6rem;
        font-size: 0.78rem;
        font-weight: 700;
        letter-spacing: 0.14em;
        text-transform: uppercase;
        color: var(--accent);
      }}
      h2 {{
        margin: 0 0 0.75rem;
        font-size: 1.3rem;
      }}
      p, li {{
        line-height: 1.55;
      }}
      ul {{
        margin: 0.75rem 0 0;
        padding-left: 1.2rem;
      }}
      .meta {{
        display: grid;
        gap: 0.4rem;
        margin-top: 1rem;
        padding-top: 1rem;
        border-top: 1px solid var(--line);
        color: var(--muted);
        font-size: 0.95rem;
      }}
      a.button, button {{
        appearance: none;
        border: 0;
        border-radius: 999px;
        background: var(--accent);
        color: #fff;
        text-decoration: none;
        font: inherit;
        font-weight: 700;
        cursor: pointer;
        padding: 0.85rem 1.1rem;
        transition: transform 120ms ease, background 120ms ease;
      }}
      a.button:hover, button:hover {{
        background: var(--accent-strong);
        transform: translateY(-1px);
      }}
      form {{
        display: grid;
        gap: 1rem;
      }}
      label {{
        display: grid;
        gap: 0.4rem;
        font-weight: 600;
      }}
      input {{
        width: 100%;
        padding: 0.85rem 0.95rem;
        border-radius: 0.9rem;
        border: 1px solid var(--line);
        font: inherit;
        background: #fff;
      }}
      input:focus {{
        outline: 2px solid rgba(161, 77, 28, 0.2);
        border-color: var(--accent);
      }}
      .hint {{
        color: var(--muted);
        font-size: 0.92rem;
        margin: 0;
      }}
      .error {{
        border: 1px solid rgba(139, 30, 30, 0.25);
        background: rgba(139, 30, 30, 0.08);
        color: var(--error);
        border-radius: 1rem;
        padding: 0.85rem 1rem;
      }}
      @media (max-width: 860px) {{
        .grid {{
          grid-template-columns: 1fr;
        }}
      }}
    </style>
  </head>
  <body>
    <main class="shell">
      <section class="hero">
        <p class="eyebrow">LEX AI Setup</p>
        <h1>Connect GitHub Copilot to your LEX MCP setup.</h1>
        <p>This flow will store the required secrets in <code>{html.escape(str(env_file_path))}</code> and register <code>{html.escape(LEX_MCP_LOCAL_SERVER_NAME)}</code> in GitHub Copilot's <code>mcp.json</code>.</p>
        <p>Project root: <code>{html.escape(str(project_root))}</code></p>
      </section>

      <section class="grid">
        <article class="panel">
          <p class="eyebrow">Documentation</p>
          <h2>Create a fine-grained GitHub token</h2>
          <p>Open GitHub's fine-grained token page, create a new token, pick the right resource owner, then set the repository and account permissions shown below.</p>
          <p><a class="button" href="{html.escape(GITHUB_FINE_GRAINED_TOKEN_URL)}" target="_blank" rel="noreferrer">Open GitHub token page</a></p>
          <ul>
            {permission_items}
          </ul>
          <div class="meta">
            <div>The token must have Copilot-related account permissions enabled, otherwise Copilot Extensions access will fail.</div>
                        <div>Use the remote MCP API key that both authenticates your hosted MCP server and unlocks the Cloudsmith package install for <code>lex-mcp-local</code>.</div>
            <div>Provide the Gemini API key that should be exposed to the MCP wrapper as <code>GEMINI_API_KEY</code>.</div>
          </div>
        </article>

        <section class="panel">
          <p class="eyebrow">Credentials</p>
          <h2>Save tokens to this project</h2>
          {error_block}
          <form method="post" action="/submit">
            <input type="hidden" name="state" value="{html.escape(state)}">

            <label>
              GitHub token
              <input type="password" name="github_token" autocomplete="off" required>
            </label>
            <p class="hint">Paste the fine-grained GitHub token you just created.</p>

            <label>
              Remote MCP API key
              <input type="password" name="remote_mcp_api_key" autocomplete="off" required>
            </label>
                        <p class="hint">Paste the API key used both for the hosted MCP endpoint and the entitlement-gated <code>lex-mcp-local</code> package install.</p>

            <label>
              Gemini API key
              <input type="password" name="gemini_api_key" autocomplete="off" required>
            </label>
            <p class="hint">Paste the Gemini API key that should be available to the MCP server.</p>

            <button type="submit">Save and finish setup</button>
          </form>
        </section>
      </section>
    </main>
  </body>
</html>
"""


def _build_success_html(*, env_file_path: Path) -> str:
    return f"""<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
        <title>LEX AI Setup In Progress</title>
    <style>
      body {{
        margin: 0;
        min-height: 100vh;
        display: grid;
        place-items: center;
        background:
          radial-gradient(circle at top left, rgba(82, 141, 90, 0.16), transparent 20rem),
          linear-gradient(180deg, #eef5ec 0%, #f7fbf6 100%);
        color: #1b261b;
        font-family: "Avenir Next", "Segoe UI", sans-serif;
      }}
      .card {{
        width: min(38rem, calc(100vw - 2rem));
        background: rgba(255, 255, 255, 0.94);
        border: 1px solid rgba(82, 141, 90, 0.25);
        border-radius: 1.4rem;
        padding: 1.6rem;
        box-shadow: 0 18px 42px rgba(47, 71, 50, 0.10);
      }}
      h1 {{
        margin: 0 0 0.75rem;
      }}
      p {{
        line-height: 1.6;
      }}
      code {{
        font-family: "SFMono-Regular", "Consolas", monospace;
      }}
    </style>
  </head>
  <body>
    <section class="card">
            <h1>Credentials received</h1>
            <p>Return to the terminal while LEX installs <code>lex-mcp-local</code> and finishes writing <code>{html.escape(str(env_file_path))}</code> plus the GitHub Copilot <code>mcp.json</code> entry for <code>{html.escape(LEX_MCP_LOCAL_SERVER_NAME)}</code>.</p>
            <p>You can close this tab after the terminal prints the final setup success message.</p>
    </section>
  </body>
</html>
"""
