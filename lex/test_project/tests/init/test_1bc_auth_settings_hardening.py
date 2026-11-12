"""Deployed secret key, session lifetime and PKCE: the sign-in settings a deployed instance runs with.

Intent
------
* **A deployed instance never runs on the published secret key.** The settings
  fall back to a development key that ships with this package; anything it signs
  (session data, signed tokens, the dashboard proxy's derived cookie key) is
  forgeable by anyone who has read the source. A deployed pod -- one with
  ``DEPLOYMENT_ENVIRONMENT`` set -- refuses to start without its own
  ``DJANGO_SECRET_KEY``, the same way it already refuses to start without
  ``DOMAIN_HOSTED``.
* **Django never ends a sign-in before Keycloak does.** Keycloak decides how long
  a sign-in lasts (app realms: 30 days idle). The session cookie lives 31 days
  by default (``LEX_SESSION_COOKIE_AGE_DAYS``) and the library's own session
  clock is 30 days, inside the cookie as the library requires -- previously the
  14-day Django default and a fixed 7-day library cap logged people out early.
* **Every authorization-code exchange is PKCE-protected.** Both relying parties
  on the shared Keycloak client -- Django's sign-in and the dashboard proxy's own
  sign-in -- send an S256 challenge and keep the verifier server-side, so an
  intercepted code is useless, and the client can later be set to require PKCE.

Cluster 1bc -- scenarios 1.481-1.489. Type: U (1.481-1.486, 1.489) and I (1.487-1.488).
Covers: lex/lex_app/settings.py (PUBLISHED_DEFAULT_SECRET_KEY, deployed checks,
SESSION_COOKIE_AGE, OIDC_MIDDLEWARE_SESSION_TIMEOUT_SECONDS, OIDC_RP_USE_PKCE,
OIDC_RP_FORCE_SECRET_WITH_PKCE), lex/proxy.py (OIDC client registration).
Run: python -m lex pytest lex/test_project/tests/init/test_1bc_auth_settings_hardening.py -v
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from unittest import mock
from unittest.mock import AsyncMock
from urllib.parse import parse_qs, urlparse

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import ImproperlyConfigured
from django.test import SimpleTestCase, TestCase, override_settings
from starlette.testclient import TestClient

import lex
import lex.proxy as proxy
from lex.lex_app.settings import PUBLISHED_DEFAULT_SECRET_KEY

pytestmark = pytest.mark.init

#: The checkout holding the ``lex`` package, so a fresh interpreter imports this lex.
_CHECKOUT = Path(lex.__file__).resolve().parents[1]

#: Variables a fresh interpreter must not inherit from the test process.
_CLEARED = (
    "DJANGO_SETTINGS_MODULE", "PROJECT_ROOT", "PYTHONPATH", "DEPLOYMENT_ENVIRONMENT", "DJANGO_SECRET_KEY",
    "LEX_SESSION_COOKIE_AGE_DAYS", "DOMAIN_HOSTED", "REDIS_HOST", "REDIS_USERNAME", "REDIS_PASSWORD",
)

#: What a deployed pod has besides the secret key (the checks before it must pass).
_DEPLOYED = {
    "DEPLOYMENT_ENVIRONMENT": "DEV",
    "DOMAIN_HOSTED": "app.example.com",
    "REDIS_HOST": "127.0.0.1:6391",
    "REDIS_USERNAME": "default",
    "REDIS_PASSWORD": "redis-password",
}

_SETTINGS_PROBE = (
    "import json\n"
    "try:\n"
    "    from lex.lex_app import settings as s\n"
    "except Exception as exc:\n"
    "    print(json.dumps({'error': type(exc).__name__, 'message': str(exc)}))\n"
    "else:\n"
    "    print(json.dumps({'secret_key': s.SECRET_KEY, 'cookie_age': s.SESSION_COOKIE_AGE,\n"
    "        'oidc_timeout': s.OIDC_MIDDLEWARE_SESSION_TIMEOUT_SECONDS,\n"
    "        'algos': s.OIDC_RP_SIGN_ALGOS_ALLOWED, 'pkce': s.OIDC_RP_USE_PKCE,\n"
    "        'secret_with_pkce': s.OIDC_RP_FORCE_SECRET_WITH_PKCE}))\n"
)


def _settings_in_fresh_interpreter(**env_overrides) -> dict:
    """Import lex's settings in a new interpreter with ``env_overrides``; return what it reports."""
    env = {k: v for k, v in os.environ.items() if k not in _CLEARED}
    env["PYTHONPATH"] = _CHECKOUT.as_posix()
    env.update(env_overrides)
    result = subprocess.run(
        [sys.executable, "-c", _SETTINGS_PROBE], cwd=_CHECKOUT, env=env, capture_output=True, text=True, timeout=180
    )
    assert result.returncode == 0, f"fresh interpreter crashed:\n{result.stderr[-3000:]}"
    return json.loads(result.stdout.strip().splitlines()[-1])


