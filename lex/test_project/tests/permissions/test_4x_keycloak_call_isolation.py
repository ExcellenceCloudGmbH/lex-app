"""Per-user Keycloak calls stay with their user; forged tokens and look-alike paths get no access.

Intent
------
Keycloak is the single source of truth for who may do what (docs: permissions):
the permissions middleware asks it, once per request, for the caller's UMA
permissions and profile, and every downstream check reads those. Three things
broke that promise:

* **Cross-user answers.** The calls went through python-keycloak, which puts the
  caller's bearer token on a connection object shared by the whole process and
  only then sends the request. With several request threads, one user's request
  could leave carrying another user's token -- and come back with that user's
  permissions. The calls now go through ``lex.api.keycloak.uma_client``, which
  puts the token on that one request only. Every caller is covered because they
  all go through ``KeycloakManager`` (middleware, Reflex, Streamlit, token views).
* **Forged bearer tokens.** The bearer path accepted HS256 signed with the OIDC
  client *secret*, which every instance holds in its environment. Only Keycloak's
  RS256 is accepted now.
* **Look-alike paths skipping sign-in.** The login exemptions were unanchored
  regular expressions, so any path *containing* ``/health`` or ``api/user`` --
  e.g. the real ``/api/user_permissions/`` -- was served without a signed-in user.

A regression here hands one customer's data to another, or to nobody in
particular, so these are end-to-end through the public entry points: the
permissions middleware, ``KeycloakManager``, and Django's full middleware stack.

Cluster 4x -- scenarios 4.138-4.147. Type: U (4.138-4.143) and I (4.144-4.147).
Covers: lex/api/keycloak/uma_client.py, lex/api/views/authentication/KeycloakManager.py,
lex/api/middleware/keycloak_permissions.py, lex/lex_app/settings.py
(OIDC_RP_SIGN_ALGOS_ALLOWED, OIDC_MIDDLEWARE_NO_AUTH_URL_PATTERNS).
Run: python -m lex pytest lex/test_project/tests/permissions/test_4x_keycloak_call_isolation.py -v
"""

from __future__ import annotations

import json
import logging
import os
import random
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import mock
from urllib.parse import parse_qs

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from django.conf import settings
from django.contrib.auth import get_user_model
from django.http import HttpResponse
from django.test import RequestFactory, SimpleTestCase, TestCase, override_settings

from lex.api.keycloak import uma_client
from lex.api.middleware import keycloak_permissions
from lex.api.middleware.keycloak_permissions import KeycloakPermissionsMiddleware
from lex.api.views.authentication.KeycloakManager import KeycloakManager

pytestmark = pytest.mark.permissions

REALM = "lex"
CLIENT_ID = "lex-app"
CLIENT_SECRET = "the-instance-client-secret"


# ---------------------------------------------------------------------------
# A Keycloak stand-in that answers for whichever token *this* request carries.
# ---------------------------------------------------------------------------
class _StubServer(ThreadingHTTPServer):
    # Up to 24 callers connect at the same moment. socketserver's default listen backlog
    # of 5 turns some of those connections away, which would read as Keycloak being down.
    request_queue_size = 128
    daemon_threads = True


