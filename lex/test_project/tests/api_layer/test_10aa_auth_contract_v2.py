"""lex-app's REST surface speaks the HTTP auth contract v2.

Intent
------
Browsers and frontends (PAC, the IC frontend, atrium) decide what to do about an auth
problem from the status code and body alone (``lex/contracts/auth/v2/README.md``):

* **401** -- the session is gone: re-authenticate (silently if possible), never sign the
  user out of Keycloak. Body ``{"error": "not_authenticated" | "session_expired",
  "login_url"}``, a ``WWW-Authenticate`` header.
* **403** -- this user may not do this (``code: permission_denied``) or the CSRF token was
  missing (``code: csrf_failed``): show it, stay signed in.
* **503** ``{"error": "auth_unavailable", "retry_after"}`` with ``Retry-After`` -- Keycloak
  could not answer: retry, stay signed in.

All of them ``Cache-Control: no-store``, none of them carrying a token. Before 2.5, DRF answered
"not authenticated" with 403 (BUG-008, the unauthenticated half), ``api/user/`` passed
Keycloak's own answers through and called userinfo on every page load, and a frontend could
not tell a lost session from a missing permission -- so it signed users out on both.

This batch drives lex's real responses: the DRF answers, ``api/user/`` (now answered from the
validated sign-in), the new ``api/auth/session`` and ``oidc/auth-complete`` endpoints, the
silent re-authentication round trip, the embedded-dashboard token endpoint, and -- per
``server_case`` -- the contract's shared fixtures, so the frontends' tests and lex's agree.

Cluster 10aa -- scenarios 10.152-10.166. Type: E.
Covers: lex/authentication/drf_exceptions.py, lex/authentication/authentication_backends/
BearerMiddlewareAuthentication.py, lex/authentication/identity.py,
lex/authentication/views/user_api.py, lex/authentication/views/session_views.py,
lex/authentication/capabilities.py, lex/authentication/views/token_views.py,
lex/api/views/model_entries/One.py and .../mixins/DestroyOneWithPayloadMixin.py (refusals),
lex/lex_app/settings.py (REST_FRAMEWORK), lex/contracts/auth/v2/fixtures/http/*.json.
Run: python -m lex pytest lex/test_project/tests/api_layer/test_10aa_auth_contract_v2.py -v
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from unittest import mock
from urllib.parse import parse_qs, urlparse

import pytest
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from oauth2_authcodeflow.breaker import reset_breakers
from oauth2_authcodeflow.models import OIDCSession
from oauth2_authcodeflow.testing import FakeOP
from rest_framework.exceptions import PermissionDenied
from rest_framework.test import APIClient

import lex
from lex.api.keycloak import uma_client
from lex.api.middleware.keycloak_permissions import reset_uma_cache
from lex.authentication.drf_exceptions import exception_handler
from lex.test_project.tests._e2e_test_case import E2ETestCase, oidc_test_settings, sign_in

from .models import CONTRACT_ITEM, CONTRACT_MODELS, ContractItem

pytestmark = pytest.mark.api_layer

FIXTURES = Path(lex.__file__).resolve().parent / "contracts" / "auth" / "v2" / "fixtures" / "http"
FETCH = "lex.api.middleware.keycloak_permissions.uma_client.fetch_uma_permissions"
KEYCLOAK_ENV = {"KEYCLOAK_URL": "https://keycloak.test", "KEYCLOAK_REALM": "lex", "OIDC_RP_CLIENT_ID": "lex-app"}
#: A JWT in a body or a page is a leaked token.
JWT = re.compile(rb"eyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}\.")
SESSION_REALM = re.compile(r'^Session realm="[^"]*"$')


def _no_token(test, response):
    content = b"".join(response.streaming_content) if getattr(response, "streaming", False) else response.content
    test.assertIsNone(JWT.search(content), "an auth answer must never carry a token")


class _ContractMixin:
    """OIDC sign-ins without a provider, Keycloak's UMA answer under control, caches fresh."""

    def contract_setup(self):
        overrides = override_settings(**oidc_test_settings())
        overrides.enable()
        self.addCleanup(overrides.disable)
        patcher = mock.patch(FETCH, return_value=[])
        self.uma = patcher.start()
        self.addCleanup(patcher.stop)
        reset_uma_cache()
        reset_breakers()
        self.addCleanup(reset_uma_cache)
        self.addCleanup(reset_breakers)

    def install_op(self) -> FakeOP:
        """An in-process Keycloak stand-in (discovery, JWKS, token endpoint)."""
        op = FakeOP()
        overrides = override_settings(**op.django_settings())
        overrides.enable()
        self.addCleanup(overrides.disable)
        op.install()
        self.addCleanup(op.uninstall)
        return op

    def make_user(self, username="ada", **extra):
        get_user_model().objects.filter(username=username).delete()
        return get_user_model().objects.create_user(username=username, email=f"{username}@example.com", **extra)