DAY = 86400


class TestCluster01bc_DeployedSecretKey(SimpleTestCase):
    """1.481-1.483: a deployed pod refuses to sign anything with the published key."""

    # -- 1.481 ---------------------------------------------------------
    def test_1_481_deployed_pod_without_a_secret_key_refuses_to_start(self) -> None:
        """
        Scenario 1.481: a deployed pod with no DJANGO_SECRET_KEY does not start.
        Given: DEPLOYMENT_ENVIRONMENT, DOMAIN_HOSTED and the Redis variables set, DJANGO_SECRET_KEY unset
        When: the settings module is imported
        Then: it raises ImproperlyConfigured naming DJANGO_SECRET_KEY instead of falling back
              to the published development key.
        """
        report = _settings_in_fresh_interpreter(**_DEPLOYED)
        self.assertEqual(report.get("error"), "ImproperlyConfigured", f"must refuse to start, got {report}")
        self.assertIn("DJANGO_SECRET_KEY", report["message"])

    # -- 1.482 ---------------------------------------------------------
    def test_1_482_deployed_pod_on_the_published_key_refuses_to_start(self) -> None:
        """
        Scenario 1.482: a deployed pod configured with the published key does not start either.
        Given: a deployed environment whose DJANGO_SECRET_KEY is the key shipped in this package
        When: the settings module is imported
        Then: it raises ImproperlyConfigured -- setting the variable to the public value is
              no better than not setting it.
        """
        report = _settings_in_fresh_interpreter(**_DEPLOYED, DJANGO_SECRET_KEY=PUBLISHED_DEFAULT_SECRET_KEY)
        self.assertEqual(report.get("error"), "ImproperlyConfigured", f"must refuse the published key, got {report}")

    # -- 1.483 ---------------------------------------------------------
    def test_1_483_private_key_is_used_and_local_runs_keep_the_dev_key(self) -> None:
        """
        Scenario 1.483: a private key starts the pod; local development is unaffected.
        Given: a deployed environment with its own DJANGO_SECRET_KEY, and separately a local
               run with no DEPLOYMENT_ENVIRONMENT and no key
        When: the settings module is imported in each
        Then: the deployed pod starts and signs with its own key; the local run starts with
              the development key, as before.
        """
        deployed = _settings_in_fresh_interpreter(**_DEPLOYED, DJANGO_SECRET_KEY="a-private-instance-key")
        self.assertNotIn("error", deployed, f"a private key must start the pod: {deployed}")
        self.assertEqual(deployed["secret_key"], "a-private-instance-key")
        local = _settings_in_fresh_interpreter()
        self.assertNotIn("error", local, f"local runs must not need a key: {local}")
        self.assertEqual(local["secret_key"], PUBLISHED_DEFAULT_SECRET_KEY)