class _StubKeycloak:
    """Serves UMA and userinfo for the bearer token on each request, echoing its user.

    Tokens look like ``tok-<user>``. A short random delay per answer makes
    concurrent requests overlap, which is what exposed the shared-header race.
    Special tokens: ``tok-denied`` (UMA access_denied), ``tok-expired`` (401),
    ``tok-broken`` (502 with an HTML body).
    """

    def __init__(self) -> None:
        stub = self
        self.seen_authorization: list[str] = []
        self.seen_cookies: list[str] = []
        self._lock = threading.Lock()

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):  # keep test output clean
                pass

            def _send(self, status, body, content_type="application/json"):
                raw = body if isinstance(body, bytes) else json.dumps(body).encode()
                self.send_response(status)
                # Like a sticky-routing ingress: every answer tries to set a per-caller cookie.
                self.send_header("Set-Cookie", f"route={self.headers.get('Authorization', '')[-12:]}; Path=/")
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def _user(self):
                auth = self.headers.get("Authorization", "")
                with stub._lock:
                    stub.seen_authorization.append(auth)
                    if self.headers.get("Cookie"):
                        stub.seen_cookies.append(self.headers.get("Cookie"))
                token = auth[len("Bearer "):] if auth.startswith("Bearer ") else ""
                return token[len("tok-"):] if token.startswith("tok-") else ""

            def do_POST(self):
                length = int(self.headers.get("Content-Length") or 0)
                form = parse_qs(self.rfile.read(length).decode())
                grant = (form.get("grant_type") or [""])[0]
                if self.path.endswith("/protocol/openid-connect/token") and grant == "client_credentials":
                    return self._send(200, {"access_token": "service", "expires_in": 300, "token_type": "Bearer"})
                if self.path != f"/realms/{REALM}/protocol/openid-connect/token":
                    return self._send(404, {"error": "not_found"})
                user = self._user()
                time.sleep(random.uniform(0.0005, 0.004))
                if user == "denied":
                    return self._send(403, {"error": "access_denied", "error_description": "not_authorized"})
                if user == "expired":
                    return self._send(401, {"error": "invalid_grant", "error_description": "Token is not active"})
                if user == "broken":
                    return self._send(502, b"<html>Bad gateway</html>", content_type="text/html")
                if grant != uma_client.UMA_GRANT_TYPE or (form.get("audience") or [""])[0] != CLIENT_ID:
                    return self._send(400, {"error": "invalid_request"})
                return self._send(200, [{"rsname": f"resource-of-{user}", "rsid": user, "scopes": ["read"]}])

            def do_GET(self):
                if self.path.startswith(f"/admin/realms/{REALM}/clients"):
                    return self._send(200, [{"id": "client-uuid", "clientId": CLIENT_ID}])
                if self.path != f"/realms/{REALM}/protocol/openid-connect/userinfo":
                    return self._send(404, {"error": "not_found"})
                user = self._user()
                time.sleep(random.uniform(0.0005, 0.004))
                if user in ("expired", ""):
                    return self._send(401, {"error": "invalid_token"})
                if user == "broken":
                    return self._send(502, b"<html>Bad gateway</html>", content_type="text/html")
                return self._send(200, {"sub": user, "email": f"{user}@example.com", "client_roles": [f"role-{user}"]})

        self.server = _StubServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    @property
    def url(self) -> str:
        host, port = self.server.server_address[:2]
        return f"http://{host}:{port}"

    def __enter__(self) -> "_StubKeycloak":
        self.thread.start()
        return self

    def __exit__(self, *exc) -> None:
        self.server.shutdown()
        self.server.server_close()


def _fresh_keycloak_manager() -> None:
    """Forget the process-wide manager so the next one is built against the stub."""
    # The shared-manager cache exists on the v2 line only; 2.1.x builds one per request.
    getattr(keycloak_permissions, "_reset_shared_manager", lambda: None)()
    KeycloakManager._singleton_instance = None
    KeycloakManager._singleton_initialized = False


class _StubKeycloakTestCase(SimpleTestCase):
    """Runs each scenario against a fresh stub Keycloak and a fresh manager."""

    def setUp(self) -> None:
        self.stub = _StubKeycloak().__enter__()
        self.addCleanup(self.stub.__exit__, None, None, None)
        env = mock.patch.dict(
            os.environ,
            {
                "KEYCLOAK_URL": self.stub.url,
                "KEYCLOAK_REALM": REALM,
                "OIDC_RP_CLIENT_ID": CLIENT_ID,
                "OIDC_RP_CLIENT_SECRET": CLIENT_SECRET,
            },
        )
        env.start()
        self.addCleanup(env.stop)
        self.addCleanup(_fresh_keycloak_manager)
        _fresh_keycloak_manager()

    def _middleware_request(self, token: str):
        request = RequestFactory().get("/api/model_entries/x/list")
        request.session = {"oidc_access_token": token}
        KeycloakPermissionsMiddleware(lambda r: HttpResponse("ok"))(request)
        return request


