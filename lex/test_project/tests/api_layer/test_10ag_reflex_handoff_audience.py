"""The app's half of the Reflex hand-off: ``GET oidc/handoff?aud=reflex``.

Intent: a Reflex dashboard signs in exactly as a Streamlit dashboard in hand-off mode does --
through the app, inside its own frame, never through Keycloak directly -- with its own audience,
``reflex``. The app mints a single-use code for the browser's sign-in, bound to ``reflex`` and to
the Reflex origin that asked; it only ever sends that code to a host the instance lists for its
Reflex dashboards, and a code minted for one surface never redeems on the other. Unlike
Streamlit's, the Reflex audience does not wait for ``LEX_STREAMLIT_AUTH``: it is the only way a
Reflex dashboard signs in. ``api/auth/session`` lists ``reflex`` among the hand-off surfaces
while the instance serves Reflex dashboards.

A regression here is a Reflex dashboard that cannot sign in at all, a code handed to a host the
instance never named (an open redirect on the sign-in path), or a Streamlit code accepted by a
Reflex dashboard -- one surface's sign-in passing for the other's.

Cluster 10ag -- scenarios 10.194-10.197. Type: E (10.197: I).
Covers: lex/authentication/views/handoff_views.py, lex/authentication/dashboards/handoff.py
        (AUD_REFLEX, issue_code, redeem, resolve), lex/authentication/dashboards/sites.py
        (reflex_origins, is_reflex_origin), lex/authentication/capabilities.py
        (dashboard_handoff_surfaces).
Run: python -m lex pytest lex/test_project/tests/api_layer/test_10ag_reflex_handoff_audience.py -v
"""

from __future__ import annotations

import json
import os
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

import pytest
from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings
from django.utils import timezone

from lex.authentication.dashboards import handoff, sites
from lex.authentication.models import DashboardBinding, DashboardHandoffCode
from lex.lex_app.tests.auth import sign_in

pytestmark = pytest.mark.api_layer

APP_HOST = "app.example.com"
REFLEX = "https://reflex.example.com"
STREAMLIT = "https://dash.example.com"
HOSTS = json.dumps({
    "app": {"canonical": APP_HOST},
    "streamlit": {"canonical": "dash.example.com"},
    "reflex": {"canonical": "reflex.example.com"},
})
STATE = "r" * 32
FRAME = {"HTTP_SEC_FETCH_MODE": "navigate", "HTTP_SEC_FETCH_DEST": "iframe", "HTTP_ACCEPT": "text/html"}
TOP = {"HTTP_SEC_FETCH_MODE": "navigate", "HTTP_SEC_FETCH_DEST": "document", "HTTP_ACCEPT": "text/html"}


class _ReflexHandoffCase(TestCase):
    """The app answering on its own host, with Streamlit's hand-off flag in ``legacy`` (off)."""

    streamlit_mode = "legacy"

    def setUp(self) -> None:
        super().setUp()
        env = patch.dict(os.environ, {"LEX_STREAMLIT_AUTH": self.streamlit_mode, "LEX_INSTANCE_HOSTS": HOSTS,
                                      "DEPLOYMENT_ENVIRONMENT": "test"})
        env.start()
        self.addCleanup(env.stop)
        hosts = override_settings(ALLOWED_HOSTS=[APP_HOST, "testserver"])
        hosts.enable()
        self.addCleanup(hosts.disable)
        self.client = Client()

    def user(self, name: str = "ada"):
        return get_user_model().objects.create_user(username=name, email=f"{name}@example.com")

    def handoff(self, *, ret: str = REFLEX + "/records?page=2", aud: str = "reflex", headers=None):
        return self.client.get("/oidc/handoff", {"aud": aud, "state": STATE, "return": ret},
                               HTTP_HOST=APP_HOST, secure=True, **(headers if headers is not None else FRAME))