class TestCluster10aa_NotAuthenticated(_ContractMixin, TestCase):
    """10.152-10.154: DRF answers "not authenticated" with 401, and session requests keep CSRF."""

    def setUp(self) -> None:
        self.contract_setup()

    # -- 10.152 --------------------------------------------------------
    def test_10_152_anonymous_drf_request_gets_401_not_403(self) -> None:
        """
        Scenario 10.152: DRF's "not authenticated" is a 401 with WWW-Authenticate (BUG-008, unauthenticated half).
        Given: an anonymous browser
        When: it asks a DRF view that needs a signed-in user but is not behind the login
              middleware (api/user/)
        Then: the answer is 401 -- not 403 -- with WWW-Authenticate: Session realm=..., the
              body's code not_authenticated plus the contract's error and login_url, and
              Cache-Control: no-store.
        """
        response = self.client.get("/api/user/", HTTP_ACCEPT="application/json")
        self.assertEqual(response.status_code, 401, response.content[:300])
        self.assertRegex(response["WWW-Authenticate"], SESSION_REALM)
        body = response.json()
        self.assertEqual(body["code"], "not_authenticated")
        self.assertEqual(body["error"], "not_authenticated")
        self.assertEqual(body["login_url"], "/oidc/authenticate")
        self.assertEqual(response["Cache-Control"], "no-store")

    # -- 10.153 --------------------------------------------------------
    def test_10_153_rejected_bearer_token_gets_a_bearer_challenge(self) -> None:
        """
        Scenario 10.153: a rejected bearer token is told so in the challenge.
        Given: a machine client sending Authorization: Bearer with a token that is not a valid
               access token
        When: it calls api/user/
        Then: 401 with WWW-Authenticate: Bearer error="invalid_token" and code not_authenticated.
        """
        self.install_op()
        response = self.client.get("/api/user/", HTTP_AUTHORIZATION="Bearer not.a.token")
        self.assertEqual(response.status_code, 401, response.content[:300])
        self.assertIn('error="invalid_token"', response["WWW-Authenticate"])
        self.assertTrue(response["WWW-Authenticate"].startswith("Bearer"))
        self.assertEqual(response.json()["code"], "not_authenticated")

    # -- 10.154 --------------------------------------------------------
    def test_10_154_a_bearer_header_never_switches_csrf_off_for_a_session(self) -> None:
        """
        Scenario 10.154: a session request is CSRF-checked even with some Authorization: Bearer header.
        Given: a signed-in browser session and a request that adds a made-up bearer token
               but no CSRF token (what a cross-site page could try)
        When: it POSTs to the embedded-dashboard token endpoint
        Then: the answer is 403 code csrf_failed -- the bearer header did not authenticate the
              request, so the session's CSRF check still applies -- and no token is handed out.
        """
        client = APIClient(enforce_csrf_checks=True)
        sign_in(client, self.make_user())
        response = client.post("/api/auth/streamlit-token/", HTTP_AUTHORIZATION="Bearer forged-by-another-site")
        self.assertEqual(response.status_code, 403, response.content[:300])
        self.assertEqual(response.json()["code"], "csrf_failed")
        self.assertNotIn("token", response.json())
        _no_token(self, response)