class TestCluster04x_ConcurrentUsersStayApart(_StubKeycloakTestCase):
    """4.138-4.139: concurrent callers each get their own Keycloak answer."""

    def setUp(self) -> None:
        super().setUp()
        # A loaded server switches threads at arbitrary points. CPython's default 5 ms
        # switch interval almost never lands between "token set on a shared connection"
        # and "request built from it", so a shared-state race would pass here unseen.
        # Switching every microsecond makes those interleavings routine.
        previous = sys.getswitchinterval()
        sys.setswitchinterval(1e-6)
        self.addCleanup(sys.setswitchinterval, previous)

    # -- 4.138 ---------------------------------------------------------
    def test_4_138_middleware_never_hands_one_user_another_users_permissions(self) -> None:
        """
        Scenario 4.138: concurrent requests through the permissions middleware keep their own answers.
        Given: 12 users, 20 requests each, all in flight at once on 24 threads, against a
               Keycloak that answers UMA and userinfo for whichever token a request carries
        When: the permissions middleware handles every request
        Then: every request carries exactly its own user's permissions, profile and client
              roles -- never another user's -- and every Keycloak call carried a bearer token.
        """
        users = [f"user{i}" for i in range(12)] * 20
        random.shuffle(users)

        def handle(user):
            request = self._middleware_request(f"tok-{user}")
            return user, request.user_permissions, request.userinfo, request.client_roles

        with ThreadPoolExecutor(max_workers=24) as pool:
            results = list(pool.map(handle, users))

        wrong = [
            (user, perms, info.get("sub"))
            for user, perms, info, roles in results
            if perms != [{"rsname": f"resource-of-{user}", "rsid": user, "scopes": ["read"]}]
            or info.get("sub") != user
            or roles != [f"role-{user}"]
        ]
        self.assertEqual(
            wrong, [],
            f"{len(wrong)} of {len(results)} requests received another user's Keycloak answer "
            f"(first few: {wrong[:3]}) -- per-user calls must not share request state.",
        )
        self.assertTrue(
            all(header.startswith("Bearer tok-") for header in self.stub.seen_authorization),
            "every UMA/userinfo call must carry the caller's own bearer token",
        )

    # -- 4.139 ---------------------------------------------------------
    def test_4_139_keycloak_manager_answers_each_caller_with_its_own_permissions(self) -> None:
        """
        Scenario 4.139: KeycloakManager.get_uma_permissions is safe to call from many threads.
        Given: the one process-wide KeycloakManager that Reflex dashboards, Streamlit
               dashboards and the token views ask, and 10 users asking 25 times each at once
        When: they all call get_uma_permissions(token) concurrently
        Then: every caller receives the permissions of its own token,
              and no cookie Keycloak (or an ingress) set while answering one caller
              travels with any later caller's request.
        """
        manager = KeycloakManager()
        users = [f"user{i}" for i in range(10)] * 25
        random.shuffle(users)

        with ThreadPoolExecutor(max_workers=20) as pool:
            results = list(pool.map(lambda u: (u, manager.get_uma_permissions(f"tok-{u}")), users))

        wrong = [(u, p) for u, p in results if p != [{"rsname": f"resource-of-{u}", "rsid": u, "scopes": ["read"]}]]
        self.assertEqual(wrong, [], f"{len(wrong)} of {len(results)} callers got another user's permissions: {wrong[:3]}")
        self.assertEqual(self.stub.seen_cookies, [], "a cookie from an earlier caller was sent to Keycloak")


