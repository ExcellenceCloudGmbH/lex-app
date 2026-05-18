"""Live validators for the ``lex setup-with-ai`` web flow.

These run server-side from the local setup HTTP handler. They only reach
out to the GitHub API and the configured remote MCP endpoint and return
small JSON-serialisable dataclasses describing the result.
"""

from __future__ import annotations

import json
import socket
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field
from typing import Iterable

# Scopes a Classic PAT must carry for the kickstart workflow to work.
# Mirrors the scope list baked into ``GITHUB_TOKEN_URL`` in
# ``setup_with_ai.py``. Keep the two in sync.
REQUIRED_GITHUB_SCOPES: tuple[str, ...] = (
    "repo",
    "workflow",
    "admin:org",
    "user",
)
RECOMMENDED_GITHUB_SCOPES: tuple[str, ...] = (
    "admin:repo_hook",
    "project",
    "read:audit_log",
)

_DEFAULT_TIMEOUT_SECONDS = 8.0
_USER_AGENT = "lex-setup-with-ai/1"


@dataclass(frozen=True)
class GithubTokenValidation:
    ok: bool
    login: str = ""
    name: str = ""
    avatar_url: str = ""
    scopes: tuple[str, ...] = ()
    missing_required_scopes: tuple[str, ...] = ()
    missing_recommended_scopes: tuple[str, ...] = ()
    error: str = ""

    def to_dict(self) -> dict:
        d = asdict(self)
        d["scopes"] = list(self.scopes)
        d["missing_required_scopes"] = list(self.missing_required_scopes)
        d["missing_recommended_scopes"] = list(self.missing_recommended_scopes)
        return d


@dataclass(frozen=True)
class RemoteMcpKeyValidation:
    ok: bool
    status_code: int = 0
    detail: str = ""
    error: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def _split_scope_header(value: str) -> tuple[str, ...]:
    if not value:
        return ()
    return tuple(s.strip() for s in value.split(",") if s.strip())


def _missing_scopes(
    granted: Iterable[str], required: Iterable[str]
) -> tuple[str, ...]:
    granted_set = {s.lower() for s in granted}
    missing: list[str] = []
    for scope in required:
        # GitHub token scopes are hierarchical: e.g. ``admin:org`` implies
        # ``write:org`` and ``read:org``; we only check exact membership for
        # now — false positives are non-fatal (we surface them as warnings,
        # not errors).
        if scope.lower() not in granted_set:
            missing.append(scope)
    return tuple(missing)


def validate_github_token(
    token: str, *, timeout: float = _DEFAULT_TIMEOUT_SECONDS
) -> GithubTokenValidation:
    token = (token or "").strip()
    if not token:
        return GithubTokenValidation(ok=False, error="Token is empty.")

    req = urllib.request.Request(
        "https://api.github.com/user",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": _USER_AGENT,
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8") or "{}")
            scopes = _split_scope_header(resp.headers.get("X-OAuth-Scopes", ""))
    except urllib.error.HTTPError as exc:
        if exc.code == 401:
            return GithubTokenValidation(ok=False, error="GitHub rejected the token (401 Unauthorized).")
        if exc.code == 403:
            return GithubTokenValidation(ok=False, error="GitHub returned 403 Forbidden — the token may be expired or rate-limited.")
        return GithubTokenValidation(ok=False, error=f"GitHub returned HTTP {exc.code}.")
    except (urllib.error.URLError, socket.timeout) as exc:
        return GithubTokenValidation(ok=False, error=f"Could not reach api.github.com: {exc}")
    except (json.JSONDecodeError, OSError) as exc:
        return GithubTokenValidation(ok=False, error=f"Unexpected response from GitHub: {exc}")

    return GithubTokenValidation(
        ok=True,
        login=str(payload.get("login", "")),
        name=str(payload.get("name") or ""),
        avatar_url=str(payload.get("avatar_url", "")),
        scopes=scopes,
        missing_required_scopes=_missing_scopes(scopes, REQUIRED_GITHUB_SCOPES),
        missing_recommended_scopes=_missing_scopes(scopes, RECOMMENDED_GITHUB_SCOPES),
    )


def validate_remote_mcp_key(
    url: str, api_key: str, *, timeout: float = _DEFAULT_TIMEOUT_SECONDS
) -> RemoteMcpKeyValidation:
    """Best-effort liveness check against the remote MCP endpoint.

    The remote MCP server speaks JSON-RPC over HTTP; we don't speak the
    protocol here, we only verify that the API key is accepted by the
    transport layer. A 200/204/405 (method not allowed) response means the
    endpoint accepted the auth header; 401/403 means the key is wrong.
    """
    api_key = (api_key or "").strip()
    if not api_key:
        return RemoteMcpKeyValidation(ok=False, error="API key is empty.")
    if not url:
        return RemoteMcpKeyValidation(ok=False, error="Remote MCP URL is empty.")

    req = urllib.request.Request(
        url,
        method="GET",
        headers={
            "Authorization": f"Bearer {api_key}",
            "User-Agent": _USER_AGENT,
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return RemoteMcpKeyValidation(
                ok=True,
                status_code=resp.status,
                detail="Remote MCP endpoint accepted the API key.",
            )
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            return RemoteMcpKeyValidation(
                ok=False,
                status_code=exc.code,
                error="Remote MCP rejected the API key.",
            )
        # 405 / 404 / 200-via-redirect: auth layer accepted us, the JSON-RPC
        # method is just not exposed via GET. That's fine for a liveness check.
        if exc.code in (404, 405, 406):
            return RemoteMcpKeyValidation(
                ok=True,
                status_code=exc.code,
                detail="Remote MCP reachable; key accepted at transport layer.",
            )
        return RemoteMcpKeyValidation(
            ok=False, status_code=exc.code, error=f"Remote MCP returned HTTP {exc.code}."
        )
    except (urllib.error.URLError, socket.timeout) as exc:
        return RemoteMcpKeyValidation(ok=False, error=f"Could not reach remote MCP: {exc}")
