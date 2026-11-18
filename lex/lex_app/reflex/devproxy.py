"""One port for a development run: the backend passes every other request to the frontend server.

A pod exposes one port, and a Reflex development run has two servers -- the
frontend's dev server and the backend. ``REFLEX_USE_SINGLE_PORT`` (the switch a
deployment already sets) has the backend answer its own routes and pass
everything else, HTTP and WebSocket alike (the dev server's hot reload is a
WebSocket), to the frontend on ``localhost:<frontend port>``. Reflex
Enterprise shipped this; plain Reflex only offers one port in production mode,
which serves the compiled frontend itself and needs none of it (decision E7).

The proxy is mounted on the backend's own Starlette app (``app._api``) after
every backend route, inside every ``api_transformer`` -- so the sign-in routes
and security headers apply to proxied pages too -- once the server starts.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
from typing import Any, AsyncIterator

logger = logging.getLogger(__name__)

_TRUTHY = {"1", "true", "yes", "y", "on"}

#: Request headers never passed on: hop-by-hop, or rewritten for the upstream.
_DROPPED = frozenset({b"host", b"connection", b"keep-alive", b"transfer-encoding", b"upgrade",
                      b"proxy-connection", b"te", b"trailer"})

#: How long a request waits for a frontend server that is still starting.
CONNECT_RETRIES = 25
RETRY_DELAY = 0.3


def enabled() -> bool:
    """Whether this process should proxy the frontend: asked for, in development, not backend-only."""
    if (os.getenv("REFLEX_USE_SINGLE_PORT") or "").strip().lower() not in _TRUTHY:
        return False
    try:
        from reflex.utils.exec import is_prod_mode

        if is_prod_mode():
            return False
    except Exception:  # noqa: BLE001
        pass
    try:
        from reflex_base.environment import environment

        return not environment.REFLEX_BACKEND_ONLY.get()  # `reflex run --backend-only` sets it
    except Exception:  # noqa: BLE001
        return True


class FrontendProxy:
    """An ASGI app passing HTTP and WebSocket requests to ``upstream`` (``http://localhost:3000``)."""

    def __init__(self, upstream: str) -> None:
        self.upstream = upstream.rstrip("/")
        self._client: Any = None

    async def __aenter__(self) -> "FrontendProxy":
        import httpx

        self._client = httpx.AsyncClient(base_url=self.upstream, timeout=None, follow_redirects=False)
        return self

    async def __aexit__(self, *exc: Any) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] == "http":
            await self._http(scope, receive, send)
        elif scope["type"] == "websocket":
            await self._websocket(scope, receive, send)

    @staticmethod
    def _target(scope: Any) -> str:
        path = scope.get("root_path", "") + scope.get("path", "/")
        query = scope.get("query_string", b"").decode("latin-1")
        return path + (f"?{query}" if query else "")

    async def _http(self, scope: Any, receive: Any, send: Any) -> None:
        import httpx

        body = b""
        while True:
            message = await receive()
            body += message.get("body", b"")
            if not message.get("more_body"):
                break
        headers = [(k, v) for k, v in scope.get("headers") or [] if k.lower() not in _DROPPED]
        response = None
        for _ in range(CONNECT_RETRIES):
            # A fresh request each try: a sent request's body is spent.
            request = self._client.build_request(scope["method"], self._target(scope), headers=headers, content=body)
            try:
                response = await self._client.send(request, stream=True)
                break
            except httpx.ConnectError:
                await asyncio.sleep(RETRY_DELAY)
        if response is None:
            await send({"type": "http.response.start", "status": 502,
                        "headers": [(b"content-type", b"text/plain; charset=utf-8")]})
            await send({"type": "http.response.body", "body": b"The frontend server is not answering yet."})
            return
        try:
            # The body goes on decoded (aiter_bytes), so the encoding and length headers that
            # described the upstream's bytes are dropped with it.
            out = [(k.encode("latin-1"), v.encode("latin-1")) for k, v in response.headers.multi_items()
                   if k.lower() not in ("connection", "transfer-encoding", "content-encoding", "content-length")]
            await send({"type": "http.response.start", "status": response.status_code, "headers": out})
            async for chunk in response.aiter_bytes():
                await send({"type": "http.response.body", "body": chunk, "more_body": True})
            await send({"type": "http.response.body", "body": b""})
        finally:
            await response.aclose()

    async def _websocket(self, scope: Any, receive: Any, send: Any) -> None:
        try:
            from websockets.asyncio.client import connect
        except ImportError:  # websockets < 13
            from websockets import connect  # type: ignore[no-redef]

        first = await receive()
        if first.get("type") != "websocket.connect":
            return
        url = "ws" + self.upstream[len("http"):] + self._target(scope)
        protocols = list(scope.get("subprotocols") or [])
        try:
            upstream = await connect(url, subprotocols=protocols or None)
        except Exception:  # noqa: BLE001 - the dev server is not there: refuse the socket
            await send({"type": "websocket.close", "code": 1011})
            return
        accept: dict[str, Any] = {"type": "websocket.accept"}
        if getattr(upstream, "subprotocol", None):
            accept["subprotocol"] = upstream.subprotocol
        await send(accept)

        async def to_upstream() -> None:
            while True:
                message = await receive()
                if message["type"] == "websocket.disconnect":
                    await upstream.close()
                    return
                await upstream.send(message.get("text") if message.get("text") is not None else message.get("bytes"))

        async def to_client() -> None:
            async for data in upstream:
                key = "text" if isinstance(data, str) else "bytes"
                await send({"type": "websocket.send", key: data})
            await send({"type": "websocket.close", "code": 1000})

        tasks = [asyncio.ensure_future(to_upstream()), asyncio.ensure_future(to_client())]
        try:
            await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for task in tasks:
                task.cancel()
            with contextlib.suppress(Exception):
                await upstream.close()


@contextlib.asynccontextmanager
async def _proxy_lifespan(app: Any) -> AsyncIterator[None]:
    """Mount the proxy on the backend (``app`` is the Reflex app) for the server's life."""
    from reflex.config import get_config

    proxy = FrontendProxy(f"http://localhost:{get_config().frontend_port}")
    app._api.mount("/", proxy)  # noqa: SLF001 - after every backend route, inside every transformer
    logger.info("Single-port mode: the backend passes pages on to %s", proxy.upstream)
    async with proxy:
        yield


def install(app: Any) -> None:
    """Proxy the frontend through the backend when :func:`enabled` (``create_app`` calls it)."""
    if enabled():
        app.register_lifespan_task(_proxy_lifespan)


__all__ = ["FrontendProxy", "enabled", "install"]