class TestCluster10ag_SignedIn(_ReflexHandoffCase):
    """10.194: a signed-in browser gets a code for its Reflex dashboard, whatever Streamlit's flag says."""

    # -- 10.194 --------------------------------------------------------
    def test_10_194_a_reflex_dashboard_gets_a_code_bound_to_reflex(self) -> None:
        """
        Scenario 10.194: oidc/handoff?aud=reflex answers a sign-in with a code for that dashboard.
        Given: a browser signed in through Keycloak, in a Reflex dashboard's frame, with
               LEX_STREAMLIT_AUTH=legacy (Streamlit's hand-off off)
        When: it requests oidc/handoff?aud=reflex&return=<Reflex URL>&state=<nonce>
        Then: 302 to <Reflex origin>/auth/handoff?code&state&next=<path>, no-store; the stored
              code is for `reflex` and that origin, valid 60 s; it redeems into a `reflex` binding
              that lives as long as the sign-in -- and is refused as `streamlit` or for the
              Streamlit host.
        """
        row = sign_in(self, self.client, self.user())
        response = self.handoff()

        self.assertEqual(response.status_code, 302, response.content[:300])
        target = urlsplit(response["Location"])
        self.assertEqual((f"{target.scheme}://{target.netloc}", target.path), (REFLEX, "/auth/handoff"))
        query = parse_qs(target.query)
        self.assertEqual((query["state"], query["next"]), ([STATE], ["/records?page=2"]))
        self.assertEqual(response["Cache-Control"], "no-store")
        stored = DashboardHandoffCode.objects.get()
        self.assertEqual((stored.session_id, stored.aud, stored.return_origin), (row.pk, "reflex", REFLEX))
        self.assertAlmostEqual((stored.expires_at - timezone.now()).total_seconds(), 60, delta=5)

        for wrong in ({"aud": "streamlit", "origin": REFLEX, "state": STATE},
                      {"aud": "reflex", "origin": STREAMLIT, "state": STATE}):
            with self.subTest(**wrong):
                fresh = parse_qs(urlsplit(self.handoff()["Location"]).query)["code"][0]
                with self.assertRaises(handoff.HandoffError):
                    handoff.redeem(fresh, **wrong)
        redeemed = handoff.redeem(query["code"][0], aud="reflex", origin=REFLEX, state=STATE)
        self.assertEqual((redeemed.binding.aud, redeemed.binding.expires_at), ("reflex", None),
                         "a dashboard binding lives as long as its sign-in")
        self.assertEqual(handoff.resolve(redeemed.handle, aud="reflex").pk, redeemed.binding.pk)
        self.assertIsNone(handoff.resolve(redeemed.handle, aud="streamlit"),
                          "a Reflex handle never passes for a Streamlit one")


class TestCluster10ag_Origins(_ReflexHandoffCase):
    """10.195: each audience goes only to its own listed hosts."""

    # -- 10.195 --------------------------------------------------------
    def test_10_195_each_audience_goes_only_to_its_own_hosts(self) -> None:
        """
        Scenario 10.195: the return origin is checked against the audience's own hosts.
        Given: an instance listing a Streamlit host and a Reflex host; a deployed instance
               (no loopback exception); Streamlit's hand-off on (handoff mode) for part of it
        When: oidc/handoff is asked to return a reflex code to the Streamlit host, a streamlit
              code to the Reflex host, a code to an unlisted host, or for an unknown audience
        Then: each is a 400 with no code minted; with Streamlit's hand-off off, `streamlit` is
              still a 404 while `reflex` is served; sites lists the Reflex origins, and accepts
              loopback ones only when the instance is not deployed.
        """
        sign_in(self, self.client, self.user())
        self.assertEqual(self.handoff(aud="streamlit", ret=STREAMLIT + "/").status_code, 404,
                         "Streamlit's hand-off stays behind its own flag")
        self.assertEqual(self.handoff(aud="widgets", ret=REFLEX + "/").status_code, 400)
        self.assertEqual(self.handoff(ret=STREAMLIT + "/").status_code, 400, "a reflex code never goes to Streamlit")
        self.assertEqual(self.handoff(ret="https://evil.example.org/").status_code, 400)
        with patch.dict(os.environ, {"LEX_STREAMLIT_AUTH": "handoff"}):
            self.assertEqual(self.handoff(aud="streamlit", ret=REFLEX + "/").status_code, 400,
                             "a streamlit code never goes to Reflex")
            self.assertEqual(self.handoff(aud="streamlit", ret=STREAMLIT + "/").status_code, 302)
        self.assertEqual(DashboardHandoffCode.objects.filter(aud="reflex").count(), 0)
        self.assertEqual(sites.reflex_origins(), (REFLEX,))
        self.assertFalse(sites.is_reflex_origin("http://localhost:8503"), "deployed: no loopback")
        with patch.dict(os.environ):
            del os.environ["DEPLOYMENT_ENVIRONMENT"]  # set at all, even empty, means deployed
            self.assertTrue(sites.is_reflex_origin("http://localhost:8503"), "development: any loopback port")
            self.assertFalse(sites.is_reflex_origin("http://evil.example.org"))


