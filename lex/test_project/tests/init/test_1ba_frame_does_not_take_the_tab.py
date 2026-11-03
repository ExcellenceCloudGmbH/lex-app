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

Cluster 1ba — scenarios 1.423–1.427. Type: U.
Covers: lex/proxy.py (_frame_may_move_the_tab, _frame_breakout_response).
Run: python -m lex pytest lex/test_project/tests/init/test_1ba_frame_does_not_take_the_tab.py -v
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from django.test import SimpleTestCase
from starlette.requests import Request
from starlette.responses import HTMLResponse

import lex.proxy as proxy

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
