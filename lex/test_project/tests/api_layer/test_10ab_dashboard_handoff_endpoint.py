"""The app's half of the Streamlit hand-off: ``GET oidc/handoff``, and what api/auth/* report about it.

Intent: a Streamlit dashboard signs in through the app, inside its own frame. ``oidc/handoff``
answers a signed-in browser with a single-use code (60 s, bound to the sign-in, the audience, the
dashboard origin and the dashboard's ``state``) sent back to the dashboard; a browser without a
sign-in goes back with ``error=login_required`` when it is framed -- Keycloak cannot render in a
frame -- and to the normal sign-in when it is not. It only ever redirects to a dashboard origin the
instance lists. ``api/auth/session`` advertises the hand-off (``dashboard_handoff: ["streamlit"]``)
exactly while ``LEX_STREAMLIT_AUTH`` is ``dual`` or ``handoff``, which is how the frontend chooses
its code path; and in ``handoff`` mode ``api/auth/streamlit-token/`` answers a placeholder, so a
frontend that predates the hand-off still loads its dashboards (through the hand-off).

A regression here is an open redirect on the sign-in path, a code handed to the wrong host, a
Keycloak page in a frame, a dashboard that never learns the user signed in again, or a frontend
that takes a path the server does not serve.

Cluster 10ab -- scenarios 10.167-10.174. Type: E.
Covers: lex/authentication/views/handoff_views.py, lex/authentication/dashboards/handoff.py
        (issue_code, redeem), lex/authentication/dashboards/sites.py, lex/authentication/capabilities.py
        (dashboard_handoff_surfaces), lex/authentication/views/token_views.py (handoff placeholder),
        lex/lex_app/urls.py (oidc/handoff), lex/lex_app/settings.py (exempt patterns).
Run: python -m lex pytest lex/test_project/tests/api_layer/test_10ab_dashboard_handoff_endpoint.py -v
"""

from __future__ import annotations

import json
import os
from datetime import timedelta
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

import pytest
from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings
from django.utils import timezone
from oauth2_authcodeflow.models import OIDCSession
from oauth2_authcodeflow.testing import FakeOP

from lex.authentication.dashboards import handoff
from lex.authentication.models import DashboardHandoffCode
from lex.lex_app.tests.auth import sign_in

pytestmark = pytest.mark.api_layer

APP_HOST = "app.example.com"
DASH = "https://dash.example.com"
HOSTS = json.dumps({"app": {"canonical": APP_HOST}, "streamlit": {"canonical": "dash.example.com"}})
STATE = "n" * 32
FRAME = {"HTTP_SEC_FETCH_MODE": "navigate", "HTTP_SEC_FETCH_DEST": "iframe", "HTTP_ACCEPT": "text/html"}
TOP = {"HTTP_SEC_FETCH_MODE": "navigate", "HTTP_SEC_FETCH_DEST": "document", "HTTP_ACCEPT": "text/html"}
XHR = {"HTTP_SEC_FETCH_MODE": "cors", "HTTP_SEC_FETCH_DEST": "empty", "HTTP_ACCEPT": "application/json"}


class _HandoffEndpointCase(TestCase):
    """The app in LEX_STREAMLIT_AUTH=handoff, answering on its own host."""

    mode = "handoff"

    def setUp(self) -> None:
        super().setUp()
        env = patch.dict(os.environ, {"LEX_STREAMLIT_AUTH": self.mode, "LEX_INSTANCE_HOSTS": HOSTS})
        env.start()
        self.addCleanup(env.stop)
        hosts = override_settings(ALLOWED_HOSTS=[APP_HOST, "testserver"])
        hosts.enable()
        self.addCleanup(hosts.disable)
        self.client = Client()

    def user(self, name: str = "ada"):
        return get_user_model().objects.create_user(username=name, email=f"{name}@example.com")

    def handoff(self, *, ret: str = DASH + "/?model=fund&lex_hs=1", state: str = STATE, aud: str = "streamlit",
                headers=None):
        query = {"aud": aud, "state": state, "return": ret}
        return self.client.get("/oidc/handoff", {k: v for k, v in query.items() if v is not None},
                               HTTP_HOST=APP_HOST, secure=True, **(headers if headers is not None else FRAME))


