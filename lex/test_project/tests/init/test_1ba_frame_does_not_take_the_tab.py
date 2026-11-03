"""A frame another application put on screen does not get to move that tab.

Intent
------
When the Streamlit auth proxy has no valid identity for a *framed* document it
answers with the breakout page rather than redirecting the frame to Keycloak,
which forbids being framed. The page carries three layers: an automatic
top-level navigation, a ``lex-auth-required`` ``postMessage``, and a
``target="_top"`` link.

The automatic layer is the one that needs an owner. lex-app's React shell frames
the dashboard host from the instance hostname beside it; the top window is the
shell's page, and the user is reading it. A frame that cannot authenticate has
learned something about itself, not about that page, and moving the window on
its own behalf is how a sidebar click inside an instance ended up parked on
``https://<instance>-streamlit.<domain>/``: the frame 401'd, the page moved the
tab to the dashboard host's login, and a live SSO session completed the round
trip before anyone could read it.

So the automatic navigation belongs to the frame only when this origin is also
the one framing it. ``Sec-Fetch-Site`` is what says so, and it is present on
every request that can reach here at all -- the breakout is gated on
``Sec-Fetch-Dest``. Withheld, nothing else changes: the response is the same
401, it still posts ``lex-auth-required`` (the channel the shell listens on and
recovers the dashboard through), and it still offers the link, which is a user
gesture and always permitted. The frame is never redirected to the IdP, so the
"refused to connect" wall the breakout exists for cannot come back either.

A client that sends no ``Sec-Fetch-Site`` keeps the pre-existing behaviour, the
same safe default scenario 1.214 pins for the rest of this routing.

Cluster 1ba — scenarios 1.423–1.429. Type: U.
Covers: lex/proxy.py (_frame_may_move_the_tab, _frame_breakout_response, proxy),
lex/lex_app/streamlit/Streamlit.py (get_html) at the join.
Run: python -m lex pytest lex/test_project/tests/init/test_1ba_frame_does_not_take_the_tab.py -v
"""

from __future__ import annotations

import os
import re
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import urlparse

import httpx
import pytest
from django.test import SimpleTestCase
from starlette.requests import Request
from starlette.responses import HTMLResponse
from starlette.testclient import TestClient

import lex.proxy as proxy
from lex.lex_app.streamlit.Streamlit import Streamlit

pytestmark = pytest.mark.init

#: The script the breakout page runs to move the window the frame sits in.
TAKEOVER = "window.top.location"


def _framed_request(site: str | None, path: str = "/", query: str = "") -> Request:
    """An unauthenticated iframe document load, optionally naming who framed it."""
    headers = [
        (b"host", b"dash.example.com"),
        (b"accept", b"text/html"),
        (b"sec-fetch-dest", b"iframe"),
    ]
    if site is not None:
        headers.append((b"sec-fetch-site", site.encode()))
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": path,
            "query_string": query.encode(),
            "scheme": "https",
            "server": ("dash.example.com", 443),
            "headers": headers,
        }
    )


