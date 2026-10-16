"""Access to lex's generic endpoints, and where OIDC may redirect to.

Intent
------
A project that serves its own product API may keep lex's generic
``api/*`` endpoints for operators only. ``lex_config.GENERIC_API`` decides:
``"all"`` (default, unchanged), ``"superuser"`` (everyone else gets 404, as if
the path did not exist) or ``"off"``. The project API and the health check are
never closed by this switch.

The OIDC sign-in and sign-out views of ``oauth2_authcodeflow`` follow
``next``/``fail`` without checking them — an open redirect. Since 3.0 a target
must be relative, the request's own origin or a configured trusted origin;
``LEX_SEC_OIDC_REDIRECT=false`` (or the legacy switch) restores the old
behaviour.

Cluster 4o — scenarios 4.80–4.85. Type: U + E.
Covers: lex/api/middleware/generic_api_gate.py,
lex/authentication/middleware.py (OidcRedirectGuardMiddleware) and their
place in lex/lex_app/settings.py MIDDLEWARE.
Run: python -m lex pytest lex/test_project/tests/permissions/test_4o_generic_api_gate_and_redirects.py -v
"""

from __future__ import annotations

import os
from unittest import mock

import pytest
from django.contrib.auth.models import AnonymousUser
from django.test import RequestFactory, SimpleTestCase, override_settings

from lex.api.middleware.generic_api_gate import generic_api_closed_for
from lex.authentication.middleware import OidcRedirectGuardMiddleware
from lex.core import extensions
from lex.test_project.tests._e2e_test_case import E2ETestCase

pytestmark = pytest.mark.permissions

_FACTORY = RequestFactory()


class _User:
    def __init__(self, superuser: bool):
        self.is_authenticated = True
        self.is_superuser = superuser


def _request(path: str, user=None):
    request = _FACTORY.get(path)
    request.user = user or AnonymousUser()
    return request


class TestCluster04o_GenericApiGate(SimpleTestCase):
    """Cluster 4o (unit): the GENERIC_API decision per path and user."""

    # -- 4.80 ----------------------------------------------------------
    def test_4_80_modes_decide_per_path_and_user(self) -> None:
        """
        Scenario 4.80: the gate closes generic endpoints by mode, never the project API.
        Given: GENERIC_API in each mode and a project API under api/sample/
        When: generic paths, the health check and project paths are checked
        Then: "all" closes nothing; "superuser" closes generic paths for
              non-superusers only; "off" closes them for everyone; api/health and
              the project prefix stay open in every mode; non-api paths are untouched.
        """
        regular, admin = _User(False), _User(True)
        generic = "/api/model_entries/item/list"
        with extensions.override_extensions(GENERIC_API="all"):
            self.assertFalse(generic_api_closed_for(_request(generic, regular)))
        with extensions.override_extensions(
            GENERIC_API="superuser",
            API_URLCONF="lex.test_project.tests.permissions.test_4o_generic_api_gate_and_redirects",
            API_PREFIX="api/sample/",
        ):
            self.assertTrue(generic_api_closed_for(_request(generic, regular)))
            self.assertTrue(generic_api_closed_for(_request(generic)))
            self.assertFalse(generic_api_closed_for(_request(generic, admin)))
            self.assertFalse(generic_api_closed_for(_request("/api/health", regular)))
            self.assertFalse(generic_api_closed_for(_request("/api/sample/me", regular)))
            self.assertFalse(generic_api_closed_for(_request("/some/page", regular)))
        with extensions.override_extensions(GENERIC_API="off"):
            self.assertTrue(generic_api_closed_for(_request(generic, admin)))
            self.assertFalse(generic_api_closed_for(_request("/api/health", admin)))