class TestCluster01bc_SessionLifetime(SimpleTestCase):
    """1.484-1.486: Django keeps a sign-in at least as long as Keycloak does."""

    # -- 1.484 ---------------------------------------------------------
    def test_1_484_defaults_outlive_the_keycloak_idle_window(self) -> None:
        """
        Scenario 1.484: by default a sign-in survives 30 idle days on the Django side.
        Given: no LEX_SESSION_COOKIE_AGE_DAYS
        When: the settings module is imported
        Then: the session cookie lives 31 days and the library's session clock 30 days,
              inside the cookie; ID tokens are accepted as RS256 only; PKCE is on with the
              client secret still sent for the confidential client.
        """
        report = _settings_in_fresh_interpreter()
        self.assertEqual(report["cookie_age"], 31 * DAY)
        self.assertEqual(report["oidc_timeout"], 30 * DAY)
        self.assertLess(report["oidc_timeout"], report["cookie_age"], "the library requires timeout < cookie age")
        self.assertEqual(report["algos"], ["RS256"])
        self.assertTrue(report["pkce"], "PKCE must be on")
        self.assertTrue(report["secret_with_pkce"], "the confidential client must still send its secret")

    # -- 1.485 ---------------------------------------------------------
    def test_1_485_lifetime_follows_the_environment_and_rejects_nonsense(self) -> None:
        """
        Scenario 1.485: LEX_SESSION_COOKIE_AGE_DAYS sets the lifetime; unusable values stop startup.
        Given: LEX_SESSION_COOKIE_AGE_DAYS set to 45, 10, "abc" and 1
        When: the settings module is imported with each
        Then: 45 gives a 45-day cookie with the 30-day session clock; 10 gives 10 days with a
              9-day clock (still inside the cookie); "abc" and 1 refuse to start with a clear error.
        """
        long = _settings_in_fresh_interpreter(LEX_SESSION_COOKIE_AGE_DAYS="45")
        self.assertEqual((long["cookie_age"], long["oidc_timeout"]), (45 * DAY, 30 * DAY))
        short = _settings_in_fresh_interpreter(LEX_SESSION_COOKIE_AGE_DAYS="10")
        self.assertEqual((short["cookie_age"], short["oidc_timeout"]), (10 * DAY, 9 * DAY))
        for bad in ("abc", "1"):
            with self.subTest(value=bad):
                report = _settings_in_fresh_interpreter(LEX_SESSION_COOKIE_AGE_DAYS=bad)
                self.assertEqual(report.get("error"), "ImproperlyConfigured", f"{bad!r} must not start: {report}")
                self.assertIn("LEX_SESSION_COOKIE_AGE_DAYS", report["message"])

    # -- 1.486 ---------------------------------------------------------
    def test_1_486_library_session_middleware_accepts_the_configured_lifetimes(self) -> None:
        """
        Scenario 1.486: the OIDC session middleware starts with the configured lifetimes.
        Given: the default settings, and the shortest allowed override (2 days -> 1-day clock)
        When: the library's RefreshSessionMiddleware is constructed, as Django does at startup
        Then: it does not raise -- its "timeout must be shorter than the cookie" check holds.
        """
        from oauth2_authcodeflow.middleware import RefreshSessionMiddleware

        RefreshSessionMiddleware(lambda request: None)
        with override_settings(SESSION_COOKIE_AGE=2 * DAY, OIDC_MIDDLEWARE_SESSION_TIMEOUT_SECONDS=DAY):
            try:
                RefreshSessionMiddleware(lambda request: None)
            except ImproperlyConfigured as exc:  # pragma: no cover - the assertion message is the point
                self.fail(f"the library rejected the configured lifetimes: {exc}")


def _rsa_keypair():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_pem = key.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    return key, public_pem


_PRIVATE_KEY, _PUBLIC_PEM = _rsa_keypair()
_CLIENT_ID = "lex-app"
_CLIENT_SECRET = "the-instance-client-secret"

_OIDC_FLOW_SETTINGS = dict(
    OIDC_RP_CLIENT_ID=_CLIENT_ID,
    OIDC_RP_CLIENT_SECRET=_CLIENT_SECRET,
    OIDC_OP_DISCOVERY_DOCUMENT_URL="",
    OIDC_OP_AUTHORIZATION_URL="https://keycloak.test/realms/lex/protocol/openid-connect/auth",
    OIDC_OP_TOKEN_URL="https://keycloak.test/realms/lex/protocol/openid-connect/token",
    OIDC_OP_USERINFO_URL="https://keycloak.test/realms/lex/protocol/openid-connect/userinfo",
    OIDC_OP_JWKS_URL=None,
    OIDC_OP_END_SESSION_URL=None,
    OIDC_OP_FETCH_USER_INFO=False,
    OIDC_RP_IDP_SIGN_KEY=_PUBLIC_PEM,
    AUTHENTICATION_BACKENDS=(
        "oauth2_authcodeflow.auth.AuthenticationBackend",
        "django.contrib.auth.backends.ModelBackend",
    ),
)


def _s256(verifier: str) -> str:
    return base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).decode("ascii").rstrip("=")


