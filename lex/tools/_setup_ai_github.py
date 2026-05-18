"""GitHub OAuth Device Flow client (stdlib-only).

Used by the ``lex setup-with-ai`` web wizard so the user can sign in with
GitHub instead of pasting a personal access token. No new dependencies —
this module talks to the GitHub Device Flow endpoints with
``urllib.request``.

The OAuth client_id is configurable via the ``LEX_GITHUB_OAUTH_CLIENT_ID``
environment variable. A registered "Lex CLI" GitHub OAuth App's client ID
should eventually be baked into ``DEFAULT_LEX_GITHUB_OAUTH_CLIENT_ID``;
until then, the device flow gracefully reports that it is unavailable and
the wizard falls back to the PAT paste flow.
"""

from __future__ import annotations

import json
import os
import socket
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from typing import Mapping


# Placeholder — replace with the real Lex CLI GitHub OAuth App client_id
# once the app is registered. Until then, the wizard falls back to PAT.
DEFAULT_LEX_GITHUB_OAUTH_CLIENT_ID = ""

# Scopes the Lex workflow needs. Mirrors GITHUB_TOKEN_URL in setup_with_ai.py.
DEFAULT_DEVICE_FLOW_SCOPES = (
    "repo",
    "workflow",
    "admin:org",
    "admin:repo_hook",
    "user",
    "project",
    "read:audit_log",
)

_DEVICE_CODE_URL = "https://github.com/login/device/code"
_ACCESS_TOKEN_URL = "https://github.com/login/oauth/access_token"
_DEFAULT_TIMEOUT_SECONDS = 10.0
_USER_AGENT = "lex-setup-with-ai/1"


class DeviceFlowError(RuntimeError):
    pass


class DeviceFlowUnavailable(DeviceFlowError):
    """Raised when no GitHub OAuth client_id is configured."""


@dataclass(frozen=True)
class DeviceCode:
    device_code: str
    user_code: str
    verification_uri: str
    verification_uri_complete: str
    expires_in: int
    interval: int

    def to_public_dict(self) -> dict:
        # Never expose ``device_code`` to the browser — keep it server-side
        # and reference it by an opaque session id.
        return {
            "user_code": self.user_code,
            "verification_uri": self.verification_uri,
            "verification_uri_complete": self.verification_uri_complete,
            "expires_in": self.expires_in,
            "interval": self.interval,
        }


@dataclass(frozen=True)
class DevicePollResult:
    status: str  # "authorized" | "pending" | "slow_down" | "expired" | "denied" | "error"
    access_token: str = ""
    token_type: str = ""
    scopes: tuple[str, ...] = ()
    interval: int = 0
    error: str = ""

    def to_public_dict(self) -> dict:
        d = asdict(self)
        d["scopes"] = list(self.scopes)
        # Never expose the access token to the polling response body unless
        # the caller (the local handler) decides to. The handler caches the
        # token server-side and signals the SPA via a separate ready event.
        return d


def resolve_client_id(env: Mapping[str, str] | None = None) -> str:
    env = env or os.environ
    return (env.get("LEX_GITHUB_OAUTH_CLIENT_ID") or DEFAULT_LEX_GITHUB_OAUTH_CLIENT_ID).strip()


def is_device_flow_available(env: Mapping[str, str] | None = None) -> bool:
    return bool(resolve_client_id(env=env))


def _post_form(url: str, data: dict, *, timeout: float) -> dict:
    body = urllib.parse.urlencode(data).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Accept": "application/json",
            "Content-Type": "application/x-www-form-urlencoded",
            "User-Agent": _USER_AGENT,
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8") or "{}"
    except urllib.error.HTTPError as exc:
        raise DeviceFlowError(f"GitHub returned HTTP {exc.code} from {url}.") from exc
    except (urllib.error.URLError, socket.timeout) as exc:
        raise DeviceFlowError(f"Could not reach {url}: {exc}") from exc
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise DeviceFlowError(f"Unexpected response from {url}: {raw[:200]!r}") from exc
    if not isinstance(payload, dict):
        raise DeviceFlowError(f"Unexpected response shape from {url}.")
    return payload


def start_device_flow(
    *,
    scopes: tuple[str, ...] = DEFAULT_DEVICE_FLOW_SCOPES,
    env: Mapping[str, str] | None = None,
    timeout: float = _DEFAULT_TIMEOUT_SECONDS,
) -> DeviceCode:
    client_id = resolve_client_id(env=env)
    if not client_id:
        raise DeviceFlowUnavailable(
            "GitHub Device Flow is not configured. Set LEX_GITHUB_OAUTH_CLIENT_ID "
            "or paste a personal access token instead."
        )
    payload = _post_form(
        _DEVICE_CODE_URL,
        {"client_id": client_id, "scope": " ".join(scopes)},
        timeout=timeout,
    )
    if "device_code" not in payload:
        raise DeviceFlowError(
            f"GitHub did not return a device code: {payload.get('error_description') or payload}"
        )
    return DeviceCode(
        device_code=str(payload["device_code"]),
        user_code=str(payload.get("user_code", "")),
        verification_uri=str(payload.get("verification_uri", "https://github.com/login/device")),
        verification_uri_complete=str(
            payload.get("verification_uri_complete")
            or payload.get("verification_uri", "https://github.com/login/device")
        ),
        expires_in=int(payload.get("expires_in", 900)),
        interval=int(payload.get("interval", 5)),
    )


def poll_device_flow(
    device_code: str,
    *,
    env: Mapping[str, str] | None = None,
    timeout: float = _DEFAULT_TIMEOUT_SECONDS,
) -> DevicePollResult:
    client_id = resolve_client_id(env=env)
    if not client_id:
        raise DeviceFlowUnavailable("GitHub Device Flow is not configured.")
    payload = _post_form(
        _ACCESS_TOKEN_URL,
        {
            "client_id": client_id,
            "device_code": device_code,
            "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
        },
        timeout=timeout,
    )
    if "access_token" in payload:
        scope_str = str(payload.get("scope", "")).replace(",", " ")
        return DevicePollResult(
            status="authorized",
            access_token=str(payload["access_token"]),
            token_type=str(payload.get("token_type", "bearer")),
            scopes=tuple(s for s in scope_str.split() if s),
        )
    error = str(payload.get("error", "") or "")
    if error == "authorization_pending":
        return DevicePollResult(status="pending")
    if error == "slow_down":
        return DevicePollResult(status="slow_down", interval=int(payload.get("interval", 5)))
    if error == "expired_token":
        return DevicePollResult(status="expired", error="The device code expired before authorization completed.")
    if error == "access_denied":
        return DevicePollResult(status="denied", error="Authorization was denied.")
    return DevicePollResult(
        status="error",
        error=str(payload.get("error_description") or error or "Unknown error from GitHub."),
    )