class TestCluster10ab_SignedIn(_HandoffEndpointCase):
    """10.167: a signed-in browser gets a code, bound to everything that asked for it."""

    # -- 10.167 --------------------------------------------------------
    def test_10_167_a_signed_in_browser_gets_a_bound_single_use_code(self) -> None:
        """
        Scenario 10.167: oidc/handoff answers a sign-in with a code for the asking dashboard.
        Given: a browser signed in through Keycloak, in a dashboard's frame
        When: it requests oidc/handoff?aud=streamlit&return=<dashboard URL>&state=<nonce>
        Then: 302 to <dashboard origin>/auth/handoff?code&state&next=<path of the URL>, no-store,
              no-referrer; the code is valid 60 s and only for this sign-in, `streamlit`, this
              dashboard origin and this state -- presented with any other one, it is refused.
        """
        row = sign_in(self, self.client, self.user())
        response = self.handoff()

        self.assertEqual(response.status_code, 302, response.content[:300])
        target = urlsplit(response["Location"])
        self.assertEqual((f"{target.scheme}://{target.netloc}", target.path), (DASH, "/auth/handoff"))
        query = parse_qs(target.query)
        self.assertEqual(query["state"], [STATE])
        self.assertEqual(query["next"], ["/?model=fund&lex_hs=1"])
        self.assertEqual(response["Cache-Control"], "no-store")
        self.assertEqual(response["Referrer-Policy"], "no-referrer")
        stored = DashboardHandoffCode.objects.get()
        self.assertEqual((stored.session_id, stored.aud, stored.return_origin), (row.pk, "streamlit", DASH))
        self.assertAlmostEqual((stored.expires_at - timezone.now()).total_seconds(), 60, delta=5)

        code = query["code"][0]
        for wrong in ({"aud": "embed", "origin": DASH, "state": STATE},
                      {"aud": "streamlit", "origin": "https://other.example.com", "state": STATE},
                      {"aud": "streamlit", "origin": DASH, "state": "x" * 32}):
            with self.subTest(**wrong):
                second = self.handoff()  # a fresh code for each wrong attempt (a refused code is spent)
                fresh = parse_qs(urlsplit(second["Location"]).query)["code"][0]
                with self.assertRaises(handoff.HandoffError):
                    handoff.redeem(fresh, **wrong)
        self.assertEqual(handoff.redeem(code, aud="streamlit", origin=DASH, state=STATE).binding.session_id, row.pk)


class TestCluster10ab_NotSignedIn(_HandoffEndpointCase):
    """10.168-10.169: no sign-in, or an ended one: never a Keycloak page in a frame."""

    # -- 10.168 --------------------------------------------------------
    def test_10_168_without_a_sign_in_frames_go_back_and_pages_sign_in(self) -> None:
        """
        Scenario 10.168: no sign-in -- the answer depends on where the request comes from.
        Given: an anonymous browser
        When: the hand-off is requested in a frame, at top level, and as a background request
        Then: the frame goes back to the dashboard with error=login_required (Keycloak never in a
              frame); the top-level page goes to oidc/authenticate?next=<this hand-off URL>, so
              the hand-off continues after signing in; a background request gets the contract's
              401 JSON. No code is minted.
        """
        framed = self.handoff()
        self.assertEqual(framed.status_code, 302)
        back = urlsplit(framed["Location"])
        self.assertEqual((f"{back.scheme}://{back.netloc}", back.path), (DASH, "/auth/handoff"))
        self.assertEqual(parse_qs(back.query)["error"], ["login_required"])
        self.assertEqual(parse_qs(back.query)["state"], [STATE])

        top = self.handoff(headers=TOP)
        self.assertEqual(top.status_code, 302)
        login = urlsplit(top["Location"])
        self.assertTrue(login.path.endswith("/authenticate"), top["Location"])
        self.assertTrue(parse_qs(login.query)["next"][0].startswith("/oidc/handoff?"),
                        "after signing in the hand-off must continue")

        background = self.handoff(headers=XHR)
        self.assertEqual(background.status_code, 401)
        self.assertEqual(background.json()["error"], "not_authenticated")
        self.assertFalse(DashboardHandoffCode.objects.exists())

    # -- 10.169 --------------------------------------------------------
    def test_10_169_an_ended_sign_in_is_login_required_in_a_frame_too(self) -> None:
        """
        Scenario 10.169: a sign-in that has ended is answered like none, by the hand-off itself.
        Given: a browser whose sign-in was ended (logout, back-channel logout)
        When: its dashboard frame requests the hand-off
        Then: back to the dashboard with error=login_required -- not the session middleware's
              401 sign-in page in the frame -- and the browser's Django session is signed out.
        """
        row = sign_in(self, self.client, self.user())
        OIDCSession.objects.filter(pk=row.pk).update(ended_at=timezone.now(), end_reason="logout")
        response = self.handoff()
        self.assertEqual(response.status_code, 302)
        self.assertEqual(parse_qs(urlsplit(response["Location"]).query)["error"], ["login_required"])
        self.assertFalse(DashboardHandoffCode.objects.exists())
        self.assertEqual(self.client.get("/api/auth/session", HTTP_HOST=APP_HOST, secure=True).status_code, 401)