@override_settings(**_OIDC_FLOW_SETTINGS)
class TestCluster01bc_DjangoSignInUsesPkce(TestCase):
    """1.487-1.488: Django's sign-in sends a PKCE challenge and proves it at the code exchange."""

    def _start_sign_in(self):
        response = self.client.get("/oidc/authenticate", {"next": "/after-sign-in", "fail": "/sign-in-failed"})
        self.assertEqual(response.status_code, 302, response.content[:300])
        location = urlparse(response["Location"])
        self.assertTrue(response["Location"].startswith(_OIDC_FLOW_SETTINGS["OIDC_OP_AUTHORIZATION_URL"]))
        return parse_qs(location.query)

    # -- 1.487 ---------------------------------------------------------
    def test_1_487_sign_in_redirect_carries_an_s256_challenge(self) -> None:
        """
        Scenario 1.487: the redirect to Keycloak carries a PKCE S256 challenge, never the verifier.
        Given: an anonymous browser
        When: it starts signing in at /oidc/authenticate
        Then: the authorization URL has code_challenge_method=S256 and a code_challenge that
              is the S256 hash of a verifier kept in the server-side session; the verifier
              itself is not in the URL.
        """
        query = self._start_sign_in()
        self.assertEqual(query.get("code_challenge_method"), ["S256"])
        self.assertNotIn("code_verifier", query, "the verifier must never leave the server")
        verifier = self.client.session.get("oidc_challenge")
        self.assertTrue(verifier, "the verifier must be kept in the session")
        self.assertEqual(query["code_challenge"], [_s256(verifier)])

    # -- 1.488 ---------------------------------------------------------
    def test_1_488_code_exchange_proves_the_verifier_and_sends_the_secret(self) -> None:
        """
        Scenario 1.488: the callback redeems the code with the verifier and the client secret.
        Given: a sign-in started at /oidc/authenticate and Keycloak answering the code exchange
               with an RS256 ID token
        When: the browser returns to /oidc/callback with the authorization code
        Then: the token request carries the session's code_verifier and the client secret, and
              the user is signed in and sent to the page they asked for.
        """
        query = self._start_sign_in()
        verifier = self.client.session["oidc_challenge"]
        claims = {"aud": _CLIENT_ID, "email": "signed.in@example.com", "sub": "kc-user-1",
                  "iat": int(time.time()), "exp": int(time.time()) + 300}
        if query.get("nonce"):
            claims["nonce"] = query["nonce"][0]
        id_token = jwt.encode(claims, _PRIVATE_KEY, algorithm="RS256")
        token_response = mock.Mock(status_code=200)
        token_response.json.return_value = {
            "id_token": id_token, "access_token": "at", "refresh_token": "rt", "expires_in": 300,
        }
        callback = {"code": "the-authorization-code"}
        if query.get("state"):
            callback["state"] = query["state"][0]
        with mock.patch("oauth2_authcodeflow.auth.request_post", return_value=token_response) as post:
            response = self.client.get("/oidc/callback", callback)

        sent = post.call_args.kwargs.get("data") or post.call_args.args[1]
        self.assertEqual(sent.get("grant_type"), "authorization_code")
        self.assertEqual(sent.get("code_verifier"), verifier, "the code exchange must prove the PKCE verifier")
        self.assertEqual(sent.get("client_secret"), _CLIENT_SECRET, "the confidential client must still authenticate")
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response["Location"].endswith("/after-sign-in"), response["Location"])
        user = get_user_model().objects.get(email="signed.in@example.com")
        self.assertEqual(str(self.client.session.get("_auth_user_id")), str(user.pk), "the user must be signed in")


class TestCluster01bc_ProxySignInUsesPkce(SimpleTestCase):
    """1.489: the dashboard proxy's own sign-in is PKCE-protected too."""

    # -- 1.489 ---------------------------------------------------------
    def test_1_489_proxy_sign_in_redirect_carries_an_s256_challenge(self) -> None:
        """
        Scenario 1.489: the dashboard proxy's /auth/login sends a PKCE S256 challenge.
        Given: the Streamlit auth proxy with Keycloak's metadata available
        When: a browser without a dashboard session starts the proxy's sign-in at /auth/login
        Then: the redirect to Keycloak carries code_challenge_method=S256 and a code_challenge,
              and no code_verifier.
        """
        metadata = {
            "issuer": "https://keycloak.test/realms/lex",
            "authorization_endpoint": "https://keycloak.test/realms/lex/protocol/openid-connect/auth",
            "token_endpoint": "https://keycloak.test/realms/lex/protocol/openid-connect/token",
            "jwks_uri": "https://keycloak.test/realms/lex/protocol/openid-connect/certs",
        }
        with mock.patch.object(proxy.oauth.oidc, "load_server_metadata", AsyncMock(return_value=metadata)):
            response = TestClient(proxy.app).get("/auth/login", follow_redirects=False)
        self.assertIn(response.status_code, (302, 303, 307), response.text[:300])
        location = response.headers["location"]
        self.assertTrue(location.startswith(metadata["authorization_endpoint"]), location)
        query = parse_qs(urlparse(location).query)
        self.assertEqual(query.get("code_challenge_method"), ["S256"])
        self.assertTrue(query.get("code_challenge", [""])[0], "a challenge must be sent")
        self.assertNotIn("code_verifier", query, "the verifier must never leave the server")