class TestCluster10ag_NotSignedIn(_ReflexHandoffCase):
    """10.196: without a sign-in, a frame goes back to the dashboard and a page signs in."""

    # -- 10.196 --------------------------------------------------------
    def test_10_196_without_a_sign_in_frames_go_back_and_pages_sign_in(self) -> None:
        """
        Scenario 10.196: no sign-in in the browser.
        Given: a browser with no lex-app sign-in
        When: a Reflex dashboard's frame asks oidc/handoff; then a top-level page does
        Then: the frame goes back to <Reflex origin>/auth/handoff?error=login_required with its
              state and path (Keycloak never renders in a frame); the page goes to lex-app's
              sign-in, which comes back to this very hand-off; no code is minted.
        """
        framed = self.handoff()
        self.assertEqual(framed.status_code, 302)
        target = urlsplit(framed["Location"])
        self.assertEqual((f"{target.scheme}://{target.netloc}", target.path), (REFLEX, "/auth/handoff"))
        self.assertEqual(parse_qs(target.query), {"error": ["login_required"], "state": [STATE],
                                                  "next": ["/records?page=2"]})

        page = self.handoff(headers=TOP)
        self.assertEqual(page.status_code, 302)
        login = urlsplit(page["Location"])
        self.assertNotEqual(f"{login.scheme}://{login.netloc}", REFLEX)
        self.assertIn("/oidc/handoff", parse_qs(login.query).get("next", [""])[0] or login.query)
        self.assertFalse(DashboardHandoffCode.objects.exists())


class TestCluster10ag_Advertised(TestCase):
    """10.197: api/auth/session lists the Reflex hand-off while the instance serves Reflex."""

    # -- 10.197 --------------------------------------------------------
    def test_10_197_the_capability_lists_reflex_while_reflex_is_served(self) -> None:
        """
        Scenario 10.197: the frontend learns which dashboards sign in through the hand-off.
        Given: a signed-in frontend; Reflex on or off; Streamlit's hand-off on or off
        When: it reads api/auth/session
        Then: `reflex` is listed exactly while IS_REFLEX_ENABLED is true, after `streamlit`
              when both are; and no binding exists until a code is redeemed.
        """
        user = get_user_model().objects.create_user(username="cap", email="cap@example.com")
        client = Client()
        sign_in(self, client, user)

        def surfaces(**env):
            with patch.dict(os.environ, env):
                return client.get("/api/auth/session").json()["capabilities"]["dashboard_handoff"]

        self.assertEqual(surfaces(LEX_STREAMLIT_AUTH="legacy", IS_REFLEX_ENABLED="false"), [])
        self.assertEqual(surfaces(LEX_STREAMLIT_AUTH="legacy", IS_REFLEX_ENABLED="true"), ["reflex"])
        self.assertEqual(surfaces(LEX_STREAMLIT_AUTH="handoff", IS_REFLEX_ENABLED="true"), ["streamlit", "reflex"])
        self.assertFalse(DashboardBinding.objects.exists())
