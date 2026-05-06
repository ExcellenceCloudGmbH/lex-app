"""ASGI entry point: auth middleware + FastMCP Streamable-HTTP app.

Usage from :mod:`lex.lex_app.asgi`::

    from lex.mcp_server.asgi import mcp_asgi_app, is_mcp_path

    if scope["type"] == "http" and is_mcp_path(scope["path"]):
        await mcp_asgi_app()(scope, receive, send)
        return
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Optional

from lex.mcp_server.auth import McpAuthError, resolve_principal
from lex.mcp_server.config import mcp_setting
from lex.mcp_server.context import bind_principal
from lex.mcp_server.ratelimit import get_rate_limiter

logger = logging.getLogger(__name__)

_APP = None
_INNER_APP = None
_LIFESPAN_READY: Optional[asyncio.Event] = None
_LIFESPAN_TASK: Optional[asyncio.Task] = None
_LIFESPAN_LOCK: Optional[asyncio.Lock] = None


def _mount_path() -> str:
    raw = (mcp_setting("MOUNT_PATH") or "/mcp").rstrip("/")
    return raw or "/mcp"


def is_mcp_path(path: str) -> bool:
    if not mcp_setting("ENABLED"):
        return False
    mount = _mount_path()
    return path == mount or path.startswith(mount + "/")


# ---------------------------------------------------------------------------
# RFC 9728 — OAuth 2.0 Protected Resource Metadata
# ---------------------------------------------------------------------------

_WELL_KNOWN_PREFIXES = (
    "/.well-known/oauth-protected-resource",
    "/.well-known/oauth-authorization-server",
)


def is_well_known_mcp_oauth_path(path: str) -> bool:
    """Return True for /.well-known/oauth-* paths that MCP clients query."""
    if not mcp_setting("ENABLED"):
        return False
    return any(path == p or path.startswith(p + "/") for p in _WELL_KNOWN_PREFIXES)


def _keycloak_issuer() -> Optional[str]:
    """Return the Keycloak issuer URL (e.g. https://auth.example.com/realms/lex)."""
    kc_url = os.getenv("KEYCLOAK_URL")
    kc_realm = os.getenv("KEYCLOAK_REALM")
    if kc_url and kc_realm:
        return f"{kc_url.rstrip('/')}/realms/{kc_realm}"
    return None


def _server_origin(scope: dict) -> str:
    """Reconstruct the origin (scheme + host) from the ASGI scope."""
    scheme = scope.get("scheme", "https")
    headers = dict(scope.get("headers", []))
    host = (headers.get(b"host") or b"localhost").decode("latin-1")
    return f"{scheme}://{host}"


async def _well_known_oauth(scope, receive, send) -> None:
    """Serve RFC 9728 OAuth metadata so MCP clients can discover Keycloak."""
    path = scope.get("path", "")
    issuer = _keycloak_issuer()

    if not issuer:
        await _send_json(send, 404, {"error": "OIDC not configured"})
        return

    origin = _server_origin(scope)
    mount = _mount_path()

    if path.startswith("/.well-known/oauth-authorization-server"):
        # RFC 8414 — Authorization Server Metadata
        # Redirect the client to Keycloak's own discovery document.
        # Most clients follow redirects, but we can also inline the essentials.
        oidc_config_url = f"{issuer}/.well-known/openid-configuration"
        body = json.dumps({"issuer": issuer, "_redirect": oidc_config_url}).encode()
        await send({
            "type": "http.response.start",
            "status": 302,
            "headers": [
                (b"location", oidc_config_url.encode("latin-1")),
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode("ascii")),
            ],
        })
        await send({"type": "http.response.body", "body": body})
        return

    # /.well-known/oauth-protected-resource[/mcp]
    resource = f"{origin}{mount}"
    metadata = {
        "resource": resource,
        "authorization_servers": [issuer],
        "bearer_methods_supported": ["header"],
        "scopes_supported": ["openid", "email", "profile"],
    }
    await _send_json(send, 200, metadata)