class TestCluster10aa_Refusals(_ContractMixin, E2ETestCase):
    """10.155: refusals are 403 with a code; the user stays signed in."""

    e2e_models = CONTRACT_MODELS
    e2e_unpatch = {"keycloak_uma"}

    def setUp(self) -> None:
        super().setUp()
        self.contract_setup()

    # -- 10.155 --------------------------------------------------------
    def test_10_155_refusals_carry_permission_denied_and_csrf_failed(self) -> None:
        """
        Scenario 10.155: every refusal is a 403 with a code, never a 401 that would sign the user out.
        Given: a signed-in user who may not create or delete ContractItems, a DRF permission
               refusal, and a session request without its CSRF token
        When: the user tries to create and to delete, a view raises PermissionDenied, and the
              CSRF-less POST arrives
        Then: create and delete answer 403 with code permission_denied, a detail and the
              documented message; DRF's own refusal gets code permission_denied; the CSRF failure
              code csrf_failed; all no-store; nothing was created or deleted; the user is still
              signed in.
        """
        item = ContractItem.objects.create(name="kept")
        created = self.client.post(self.url_create(CONTRACT_ITEM), data={"name": "new"}, format="json")
        deleted = self.client.delete(self.url_detail(CONTRACT_ITEM, item.pk))
        for name, response in (("create", created), ("delete", deleted)):
            with self.subTest(refusal=name):
                self.assertEqual(response.status_code, 403, response.content[:300])
                body = response.json()
                self.assertEqual(body["code"], "permission_denied")
                self.assertTrue(body["detail"])
                self.assertEqual(body["message"], body["detail"], "the documented message key stays")
                self.assertEqual(response["Cache-Control"], "no-store")
        self.assertEqual(list(ContractItem.objects.values_list("name", flat=True)), ["kept"])

        drf = exception_handler(PermissionDenied("You may not do this."), {"request": None})
        self.assertEqual((drf.status_code, drf.data["code"]), (403, "permission_denied"))

        csrf_client = APIClient(enforce_csrf_checks=True)
        sign_in(csrf_client, self.user)
        csrf = csrf_client.post(self.url_create(CONTRACT_ITEM), data={"name": "new"}, format="json")
        self.assertEqual(csrf.status_code, 403, csrf.content[:300])
        self.assertEqual(csrf.json()["code"], "csrf_failed")
        self.assertTrue(csrf.json()["detail"].startswith("CSRF Failed"))

        signed_in = self.client.get("/api/auth/session")
        self.assertEqual(signed_in.status_code, 200)