class TestCluster10ab_Refusals(_HandoffEndpointCase):
    """10.170-10.171: nothing but listed dashboards; Keycloak down is a 503."""

    # -- 10.170 --------------------------------------------------------
    def test_10_170_the_handoff_only_ever_redirects_to_a_listed_dashboard(self) -> None:
        """
        Scenario 10.170: no open redirect, and no hand-off while the feature is off.
        Given: a signed-in browser
        When: return names an unlisted origin (or no URL), state is missing or malformed, aud is
              not `streamlit`; and when LEX_STREAMLIT_AUTH is legacy
        Then: 400 invalid_request with no Location for each refusal; 404 in legacy mode;
              loopback dashboards are accepted only on an instance that is not deployed.
        """
        sign_in(self, self.client, self.user())
        refusals = {
            "foreign return": dict(ret="https://evil.example.net/"),
            "relative return": dict(ret="/just/a/path"),
            "missing state": dict(state=None),
            "short state": dict(state="abc"),
            "state with symbols": dict(state="<script>" + "a" * 20),
            "another audience": dict(aud="embed"),
        }
        for label, kwargs in refusals.items():
            with self.subTest(refusal=label):
                response = self.handoff(**kwargs)
                self.assertEqual(response.status_code, 400, label)
                self.assertEqual(response.json()["error"], "invalid_request")
                self.assertFalse(response.has_header("Location"))
        self.assertFalse(DashboardHandoffCode.objects.exists())

        self.assertEqual(self.handoff(ret="http://localhost:8501/").status_code, 302, "loopback works locally")
        with patch.dict(os.environ, {"DEPLOYMENT_ENVIRONMENT": "DEV"}):
            self.assertEqual(self.handoff(ret="http://localhost:8501/").status_code, 400, "not when deployed")
        with patch.dict(os.environ, {"LEX_STREAMLIT_AUTH": "legacy"}):
            self.assertEqual(self.handoff().status_code, 404, "the hand-off is off in legacy mode")

    # -- 10.171 --------------------------------------------------------
    def test_10_171_keycloak_unavailable_is_a_503_and_no_code(self) -> None:
        """
        Scenario 10.171: a sign-in that cannot be refreshed right now gets the contract's 503.
        Given: a sign-in whose token has run out, and Keycloak answering 503
        When: the hand-off is requested (as a background request)
        Then: 503 auth_unavailable with Retry-After; no code is minted; the sign-in survives.
        """
        op = FakeOP()
        overrides = override_settings(**op.django_settings())
        overrides.enable()
        self.addCleanup(overrides.disable)
        op.install()
        self.addCleanup(op.uninstall)
        row = sign_in(self, self.client, self.user())
        OIDCSession.objects.filter(pk=row.pk).update(access_expires_at=timezone.now() + timedelta(seconds=2))
        op.script_token(*[(503, "<html>down</html>")] * 3)

        response = self.handoff(headers=XHR)
        self.assertEqual(response.status_code, 503, response.content[:300])
        self.assertEqual(response.json()["error"], "auth_unavailable")
        self.assertTrue(response.has_header("Retry-After"))
        self.assertFalse(DashboardHandoffCode.objects.exists())
        self.assertIsNone(OIDCSession.objects.get(pk=row.pk).ended_at, "an outage never ends a sign-in")


