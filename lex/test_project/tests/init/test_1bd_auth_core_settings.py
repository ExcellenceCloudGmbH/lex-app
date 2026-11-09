"""The settings, routes and bootstrap pieces lex-app 2.5 runs django-oauth2-codeflow 3.0 with.

Intent
------
lex-app 2.5 adopts django-oauth2-codeflow 3.0 so users stay signed in for weeks while
revocation, token protection and failure behaviour meet enterprise expectations (plan
"Long-lived, secure sessions", section 6.2 P4). On the settings side that means:

* **The middleware runs in the library's order** -- Session, CORS, Common, CSRF, Auth, then
  the bearer middleware, then the OIDC session middleware, and only later the login policy
  and lex's permission lookup -- so every request has ``request.oidc`` before anything asks
  who it is, and the library's own order check passes.
* **Server-to-server Keycloak traffic can stay inside the cluster.** Discovery (and so
  refresh, JWKS and UMA) use ``KEYCLOAK_INTERNAL_URL`` when set, while the issuer every token
  must carry stays the public ``KEYCLOAK_URL`` realm. PKCE is on and the confidential client
  still authenticates with its secret.
* **TLS to Keycloak is verified on every deployed instance** (``KEYCLOAK_VERIFY_SSL`` defaults
  to true there), and the token encryption keys can come from ``OIDC_TOKEN_ENCRYPTION_KEYS``.
* **CORS is strict by default** (security flag ``CORS``): credentialed cross-origin requests
  only from ``https://<DOMAIN_HOSTED>``, the ``LEX_INSTANCE_HOSTS`` hosts and
  ``LEX_CORS_ALLOWED_ORIGINS``; localhost only when not deployed; never ``*``.
  ``LEX_SEC_CORS=false`` is the documented opt-out. The same origins are the only redirect
  targets the sign-in accepts (``OIDC_ALLOWED_REDIRECT_HOSTS``).
* **``LEX_INSTANCE_HOSTS`` is read in one place** (``lex.core.instance_hosts``), with a
  fallback to ``DOMAIN_HOSTED`` / ``STREAMLIT_URL`` / ``REFLEX_URL`` and no crash on bad input.
* **Every forced sign-out is visible**: the library's ``event=oidc.session.*`` lines reach
  stdout at INFO whatever ``LOG_LEVEL`` says.
* **The contract endpoints are routed and public**: ``api/auth/session`` and
  ``oidc/auth-complete`` (also under ``DJANGO_BASE_PATH``), exempt from the login requirement.
* **``lex init`` keeps the client's sign-in hardening** (``attributes`` merged,
  ``frontchannelLogout``), and **writes no migration into the library** any more.

A regression here signs people out (wrong order, wrong issuer), lets any website act as the
signed-in user (CORS), or hides forced sign-outs from the alerts built on the event lines.

Cluster 1bd -- scenarios 1.490-1.500. Type: U (1.490-1.493, 1.496-1.497, 1.499-1.500) and
I (1.494-1.495, 1.498).
Covers: lex/lex_app/settings.py (MIDDLEWARE, OIDC_OP_DISCOVERY_DOCUMENT_URL, OIDC_OP_ISSUER,
OIDC_RP_CLIENT_AUTH_METHOD, KEYCLOAK_VERIFY_SSL, OIDC_TOKEN_ENCRYPTION_KEYS, CORS_*,
OIDC_ALLOWED_REDIRECT_HOSTS, OIDC_LOGIN_REQUIRED_EXEMPT_URL_PATTERNS, LOGGING),
lex/core/security_flags.py (CORS), lex/core/instance_hosts.py, lex/lex_app/urls.py,
lex/process_admin/sites/process_admin_site.py, lex/lex_app/management/commands/init.py
(_snapshot_client_settings, _restore_client_settings).
Run: python -m lex pytest lex/test_project/tests/init/test_1bd_auth_core_settings.py -v
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from unittest import TestCase as PlainTestCase
from unittest import mock

import pytest
from django.conf import settings
from django.test import RequestFactory, SimpleTestCase, TestCase, override_settings
from django.urls import resolve, reverse
from oauth2_authcodeflow.checks import check_middleware_order
from oauth2_authcodeflow.testing import FakeOP

import lex
from lex.authentication.middleware import ApiKeyAwareLoginRequiredMiddleware
from lex.authentication.views.session_views import AuthCompleteView, SessionStatusView
from lex.core.instance_hosts import Endpoint, instance_hosts, parse_endpoint, parse_instance_hosts
from lex.lex_app import settings as lex_settings
from lex.lex_app.management.commands.init import KeycloakSyncManager

pytestmark = pytest.mark.init

#: The checkout holding the ``lex`` package, so a fresh interpreter imports this lex.
_CHECKOUT = Path(lex.__file__).resolve().parents[1]

#: Variables a fresh interpreter must not inherit from the test process (or its .env).
_CLEARED = (
    "DJANGO_SETTINGS_MODULE", "PROJECT_ROOT", "PYTHONPATH", "DEPLOYMENT_ENVIRONMENT", "DJANGO_SECRET_KEY",
    "DOMAIN_HOSTED", "REDIS_HOST", "REDIS_USERNAME", "REDIS_PASSWORD", "KEYCLOAK_URL", "KEYCLOAK_REALM",
    "KEYCLOAK_INTERNAL_URL", "KEYCLOAK_VERIFY_SSL", "OIDC_RP_CLIENT_ID", "OIDC_RP_CLIENT_SECRET",
    "OIDC_TOKEN_ENCRYPTION_KEYS", "LEX_INSTANCE_HOSTS", "STREAMLIT_URL", "REFLEX_URL",
    "LEX_CORS_ALLOWED_ORIGINS", "LEX_SEC_CORS", "LEX_LEGACY_SECURITY", "LOG_LEVEL", "LEX_LOG_LEVEL",
    "DJANGO_BASE_PATH",
)

#: A deployed pod (the checks before the ones under test must pass).
_DEPLOYED = {
    "DEPLOYMENT_ENVIRONMENT": "DEV",
    "DOMAIN_HOSTED": "acme.lex.example",
    "DJANGO_SECRET_KEY": "a-private-key-for-the-test",
    "REDIS_HOST": "127.0.0.1:6392",
    "REDIS_USERNAME": "default",
    "REDIS_PASSWORD": "redis-password",
}

#: Keycloak as a deployed instance sees it.
_KEYCLOAK = {
    "KEYCLOAK_URL": "https://auth.example.com/",
    "KEYCLOAK_REALM": "acme",
    "OIDC_RP_CLIENT_ID": "lex-acme",
    "OIDC_RP_CLIENT_SECRET": "the-secret",
}

_INSTANCE_HOSTS = json.dumps({
    "app": {"canonical": "acme.lex.example", "extra": ["app.acme.com"], "platform": "acme.lex.example"},
    "streamlit": {"canonical": "acme-streamlit.lex.example", "extra": [], "platform": "acme-streamlit.lex.example"},
    "reflex": {"canonical": "https://acme-reflex.lex.example/", "extra": [], "platform": None},
})

_PROBE = (
    "import json\n"
    "from lex.lex_app import settings as s\n"
    "print(json.dumps({{k: getattr(s, k, None) for k in {names!r}}}))\n"
)


def _settings(names, **env_overrides) -> dict:
    """Import lex's settings in a fresh interpreter with ``env_overrides``; return ``names``."""
    env = {k: v for k, v in os.environ.items() if k not in _CLEARED}
    env["PYTHONPATH"] = _CHECKOUT.as_posix()
    env.update(env_overrides)
    result = subprocess.run(
        [sys.executable, "-c", _PROBE.format(names=list(names))],
        cwd=_CHECKOUT, env=env, capture_output=True, text=True, timeout=180,
    )
    assert result.returncode == 0, f"fresh interpreter crashed:\n{result.stderr[-3000:]}"
    return json.loads(result.stdout.strip().splitlines()[-1])


