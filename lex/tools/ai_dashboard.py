"""LEX AI Dashboard — browser-based configuration management for the MCP server.

Opens a local HTTP page that displays and lets the user edit:

* **MCP workflow mode** (forward / backward) — writes the one-shot override
  file at ``~/.lex-mcp/mode-override`` and updates ``LEX_MCP_MODE`` in the
  project ``.env`` and ``mcp.json``.
* **GitHub token** — written to ``GITHUB_TOKEN`` in ``.env`` and ``mcp.json``.
* **Remote MCP API key** — written to ``REMOTE_MCP_API_KEY`` in ``.env`` and
  ``mcp.json``.
* **Remote MCP URL** — written to ``REMOTE_MCP_URL`` in ``.env`` and
  ``mcp.json``.
* Read-only system information (installed package version, Python
  executable, override file status, etc.).
"""

from __future__ import annotations

import html
import json
import os
import secrets
import sqlite3
import subprocess
import signal
import threading
import time
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Mapping
from urllib.parse import parse_qs, urlparse

from lex.tools.setup_with_ai import (
    DEFAULT_LEX_MCP_MODE,
    DEFAULT_REMOTE_MCP_URL,
    GITHUB_COPILOT_MCP_FIRST_BOOT_COMPLETED_KEY,
    GITHUB_COPILOT_MCP_SERVERS_CACHE_KEY,
    LEX_MCP_LOCAL_SERVER_NAME,
    SetupWithAIError,
    _atomic_write_text,
    _ensure_github_copilot_state_table,
    _load_github_copilot_mcp_servers_cache,
    _read_dotenv_value,
    _resolve_lex_mcp_local_runtime_paths,
    _read_pid_file,
    _is_process_running,
    _write_github_copilot_state_value,
    get_installed_lex_mcp_local_version,
    resolve_github_copilot_mcp_config_path,
    resolve_github_copilot_state_db_path,
    update_env_file,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MODE_OVERRIDE_DIR = Path.home() / ".lex-mcp"
MODE_OVERRIDE_FILE = MODE_OVERRIDE_DIR / "mode-override"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _invalidate_copilot_mcp_cache(
    mcp_config_path: Path,
    server_name: str = LEX_MCP_LOCAL_SERVER_NAME,
) -> bool:
    """Clear the cached tool list in the Copilot IntelliJ state DB.

    PyCharm's Copilot agent caches the tool list returned by the MCP
    server's ``tools/list`` response. When the mode changes, the tool
    surface changes completely, but PyCharm keeps serving the stale cache.

    This function removes the server entry from ``mcp-servers-cache`` and
    resets ``mcp-first-boot-completed`` to ``"false"`` so PyCharm
    re-probes the server on the next Copilot interaction.

    Returns ``True`` if the cache was modified, ``False`` otherwise.
    """
    try:
        state_db_path = resolve_github_copilot_state_db_path(
            mcp_config_path=mcp_config_path,
        )
    except Exception:
        return False

    if not state_db_path.exists():
        return False

    try:
        with sqlite3.connect(state_db_path) as conn:
            _ensure_github_copilot_state_table(conn)
            cached = _load_github_copilot_mcp_servers_cache(conn)
            cached.pop(server_name, None)
            _write_github_copilot_state_value(
                conn,
                GITHUB_COPILOT_MCP_SERVERS_CACHE_KEY,
                cached,
            )
            _write_github_copilot_state_value(
                conn,
                GITHUB_COPILOT_MCP_FIRST_BOOT_COMPLETED_KEY,
                "false",
            )
            conn.commit()
        return True
    except Exception:
        return False


def _mask_token(token: str) -> str:
    """Return a masked representation of a secret token for display."""
    if not token:
        return "(not set)"
    if len(token) <= 8:
        return "\u2022" * len(token)
    return token[:4] + "\u2022" * min(len(token) - 8, 16) + token[-4:]


def _read_mode_from_mcp_json(
    mcp_config_path: Path,
    server_name: str = LEX_MCP_LOCAL_SERVER_NAME,
) -> str | None:
    """Extract the active mode from an mcp.json server definition.

    Checks the ``--mode`` CLI arg first, then ``LEX_MCP_MODE`` in the env
    block.  Returns *None* if neither is present or the file is missing.
    """
    if not mcp_config_path.exists():
        return None
    try:
        config = json.loads(mcp_config_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    server_def = config.get("servers", {}).get(server_name)
    if not isinstance(server_def, dict):
        return None
    # Prefer the --mode CLI arg.
    args = server_def.get("args", [])
    if isinstance(args, list):
        for i, arg in enumerate(args):
            if arg == "--mode" and i + 1 < len(args):
                val = str(args[i + 1]).strip().lower()
                if val in ("forward", "backward"):
                    return val
    # Fall back to the env block.
    env_block = server_def.get("env", {})
    if isinstance(env_block, dict):
        val = str(env_block.get("LEX_MCP_MODE", "")).strip().lower()
        if val in ("forward", "backward"):
            return val
    return None


def _read_override_file() -> dict[str, Any] | None:
    if not MODE_OVERRIDE_FILE.exists():
        return None
    try:
        raw = MODE_OVERRIDE_FILE.read_text(encoding="utf-8").strip()
        return json.loads(raw) if raw.startswith("{") else {"mode": raw}
    except Exception:
        return None


def _write_mode_override(mode: str) -> None:
    MODE_OVERRIDE_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "mode": mode,
        "reason": "Changed via LEX AI Dashboard",
        "source_tool": "lex-ai-dashboard",
        "pid": os.getpid(),
    }
    MODE_OVERRIDE_FILE.write_text(
        json.dumps(payload, indent=2), encoding="utf-8",
    )


def _update_mcp_json_mode(
    mcp_config_path: Path,
    mode: str,
    server_name: str = LEX_MCP_LOCAL_SERVER_NAME,
) -> bool:
    """Update ``--mode`` arg and ``LEX_MCP_MODE`` env in an mcp.json entry."""
    if not mcp_config_path.exists():
        return False
    try:
        config = json.loads(mcp_config_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return False
    servers = config.get("servers", {})
    server_def = servers.get(server_name)
    if not isinstance(server_def, dict):
        return False

    args = server_def.get("args", [])
    if isinstance(args, list):
        found = False
        for i, arg in enumerate(args):
            if arg == "--mode" and i + 1 < len(args):
                args[i + 1] = mode
                found = True
                break
        if not found:
            args.extend(["--mode", mode])
        server_def["args"] = args

    env_block = server_def.get("env", {})
    if isinstance(env_block, dict):
        env_block["LEX_MCP_MODE"] = mode
        server_def["env"] = env_block

    _atomic_write_text(mcp_config_path, json.dumps(config, indent=2) + "\n")
    return True


def _update_mcp_json_env_values(
    mcp_config_path: Path,
    values: Mapping[str, str],
    server_name: str = LEX_MCP_LOCAL_SERVER_NAME,
) -> bool:
    """Update env values in an mcp.json server entry."""
    if not mcp_config_path.exists():
        return False
    try:
        config = json.loads(mcp_config_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return False
    servers = config.get("servers", {})
    server_def = servers.get(server_name)
    if not isinstance(server_def, dict):
        return False
    env_block = server_def.get("env", {})
    if not isinstance(env_block, dict):
        env_block = {}
    env_block.update(values)
    server_def["env"] = env_block
    _atomic_write_text(mcp_config_path, json.dumps(config, indent=2) + "\n")
    return True


def _stop_mcp_server(
    mcp_config_path: Path,
    server_name: str = LEX_MCP_LOCAL_SERVER_NAME,
    timeout_seconds: float = 5.0,
) -> bool:
    """Stop a running lex-mcp-local server. Returns True if stopped."""
    pid_file_path, _ = _resolve_lex_mcp_local_runtime_paths(
        mcp_config_path, server_name=server_name,
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


# ---------------------------------------------------------------------------
# Dashboard state
# ---------------------------------------------------------------------------


def _read_dashboard_state(
    project_root: Path,
    env_file_path: Path,
    python_executable: Path,
    mcp_config_path: Path,
) -> dict[str, Any]:
    return {
        "project_root": str(project_root),
        "env_file_path": str(env_file_path),
        "python_executable": str(python_executable),
        "mcp_config_path": str(mcp_config_path),
        "mcp_mode": (
            _read_dotenv_value(env_file_path, "LEX_MCP_MODE")
            or _read_mode_from_mcp_json(mcp_config_path)
            or DEFAULT_LEX_MCP_MODE
        ),
        "github_token": _read_dotenv_value(env_file_path, "GITHUB_TOKEN") or "",
        "remote_mcp_api_key": (
            _read_dotenv_value(env_file_path, "REMOTE_MCP_API_KEY") or ""
        ),
        "remote_mcp_url": (
            _read_dotenv_value(env_file_path, "REMOTE_MCP_URL")
            or DEFAULT_REMOTE_MCP_URL
        ),
        "lex_mcp_local_version": get_installed_lex_mcp_local_version(
            python_executable,
        ),
        "override_pending": _read_override_file(),
        "mcp_config_exists": mcp_config_path.exists(),
    }


# ---------------------------------------------------------------------------
# Save handler
# ---------------------------------------------------------------------------


def _handle_save(
    form_data: dict[str, list[str]],
    project_root: Path,
    env_file_path: Path,
    mcp_config_path: Path,
    server_name: str = LEX_MCP_LOCAL_SERVER_NAME,
) -> tuple[list[str], list[str]]:
    """Process a dashboard save. Returns ``(successes, errors)``."""
    successes: list[str] = []
    errors: list[str] = []

    new_mode = form_data.get("mcp_mode", [""])[0].strip().lower()
    new_github_token = form_data.get("github_token", [""])[0].strip()
    new_remote_key = form_data.get("remote_mcp_api_key", [""])[0].strip()
    new_remote_url = form_data.get("remote_mcp_url", [""])[0].strip()

    stored_mode = _read_dotenv_value(env_file_path, "LEX_MCP_MODE")
    current_mode = (
        stored_mode
        or _read_mode_from_mcp_json(mcp_config_path)
        or DEFAULT_LEX_MCP_MODE
    )

    # ── Mode change ──────────────────────────────────────────────────
    # Treat as a change when: (a) the user picked a different mode, OR
    # (b) the mode was never explicitly written to .env yet.
    mode_changed = (
        new_mode
        and new_mode in ("forward", "backward")
        and (new_mode != current_mode or stored_mode is None)
    )
    if mode_changed:
        try:
            _write_mode_override(new_mode)
            update_env_file(env_file_path, {"LEX_MCP_MODE": new_mode})
            _update_mcp_json_mode(mcp_config_path, new_mode, server_name=server_name)
            cache_cleared = _invalidate_copilot_mcp_cache(
                mcp_config_path, server_name=server_name,
            )
            stopped = _stop_mcp_server(mcp_config_path, server_name=server_name)
            msg = f"Mode changed to {new_mode}."
            if stopped:
                msg += " Server stopped; it will restart in the new mode on next use."
            else:
                msg += " The new mode takes effect on next server start."
            if cache_cleared:
                msg += " IDE tool cache cleared."
            successes.append(msg)
        except Exception as exc:
            errors.append(f"Failed to switch mode: {exc}")

    # ── Credential / URL updates ─────────────────────────────────────
    env_updates: dict[str, str] = {}
    mcp_env_updates: dict[str, str] = {}

    if new_github_token:
        env_updates["GITHUB_TOKEN"] = new_github_token
        mcp_env_updates["GITHUB_TOKEN"] = new_github_token
    if new_remote_key:
        env_updates["REMOTE_MCP_API_KEY"] = new_remote_key
        mcp_env_updates["REMOTE_MCP_API_KEY"] = new_remote_key
    if new_remote_url:
        current_url = _read_dotenv_value(env_file_path, "REMOTE_MCP_URL") or ""
        if new_remote_url != current_url:
            env_updates["REMOTE_MCP_URL"] = new_remote_url
            mcp_env_updates["REMOTE_MCP_URL"] = new_remote_url

    if env_updates:
        try:
            update_env_file(env_file_path, env_updates)
            _update_mcp_json_env_values(
                mcp_config_path, mcp_env_updates, server_name=server_name,
            )
            keys = ", ".join(sorted(env_updates))
            successes.append(f"Updated: {keys}")
        except Exception as exc:
            errors.append(f"Failed to update credentials: {exc}")

    if not successes and not errors:
        successes.append("No changes detected.")

    return successes, errors


# ---------------------------------------------------------------------------
# HTML builder
# ---------------------------------------------------------------------------

# The Excellence Cloud logo SVG, shared with the setup form.
_LOGO_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 329.02 78.41">'
    '<defs><style>.d1{fill:#24b6bb}.d2{fill:#283067}.d3{fill:#282f63}</style></defs>'
    '<g><path class="d3" d="M269.21,58.25h-77.14c.57.57,1.22,1.06,1.97,1.47l32.26,'
    '17.6c2.68,1.46,5.99,1.46,8.66,0l32.28-17.6c.73-.41,1.4-.9,1.96-1.47h0Z"/>'
    '<path class="d3" d="M269.21,20.16h-77.14c.57-.57,1.22-1.06,1.97-1.47L226.32,'
    '1.09c2.68-1.46,5.99-1.46,8.66,0l32.28,17.6c.73.41,1.4.9,1.96,1.47h0Z"/></g>'
    '<g><path class="d1" d="M196.83,43.09c1.37,0,2.48-.54,3.35-1.6l1.78,1.81c-1.42,'
    '1.57-3.07,2.36-5,2.36s-3.5-.59-4.73-1.79c-1.25-1.2-1.86-2.7-1.86-4.52s.63-3.34,'
    '1.9-4.57c1.26-1.22,2.82-1.82,4.64-1.82,2.05,0,3.76.78,5.12,2.31l-1.72,1.94c-.87-1.08-1.96-1.62-3.28-1.62-1.04,0-1.93.34-2.68,1.01-.75.68-1.11,1.59-1.11,2.73s.34,2.06,1.06,2.75c.7.66,1.55,1.01,2.54,1.01h0Z"/>'
    '<path class="d1" d="M208.56,45.51v-12.3h2.78v9.86h5.31v2.45h-8.09Z"/>'
    '<path class="d1" d="M233.22,43.82c-1.26,1.22-2.8,1.82-4.64,1.82s-3.38-.61-4.64-1.82c-1.26-1.22-1.88-2.73-1.88-4.54s.63-3.32,1.88-4.54c1.26-1.22,2.8-1.82,4.64-1.82s3.38.61,4.64,1.82c1.26,1.22,1.88,2.73,1.88,4.54s-.63,3.32-1.88,4.54ZM232.27,39.3c0-1.1-.36-2.03-1.08-2.8-.72-.78-1.59-1.16-2.63-1.16s-1.91.39-2.63,1.16-1.08,1.7-1.08,2.8.36,2.03,1.08,2.8c.72.78,1.59,1.15,2.63,1.15s1.91-.39,2.63-1.15c.73-.78,1.08-1.7,1.08-2.8Z"/>'
    '<path class="d1" d="M245.15,42.33c.46.57,1.09.86,1.86.86s1.4-.29,1.86-.86c.46-.57.68-1.35.68-2.33v-6.8h2.78v6.89c0,1.79-.5,3.16-1.5,4.1-.99.96-2.27,1.43-3.82,1.43s-2.83-.49-3.84-1.45c-1.01-.96-1.5-2.33-1.5-4.1v-6.89h2.78v6.8c.02,1,.24,1.79.7,2.35h0Z"/>'
    '<path class="d1" d="M269.15,34.82c1.18,1.08,1.78,2.57,1.78,4.47s-.58,3.43-1.74,4.54c-1.16,1.11-2.92,1.67-5.29,1.67h-4.25v-12.3h4.41c2.22.02,3.93.54,5.11,1.62h0ZM267.12,42.13c.68-.64,1.02-1.55,1.02-2.77s-.34-2.14-1.02-2.78c-.68-.66-1.72-.98-3.14-.98h-1.55v7.48h1.76c1.28.02,2.25-.3,2.94-.95h0Z"/></g>'
    '<g><path class="d2" d="M8.92,33.22v2.43H2.76v2.51h5.53v2.33H2.76v2.53h6.34v2.41H0v-12.21h8.92Z"/>'
    '<path class="d2" d="M24.51,33.22h3.32l-3.85,5.88,4.17,6.32h-3.36l-2.63-4.02-2.61,4.02h-3.32l4.15-6.25-3.86-5.96h3.31l2.36,3.62,2.32-3.6Z"/>'
    '<path class="d2" d="M41.53,43.02c1.36,0,2.46-.54,3.32-1.59l1.76,1.79c-1.41,1.56-3.05,2.35-4.97,2.35s-3.47-.59-4.7-1.78c-1.24-1.19-1.85-2.68-1.85-4.49s.63-3.32,1.88-4.54c1.25-1.21,2.8-1.81,4.61-1.81,2.03,0,3.73.77,5.08,2.3l-1.71,1.93c-.86-1.07-1.95-1.61-3.25-1.61-1.03,0-1.92.34-2.66,1.01-.73.67-1.1,1.57-1.1,2.71s.36,2.04,1.05,2.73c.68.65,1.53,1.01,2.53,1.01h0Z"/>'
    '<path class="d2" d="M63.68,33.22v2.43h-6.15v2.51h5.53v2.33h-5.53v2.53h6.34v2.41h-9.1v-12.21h8.92Z"/>'
    '<path class="d2" d="M72.33,45.42v-12.21h2.76v9.78h5.27v2.43h-8.03Z"/>'
    '<path class="d2" d="M88.36,45.42v-12.21h2.76v9.78h5.27v2.43h-8.03Z"/>'
    '<path class="d2" d="M113.31,33.22v2.43h-6.15v2.51h5.53v2.33h-5.53v2.53h6.34v2.41h-9.1v-12.21h8.92Z"/>'
    '<path class="d2" d="M132.14,33.22h2.76v12.21h-2.76l-5.88-7.66v7.66h-2.76v-12.21h2.58l6.07,7.86v-7.86Z"/>'
    '<path class="d2" d="M149.62,43.02c1.36,0,2.46-.54,3.32-1.59l1.76,1.79c-1.41,1.56-3.05,2.35-4.97,2.35s-3.47-.59-4.7-1.78c-1.24-1.19-1.85-2.68-1.85-4.49s.63-3.32,1.88-4.54c1.25-1.21,2.8-1.81,4.61-1.81,2.03,0,3.73.77,5.08,2.3l-1.71,1.93c-.86-1.07-1.95-1.61-3.25-1.61-1.03,0-1.92.34-2.66,1.01-.73.67-1.1,1.57-1.1,2.71s.34,2.04,1.05,2.73c.68.65,1.53,1.01,2.53,1.01h0Z"/>'
    '<path class="d2" d="M171.77,33.22v2.43h-6.15v2.51h5.53v2.33h-5.53v2.53h6.34v2.41h-9.1v-12.21h8.92Z"/></g></svg>'
)


def _build_dashboard_html(
    *,
    state: str,
    config: dict[str, Any],
    successes: list[str] | None = None,
    errors: list[str] | None = None,
) -> str:
    mode = config["mcp_mode"]
    forward_sel = "selected" if mode == "forward" else ""
    backward_sel = "selected" if mode == "backward" else ""
    forward_checked = "checked" if mode == "forward" else ""
    backward_checked = "checked" if mode == "backward" else ""

    masked_gh = html.escape(_mask_token(config["github_token"]))
    masked_key = html.escape(_mask_token(config["remote_mcp_api_key"]))
    current_url = html.escape(config["remote_mcp_url"])

    version = config["lex_mcp_local_version"] or "not installed"
    override = config["override_pending"]
    override_str = (
        f"Pending: {html.escape(override.get('mode', '?'))}"
        if override
        else "None"
    )

    # Toast messages
    toast_html = ""
    if successes:
        for msg in successes:
            toast_html += (
                f'<div class="toast toast-ok">'
                f'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" class="toast-icon"><polyline points="20 6 9 17 4 12"/></svg>'
                f'{html.escape(msg)}</div>'
            )
    if errors:
        for msg in errors:
            toast_html += (
                f'<div class="toast toast-err">'
                f'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" class="toast-icon"><circle cx="12" cy="12" r="10"/><line x1="15" y1="9" x2="9" y2="15"/><line x1="9" y1="9" x2="15" y2="15"/></svg>'
                f'{html.escape(msg)}</div>'
            )

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>LEX AI Dashboard</title>
  <style>
    :root {{
      /* DESIGN.md color tokens */
      --primary: #0f172a;
      --primary-container: #1e293b;
      --secondary: #1d4ed8;
      --secondary-container: #dbeafe;
      --tertiary: #38bdf8;
      --tertiary-container: #eff6ff;
      --neutral: #ffffff;
      --neutral-soft: #f8fafc;
      --neutral-page: #eef4fb;
      --border: #e2e8f0;
      --border-accent: #bfdbfe;
      --text-primary: #0f172a;
      --text-secondary: #475569;
      --text-muted: #64748b;
      --text-inverse: #ffffff;
      --success: #059669;
      --success-bg: #ecfdf5;
      --error: #dc2626;
      --error-bg: #fef2f2;
      /* DESIGN.md rounded tokens */
      --rounded-sm: 10px;
      --rounded-md: 12px;
      --rounded-lg: 14px;
      --rounded-xl: 16px;
      --rounded-container: 20px;
      --rounded-pill: 999px;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: Arial, Helvetica, sans-serif;
      font-size: 16px;
      line-height: 26px;
      background: var(--neutral-page);
      color: var(--text-primary);
    }}
    .shell {{
      max-width: 760px;
      margin: 0 auto;
      padding: 2rem 1.25rem 3rem;
    }}

    /* ── Hero ─────────────────────────────────────────────────── */
    .hero {{
      background: var(--neutral);
      border: 1px solid var(--border);
      border-radius: var(--rounded-container);
      padding: 1.75rem 1.5rem;
      display: flex;
      align-items: center;
      gap: 1.25rem;
      margin-bottom: 1.25rem;
    }}
    .hero-logo {{ flex-shrink: 0; }}
    .hero-logo svg {{ height: 48px; width: auto; }}
    .hero-text {{ flex: 1; min-width: 0; }}
    .hero h1 {{
      margin: 0 0 0.25rem;
      font-size: 26px;
      font-weight: 700;
      line-height: 34px;
      color: var(--primary);
    }}
    .hero p {{
      margin: 0;
      color: var(--text-secondary);
      font-size: 15px;
      line-height: 24px;
    }}
    .hero code {{
      background: var(--neutral-soft);
      padding: 0.1em 0.35em;
      border-radius: 4px;
      font-size: 14px;
      font-family: Consolas, "SFMono-Regular", Menlo, Monaco, monospace;
    }}

    /* ── Toast messages ───────────────────────────────────────── */
    .toast {{
      display: flex;
      align-items: center;
      gap: 0.6rem;
      padding: 0.75rem 1rem;
      border-radius: var(--rounded-sm);
      font-size: 15px;
      line-height: 22px;
      margin-bottom: 1rem;
    }}
    .toast-icon {{ width: 18px; height: 18px; flex-shrink: 0; }}
    .toast-ok {{
      background: var(--success-bg);
      color: var(--success);
      border: 1px solid rgba(5, 150, 105, 0.25);
    }}
    .toast-err {{
      background: var(--error-bg);
      color: var(--error);
      border: 1px solid rgba(220, 38, 38, 0.25);
    }}

    /* ── Cards ─────────────────────────────────────────────────── */
    .card {{
      background: var(--neutral);
      border: 1px solid var(--border);
      border-radius: var(--rounded-container);
      padding: 1.5rem;
      margin-bottom: 1.25rem;
    }}
    .badge {{
      display: inline-block;
      background: var(--secondary-container);
      color: var(--secondary);
      font-size: 12px;
      font-weight: 700;
      letter-spacing: 0.3px;
      text-transform: uppercase;
      padding: 4px 10px;
      border-radius: var(--rounded-pill);
      margin-bottom: 0.6rem;
    }}
    .card h2 {{
      margin: 0 0 0.75rem;
      font-size: 17px;
      font-weight: 700;
      line-height: 24px;
      color: var(--primary);
    }}
    .card p {{
      margin: 0 0 1rem;
      color: var(--text-secondary);
      font-size: 15px;
      line-height: 24px;
    }}

    /* ── Mode toggle ──────────────────────────────────────────── */
    .mode-toggle {{
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 1rem;
    }}
    .mode-card {{
      position: relative;
      background: var(--neutral);
      border: 2px solid var(--border);
      border-radius: var(--rounded-md);
      padding: 1.15rem 1.15rem 1rem;
      cursor: pointer;
      transition: border-color 150ms ease, box-shadow 150ms ease;
    }}
    .mode-card:hover {{ border-color: var(--tertiary); }}
    .mode-card.selected {{
      border-color: var(--tertiary);
      box-shadow: 0 0 0 3px rgba(56, 189, 248, 0.18);
    }}
    .mode-card input[type="radio"] {{
      position: absolute; opacity: 0; pointer-events: none;
    }}
    .mode-icon {{
      width: 38px; height: 38px; border-radius: var(--rounded-sm);
      display: flex; align-items: center; justify-content: center;
      margin-bottom: 0.65rem;
    }}
    .mode-icon svg {{ width: 20px; height: 20px; }}
    .mode-card.forward .mode-icon {{ background: rgba(56, 189, 248, 0.12); }}
    .mode-card.backward .mode-icon {{ background: rgba(29, 78, 216, 0.08); }}
    .mode-title {{
      font-size: 15px; font-weight: 700; color: var(--primary);
      margin-bottom: 0.3rem;
    }}
    .mode-desc {{
      color: var(--text-muted); font-size: 13px; line-height: 20px; margin: 0;
    }}
    .mode-check {{
      position: absolute; top: 0.65rem; right: 0.65rem;
      width: 20px; height: 20px; border-radius: 50%;
      border: 2px solid var(--border);
      display: flex; align-items: center; justify-content: center;
      transition: background 150ms ease, border-color 150ms ease;
    }}
    .mode-card.selected .mode-check {{
      background: var(--secondary); border-color: var(--secondary);
    }}
    .mode-check svg {{ width: 11px; height: 11px; opacity: 0; transition: opacity 150ms; }}
    .mode-card.selected .mode-check svg {{ opacity: 1; }}
    .mode-note {{
      margin: 0.75rem 0 0;
      padding: 0.7rem 0.9rem;
      background: var(--neutral-soft);
      border-radius: var(--rounded-sm);
      color: var(--text-muted);
      font-size: 13px;
      line-height: 20px;
    }}

    /* ── Form elements ────────────────────────────────────────── */
    form {{ display: grid; gap: 0.9rem; }}
    label {{
      display: grid; gap: 0.3rem;
      font-weight: 700; font-size: 15px; color: var(--primary);
    }}
    input[type="password"], input[type="text"], input[type="url"] {{
      width: 100%; padding: 0.65rem 0.75rem;
      border-radius: var(--rounded-sm); border: 1px solid var(--border);
      font: inherit; font-size: 15px; background: var(--neutral);
      transition: border-color 120ms ease, box-shadow 120ms ease;
    }}
    input:focus {{
      outline: none;
      border-color: var(--secondary);
      box-shadow: 0 0 0 3px rgba(29, 78, 216, 0.10);
    }}
    .hint {{
      color: var(--text-muted); font-size: 13px; line-height: 20px; margin: 0;
    }}
    .hint code {{
      background: var(--neutral-soft); padding: 0.1em 0.3em;
      border-radius: 4px; font-size: 13px;
      font-family: Consolas, "SFMono-Regular", Menlo, Monaco, monospace;
    }}
    button {{
      display: inline-block; appearance: none; border: 0;
      border-radius: var(--rounded-sm);
      background: var(--secondary); color: var(--text-inverse);
      font: inherit; font-size: 15px; font-weight: 700;
      cursor: pointer; padding: 0.7rem 1.25rem;
      transition: background 120ms ease, box-shadow 120ms ease;
    }}
    button:hover {{
      background: #1e40af;
      box-shadow: 0 4px 14px rgba(29, 78, 216, 0.18);
    }}

    /* ── Info table ────────────────────────────────────────────── */
    .info-grid {{
      display: grid; gap: 0.5rem;
    }}
    .info-row {{
      display: grid; grid-template-columns: 11rem 1fr; gap: 0.5rem;
      font-size: 14px; line-height: 22px;
      padding: 0.35rem 0;
      border-bottom: 1px solid var(--border);
    }}
    .info-row:last-child {{ border-bottom: none; }}
    .info-label {{ color: var(--text-muted); font-weight: 700; }}
    .info-value {{
      color: var(--text-primary); word-break: break-all;
      font-family: Consolas, "SFMono-Regular", Menlo, Monaco, monospace;
      font-size: 13px;
    }}

    /* ── Footer ───────────────────────────────────────────────── */
    .footer {{
      text-align: center; color: var(--text-muted);
      font-size: 13px; line-height: 20px; margin-top: 0.5rem;
    }}

    @media (max-width: 640px) {{
      .mode-toggle {{ grid-template-columns: 1fr; }}
      .hero {{ flex-direction: column; align-items: flex-start; gap: 0.75rem; }}
      .info-row {{ grid-template-columns: 1fr; }}
    }}
  </style>
</head>
<body>
<main class="shell">
  <section class="hero">
    <div class="hero-logo">{_LOGO_SVG}</div>
    <div class="hero-text">
      <h1>LEX AI Dashboard</h1>
      <p>Manage your MCP server mode, credentials, and configuration for <code>{html.escape(str(config['project_root']))}</code></p>
    </div>
  </section>

  {toast_html}

  <form method="post" action="/save">
    <input type="hidden" name="state" value="{html.escape(state)}">
    <input type="hidden" name="mcp_mode" id="mcpModeInput" value="{html.escape(mode)}">

    <!-- Mode -->
    <section class="card">
      <div class="badge">MCP Mode</div>
      <h2>Workflow Mode</h2>
      <p>Choose which MCP workflow the server exposes. Changing mode writes a one-shot override file and stops the running server.</p>
      <div class="mode-toggle" id="modeToggle">
        <label class="mode-card forward {forward_sel}" data-mode="forward">
          <input type="radio" name="mcp_mode_select" value="forward" {forward_checked}>
          <div class="mode-icon">
            <svg viewBox="0 0 24 24" fill="none" stroke="#38bdf8" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="12" y1="5" x2="12" y2="19"/><polyline points="19 12 12 19 5 12"/></svg>
          </div>
          <div class="mode-title">Create new project</div>
          <p class="mode-desc">AI-assisted planning, implementation, and documentation from scratch.</p>
          <div class="mode-check">
            <svg viewBox="0 0 24 24" fill="none" stroke="#fff" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"/></svg>
          </div>
        </label>
        <label class="mode-card backward {backward_sel}" data-mode="backward">
          <input type="radio" name="mcp_mode_select" value="backward" {backward_checked}>
          <div class="mode-icon">
            <svg viewBox="0 0 24 24" fill="none" stroke="#1d4ed8" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/></svg>
          </div>
          <div class="mode-title">Document existing project</div>
          <p class="mode-desc">Generate documentation and canonical context files for an existing codebase.</p>
          <div class="mode-check">
            <svg viewBox="0 0 24 24" fill="none" stroke="#fff" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"/></svg>
          </div>
        </label>
      </div>
      <p class="mode-note">The MCP server must restart for a mode change to take effect. Saving will stop any running server automatically.</p>
    </section>

    <!-- Credentials -->
    <section class="card">
      <div class="badge">Credentials</div>
      <h2>Authentication Tokens</h2>
      <p>Leave a field empty to keep the current value. Current values are shown masked below each field.</p>

      <label>
        GitHub Token
        <input type="password" name="github_token" autocomplete="off" placeholder="Paste new token to update">
      </label>
      <p class="hint">Current: <code>{masked_gh}</code></p>

      <label>
        Lex MCP Access Key
        <input type="password" name="remote_mcp_api_key" autocomplete="off" placeholder="Paste new key to update">
      </label>
      <p class="hint">Current: <code>{masked_key}</code></p>

      <label>
        Remote MCP URL
        <input type="url" name="remote_mcp_url" autocomplete="off" value="{current_url}">
      </label>
      <p class="hint">The hosted MCP server endpoint.</p>
    </section>

    <!-- System Info -->
    <section class="card">
      <div class="badge">System</div>
      <h2>System Information</h2>
      <div class="info-grid">
        <div class="info-row"><span class="info-label">lex-mcp-local</span><span class="info-value">{html.escape(version)}</span></div>
        <div class="info-row"><span class="info-label">Active mode</span><span class="info-value">{html.escape(mode)}</span></div>
        <div class="info-row"><span class="info-label">Mode override</span><span class="info-value">{override_str}</span></div>
        <div class="info-row"><span class="info-label">Python</span><span class="info-value">{html.escape(config['python_executable'])}</span></div>
        <div class="info-row"><span class="info-label">Project root</span><span class="info-value">{html.escape(config['project_root'])}</span></div>
        <div class="info-row"><span class="info-label">.env file</span><span class="info-value">{html.escape(config['env_file_path'])}</span></div>
        <div class="info-row"><span class="info-label">mcp.json</span><span class="info-value">{html.escape(config['mcp_config_path'])}</span></div>
      </div>
    </section>

    <button type="submit">Save changes</button>
  </form>

  <p class="footer">Press <strong>Ctrl+C</strong> in the terminal to close the dashboard server.</p>
</main>

<script>
(function() {{
  var cards = document.querySelectorAll('.mode-card');
  var hidden = document.getElementById('mcpModeInput');
  cards.forEach(function(card) {{
    card.addEventListener('click', function() {{
      cards.forEach(function(c) {{ c.classList.remove('selected'); }});
      card.classList.add('selected');
      var radio = card.querySelector('input[type="radio"]');
      if (radio) radio.checked = true;
      hidden.value = card.getAttribute('data-mode');
    }});
  }});
}})();
</script>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# Local HTTP server
# ---------------------------------------------------------------------------


def launch_ai_dashboard(
    project_root: Path,
    env_file_path: Path | None = None,
    python_executable: Path | None = None,
    mcp_config_path: Path | None = None,
    reporter: Callable[[str], None] | None = None,
    timeout_seconds: int = 3600,
    server_name: str = LEX_MCP_LOCAL_SERVER_NAME,
) -> None:
    """Start the dashboard HTTP server and open the browser.

    Blocks until the user presses Ctrl+C or *timeout_seconds* elapse.
    """
    from lex.tools.setup_with_ai import resolve_active_python_executable

    project_root = Path(project_root).resolve()
    env_path = (
        Path(env_file_path).resolve()
        if env_file_path is not None
        else (project_root / ".env").resolve()
    )
    py_exec = (
        Path(python_executable)
        if python_executable is not None
        else resolve_active_python_executable(project_root)
    )
    mcp_path = (
        Path(mcp_config_path).resolve()
        if mcp_config_path is not None
        else resolve_github_copilot_mcp_config_path().resolve()
    )
    report = reporter or (lambda msg: None)
    state = secrets.token_urlsafe(16)

    # Mutable feedback stored across requests.
    feedback: dict[str, Any] = {"successes": [], "errors": []}

    class DashboardHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            if parsed.path not in {"", "/"}:
                self.send_error(HTTPStatus.NOT_FOUND)
                return

            config = _read_dashboard_state(
                project_root, env_path, py_exec, mcp_path,
            )
            body = _build_dashboard_html(
                state=state,
                config=config,
                successes=feedback.pop("successes", None) or None,
                errors=feedback.pop("errors", None) or None,
            )
            encoded = body.encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def do_POST(self) -> None:
            if self.path != "/save":
                self.send_error(HTTPStatus.NOT_FOUND)
                return

            content_length = int(self.headers.get("Content-Length", "0"))
            payload = self.rfile.read(content_length).decode("utf-8")
            form_data = parse_qs(payload, keep_blank_values=True)

            if form_data.get("state", [""])[0] != state:
                self.send_error(HTTPStatus.FORBIDDEN, "State mismatch")
                return

            successes, errors = _handle_save(
                form_data, project_root, env_path, mcp_path,
                server_name=server_name,
            )
            feedback["successes"] = successes
            feedback["errors"] = errors

            # Redirect back to GET / so a page refresh doesn't resubmit.
            self.send_response(HTTPStatus.SEE_OTHER)
            self.send_header("Location", "/")
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, format: str, *args: Any) -> None:
            return  # silence request logs

    server = ThreadingHTTPServer(("127.0.0.1", 0), DashboardHandler)
    server.daemon_threads = True
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()

    url = f"http://127.0.0.1:{server.server_port}/"
    report(f"Dashboard running at {url}")
    try:
        opened = webbrowser.open(url, new=1, autoraise=True)
        if not opened:
            report("Could not open browser automatically. Paste the URL above into any browser.")
    except Exception as exc:
        report(f"Browser launch failed: {exc}")
        report("Paste the dashboard URL into any browser.")

    report("Press Ctrl+C to stop the dashboard server.")
    try:
        # Block until timeout or keyboard interrupt.
        threading.Event().wait(timeout=timeout_seconds)
    except KeyboardInterrupt:
        pass
    finally:
        server.shutdown()
        server.server_close()
        server_thread.join(timeout=5)
        report("Dashboard server stopped.")
