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
        await _send_json(
            send,
            401,
            {"error": str(exc)},
            headers=[(b"www-authenticate", exc.www_authenticate.encode("latin-1"))],
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