class TestCluster01bd_MiddlewareOrder(SimpleTestCase):
    """1.490: the OIDC middlewares sit where django-oauth2-codeflow 3.0 needs them."""

    # -- 1.490 ---------------------------------------------------------
    def test_1_490_middleware_follows_the_library_order(self) -> None:
        """
        Scenario 1.490: Session -> CORS -> Common -> CSRF -> Auth -> Bearer -> OIDC session -> ... -> login -> permissions.
        Given: lex-app's MIDDLEWARE
        When: the positions of the auth middlewares are compared, and the library's own
              order check (oauth2_authcodeflow.E001) runs
        Then: they come in the plan's order, the deprecated 2.x refresh aliases are gone, the
              login policy is lex's API-key-aware subclass of the library's middleware, and
              the library reports no order error.
        """
        order = list(settings.MIDDLEWARE)
        expected = [
            "django.contrib.sessions.middleware.SessionMiddleware",
            "corsheaders.middleware.CorsMiddleware",
            "django.middleware.common.CommonMiddleware",
            "django.middleware.csrf.CsrfViewMiddleware",
            "django.contrib.auth.middleware.AuthenticationMiddleware",
            "oauth2_authcodeflow.middleware.BearerAuthMiddleware",
            "oauth2_authcodeflow.middleware.OIDCSessionMiddleware",
            "lex.authentication.middleware.ApiKeyAwareLoginRequiredMiddleware",
            "lex.api.middleware.keycloak_permissions.KeycloakPermissionsMiddleware",
        ]
        positions = [order.index(name) for name in expected]
        self.assertEqual(positions, sorted(positions), f"auth middleware out of order: {order}")
        self.assertEqual(order[-1], expected[-1], "the permission lookup must run last, after the login policy")
        for alias in ("RefreshSessionMiddleware", "RefreshAccessTokenMiddleware"):
            self.assertFalse(any(alias in name for name in order), f"deprecated alias {alias} still installed")
        from oauth2_authcodeflow.middleware import LoginRequiredMiddleware

        self.assertTrue(issubclass(ApiKeyAwareLoginRequiredMiddleware, LoginRequiredMiddleware))
        self.assertTrue(hasattr(ApiKeyAwareLoginRequiredMiddleware, "check_login_required"))
        self.assertEqual(check_middleware_order(), [], "the library's middleware-order check must pass")


