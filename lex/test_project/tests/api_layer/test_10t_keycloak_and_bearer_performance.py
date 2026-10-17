"""Keycloak calls and session rows per request.

Intent
------
Every request with a token built a new KeycloakManager (admin, UMA and OIDC
clients plus an admin lookup of the client) and then asked Keycloak twice,
for permissions and userinfo. lex now keeps one manager per process — the
answers are the same — and can cache both answers per token for
``LEX_KEYCLOAK_PERMISSION_CACHE_SECONDS`` (off by default, because a cache
delays revocations), never past the token's expiry. Token clients without a
session cookie also stop creating a ``django_session`` row per request when
``LEX_BEARER_STATELESS=true``.

Cluster 10t — scenarios 10.113–10.116. Type: U + I.
Covers: lex/api/middleware/keycloak_permissions.py,
lex/authentication/middleware.py (StatelessBearerSessionMiddleware).
Run: python -m lex pytest lex/test_project/tests/api_layer/test_10t_keycloak_and_bearer_performance.py -v
"""

from __future__ import annotations

import base64
import json
import os
import time
from unittest import mock

import pytest
from django.contrib.sessions.backends.db import SessionStore
from django.contrib.sessions.models import Session
from django.core.cache import caches
from django.http import HttpResponse
from django.test import RequestFactory, TestCase

from lex.api.middleware import keycloak_permissions
from lex.api.middleware.keycloak_permissions import KeycloakPermissionsMiddleware
from lex.authentication.middleware import StatelessBearerSessionMiddleware

pytestmark = pytest.mark.api_layer


def _token(exp_in_seconds):
    payload = base64.urlsafe_b64encode(json.dumps({"exp": int(time.time()) + exp_in_seconds}).encode()).decode().rstrip("=")
    return f"header.{payload}.signature"


class _FakeManager:
    instances = 0

    def __init__(self):
        type(self).instances += 1
        self.oidc = mock.MagicMock()
        self.oidc.userinfo.return_value = {"client_roles": ["analyst"]}
        self.uma_calls = 0

    def get_uma_permissions(self, token):
        self.uma_calls += 1
        return [{"rsname": "app.Model", "resource_set_id": None, "scopes": ["read"]}]


class TestCluster10t_KeycloakManagerAndCache(TestCase):
    """Cluster 10t: one manager, optional cache."""

    def setUp(self) -> None:
        keycloak_permissions._reset_shared_manager()
        self.addCleanup(keycloak_permissions._reset_shared_manager)
        _FakeManager.instances = 0
        patcher = mock.patch.object(keycloak_permissions, "KeycloakManager", _FakeManager)
        patcher.start()
        self.addCleanup(patcher.stop)
        caches["oidc"].clear()

    def _run(self, token):
        request = RequestFactory().get("/api/x", HTTP_AUTHORIZATION=f"Bearer {token}")
        request.session = {}
        KeycloakPermissionsMiddleware(lambda r: HttpResponse("ok"))(request)
        return request

    # -- 10.113 --------------------------------------------------------
    def test_10_113_one_manager_per_process(self) -> None:
        """
        Scenario 10.113: requests share one KeycloakManager.
        Given: three requests with tokens
        When: the middleware handles them
        Then: the manager was built once and every request got permissions and roles.
        """
        with mock.patch.dict(os.environ, {"LEX_KEYCLOAK_PERMISSION_CACHE_SECONDS": "0"}):
            requests = [self._run(_token(300)) for _ in range(3)]
        self.assertEqual(_FakeManager.instances, 1)
        for request in requests:
            self.assertEqual(request.user_permissions[0]["scopes"], ["read"])
            self.assertEqual(request.client_roles, ["analyst"])

    # -- 10.114 --------------------------------------------------------
    def test_10_114_cache_is_off_by_default(self) -> None:
        """
        Scenario 10.114: without the setting every request asks Keycloak.
        Given: no LEX_KEYCLOAK_PERMISSION_CACHE_SECONDS
        When: two requests use the same token
        Then: Keycloak was asked twice.
        """
        token = _token(300)
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("LEX_KEYCLOAK_PERMISSION_CACHE_SECONDS", None)
            self._run(token)
            self._run(token)
        self.assertEqual(keycloak_permissions._shared_manager().uma_calls, 2)

    # -- 10.115 --------------------------------------------------------
    def test_10_115_cache_reuses_answers_until_expiry(self) -> None:
        """
        Scenario 10.115: with the cache, one Keycloak round per token and period.
        Given: LEX_KEYCLOAK_PERMISSION_CACHE_SECONDS=60, a valid token and an
               already expired token
        When: the valid token is used three times and the expired one twice
        Then: Keycloak was asked once for the valid token (same answers each
              time) and twice for the expired one (never cached).
        """
        valid, expired = _token(300), _token(-10)
        with mock.patch.dict(os.environ, {"LEX_KEYCLOAK_PERMISSION_CACHE_SECONDS": "60"}):
            first = self._run(valid)
            self._run(valid)
            third = self._run(valid)
            self.assertEqual(keycloak_permissions._shared_manager().uma_calls, 1)
            self.assertEqual(first.user_permissions, third.user_permissions)
            self.assertEqual(third.client_roles, ["analyst"])
            self._run(expired)
            self._run(expired)
        self.assertEqual(keycloak_permissions._shared_manager().uma_calls, 3)


class TestCluster10t_StatelessBearer(TestCase):
    """Cluster 10t: no session row for token clients (opt-in)."""

    # -- 10.116 --------------------------------------------------------
    def test_10_116_stateless_bearer_writes_no_session(self) -> None:
        """
        Scenario 10.116: LEX_BEARER_STATELESS=true keeps bearer requests out of django_session.
        Given: a bearer request without a session cookie whose token authenticates
        When: the stateless middleware and oauth2_authcodeflow's bearer
              middleware handle it, with the switch off and then on
        Then: off — a session row is created (upstream behaviour); on — the
              user is authenticated and no row is written.
        """
        from django.contrib.auth.models import User
        from oauth2_authcodeflow.middleware import BearerAuthMiddleware

        user = User.objects.create_user(username="token-client")

        def handle():
            request = RequestFactory().get("/api/x", HTTP_AUTHORIZATION="Bearer some.jwt.token")
            request.session = SessionStore()
            chain = StatelessBearerSessionMiddleware(BearerAuthMiddleware(lambda r: HttpResponse("ok")))
            with mock.patch("oauth2_authcodeflow.auth.BearerAuthenticationBackend.authenticate", return_value=user), \
                    mock.patch("oauth2_authcodeflow.auth.BearerAuthenticationBackend.__init__", return_value=None):
                chain(request)
            return request

        with mock.patch.dict(os.environ, {"LEX_BEARER_STATELESS": ""}):
            handle()
        self.assertEqual(Session.objects.count(), 1)
        Session.objects.all().delete()
        with mock.patch.dict(os.environ, {"LEX_BEARER_STATELESS": "true"}):
            request = handle()
        self.assertEqual(request.user, user)
        self.assertEqual(Session.objects.count(), 0)
