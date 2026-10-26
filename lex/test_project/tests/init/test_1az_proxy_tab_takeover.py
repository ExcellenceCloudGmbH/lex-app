"""A machine endpoint on the dashboard origin must never take over the user's tab.

Intent: the Streamlit proxy serves two kinds of thing on one host. Pages a
person looks at -- the dashboard itself -- and endpoints that exist only for
code: ``/_lex/theme-relay`` is framed invisibly at zero size, ``/auth/token``
and ``/auth/adopt`` are ``fetch`` targets, ``/_stcore/*`` is Streamlit's own
bootstrap. The proxy's re-auth machinery was written for the first kind and
applied to both, and the difference matters because that machinery's whole
purpose is to move the TOP window: a framed document that cannot authenticate
is answered with a page whose first statement is ``window.top.location =
<login>``, and the OIDC round trip then returns the browser to whatever path
had 401'd.

For a dashboard that is the right trade -- the user loses the page they were
on and gets back the one they wanted. For a hidden shim it is indefensible,
and it reached a customer as LEX-1477: a frontend whose proxy did not yet
serve ``/_lex/theme-relay`` had its hidden relay frame fall into the
authenticated catch-all, and clicking a model with an embedded dashboard
threw the whole browser tab onto ``https://<instance>-streamlit.../_lex/
theme-relay`` -- a blank storage document with no UI and no way back.

That route exists now, so that particular 401 is gone. These scenarios are
about the consequence rather than the one cause, because the cause is
replaceable: any utility path that 401s, in any future version, must not be
able to move a browser, and no OIDC round trip may ever land one on a
document that has nothing to show. The third guard is the recovery: a tab
that gets there anyway -- a bookmark, a history entry, an older proxy still in
the chain -- is sent back to the frontend rather than left staring at a blank
page.

A regression here is quiet in the worst way. Nothing errors; the user simply
finds themselves on another site, intermittently, with no idea what did it.

Cluster 1az -- scenarios 1.415-1.422. Type: U.
Covers: lex/proxy.py (_is_landing_path, _safe_next_path, _unauthenticated_response,
theme_relay, _is_top_level_document_request).
Run: python -m lex pytest lex/test_project/tests/init/test_1az_proxy_tab_takeover.py -v
"""

from __future__ import annotations

import asyncio
from unittest.mock import patch

import pytest
from django.test import SimpleTestCase
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse

import lex.proxy as proxy

pytestmark = pytest.mark.init


#: Paths that exist for code, not for a person. The relay is the one the
#: customer actually landed on; the rest reach the browser the same way and
#: would strand it just as completely.
UTILITY_PATHS = (
    "/_lex/theme-relay",
    "/_stcore/health",
    "/_stcore/host-config",
    "/auth/token",
    "/auth/adopt",
)

#: Paths a person can be looking at, and must still be returned to.
PAGE_PATHS = ("/", "/07-tax/2025", "/07-tax/2025?model=Period&pk=3#top")


def _request(path: str, headers: dict[str, str], query: str = "") -> Request:
    """A browser-like GET for *path*, carrying *headers*."""
    raw = [(b"host", b"dash.example.com")]
    for key, value in headers.items():
        raw.append((key.lower().encode(), value.encode()))
    return Request({
        "type": "http",
        "method": "GET",
        "path": path,
        "query_string": query.encode(),
        "scheme": "https",
        "server": ("dash.example.com", 443),
        "headers": raw,
    })


async def _noop_put_tokens(*_args, **_kwargs) -> None:
    return None