class TestCluster01bd_KeycloakSettings(SimpleTestCase):
    """1.491-1.492: issuer, discovery, PKCE, TLS and encryption keys from the environment."""

    # -- 1.491 ---------------------------------------------------------
    def test_1_491_internal_discovery_keeps_the_public_issuer(self) -> None:
        """
        Scenario 1.491: discovery may be internal; the issuer is always the public realm.
        Given: Keycloak configured with and without KEYCLOAK_INTERNAL_URL
        When: the settings module is imported
        Then: without it, discovery and issuer are both on KEYCLOAK_URL (trailing slash
              dropped); with it, discovery uses the internal URL while OIDC_OP_ISSUER stays
              the public realm; PKCE is on and the client authenticates with
              client_secret_post; the 2.x setting names are not used any more.
        """
        names = ("OIDC_OP_DISCOVERY_DOCUMENT_URL", "OIDC_OP_ISSUER", "OIDC_RP_USE_PKCE",
                 "OIDC_RP_CLIENT_AUTH_METHOD", "OIDC_RP_FORCE_SECRET_WITH_PKCE",
                 "OIDC_MIDDLEWARE_NO_AUTH_URL_PATTERNS", "OIDC_MIDDLEWARE_SESSION_TIMEOUT_SECONDS")
        public = _settings(names, **_KEYCLOAK)
        self.assertEqual(public["OIDC_OP_ISSUER"], "https://auth.example.com/realms/acme")
        self.assertEqual(public["OIDC_OP_DISCOVERY_DOCUMENT_URL"],
                         "https://auth.example.com/realms/acme/.well-known/openid-configuration")
        internal = _settings(names, **_KEYCLOAK, KEYCLOAK_INTERNAL_URL="http://keycloak-http.keycloak.svc:8080")
        self.assertEqual(internal["OIDC_OP_DISCOVERY_DOCUMENT_URL"],
                         "http://keycloak-http.keycloak.svc:8080/realms/acme/.well-known/openid-configuration")
        self.assertEqual(internal["OIDC_OP_ISSUER"], "https://auth.example.com/realms/acme",
                         "tokens are issued on the public URL; the issuer must not follow the internal one")
        self.assertTrue(internal["OIDC_RP_USE_PKCE"])
        self.assertEqual(internal["OIDC_RP_CLIENT_AUTH_METHOD"], "client_secret_post")
        for legacy in ("OIDC_RP_FORCE_SECRET_WITH_PKCE", "OIDC_MIDDLEWARE_NO_AUTH_URL_PATTERNS",
                       "OIDC_MIDDLEWARE_SESSION_TIMEOUT_SECONDS"):
            self.assertIsNone(internal[legacy], f"{legacy} is a 2.x name; 3.0 warns on it")

    # -- 1.492 ---------------------------------------------------------
    def test_1_492_tls_verified_when_deployed_and_encryption_keys_from_the_environment(self) -> None:
        """
        Scenario 1.492: KEYCLOAK_VERIFY_SSL defaults to on when deployed; keys come from the environment.
        Given: a local run and a deployed pod, each with and without KEYCLOAK_VERIFY_SSL, and
               OIDC_TOKEN_ENCRYPTION_KEYS set or not
        When: the settings module is imported
        Then: deployed defaults to verifying, local to not; "false", "true" and a CA bundle
              path are honoured either way; the encryption keys are the comma-separated list
              (trimmed), and unset means "derive from the secret key" (None).
        """
        names = ("KEYCLOAK_VERIFY_SSL", "OIDC_TOKEN_ENCRYPTION_KEYS")
        self.assertIs(_settings(names, **_DEPLOYED)["KEYCLOAK_VERIFY_SSL"], True)
        self.assertIs(_settings(names)["KEYCLOAK_VERIFY_SSL"], False)
        self.assertIs(_settings(names, **_DEPLOYED, KEYCLOAK_VERIFY_SSL="false")["KEYCLOAK_VERIFY_SSL"], False)
        self.assertIs(_settings(names, KEYCLOAK_VERIFY_SSL="true")["KEYCLOAK_VERIFY_SSL"], True)
        self.assertEqual(
            _settings(names, **_DEPLOYED, KEYCLOAK_VERIFY_SSL="/etc/ssl/internal-ca.pem")["KEYCLOAK_VERIFY_SSL"],
            "/etc/ssl/internal-ca.pem",
        )
        keys = _settings(names, OIDC_TOKEN_ENCRYPTION_KEYS=" key-new , key-old ,")
        self.assertEqual(keys["OIDC_TOKEN_ENCRYPTION_KEYS"], ["key-new", "key-old"])
        self.assertIsNone(_settings(names)["OIDC_TOKEN_ENCRYPTION_KEYS"])