class TestCluster04o_OidcRedirectGuard(SimpleTestCase):
    """Cluster 4o (unit): next/fail targets of the OIDC views."""

    def _guard(self):
        return OidcRedirectGuardMiddleware(lambda request: "passed")

    def _env(self, **extra):
        clean = {k: v for k, v in os.environ.items() if not k.startswith(("LEX_SEC_", "LEX_LEGACY_SECURITY"))}
        clean.update(extra)
        return mock.patch.dict(os.environ, clean, clear=True)

    # -- 4.81 ----------------------------------------------------------
    @override_settings(
        CORS_ALLOWED_ORIGINS=["http://localhost:3000", "*"],
        CORS_ORIGIN_WHITELIST=[],
        CSRF_TRUSTED_ORIGINS=["https://*.example.com"],
        ALLOWED_HOSTS=["testserver"],
    )
    def test_4_81_foreign_targets_rejected_trusted_targets_pass(self) -> None:
        """
        Scenario 4.81: next/fail must stay on the site or a trusted origin.
        Given: the guard with default security flags
        When: /oidc/authenticate is requested with various next/fail targets
        Then: relative paths, the request's own origin, a CORS origin and a
              CSRF_TRUSTED_ORIGINS wildcard match pass; a foreign origin,
              a protocol-relative URL and a javascript: URL get 400.
        """
        guard = self._guard()
        with self._env():
            for target in (
                "/admin/",
                "http://testserver/after",
                "http://localhost:3000/list",
                "https://app.example.com/x",
            ):
                self.assertEqual(guard(_FACTORY.get("/oidc/authenticate", {"next": target})), "passed", target)
            for target in ("https://evil.test/", "//evil.test/x", "javascript:alert(1)"):
                response = guard(_FACTORY.get("/oidc/authenticate", {"next": target}))
                self.assertEqual(response.status_code, 400, target)
            response = guard(_FACTORY.get("/oidc/total_logout", {"next": "/", "fail": "https://evil.test"}))
            self.assertEqual(response.status_code, 400)
            # Paths outside /oidc/ are never inspected.
            self.assertEqual(guard(_FACTORY.get("/api/x", {"next": "https://evil.test"})), "passed")

    # -- 4.82 ----------------------------------------------------------
    def test_4_82_opt_out_restores_old_behaviour(self) -> None:
        """
        Scenario 4.82: the legacy switches turn the guard off.
        Given: LEX_SEC_OIDC_REDIRECT=false, then LEX_LEGACY_SECURITY=true
        When: a foreign next target is requested
        Then: the request passes in both cases.
        """
        guard = self._guard()
        request = _FACTORY.get("/oidc/authenticate", {"next": "https://evil.test/"})
        with self._env(LEX_SEC_OIDC_REDIRECT="false"):
            self.assertEqual(guard(request), "passed")
        with self._env(LEX_LEGACY_SECURITY="true"):
            self.assertEqual(guard(request), "passed")


class TestCluster04o_GateAndGuardWiring(E2ETestCase):
    """Cluster 4o (E2E): both middlewares are active in lex's MIDDLEWARE."""

    e2e_models: list = []

    def _client_for(self, superuser: bool):
        self.user.is_superuser = superuser
        self.user.save()
        return self.client

    # -- 4.83 ----------------------------------------------------------
    def test_4_83_default_mode_leaves_generic_api_open(self) -> None:
        """
        Scenario 4.83: without configuration the generic API answers as before.
        Given: a signed-in regular user and no GENERIC_API setting
        When: GET /api/model-structure
        Then: HTTP 200 — the gate does not interfere.
        """
        response = self._client_for(False).get("/api/model-structure")
        self.assertEqual(response.status_code, 200)

    # -- 4.84 ----------------------------------------------------------
    def test_4_84_superuser_mode_hides_generic_api(self) -> None:
        """
        Scenario 4.84: GENERIC_API="superuser" hides the generic API from other users.
        Given: GENERIC_API="superuser"
        When: a regular user and then a superuser GET /api/model-structure,
              and the regular user GETs /api/health
        Then: 404 with {"code": "NOT_FOUND"} for the regular user, 200 for the
              superuser, and the health check stays 200.
        """
        with extensions.override_extensions(GENERIC_API="superuser"):
            response = self._client_for(False).get("/api/model-structure")
            self.assertEqual(response.status_code, 404)
            self.assertEqual(response.json()["code"], "NOT_FOUND")
            self.assertEqual(self.client.get("/api/health").status_code, 200)
            self.assertEqual(self._client_for(True).get("/api/model-structure").status_code, 200)

    # -- 4.85 ----------------------------------------------------------
    def test_4_85_redirect_guard_runs_before_oidc_views(self) -> None:
        """
        Scenario 4.85: a foreign next target never reaches the OIDC view.
        Given: default security flags
        When: GET /oidc/authenticate?next=https://evil.test/
        Then: HTTP 400 with code REDIRECT_NOT_ALLOWED, answered by lex's guard.
        """
        clean = {k: v for k, v in os.environ.items() if not k.startswith(("LEX_SEC_", "LEX_LEGACY_SECURITY"))}
        with mock.patch.dict(os.environ, clean, clear=True):
            response = self.client.get("/oidc/authenticate", {"next": "https://evil.test/"})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["code"], "REDIRECT_NOT_ALLOWED")


#: Lets this module serve as an (empty) API_URLCONF in scenario 4.80.
urlpatterns: list = []