class TestCluster01az_WhereALoginMayReturnTo(SimpleTestCase):
    """Cluster 1az: the OIDC round trip can only land on a page."""

    # -- 1.415 ---------------------------------------------------------
    def test_1_415_a_utility_path_is_never_a_return_target(self) -> None:
        """
        Scenario 1.415: a same-origin path that has no UI is refused as a return target.
        Given: each of the proxy's machine endpoints offered as a `next` value
        When: the return-path guard validates it
        Then: every one is rejected, while real pages -- including query and fragment --
              survive untouched.

        Same-origin was the whole test before this, and `/_lex/theme-relay` is
        as same-origin as a path gets. It is also a zero-sized storage shim, so
        honouring it ends the login flow on a blank document. Origin says the
        target is ours; it does not say a person can use it.
        """
        for path in UTILITY_PATHS:
            with self.subTest(next=path):
                self.assertIsNone(
                    proxy._safe_next_path(path),
                    msg=f"{path!r} has no UI -- returning a browser there strands it",
                )
        # Query strings must not smuggle one past the check either.
        self.assertIsNone(
            proxy._safe_next_path("/_lex/theme-relay?mode=dark"),
            msg="the path is what decides, so a query must not change the verdict",
        )

        for path in PAGE_PATHS:
            with self.subTest(next=path):
                self.assertEqual(
                    proxy._safe_next_path(path), path,
                    msg=f"{path!r} is a page the user can be looking at and must be preserved",
                )

    # -- 1.416 ---------------------------------------------------------
    def test_1_416_a_stashed_utility_path_lands_on_the_app_root(self) -> None:
        """
        Scenario 1.416: the callback re-validates the stashed path and falls back to `/`.
        Given: a session whose stashed return path is the theme relay
        When: `auth_callback` completes the OIDC round trip
        Then: it redirects to `/`, not to the relay.

        This is the step the customer actually saw. The breakout aimed the top
        window at `/auth/login?next=/_lex/theme-relay`; Keycloak had a live SSO
        session, so it came straight back, and the callback dutifully parked
        the tab on the relay. Validating on entry is not enough on its own --
        a value can be stashed by an older build or by a frame that had no
        business asking -- so the exit is checked too.
        """
        asyncio.run(self._assert_callback_returns_to("/_lex/theme-relay", "/"))

    async def _assert_callback_returns_to(self, stashed: str, expected: str) -> None:
        request = Request({
            "type": "http", "method": "GET", "path": "/auth/callback",
            "query_string": b"", "headers": [], "scheme": "https",
            "server": ("dash.example.com", 443),
            "session": {proxy._NEXT_SESSION_KEY: stashed},
        })

        async def _token(_request):
            return {"access_token": "at", "id_token": "it",
                    "userinfo": {"email": "u@example.com"}}

        with patch.object(proxy.oauth, "oidc") as oidc, \
             patch.object(proxy, "_put_tokens", _noop_put_tokens):
            oidc.authorize_access_token = _token
            resp = await proxy.auth_callback(request)

        self.assertEqual(
            resp.headers["location"], expected,
            msg=f"a stashed {stashed!r} must land on {expected!r}, "
                f"got {resp.headers['location']!r}",
        )

    # -- 1.417 ---------------------------------------------------------
    def test_1_417_a_top_level_visit_to_a_utility_path_carries_no_return_target(self) -> None:
        """
        Scenario 1.417: a top-level navigation to a utility path redirects to a bare login.
        Given: an unauthenticated top-level document load of `/auth/token`
        When: the proxy builds the unauthenticated response
        Then: it redirects to `/auth/login` with no `next`, so the callback cannot be
              aimed back at the endpoint that has nothing to show.
        """
        resp = proxy._unauthenticated_response(
            _request("/auth/token", {"accept": "text/html", "sec-fetch-dest": "document"})
        )

        self.assertIsInstance(resp, RedirectResponse, msg="a top-level load still redirects to login")
        self.assertEqual(
            resp.headers["location"], "/auth/login",
            msg="a utility path must not be smuggled into the login flow as ?next=",
        )


class TestCluster01az_WhoMayMoveTheTopWindow(SimpleTestCase):
    """Cluster 1az: only a framed *page* may escalate to a top-level login."""

    def setUp(self) -> None:
        # Derive the login URL from the request rather than PUBLIC_URL, so the
        # expected absolute URL is deterministic.
        patcher = patch.object(proxy, "PUBLIC_URL", "")
        patcher.start()
        self.addCleanup(patcher.stop)

    # -- 1.418 ---------------------------------------------------------
    def test_1_418_a_hidden_utility_frame_gets_a_401_not_a_breakout(self) -> None:
        """
        Scenario 1.418: a framed utility path is answered with 401 JSON, never the breakout page.
        Given: an unauthenticated iframe document load of each machine endpoint
        When: the proxy builds the unauthenticated response
        Then: it is a plain 401 -- no `window.top.location`, no `postMessage`, nothing
              that asks the surrounding page to move the tab.

        The breakout exists to rescue a dashboard the user is looking at. A
        hidden frame is not one: the user cannot see it, did not ask for it,
        and has no way to connect the tab suddenly changing site with a shim
        that failed to authenticate. Answering it with a 401 leaves the shim
        broken -- which is the correct outcome, because a broken theme sync is
        invisible and a hijacked tab is not.
        """
        for path in UTILITY_PATHS:
            with self.subTest(path=path):
                resp = proxy._unauthenticated_response(
                    _request(path, {"accept": "text/html", "sec-fetch-dest": "iframe"})
                )
                self.assertIsInstance(
                    resp, JSONResponse,
                    msg=f"{path!r} is machinery; a framed 401 there must not escalate to a "
                        f"top-level login",
                )
                self.assertEqual(resp.status_code, 401, msg="still unauthenticated, still a 401")
                self.assertNotIn(
                    b"window.top.location", resp.body,
                    msg="nothing served to a hidden frame may try to navigate the top window",
                )

    # -- 1.419 ---------------------------------------------------------
    def test_1_419_a_framed_dashboard_still_breaks_out(self) -> None:
        """
        Scenario 1.419: the dashboard's own recovery survives the narrowing.
        Given: an unauthenticated iframe document load of a dashboard view
        When: the proxy builds the unauthenticated response
        Then: it is still the 401 breakout page, carrying that view as the return target.

        The other half of 1.418, and the one a careless fix breaks: withhold
        the breakout from everything and an expired dashboard shows Keycloak's
        "refused to connect" wall again, which is the failure the breakout was
        written for.
        """
        resp = proxy._unauthenticated_response(
            _request("/", {"accept": "text/html", "sec-fetch-dest": "iframe"}, query="model=Period")
        )

        self.assertIsInstance(resp, HTMLResponse, msg="a framed dashboard must still break out")
        self.assertEqual(resp.status_code, 401, msg="the breakout is a 401")
        body = resp.body.decode()
        self.assertIn("window.top.location", body, msg="it must still escape the frame")
        self.assertIn(
            "next=%2F%3Fmodel%3DPeriod", body,
            msg="and still return the user to the dashboard they were looking at",
        )