class TestCluster10aa_CurrentUser(_ContractMixin, TestCase):
    """10.156-10.157: api/user/ answers from the sign-in, and per the contract when it cannot."""

    def setUp(self) -> None:
        self.contract_setup()

    # -- 10.156 --------------------------------------------------------
    def test_10_156_current_user_comes_from_the_sign_in_without_calling_keycloak(self) -> None:
        """
        Scenario 10.156: api/user/ answers who the user is from the validated sign-in.
        Given: a browser signed in through OIDC (profile claims in the ID token, client roles
               in the access token), a machine client with a valid bearer access token, and an
               admin signed in to Django without OIDC
        When: each asks api/user/, with every outgoing HTTP request refused
        Then: the browser gets the userinfo-shaped profile with its client_roles; the bearer
              client its own; the Django-only login a profile from the user record and no
              roles; none of it contains token bookkeeping or a token; it is no-store; and no
              HTTP request left the process (no userinfo call).
        """
        op = self.install_op()
        user = self.make_user("ada")
        sign_in(self.client, user, client_roles=["admin", "analyst"],
                claims={"name": "Ada Lovelace", "given_name": "Ada", "family_name": "Lovelace",
                        "preferred_username": "ada", "email": "ada@example.com"})
        bearer = op.issue_tokens("kc-machine", email="machine@example.com", client_roles=["viewer"])["access_token"]
        django_only = APIClient()
        django_only.force_login(self.make_user("admin"), backend="django.contrib.auth.backends.ModelBackend")

        with mock.patch("requests.sessions.Session.request", side_effect=AssertionError("no HTTP call")) as http:
            browser = self.client.get("/api/user/")
            machine = APIClient().get("/api/user/", HTTP_AUTHORIZATION=f"Bearer {bearer}")
            local = django_only.get("/api/user/")
        http.assert_not_called()

        for name, response in (("browser", browser), ("machine", machine), ("django-only", local)):
            with self.subTest(caller=name):
                self.assertEqual(response.status_code, 200, response.content[:300])
                self.assertEqual(response["Cache-Control"], "no-store")
                for bookkeeping in ("iss", "aud", "azp", "sid", "exp", "iat", "resource_access", "typ"):
                    self.assertNotIn(bookkeeping, response.json())
                _no_token(self, response)
        profile = browser.json()
        self.assertEqual(
            {k: profile[k] for k in ("email", "name", "given_name", "family_name", "preferred_username")},
            {"email": "ada@example.com", "name": "Ada Lovelace", "given_name": "Ada",
             "family_name": "Lovelace", "preferred_username": "ada"},
        )
        self.assertEqual(sorted(profile["client_roles"]), ["admin", "analyst"])
        self.assertTrue(profile["sub"])
        self.assertEqual((machine.json()["email"], machine.json()["client_roles"]), ("machine@example.com", ["viewer"]))
        self.assertEqual((local.json()["email"], local.json()["client_roles"]), ("admin@example.com", []))

    # -- 10.157 --------------------------------------------------------
    def test_10_157_current_user_answers_401_or_503_when_the_sign_in_cannot_be_used(self) -> None:
        """
        Scenario 10.157: an ended sign-in is 401 session_expired; Keycloak down is 503 -- per the contract.
        Given: a sign-in whose access token is about to expire, and Keycloak answering the refresh
               first with invalid_grant (the SSO session ended), then -- for another sign-in
               whose token is already unusable -- with 503
        When: api/user/ is asked
        Then: the first gets 401 session_expired with WWW-Authenticate and is signed out (the
              next request is anonymous); the second gets 503 auth_unavailable with Retry-After,
              and its sign-in is kept for when Keycloak is back.
        """
        op = self.install_op()
        sign_in(self.client, self.make_user("ended"), access_expires_in=30)
        op.script_token((400, {"error": "invalid_grant", "error_description": "Session not active"}))
        ended = self.client.get("/api/user/")
        self.assertEqual(ended.status_code, 401, ended.content[:300])
        self.assertEqual(ended.json()["error"], "session_expired")
        self.assertRegex(ended["WWW-Authenticate"], SESSION_REALM)
        self.assertEqual(self.client.get("/api/user/").json()["error"], "not_authenticated")

        waiting = APIClient()
        row = sign_in(waiting, self.make_user("waiting"), access_expires_in=3)
        op.script_token((503, "<html>Service Unavailable</html>"))
        down = waiting.get("/api/user/")
        self.assertEqual(down.status_code, 503, down.content[:300])
        self.assertEqual(down.json()["error"], "auth_unavailable")
        self.assertEqual(down["Retry-After"], str(down.json()["retry_after"]))
        self.assertIsNone(OIDCSession.objects.get(pk=row.pk).ended_at, "an outage must not end the sign-in")


