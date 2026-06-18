"""Readiness probe and ASGI HTTP routing — liveness vs readiness separation.

Intent: the framework exposes two separate HTTP paths for Kubernetes probes:
``/health`` (fast liveness — no DB, 200 always) and ``/readiness`` (confirms
the default database is reachable). Keeping them separate ensures the pod is
not added to the Kubernetes Service before it can actually serve traffic.
``/health`` never touches Django/DB so the liveness probe answers instantly;
``/readiness`` returns 503 if the DB is unreachable so Kubernetes withholds
traffic. A regression that conflates the two paths, breaks the 503 path, or
routes HTTP requests incorrectly would cause either spurious pod restarts
(failing liveness when it shouldn't) or user-visible errors (traffic routed to
an unready pod).

Cluster 1u — scenarios 1.171–1.179. Type: U.
Covers: lex/lex_app/fast_health.py, lex/lex_app/asgi.py.
Run: python -m lex pytest lex/test_project/tests/init/test_1u_readiness_probe_and_asgi_routing.py -v
"""

from __future__ import annotations

import json
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from asgiref.sync import async_to_sync
from django.test import SimpleTestCase

pytestmark = pytest.mark.init


# ---------------------------------------------------------------------------
# ASGI helpers
# ---------------------------------------------------------------------------

def _make_http_scope(path: str) -> dict:
    return {"type": "http", "path": path}