class TestCluster01bd_StrictCors(SimpleTestCase):
    """1.493: the CORS allowlist is the instance's own origins, behind the CORS flag."""

    # -- 1.493 ---------------------------------------------------------
    def test_1_493_cors_allows_only_the_instance_origins(self) -> None:
        """
        Scenario 1.493: strict CORS by default; LEX_SEC_CORS=false is the opt-out.
        Given: a deployed pod with DOMAIN_HOSTED, LEX_INSTANCE_HOSTS and LEX_CORS_ALLOWED_ORIGINS,
               the same pod with LEX_SEC_CORS=false, and a local run
        When: the settings module is imported
        Then: deployed, credentialed CORS allows exactly https://<DOMAIN_HOSTED>, every app,
              Streamlit and Reflex host and the extra origins -- no localhost, no "*", never
              "any origin"; the opt-out allows any origin again while keeping the list; a
              local run adds the localhost dev servers; the redirect allowlist holds the
              same hosts.
        """
        names = ("CORS_ALLOWED_ORIGINS", "CORS_ALLOW_ALL_ORIGINS", "CORS_ALLOW_CREDENTIALS",
                 "CORS_ORIGIN_ALLOW_ALL", "CORS_ORIGIN_WHITELIST", "OIDC_ALLOWED_REDIRECT_HOSTS")
        deployed = _settings(names, **_DEPLOYED, LEX_INSTANCE_HOSTS=_INSTANCE_HOSTS,
                             LEX_CORS_ALLOWED_ORIGINS="https://partner.example.org/")
        self.assertEqual(deployed["CORS_ALLOWED_ORIGINS"], [
            "https://acme.lex.example",
            "https://app.acme.com",
            "https://acme-streamlit.lex.example",
            "https://acme-reflex.lex.example",
            "https://partner.example.org",
        ])
        self.assertIs(deployed["CORS_ALLOW_ALL_ORIGINS"], False)
        self.assertIs(deployed["CORS_ALLOW_CREDENTIALS"], True)
        self.assertIsNone(deployed["CORS_ORIGIN_ALLOW_ALL"], "the legacy allow-all name must not be set")
        self.assertIsNone(deployed["CORS_ORIGIN_WHITELIST"], "the legacy list (with '*') must not be set")
        self.assertTrue(set(deployed["OIDC_ALLOWED_REDIRECT_HOSTS"]) >= {
            "acme.lex.example", "app.acme.com", "acme-streamlit.lex.example", "acme-reflex.lex.example",
            "partner.example.org",
        })
        self.assertFalse(any("*" in host for host in deployed["OIDC_ALLOWED_REDIRECT_HOSTS"]))

        opted_out = _settings(names, **_DEPLOYED, LEX_INSTANCE_HOSTS=_INSTANCE_HOSTS, LEX_SEC_CORS="false")
        self.assertIs(opted_out["CORS_ALLOW_ALL_ORIGINS"], True, "LEX_SEC_CORS=false restores any-origin CORS")
        self.assertIn("https://acme.lex.example", opted_out["CORS_ALLOWED_ORIGINS"])

        local = _settings(names)
        self.assertIs(local["CORS_ALLOW_ALL_ORIGINS"], False)
        self.assertIn("http://localhost:3000", local["CORS_ALLOWED_ORIGINS"])
        for origins in (deployed["CORS_ALLOWED_ORIGINS"], opted_out["CORS_ALLOWED_ORIGINS"], local["CORS_ALLOWED_ORIGINS"]):
            self.assertNotIn("*", origins)
        self.assertFalse(any("localhost" in origin for origin in deployed["CORS_ALLOWED_ORIGINS"]),
                         "a deployed instance must not trust a developer's localhost")


