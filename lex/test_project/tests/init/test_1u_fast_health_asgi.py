"""Fast ASGI health and readiness probes stay cheap and correctly routed.

Intent: Kubernetes liveness must answer ``/health`` without touching Django or
the database, while readiness must withhold traffic until the DB can serve. The
ASGI wrapper is the public entry point for those probes, so regressions here
either restart healthy pods or send WebSocket/API traffic to a pod that is not
ready yet.

Cluster 1u — scenarios 1.171–1.175. Type: U.
Covers: lex/lex_app/fast_health.py, lex/lex_app/asgi.py.
Run: python -m lex pytest lex/test_project/tests/init/test_1u_fast_health_asgi.py -v
"""

from __future__ import annotations

import atexit
import json
from unittest.mock import AsyncMock, patch

import pytest
from asgiref.sync import async_to_sync
from django.test import SimpleTestCase

from lex.lex_app import asgi
from lex.lex_app.fast_health import (
    health_asgi_app,
    is_fast_health_path,
    is_readiness_path,
    readiness_asgi_app,
)

pytestmark = pytest.mark.init

try:
    atexit.unregister(asgi.on_server_shutdown)
except ValueError:
    pass


async def _run_asgi_app(app, path: str = "/health", messages: list[dict] | None = None):
    """Drive a tiny ASGI app and return messages sent to the client."""
    incoming = list(messages or [{"type": "http.request", "body": b"", "more_body": False}])
    sent: list[dict] = []

    async def receive() -> dict:
        return incoming.pop(0)

    async def send(message: dict) -> None:
        sent.append(message)

    await app({"type": "http", "path": path}, receive, send)
    return sent


class TestCluster01u_FastHealthAsgi(SimpleTestCase):
    """Cluster 1u: ASGI health/readiness routing for deployment probes."""

    # -- 1.171 ---------------------------------------------------------
    def test_1_171_path_helpers_separate_liveness_from_readiness(self) -> None:
        """
        Scenario 1.171: liveness and readiness paths are intentionally distinct.
        Given: the documented health/readiness endpoint paths
        When: the fast-health match helpers are called
        Then: only health paths use the liveness app; readiness paths require
              the DB-aware readiness app.
        """
        for path in ("/health", "/health/", "/api/health", "/api/health/"):
            self.assertTrue(is_fast_health_path(path), f"{path} must be fast liveness")
            self.assertFalse(is_readiness_path(path), f"{path} must not be readiness")

        for path in ("/readiness", "/readiness/", "/api/readiness", "/api/readiness/"):
            self.assertTrue(is_readiness_path(path), f"{path} must be readiness")
            self.assertFalse(is_fast_health_path(path), f"{path} must not be fast health")

    # -- 1.172 ---------------------------------------------------------
    def test_1_172_health_app_drains_body_and_returns_static_payload(self) -> None:
        """
        Scenario 1.172: ``/health`` drains the request and returns static 200 JSON.
        Given: an HTTP request body split over multiple ASGI messages
        When: ``health_asgi_app`` handles it
        Then: it consumes the full body and returns the documented payload
              without invoking Django or the database.
        """
        sent = async_to_sync(_run_asgi_app)(
            health_asgi_app,
            messages=[
                {"type": "http.request", "body": b"ignored", "more_body": True},
                {"type": "http.request", "body": b"", "more_body": False},
            ],
        )

        self.assertEqual(sent[0]["status"], 200, "Liveness must always return HTTP 200")
        self.assertEqual(
            json.loads(sent[1]["body"].decode("utf-8")),
            {"status": "Healthy :)"},
            "The health payload is a deployed probe/frontend contract",
        )
        self.assertIn(
            (b"cache-control", b"no-store"),
            sent[0]["headers"],
            "Health responses must not be cached by proxies",
        )

    # -- 1.173 ---------------------------------------------------------
    def test_1_173_readiness_reports_ready_only_when_database_is_ready(self) -> None:
        """
        Scenario 1.173: readiness returns 200 only when the DB check passes.
        Given: the database readiness seam returns both success and failure
        When: ``readiness_asgi_app`` handles the probe
        Then: success returns ``{"status": "ready"}``; failure returns 503 with
              ``database-unavailable`` so Kubernetes withholds traffic.
        """
        with patch("lex.lex_app.fast_health._database_ready", new=AsyncMock(return_value=True)):
            ready = async_to_sync(_run_asgi_app)(readiness_asgi_app, path="/readiness")
        self.assertEqual(ready[0]["status"], 200, "DB-ready pods should be marked Ready")
        self.assertEqual(json.loads(ready[1]["body"].decode("utf-8")), {"status": "ready"})

        with patch("lex.lex_app.fast_health._database_ready", new=AsyncMock(return_value=False)):
            not_ready = async_to_sync(_run_asgi_app)(readiness_asgi_app, path="/readiness")
        self.assertEqual(
            not_ready[0]["status"],
            503,
            "DB-unavailable pods must not be added to the Service",
        )
        self.assertEqual(
            json.loads(not_ready[1]["body"].decode("utf-8")),
            {"status": "not-ready", "reason": "database-unavailable"},
        )

    # -- 1.174 ---------------------------------------------------------
    def test_1_174_http_application_short_circuits_probe_paths(self) -> None:
        """
        Scenario 1.174: ASGI routes health/readiness before Django.
        Given: the top-level ASGI HTTP application
        When: a liveness or readiness path is requested
        Then: the matching lightweight ASGI app handles it and Django is not
              invoked, preserving liveness during slow Django/DB startup.
        """
        with (
            patch.object(asgi, "health_asgi_app", new=AsyncMock()) as health,
            patch.object(asgi, "readiness_asgi_app", new=AsyncMock()) as readiness,
            patch.object(asgi, "django_asgi_app", new=AsyncMock()) as django_app,
        ):
            async_to_sync(_run_asgi_app)(asgi.http_application, path="/health")
            health.assert_awaited_once()
            readiness.assert_not_awaited()
            django_app.assert_not_awaited()

        with (
            patch.object(asgi, "health_asgi_app", new=AsyncMock()) as health,
            patch.object(asgi, "readiness_asgi_app", new=AsyncMock()) as readiness,
            patch.object(asgi, "django_asgi_app", new=AsyncMock()) as django_app,
        ):
            async_to_sync(_run_asgi_app)(asgi.http_application, path="/readiness")
            readiness.assert_awaited_once()
            health.assert_not_awaited()
            django_app.assert_not_awaited()

    # -- 1.175 ---------------------------------------------------------
    def test_1_175_http_application_delegates_non_probe_requests_to_django(self) -> None:
        """
        Scenario 1.175: non-probe HTTP requests still reach Django.
        Given: an arbitrary application path
        When: ``http_application`` receives the request
        Then: neither health shortcut handles it and the Django ASGI app is
              awaited exactly once.
        """
        with (
            patch.object(asgi, "health_asgi_app", new=AsyncMock()) as health,
            patch.object(asgi, "readiness_asgi_app", new=AsyncMock()) as readiness,
            patch.object(asgi, "django_asgi_app", new=AsyncMock()) as django_app,
        ):
            async_to_sync(_run_asgi_app)(asgi.http_application, path="/api/model_entries/foo")

        health.assert_not_awaited()
        readiness.assert_not_awaited()
        django_app.assert_awaited_once()
