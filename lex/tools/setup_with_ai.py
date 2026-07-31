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
from typing import Any, Callable, Iterable, Mapping
from urllib.parse import parse_qs

DEFAULT_REMOTE_MCP_URL = "https://mcp.excellence-cloud.de/mcp"
DEFAULT_REMOTE_MCP_TRANSPORT = "http"
DEFAULT_LEX_MCP_PRODUCTION = "false"
DEFAULT_LEX_MCP_MODE = "forward"
SUPPORTED_MCP_MODES: tuple[str, ...] = (
    "forward",
    "backward",
    "edit",
    "review",
    "mvp_generator",
    "mvp_completion",
)
MCP_MODE_CARD_DEFS: tuple[dict[str, str], ...] = (
    {
        "value": "forward",
        "title": "New Project",
        "desc": "Full planning and implementation flow from scratch.",
        "tone": "forward",
        "icon_html": (
            '<svg viewBox="0 0 24 24" fill="none" stroke="#24b6bb" '
            'stroke-width="2" stroke-linecap="round" stroke-linejoin="round">'
            '<line x1="12" y1="5" x2="12" y2="19"/>'
            '<polyline points="19 12 12 19 5 12"/>'
            '</svg>'
        ),
    },
    {
        "value": "backward",
        "title": "Documentation",
        "desc": "Reverse-map an existing codebase and generate docs.",
        "tone": "backward",
        "icon_html": (
            '<svg viewBox="0 0 24 24" fill="none" stroke="#283067" '
            'stroke-width="2" stroke-linecap="round" stroke-linejoin="round">'
            '<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/>'
            '<polyline points="14 2 14 8 20 8"/>'
            '<line x1="16" y1="13" x2="8" y2="13"/>'
            '<line x1="16" y1="17" x2="8" y2="17"/>'
            '</svg>'
        ),
    },
    {
        "value": "edit",
        "title": "Edit",
        "desc": "Targeted code modifications in an existing project.",
        "tone": "edit",
        "icon_html": "&#x270F;&#xFE0F;",
    },
    {
        "value": "review",
        "title": "Review",
        "desc": "Audit, validate, and improve quality before merge.",
        "tone": "review",
        "icon_html": "&#x1F50D;",
    },
    {
        "value": "mvp_generator",
        "title": "MVP Generator",
        "desc": "Generate a lean, production-minded MVP baseline.",
        "tone": "mvp",
        "icon_html": "&#x1F4A1;",
    },
    {
        "value": "mvp_completion",
        "title": "MVP Completion",
        "desc": "Finalize and polish an MVP to production readiness.",
        "tone": "mvp_completion",
        "icon_html": "&#x2705;",
    },
)
#: Agentic environments the setup flow can onboard. Mirrors the registry in
#: ``lex_mcp.environments`` — that module is authoritative, and this table is
#: only what the browser form renders (plus the fallback used when an older
#: ``lex-mcp-local`` is installed and the registry is unavailable).
DEFAULT_AI_ENVIRONMENT = "pycharm-copilot"
AI_ENVIRONMENT_CARD_DEFS: tuple[dict[str, str], ...] = (
    {
        "value": "pycharm-copilot",
        "title": "PyCharm / JetBrains",
        "desc": "GitHub Copilot inside any JetBrains IDE.",
        "icon_html": "&#x1F9E9;",
    },
    {
        "value": "vscode-copilot",
        "title": "VS Code",
        "desc": "GitHub Copilot Chat with workspace MCP config.",
        "icon_html": "&#x1F4D8;",
    },
    {
        "value": "cursor",
        "title": "Cursor",
        "desc": "Cursor Agent with project rules and commands.",
        "icon_html": "&#x1F5B1;&#xFE0F;",
    },
    {
        "value": "claude-code",
        "title": "Claude Code",
        "desc": "Native subagents, slash commands, and CLAUDE.md.",
        "icon_html": "&#x1F916;",
    },
    {
        "value": "codex",
        "title": "OpenAI Codex",
        "desc": "AGENTS.md plus [mcp_servers] in config.toml.",
        "icon_html": "&#x26A1;",
    },
    {
        "value": "copilot-cli",
        "title": "Copilot CLI",
        "desc": "Terminal Copilot sharing the .github payload.",
        "icon_html": "&#x1F4BB;",
    },
    {
        "value": "windsurf",
        "title": "Windsurf",
        "desc": "Cascade rules and workflows.",
        "icon_html": "&#x1F30A;",
    },
)
SUPPORTED_AI_ENVIRONMENTS: tuple[str, ...] = tuple(
    card["value"] for card in AI_ENVIRONMENT_CARD_DEFS
)
#: Mirror of the alias table in ``lex_mcp.environments``. The registry is
#: authoritative; this exists only so ``--environment claude`` still resolves
#: correctly when the registry cannot be imported. Without it the fallback
#: silently discarded every alias and substituted the default, which onboarded
#: the wrong tool while reporting success.
AI_ENVIRONMENT_ALIASES: dict[str, str] = {
    "pycharm": "pycharm-copilot",
    "jetbrains": "pycharm-copilot",
    "jetbrains-copilot": "pycharm-copilot",
    "intellij": "pycharm-copilot",
    "copilot": "pycharm-copilot",
    "vscode": "vscode-copilot",
    "vs-code": "vscode-copilot",
    "code": "vscode-copilot",
    "vscode-github-copilot": "vscode-copilot",
    "gh-copilot": "copilot-cli",
    "copilot-terminal": "copilot-cli",
    "cursor-ide": "cursor",
    "cursor-agent": "cursor",
    "claude": "claude-code",
    "claudecode": "claude-code",
    "anthropic-claude-code": "claude-code",
    "openai-codex": "codex",
    "codex-cli": "codex",
    "gpt-codex": "codex",
    "codeium": "windsurf",
    "windsurf-ide": "windsurf",
    "cascade": "windsurf",
}

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
LEX_MCP_LOCAL_SOURCE_DIRECTORY = Path(
    "/Users/melihsunbul/LUND_IT/lex-mcp-local"
)
_SAFE_UNQUOTED_ENV_VALUE_RE = re.compile(r"^[A-Za-z0-9._:/@+-]*$")
LEGACY_GITHUB_TOKEN_ENV_NAMES = ("COPILOT_GITHUB_TOKEN",)

# Minimum lex-mcp-local version that ships the unified lex_mcp.server entry
# point and backward-mode support.
MINIMUM_DUAL_MODE_VERSION = "1.0.0"


class SetupWithAIError(RuntimeError):
    pass


@dataclass(frozen=True)
class SetupWithAICredentials:
    github_token: str
    remote_mcp_api_key: str
    mcp_mode: str = "forward"
    environments: tuple[str, ...] = (DEFAULT_AI_ENVIRONMENT,)