class TestCluster01bd_CorsOnTheWire(TestCase):
    """1.494: what a browser sees from the CORS middleware with lex's configuration."""

    # -- 1.494 ---------------------------------------------------------
    def test_1_494_only_allowed_origins_get_credentialed_cors_headers(self) -> None:
        """
        Scenario 1.494: a foreign site gets no CORS grant; an allowed origin gets one with credentials.
        Given: lex's CORS configuration in this (local) process, whose allowlist holds the
               local dev server http://localhost:3000
        When: that origin and https://evil.example send a preflight and a credentialed GET
              to /api/health
        Then: the allowed origin is echoed back with Access-Control-Allow-Credentials: true;
              the foreign origin gets no Access-Control-Allow-Origin at all -- and nobody gets "*".
        """
        self.assertIn("http://localhost:3000", settings.CORS_ALLOWED_ORIGINS)
        for method in ("options", "get"):
            extra = {"HTTP_ACCESS_CONTROL_REQUEST_METHOD": "GET"} if method == "options" else {}
            allowed = getattr(self.client, method)("/api/health", HTTP_ORIGIN="http://localhost:3000", **extra)
            foreign = getattr(self.client, method)("/api/health", HTTP_ORIGIN="https://evil.example", **extra)
            with self.subTest(method=method):
                self.assertEqual(allowed.get("Access-Control-Allow-Origin"), "http://localhost:3000")
                self.assertEqual(allowed.get("Access-Control-Allow-Credentials"), "true")
                self.assertIsNone(foreign.get("Access-Control-Allow-Origin"),
                                  "a foreign origin must not be allowed to read credentialed answers")


class TestCluster01bd_RedirectTargets(TestCase):
    """1.495: the sign-in follows next/fail only to the instance's own hosts."""

    def setUp(self) -> None:
        env = {"DOMAIN_HOSTED": "acme.lex.example", "LEX_INSTANCE_HOSTS": _INSTANCE_HOSTS}
        origins = lex_settings._cors_allowed_origins(environ=env, deployed=True)
        self.op = FakeOP()
        overrides = override_settings(
            ALLOWED_HOSTS=["acme.lex.example"],
            CSRF_TRUSTED_ORIGINS=["https://*.acme.lex.example"],
            CORS_ALLOWED_ORIGINS=origins,
            OIDC_ALLOWED_REDIRECT_HOSTS=lex_settings._redirect_hosts(origins),
            OIDC_RP_CLIENT_ID="lex-acme",
            OIDC_RP_CLIENT_SECRET="the-secret",
            **self.op.django_settings(),
        )
        overrides.enable()
        self.addCleanup(overrides.disable)
        self.op.install()
        self.addCleanup(self.op.uninstall)

    def _authenticate(self, next_url: str):
        return self.client.get("/oidc/authenticate", {"next": next_url}, HTTP_HOST="acme.lex.example", secure=True)

    # -- 1.495 ---------------------------------------------------------
    def test_1_495_sign_in_redirects_only_to_instance_hosts(self) -> None:
        """
        Scenario 1.495: a dashboard host is a valid sign-in target; a foreign site is not.
        Given: an instance whose LEX_INSTANCE_HOSTS names its Streamlit host, with the redirect
               allowlist derived the way the settings derive it
        When: a sign-in is started with next= the Streamlit host, a relative path, and a
              foreign site
        Then: the first two are sent on to Keycloak (both lex's guard and the library accept
              them); the foreign target is refused with 400 before any redirect.
        """
        for target in ("https://acme-streamlit.lex.example/dashboards/x", "/process_admin/all"):
            with self.subTest(target=target):
                response = self._authenticate(target)
                self.assertEqual(response.status_code, 302, response.content[:300])
                self.assertTrue(response["Location"].startswith(self.op.authorization_endpoint), response["Location"])
        refused = self._authenticate("https://evil.example/steal")
        self.assertEqual(refused.status_code, 400)
        self.assertNotIn("Location", refused)


