"""A local run lets the dashboards' sign-in and sign-out send the browser back to them.

Intent: a dashboard hands the browser to lex-app to sign in or out, with ``next=`` pointing back at
the dashboard. lex-app follows ``next`` only to origins it trusts -- its own guard
(``OidcRedirectGuardMiddleware``) and the OIDC library (``OIDC_ALLOWED_REDIRECT_HOSTS``) both
check -- and a deployed instance trusts its dashboards through ``LEX_INSTANCE_HOSTS``. A local run
sets no such hosts, so the dev servers' loopback origins must be trusted instead: the React dev
server and Django always were, the dashboards' were not, and pressing Sign out on a Reflex
dashboard at ``localhost:8502`` ended on lex-app's JSON error "Redirect target not allowed."
A regression is a developer who cannot sign out of a dashboard (or sign in to one) locally; the
opposite regression -- a deployed instance trusting a developer's localhost, or a local run
trusting every loopback port -- is an open redirect.

Cluster 1ca -- scenario 1.745. Type: I.
Covers: lex/lex_app/settings.py (_LOCAL_DEV_PORTS, _local_dev_origins, _cors_allowed_origins,
_redirect_hosts), lex/authentication/middleware.py (OidcRedirectGuardMiddleware, through the views).
Run: python -m lex pytest lex/test_project/tests/init/test_1ca_local_dashboard_redirects.py -v
"""

from __future__ import annotations

import pytest
from django.conf import settings
from django.test import TestCase, override_settings
from oauth2_authcodeflow.testing import FakeOP

from lex.lex_app import settings as lex_settings

pytestmark = pytest.mark.init

#: The dashboards' default local origins: `lex streamlit` and `lex reflex run` (frontend, backend).
_DASHBOARDS = ("http://localhost:8501", "http://localhost:8502", "http://localhost:8503",
               "http://127.0.0.1:8502")


class TestCluster01ca_LocalDashboardRedirects(TestCase):
    """1.745: the dashboards' local origins are valid next= targets; nothing else new is."""

    def _views_with(self, origins: list[str]) -> None:
        """Run the OIDC views with the allowlists derived from ``origins``, as settings derives them."""
        op = FakeOP()
        overrides = override_settings(
            ALLOWED_HOSTS=["localhost", "127.0.0.1", "testserver"],
            CORS_ALLOWED_ORIGINS=origins,
            OIDC_ALLOWED_REDIRECT_HOSTS=lex_settings._redirect_hosts(origins),
            OIDC_RP_CLIENT_ID="lex-local",
            OIDC_RP_CLIENT_SECRET="the-secret",
            **op.django_settings(),
        )
        overrides.enable()
        self.addCleanup(overrides.disable)
        op.install()
        self.addCleanup(op.uninstall)

    # -- 1.745 ---------------------------------------------------------
    def test_1_745_a_local_run_trusts_the_dashboards_dev_servers_and_no_more(self) -> None:
        """
        Scenario 1.745: sign-out from a local Reflex dashboard returns to it.
        Given: a local run (no DEPLOYMENT_ENVIRONMENT, no LEX_INSTANCE_HOSTS), one with
               REFLEX_FRONTEND_PORT moved to 9100, and a deployed pod
        When: the allowlists are derived, and lex-app's total_logout and authenticate views are
              asked to return to the dashboard (the Sign out and Sign in of a dashboard)
        Then: locally, the Streamlit (8501) and Reflex (8502, 8503, and a moved port) origins are
              trusted on localhost and 127.0.0.1 -- the views redirect instead of answering
              REDIRECT_NOT_ALLOWED -- while another loopback port and a foreign site are still
              refused with 400; a deployed pod trusts no localhost at all; this process (a
              local run) carries the dashboards' origins in its real settings.
        """
        local = lex_settings._cors_allowed_origins(environ={}, deployed=False)
        for origin in _DASHBOARDS + ("http://localhost:3000", "http://localhost:8000"):
            self.assertIn(origin, local, f"a local run must trust {origin}")
        self.assertNotIn("http://localhost:9999", local, "only the dev servers' ports, not every loopback port")
        moved = lex_settings._cors_allowed_origins(environ={"REFLEX_FRONTEND_PORT": "9100"}, deployed=False)
        self.assertIn("http://localhost:9100", moved, "a moved Reflex port is trusted too")
        self.assertEqual(len(moved), len(set(moved)), "each origin once")
        deployed = lex_settings._cors_allowed_origins(
            environ={"DOMAIN_HOSTED": "acme.lex.example", "REFLEX_FRONTEND_PORT": "9100"}, deployed=True)
        self.assertFalse([o for o in deployed if "localhost" in o or "127.0.0.1" in o],
                         "a deployed instance must not trust a developer's localhost")
        self.assertIn("http://localhost:8502", settings.CORS_ALLOWED_ORIGINS, "this local run's real settings")
        self.assertIn("localhost:8502", settings.OIDC_ALLOWED_REDIRECT_HOSTS)

        self._views_with(local)
        for target in ("http://localhost:8502/", "http://127.0.0.1:8502/", "http://localhost:8503/", "http://localhost:8501/x"):
            with self.subTest(view="total_logout", target=target):
                response = self.client.get("/oidc/total_logout", {"next": target}, HTTP_HOST="localhost:8000")
                self.assertIn(response.status_code, (302, 303), response.content[:300])
                self.assertNotIn(b"REDIRECT_NOT_ALLOWED", response.content)
        signed_in = self.client.get("/oidc/authenticate", {"next": "http://localhost:8502/who"}, HTTP_HOST="localhost:8000")
        self.assertEqual(signed_in.status_code, 302, signed_in.content[:300])

        for target in ("http://localhost:9999/", "https://evil.example/steal"):
            with self.subTest(view="total_logout", refused=target):
                refused = self.client.get("/oidc/total_logout", {"next": target}, HTTP_HOST="localhost:8000")
                self.assertEqual(refused.status_code, 400)
                self.assertIn(b"REDIRECT_NOT_ALLOWED", refused.content)