class TestCluster04x_KeycloakAnswersAreClassified(_StubKeycloakTestCase):
    """4.140-4.143: what callers see when Keycloak says no, rejects the token, or is down."""

    # -- 4.140 ---------------------------------------------------------
    def test_4_140_access_denied_is_an_empty_answer(self) -> None:
        """
        Scenario 4.140: a user Keycloak grants nothing to has no permissions -- not an error.
        Given: Keycloak answers the UMA request with 403 access_denied
        When: the middleware, get_uma_permissions and get_user_permissions ask for that user
        Then: the request carries [], get_uma_permissions returns [] (a real answer, which
              Reflex caches like any other) and get_user_permissions returns an empty set.
        """
        request = self._middleware_request("tok-denied")
        self.assertEqual(request.user_permissions, [], "no permissions, not an error, for access_denied")
        manager = KeycloakManager()
        self.assertEqual(manager.get_uma_permissions("tok-denied"), [])
        self.assertEqual(manager.get_user_permissions("tok-denied", get_user_model()), set())

    # -- 4.141 ---------------------------------------------------------
    def test_4_141_rejected_token_leaves_the_request_unprivileged(self) -> None:
        """
        Scenario 4.141: a token Keycloak no longer accepts yields no permissions and no profile.
        Given: Keycloak answers 401 invalid_grant / invalid_token for the token
        When: the middleware handles the request and the manager is asked directly
        Then: the request still reaches the view with [] permissions, {} profile and no
              roles; get_uma_permissions returns None ("Keycloak could not say").
        """
        request = self._middleware_request("tok-expired")
        self.assertEqual((request.user_permissions, request.userinfo, request.client_roles), ([], {}, []))
        self.assertIsNone(KeycloakManager().get_uma_permissions("tok-expired"))
        with self.assertRaises(uma_client.KeycloakUnauthorized):
            uma_client.fetch_userinfo("tok-expired")

    # -- 4.142 ---------------------------------------------------------
    def test_4_142_keycloak_outage_never_breaks_the_request_or_logs_the_token(self) -> None:
        """
        Scenario 4.142: a Keycloak outage keeps the request alive and keeps tokens out of logs.
        Given: Keycloak answers 502 with an HTML page, and separately cannot be reached at all
        When: the middleware handles requests in both cases
        Then: both requests reach the view with the empty defaults, the client reports the
              outage as KeycloakUnavailable, and no log line contains the access token.
        """
        with self.assertLogs("lex", level=logging.WARNING) as logs:
            broken = self._middleware_request("tok-broken")
        self.assertEqual((broken.user_permissions, broken.userinfo), ([], {}))
        with self.assertRaises(uma_client.KeycloakUnavailable):
            uma_client.fetch_uma_permissions("tok-broken")

        unreachable = uma_client.KeycloakEndpoints(
            token_url="http://127.0.0.1:9/realms/lex/protocol/openid-connect/token",
            userinfo_url="http://127.0.0.1:9/realms/lex/protocol/openid-connect/userinfo",
            client_id=CLIENT_ID,
        )
        with self.assertRaises(uma_client.KeycloakUnavailable):
            uma_client.fetch_uma_permissions("tok-secret-value", endpoints=unreachable, timeout=(0.5, 0.5))
        with mock.patch.object(uma_client.KeycloakEndpoints, "from_environment", return_value=unreachable):
            with self.assertLogs("lex", level=logging.WARNING) as more_logs:
                down = self._middleware_request("tok-secret-value")
        self.assertEqual((down.user_permissions, down.userinfo), ([], {}))
        joined = "\n".join(logs.output + more_logs.output)
        self.assertNotIn("tok-broken", joined, "an access token must never be written to the log")
        self.assertNotIn("tok-secret-value", joined, "an access token must never be written to the log")

    # -- 4.143 ---------------------------------------------------------
    def test_4_143_tls_setting_and_timeouts_reach_every_call(self) -> None:
        """
        Scenario 4.143: per-user calls verify TLS as configured and never wait unbounded.
        Given: KEYCLOAK_VERIFY_SSL set to True, False, or a CA-bundle path
        When: UMA and userinfo are requested
        Then: each request goes out with that verification setting and the (3.05, 10)
              connect/read timeout, and with the caller's bearer token on that request.
        """
        calls = []

        class _Recorder:
            def post(self, url, **kwargs):
                calls.append(("post", kwargs))
                return mock.Mock(status_code=200, json=lambda: [])

            def get(self, url, **kwargs):
                calls.append(("get", kwargs))
                return mock.Mock(status_code=200, json=lambda: {"sub": "x"})

        for verify in (True, False, "/etc/ssl/internal-ca.pem"):
            calls.clear()
            with override_settings(KEYCLOAK_VERIFY_SSL=verify), \
                    mock.patch.object(uma_client, "_session", return_value=_Recorder()):
                uma_client.fetch_uma_permissions("tok-a")
                uma_client.fetch_userinfo("tok-a")
            for method, kwargs in calls:
                self.assertEqual(kwargs["verify"], verify, f"{method} must honour KEYCLOAK_VERIFY_SSL={verify!r}")
                self.assertEqual(kwargs["timeout"], (3.05, 10.0), f"{method} must use the bounded timeout")
                self.assertEqual(kwargs["headers"]["Authorization"], "Bearer tok-a")


