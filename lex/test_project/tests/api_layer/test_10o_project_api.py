"""Project API endpoints mounted through lex, and the removed Process route.

Intent
------
A project serves its own REST endpoints through lex instead of replacing
``ROOT_URLCONF``: ``lex_config.API_URLCONF`` is mounted under
``lex_config.API_PREFIX`` ahead of lex's routes and the frontend catch-all,
and views built on ``LexAPIView`` get lex's guarantees — sign-in required
unless public, one transaction per request that rolls back on an error
answer, read permission on lookups (a record the caller may not read is
"not found"), strict input on request, a CSRF cookie for a bootstrap
endpoint, and signed webhooks that act as a service user.

The ``Process`` model type is gone in 3.0: ``api/process/<name>`` no longer
resolves to an API view.

Cluster 10o — scenarios 10.83–10.91. Type: E.
Covers: lex/lex_app/urls.py (build_project_urlpatterns), lex/api/project/
(views, public_paths), lex/authentication/middleware.py
(public paths in ApiKeyAwareLoginRequiredMiddleware),
lex/process_admin/sites/process_admin_site.py (Process route removed).
Run: python -m lex pytest lex/test_project/tests/api_layer/test_10o_project_api.py -v
"""

from __future__ import annotations

import hashlib
import hmac
import json

import pytest
from django.contrib.auth.models import User
from django.urls import resolve
from rest_framework.test import APIClient

from lex.react.views import serve_react
from lex.test_project.tests._e2e_test_case import E2ETestCase

from ._project_api import WEBHOOK_SECRET
from .models import PROJECT_MODELS, ApiProjectItem

pytestmark = pytest.mark.api_layer

PREFIX = "/api/cluster10/"
PROJECT_API = {
    "API_URLCONF": "lex.test_project.tests.api_layer._project_api",
    "API_PREFIX": "api/cluster10/",
    "PUBLIC_PATHS": ["listed"],
}