class TestCluster01az_LandingOnTheRelayAnyway(SimpleTestCase):
    """Cluster 1az: the relay document is a dead end, so it does not stay one."""

    # -- 1.420 ---------------------------------------------------------
    def test_1_420_a_top_level_visit_is_sent_back_to_the_frontend(self) -> None:
        """
        Scenario 1.420: opening the relay as a page returns the browser to the app.
        Given: a deployment that was told where its frontend lives
        When: the relay URL is navigated to as a top-level document
        Then: it redirects there instead of rendering a blank storage shim.

        The guards above stop this proxy from sending anyone here. They cannot
        stop a bookmark, a back-button entry, or an older proxy still in the
        chain -- and every one of those leaves the user on a page with no
        content and no navigation. The frontend origin is already configured
        for the framing allow-list, so the way out costs nothing.
        """
        with patch.object(proxy, "_THEME_RELAY_CONFIGURED", ["https://app.example.com"]):
            resp = asyncio.run(proxy.theme_relay(
                _request("/_lex/theme-relay", {"accept": "text/html",
                                               "sec-fetch-dest": "document"})
            ))

        self.assertIsInstance(resp, RedirectResponse, msg="a top-level visit must not dead-end")
        self.assertEqual(
            resp.headers["location"], "https://app.example.com",
            msg="the way out is the frontend this deployment was configured with",
        )

    # -- 1.421 ---------------------------------------------------------
    def test_1_421_an_unconfigured_deployment_does_not_bounce_to_localhost(self) -> None:
        """
        Scenario 1.421: with no frontend configured, the relay is served rather than guessed at.
        Given: a deployment where neither REACT_APP_URL nor LEX_FRONTEND_URL is set
        When: the relay URL is navigated to as a top-level document
        Then: the relay document is served as usual -- no redirect.

        The framing allow-list falls back to `http://localhost:8000` so local
        development works out of the box. Reusing that fallback as a redirect
        target would send a production tab to a machine that is not the user's,
        which is a worse outcome than the blank page it was meant to replace.
        """
        with patch.object(proxy, "_THEME_RELAY_CONFIGURED", []):
            resp = asyncio.run(proxy.theme_relay(
                _request("/_lex/theme-relay", {"accept": "text/html",
                                               "sec-fetch-dest": "document"})
            ))

        self.assertNotIsInstance(
            resp, RedirectResponse,
            msg="an unconfigured deployment must not invent a destination",
        )
        self.assertEqual(resp.status_code, 200, msg="it serves the relay, as it always did")

    # -- 1.422 ---------------------------------------------------------
    def test_1_422_the_framed_relay_is_untouched(self) -> None:
        """
        Scenario 1.422: the relay still works for the only caller it has.
        Given: the frontend's hidden frame requesting the relay (Sec-Fetch-Dest: iframe)
        When: the proxy serves it
        Then: it is the relay document, carrying the shared storage key and a
              frame-ancestors policy naming the frontend -- unchanged by the
              top-level handling above.
        """
        with patch.object(proxy, "_THEME_RELAY_CONFIGURED", ["https://app.example.com"]), \
             patch.object(proxy, "_THEME_RELAY_ALLOWED", ["https://app.example.com"]):
            resp = asyncio.run(proxy.theme_relay(
                _request("/_lex/theme-relay", {"accept": "text/html",
                                               "sec-fetch-dest": "iframe"})
            ))

        self.assertEqual(resp.status_code, 200, msg="the framed relay must still be served")
        body = resp.body.decode()
        self.assertIn(
            proxy.THEME_STORAGE_KEY, body,
            msg="the relay is useless without the key both origins agree on",
        )
        self.assertEqual(
            resp.headers["content-security-policy"],
            "frame-ancestors https://app.example.com",
            msg="being framed by the frontend is the relay's entire purpose",
        )