class TestCluster10aa_SessionEndpoints(_ContractMixin, TestCase):
    """10.158-10.161: api/auth/session, oidc/auth-complete, silent re-auth, the dashboard token."""

    def setUp(self) -> None:
        self.contract_setup()

    @staticmethod
    def _message(page) -> dict:
        match = re.search(r"var message = (\{.*?\});", page.content.decode())
        assert match, page.content[:500]
        return json.loads(match.group(1))

    # -- 10.158 --------------------------------------------------------
    def test_10_158_session_status_reports_sign_in_and_capabilities(self) -> None:
        """
        Scenario 10.158: api/auth/session tells a client whether it is signed in and what the server supports.
        Given: a signed-in browser and an anonymous one
        When: they GET api/auth/session (also with a trailing slash, and through the process-admin
              route used under DJANGO_BASE_PATH)
        Then: signed in: 200 {"authenticated": true, "user": {...profile...}, "capabilities":
              {"auth_contract": 2, "dashboard_handoff": [], "ws_close_codes": false}}; anonymous:
              the contract's 401 not_authenticated with WWW-Authenticate; both no-store; the
              capabilities come from lex.authentication.capabilities, so the hand-off and
              socket work can switch theirs on there.
        """
        from django.urls import reverse

        sign_in(self.client, self.make_user("ada"), client_roles=["admin"])
        for path in ("/api/auth/session", "/api/auth/session/",
                     reverse("process_admin_rest_api:process-admin-auth-session")):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200, response.content[:300])
                body = response.json()
                self.assertIs(body["authenticated"], True)
                self.assertEqual(body["user"]["email"], "ada@example.com")
                self.assertEqual(body["user"]["client_roles"], ["admin"])
                self.assertEqual(body["capabilities"],
                                 {"auth_contract": 2, "dashboard_handoff": [], "ws_close_codes": False})
                self.assertEqual(response["Cache-Control"], "no-store")
                _no_token(self, response)

        with mock.patch("lex.authentication.capabilities.dashboard_handoff_surfaces", return_value=["streamlit"]), \
                mock.patch("lex.authentication.capabilities.ws_close_codes", return_value=True):
            flipped = self.client.get("/api/auth/session").json()["capabilities"]
        self.assertEqual(flipped, {"auth_contract": 2, "dashboard_handoff": ["streamlit"], "ws_close_codes": True})

        anonymous = APIClient().get("/api/auth/session")
        self.assertEqual(anonymous.status_code, 401)
        self.assertEqual(anonymous.json(), {"error": "not_authenticated", "login_url": "/oidc/authenticate"})
        self.assertRegex(anonymous["WWW-Authenticate"], SESSION_REALM)
        self.assertEqual(anonymous["Cache-Control"], "no-store")

    # -- 10.159 --------------------------------------------------------
    def test_10_159_auth_complete_posts_the_result_to_its_own_origin_only(self) -> None:
        """
        Scenario 10.159: oidc/auth-complete reports a finished sign-in, same-origin only, without a token.
        Given: a signed-in browser finishing a silent sign-in, an anonymous one whose sign-in
               failed (error=1 from the client, error=login_required from Keycloak), and
               hostile query values
        When: they open oidc/auth-complete
        Then: the page posts {"type": "lex-auth", "v": 2, "event": "result", "mode", "ok"
              [, "error"]} to its opener and parent with its own origin as the target, and on
              BroadcastChannel('lex-auth'); success is ok:true, failure ok:false with the
              Keycloak error; an unknown mode is "popup" and an unsafe error code is never
              reflected; it may be framed by its own origin only (the silent attempt is a
              hidden iframe), is no-store, and carries no token.
        """
        sign_in(self.client, self.make_user("ada"))
        page = self.client.get("/oidc/auth-complete", {"m": "silent"})
        self.assertEqual(page.status_code, 200)
        self.assertEqual(self._message(page),
                         {"type": "lex-auth", "v": 2, "event": "result", "mode": "silent", "ok": True})
        html = page.content.decode()
        self.assertIn("window.location.origin", html)
        self.assertIn("post(window.opener)", html)
        self.assertIn("post(window.parent)", html)
        self.assertIn('new BroadcastChannel("lex-auth")', html)
        self.assertNotIn('postMessage(message, "*")', html)
        self.assertEqual(page["X-Frame-Options"], "SAMEORIGIN")
        self.assertEqual(page["Cache-Control"], "no-store")
        _no_token(self, page)

        anonymous = APIClient()
        failed = anonymous.get("/oidc/auth-complete?m=silent&error=1&error=login_required")
        self.assertEqual(self._message(failed),
                         {"type": "lex-auth", "v": 2, "event": "result", "mode": "silent", "ok": False,
                          "error": "login_required"})
        hostile = anonymous.get("/oidc/auth-complete", {"m": "<x>", "error": "</script><script>alert(1)</script>"})
        message = self._message(hostile)
        self.assertEqual((message["mode"], message["ok"], message["error"]), ("popup", False, "login_required"))
        self.assertNotIn(b"alert(1)", hostile.content)

    # -- 10.160 --------------------------------------------------------
    def test_10_160_silent_reauthentication_round_trip(self) -> None:
        """
        Scenario 10.160: the silent re-authentication path the contract client drives works end to end.
        Given: a signed-out browser and Keycloak (FakeOP), the client building its URLs as the
               contract client does: oidc/authenticate?prompt=none with next and fail pointing
               at oidc/auth-complete on the same origin
        When: Keycloak first answers login_required (no SSO session), then -- on a second
              attempt -- signs the user in
        Then: both attempts reach Keycloak with prompt=none; the first lands on auth-complete
              with ok:false / login_required; the second on auth-complete with ok:true, and the
              browser is signed in (api/auth/session answers 200).
        """
        op = self.install_op()
        complete = "http://testserver/oidc/auth-complete?m=silent"
        params = {"next": complete, "fail": complete + "&error=1", "prompt": "none"}

        def start():
            response = self.client.get("/oidc/authenticate", params)
            self.assertEqual(response.status_code, 302, response.content[:300])
            self.assertTrue(response["Location"].startswith(op.authorization_endpoint))
            query = parse_qs(urlparse(response["Location"]).query)
            self.assertEqual(query.get("prompt"), ["none"])
            return query

        first = start()
        failed = self.client.get("/oidc/callback", {"state": first["state"][0], "error": "login_required"},
                                 follow=True)
        self.assertEqual(self._message(failed)["ok"], False)
        self.assertEqual(self._message(failed)["error"], "login_required")

        second = start()
        code = op.authorization_code("kc-silent", nonce=second["nonce"][0], email="silent@example.com")
        signed_in = self.client.get("/oidc/callback", {"state": second["state"][0], "code": code}, follow=True)
        self.assertEqual(self._message(signed_in),
                         {"type": "lex-auth", "v": 2, "event": "result", "mode": "silent", "ok": True})
        self.assertEqual(self.client.get("/api/auth/session").status_code, 200)

    # -- 10.161 --------------------------------------------------------
    def test_10_161_dashboard_token_comes_from_the_public_token_api(self) -> None:
        """
        Scenario 10.161: api/auth/streamlit-token/ hands out the sign-in's token, renewed in time.
        Given: a sign-in whose access token expires in 30 s (inside the 60 s renewal window),
               Keycloak answering refreshes, and later Keycloak ending the SSO session
        When: the frontend asks for the dashboard token, and asks again after the end
        Then: the first answer is a refreshed token (not the old one) with expires_in about
              Keycloak's 300 s and refresh_interval 60 s earlier, no-store; the second is 401
              session_expired, without a token.
        """
        op = self.install_op()
        row = sign_in(self.client, self.make_user("dash"), access_expires_in=30)
        old_token = row.access_token
        fresh = self.client.post("/api/auth/streamlit-token/")
        self.assertEqual(fresh.status_code, 200, fresh.content[:300])
        body = fresh.json()
        self.assertNotEqual(body["token"], old_token, "a token about to expire must be renewed first")
        self.assertAlmostEqual(body["expires_in"], op.access_lifetime, delta=5)
        self.assertEqual(body["refresh_interval"], body["expires_in"] - 60)
        self.assertEqual(fresh["Cache-Control"], "no-store")

        OIDCSession.objects.filter(pk=row.pk).update(access_expires_at=row.access_expires_at)
        op.script_token((400, {"error": "invalid_grant", "error_description": "Session not active"}))
        ended = self.client.post("/api/auth/streamlit-token/")
        self.assertEqual(ended.status_code, 401, ended.content[:300])
        self.assertEqual(ended.json()["error"], "session_expired")
        self.assertNotIn("token", ended.json())