async def _send_json(send, status: int, payload: dict, *, headers: Optional[list] = None) -> None:
    body = json.dumps(payload).encode("utf-8")
    raw_headers = [
        (b"content-type", b"application/json"),
        (b"content-length", str(len(body)).encode("ascii")),
    ]
    if headers:
        raw_headers.extend(headers)
    await send({"type": "http.response.start", "status": status, "headers": raw_headers})
    await send({"type": "http.response.body", "body": body})


def _build_inner_app():
    global _INNER_APP
    if _INNER_APP is None:
        from lex.mcp_server.server import get_server

        server = get_server()
        _INNER_APP = server.streamable_http_app()
    return _INNER_APP


async def _ensure_lifespan_started(inner) -> None:
    """Drive the inner Starlette app's lifespan once per process.

    FastMCP's ``streamable_http_app`` ships a lifespan that calls
    ``StreamableHTTPSessionManager.run()``. Our outer ASGI wrapper only
    handles ``http`` scope, so without this helper the inner app never
    receives a ``lifespan.startup`` event and its task group stays
    uninitialised — which surfaces as ``RuntimeError: Task group is not
    initialized`` on the first request.
    """
    global _LIFESPAN_READY, _LIFESPAN_TASK, _LIFESPAN_LOCK

    if _LIFESPAN_READY is not None and _LIFESPAN_READY.is_set():
        return

    if _LIFESPAN_LOCK is None:
        _LIFESPAN_LOCK = asyncio.Lock()

    async with _LIFESPAN_LOCK:
        if _LIFESPAN_READY is not None and _LIFESPAN_READY.is_set():
            return

        ready = asyncio.Event()
        stop = asyncio.Event()

        async def _runner():
            try:
                async with inner.router.lifespan_context(inner):
                    ready.set()
                    await stop.wait()
            except Exception:  # noqa: BLE001
                logger.exception("MCP inner-app lifespan crashed")
                ready.set()  # unblock waiters; subsequent requests will fail loudly

        _LIFESPAN_READY = ready
        _LIFESPAN_TASK = asyncio.create_task(_runner(), name="mcp-lifespan")
        _LIFESPAN_TASK._mcp_stop_event = stop  # type: ignore[attr-defined]
        await ready.wait()


def _strip_prefix(scope: dict) -> dict:
    """Return a new scope with the MCP mount prefix stripped from the path."""
    mount = _mount_path()
    new_path = scope["path"][len(mount):] or "/"
    new_raw = scope.get("raw_path", scope["path"].encode("latin-1"))
    if isinstance(new_raw, bytes) and new_raw.startswith(mount.encode("latin-1")):
        new_raw = new_raw[len(mount):] or b"/"
    new_scope = dict(scope)
    new_scope["path"] = new_path
    new_scope["raw_path"] = new_raw
    new_scope["root_path"] = scope.get("root_path", "") + mount
    return new_scope


async def _asgi(scope, receive, send):
    if scope["type"] != "http":
        await _send_json(send, 400, {"error": "Only HTTP transport is supported on /mcp."})
        return

    headers = {k: v for k, v in scope.get("headers", [])}

    try:
        principal = await resolve_principal(headers)
    except McpAuthError as exc:
        # Build WWW-Authenticate with resource_metadata pointer (RFC 9728).
        origin = _server_origin(scope)
        mount = _mount_path()
        resource_meta_url = f"{origin}/.well-known/oauth-protected-resource{mount}"
        www_auth = f'{exc.www_authenticate} resource_metadata="{resource_meta_url}"'
        await _send_json(
            send,
            401,
            {"error": str(exc)},
            headers=[(b"www-authenticate", www_auth.encode("latin-1"))],
        )
        return

    decision = get_rate_limiter().acquire(principal)
    if not decision.allowed:
        await _send_json(
            send,
            429,
            {
                "error": "rate_limited",
                "current": decision.current,
                "limit": decision.limit,
                "retry_after": decision.retry_after_seconds,
            },
            headers=[(b"retry-after", str(decision.retry_after_seconds).encode("ascii"))],
        )
        return

    inner = _build_inner_app()
    await _ensure_lifespan_started(inner)
    inner_scope = _strip_prefix(scope)

    with bind_principal(principal):
        await inner(inner_scope, receive, send)


def mcp_asgi_app():
    """Return the (cached) ASGI callable for the MCP server."""
    global _APP
    if _APP is None:
        _APP = _asgi
    return _APP