class TestCluster10o_ProjectApi(E2ETestCase):
    """Cluster 10o: project endpoints on lex's extension point."""

    e2e_models = PROJECT_MODELS

    def setUp(self) -> None:
        super().setUp()
        self.use_lex_extensions(**PROJECT_API)
        self.anonymous = APIClient()

    # -- 10.83 ---------------------------------------------------------
    def test_10_83_process_route_is_gone(self) -> None:
        """
        Scenario 10.83: the Process structure endpoint no longer exists.
        Given: lex 3.0, where the Process model type was removed
        When: /api/process/anything is resolved
        Then: it falls through to the frontend catch-all instead of an API view,
              and lex.core.models exports no Process class.
        """
        self.assertIs(resolve("/api/process/anything").func, serve_react)
        import lex.core.models as core_models

        self.assertNotIn("Process", core_models.__all__)

    # -- 10.84 ---------------------------------------------------------
    def test_10_84_project_endpoint_requires_sign_in(self) -> None:
        """
        Scenario 10.84: a LexAPIView endpoint answers signed-in callers only.
        Given: the project API mounted under api/cluster10/
        When: a signed-in and an anonymous client GET whoami
        Then: the signed-in caller gets 200 with its username; the anonymous
              caller never reaches the view (no 200).
        """
        response = self.client.get(PREFIX + "whoami")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"username": "e2e_user"})
        self.assertNotEqual(self.anonymous.get(PREFIX + "whoami").status_code, 200)

    # -- 10.85 ---------------------------------------------------------
    def test_10_85_public_paths_answer_without_sign_in(self) -> None:
        """
        Scenario 10.85: public = True and PUBLIC_PATHS skip the sign-in requirement.
        Given: a view with public = True (ping) and one listed in PUBLIC_PATHS (listed)
        When: an anonymous client and a signed-in client call them
        Then: both answer 200 anonymously; the public view still recognises a
              signed-in caller (authenticated: true).
        """
        self.assertEqual(self.anonymous.get(PREFIX + "ping").json(), {"authenticated": False})
        self.assertEqual(self.anonymous.get(PREFIX + "listed").json(), {"listed": True})
        self.assertEqual(self.client.get(PREFIX + "ping").json(), {"authenticated": True})

    # -- 10.86 ---------------------------------------------------------
    def test_10_86_error_answer_rolls_back_the_request(self) -> None:
        """
        Scenario 10.86: one transaction per request, rolled back on an error answer.
        Given: a view that creates a row and then raises LexActionError(status 409)
        When: it is called
        Then: HTTP 409 with {code, detail, error, fields}, and the row does not exist.
        """
        response = self.client.post(PREFIX + "create-then-fail", {"name": "temporary"}, format="json")
        self.assertEqual(response.status_code, 409)
        self.assertEqual(
            response.json(),
            {"code": "NOT_TODAY", "detail": "Not today.", "error": "Not today.", "fields": {"name": "Rejected."}},
        )
        self.assertFalse(ApiProjectItem.objects.filter(name="temporary").exists())

    # -- 10.87 ---------------------------------------------------------
    def test_10_87_lookup_applies_read_permission(self) -> None:
        """
        Scenario 10.87: get_object_or_404 hides rows the caller may not read.
        Given: a readable row and a row whose permission_read denies access
        When: both are requested by id, plus an id that does not exist
        Then: the readable row answers 200; the denied row and the missing id
              both answer 404 — indistinguishable.
        """
        visible = ApiProjectItem.objects.create(name="visible")
        hidden = ApiProjectItem.objects.create(name="hidden")
        self.assertEqual(self.client.get(f"{PREFIX}items/{visible.pk}").json()["name"], "visible")
        self.assertEqual(self.client.get(f"{PREFIX}items/{hidden.pk}").status_code, 404)
        self.assertEqual(self.client.get(f"{PREFIX}items/999999").status_code, 404)

    # -- 10.88 ---------------------------------------------------------
    def test_10_88_strict_serializer_rejects_unknown_keys(self) -> None:
        """
        Scenario 10.88: strict input refuses keys the endpoint does not declare.
        Given: an endpoint whose input serializer is decorated with strict_serializer
        When: it is called with an extra "actor" key, then with valid input
        Then: 400 whose fields name "actor"; valid input answers 200.
        """
        response = self.client.post(PREFIX + "rename", {"name": "x", "actor": "someone"}, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["fields"], {"actor": "Unknown field."})
        self.assertEqual(self.client.post(PREFIX + "rename", {"name": "x"}, format="json").json(), {"name": "x"})

    # -- 10.89 ---------------------------------------------------------
    def test_10_89_bootstrap_endpoint_sets_csrf_cookie(self) -> None:
        """
        Scenario 10.89: ensure_csrf = True sets the CSRF cookie.
        Given: a public view with ensure_csrf = True
        When: an anonymous client GETs it
        Then: HTTP 200 and the response sets the csrftoken cookie.
        """
        response = self.anonymous.get(PREFIX + "bootstrap")
        self.assertEqual(response.status_code, 200)
        self.assertIn("csrftoken", response.cookies)

    # -- 10.90 ---------------------------------------------------------
    def test_10_90_signed_webhook_acts_as_service_user(self) -> None:
        """
        Scenario 10.90: a webhook needs no sign-in or CSRF token but a valid signature.
        Given: a LexWebhookView with service_user and an HMAC check, and a client
               that enforces CSRF
        When: the body is posted with a correct, then a wrong signature
        Then: the correct one answers 200 acting as the service user and sees the
              exact raw body; the wrong one answers 401 BAD_SIGNATURE.
        """
        User.objects.get_or_create(username="cluster10_webhook")
        client = APIClient(enforce_csrf_checks=True)
        body = json.dumps({"event": "signed"}).encode()
        signature = "sha256=" + hmac.new(WEBHOOK_SECRET.encode(), body, hashlib.sha256).hexdigest()
        response = client.post(
            PREFIX + "hooks/signed", data=body, content_type="application/json", HTTP_X_SIGNATURE=signature
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json(), {"actor": "cluster10_webhook", "bytes": len(body)})
        response = client.post(
            PREFIX + "hooks/signed", data=body, content_type="application/json", HTTP_X_SIGNATURE="sha256=00"
        )
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["code"], "BAD_SIGNATURE")


class TestCluster10o_ProjectApiUnmounted(E2ETestCase):
    """Cluster 10o: nothing is mounted without configuration."""

    e2e_models: list = []

    # -- 10.91 ---------------------------------------------------------
    def test_10_91_unconfigured_project_api_is_not_mounted(self) -> None:
        """
        Scenario 10.91: without API_URLCONF nothing is mounted.
        Given: the extension keys at their defaults
        When: a would-be project path is resolved
        Then: it falls through to the frontend catch-all.
        """
        self.assertIs(resolve(PREFIX + "whoami").func, serve_react)
