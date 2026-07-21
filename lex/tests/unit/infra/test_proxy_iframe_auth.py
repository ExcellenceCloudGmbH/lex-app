"""Tests for the unauthenticated-response behaviour of the Streamlit auth proxy.

Regression coverage for the "auth.excellence-cloud.de refused to connect" bug:
when the proxy has no valid identity and the request is a document load happening
*inside an iframe*, redirecting it to the IdP renders Keycloak's login page inside
the frame, which Keycloak forbids via ``X-Frame-Options`` / ``frame-ancestors 'self'``
-> the browser shows "refused to connect". The proxy must instead break out of the
frame (top-level login), while keeping the normal redirect for top-level navigations
and a 401 for XHR/API calls.
"""
from unittest import TestCase
from unittest.mock import patch

from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse

import lex.proxy as proxy


def _request(headers, scheme="https", host="app.example.com"):
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


class UnauthenticatedResponseTests(TestCase):
    def setUp(self):
        # Force _external_base_url() to derive from the request instead of the
        # PUBLIC_URL env var, so the expected login URL is deterministic.
        patcher = patch.object(proxy, "PUBLIC_URL", "")
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_iframe_document_breaks_out_instead_of_redirecting_to_idp(self):
        req = _request({"accept": "text/html", "sec-fetch-dest": "iframe"})

        resp = proxy._unauthenticated_response(req)

        # Must NOT 30x the frame to the IdP (that renders Keycloak in-frame ->
        # "refused to connect").
        self.assertNotIsInstance(resp, RedirectResponse)
        self.assertIsInstance(resp, HTMLResponse)
        self.assertEqual(resp.status_code, 401)

        body = resp.body.decode()
        # Escapes the frame to the top-level window ...
        self.assertIn("window.top.location", body)
        # ... offers a guaranteed user-gesture fallback that targets the top frame ...
        self.assertIn('target="_top"', body)
        # ... notifies a cooperating parent shell ...
        self.assertIn("postMessage", body)
        # ... and points at this proxy's absolute login URL.
        self.assertIn("https://app.example.com/auth/login", body)

    def test_frame_dest_also_breaks_out(self):
        req = _request({"accept": "text/html", "sec-fetch-dest": "frame"})

        resp = proxy._unauthenticated_response(req)

        self.assertIsInstance(resp, HTMLResponse)
        self.assertEqual(resp.status_code, 401)

    def test_top_level_html_still_redirects_to_login(self):
        req = _request({"accept": "text/html", "sec-fetch-dest": "document"})

        resp = proxy._unauthenticated_response(req)

        self.assertIsInstance(resp, RedirectResponse)
        self.assertEqual(resp.headers["location"], "/auth/login")

    def test_html_without_sec_fetch_headers_keeps_redirect(self):
        # Older clients / non-browsers that don't send Sec-Fetch-* must retain the
        # existing top-level redirect behaviour (safe default).
        req = _request({"accept": "text/html"})

        resp = proxy._unauthenticated_response(req)

        self.assertIsInstance(resp, RedirectResponse)

    def test_non_html_request_returns_401_json(self):
        req = _request({"accept": "application/json"})

        resp = proxy._unauthenticated_response(req)

        self.assertIsInstance(resp, JSONResponse)
        self.assertEqual(resp.status_code, 401)


class IsIframeDocumentRequestTests(TestCase):
    def test_detects_iframe_and_frame_dest_case_insensitively(self):
        self.assertTrue(proxy._is_iframe_document_request(_request({"sec-fetch-dest": "iframe"})))
        self.assertTrue(proxy._is_iframe_document_request(_request({"sec-fetch-dest": "FRAME"})))

    def test_ignores_top_level_and_missing_dest(self):
        self.assertFalse(proxy._is_iframe_document_request(_request({"sec-fetch-dest": "document"})))
        self.assertFalse(proxy._is_iframe_document_request(_request({})))