class TestCluster01bd_InstanceHosts(PlainTestCase):
    """1.496: LEX_INSTANCE_HOSTS is parsed in one place, with a safe fallback."""

    # -- 1.496 ---------------------------------------------------------
    def test_1_496_instance_hosts_from_json_or_the_old_variables(self) -> None:
        """
        Scenario 1.496: the frozen JSON shape, its fallback, and bad input.
        Given: LEX_INSTANCE_HOSTS in the platform's JSON shape (bare hosts and URLs mixed),
               an instance without it, one where it names only the app, and malformed values
        When: the hosts are parsed
        Then: each surface reports canonical, platform and extra hosts once, lower-case, with
              their origins; without the variable DOMAIN_HOSTED / STREAMLIT_URL / REFLEX_URL
              are used (keeping an http:// scheme); a surface the variable leaves out falls
              back alone; invalid JSON, a non-object and wildcard or credential-bearing hosts
              are dropped without raising.
        """
        hosts = parse_instance_hosts(_INSTANCE_HOSTS.replace("app.acme.com", "APP.Acme.com"))
        self.assertEqual(hosts.source, "LEX_INSTANCE_HOSTS")
        self.assertEqual(hosts.surface("app").hosts, ("acme.lex.example", "app.acme.com"))
        self.assertEqual(hosts.surface("reflex").origins, ("https://acme-reflex.lex.example",))
        self.assertEqual(hosts.hosts("streamlit"), ("acme-streamlit.lex.example",))
        self.assertEqual(len(hosts.origins()), 4)

        fallback = instance_hosts({"DOMAIN_HOSTED": "acme.lex.example", "STREAMLIT_URL": "http://localhost:8501",
                                   "REFLEX_URL": "https://acme-reflex.lex.example"})
        self.assertEqual(fallback.source, "fallback")
        self.assertEqual(fallback.origins(), ("https://acme.lex.example", "http://localhost:8501",
                                              "https://acme-reflex.lex.example"))

        partial = parse_instance_hosts('{"app": {"canonical": "acme.lex.example"}}',
                                       streamlit_url="https://acme-streamlit.lex.example")
        self.assertEqual(partial.hosts("app", "streamlit"), ("acme.lex.example", "acme-streamlit.lex.example"))

        for raw in ("{not json", "[1, 2]", '"a string"'):
            with self.subTest(raw=raw), self.assertLogs("lex.core.instance_hosts", level="WARNING"):
                broken = parse_instance_hosts(raw, domain_hosted="acme.lex.example")
                self.assertEqual(broken.hosts(), ("acme.lex.example",))
        self.assertEqual(parse_instance_hosts(None).hosts(), ())
        for bad in ("*", "*.acme.com", "user:pw@acme.com", "ftp://acme.com", "", None, 42):
            with self.subTest(bad=bad):
                self.assertIsNone(parse_endpoint(bad))
        self.assertEqual(parse_endpoint("localhost:8501"), Endpoint("localhost:8501", "https"))
        with self.assertRaises(KeyError):
            hosts.surface("mcp")


