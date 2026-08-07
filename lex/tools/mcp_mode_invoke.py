"""Invoke the MCP server's ``switch_to_mode`` behaviour from outside the server.

The in-process ``switch_to_mode`` MCP tool (defined in every mode package
under ``C:/.../lex-mcp-local/src/lex_mcp_*``) performs three steps:

1. ``write_override(mode, ...)`` — persist the one-shot override marker.
2. ``apply_mode_change_to_external_state(mode, ...)`` — eagerly sync
   ``.env`` files, every reachable ``mcp.json``, and IDE caches.
3. ``crash_and_reboot(mode)`` / ``live_switch_mode(mode)`` — surface the
   new tool list to the IDE.

When the lex-app side (``ai-verify`` / ``ai-dashboard``) needs to align the
running MCP mode with the project ``.env`` (the SoT), it cannot speak the
in-process JSON-RPC handshake — but it *can* run the same primitives. This
module exposes :func:`invoke_switch_to_mode`, which:

* Calls the canonical ``lex_mcp.mode_switch`` helpers if the installed
  ``lex-mcp-local`` package exposes them.
* Falls back to local lex-app helpers (already used by the dashboard) when
  ``lex_mcp.mode_switch`` is unavailable.
* Stops the running MCP server PID so the IDE re-launches the subprocess
  and consumes the override on the fresh start.

The result is structurally equivalent to invoking the MCP ``switch_to_mode``
tool, without requiring a running client/server session.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from lex.tools.setup_with_ai import (
    LEX_MCP_LOCAL_SERVER_NAME,
)


SUPPORTED_MCP_MODES: tuple[str, ...] = (
    "brief",
    "forward",
    "backward",
    "edit",
    "review",
    "test",
    "input",
    "mvp_generator",
    "mvp_completion",
)


@dataclass
class InvokeSwitchResult:
    """Structured outcome of :func:`invoke_switch_to_mode`."""

    target_mode: str
    strategy: str = "noop"          # "lex_mcp" | "fallback" | "noop"
    override_written: bool = False
    env_files_updated: tuple[str, ...] = ()
    env_files_inspected: tuple[str, ...] = ()
    mcp_json_updated: tuple[str, ...] = ()
    mcp_json_inspected: tuple[str, ...] = ()
    ide_caches_cleared: tuple[str, ...] = ()
    server_stopped: bool = False
    errors: tuple[str, ...] = field(default_factory=tuple)

    @property
    def ok(self) -> bool:
        return not self.errors


def _normalise_mode(mode: str) -> str:
    candidate = (mode or "").strip().lower()
    if candidate not in SUPPORTED_MCP_MODES:
        raise ValueError(
            f"Unsupported MCP mode {mode!r}; expected one of "
            f"{', '.join(SUPPORTED_MCP_MODES)}."
        )
    return candidate


def invoke_switch_to_mode(
    target_mode: str,
    *,
    project_root: Path,
    mcp_config_path: Path,
    source_tool: str = "lex-app",
    reason: str = "",
    server_name: str = LEX_MCP_LOCAL_SERVER_NAME,
    stop_server: bool = True,
    extra_env_paths: Iterable[Path] | None = None,
    extra_mcp_json_paths: Iterable[Path] | None = None,
) -> InvokeSwitchResult:
    """Run the same steps the in-MCP ``switch_to_mode`` tool runs.

    Parameters
    ----------
    target_mode:
        Mode to switch to (must be in :data:`SUPPORTED_MCP_MODES`).
    project_root:
        Project root whose ``.env`` should be treated as the canonical
        target. Forwarded as ``LEX_MCP_PROJECT_DIR`` to the lex_mcp
        helpers so the project ``.env`` is always in scope.
    mcp_config_path:
        Path to the IDE ``mcp.json`` (e.g. PyCharm Copilot config).
        Added explicitly to the sync list so the SoT-aligned mode is
        guaranteed to be written even if auto-discovery misses it.
    source_tool / reason:
        Recorded in the override-file payload for traceability.
    stop_server:
        When *True* (default), terminate any running MCP server PID so
        the IDE auto-restarts it on the next interaction and consumes
        the override.
    """
    target = _normalise_mode(target_mode)
    result = InvokeSwitchResult(target_mode=target)

    project_env = (Path(project_root) / ".env").resolve()
    extra_env_list = [str(project_env)]
    for extra in extra_env_paths or ():
        try:
            extra_env_list.append(str(Path(extra).resolve()))
        except Exception:  # pragma: no cover - defensive
            continue

    extra_mcp_list = [str(Path(mcp_config_path).resolve())]
    for extra in extra_mcp_json_paths or ():
        try:
            extra_mcp_list.append(str(Path(extra).resolve()))
        except Exception:  # pragma: no cover - defensive
            continue

    used_lex_mcp = False

    # --- 1) Try the canonical lex_mcp helpers ---------------------------------
    try:
        import os as _os

        from lex_mcp.mode_switch import (  # type: ignore[import-not-found]
            apply_mode_change_to_external_state,
            write_override,
        )

        # Make sure the project's .env is picked up by sync_env_var.
        prev_project_dir = _os.environ.get("LEX_MCP_PROJECT_DIR")
        _os.environ["LEX_MCP_PROJECT_DIR"] = str(Path(project_root).resolve())
        try:
            write_override(target, reason=reason, source_tool=source_tool)
            result.override_written = True
            report = apply_mode_change_to_external_state(
                target,
                extra_env_paths=extra_env_list,
                extra_mcp_json_paths=extra_mcp_list,
            )
        finally:
            if prev_project_dir is None:
                _os.environ.pop("LEX_MCP_PROJECT_DIR", None)
            else:
                _os.environ["LEX_MCP_PROJECT_DIR"] = prev_project_dir

        env_report = report.get("env") or {}
        result.env_files_updated = tuple(env_report.get("files_updated", ()) or ())
        result.env_files_inspected = tuple(env_report.get("files_inspected", ()) or ())
        result.mcp_json_updated = tuple(report.get("mcp_json_updated", ()) or ())
        result.mcp_json_inspected = tuple(report.get("mcp_json_inspected", ()) or ())
        result.ide_caches_cleared = tuple(report.get("ide_caches_cleared", ()) or ())
        result.strategy = "lex_mcp"
        used_lex_mcp = True
    except Exception as exc:
        result.errors = result.errors + (
            f"lex_mcp.mode_switch unavailable ({exc!r}); falling back.",
        )

    # --- 2) Fallback to local lex-app primitives if needed --------------------
    if not used_lex_mcp:
        try:
            from lex.tools.ai_dashboard import (
                _invalidate_copilot_mcp_cache,
                _update_mcp_json_mode,
                _write_mode_override,
            )
            from lex.tools.setup_with_ai import update_env_file

            _write_mode_override(target) if False else None  # keep symbol used
            # Write override directly (mirrors _write_mode_override but
            # records our source_tool / reason).
            from lex.tools.ai_dashboard import MODE_OVERRIDE_DIR, MODE_OVERRIDE_FILE
            import json as _json
            import os as _os
            MODE_OVERRIDE_DIR.mkdir(parents=True, exist_ok=True)
            MODE_OVERRIDE_FILE.write_text(
                _json.dumps(
                    {
                        "mode": target,
                        "reason": reason,
                        "source_tool": source_tool,
                        "pid": _os.getpid(),
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )
            result.override_written = True

            update_env_file(project_env, {"LEX_MCP_MODE": target})
            result.env_files_updated = (str(project_env),)
            result.env_files_inspected = (str(project_env),)

            mcp_path = Path(mcp_config_path)
            if _update_mcp_json_mode(mcp_path, target, server_name=server_name):
                result.mcp_json_updated = (str(mcp_path),)
            result.mcp_json_inspected = (str(mcp_path),)

            if _invalidate_copilot_mcp_cache(mcp_path, server_name=server_name):
                result.ide_caches_cleared = (str(mcp_path),)

            result.strategy = "fallback"
        except Exception as exc:
            result.errors = result.errors + (f"Fallback sync failed: {exc!r}",)

    # --- 3) Stop the running MCP server PID -----------------------------------
    if stop_server:
        try:
            from lex.tools.ai_dashboard import _stop_mcp_server
            result.server_stopped = bool(
                _stop_mcp_server(Path(mcp_config_path), server_name=server_name)
            )
        except Exception as exc:
            result.errors = result.errors + (
                f"Server stop step failed: {exc!r}",
            )

    return result

