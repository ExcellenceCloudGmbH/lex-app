"""The Streamlit auth proxy breaks out of the iframe on re-auth instead of framing the IdP.

Intent: an embedded Streamlit dashboard runs inside an ``<iframe>``. When the
proxy has no valid identity it must re-authenticate the user, but Keycloak's
login page is served with ``X-Frame-Options: SAMEORIGIN`` /
``Content-Security-Policy: frame-ancestors 'self'`` and cannot render inside a
frame -- redirecting the iframe to the IdP makes the browser show
"auth.<host> refused to connect" and dead-ends the user. The proxy must instead
break out to a top-level login for iframe/frame document loads, while keeping the
ordinary top-level redirect and the API 401 untouched. A regression here either
resurrects the "refused to connect" wall (over-eager redirect) or breaks normal
top-level / API auth (over-eager breakout).

Cluster 1y — scenarios 1.195–1.200. Type: U.
Covers: lex/proxy.py (_unauthenticated_response, _is_iframe_document_request).
Run: python -m lex pytest lex/test_project/tests/init/test_1y_proxy_iframe_breakout.py -v
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from django.test import SimpleTestCase
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse

import lex.proxy as proxy

pytestmark = pytest.mark.init


def _request(headers: dict[str, str], scheme: str = "https", host: str = "app.example.com") -> Request:
    """Build a minimal ASGI ``Request`` carrying the given headers (a browser-like GET)."""
    raw = [(b"host", host.encode())]
    for key, value in headers.items():
        raw.append((key.lower().encode(), value.encode()))
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/dashboard",
        "query_string": b"",
        "scheme": scheme,
        "server": (host, 443 if scheme == "https" else 80),
        "headers": raw,
    }
    return Request(scope)


class TestCluster01y_ProxyIframeBreakout(SimpleTestCase):
    """Cluster 1y: unauthenticated-response routing of the Streamlit auth proxy."""

    def setUp(self) -> None:
        # Force _external_base_url() to derive the login URL from the request rather
        # than the PUBLIC_URL env var, so the expected absolute URL is deterministic.
        patcher = patch.object(proxy, "PUBLIC_URL", "")
        patcher.start()
        self.addCleanup(patcher.stop)

    # -- 1.195 ---------------------------------------------------------
    def test_1_195_iframe_document_breaks_out_instead_of_framing_idp(self) -> None:
        """
        Scenario 1.195: an unauthenticated iframe document load breaks out to a top-level login.
        Given: no valid identity and an iframe document navigation (Sec-Fetch-Dest: iframe, Accept: text/html)
        When: the proxy builds the unauthenticated response
        Then: it is a 401 that escapes the frame (top-nav + postMessage + target="_top" link to the
              absolute /auth/login URL) -- never a redirect that would render Keycloak inside the frame.
        """
        resp = proxy._unauthenticated_response(
            _request({"accept": "text/html", "sec-fetch-dest": "iframe"})
        )

        self.assertNotIsInstance(
            resp, RedirectResponse,
            msg="iframe re-auth must not redirect the frame to the IdP (Keycloak forbids framing "
                "its login page -> the browser shows 'refused to connect')",
        )
        self.assertIsInstance(resp, HTMLResponse, msg="expected an HTML frame-breakout response")
        self.assertEqual(resp.status_code, 401, msg="frame-breakout must signal unauthenticated (401)")

        body = resp.body.decode()
        self.assertIn("window.top.location", body, msg="must attempt automatic top-level navigation")
        self.assertIn("postMessage", body, msg="must notify a cooperating parent shell")
        self.assertIn('target="_top"', body, msg="must offer a user-gesture link that targets the top frame")
        self.assertIn(
            "https://app.example.com/auth/login", body,
            msg="breakout must point at this proxy's absolute login URL",
        )

    # -- 1.196 ---------------------------------------------------------
    def test_1_196_frame_document_also_breaks_out(self) -> None:
        """
        Scenario 1.196: a legacy <frame> document load breaks out the same way as an <iframe>.
        Given: no valid identity and Sec-Fetch-Dest: frame with Accept: text/html
        When: the proxy builds the unauthenticated response
        Then: it is the same 401 HTML frame-breakout (frame and iframe are both framed contexts).
        """
        resp = proxy._unauthenticated_response(
            _request({"accept": "text/html", "sec-fetch-dest": "frame"})
        )

        self.assertIsInstance(resp, HTMLResponse, msg="a framed (<frame>) load must also break out")
        self.assertEqual(resp.status_code, 401, msg="frame-breakout must signal unauthenticated (401)")

    # -- 1.197 ---------------------------------------------------------
    def test_1_197_top_level_html_still_redirects_to_login(self) -> None:
        """
        Scenario 1.197: a top-level HTML navigation still redirects to the login route.
        Given: no valid identity and a top-level document load (Sec-Fetch-Dest: document, Accept: text/html)
        When: the proxy builds the unauthenticated response
        Then: it redirects to /auth/login -- unchanged behaviour, since a top-level page can render the IdP.
        """
        resp = proxy._unauthenticated_response(
            _request({"accept": "text/html", "sec-fetch-dest": "document"})
        )

        self.assertIsInstance(resp, RedirectResponse, msg="top-level navigation must redirect to login")
        self.assertEqual(
            resp.headers["location"], "/auth/login",
            msg="top-level redirect target must be the login route",
        )

    # -- 1.198 ---------------------------------------------------------
    def test_1_198_html_without_sec_fetch_headers_keeps_redirect(self) -> None:
        """
        Scenario 1.198: clients that omit Sec-Fetch-* keep the existing redirect (safe default).
        Given: no valid identity, Accept: text/html, and no Sec-Fetch-* headers (older/non-browser client)
        When: the proxy builds the unauthenticated response
        Then: it redirects to login -- the breakout only triggers on a positively-identified framed context.
        """
        resp = proxy._unauthenticated_response(_request({"accept": "text/html"}))

        self.assertIsInstance(
            resp, RedirectResponse,
            msg="absent Sec-Fetch-* must fall back to the pre-existing redirect, not a breakout",
        )

    # -- 1.199 ---------------------------------------------------------
    def test_1_199_non_html_request_returns_401_json(self) -> None:
        """
        Scenario 1.199: an XHR/API call gets a JSON 401, not a redirect or an HTML page.
        Given: no valid identity and a non-HTML request (Accept: application/json)
        When: the proxy builds the unauthenticated response
        Then: it is a 401 JSON body so the caller can handle auth programmatically.
        """
        resp = proxy._unauthenticated_response(_request({"accept": "application/json"}))

        self.assertIsInstance(resp, JSONResponse, msg="non-HTML callers must get a JSON response")
        self.assertEqual(resp.status_code, 401, msg="non-HTML unauthenticated response must be 401")

    # -- 1.200 ---------------------------------------------------------
    def test_1_200_iframe_detection_is_case_insensitive_and_scoped(self) -> None:
        """
        Scenario 1.200: the framed-context detector matches iframe/frame case-insensitively and nothing else.
        Given: requests carrying various Sec-Fetch-Dest values (or none)
        When: _is_iframe_document_request inspects them
        Then: iframe/FRAME are detected (case-insensitive); document and a missing header are not.
        """
        self.assertTrue(
            proxy._is_iframe_document_request(_request({"sec-fetch-dest": "iframe"})),
            msg="'iframe' dest must be detected as a framed context",
        )
        self.assertTrue(
            proxy._is_iframe_document_request(_request({"sec-fetch-dest": "FRAME"})),
            msg="detection must be case-insensitive",
        )
        self.assertFalse(
            proxy._is_iframe_document_request(_request({"sec-fetch-dest": "document"})),
            msg="a top-level document must not be treated as framed",
        )
        self.assertFalse(
            proxy._is_iframe_document_request(_request({})),
            msg="a missing Sec-Fetch-Dest must not be treated as framed",
        )
