"""Unit tests for the MCP rate limiter."""
from __future__ import annotations

from unittest import mock

import django
import pytest
from django.conf import settings


def _ensure_django(per_minute=5, burst=0):
    if not settings.configured:
        settings.configure(
            DEBUG=True,
            INSTALLED_APPS=[],
            DATABASES={"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}},
            USE_TZ=True,
            CACHES={
                "default": {
                    "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
                    "LOCATION": "lexmcp-rl-tests",
                },
            },
            MCP_SERVER={
                "RATE_LIMIT_ENABLED": True,
                "RATE_LIMIT_PER_MINUTE": per_minute,
                "RATE_LIMIT_BURST": burst,
                "RATE_LIMIT_CACHE": "default",
                "RATE_LIMIT_NAMESPACE": "lexmcp:rl:test",
            },
        )
        django.setup()
    else:
        settings.MCP_SERVER = {
            "RATE_LIMIT_ENABLED": True,
            "RATE_LIMIT_PER_MINUTE": per_minute,
            "RATE_LIMIT_BURST": burst,
            "RATE_LIMIT_CACHE": "default",
            "RATE_LIMIT_NAMESPACE": "lexmcp:rl:test",
        }


_ensure_django()

from django.core.cache import caches  # noqa: E402

from lex.mcp_server.context import McpPrincipal  # noqa: E402
from lex.mcp_server import ratelimit  # noqa: E402


def _principal(name="alice"):
    return McpPrincipal(
        user=mock.Mock(is_authenticated=True),
        auth_kind="api_key",
        api_key_name=name,
    )


@pytest.fixture(autouse=True)
def _reset_state():
    caches["default"].clear()
    ratelimit.reset_rate_limiter()
    yield
    ratelimit.reset_rate_limiter()
    caches["default"].clear()


def test_under_limit_allowed():
    _ensure_django(per_minute=5, burst=0)
    limiter = ratelimit.get_rate_limiter()
    p = _principal()
    decisions = [limiter.acquire(p) for _ in range(5)]
    assert all(d.allowed for d in decisions)
    assert decisions[-1].current == 5
    assert decisions[-1].limit == 5


def test_over_limit_denied_with_retry_after():
    _ensure_django(per_minute=2, burst=0)
    limiter = ratelimit.get_rate_limiter()
    p = _principal()
    limiter.acquire(p)
    limiter.acquire(p)
    third = limiter.acquire(p)
    assert third.allowed is False
    assert third.current == 3
    assert third.retry_after_seconds >= 1


def test_principals_are_isolated():
    _ensure_django(per_minute=1, burst=0)
    limiter = ratelimit.get_rate_limiter()
    a = _principal("alice")
    b = _principal("bob")
    assert limiter.acquire(a).allowed is True
    assert limiter.acquire(b).allowed is True
    assert limiter.acquire(a).allowed is False
    assert limiter.acquire(b).allowed is False


def test_cache_failure_fails_open():
    _ensure_django(per_minute=1, burst=0)
    limiter = ratelimit.get_rate_limiter()
    p = _principal()
    with mock.patch.object(limiter._cache, "incr", side_effect=RuntimeError("boom")), \
         mock.patch.object(limiter._cache, "add", side_effect=RuntimeError("boom")):
        decision = limiter.acquire(p)
    assert decision.allowed is True


def test_disabled_short_circuits():
    settings.MCP_SERVER = {**settings.MCP_SERVER, "RATE_LIMIT_ENABLED": False}
    ratelimit.reset_rate_limiter()
    limiter = ratelimit.get_rate_limiter()
    p = _principal()
    for _ in range(50):
        assert limiter.acquire(p).allowed is True