class TestCluster10ab_Advertised(TestCase):
    """10.172-10.174: what api/auth/* say about the hand-off, per mode."""

    def _session(self, mode):
        user = get_user_model().objects.create_user(username=f"u-{mode}", email=f"u-{mode}@example.com")
        client = Client()
        sign_in(self, client, user)
        with patch.dict(os.environ, {"LEX_STREAMLIT_AUTH": mode}):
            return client.get("/api/auth/session").json()["capabilities"]["dashboard_handoff"]

    # -- 10.172 --------------------------------------------------------
    def test_10_172_the_capability_follows_the_flag(self) -> None:
        """
        Scenario 10.172: api/auth/session advertises the hand-off exactly while it is served.
        Given: LEX_STREAMLIT_AUTH legacy, dual, handoff, and a misspelt value
        When: a signed-in frontend reads its capabilities
        Then: legacy -> [], dual and handoff -> ["streamlit"], the typo -> [] (the app never
              crashes on it; it advertises nothing, so every frontend keeps the path that works).
        """
        self.assertEqual(self._session("legacy"), [])
        self.assertEqual(self._session("dual"), ["streamlit"])
        self.assertEqual(self._session("HANDOFF"), ["streamlit"])
        self.assertEqual(self._session("sometimes"), [])

    # -- 10.173 --------------------------------------------------------
    def test_10_173_handoff_mode_hands_old_frontends_a_placeholder(self) -> None:
        """
        Scenario 10.173: api/auth/streamlit-token/ keeps old frontends working without a token.
        Given: LEX_STREAMLIT_AUTH=handoff and a signed-in old frontend
        When: it asks for its dashboard token
        Then: 200 with a placeholder `token` that is not a JWT and carries no credential, a
              renewal an hour away (so it asks rarely) and the `dashboard_handoff` marker,
              no-store; in dual mode it is the sign-in's real token, as before; anonymous is still
              the contract's 401.
        """
        client = Client()
        row = sign_in(self, client, get_user_model().objects.create_user(username="old", email="old@example.com"))

        with patch.dict(os.environ, {"LEX_STREAMLIT_AUTH": "handoff"}):
            placeholder = client.post("/api/auth/streamlit-token/")
        self.assertEqual(placeholder.status_code, 200, placeholder.content[:300])
        body = placeholder.json()
        self.assertEqual(body["token"], "lex-dashboard-handoff")
        self.assertEqual(body["token"].count("."), 0, "the placeholder must not look like a JWT")
        self.assertEqual((body["expires_in"], body["refresh_interval"], body["dashboard_handoff"]), (3600, 3540, True))
        self.assertEqual(placeholder["Cache-Control"], "no-store")
        self.assertNotIn(row.access_token, placeholder.content.decode(), "no credential in the placeholder")

        with patch.dict(os.environ, {"LEX_STREAMLIT_AUTH": "dual"}):
            real = client.post("/api/auth/streamlit-token/")
        self.assertEqual(real.status_code, 200)
        self.assertEqual(real.json()["token"].count("."), 2, "dual mode still hands out the real token")
        with patch.dict(os.environ, {"LEX_STREAMLIT_AUTH": "handoff"}):
            self.assertEqual(Client().post("/api/auth/streamlit-token/").status_code, 401)

    # -- 10.174 --------------------------------------------------------
    def test_10_174_only_the_exact_handoff_path_skips_the_login_requirement(self) -> None:
        """
        Scenario 10.174: oidc/handoff answers anonymous requests itself; look-alikes do not.
        Given: an anonymous background request
        When: it reaches /oidc/handoff (with or without a trailing slash), and look-alike paths
        Then: the hand-off view answers (here: 400, nothing to hand off) instead of the login
              requirement's 401; /oidc/handoffx and /api/oidc/handoff are not exempt (401).
        """
        with patch.dict(os.environ, {"LEX_STREAMLIT_AUTH": "handoff", "LEX_INSTANCE_HOSTS": HOSTS}):
            for path in ("/oidc/handoff", "/oidc/handoff/"):
                with self.subTest(path=path):
                    self.assertEqual(Client().get(path, **XHR).status_code, 400)
            for path in ("/oidc/handoffx", "/api/oidc/handoff"):
                with self.subTest(path=path):
                    response = Client().get(path, **XHR)
                    self.assertEqual(response.status_code, 401, path)
                    self.assertEqual(response.json()["error"], "not_authenticated")