class TestCluster01bd_EventLog(SimpleTestCase):
    """1.497: the library's sign-in events reach the container log."""

    # -- 1.497 ---------------------------------------------------------
    def test_1_497_oidc_session_events_reach_stdout_at_info(self) -> None:
        """
        Scenario 1.497: event=oidc.session.* lines are written to stdout even with LOG_LEVEL=WARNING.
        Given: a deployed pod and a local run with LOG_LEVEL=WARNING, and lex's logging
               configuration for the oauth2_authcodeflow logger
        When: the library emits event=oidc.session.ended at INFO
        Then: the line appears on stdout in both cases -- the alerts on forced sign-outs are
              built on it -- and the logger is at INFO.
        """
        code = (
            "import json, logging, logging.config\n"
            "from lex.lex_app import settings as s\n"
            "cfg = s.LOGGING\n"
            "logging.config.dictConfig({'version': 1, 'disable_existing_loggers': False,\n"
            "    'formatters': cfg['formatters'],\n"
            "    'handlers': {'oidc_console': cfg['handlers']['oidc_console']},\n"
            "    'loggers': {'oauth2_authcodeflow': cfg['loggers']['oauth2_authcodeflow']}})\n"
            "from oauth2_authcodeflow import events\n"
            "events.emit('oidc.session.ended', logging.INFO, session_id='s-1', reason='refresh_invalid_grant')\n"
            "print(json.dumps({'level': cfg['loggers']['oauth2_authcodeflow']['level']}))\n"
        )
        for env in ({"LOG_LEVEL": "WARNING"}, dict(_DEPLOYED, LOG_LEVEL="WARNING")):
            with self.subTest(deployed="DEPLOYMENT_ENVIRONMENT" in env):
                full = {k: v for k, v in os.environ.items() if k not in _CLEARED}
                full.update(env, PYTHONPATH=_CHECKOUT.as_posix())
                result = subprocess.run([sys.executable, "-c", code], cwd=_CHECKOUT, env=full,
                                        capture_output=True, text=True, timeout=180)
                self.assertEqual(result.returncode, 0, result.stderr[-2000:])
                self.assertIn("event=oidc.session.ended", result.stdout)
                self.assertIn("reason=refresh_invalid_grant", result.stdout)
                self.assertEqual(json.loads(result.stdout.strip().splitlines()[-1])["level"], "INFO")


class TestCluster01bd_ContractRoutes(TestCase):
    """1.498: the contract endpoints are routed, before the library's, and public."""

    # -- 1.498 ---------------------------------------------------------
    def test_1_498_contract_endpoints_are_routed_and_login_exempt(self) -> None:
        """
        Scenario 1.498: api/auth/session and oidc/auth-complete resolve to lex's views and need no sign-in.
        Given: lex's URL configuration and its login policy
        When: the two paths (with and without a trailing slash, and the /api/api/ form the
              ingress produces) are resolved and checked against the login middleware, and an
              anonymous browser opens oidc/auth-complete
        Then: they resolve to SessionStatusView / AuthCompleteView ahead of the oidc/ include
              (whose own views still resolve); the process-admin site routes them too, for
              DJANGO_BASE_PATH; the login middleware lets them through while look-alikes stay
              protected; the anonymous page renders (200) instead of a sign-in redirect.
        """
        self.assertIs(resolve("/api/auth/session").func.view_class, SessionStatusView)
        self.assertIs(resolve("/api/auth/session/").func.view_class, SessionStatusView)
        self.assertIs(resolve("/oidc/auth-complete").func.view_class, AuthCompleteView)
        self.assertEqual(resolve("/oidc/authenticate").url_name, "oidc_authentication")
        self.assertIs(
            resolve(reverse("process_admin_rest_api:process-admin-auth-session")).func.view_class, SessionStatusView
        )
        self.assertTrue(reverse("process_admin_rest_api:process-admin-oidc-auth-complete").endswith("oidc/auth-complete"))

        policy = ApiKeyAwareLoginRequiredMiddleware(lambda request: None)
        factory = RequestFactory()
        for path in ("/api/auth/session", "/api/auth/session/", "/api/api/auth/session", "/oidc/auth-complete"):
            with self.subTest(path=path):
                self.assertFalse(policy.is_login_required_for_url(factory.get(path)), f"{path} must be public")
        for path in ("/api/auth/sessions", "/api/auth/session/x", "/oidc/auth-complete/../x"):
            with self.subTest(path=path):
                self.assertTrue(policy.is_login_required_for_url(factory.get(path)), f"{path} must need sign-in")

        page = self.client.get("/oidc/auth-complete", {"m": "silent"}, HTTP_SEC_FETCH_MODE="navigate",
                               HTTP_SEC_FETCH_DEST="iframe")
        self.assertEqual(page.status_code, 200, "the result page must render for a signed-out browser")


def _sync_manager():
    """A KeycloakSyncManager whose Keycloak admin client is a mock (no .env, no Keycloak)."""
    manager = KeycloakSyncManager.__new__(KeycloakSyncManager)
    manager.kc_manager = mock.MagicMock()
    manager.kc_manager.client_uuid = "client-uuid"
    return manager