def _fixture_cases(server_case: str):
    for path in sorted(FIXTURES.glob("*.json")):
        for case in json.loads(path.read_text())["cases"]:
            if case.get("server_case") == server_case:
                yield case


class TestCluster10aa_ContractFixtures(_ContractMixin, E2ETestCase):
    """10.162-10.166: lex's real responses match the contract's shared fixtures, case by case."""

    e2e_models = CONTRACT_MODELS
    e2e_unpatch = {"keycloak_uma"}

    def setUp(self) -> None:
        super().setUp()
        self.contract_setup()

    def send(self, client, case, **extra):
        method = case["request"]["method"].lower()
        path = "/" + case["request"]["path"]
        kwargs = {"format": "json"} if method != "get" else {}
        return getattr(client, method)(path, **kwargs, **extra)

    def assert_conforms(self, case, response):
        """Status, body keys, the listed headers and the protocol values of the fixture -- and no token."""
        expected = case["response"]
        self.assertEqual(response.status_code, expected["status"], f"{case['id']}: {response.content[:300]}")
        body = response.json()
        fixture_body = expected["body"]
        self.assertTrue(set(fixture_body) <= set(body), f"{case['id']}: {sorted(fixture_body)} not in {sorted(body)}")
        for key in ("error", "login_url", "code"):
            if key in fixture_body:
                self.assertEqual(body[key], fixture_body[key], f"{case['id']}: {key}")
        for header, value in expected["headers"].items():
            actual = response.get(header)
            self.assertIsNotNone(actual, f"{case['id']}: header {header} missing")
            if header == "www-authenticate":
                self.assertRegex(actual, SESSION_REALM)
            elif header == "retry-after":
                self.assertEqual(actual, str(body["retry_after"]))
            elif header == "content-type":
                self.assertTrue(actual.startswith(value), f"{case['id']}: {actual}")
            else:
                self.assertEqual(actual, value, f"{case['id']}: {header}")
        _no_token(self, response)

    def cases(self, server_case):
        cases = list(_fixture_cases(server_case))
        self.assertTrue(cases, f"the contract has no fixture for {server_case}")
        return cases

    # -- 10.162 --------------------------------------------------------
    def test_10_162_fixture_session_missing(self) -> None:
        """
        Scenario 10.162: server_case session_missing -- a background request without a session.
        Given: the contract fixtures owned by the server for session_missing
        When: an anonymous client sends each fixture's request to lex
        Then: lex answers with the fixture's status, body keys and values, and headers.
        """
        for case in self.cases("session_missing"):
            with self.subTest(case=case["id"]):
                self.assert_conforms(case, self.send(APIClient(), case))

    # -- 10.163 --------------------------------------------------------
    def test_10_163_fixture_session_expired(self) -> None:
        """
        Scenario 10.163: server_case session_expired -- Keycloak ended the session (invalid_grant on refresh).
        Given: a sign-in whose token must be refreshed, and Keycloak answering invalid_grant
        When: the client sends each fixture's request
        Then: lex answers with the fixture's status, body keys and values, and headers.
        """
        op = self.install_op()
        for case in self.cases("session_expired"):
            with self.subTest(case=case["id"]):
                client = APIClient()
                sign_in(client, self.user, access_expires_in=30)
                op.script_token((400, {"error": "invalid_grant", "error_description": "Session not active"}))
                self.assert_conforms(case, self.send(client, case))

    # -- 10.164 --------------------------------------------------------
    def test_10_164_fixture_auth_unavailable(self) -> None:
        """
        Scenario 10.164: server_case auth_unavailable -- Keycloak cannot be reached for refresh or UMA.
        Given: (a) a sign-in whose token is unusable and Keycloak answering the refresh with 503,
               and (b) a fresh sign-in while Keycloak's UMA endpoint fails
        When: the client sends each fixture's request
        Then: both routes answer with the fixture's status, body keys and values, and headers --
              before the view: nothing was created.
        """
        op = self.install_op()
        env = mock.patch.dict(os.environ, KEYCLOAK_ENV)
        env.start()
        self.addCleanup(env.stop)
        for case in self.cases("auth_unavailable"):
            with self.subTest(case=case["id"], route="refresh"):
                client = APIClient()
                sign_in(client, self.user, access_expires_in=3)
                op.script_token((503, "<html>Service Unavailable</html>"))
                self.assert_conforms(case, self.send(client, case))
            with self.subTest(case=case["id"], route="uma"):
                reset_breakers()
                client = APIClient()
                sign_in(client, self.user)
                self.uma.side_effect = uma_client.KeycloakUnavailable("Keycloak answered 502", status=502)
                self.assert_conforms(case, self.send(client, case))
                self.uma.side_effect = None
        self.assertFalse(ContractItem.objects.exists())

    # -- 10.165 --------------------------------------------------------
    def test_10_165_fixture_permission_denied(self) -> None:
        """
        Scenario 10.165: server_case permission_denied -- a signed-in user may not do this.
        Given: a signed-in user who may not create ContractItems
        When: the user sends the fixture's write (to a real lex model route)
        Then: lex answers with the fixture's status, body keys and values, and headers.
        """
        for case in self.cases("permission_denied"):
            with self.subTest(case=case["id"]):
                response = getattr(self.client, case["request"]["method"].lower())(
                    self.url_create(CONTRACT_ITEM), data={"name": "x"}, format="json"
                )
                self.assert_conforms(case, response)

    # -- 10.166 --------------------------------------------------------
    def test_10_166_fixture_csrf_failed(self) -> None:
        """
        Scenario 10.166: server_case csrf_failed -- a session write without its CSRF token.
        Given: a signed-in browser session that does not send the CSRF token
        When: it sends the fixture's write (to a real lex model route)
        Then: lex answers with the fixture's status, body keys and values, and headers.
        """
        for case in self.cases("csrf_failed"):
            with self.subTest(case=case["id"]):
                client = APIClient(enforce_csrf_checks=True)
                sign_in(client, self.user)
                response = getattr(client, case["request"]["method"].lower())(
                    self.url_create(CONTRACT_ITEM), data={"name": "x"}, format="json"
                )
                self.assert_conforms(case, response)