def _rsa_keypair():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_pem = key.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    return key, public_pem


_PRIVATE_KEY, _PUBLIC_PEM = _rsa_keypair()

#: Enough explicit OP configuration for the bearer backend to start without any network call.
_OIDC_TEST_SETTINGS = dict(
    OIDC_RP_CLIENT_ID=CLIENT_ID,
    OIDC_RP_CLIENT_SECRET=CLIENT_SECRET,
    OIDC_OP_DISCOVERY_DOCUMENT_URL="",
    OIDC_OP_AUTHORIZATION_URL="https://keycloak.test/realms/lex/protocol/openid-connect/auth",
    OIDC_OP_TOKEN_URL="https://keycloak.test/realms/lex/protocol/openid-connect/token",
    OIDC_OP_USERINFO_URL="https://keycloak.test/realms/lex/protocol/openid-connect/userinfo",
    OIDC_OP_JWKS_URL=None,
    OIDC_OP_END_SESSION_URL=None,
    OIDC_RP_IDP_SIGN_KEY=_PUBLIC_PEM,
)


def _claims(email: str) -> dict:
    now = int(time.time())
    return {"aud": CLIENT_ID, "email": email, "sub": email, "iat": now, "exp": now + 300}


class TestCluster04x_ForgedBearerTokens(TestCase):
    """4.144: the bearer path accepts Keycloak's RS256 tokens and nothing signed with the client secret."""

    # -- 4.144 ---------------------------------------------------------
    def test_4_144_token_signed_with_the_client_secret_is_not_accepted(self) -> None:
        """
        Scenario 4.144: a token "signed" with the instance's client secret does not sign anyone in.
        Given: lex-app's settings (OIDC_RP_SIGN_ALGOS_ALLOWED) and an attacker who knows the
               client secret, which sits in every instance's environment
        When: they call an authenticated API with an HS256 token for a victim's e-mail, and
              -- as a control -- a real RS256 token from the identity provider's key is used
        Then: the forged token gets no access and creates or signs in no user; the RS256
              token is accepted.
        """
        self.assertEqual(settings.OIDC_RP_SIGN_ALGOS_ALLOWED, ["RS256"], "lex-app must accept Keycloak's RS256 only")
        User = get_user_model()
        victim = "victim@customer.example"
        forged = jwt.encode(_claims(victim), CLIENT_SECRET, algorithm="HS256")

        with override_settings(**_OIDC_TEST_SETTINGS):
            response = self.client.get("/api/user_permissions/", HTTP_AUTHORIZATION=f"Bearer {forged}")
            self.assertNotEqual(response.status_code, 200, "a forged HS256 token must not reach the view")
            self.assertFalse(User.objects.filter(email=victim).exists(), "a forged token must not create or sign in a user")

            genuine = jwt.encode(_claims("real.user@customer.example"), _PRIVATE_KEY, algorithm="RS256")
            accepted = self.client.get("/api/user_permissions/", HTTP_AUTHORIZATION=f"Bearer {genuine}")
            self.assertEqual(accepted.status_code, 200, "Keycloak's RS256 token must still be accepted")
            self.assertTrue(User.objects.filter(email="real.user@customer.example").exists())


