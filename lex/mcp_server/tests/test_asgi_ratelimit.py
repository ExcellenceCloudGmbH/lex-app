"""Verify the ASGI dispatcher returns HTTP 429 once the limit is hit."""
from __future__ import annotations

import asyncio
import json
from unittest import mock

import django
import pytest
from django.conf import settings


def _ensure_django():
    if not settings.configured:
        settings.configure(
            DEBUG=True,
            INSTALLED_APPS=[],
            DATABASES={"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}},
            USE_TZ=True,
            CACHES={
                "default": {
                    "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
                    "LOCATION": "lexmcp-asgi-rl-tests",
                },
            },
            MCP_SERVER={
                "ENABLED": True,
                "MOUNT_PATH": "/mcp",
                "RATE_LIMIT_ENABLED": True,
                "RATE_LIMIT_PER_MINUTE": 2,
                "RATE_LIMIT_BURST": 0,
                "RATE_LIMIT_CACHE": "default",
                "RATE_LIMIT_NAMESPACE": "lexmcp:rl:asgi-test",
            },
        )
        django.setup()
    else:
        settings.MCP_SERVER = {
            "ENABLED": True,
            "MOUNT_PATH": "/mcp",
            "RATE_LIMIT_ENABLED": True,
            "RATE_LIMIT_PER_MINUTE": 2,
            "RATE_LIMIT_BURST": 0,
            "RATE_LIMIT_CACHE": "default",
            "RATE_LIMIT_NAMESPACE": "lexmcp:rl:asgi-test",
        }


_ensure_django()

from django.core.cache import caches  # noqa: E402

from lex.mcp_server import ratelimit  # noqa: E402
from lex.mcp_server.context import McpPrincipal  # noqa: E402


@pytest.fixture(autouse=True)
def _reset_state():
    _ensure_django()  # restore MCP_SERVER in case a sibling test mutated it
    caches["default"].clear()
    ratelimit.reset_rate_limiter()
    yield
    ratelimit.reset_rate_limiter()
    caches["default"].clear()


async def _drive_one(asgi_app, principal):
    sent = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        sent.append(message)

    scope = {
        "type": "http",
        "method": "POST",
        "path": "/mcp",
        "raw_path": b"/mcp",
        "headers": [(b"api-key", b"sentinel")],
    }

    async def _fake_resolve(headers):
        return principal

    inner = mock.AsyncMock()  # would be invoked when allowed; we don't need it

    async def _noop_lifespan(_inner):
        return None

    with mock.patch("lex.mcp_server.asgi.resolve_principal", _fake_resolve), \
         mock.patch("lex.mcp_server.asgi._build_inner_app", return_value=inner), \
         mock.patch("lex.mcp_server.asgi._ensure_lifespan_started", _noop_lifespan):
        await asgi_app(scope, receive, send)
    return sent, inner


def test_third_request_returns_429():
    from lex.mcp_server.asgi import _asgi

    principal = McpPrincipal(user=mock.Mock(is_authenticated=True), auth_kind="api_key",
                             api_key_name="alice")

    # First two: allowed (inner called, no 429 emitted by us)
    for _ in range(2):
        sent, inner = asyncio.run(_drive_one(_asgi, principal))
        # inner mock was called — limiter let it through
        inner.assert_awaited()

    # Third: rejected with 429
    sent, inner = asyncio.run(_drive_one(_asgi, principal))
    assert sent[0]["status"] == 429
    body = json.loads(sent[1]["body"])
    assert body["error"] == "rate_limited"
    assert body["limit"] == 2
    assert body["retry_after"] >= 1
    inner.assert_not_called()
    # And Retry-After header set
    headers = dict(sent[0]["headers"])
    assert b"retry-after" in headers