@dataclass(frozen=True)
class SetupWithAIArtifacts:
    env_file_path: Path
    #: Primary MCP config path. Kept singular for backward compatibility with
    #: callers written when GitHub Copilot in PyCharm was the only target;
    #: see ``mcp_config_paths`` for the full set.
    mcp_config_path: Path
    wrapper_script_path: Path
    python_executable: Path
    server_name: str = LEX_MCP_LOCAL_SERVER_NAME
    github_directory_path: Path | None = None
    docs_directory_path: Path | None = None
    environments: tuple[str, ...] = ()
    mcp_config_paths: tuple[Path, ...] = ()
    payload_files_written: tuple[str, ...] = ()
    environment_notes: tuple[str, ...] = ()


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


def normalize_mcp_mode(
    mode: str | None,
    *,
    default: str = DEFAULT_LEX_MCP_MODE,
) -> str:
    candidate = str(mode or "").strip().lower()
    if candidate in SUPPORTED_MCP_MODES:
        return candidate
    return default


def _environment_registry():
    """Return ``lex_mcp.environments``, or ``None`` when it is unavailable.

    The registry ships with ``lex-mcp-local``, which ``lex setup-with-ai``
    installs before it configures anything. Older releases predate it, so
    every call site degrades to the Copilot-only behaviour rather than
    failing.
    """
    try:
        import importlib

        return importlib.import_module("lex_mcp.environments")
    except Exception:
        return None


def _onboarding_module():
    """Return ``lex_mcp.ai_onboarding``, or ``None`` when unavailable.

    Note this is imported *in this process*, not through the project
    interpreter, so a ``lex`` launched from a different environment than the
    project virtualenv will not find it.
    """
    try:
        import importlib

        return importlib.import_module("lex_mcp.ai_onboarding")
    except Exception:
        return None


def normalize_ai_environments(
    environments: str | Iterable[str] | None,
    *,
    default: Iterable[str] = (DEFAULT_AI_ENVIRONMENT,),
    strict: bool = True,
) -> tuple[str, ...]:
    """Normalise environment names, preferring the lex-mcp-local registry.

    Accepts a comma/space separated string or an iterable, resolves aliases
    (``vscode`` -> ``vscode-copilot``), expands ``all``, and drops duplicates
    while preserving order. Empty input falls back to *default*.

    With *strict* (the default) an unrecognised name raises
    :class:`SetupWithAIError`. Silently dropping it and falling back to the
    default is far worse than failing: it onboards a tool the user did not ask
    for and still reports success. Pass ``strict=False`` only for values read
    back from persisted config, where tolerating drift is correct.
    """
    registry = _environment_registry()
    if registry is not None:
        try:
            return tuple(
                registry.resolve_environment_keys(environments, default=tuple(default))
            )
        except Exception as exc:
            if strict:
                raise SetupWithAIError(str(exc)) from exc

    if environments is None:
        items: list[str] = []
    elif isinstance(environments, str):
        items = [chunk for chunk in re.split(r"[,\s]+", environments) if chunk]
    else:
        items = [str(chunk) for chunk in environments if str(chunk).strip()]
    if not items:
        items = list(default)

    out: list[str] = []
    unknown: list[str] = []
    for item in items:
        candidate = item.strip().lower().replace("_", "-").replace(" ", "-")
        if candidate == "all":
            for key in SUPPORTED_AI_ENVIRONMENTS:
                if key not in out:
                    out.append(key)
            continue
        resolved = (
            candidate
            if candidate in SUPPORTED_AI_ENVIRONMENTS
            else AI_ENVIRONMENT_ALIASES.get(candidate)
        )
        if resolved is None:
            unknown.append(item)
            continue
        if resolved not in out:
            out.append(resolved)

    if unknown and strict:
        raise SetupWithAIError(
            f"Unknown agentic environment(s): {', '.join(unknown)}. "
            f"Choose from: {', '.join(SUPPORTED_AI_ENVIRONMENTS)} (or 'all'). "
            "Run `lex setup-with-ai --list-environments` for the full list "
            "with aliases."
        )
    return tuple(out) or tuple(default) or (DEFAULT_AI_ENVIRONMENT,)


def suggest_ai_environments(project_root: Path) -> tuple[str, ...]:
    """Environments to pre-select in the setup form for this machine/project."""
    try:
        import importlib

        onboarding = importlib.import_module("lex_mcp.ai_onboarding")
        suggested = tuple(onboarding.suggest_environments(project_root))
        if suggested:
            return suggested
    except Exception:
        pass
    return (DEFAULT_AI_ENVIRONMENT,)


def describe_ai_environments() -> list[dict[str, object]]:
    """Registry summary for CLI help and the dashboard; empty when unavailable."""
    registry = _environment_registry()
    if registry is None:
        return [
            {
                "key": card["value"],
                "display_name": card["title"],
                "summary": card["desc"],
            }
            for card in AI_ENVIRONMENT_CARD_DEFS
        ]
    try:
        return registry.describe_environments()
    except Exception:
        return []


def build_lex_mcp_local_install_command(
    python_executable: str | os.PathLike[str],
    remote_mcp_api_key: str,
    *,
    upgrade: bool = False,
) -> list[str]:
    """Build the editable install command for the local lex-mcp checkout.

    ``remote_mcp_api_key`` remains part of the public signature because setup
    also uses it to authenticate the hosted MCP runtime.  Local package
    installation itself must not send that secret to a package index.
    """
    del remote_mcp_api_key

    cmd = [
        str(python_executable),
        "-m",
        "pip",
        "install",
    ]
    if upgrade:
        cmd.append("--upgrade")
    cmd.extend(["--editable", str(LEX_MCP_LOCAL_SOURCE_DIRECTORY)])
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
    destination = (project_root_resolved / source_directory.name).resolve()
    if source_directory == destination:
        # True self-copy (source IS the destination). Anything else — including
        # an editable install where ``lex/docs`` lives under the project root —
        # must still refresh ``<project_root>/docs`` from the package source.
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
    environments: str | Iterable[str] | None = None,
) -> dict[str, str]:
    normalized_mode = normalize_mcp_mode(mcp_mode)
    values = {
        "REMOTE_MCP_TRANSPORT": DEFAULT_REMOTE_MCP_TRANSPORT,
        "REMOTE_MCP_URL": remote_mcp_url,
        "LEX_MCP_PRODUCTION": DEFAULT_LEX_MCP_PRODUCTION,
        "REMOTE_MCP_API_KEY": remote_mcp_api_key,
        "GITHUB_TOKEN": github_token,
        "LEX_MCP_MODE": normalized_mode,
        "LEX_MCP_ANALYTICS_BACKEND": "remote",
    }
    if environments is not None:
        values["LEX_AI_ENVIRONMENTS"] = ",".join(
            normalize_ai_environments(environments)
        )
    return values


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
    normalized_mode = normalize_mcp_mode(mcp_mode)
    if _has_unified_mcp_entry_point(python_executable):
        return ["-m", "lex_mcp.server", "--mode", normalized_mode]
    wrapper_path = resolve_wrapper_script_path(python_executable)
    return [str(wrapper_path), normalized_mode]