class TestCluster01ba_FrameDoesNotTakeTheTab(SimpleTestCase):
    """Cluster 1ba: who the breakout page is allowed to navigate."""

    def setUp(self) -> None:
        # Derive the login URL from the request rather than PUBLIC_URL, so the
        # expected absolute URL is deterministic (as cluster 1z does).
        patcher = patch.object(proxy, "PUBLIC_URL", "")
        patcher.start()
        self.addCleanup(patcher.stop)

    def _breakout(self, site: str | None, **kwargs) -> str:
        resp = proxy._unauthenticated_response(_framed_request(site, **kwargs))
        self.assertIsInstance(resp, HTMLResponse, msg="a framed 401 is still the breakout page")
        self.assertEqual(resp.status_code, 401, msg="the breakout is a 401")
        return resp.body.decode()

    # -- 1.423 ---------------------------------------------------------
    def test_1_423_the_shell_s_frame_cannot_move_the_shell_s_tab(self) -> None:
        """
        Scenario 1.423: the dashboard framed by lex-app's own shell leaves the tab alone.
        Given: an unauthenticated iframe document load framed from the instance
               hostname beside this one (Sec-Fetch-Site: same-site)
        When:  the proxy builds the unauthenticated response
        Then:  the page runs no top-level navigation -- the window belongs to the
               page the user is reading -- while still posting lex-auth-required
               for the shell to recover through and still offering the _top link.
        """
        body = self._breakout("same-site", query="embed=true")

        self.assertNotIn(
            TAKEOVER, body,
            msg="a frame on someone else's page must not move that page's window: this is how "
                "a sidebar click inside the instance ended up on the dashboard host",
        )
        self.assertIn(
            "lex-auth-required", body,
            msg="the shell's recovery channel must survive -- without it an expired dashboard "
                "has no way back at all",
        )
        self.assertIn(
            'target="_top"', body,
            msg="the user must keep a way out they can take themselves",
        )
        self.assertIn(
            "https://dash.example.com/auth/login", body,
            msg="and it must point at this proxy's login",
        )

    # -- 1.424 ---------------------------------------------------------
    def test_1_424_an_unrelated_site_framing_this_cannot_move_its_tab_either(self) -> None:
        """
        Scenario 1.424: the rule is about ownership, not about which host it is.
        Given: an unauthenticated iframe document load framed from an unrelated site
               (Sec-Fetch-Site: cross-site)
        When:  the proxy builds the unauthenticated response
        Then:  no top-level navigation, and the message and link are still offered --
               an embedder this proxy has never heard of has even less claim to the tab
               than lex-app's own shell.
        """
        body = self._breakout("cross-site")

        self.assertNotIn(TAKEOVER, body, msg="an unrelated embedder's tab is not this frame's to move")
        self.assertIn("lex-auth-required", body)
        self.assertIn('target="_top"', body)

    # -- 1.425 ---------------------------------------------------------
    def test_1_425_a_frame_this_origin_put_there_still_navigates(self) -> None:
        """
        Scenario 1.425: the proxy framing its own page keeps the automatic navigation.
        Given: an unauthenticated iframe document load framed by this same origin
               (Sec-Fetch-Site: same-origin)
        When:  the proxy builds the unauthenticated response
        Then:  the page still navigates the top window: the only window it could move
               is one this response already owns, so withholding it here would strand
               the dashboard's own recovery for nothing.
        """
        body = self._breakout("same-origin")

        self.assertIn(
            TAKEOVER, body,
            msg="this origin's own frame must keep recovering automatically",
        )
        self.assertIn("lex-auth-required", body)

    # -- 1.426 ---------------------------------------------------------
    def test_1_426_a_client_that_names_no_embedder_keeps_the_old_behaviour(self) -> None:
        """
        Scenario 1.426: an absent Sec-Fetch-Site is the pre-existing behaviour, as 1.214 has it.
        Given: a framed document load from a client that sends no Sec-Fetch-Site
        When:  the proxy builds the unauthenticated response
        Then:  the automatic navigation is still there -- a client too old or too
               synthetic to name its embedder is not the browser tab this guard is
               about, and the rest of this routing already treats an absent
               Sec-Fetch-* as "change nothing".
        """
        body = self._breakout(None)

        self.assertIn(
            TAKEOVER, body,
            msg="absent Sec-Fetch-* must keep the pre-existing behaviour (cf. scenario 1.214)",
        )

    # -- 1.427 ---------------------------------------------------------
    def test_1_427_the_detector_reads_the_embedder_case_insensitively(self) -> None:
        """
        Scenario 1.427: the embedder check is a header read, so casing cannot defeat it.
        Given: Sec-Fetch-Site in mixed case, and the two values that keep the navigation
        When:  _frame_may_move_the_tab inspects them
        Then:  SAME-SITE and Cross-Site are both refused; same-origin and an absent
               header are both allowed.
        """
        self.assertFalse(proxy._frame_may_move_the_tab(_framed_request("SAME-SITE")))
        self.assertFalse(proxy._frame_may_move_the_tab(_framed_request("Cross-Site")))
        self.assertTrue(proxy._frame_may_move_the_tab(_framed_request("same-origin")))
        self.assertTrue(proxy._frame_may_move_the_tab(_framed_request(None)))


class _Upstream:
    """Stands in for Streamlit at the one seam every proxied request leaves through."""

    calls: list = []

    @classmethod
    def reset(cls) -> None:
        cls.calls = []

    @classmethod
    async def send(cls, method, url, *, content=None, headers=None):
        cls.calls.append(str(url))
        return httpx.Response(200, content=b"<html>the dashboard</html>")


