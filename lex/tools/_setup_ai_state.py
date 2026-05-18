"""Persistent, non-secret defaults for the ``lex setup-with-ai`` web flow.

Stores last-used choices (mode, remote MCP URL, last project root) in
``~/.config/lex/setup-with-ai.json`` so returning users see one-click
defaults. **No secrets are ever written here** — tokens and API keys are
deliberately excluded; the dataclass below has no field for them.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Mapping


_FORBIDDEN_KEYS = frozenset(
    {
        "github_token",
        "remote_mcp_api_key",
        "token",
        "api_key",
        "password",
        "secret",
    }
)


@dataclass(frozen=True)
class SetupWithAILastUsed:
    mcp_mode: str = "forward"
    remote_mcp_url: str = ""
    last_project_root: str = ""
    last_lex_mcp_local_version: str = ""
    prefer_pat: bool = False  # remember user's auth preference (device-flow vs PAT)
    extras: Mapping[str, Any] = field(default_factory=dict)


def _resolve_settings_path(env: Mapping[str, str] | None = None) -> Path:
    env = env or os.environ
    override = env.get("LEX_SETUP_AI_STATE_PATH")
    if override:
        return Path(override).expanduser().resolve()
    xdg = env.get("XDG_CONFIG_HOME")
    base = Path(xdg).expanduser() if xdg else Path.home() / ".config"
    return (base / "lex" / "setup-with-ai.json").resolve()


def load_last_used(env: Mapping[str, str] | None = None) -> SetupWithAILastUsed:
    path = _resolve_settings_path(env=env)
    if not path.exists():
        return SetupWithAILastUsed()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return SetupWithAILastUsed()
    if not isinstance(raw, dict):
        return SetupWithAILastUsed()
    # Defensive: ignore any forbidden keys someone may have hand-edited in.
    raw = {k: v for k, v in raw.items() if k.lower() not in _FORBIDDEN_KEYS}
    return SetupWithAILastUsed(
        mcp_mode=str(raw.get("mcp_mode", "forward")) or "forward",
        remote_mcp_url=str(raw.get("remote_mcp_url", "") or ""),
        last_project_root=str(raw.get("last_project_root", "") or ""),
        last_lex_mcp_local_version=str(raw.get("last_lex_mcp_local_version", "") or ""),
        prefer_pat=bool(raw.get("prefer_pat", False)),
        extras={
            k: v
            for k, v in raw.items()
            if k
            not in {
                "mcp_mode",
                "remote_mcp_url",
                "last_project_root",
                "last_lex_mcp_local_version",
                "prefer_pat",
            }
        },
    )


def save_last_used(
    settings: SetupWithAILastUsed,
    env: Mapping[str, str] | None = None,
) -> Path:
    path = _resolve_settings_path(env=env)
    payload = asdict(settings)
    # Final defensive sweep: never persist anything that looks like a secret.
    for forbidden in _FORBIDDEN_KEYS:
        payload.pop(forbidden, None)
    if isinstance(payload.get("extras"), dict):
        payload["extras"] = {
            k: v for k, v in payload["extras"].items() if k.lower() not in _FORBIDDEN_KEYS
        }
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp_path, path)
    return path