class TestCluster01bd_InitKeepsClientHardening(SimpleTestCase):
    """1.499: lex init puts the client's sign-in hardening back after its import."""

    # -- 1.499 ---------------------------------------------------------
    def test_1_499_snapshot_restores_attributes_merged_and_frontchannel_logout(self) -> None:
        """
        Scenario 1.499: attributes are merged back and frontchannelLogout restored.
        Given: a client with PKCE, post-logout and back-channel attributes and front-channel
               logout off, and an authorization import that dropped one attribute, changed
               another, added a new one and turned front-channel logout on
        When: lex init snapshots the client before and restores it after
        Then: the snapshot holds attributes and frontchannelLogout (never the secret); the
              restore PUTs once with every snapshotted attribute back, the new attribute kept,
              and frontchannelLogout off again; an unchanged client is not written.
        """
        manager = _sync_manager()
        before = {
            "secret": "never-copied",
            "frontchannelLogout": False,
            "attributes": {
                "pkce.code.challenge.method": "S256",
                "post.logout.redirect.uris": "+",
                "backchannel.logout.url": "https://acme.lex.example/oidc/backchannel-logout",
                "backchannel.logout.session.required": "true",
            },
        }
        manager.kc_manager.admin.get_client.return_value = json.loads(json.dumps(before))
        snapshot = manager._snapshot_client_settings()
        self.assertEqual(snapshot["attributes"], before["attributes"])
        self.assertIs(snapshot["frontchannelLogout"], False)
        self.assertNotIn("secret", snapshot)

        after_import = {
            "secret": "never-copied",
            "frontchannelLogout": True,
            "attributes": {
                "pkce.code.challenge.method": "plain",
                "post.logout.redirect.uris": "+",
                "backchannel.logout.session.required": "true",
                "added.by.import": "kept",
            },
        }
        manager.kc_manager.admin.get_client.return_value = after_import
        manager._restore_client_settings(snapshot)
        manager.kc_manager.admin.update_client.assert_called_once()
        client_id, payload = manager.kc_manager.admin.update_client.call_args.args
        self.assertEqual(client_id, "client-uuid")
        self.assertIs(payload["frontchannelLogout"], False)
        self.assertEqual(payload["attributes"], dict(before["attributes"], **{"added.by.import": "kept"}))

        manager.kc_manager.admin.update_client.reset_mock()
        manager.kc_manager.admin.get_client.return_value = json.loads(json.dumps(before))
        manager._restore_client_settings(snapshot)
        manager.kc_manager.admin.update_client.assert_not_called()


_AUTODETECT = (
    "import json\n"
    "from lex.tools.django_bootstrap import setup_django\n"
    "setup_django()\n"
    "from django.apps import apps\n"
    "from django.conf import settings\n"
    "from django.db.migrations.autodetector import MigrationAutodetector\n"
    "from django.db.migrations.loader import MigrationLoader\n"
    "from django.db.migrations.state import ProjectState\n"
    "loader = MigrationLoader(None, ignore_no_migrations=True)\n"
    "changes = MigrationAutodetector(loader.project_state(), ProjectState.from_apps(apps)).changes(\n"
    "    graph=loader.graph, trim_to_apps={'oauth2_authcodeflow'})\n"
    "print(json.dumps({'auto_field': settings.DEFAULT_AUTO_FIELD, 'planned': [\n"
    "    op.describe() for m in changes.get('oauth2_authcodeflow', []) for op in m.operations]}))\n"
)


class TestCluster01bd_NoLibraryMigrationDrift(SimpleTestCase):
    """1.500: lex init writes no migration into oauth2_authcodeflow."""

    # -- 1.500 ---------------------------------------------------------
    def test_1_500_oauth2_authcodeflow_models_match_their_migrations(self) -> None:
        """
        Scenario 1.500: under lex's BigAutoField default, the library needs no new migration.
        Given: lex's DEFAULT_AUTO_FIELD (BigAutoField) and django-oauth2-codeflow 3.0, whose
               AppConfig pins AutoField -- in a fresh process set up the way `lex` sets itself
               up (the test process's app registry is shaped by the tests that ran before)
        When: Django's migration autodetector compares the library's models with its migrations
        Then: it plans nothing for oauth2_authcodeflow -- so `lex init` (makemigrations for every
              app) no longer writes a migration into site-packages on every customer machine.
        """
        env = dict(os.environ, PYTHONPATH=_CHECKOUT.as_posix())
        result = subprocess.run([sys.executable, "-c", _AUTODETECT], cwd=os.getcwd(), env=env,
                                capture_output=True, text=True, timeout=300)
        self.assertEqual(result.returncode, 0, result.stderr[-3000:])
        report = json.loads(result.stdout.strip().splitlines()[-1])
        self.assertEqual(report["auto_field"], "django.db.models.BigAutoField")
        self.assertEqual(report["planned"], [], "the library's models must match the migrations it ships")