class TestCluster01ba_TheReportedJourney(SimpleTestCase):
    """Cluster 1ba: the URL the sidebar report builds, loaded against the real proxy.

    The two halves of this fix live in different processes -- the report is
    Django, the frame's destination is the Starlette proxy -- so each cluster
    pins its own. This class is the join: the URL the real ``Streamlit`` report
    produces is what the browser actually asks the real proxy for, and what the
    customer saw is the answer to that one request.
    """

    def setUp(self) -> None:
        patcher = patch.object(proxy, "PUBLIC_URL", "")
        patcher.start()
        self.addCleanup(patcher.stop)
        _Upstream.reset()

    #: An iframe document load from the shell, which lives on the instance
    #: hostname beside the dashboard host.
    FRAMED = {
        "Accept": "text/html",
        "Sec-Fetch-Dest": "iframe",
        "Sec-Fetch-Site": "same-site",
    }

    @classmethod
    def _framed_fetch_of(cls, report_html: str, client) -> "httpx.Response":
        """Load the report's frame the way a browser embedded in the shell would."""
        src = urlparse(re.findall(r'src="([^"]+)"', report_html)[0])
        return client.get(
            f"{src.path}?{src.query}" if src.query else src.path,
            headers=cls.FRAMED,
            follow_redirects=False,
        )

    # -- 1.428 ---------------------------------------------------------
    def test_1_428_the_frame_the_report_builds_is_let_in(self) -> None:
        """
        Scenario 1.428: the dashboard the sidebar entry frames actually loads.
        Given: the URL the real Streamlit report builds for a session holding an
               access token, and a proxy that accepts that token
        When:  a browser loads it as an iframe document, from the shell's origin
        Then:  the proxy banks the token, strips it from the address and serves the
               dashboard from upstream. No 401, so nothing that could move the tab:
               this is the expected behaviour in the report -- Streamlit embedded
               inside the instance, at the instance's own address.
        """
        claims = {"sub": "u1", "email": "u@example.com", "exp": 4_000_000_000}
        session = SimpleNamespace(session={"oidc_access_token": "bootstrap-jwt"})

        with patch.dict(os.environ, {"STREAMLIT_URL": "https://dash.example.com"}):
            html = Streamlit().get_html(None, session)

        with patch.object(proxy, "validate_jwt_token", lambda _t: claims), \
             patch.object(proxy, "_upstream_send", _Upstream.send):
            with TestClient(proxy.app) as client:
                resp = self._framed_fetch_of(html, client)
                self.assertNotEqual(
                    resp.status_code, 401,
                    msg="the frame the sidebar entry builds must authenticate, not 401 -- a "
                        "framed 401 is what started the reported tab takeover",
                )
                self.assertNotIn(
                    "lex-auth-required", resp.text,
                    msg="and so must never be answered with the breakout page at all",
                )
                # The handshake: the proxy exchanges the bootstrap token for its
                # own session and sends the frame back to the same place without
                # it, so the credential never stays in the address.
                while resp.status_code in (301, 302, 303, 307, 308):
                    self.assertNotIn(
                        "auth_token", resp.headers["location"],
                        msg="the banked token must be gone from where the frame is sent next",
                    )
                    resp = client.get(
                        resp.headers["location"], headers=self.FRAMED, follow_redirects=False
                    )

        self.assertEqual(
            resp.status_code, 200,
            msg=f"the frame must end on the dashboard, not on {resp.status_code}",
        )
        self.assertIn(
            "the dashboard", resp.text,
            msg="and the bytes it renders must be the ones the dashboard served",
        )
        self.assertTrue(_Upstream.calls, msg="which means the request reached upstream at all")

    # -- 1.429 ---------------------------------------------------------
    def test_1_429_without_a_token_the_tab_is_still_not_taken(self) -> None:
        """
        Scenario 1.429: the fault and the amplifier, observed together.
        Given: the URL the report builds when the session has no access token --
               what every deployment before this fix sent to the browser
        When:  a browser loads it as an iframe document, from the shell's origin
        Then:  the proxy still cannot authenticate it, so the frame shows the
               "sign in again" page -- but nothing in that page moves the top
               window, so the tab stays on the instance's own /streamlit page
               instead of ending up on the dashboard host.
        """
        with patch.dict(os.environ, {"STREAMLIT_URL": "https://dash.example.com"}):
            html = Streamlit().get_html(None, None)

        self.assertNotIn("auth_token", html, msg="no session token means nothing to frame it with")

        with patch.object(proxy, "_upstream_send", _Upstream.send):
            with TestClient(proxy.app) as client:
                resp = self._framed_fetch_of(html, client)

        self.assertEqual(resp.status_code, 401, msg="an unauthenticated frame is still a 401")
        self.assertNotIn(
            TAKEOVER, resp.text,
            msg="this is the reported fault: the 401 page moved the whole browser tab to the "
                "dashboard host's login, and a live SSO session completed the round trip",
        )
        self.assertIn('target="_top"', resp.text, msg="the user keeps a way out they choose")