async def _call_asgi(app, path: str) -> list[dict]:
    """Drive *app* with a minimal HTTP ASGI scope and collect sent messages."""
    scope = _make_http_scope(path)
    messages: list[dict] = []

    async def receive() -> dict:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: dict) -> None:
        messages.append(message)

    await app(scope, receive, send)
    return messages


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestCluster01u_ReadinessProbeAndAsgiRouting(SimpleTestCase):
    """Cluster 1u: readiness probe path-matching, response shape, and ASGI routing."""

    # -- 1.171 ---------------------------------------------------------
    def test_1_171_is_readiness_path_accepts_all_canonical_forms(self) -> None:
        """
        Scenario 1.171: is_readiness_path() recognises every registered path.
        Given: the four canonical readiness paths (with/without trailing slash,
               with/without /api/ prefix)
        When: is_readiness_path() is called for each
        Then: all four return True so the ASGI router dispatches them correctly.
        """
        from lex.lex_app.fast_health import is_readiness_path

        for path in ("/readiness", "/readiness/", "/api/readiness", "/api/readiness/"):
            self.assertTrue(
                is_readiness_path(path),
                f"is_readiness_path({path!r}) must return True — the Kubernetes "
                "readiness probe uses this path to decide whether to route traffic.",
            )

    # -- 1.172 ---------------------------------------------------------
    def test_1_172_is_readiness_path_rejects_non_readiness_paths(self) -> None:
        """
        Scenario 1.172: is_readiness_path() returns False for non-readiness paths.
        Given: the liveness path, root, a generic API path, and a similar-looking
               non-canonical string
        When: is_readiness_path() is called
        Then: all return False so the router falls through to the correct handler.
        """
        from lex.lex_app.fast_health import is_readiness_path

        for path in ("/health", "/api/health", "/", "/api/users", "/readiness_check", ""):
            self.assertFalse(
                is_readiness_path(path),
                f"is_readiness_path({path!r}) must return False",
            )

    # -- 1.173 ---------------------------------------------------------
    def test_1_173_readiness_asgi_app_returns_200_when_db_ready(self) -> None:
        """
        Scenario 1.173: readiness endpoint returns 200 and ready body when DB is up.
        Given: _database_ready() returns True
        When: readiness_asgi_app handles an HTTP request
        Then: status is 200 and body is {"status": "ready"} — the pod is considered
              Ready and Kubernetes will route traffic to it.
        """
        from lex.lex_app.fast_health import readiness_asgi_app

        with patch(
            "lex.lex_app.fast_health._database_ready",
            new=AsyncMock(return_value=True),
        ):
            messages = async_to_sync(_call_asgi)(readiness_asgi_app, "/readiness")

        self.assertEqual(
            messages[0]["status"],
            200,
            "DB is reachable; readiness probe must return 200 so Kubernetes routes traffic.",
        )
        body = json.loads(messages[1]["body"])
        self.assertEqual(
            body["status"],
            "ready",
            "Body must be {\"status\": \"ready\"} when the database is available.",
        )

    # -- 1.174 ---------------------------------------------------------
    def test_1_174_readiness_asgi_app_returns_503_when_db_unavailable(self) -> None:
        """
        Scenario 1.174: readiness endpoint returns 503 when DB is unreachable.
        Given: _database_ready() returns False (e.g., DB connection refused)
        When: readiness_asgi_app handles an HTTP request
        Then: status is 503 and body is {"status": "not-ready", "reason":
              "database-unavailable"} — Kubernetes withholds traffic until the DB
              comes up.
        """
        from lex.lex_app.fast_health import readiness_asgi_app

        with patch(
            "lex.lex_app.fast_health._database_ready",
            new=AsyncMock(return_value=False),
        ):
            messages = async_to_sync(_call_asgi)(readiness_asgi_app, "/readiness")

        self.assertEqual(
            messages[0]["status"],
            503,
            "DB unavailable; readiness probe must return 503 so Kubernetes withholds traffic.",
        )
        body = json.loads(messages[1]["body"])
        self.assertEqual(
            body["status"],
            "not-ready",
            "Status field must be 'not-ready' when the database is unavailable.",
        )
        self.assertEqual(
            body["reason"],
            "database-unavailable",
            "Reason field must be 'database-unavailable' so operators know the cause.",
        )

    # -- 1.175 ---------------------------------------------------------
    def test_1_175_readiness_response_has_no_cache_and_json_content_type(self) -> None:
        """
        Scenario 1.175: readiness response carries no-store cache-control and JSON
        content-type header.
        Given: _database_ready() returns True
        When: readiness_asgi_app sends the HTTP response start event
        Then: headers include cache-control: no-store and content-type: application/json
              so probers never cache a stale result.
        """
        from lex.lex_app.fast_health import readiness_asgi_app

        with patch(
            "lex.lex_app.fast_health._database_ready",
            new=AsyncMock(return_value=True),
        ):
            messages = async_to_sync(_call_asgi)(readiness_asgi_app, "/readiness")

        headers = dict(messages[0]["headers"])
        self.assertEqual(
            headers.get(b"cache-control"),
            b"no-store",
            "Readiness response must carry cache-control: no-store to prevent "
            "stale-probe caching.",
        )
        self.assertEqual(
            headers.get(b"content-type"),
            b"application/json",
            "Readiness response content-type must be application/json.",
        )

    # -- 1.176 ---------------------------------------------------------
    def test_1_176_database_ready_returns_false_on_exception(self) -> None:
        """
        Scenario 1.176: _database_ready() returns False when the DB cursor raises.
        Given: the default database connection raises an arbitrary exception on cursor
        When: _database_ready() is awaited
        Then: it returns False rather than propagating the exception — a DB blip
              must not crash the ASGI process; it simply reports not-ready.
        """
        from lex.lex_app.fast_health import _database_ready

        mock_conn = MagicMock()
        mock_conn.cursor.side_effect = Exception("DB connection refused")

        with patch("django.db.connections") as mock_connections:
            mock_connections.__getitem__ = MagicMock(return_value=mock_conn)
            result = async_to_sync(_database_ready)()

        self.assertFalse(
            result,
            "_database_ready() must return False when the DB cursor raises — "
            "any exception is treated as not-ready.",
        )

    # -- 1.177 ---------------------------------------------------------
    def test_1_177_http_application_routes_health_path_to_liveness_app(self) -> None:
        """
        Scenario 1.177: http_application() dispatches liveness paths to health_asgi_app.
        Given: an HTTP scope with path /health
        When: http_application() handles the request
        Then: health_asgi_app is called and django_asgi_app is NOT called — the fast
              liveness probe bypasses Django entirely.
        """
        from lex.lex_app import asgi as asgi_module

        mock_health = AsyncMock()
        mock_readiness = AsyncMock()
        mock_django = AsyncMock()

        with (
            patch.object(asgi_module, "health_asgi_app", mock_health),
            patch.object(asgi_module, "readiness_asgi_app", mock_readiness),
            patch.object(asgi_module, "django_asgi_app", mock_django),
        ):
            async_to_sync(asgi_module.http_application)(
                {"type": "http", "path": "/health"},
                AsyncMock(return_value={"type": "http.request", "more_body": False}),
                AsyncMock(),
            )

        mock_health.assert_called_once()
        mock_readiness.assert_not_called()
        mock_django.assert_not_called()

    # -- 1.178 ---------------------------------------------------------
    def test_1_178_http_application_routes_readiness_path_to_readiness_app(self) -> None:
        """
        Scenario 1.178: http_application() dispatches readiness paths to readiness_asgi_app.
        Given: an HTTP scope with path /readiness
        When: http_application() handles the request
        Then: readiness_asgi_app is called and django_asgi_app is NOT called — the
              readiness probe path is handled separately from normal requests.
        """
        from lex.lex_app import asgi as asgi_module

        mock_health = AsyncMock()
        mock_readiness = AsyncMock()
        mock_django = AsyncMock()

        with (
            patch.object(asgi_module, "health_asgi_app", mock_health),
            patch.object(asgi_module, "readiness_asgi_app", mock_readiness),
            patch.object(asgi_module, "django_asgi_app", mock_django),
        ):
            async_to_sync(asgi_module.http_application)(
                {"type": "http", "path": "/readiness"},
                AsyncMock(return_value={"type": "http.request", "more_body": False}),
                AsyncMock(),
            )

        mock_readiness.assert_called_once()
        mock_health.assert_not_called()
        mock_django.assert_not_called()

    # -- 1.179 ---------------------------------------------------------
    def test_1_179_http_application_routes_other_paths_to_django(self) -> None:
        """
        Scenario 1.179: http_application() falls through to django_asgi_app for
        ordinary paths.
        Given: an HTTP scope with a non-probe path (e.g. /api/users)
        When: http_application() handles the request
        Then: django_asgi_app is called and neither health_asgi_app nor
              readiness_asgi_app is called — normal traffic still reaches Django.
        """
        from lex.lex_app import asgi as asgi_module

        mock_health = AsyncMock()
        mock_readiness = AsyncMock()
        mock_django = AsyncMock()

        with (
            patch.object(asgi_module, "health_asgi_app", mock_health),
            patch.object(asgi_module, "readiness_asgi_app", mock_readiness),
            patch.object(asgi_module, "django_asgi_app", mock_django),
        ):
            async_to_sync(asgi_module.http_application)(
                {"type": "http", "path": "/api/users"},
                AsyncMock(return_value={"type": "http.request", "more_body": False}),
                AsyncMock(),
            )

        mock_django.assert_called_once()
        mock_health.assert_not_called()
        mock_readiness.assert_not_called()


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