class TestCluster04x_AnchoredLoginExemptions(TestCase):
    """4.145-4.147: only the real public endpoints skip sign-in."""

    def _redirected_to_sign_in(self, response) -> bool:
        """True when the login middleware refused the request for want of a signed-in user.

        A navigation is redirected to /oidc/authenticate; from django-oauth2-codeflow 2.1.0 a
        background request (any /api/ path) gets 401 JSON with a ``login_url`` instead (older
        releases answered such JSON with a ``token_type``). A view's own 401/403 -- DRF's
        ``{"detail": ...}`` -- is not the login middleware and does not count.
        """
        if response.status_code == 302:
            return "/oidc/authenticate" in response.get("Location", "")
        if response.status_code == 401 and response.get("Content-Type", "").startswith("application/json"):
            body = response.json()
            return "login_url" in body or "token_type" in body
        return False

    # -- 4.145 ---------------------------------------------------------
    def test_4_145_real_public_endpoints_still_skip_sign_in(self) -> None:
        """
        Scenario 4.145: the endpoints meant to be public still answer without a signed-in user.
        Given: an anonymous browser
        When: it requests the health checks, the current-user probe, the active-calculations
              probe, the manifest and the favicon -- including the /api/api/... forms the
              ingress produces for the frontend
        Then: none of them is redirected to sign-in by the login middleware.
        """
        for path in (
            "/health", "/api/health", "/api/api/health",
            "/api/user/", "/api/user", "/api/api/user/", "/api/api/user",
            "/api/active-calculations", "/api/api/active-calculations",
            "/manifest.json", "/favicon.ico", "/api/favicon.ico",
        ):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertFalse(self._redirected_to_sign_in(response), f"{path} must stay public")

    # -- 4.146 ---------------------------------------------------------
    def test_4_146_look_alike_paths_require_sign_in(self) -> None:
        """
        Scenario 4.146: a path that merely contains a public endpoint's name is not public.
        Given: an anonymous browser
        When: it requests /api/user_permissions/ (a real endpoint whose name contains
              "api/user"), /api/users/, and paths containing "/health"
        Then: each is redirected to sign-in instead of being served.
        """
        for path in (
            "/api/user_permissions/",
            "/api/users/",
            "/api/model_entries/healthx/list",
            "/api/global-search/health",
            "/api/user/../model_entries/x/list",
            "/process_admin/manifest.json.bak",
        ):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertTrue(
                    self._redirected_to_sign_in(response),
                    f"{path} must require sign-in (got {response.status_code})",
                )

    # -- 4.147 ---------------------------------------------------------
    def test_4_147_every_exemption_is_anchored(self) -> None:
        """
        Scenario 4.147: every login exemption matches whole paths only.
        Given: the configured OIDC_MIDDLEWARE_NO_AUTH_URL_PATTERNS
        When: each pattern is inspected
        Then: each starts with ^ and ends with $ or a path-boundary group, so the library's
              re.search cannot match it in the middle of another path.
        """
        for pattern in settings.OIDC_MIDDLEWARE_NO_AUTH_URL_PATTERNS:
            with self.subTest(pattern=pattern):
                self.assertTrue(pattern.startswith("^"), f"{pattern!r} must be anchored at the start")
                self.assertTrue(
                    pattern.endswith("$") or pattern.endswith("(?:/|$)"),
                    f"{pattern!r} must be anchored at the end",
                )