def build_mcp_server_definition(
    python_executable: Path,
    github_token: str,
    remote_mcp_api_key: str,
    remote_mcp_url: str = DEFAULT_REMOTE_MCP_URL,
    mcp_mode: str = DEFAULT_LEX_MCP_MODE,
    environments: str | Iterable[str] | None = None,
) -> dict:
    """Build the canonical stdio server definition.

    ``type`` is included because the VS Code / JetBrains Copilot schema wants
    it; writers for formats that do not (Cursor, Claude Code, Codex TOML)
    strip it when they serialise.
    """
    return {
        "type": "stdio",
        "command": str(python_executable),
        "args": resolve_mcp_server_args(python_executable, mcp_mode),
        "env": build_ai_env_values(
            github_token=github_token,
            remote_mcp_api_key=remote_mcp_api_key,
            remote_mcp_url=remote_mcp_url,
            mcp_mode=mcp_mode,
            environments=environments,
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


#: Environment-neutral name for the stdio probe. The ``_for_pycharm`` spelling
#: is kept as an alias because external callers (and older lex-mcp-local
#: releases) import it; the handshake it performs is the standard MCP one that
#: every client uses, not something PyCharm-specific.
def probe_lex_mcp_local_server(
    project_root: Path,
    python_executable: Path,
    wrapper_script_path: Path,
    env_values: Mapping[str, str],
    base_env: Mapping[str, str] | None = None,
    startup_timeout_seconds: float = 30.0,
    server_name: str = LEX_MCP_LOCAL_SERVER_NAME,
) -> SetupWithAIMCPProbeResult:
    """Run a full ``initialize`` + inventory handshake against the server."""
    return probe_lex_mcp_local_server_for_pycharm(
        project_root=project_root,
        python_executable=python_executable,
        wrapper_script_path=wrapper_script_path,
        env_values=env_values,
        base_env=base_env,
        startup_timeout_seconds=startup_timeout_seconds,
        server_name=server_name,
    )


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
    environments: str | Iterable[str] | None = None,
    python_executable: Path | None = None,
    mcp_config_path: Path | None = None,
    env: Mapping[str, str] | None = None,
    home: Path | None = None,
    verify_server: bool = True,
) -> SetupWithAIArtifacts:
    """Write credentials, register the MCP server, and deliver the payload.

    Registration and payload delivery are performed for every environment in
    *environments* (defaulting to GitHub Copilot in PyCharm) through
    ``lex_mcp.ai_onboarding``, which owns the per-environment config formats
    and asset layouts. When that module is unavailable — an older
    ``lex-mcp-local`` — the original Copilot-only path is used instead, so
    setup still completes.
    """
    project_root_path = Path(project_root)
    selected_environments = normalize_ai_environments(environments)
    python_path = (
        resolve_active_python_executable(project_root_path, env=env)
        if python_executable is None
        else Path(os.path.abspath(python_executable))
    )
    wrapper_script_path = resolve_wrapper_script_path(python_path)
    docs_directory_path = copy_lex_app_docs_directory(
        project_root_path,
        resolve_lex_app_package_root(python_path),
    )
    env_values = build_ai_env_values(
        github_token=github_token,
        remote_mcp_api_key=remote_mcp_api_key,
        remote_mcp_url=remote_mcp_url,
        mcp_mode=mcp_mode,
        environments=selected_environments,
    )

    env_file_path = (project_root_path / ".env").resolve()
    update_env_file(env_file_path, env_values)

    server_definition = build_mcp_server_definition(
        python_executable=python_path,
        github_token=github_token,
        remote_mcp_api_key=remote_mcp_api_key,
        remote_mcp_url=remote_mcp_url,
        mcp_mode=mcp_mode,
        environments=selected_environments,
    )

    # The JetBrains Copilot config is always resolved so the returned
    # ``mcp_config_path`` keeps its historic meaning for existing callers.
    copilot_mcp_path = (
        resolve_github_copilot_mcp_config_path(env=env, home=home)
        if mcp_config_path is None
        else Path(mcp_config_path)
    ).resolve()

    config_paths: list[Path] = []
    payload_files: list[str] = []
    notes: list[str] = []
    github_directory_path: Path | None = None

    onboarding = _onboarding_module()
    onboarding_error = (
        None if onboarding is not None else "lex_mcp.ai_onboarding is not importable"
    )

    # The legacy fallback can only configure GitHub Copilot. Downgrading to it
    # silently would leave a user who asked for --environment claude-code with
    # a Copilot-only setup and a "Setup complete" message, which is what
    # happened before this guard existed.
    if onboarding is None and set(selected_environments) != {DEFAULT_AI_ENVIRONMENT}:
        raise SetupWithAIError(
            "Cannot onboard "
            f"{', '.join(selected_environments)}: the agentic-environment "
            "registry could not be imported from lex-mcp-local in the "
            f"interpreter running this command ({sys.executable}).\n"
            f"  Import error: {onboarding_error}\n"
            "Only 'pycharm-copilot' can be configured without it. Fix one of:\n"
            "  - run `lex` from the virtual environment that has lex-mcp-local "
            "installed (the same one shown as 'Using interpreter' above);\n"
            "  - upgrade lex-mcp-local to a release that ships "
            "lex_mcp.ai_onboarding."
        )

    if onboarding is not None:
        result = onboarding.onboard_project(
            project_root_path,
            mode=normalize_mcp_mode(mcp_mode),
            environments=selected_environments,
            server_definition=server_definition,
            home=home,
            env=env,
        )
        # Report every config we actually own, not only the ones that changed:
        # on an idempotent re-run nothing is written, and reporting an empty
        # list used to fall through to the JetBrains path below even when that
        # environment was never selected.
        for config in result.configs:
            if config.skipped:
                continue
            config_paths.append(Path(config.path))
        payload_files = list(result.files_written)
        notes = list(result.notes)
        errors = [
            error
            for payload in result.payloads
            for error in payload.errors
        ] + [config.error for config in result.configs if config.error]
        if errors:
            raise SetupWithAIError(
                "Could not complete environment onboarding: "
                + "; ".join(str(error) for error in errors[:5])
            )
        github_dir = project_root_path.resolve() / ".github"
        if github_dir.is_dir():
            github_directory_path = github_dir
    else:
        # Legacy path: Copilot only, verbatim directory copy.
        github_directory_path = copy_lex_mcp_local_github_directory(
            project_root_path,
            wrapper_script_path,
        )
        write_github_copilot_mcp_config(copilot_mcp_path, server_definition)
        config_paths.append(copilot_mcp_path)

    if not config_paths:
        config_paths.append(copilot_mcp_path)

    if verify_server:
        verify_lex_mcp_local_server_starts(
            project_root=project_root_path,
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
        environments=selected_environments,
        mcp_config_paths=tuple(config_paths),
        payload_files_written=tuple(payload_files),
        environment_notes=tuple(notes),
    )


def launch_setup_with_ai_form(
    project_root: Path,
    env_file_path: Path,
    reporter: Callable[[str], None] | None = None,
    timeout_seconds: int = 900,
    suggested_environments: Iterable[str] | None = None,
) -> SetupWithAICredentials:
    state = secrets.token_urlsafe(16)
    result: dict[str, SetupWithAICredentials] = {}
    submitted = threading.Event()
    report = reporter or (lambda message: None)
    preselected = normalize_ai_environments(
        suggested_environments
        if suggested_environments is not None
        else suggest_ai_environments(Path(project_root))
    )

    class SetupWithAIHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path not in {"", "/"}:
                self.send_error(HTTPStatus.NOT_FOUND)
                return

            body = _build_setup_form_html(
                state=state,
                project_root=project_root,
                env_file_path=env_file_path,
                selected_environments=preselected,
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
            mcp_mode = normalize_mcp_mode(
                form_data.get("mcp_mode", [DEFAULT_LEX_MCP_MODE])[0],
            )
            # Not strict: a malformed POST must not raise inside the request
            # handler. The form only ever submits registry keys.
            selected_environments = normalize_ai_environments(
                form_data.get("ai_environments", []),
                default=preselected,
                strict=False,
            )

            if not github_token or not remote_mcp_api_key:
                body = _build_setup_form_html(
                    state=state,
                    project_root=project_root,
                    env_file_path=env_file_path,
                    selected_environments=selected_environments,
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
                environments=selected_environments,
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


# ---------------------------------------------------------------------------
# ai-update: the actual migration steps and public entry point live in
# ``lex_mcp.ai_update`` (shipped by lex-mcp-local) so new steps can ship
# without a lex-app release. This module still exposes ``_read_dotenv_value``
# because ai_dashboard / verify_ai_assets and other lex-app callers rely on it.
# ---------------------------------------------------------------------------


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


def _build_setup_form_html(
    *,
    state: str,
    project_root: Path,
    env_file_path: Path,
    selected_environments: Iterable[str] | None = None,
    error_message: str | None = None,
) -> str:
    error_block = ""
    if error_message:
        error_block = (
            f'<div class="error">{html.escape(error_message)}</div>'
        )
    selected_mode = DEFAULT_LEX_MCP_MODE
    chosen_environments = set(
        normalize_ai_environments(
            selected_environments
            if selected_environments is not None
            else (DEFAULT_AI_ENVIRONMENT,)
        )
    )
    environment_cards = "".join(
        (
            f'<label class="env-card'
            f'{" selected" if card["value"] in chosen_environments else ""}" '
            f'data-env="{card["value"]}">'
            f'<input type="checkbox" name="ai_environments" value="{card["value"]}" '
            f'{"checked" if card["value"] in chosen_environments else ""}>'
            f'<div class="env-icon">{card["icon_html"]}</div>'
            f'<div class="env-title">{html.escape(card["title"])}</div>'
            f'<p class="env-desc">{html.escape(card["desc"])}</p>'
            '<div class="env-check">'
            '<svg viewBox="0 0 24 24" fill="none" stroke="#fff" stroke-width="3" '
            'stroke-linecap="round" stroke-linejoin="round">'
            '<polyline points="20 6 9 17 4 12"/></svg>'
            '</div>'
            '</label>'
        )
        for card in AI_ENVIRONMENT_CARD_DEFS
    )
    mode_cards = "".join(
        (
            f'<label class="mode-card {card["tone"]}'
            f'{" selected" if card["value"] == selected_mode else ""}" '
            f'data-mode="{card["value"]}">'
            f'<input type="radio" name="mcp_mode_select" value="{card["value"]}" '
            f'{"checked" if card["value"] == selected_mode else ""}>'
            f'<div class="mode-icon">{card["icon_html"]}</div>'
            f'<div class="mode-title">{html.escape(card["title"])}</div>'
            f'<p class="mode-desc">{html.escape(card["desc"])}</p>'
            '<div class="mode-check">'
            '<svg viewBox="0 0 24 24" fill="none" stroke="#fff" stroke-width="3" '
            'stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"/></svg>'
            '</div>'
            '</label>'
        )
        for card in MCP_MODE_CARD_DEFS
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
        --bg: #f0f4f8;
        --card: #ffffff;
        --text: #1a1a2e;
        --muted: #5a6278;
        --line: #d0d7e2;
        --blue: #283067;
        --blue-strong: #1b2050;
        --teal: #24b6bb;
        --error: #c0392b;
      }}
      * {{
        box-sizing: border-box;
      }}
      body {{
        margin: 0;
        font-family: "Segoe UI", "Avenir Next", system-ui, sans-serif;
        color: var(--text);
        background: var(--bg);
      }}
      .shell {{
        max-width: 68rem;
        margin: 0 auto;
        padding: 2rem 1.25rem 3rem;
      }}
      .hero {{
        background: var(--card);
        border: 1px solid var(--line);
        border-radius: 1rem;
        padding: 1.75rem 1.5rem;
        box-shadow: 0 2px 12px rgba(40, 48, 103, 0.06);
        display: flex;
        align-items: center;
        gap: 1.5rem;
      }}
      .hero-logo {{
        flex-shrink: 0;
      }}
      .hero-logo svg {{
        height: 52px;
        width: auto;
      }}
      .hero-text {{
        flex: 1;
        min-width: 0;
      }}
      .hero h1 {{
        margin: 0 0 0.4rem;
        font-size: clamp(1.5rem, 2.5vw, 2rem);
        color: var(--blue);
      }}
      .hero p {{
        margin: 0.3rem 0;
        color: var(--muted);
        line-height: 1.5;
        font-size: 0.95rem;
      }}
      .hero code {{
        background: var(--bg);
        padding: 0.15em 0.4em;
        border-radius: 4px;
        font-size: 0.88em;
        font-family: "SFMono-Regular", "Consolas", "Liberation Mono", monospace;
      }}
      .grid {{
        display: grid;
        grid-template-columns: 1fr;
        gap: 1.25rem;
        margin-top: 1.25rem;
      }}
      .grid-cols {{
        display: grid;
        grid-template-columns: 1.15fr 0.85fr;
        gap: 1.25rem;
      }}
      .panel {{
        background: var(--card);
        border: 1px solid var(--line);
        border-radius: 1rem;
        padding: 1.5rem;
        box-shadow: 0 2px 12px rgba(40, 48, 103, 0.06);
      }}
      .eyebrow {{
        margin: 0 0 0.6rem;
        font-size: 0.72rem;
        font-weight: 700;
        letter-spacing: 0.14em;
        text-transform: uppercase;
        color: var(--teal);
      }}
      h2 {{
        margin: 0 0 0.75rem;
        font-size: 1.25rem;
        color: var(--blue);
      }}
      p, li {{
        line-height: 1.55;
      }}
      ul {{
        margin: 0.75rem 0 0;
        padding-left: 1.2rem;
      }}
      li {{
        padding: 0.1rem 0;
        color: var(--muted);
        font-size: 0.93rem;
      }}
      li::marker {{
        color: var(--teal);
      }}
      .meta {{
        display: grid;
        gap: 0.4rem;
        margin-top: 1rem;
        padding-top: 1rem;
        border-top: 1px solid var(--line);
        color: var(--muted);
        font-size: 0.92rem;
      }}
      .meta code {{
        background: var(--bg);
        padding: 0.1em 0.35em;
        border-radius: 4px;
        font-size: 0.88em;
        font-family: "SFMono-Regular", "Consolas", "Liberation Mono", monospace;
      }}
      a.button, button {{
        display: inline-block;
        appearance: none;
        border: 0;
        border-radius: 8px;
        background: var(--blue);
        color: #fff;
        text-decoration: none;
        font: inherit;
        font-size: 0.95rem;
        font-weight: 600;
        cursor: pointer;
        padding: 0.75rem 1.25rem;
        transition: background 120ms ease, box-shadow 120ms ease;
      }}
      a.button:hover, button:hover {{
        background: var(--blue-strong);
        box-shadow: 0 4px 14px rgba(40, 48, 103, 0.18);
      }}
      .mode-section {{
        margin-top: 1.25rem;
      }}
      .mode-section .eyebrow {{
        margin: 0 0 0.6rem;
        font-size: 0.72rem;
        font-weight: 700;
        letter-spacing: 0.14em;
        text-transform: uppercase;
        color: var(--teal);
      }}
      .mode-section h2 {{
        margin: 0 0 0.5rem;
        font-size: 1.25rem;
        color: var(--blue);
      }}
      .mode-section p {{
        margin: 0 0 1rem;
        color: var(--muted);
        font-size: 0.93rem;
        line-height: 1.55;
      }}
      .mode-toggle {{
        display: grid;
                grid-template-columns: repeat(3, minmax(0, 1fr));
        gap: 1rem;
      }}
      .mode-card {{
        position: relative;
        background: var(--card);
        border: 2px solid var(--line);
        border-radius: 12px;
        padding: 1.25rem 1.25rem 1rem;
        cursor: pointer;
        transition: border-color 150ms ease, box-shadow 150ms ease;
      }}
      .mode-card:hover {{
        border-color: var(--teal);
      }}
      .mode-card.selected {{
        border-color: var(--teal);
        box-shadow: 0 0 0 3px rgba(36, 182, 187, 0.18);
      }}
      .mode-card input[type="radio"] {{
        position: absolute;
        opacity: 0;
        pointer-events: none;
      }}
      .mode-card .mode-icon {{
        width: 40px;
        height: 40px;
        border-radius: 10px;
        display: flex;
        align-items: center;
        justify-content: center;
        margin-bottom: 0.75rem;
      }}
      .mode-card .mode-icon svg {{
        width: 22px;
        height: 22px;
      }}
      .mode-card.forward .mode-icon {{
        background: rgba(36, 182, 187, 0.12);
      }}
      .mode-card.backward .mode-icon {{
        background: rgba(40, 48, 103, 0.08);
      }}
            .mode-card.edit .mode-icon {{
                background: rgba(16, 185, 129, 0.12);
            }}
            .mode-card.review .mode-icon {{
                background: rgba(234, 179, 8, 0.14);
            }}
            .mode-card.mvp .mode-icon {{
                background: rgba(244, 114, 182, 0.12);
            }}
            .mode-card.mvp_completion .mode-icon {{
                background: rgba(34, 197, 94, 0.12);
            }}
      .mode-card .mode-title {{
        font-size: 1.05rem;
        font-weight: 700;
        color: var(--blue);
        margin-bottom: 0.35rem;
      }}
      .mode-card .mode-desc {{
        color: var(--muted);
        font-size: 0.88rem;
        line-height: 1.45;
        margin: 0;
      }}
      .mode-card .mode-check {{
        position: absolute;
        top: 0.75rem;
        right: 0.75rem;
        width: 22px;
        height: 22px;
        border-radius: 50%;
        border: 2px solid var(--line);
        display: flex;
        align-items: center;
        justify-content: center;
        transition: background 150ms ease, border-color 150ms ease;
      }}
      .mode-card.selected .mode-check {{
        background: var(--teal);
        border-color: var(--teal);
      }}
      .mode-card .mode-check svg {{
        width: 12px;
        height: 12px;
        opacity: 0;
        transition: opacity 150ms ease;
      }}
      .mode-card.selected .mode-check svg {{
        opacity: 1;
      }}
      .env-toggle {{
        display: grid;
        grid-template-columns: repeat(4, minmax(0, 1fr));
        gap: 0.85rem;
      }}
      .env-card {{
        position: relative;
        background: var(--card);
        border: 2px solid var(--line);
        border-radius: 12px;
        padding: 1rem 1rem 0.85rem;
        cursor: pointer;
        transition: border-color 150ms ease, box-shadow 150ms ease;
      }}
      .env-card:hover {{
        border-color: var(--teal);
      }}
      .env-card.selected {{
        border-color: var(--teal);
        box-shadow: 0 0 0 3px rgba(36, 182, 187, 0.18);
      }}
      .env-card input[type="checkbox"] {{
        position: absolute;
        opacity: 0;
        pointer-events: none;
      }}
      .env-card .env-icon {{
        font-size: 1.4rem;
        line-height: 1;
        margin-bottom: 0.55rem;
      }}
      .env-card .env-title {{
        font-size: 0.98rem;
        font-weight: 700;
        color: var(--blue);
        margin-bottom: 0.3rem;
      }}
      .env-card .env-desc {{
        color: var(--muted);
        font-size: 0.82rem;
        line-height: 1.4;
        margin: 0;
      }}
      .env-card .env-check {{
        position: absolute;
        top: 0.6rem;
        right: 0.6rem;
        width: 20px;
        height: 20px;
        border-radius: 50%;
        border: 2px solid var(--line);
        display: flex;
        align-items: center;
        justify-content: center;
        transition: background 150ms ease, border-color 150ms ease;
      }}
      .env-card.selected .env-check {{
        background: var(--teal);
        border-color: var(--teal);
      }}
      .env-card .env-check svg {{
        width: 11px;
        height: 11px;
        opacity: 0;
        transition: opacity 150ms ease;
      }}
      .env-card.selected .env-check svg {{
        opacity: 1;
      }}
      form {{
        display: grid;
        gap: 1rem;
      }}
      label {{
        display: grid;
        gap: 0.35rem;
        font-weight: 600;
        font-size: 0.95rem;
        color: var(--blue);
      }}
      input {{
        width: 100%;
        padding: 0.75rem 0.85rem;
        border-radius: 8px;
        border: 1px solid var(--line);
        font: inherit;
        font-size: 0.95rem;
        background: #fff;
        transition: border-color 120ms ease, box-shadow 120ms ease;
      }}
      input:focus {{
        outline: none;
        border-color: var(--blue);
        box-shadow: 0 0 0 3px rgba(40, 48, 103, 0.10);
      }}
      .hint {{
        color: var(--muted);
        font-size: 0.88rem;
        margin: 0;
      }}
      .hint code {{
        background: var(--bg);
        padding: 0.1em 0.35em;
        border-radius: 4px;
        font-size: 0.88em;
        font-family: "SFMono-Regular", "Consolas", "Liberation Mono", monospace;
      }}
      .error {{
        border: 1px solid rgba(192, 57, 43, 0.3);
        background: rgba(192, 57, 43, 0.06);
        color: var(--error);
        border-radius: 8px;
        padding: 0.75rem 1rem;
        font-size: 0.93rem;
      }}
      @media (max-width: 860px) {{
        .grid-cols {{
          grid-template-columns: 1fr;
        }}
        .env-toggle {{
          grid-template-columns: repeat(2, minmax(0, 1fr));
        }}
        .mode-toggle {{
                    grid-template-columns: repeat(2, minmax(0, 1fr));
                }}
                .hero {{
                    flex-direction: column;
                    align-items: flex-start;
                    gap: 1rem;
                }}
            }}
            @media (max-width: 640px) {{
                .mode-toggle {{
          grid-template-columns: 1fr;
        }}
        .hero {{
          flex-direction: column;
          align-items: flex-start;
          gap: 1rem;
        }}
      }}
    </style>
  </head>
  <body>
    <main class="shell">
      <section class="hero">
        <div class="hero-logo">
          <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 329.02 78.41"><defs><style>.lx1{{fill:#24b6bb}}.lx2{{fill:#283067}}.lx3{{fill:#282f63}}</style></defs><g><path class="lx3" d="M269.21,58.25h-77.14c.57.57,1.22,1.06,1.97,1.47l32.26,17.6c2.68,1.46,5.99,1.46,8.66,0l32.28-17.6c.73-.41,1.4-.9,1.96-1.47h0Z"/><path class="lx3" d="M269.21,20.16h-77.14c.57-.57,1.22-1.06,1.97-1.47L226.32,1.09c2.68-1.46,5.99-1.46,8.66,0l32.28,17.6c.73.41,1.4.9,1.96,1.47h0Z"/></g><g><path class="lx1" d="M196.83,43.09c1.37,0,2.48-.54,3.35-1.6l1.78,1.81c-1.42,1.57-3.07,2.36-5,2.36s-3.5-.59-4.73-1.79c-1.25-1.2-1.86-2.7-1.86-4.52s.63-3.34,1.9-4.57c1.26-1.22,2.82-1.82,4.64-1.82,2.05,0,3.76.78,5.12,2.31l-1.72,1.94c-.87-1.08-1.96-1.62-3.28-1.62-1.04,0-1.93.34-2.68,1.01-.75.68-1.11,1.59-1.11,2.73s.34,2.06,1.06,2.75c.7.66,1.55,1.01,2.54,1.01h0Z"/><path class="lx1" d="M208.56,45.51v-12.3h2.78v9.86h5.31v2.45h-8.09Z"/><path class="lx1" d="M233.22,43.82c-1.26,1.22-2.8,1.82-4.64,1.82s-3.38-.61-4.64-1.82c-1.26-1.22-1.88-2.73-1.88-4.54s.63-3.32,1.88-4.54c1.26-1.22,2.8-1.82,4.64-1.82s3.38.61,4.64,1.82c1.26,1.22,1.88,2.73,1.88,4.54s-.63,3.32-1.88,4.54ZM232.27,39.3c0-1.1-.36-2.03-1.08-2.8-.72-.78-1.59-1.16-2.63-1.16s-1.91.39-2.63,1.16-1.08,1.7-1.08,2.8.36,2.03,1.08,2.8c.72.78,1.59,1.15,2.63,1.15s1.91-.39,2.63-1.15c.73-.78,1.08-1.7,1.08-2.8Z"/><path class="lx1" d="M245.15,42.33c.46.57,1.09.86,1.86.86s1.4-.29,1.86-.86c.46-.57.68-1.35.68-2.33v-6.8h2.78v6.89c0,1.79-.5,3.16-1.5,4.1-.99.96-2.27,1.43-3.82,1.43s-2.83-.49-3.84-1.45c-1.01-.96-1.5-2.33-1.5-4.1v-6.89h2.78v6.8c.02,1,.24,1.79.7,2.35h0Z"/><path class="lx1" d="M269.15,34.82c1.18,1.08,1.78,2.57,1.78,4.47s-.58,3.43-1.74,4.54c-1.16,1.11-2.92,1.67-5.29,1.67h-4.25v-12.3h4.41c2.22.02,3.93.54,5.11,1.62h0ZM267.12,42.13c.68-.64,1.02-1.55,1.02-2.77s-.34-2.14-1.02-2.78c-.68-.66-1.72-.98-3.14-.98h-1.55v7.48h1.76c1.28.02,2.25-.3,2.94-.95h0Z"/></g><g><path class="lx2" d="M8.92,33.22v2.43H2.76v2.51h5.53v2.33H2.76v2.53h6.34v2.41H0v-12.21h8.92Z"/><path class="lx2" d="M24.51,33.22h3.32l-3.85,5.88,4.17,6.32h-3.36l-2.63-4.02-2.61,4.02h-3.32l4.15-6.25-3.86-5.96h3.31l2.36,3.62,2.32-3.6Z"/><path class="lx2" d="M41.53,43.02c1.36,0,2.46-.54,3.32-1.59l1.76,1.79c-1.41,1.56-3.05,2.35-4.97,2.35s-3.47-.59-4.7-1.78c-1.24-1.19-1.85-2.68-1.85-4.49s.63-3.32,1.88-4.54c1.25-1.21,2.8-1.81,4.61-1.81,2.03,0,3.73.77,5.08,2.3l-1.71,1.93c-.86-1.07-1.95-1.61-3.25-1.61-1.03,0-1.92.34-2.66,1.01-.73.67-1.1,1.57-1.1,2.71s.36,2.04,1.05,2.73c.68.65,1.53,1.01,2.53,1.01h0Z"/><path class="lx2" d="M63.68,33.22v2.43h-6.15v2.51h5.53v2.33h-5.53v2.53h6.34v2.41h-9.1v-12.21h8.92Z"/><path class="lx2" d="M72.33,45.42v-12.21h2.76v9.78h5.27v2.43h-8.03Z"/><path class="lx2" d="M88.36,45.42v-12.21h2.76v9.78h5.27v2.43h-8.03Z"/><path class="lx2" d="M113.31,33.22v2.43h-6.15v2.51h5.53v2.33h-5.53v2.53h6.34v2.41h-9.1v-12.21h8.92Z"/><path class="lx2" d="M132.14,33.22h2.76v12.21h-2.76l-5.88-7.66v7.66h-2.76v-12.21h2.58l6.07,7.86v-7.86Z"/><path class="lx2" d="M149.62,43.02c1.36,0,2.46-.54,3.32-1.59l1.76,1.79c-1.41,1.56-3.05,2.35-4.97,2.35s-3.47-.59-4.7-1.78c-1.24-1.19-1.85-2.68-1.85-4.49s.63-3.32,1.88-4.54c1.25-1.21,2.8-1.81,4.61-1.81,2.03,0,3.73.77,5.08,2.3l-1.71,1.93c-.86-1.07-1.95-1.61-3.25-1.61-1.03,0-1.92.34-2.66,1.01-.73.67-1.1,1.57-1.1,2.71s.34,2.04,1.05,2.73c.68.65,1.53,1.01,2.53,1.01h0Z"/><path class="lx2" d="M171.77,33.22v2.43h-6.15v2.51h5.53v2.33h-5.53v2.53h6.34v2.41h-9.1v-12.21h8.92Z"/></g></svg>
        </div>
        <div class="hero-text">
          <h1>Connect your AI coding tools to your LEX MCP setup</h1>
          <p>This flow will store the required secrets in <code>{html.escape(str(env_file_path))}</code> and register <code>{html.escape(LEX_MCP_LOCAL_SERVER_NAME)}</code> in every tool you select below, using each tool's own configuration format.</p>
          <p>Project root: <code>{html.escape(str(project_root))}</code></p>
        </div>
      </section>

      <section class="grid">
        <article class="panel">
          <p class="eyebrow">Workflow mode</p>
          <h2>What would you like to do?</h2>
          <div class="mode-toggle" id="modeToggle">
                        {mode_cards}
          </div>
        </article>

        <article class="panel">
          <p class="eyebrow">Coding environment</p>
          <h2>Where will you run the agent?</h2>
          <p class="hint">Select every tool you want to use. Each one gets the MCP server registered in its own config, plus the step agents, slash commands, and workspace rules in its native format. Tools already detected on this machine are pre-selected.</p>
          <div class="env-toggle" id="envToggle">
                        {environment_cards}
          </div>
        </article>

        <div class="grid-cols">
        <article class="panel">
          <p class="eyebrow">Documentation</p>
          <h2>Create a GitHub Classic Personal Access Token</h2>
          <p>Click the button below to open the GitHub token creation page. All required permission scopes are <strong>pre-selected</strong> for you.</p>
          <p>All you need to do is:</p>
          <ul>
            <li>Give the token an expiration (or select <strong>No expiration</strong>).</li>
            <li>Scroll down and click <strong>&ldquo;Generate token&rdquo;</strong>.</li>
            <li>Copy the token and paste it into the form on the right.</li>
          </ul>
          <p><a class="button" href="{html.escape(GITHUB_TOKEN_URL)}" target="_blank" rel="noreferrer">Open GitHub token page</a></p>
          <div class="meta">
            <div>Use the Lex MCP Access Key to authenticate your hosted MCP server. The <code>lex-mcp-local</code> package is installed in editable mode from <code>{html.escape(str(LEX_MCP_LOCAL_SOURCE_DIRECTORY))}</code>.</div>
          </div>
        </article>

        <section class="panel">
          <p class="eyebrow">Credentials</p>
          <h2>Save tokens to this project</h2>
          {error_block}
          <form method="post" action="/submit">
            <input type="hidden" name="state" value="{html.escape(state)}">
                        <input type="hidden" name="mcp_mode" id="mcpModeInput" value="{html.escape(selected_mode)}">

            <label>
              GitHub token
              <input type="password" name="github_token" autocomplete="off" required>
            </label>
            <p class="hint">Paste the fine-grained GitHub token you just created.</p>

            <label>
              Lex MCP Access Key
              <input type="password" name="remote_mcp_api_key" autocomplete="off" required>
            </label>
            <p class="hint">Paste the API key used by the hosted MCP endpoint. The local package install does not send this key to a package index.</p>

            <button type="submit">Save and finish setup</button>
          </form>
        </section>
      </div>
      </section>
    </main>
    <script>
      (function() {{
        var cards = document.querySelectorAll('.mode-card');
        var hiddenInput = document.getElementById('mcpModeInput');
        cards.forEach(function(card) {{
          card.addEventListener('click', function() {{
            cards.forEach(function(c) {{ c.classList.remove('selected'); }});
            card.classList.add('selected');
            var radio = card.querySelector('input[type="radio"]');
            if (radio) radio.checked = true;
            hiddenInput.value = card.getAttribute('data-mode');
          }});
        }});

        // Environment cards are a multi-select: clicking toggles one without
        // clearing the others. At least one must stay selected.
        var envCards = document.querySelectorAll('.env-card');
        function selectedCount() {{
          var n = 0;
          envCards.forEach(function(c) {{
            var box = c.querySelector('input[type="checkbox"]');
            if (box && box.checked) n++;
          }});
          return n;
        }}
        envCards.forEach(function(card) {{
          card.addEventListener('click', function(event) {{
            event.preventDefault();
            var box = card.querySelector('input[type="checkbox"]');
            if (!box) return;
            if (box.checked && selectedCount() <= 1) return;
            box.checked = !box.checked;
            card.classList.toggle('selected', box.checked);
          }});
        }});
      }})();
    </script>
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
        background: #f0f4f8;
        color: #1a1a2e;
        font-family: "Segoe UI", "Avenir Next", system-ui, sans-serif;
      }}
      .card {{
        width: min(38rem, calc(100vw - 2rem));
        background: #ffffff;
        border: 1px solid #d0d7e2;
        border-radius: 1rem;
        padding: 2rem;
        box-shadow: 0 2px 12px rgba(40, 48, 103, 0.06);
        text-align: center;
      }}
      .card-logo {{
        margin-bottom: 1.25rem;
      }}
      .card-logo svg {{
        height: 44px;
        width: auto;
      }}
      h1 {{
        margin: 0 0 0.75rem;
        color: #283067;
        font-size: 1.5rem;
      }}
      p {{
        line-height: 1.6;
        color: #5a6278;
        text-align: left;
      }}
      code {{
        background: #f0f4f8;
        padding: 0.1em 0.35em;
        border-radius: 4px;
        font-size: 0.88em;
        font-family: "SFMono-Regular", "Consolas", "Liberation Mono", monospace;
      }}
      .check {{
        display: inline-flex;
        align-items: center;
        justify-content: center;
        width: 48px;
        height: 48px;
        border-radius: 50%;
        background: rgba(36, 182, 187, 0.12);
        margin-bottom: 0.75rem;
      }}
      .check svg {{
        width: 24px;
        height: 24px;
      }}
    </style>
  </head>
  <body>
    <section class="card">
      <div class="card-logo">
        <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 329.02 78.41"><defs><style>.sx1{{fill:#24b6bb}}.sx2{{fill:#283067}}.sx3{{fill:#282f63}}</style></defs><g><path class="sx3" d="M269.21,58.25h-77.14c.57.57,1.22,1.06,1.97,1.47l32.26,17.6c2.68,1.46,5.99,1.46,8.66,0l32.28-17.6c.73-.41,1.4-.9,1.96-1.47h0Z"/><path class="sx3" d="M269.21,20.16h-77.14c.57-.57,1.22-1.06,1.97-1.47L226.32,1.09c2.68-1.46,5.99-1.46,8.66,0l32.28,17.6c.73.41,1.4.9,1.96,1.47h0Z"/></g><g><path class="sx1" d="M196.83,43.09c1.37,0,2.48-.54,3.35-1.6l1.78,1.81c-1.42,1.57-3.07,2.36-5,2.36s-3.5-.59-4.73-1.79c-1.25-1.2-1.86-2.7-1.86-4.52s.63-3.34,1.9-4.57c1.26-1.22,2.82-1.82,4.64-1.82,2.05,0,3.76.78,5.12,2.31l-1.72,1.94c-.87-1.08-1.96-1.62-3.28-1.62-1.04,0-1.93.34-2.68,1.01-.75.68-1.11,1.59-1.11,2.73s.34,2.06,1.06,2.75c.7.66,1.55,1.01,2.54,1.01h0Z"/><path class="sx1" d="M208.56,45.51v-12.3h2.78v9.86h5.31v2.45h-8.09Z"/><path class="sx1" d="M233.22,43.82c-1.26,1.22-2.8,1.82-4.64,1.82s-3.38-.61-4.64-1.82c-1.26-1.22-1.88-2.73-1.88-4.54s.63-3.32,1.88-4.54c1.26-1.22,2.8-1.82,4.64-1.82s3.38.61,4.64,1.82c1.26,1.22,1.88,2.73,1.88,4.54s-.63,3.32-1.88,4.54ZM232.27,39.3c0-1.1-.36-2.03-1.08-2.8-.72-.78-1.59-1.16-2.63-1.16s-1.91.39-2.63,1.16-1.08,1.7-1.08,2.8.36,2.03,1.08,2.8c.72.78,1.59,1.15,2.63,1.15s1.91-.39,2.63-1.15c.73-.78,1.08-1.7,1.08-2.8Z"/><path class="sx1" d="M245.15,42.33c.46.57,1.09.86,1.86.86s1.4-.29,1.86-.86c.46-.57.68-1.35.68-2.33v-6.8h2.78v6.89c0,1.79-.5,3.16-1.5,4.1-.99.96-2.27,1.43-3.82,1.43s-2.83-.49-3.84-1.45c-1.01-.96-1.5-2.33-1.5-4.1v-6.89h2.78v6.8c.02,1,.24,1.79.7,2.35h0Z"/><path class="sx1" d="M269.15,34.82c1.18,1.08,1.78,2.57,1.78,4.47s-.58,3.43-1.74,4.54c-1.16,1.11-2.92,1.67-5.29,1.67h-4.25v-12.3h4.41c2.22.02,3.93.54,5.11,1.62h0ZM267.12,42.13c.68-.64,1.02-1.55,1.02-2.77s-.34-2.14-1.02-2.78c-.68-.66-1.72-.98-3.14-.98h-1.55v7.48h1.76c1.28.02,2.25-.3,2.94-.95h0Z"/></g><g><path class="sx2" d="M8.92,33.22v2.43H2.76v2.51h5.53v2.33H2.76v2.53h6.34v2.41H0v-12.21h8.92Z"/><path class="sx2" d="M24.51,33.22h3.32l-3.85,5.88,4.17,6.32h-3.36l-2.63-4.02-2.61,4.02h-3.32l4.15-6.25-3.86-5.96h3.31l2.36,3.62,2.32-3.6Z"/><path class="sx2" d="M41.53,43.02c1.36,0,2.46-.54,3.32-1.59l1.76,1.79c-1.41,1.56-3.05,2.35-4.97,2.35s-3.47-.59-4.7-1.78c-1.24-1.19-1.85-2.68-1.85-4.49s.63-3.32,1.88-4.54c1.25-1.21,2.8-1.81,4.61-1.81,2.03,0,3.73.77,5.08,2.3l-1.71,1.93c-.86-1.07-1.95-1.61-3.25-1.61-1.03,0-1.92.34-2.66,1.01-.73.67-1.1,1.57-1.1,2.71s.36,2.04,1.05,2.73c.68.65,1.53,1.01,2.53,1.01h0Z"/><path class="sx2" d="M63.68,33.22v2.43h-6.15v2.51h5.53v2.33h-5.53v2.53h6.34v2.41h-9.1v-12.21h8.92Z"/><path class="sx2" d="M72.33,45.42v-12.21h2.76v9.78h5.27v2.43h-8.03Z"/><path class="sx2" d="M88.36,45.42v-12.21h2.76v9.78h5.27v2.43h-8.03Z"/><path class="sx2" d="M113.31,33.22v2.43h-6.15v2.51h5.53v2.33h-5.53v2.53h6.34v2.41h-9.1v-12.21h8.92Z"/><path class="sx2" d="M132.14,33.22h2.76v12.21h-2.76l-5.88-7.66v7.66h-2.76v-12.21h2.58l6.07,7.86v-7.86Z"/><path class="sx2" d="M149.62,43.02c1.36,0,2.46-.54,3.32-1.59l1.76,1.79c-1.41,1.56-3.05,2.35-4.97,2.35s-3.47-.59-4.7-1.78c-1.24-1.19-1.85-2.68-1.85-4.49s.63-3.32,1.88-4.54c1.25-1.21,2.8-1.81,4.61-1.81,2.03,0,3.73.77,5.08,2.3l-1.71,1.93c-.86-1.07-1.95-1.61-3.25-1.61-1.03,0-1.92.34-2.66,1.01-.73.67-1.1,1.57-1.1,2.71s.34,2.04,1.05,2.73c.68.65,1.53,1.01,2.53,1.01h0Z"/><path class="sx2" d="M171.77,33.22v2.43h-6.15v2.51h5.53v2.33h-5.53v2.53h6.34v2.41h-9.1v-12.21h8.92Z"/></g></svg>
      </div>
      <div class="check">
        <svg viewBox="0 0 24 24" fill="none" stroke="#24b6bb" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"/></svg>
      </div>
      <h1>Credentials received</h1>
      <p>Return to the terminal while LEX installs <code>lex-mcp-local</code> and finishes writing <code>{html.escape(str(env_file_path))}</code> plus the GitHub Copilot <code>mcp.json</code> entry for <code>{html.escape(LEX_MCP_LOCAL_SERVER_NAME)}</code>.</p>
      <p>You can close this tab after the terminal prints the final setup success message.</p>
    </section>
  </body>
</html>
"""
