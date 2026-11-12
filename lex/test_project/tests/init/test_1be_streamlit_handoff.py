"""Streamlit dashboards sign in through the app: the Django-anchored hand-off (lex-app 2.5, plan §6.3).

Intent: a dashboard never holds Keycloak tokens of its own. Its proxy, finding no
``__Host-lex_dash`` cookie, sends the frame to ``<app host>/oidc/handoff``; the app mints a
single-use code for the browser's sign-in (or answers ``login_required`` -- Keycloak is never
shown in a frame); the proxy redeems the code in-process into a binding on the user's
``OIDCSession`` and keeps only an opaque handle in a cookie. Every request and WebSocket then gets
a fresh access token of that sign-in from the broker, and nothing but that token and identity
headers reaches Streamlit. Signing out, a back-channel logout or Keycloak's ``invalid_grant`` end
the binding at its next use; Keycloak being unreachable never signs anyone out.

A regression here is what users see as "the dashboard logged me out again": a token that dies with
the access token's five minutes, a refresh token leaked to customer code, a frame showing a
Keycloak page that cannot render, or a redirect loop when cookies are blocked. The rollout flag
``LEX_STREAMLIT_AUTH`` (legacy / dual / handoff) must switch all of it on and off without
touching the legacy behaviour, which batches 1z-1an keep pinning.

A security review added 1.533-1.540: a socket opened by another site is refused (cross-site
WebSocket hijacking), redirects and page targets never leave the dashboard origin, dual mode never
lets a dead handle shadow the legacy session, the daily cookie re-set and the token pull's cookie
name are what the browser can actually rely on, a page whose socket dropped still learns that its
sign-in ended, a deactivated user is signed out, and a redeem locks only its code.

Cluster 1be -- scenarios 1.501-1.540. Type: U (1.501-1.502, 1.512-1.514, 1.518 partly, 1.519,
1.521-1.528, 1.531-1.532) and I (1.503-1.511, 1.515-1.517, 1.520, 1.529-1.530, 1.533-1.540;
transactional where the proxy's worker threads must see the rows). The component scripts of
1.524 and 1.539 also run under node, when it is installed, against a stand-in of the browser's
frame rules.
Covers: lex/proxy.py (hand-off section, proxy, ws_proxy + _ws_origin_allowed, internal_token +
        _read_handle_any, auth_status, adopt_token, login, auth_callback, oauth2_logout,
        oauth2_sign_out, _current_relative_path, _PROXY_OWNED_HEADERS, LEX_STREAMLIT_AUTH,
        _build_token_store), lex/authentication/dashboards/ (modes, cookies, sites + local_path,
        handoff, broker + standing_problem), lex/authentication/models/_dashboards.py + migration
        0002, lex/authentication/views/handoff_views.py (via the round trip),
        lex/authentication/management/commands/prune_dashboard_sessions.py, lex/streamlit_app.py
        (_pull_from_proxy, renew_access_token, needs_session_recovery, render_session_recovery,
        _handoff_recovery_js, _auth_watch_js, render_theme_follower, _sync_tokens_from_headers,
        _logout_href), lex/lex_app/streamlit/embed.py
        (validated_lex_app_origin, remember_lex_app_origin, _resolve_base_url),
        lex/lex_app/streamlit/_lex_view_component and _widget_host_component (sandbox),
        lex/bin/lex.py (_check_streamlit_auth_env), lex/lex_app/settings.py (_instance_app_hosts,
        ALLOWED_HOSTS).
Run: python -m lex pytest lex/test_project/tests/init/test_1be_streamlit_handoff.py -v
"""

from __future__ import annotations

import hashlib
import importlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import time
from base64 import b64encode
from contextlib import ExitStack, contextmanager
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, parse_qsl, quote, urljoin, urlsplit

import click
import httpx
import itsdangerous
import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import Client, SimpleTestCase, TestCase, TransactionTestCase, override_settings
from django.utils import timezone
from oauth2_authcodeflow import sessions as oidc_sessions
from oauth2_authcodeflow.models import OIDCSession
from oauth2_authcodeflow.testing import FakeOP
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

import lex
import lex.proxy as proxy
import lex.streamlit_app as sa
from lex.authentication.dashboards import cookies as dash_cookies
from lex.authentication.dashboards import handoff
from lex.authentication.models import DashboardBinding, DashboardHandoffCode
from lex.lex_app.streamlit import embed as lex_embed
from lex.lex_app.tests.auth import create_sign_in, oidc_test_settings, sign_in

pytestmark = pytest.mark.init

APP_HOST = "app.example.com"
DASH_HOST = "dash.example.com"
APP = f"https://{APP_HOST}"
DASH = f"https://{DASH_HOST}"
HOSTS = json.dumps({"app": {"canonical": APP_HOST}, "streamlit": {"canonical": DASH_HOST}})
#: The dashboard URL the frontend frames (no credential; it names its own origin).
DASH_PATH = "/?model=fund&lex_embed=true&lex_app_origin=" + quote(APP, safe="")
FRAME = {"sec-fetch-dest": "iframe", "sec-fetch-mode": "navigate", "accept": "text/html"}
XHR = {"sec-fetch-dest": "empty", "sec-fetch-mode": "cors", "accept": "application/json"}
DJANGO_FRAME = {"HTTP_SEC_FETCH_DEST": "iframe", "HTTP_SEC_FETCH_MODE": "navigate", "HTTP_ACCEPT": "text/html"}
HANDLE = "__Host-lex_dash"
NONCE = "__Host-lex_dash_hs"
#: Streamlit's socket, on the dashboard host (Starlette's TestClient would default to ws://testserver).
WS_URL = f"wss://{DASH_HOST}/_stcore/stream"

_CHECKOUT = Path(lex.__file__).resolve().parents[1]


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
@contextmanager
def streamlit_auth(mode: str = "handoff", *, hosts: str = HOSTS, **env: str):
    """The proxy (module constants) and the app (environment) in ``LEX_STREAMLIT_AUTH=mode``."""
    with ExitStack() as stack:
        stack.enter_context(patch.dict(os.environ, {"LEX_STREAMLIT_AUTH": mode, "LEX_INSTANCE_HOSTS": hosts, **env}))
        stack.enter_context(patch.object(proxy, "STREAMLIT_AUTH_MODE", mode))
        stack.enter_context(patch.object(proxy, "HANDOFF_ENABLED", mode in ("dual", "handoff")))
        stack.enter_context(patch.object(proxy, "LEGACY_ENABLED", mode in ("legacy", "dual")))
        yield


async def _no_jwks() -> None:
    """The legacy path warms Keycloak's JWKS first; no test here may reach a real Keycloak."""


def _set_cookie_headers(response) -> list:
    return response.headers.get_list("set-cookie")


def _cookie_line(response, name: str) -> str:
    for line in _set_cookie_headers(response):
        if line.split("=", 1)[0] == name:
            return line
    return ""


def _attributes(line: str) -> dict:
    """Attributes of a Set-Cookie line, lower-case names (flags map to True)."""
    out = {}
    for part in line.split(";")[1:]:
        name, sep, value = part.strip().partition("=")
        out[name.lower()] = value if sep else True
    return out


class _Upstream:
    """Stands in for Streamlit behind ``proxy._upstream_send``; keeps every header as sent."""

    def __init__(self) -> None:
        self.calls: list = []

    async def send(self, method, url, *, content=None, headers=None):
        self.calls.append(list((headers or {}).items()))
        return httpx.Response(200, content=b"upstream-ok")

    def values(self, name: str, call: int = -1) -> list:
        return [value for key, value in self.calls[call] if key.lower() == name.lower()]


class _FakeUpstreamWS:
    """Stands in for Streamlit's WebSocket; keeps the handshake headers as sent."""

    headers: list = []

    def __init__(self, url, **kwargs):
        type(self).headers = list(kwargs.get(proxy.WS_HEADER_KWARG) or [])

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    subprotocol = None

    async def send(self, data):
        pass

    async def recv(self):
        import websockets

        raise websockets.exceptions.ConnectionClosedOK(None, None)

    @classmethod
    def values(cls, name: str) -> list:
        return [value for key, value in cls.headers if key.lower() == name.lower()]


def _legacy_session_cookie(sid: str = "sid-1", email: str = "legacy@example.com") -> str:
    """A legacy proxy session cookie, minted the way SessionMiddleware mints them."""
    signer = itsdangerous.TimestampSigner(str(proxy.SESSION_SECRET))
    data = b64encode(json.dumps({"user": {"email": email, "sid": sid}}).encode("utf-8"))
    return signer.sign(data).decode("utf-8")


def _jwt(sub: str, exp: int) -> str:
    import jwt

    return jwt.encode({"sub": sub, "email": f"{sub}@example.com", "exp": exp}, "irrelevant", algorithm="HS256")


def _reimport_proxy_with(env: dict):
    """Import ``lex.proxy`` afresh under ``env`` (None removes a variable); the live module stays."""
    saved = sys.modules.get("lex.proxy")
    overrides = {k: v for k, v in env.items() if v is not None}
    removals = [k for k, v in env.items() if v is None]
    with patch.dict(os.environ, overrides, clear=False):
        for key in removals:
            os.environ.pop(key, None)
        sys.modules.pop("lex.proxy", None)
        try:
            return importlib.import_module("lex.proxy")
        finally:
            sys.modules["lex.proxy"] = saved
            if saved is not None:
                lex.proxy = saved  # type: ignore[attr-defined]


class _FakeState(dict):
    """``st.session_state`` outside a script run."""

    def __getattr__(self, key):
        try:
            return self[key]
        except KeyError:
            raise AttributeError(key) from None

    def __setattr__(self, key, value):
        self[key] = value


#: Runs a Streamlit component's scripts against a stand-in of the browser rules that matter here:
#: the component frame is same-origin with the dashboard document (it may read it and add a
#: <script> to it) but sandboxed, so the browser refuses any navigation it attempts on that parent
#: ("disallowed from navigating its ancestors"); a <script> element added to the dashboard document
#: runs as that document's own code, and a document may navigate itself.
_JS_HARNESS = r"""
const input = JSON.parse(require('fs').readFileSync(0, 'utf8'));
const out = {navigated: [], refused: [], fetched: [], intervals: [], errors: []};
const timers = [];
const listeners = {};
const on = (type, fn) => { (listeners[type] = listeners[type] || []).push(fn); };
const location = {pathname: input.pathname, search: input.search, origin: input.origin,
                  replace(u) { out.navigated.push(String(u)); }};
const doc = {hidden: false, addEventListener: on,
             createElement(tag) { return {tagName: String(tag).toUpperCase(), textContent: ''}; }};
const win = {document: doc, location, addEventListener: on};
win.window = win;
win.top = input.framed ? {} : win;
const fetchStub = (url, opts) => {
  out.fetched.push({url: String(url), credentials: opts && opts.credentials});
  return Promise.resolve({status: input.status});
};
const setIntervalStub = (fn, ms) => { timers.push({fn, live: true}); out.intervals.push(ms); return timers.length; };
const clearIntervalStub = (id) => { if (timers[id - 1]) timers[id - 1].live = false; };
const appendChild = (el) => {
  if (el && el.tagName === 'SCRIPT') {
    try {
      new Function('window', 'document', 'location', 'fetch', 'setInterval', 'clearInterval', el.textContent)(
        win, doc, location, fetchStub, setIntervalStub, clearIntervalStub);
    } catch (e) { out.errors.push(String(e)); }
  }
  return el;
};
doc.head = {appendChild};
doc.documentElement = {appendChild};
const refuse = (u) => {
  out.refused.push(String(u));
  throw new Error('The frame attempting navigation is sandboxed, and is therefore disallowed from navigating its ancestors.');
};
const parentFromFrame = {document: doc, top: win.top, location: {
  pathname: location.pathname, search: location.search, origin: location.origin,
  replace: refuse, assign: refuse, set href(u) { refuse(u); }}};
const frame = {parent: parentFromFrame, top: input.framed ? win.top : parentFromFrame};
frame.window = frame;
frame.self = frame;
(async () => {
  for (const step of input.steps) {
    if (step === 'run') {
      for (const body of input.scripts) {
        try { new Function('window', body)(frame); } catch (e) { out.errors.push(String(e)); }
      }
    } else if (step === 'tick') {
      for (const t of timers) { if (t.live) t.fn(); }
    } else if (step === 'hide') {
      doc.hidden = true;
    } else if (step === 'show') {
      doc.hidden = false;
    } else if (step.startsWith('event:')) {
      for (const fn of (listeners[step.slice(6)] || [])) fn();
    }
    await new Promise((r) => setImmediate(r));
    await new Promise((r) => setImmediate(r));
  }
  out.liveTimers = timers.filter((t) => t.live).length;
  process.stdout.write(JSON.stringify(out));
})();
"""


def _run_component_scripts(component_html: str, *, framed: bool = True, status: int = 200,
                           steps=("run",), pathname: str = "/", search: str = ""):
    """Run ``component_html``'s scripts in the stand-in above; ``None`` when node is not installed."""
    node = shutil.which("node")
    if node is None:
        return None
    payload = {"scripts": re.findall(r"<script>(.*?)</script>", component_html, re.S), "framed": framed,
               "status": status, "steps": list(steps), "pathname": pathname, "search": search, "origin": DASH}
    done = subprocess.run([node, "-e", _JS_HARNESS], input=json.dumps(payload), capture_output=True,
                          text=True, timeout=60)
    if done.returncode != 0:
        raise AssertionError(f"the component scripts did not run: {done.stderr[-2000:]}")
    ran = json.loads(done.stdout)
    if ran["errors"]:
        raise AssertionError(f"the component scripts raised: {ran['errors']}")
    return ran


def _encode_uri_component(value: str) -> str:
    """What JavaScript's ``encodeURIComponent`` makes of ``value``."""
    return quote(value, safe="!~*'()")


def _stays_on(origin: str, target: str) -> bool:
    """Whether a browser following ``target`` (a Location, a refresh or script URL) from a page on
    ``origin`` stays on it. Browsers read ``\\`` as ``/`` in http(s) URLs and drop tabs and newlines."""
    cleaned = "".join(ch for ch in target if ch not in "\t\n\r").replace("\\", "/")
    resolved = urlsplit(urljoin(origin + "/", cleaned))
    return f"{resolved.scheme}://{resolved.netloc}" == origin


# ---------------------------------------------------------------------------
# a signed-in dashboard: proxy + app, end to end
# ---------------------------------------------------------------------------
class _HandoffCase(TransactionTestCase):
    """The proxy and the app with committed rows, so the proxy's worker threads see them."""

    def setUp(self) -> None:
        super().setUp()
        stack = ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(streamlit_auth("handoff"))
        stack.enter_context(override_settings(ALLOWED_HOSTS=[APP_HOST, DASH_HOST, "testserver"]))
        self.upstream = _Upstream()
        stack.enter_context(patch.object(proxy, "_upstream_send", self.upstream.send))
        stack.enter_context(patch.object(proxy, "ws_connect", _FakeUpstreamWS))
        stack.enter_context(patch.object(proxy, "_ensure_jwks_ready", _no_jwks))
        self.stack = stack
        self.dash = TestClient(proxy.app, base_url=DASH)
        self.web = Client()

    def _user(self, username: str = "ada"):
        return get_user_model().objects.create_user(username=username, email=f"{username}@example.com")

    def _app_get(self, location: str, **extra):
        """Follow a Location to the app (Django), in the dashboard's frame."""
        parts = urlsplit(location)
        self.assertEqual(f"{parts.scheme}://{parts.netloc}", APP, f"the hand-off must go to the app host: {location}")
        headers = dict(DJANGO_FRAME)
        headers.update(extra)
        return self.web.get(parts.path, dict(parse_qsl(parts.query, keep_blank_values=True)),
                            HTTP_HOST=APP_HOST, secure=True, **headers)

    def _dash_get(self, location: str, headers=None):
        parts = urlsplit(location)
        target = parts.path + (f"?{parts.query}" if parts.query else "")
        return self.dash.get(target, headers=headers or FRAME, follow_redirects=False)

    def _hand_off(self, path: str = DASH_PATH):
        """Run the hand-off for ``path``; returns the final response of the dashboard (after redeem)."""
        start = self.dash.get(path, headers=FRAME, follow_redirects=False)
        self.assertEqual(start.status_code, 303, start.text)
        back = self._app_get(start.headers["location"])
        self.assertEqual(back.status_code, 302, back.content[:500])
        return self._dash_get(back["Location"])

    def _signed_in_dashboard(self, username: str = "ada", **sign_in_kwargs):
        """A user signed in to the app, and a dashboard handed off to; (user, row, handle)."""
        user = self._user(username)
        row = sign_in(self, self.web, user, client_roles=["viewer"], **sign_in_kwargs)
        landed = self._hand_off()
        self.assertEqual(landed.status_code, 303, landed.text)
        handle = self.dash.cookies.get(HANDLE)
        self.assertTrue(handle, "the redeem must set the handle cookie")
        return user, row, handle


class TestCluster01be_HandoffStart(SimpleTestCase):
    """1.501-1.502: no handle -> the frame goes to the app's oidc/handoff, never to Keycloak."""

    # -- 1.501 ---------------------------------------------------------
    def test_1_501_a_framed_dashboard_without_a_handle_hands_off_to_the_app(self) -> None:
        """
        Scenario 1.501: a framed dashboard with no handle starts the hand-off.
        Given: LEX_STREAMLIT_AUTH=handoff and a frame navigation to the dashboard with no cookie
        When: the proxy answers it
        Then: 303 to <app>/oidc/handoff?aud=streamlit&return=<this URL + lex_hs=1>&state=<nonce>,
              with the nonce in `__Host-lex_dash_hs` (HttpOnly; Secure; SameSite=None;
              Partitioned; Path=/; 10 min) -- never Keycloak, never a top-window breakout.
        """
        with streamlit_auth("handoff"):
            response = TestClient(proxy.app, base_url=DASH).get(DASH_PATH, headers=FRAME, follow_redirects=False)

        self.assertEqual(response.status_code, 303, "a framed document must be handed off, not answered")
        target = urlsplit(response.headers["location"])
        self.assertEqual((target.scheme, target.netloc, target.path), ("https", APP_HOST, "/oidc/handoff"))
        query = parse_qs(target.query)
        self.assertEqual(query["aud"], ["streamlit"])
        returned = urlsplit(query["return"][0])
        self.assertEqual(f"{returned.scheme}://{returned.netloc}", DASH, "the code must come back to this host")
        self.assertEqual(parse_qs(returned.query).get("lex_hs"), ["1"], "the URL must count the hand-off")
        self.assertEqual(parse_qs(returned.query).get("model"), ["fund"], "the requested view must survive")
        line = _cookie_line(response, NONCE)
        self.assertTrue(line, "the nonce cookie must be set")
        attrs = _attributes(line)
        self.assertEqual(line.split(";")[0].split("=", 1)[1], query["state"][0], "state is the nonce")
        for flag in ("httponly", "secure", "partitioned"):
            self.assertIs(attrs.get(flag), True, f"nonce cookie must be {flag}")
        self.assertEqual(attrs.get("samesite"), "None")
        self.assertEqual(attrs.get("path"), "/")
        self.assertEqual(attrs.get("max-age"), str(dash_cookies.NONCE_MAX_AGE))
        self.assertNotIn("domain", attrs, "a __Host- cookie must be host-only")
        self.assertNotIn("keycloak", response.headers["location"].lower())

    # -- 1.502 ---------------------------------------------------------
    def test_1_502_the_handoff_goes_to_the_app_host_the_user_signed_in_on(self) -> None:
        """
        Scenario 1.502: the hand-off goes to the listed app host the user is signed in on.
        Given: LEX_INSTANCE_HOSTS with an app canonical on another cloud and two app hosts on the
               customer's domain, and the dashboard served on the customer's domain
        When: the dashboard starts a hand-off framed by the frontend on one of those app hosts (it
              names its origin as `lex_app_origin`); opened on its own; framed with a
              `lex_app_origin` the instance does not list; and when a request names a Host the
              instance does not list
        Then: framed, the frame goes to the frontend's own host -- Django's session cookie is
              host-only, so any other host would find no sign-in; on its own, to the first app
              host same-site with the dashboard (the Lax session cookie only travels same-site);
              an unlisted `lex_app_origin` is ignored; an unlisted Host gets a 400 page and is
              never redirected anywhere.
        """
        hosts = json.dumps({
            "app": {"canonical": "acme.lex-cloud.io", "extra": ["app.fund.example.com", "portal.fund.example.com"]},
            "streamlit": {"canonical": "acme-dash.lex-cloud.io", "extra": ["dash.fund.example.com"]},
        })

        def start(path):
            return TestClient(proxy.app, base_url="https://dash.fund.example.com").get(
                path, headers=FRAME, follow_redirects=False)

        with streamlit_auth("handoff", hosts=hosts):
            framed = start("/?model=fund&lex_app_origin=" + quote("https://portal.fund.example.com", safe=""))
            standalone = start("/?model=fund")
            foreign = start("/?model=fund&lex_app_origin=" + quote("https://evil.example.net", safe=""))
            stranger = TestClient(proxy.app, base_url="https://evil.example.net").get(
                DASH_PATH, headers=FRAME, follow_redirects=False)

        self.assertEqual(urlsplit(framed.headers["location"]).netloc, "portal.fund.example.com",
                         "framed: the host the frontend (and so the sign-in) is on")
        self.assertEqual(urlsplit(standalone.headers["location"]).netloc, "app.fund.example.com",
                         "standalone: the first app host same-site with the dashboard")
        self.assertEqual(urlsplit(foreign.headers["location"]).netloc, "app.fund.example.com",
                         "an unlisted lex_app_origin must not decide where the hand-off goes")
        self.assertEqual(stranger.status_code, 400, "an unlisted host must not get a hand-off")
        self.assertNotIn("location", stranger.headers, "and must never be redirected")


class TestCluster01be_HandoffRoundTrip(_HandoffCase):
    """1.503-1.507, 1.509-1.511, 1.515-1.516: the round trip, and what a handle is worth."""

    # -- 1.503 ---------------------------------------------------------
    def test_1_503_the_round_trip_sets_the_handle_cookie_and_returns_to_the_view(self) -> None:
        """
        Scenario 1.503: proxy -> app -> proxy ends with the handle cookie, back on the requested view.
        Given: a user signed in to the app, and a framed dashboard without a handle
        When: the frame follows the hand-off (proxy -> app oidc/handoff -> proxy /auth/handoff)
        Then: the app answers with a code for this dashboard origin; the proxy sets
              `__Host-lex_dash` (HttpOnly; Secure; SameSite=None; Partitioned; Path=/; 30 days),
              clears the used nonce and sends the frame back to the view it asked for; the
              binding rests on the user's sign-in row.
        """
        user = self._user()
        row = sign_in(self, self.web, user)

        start = self.dash.get(DASH_PATH, headers=FRAME, follow_redirects=False)
        back = self._app_get(start.headers["location"])
        self.assertEqual(back.status_code, 302)
        callback = urlsplit(back["Location"])
        self.assertEqual((f"{callback.scheme}://{callback.netloc}", callback.path), (DASH, "/auth/handoff"))
        self.assertIn("code", parse_qs(callback.query), "a signed-in browser must get a code")

        landed = self._dash_get(back["Location"])
        self.assertEqual(landed.status_code, 303)
        landing = urlsplit(landed.headers["location"])
        self.assertEqual(parse_qs(landing.query).get("model"), ["fund"], "back to the requested view")
        attrs = _attributes(_cookie_line(landed, HANDLE))
        for flag in ("httponly", "secure", "partitioned"):
            self.assertIs(attrs.get(flag), True, f"the handle cookie must be {flag}")
        self.assertEqual(attrs.get("samesite"), "None")
        self.assertEqual(attrs.get("path"), "/")
        self.assertEqual(attrs.get("max-age"), str(30 * 24 * 3600), "the handle cookie lives 30 days")
        nonce_line = _cookie_line(landed, NONCE)
        self.assertEqual(_attributes(nonce_line).get("max-age"), "0", "the used nonce must be cleared")
        binding = DashboardBinding.objects.get()
        self.assertEqual(binding.session_id, row.pk, "the binding rests on the user's sign-in")
        self.assertEqual((binding.aud, binding.origin), ("streamlit", DASH))

    # -- 1.504 ---------------------------------------------------------
    def test_1_504_only_the_access_token_and_identity_reach_streamlit(self) -> None:
        """
        Scenario 1.504: a handle-authenticated request carries the sign-in's token and nothing else.
        Given: a dashboard handed off to (its landing URL still carries the hand-off marker)
        When: the frame loads the view, then the view is proxied
        Then: the marker is stripped with one redirect first; upstream then gets `Authorization:
              Bearer <the sign-in's current access token>` and the identity headers -- no
              refresh token, no id token, no other token header.
        """
        user = self._user()
        row = sign_in(self, self.web, user)
        landed = self._hand_off()
        self.assertIn("lex_hs=", landed.headers["location"], "the landing URL carries the hand-off marker")
        marked = self._dash_get(landed.headers["location"])
        self.assertEqual(marked.status_code, 303)
        self.assertNotIn("lex_hs", marked.headers["location"], "the marker must not stay in the URL")
        self.assertEqual(self.upstream.calls, [], "the marker is stripped before anything is served")

        served = self._dash_get(marked.headers["location"])
        self.assertEqual(served.status_code, 200)
        row.refresh_from_db()
        self.assertEqual(self.upstream.values("authorization"), [f"Bearer {row.access_token}"])
        self.assertEqual(self.upstream.values("x-streamlit-auth-method"), ["handoff"])
        self.assertEqual(self.upstream.values("x-streamlit-user-email"), [user.email])
        for leaked in ("x-streamlit-refresh-token", "x-forwarded-id-token", "x-forwarded-access-token",
                       "x-streamlit-access-token", "x-lex-auth-degraded"):
            self.assertEqual(self.upstream.values(leaked), [], f"{leaked} must not reach Streamlit")
        sent = " ".join(value for _, value in self.upstream.calls[-1])
        self.assertNotIn(row.refresh_token, sent, "the refresh token must never leave the app")

    # -- 1.505 ---------------------------------------------------------
    def test_1_505_a_code_is_redeemed_once_and_only_by_the_browser_that_asked(self) -> None:
        """
        Scenario 1.505: the code is bound to the asking browser's state and is single-use.
        Given: a code the app minted for one hand-off
        When: it comes back with a state this browser never asked for; and when the same code is
              redeemed a second time
        Then: the foreign state is never redeemed (no binding, the frame goes back to start over);
              the replay is refused and revokes the binding the code first produced, whose handle
              then stops working.
        """
        user = self._user()
        sign_in(self, self.web, user)
        start = self.dash.get(DASH_PATH, headers=FRAME, follow_redirects=False)
        back = urlsplit(self._app_get(start.headers["location"])["Location"])
        query = dict(parse_qsl(back.query))

        forged = dict(query, state="someone-elses-state-0123456789")
        refused = self.dash.get("/auth/handoff", params=forged, headers=FRAME, follow_redirects=False)
        self.assertEqual(refused.status_code, 303)
        self.assertFalse(_cookie_line(refused, HANDLE), "a foreign state must never be redeemed")
        self.assertFalse(DashboardBinding.objects.exists())

        code_row = DashboardHandoffCode.objects.get()
        first = handoff.redeem(query["code"], aud="streamlit", origin=DASH, state=query["state"])
        with self.assertRaises(handoff.HandoffError) as replay:
            handoff.redeem(query["code"], aud="streamlit", origin=DASH, state=query["state"])
        self.assertEqual(replay.exception.reason, "code_reused")
        code_row.refresh_from_db()
        self.assertIsNotNone(code_row.used_at)
        first.binding.refresh_from_db()
        self.assertEqual(first.binding.revoke_reason, "code_reused", "a replay revokes what the code produced")
        self.dash.cookies.set(HANDLE, first.handle, domain=DASH_HOST)
        api = self.dash.get("/_stcore/upload_file/x", headers=XHR, follow_redirects=False)
        self.assertEqual(api.status_code, 401, "the revoked handle must stop working")

    # -- 1.506 ---------------------------------------------------------
    def test_1_506_a_code_older_than_a_minute_is_refused(self) -> None:
        """
        Scenario 1.506: a code is valid 60 seconds.
        Given: a code that was minted more than 60 seconds ago
        When: the proxy redeems it
        Then: no handle cookie is set; the frame gets the sign-in-required page (401), which asks
              the frontend to recover the dashboard.
        """
        user = self._user()
        sign_in(self, self.web, user)
        start = self.dash.get(DASH_PATH, headers=FRAME, follow_redirects=False)
        back = self._app_get(start.headers["location"])
        DashboardHandoffCode.objects.update(expires_at=timezone.now() - timedelta(seconds=1))

        landed = self._dash_get(back["Location"])
        self.assertEqual(landed.status_code, 401)
        self.assertFalse(_cookie_line(landed, HANDLE), "an expired code must not sign anything in")
        self.assertIn('"event": "required"', landed.text)

    # -- 1.507 ---------------------------------------------------------
    def test_1_507_an_ended_sign_in_revokes_the_binding_at_its_next_use(self) -> None:
        """
        Scenario 1.507: when the sign-in ends, the dashboard's binding dies with it.
        Given: a dashboard handed off to, and then its sign-in ended (a back-channel logout)
        When: the dashboard makes a background request, and then loads a document
        Then: the document starts a fresh hand-off and clears the dead handle, and the binding is
              revoked; a background request still holding the old handle gets 401 JSON
              `session_expired` (no-store), never a redirect.
        """
        user, row, handle = self._signed_in_dashboard()
        oidc_sessions.end_sessions(sid=row.sid, reason="backchannel_logout")

        page = self.dash.get(DASH_PATH, headers=FRAME, follow_redirects=False)
        self.assertEqual(page.status_code, 303)
        self.assertEqual(urlsplit(page.headers["location"]).path, "/oidc/handoff", "a fresh hand-off starts")
        self.assertEqual(_attributes(_cookie_line(page, HANDLE)).get("max-age"), "0", "the dead handle is cleared")
        binding = DashboardBinding.objects.get(handle_hash=handoff.digest(handle))
        self.assertIsNotNone(binding.revoked_at, "the binding must be revoked")

        stale = TestClient(proxy.app, base_url=DASH)  # another frame that still holds the old cookie
        stale.cookies.set(HANDLE, handle, domain=DASH_HOST)
        api = stale.get("/_stcore/upload_file/x", headers=XHR, follow_redirects=False)
        self.assertEqual(api.status_code, 401)
        self.assertEqual(api.json()["error"], "session_expired")
        self.assertEqual(api.headers.get("cache-control"), "no-store")

    # -- 1.509 ---------------------------------------------------------
    def test_1_509_websockets_get_the_token_or_a_close_code_after_accept(self) -> None:
        """
        Scenario 1.509: the WebSocket is checked like a request, and refused with a code the browser sees.
        Given: a dashboard handed off to
        When: Streamlit's socket connects; then a socket with no handle connects; then the
              sign-in ends and the socket reconnects
        Then: the upstream handshake carries Authorization + identity only; without a handle, and
              after the sign-in ended, the socket is accepted and closed with 4401 (closing in the
              handshake reaches the browser as 1006).
        """
        user, row, handle = self._signed_in_dashboard()
        with self.dash.websocket_connect(WS_URL):
            pass
        row.refresh_from_db()
        self.assertEqual(_FakeUpstreamWS.values("authorization"), [f"Bearer {row.access_token}"])
        self.assertEqual(_FakeUpstreamWS.values("x-streamlit-refresh-token"), [])
        self.assertEqual(_FakeUpstreamWS.values("x-forwarded-id-token"), [])

        anonymous = TestClient(proxy.app, base_url=DASH)
        with anonymous.websocket_connect(WS_URL) as socket:
            with self.assertRaises(WebSocketDisconnect) as closed:
                socket.receive_text()
        self.assertEqual(closed.exception.code, 4401)

        oidc_sessions.end_sessions(sid=row.sid, reason="backchannel_logout")
        with self.dash.websocket_connect(WS_URL) as socket:
            with self.assertRaises(WebSocketDisconnect) as ended:
                socket.receive_text()
        self.assertEqual(ended.exception.code, 4401)
        self.assertIsNotNone(DashboardBinding.objects.get(handle_hash=handoff.digest(handle)).revoked_at)

    # -- 1.510 ---------------------------------------------------------
    def test_1_510_auth_token_says_401_only_when_the_sign_in_ended(self) -> None:
        """
        Scenario 1.510: Streamlit's pull channel answers by the hand-off's rules.
        Given: a dashboard handed off to, and the Cookie header Streamlit froze at connect
        When: the Streamlit process pulls /auth/token (a loopback call; either cookie name on this
              proxy, configured for plain http -- on an https proxy only `__Host-lex_dash`, 1.538)
        Then: 200 with the sign-in's access token (never a refresh token); 403 without the
              internal secret; 401 `not_authenticated` without a handle; 401 `session_expired`
              once the sign-in ended.
        """
        user, row, handle = self._signed_in_dashboard()
        internal = TestClient(proxy.app, base_url="http://127.0.0.1:8501")
        secret = {proxy.INTERNAL_AUTH_HEADER: proxy.INTERNAL_AUTH_SECRET}

        for cookie in (f"{HANDLE}={handle}", f"lex_dash={handle}"):
            pulled = internal.get("/auth/token", headers={**secret, "cookie": cookie})
            self.assertEqual(pulled.status_code, 200, pulled.text)
            row.refresh_from_db()
            self.assertEqual(pulled.json()["access_token"], row.access_token)
            self.assertNotIn("refresh_token", pulled.json())
        self.assertEqual(internal.get("/auth/token", headers={"cookie": f"{HANDLE}={handle}"}).status_code, 403)
        missing = internal.get("/auth/token", headers=secret)
        self.assertEqual((missing.status_code, missing.json()["error"]), (401, "not_authenticated"))

        oidc_sessions.end_sessions(sid=row.sid, reason="backchannel_logout")
        ended = internal.get("/auth/token", headers={**secret, "cookie": f"{HANDLE}={handle}"})
        self.assertEqual((ended.status_code, ended.json()["error"]), (401, "session_expired"))

    # -- 1.511 ---------------------------------------------------------
    def test_1_511_signing_out_revokes_the_binding(self) -> None:
        """
        Scenario 1.511: /oauth2/logout and /oauth2/sign_out end the dashboard's binding.
        Given: a dashboard handed off to
        When: the user signs out of the dashboard (/oauth2/logout); and, on a second dashboard,
              signs out everywhere (/oauth2/sign_out, with a foreign `rd`)
        Then: logout revokes the binding, clears the handle and shows a signed-out page (not a
              redirect that would hand straight back in); sign_out revokes it too and goes to the
              app's total logout with `next` back on this dashboard -- a foreign `rd` is replaced
              by the dashboard root.
        """
        user, row, handle = self._signed_in_dashboard()
        out = self.dash.get("/oauth2/logout", headers=FRAME, follow_redirects=False)
        self.assertEqual(out.status_code, 200)
        self.assertIn("signed out of this dashboard", out.text)
        self.assertEqual(_attributes(_cookie_line(out, HANDLE)).get("max-age"), "0")
        self.assertEqual(DashboardBinding.objects.get(handle_hash=handoff.digest(handle)).revoke_reason, "logout")

        landed = self._hand_off()
        second = self.dash.cookies.get(HANDLE)
        self.assertEqual(landed.status_code, 303)
        bye = self.dash.get("/oauth2/sign_out", params={"rd": "https://evil.example.net/"}, headers=FRAME,
                            follow_redirects=False)
        self.assertEqual(bye.status_code, 303)
        target = urlsplit(bye.headers["location"])
        self.assertEqual((target.netloc, target.path), (APP_HOST, "/oidc/total_logout"))
        self.assertEqual(parse_qs(target.query)["next"], [f"{DASH}/"], "a foreign rd must not survive")
        self.assertIsNotNone(DashboardBinding.objects.get(handle_hash=handoff.digest(second)).revoked_at)

    # -- 1.515 ---------------------------------------------------------
    def test_1_515_hand_offs_in_two_frames_both_complete(self) -> None:
        """
        Scenario 1.515: several tabs can hand off at once.
        Given: a signed-in user whose browser starts two hand-offs before either returns
        When: the second returns first, then the first
        Then: both are redeemed (the nonce cookie holds every hand-off in flight); and no more
              than five are kept at once.
        """
        user = self._user()
        sign_in(self, self.web, user)
        first = self.dash.get(DASH_PATH, headers=FRAME, follow_redirects=False)
        second = self.dash.get("/?model=other", headers=FRAME, follow_redirects=False)
        back_first = self._app_get(first.headers["location"])
        back_second = self._app_get(second.headers["location"])

        self.assertEqual(self._dash_get(back_second["Location"]).status_code, 303)
        self.assertEqual(self._dash_get(back_first["Location"]).status_code, 303)
        self.assertEqual(DashboardBinding.objects.count(), 2, "both hand-offs must complete")

        many = TestClient(proxy.app, base_url=DASH)  # a browser with no handle yet
        for index in range(7):
            started = many.get(f"/?model=m{index}", headers=FRAME, follow_redirects=False)
        kept = dash_cookies.pending_nonces(_cookie_line(started, NONCE).split(";")[0].split("=", 1)[1])
        self.assertEqual(len(kept), dash_cookies.MAX_PENDING_NONCES)

    # -- 1.516 ---------------------------------------------------------
    def test_1_516_the_handle_cookie_is_re_set_daily_not_per_request(self) -> None:
        """
        Scenario 1.516: the 30-day handle cookie slides, at most once a day.
        Given: a dashboard handed off to
        When: it is used again the same day; and again more than a day after the cookie was set
        Then: the same day nothing re-sets the cookie; after a day the response re-sets it for
              another 30 days and records when.
        """
        user, row, handle = self._signed_in_dashboard()
        same_day = self.dash.get(DASH_PATH, headers=FRAME, follow_redirects=False)
        self.assertFalse(_cookie_line(same_day, HANDLE), "no cookie churn on every request")

        DashboardBinding.objects.update(cookie_set_at=timezone.now() - timedelta(days=2))
        later = self.dash.get(DASH_PATH, headers=FRAME, follow_redirects=False)
        line = _cookie_line(later, HANDLE)
        self.assertTrue(line, "after a day the cookie must be re-set")
        self.assertEqual(line.split(";")[0], f"{HANDLE}={handle}", "the handle itself never changes")
        self.assertEqual(_attributes(line).get("max-age"), str(30 * 24 * 3600))
        binding = DashboardBinding.objects.get()
        self.assertGreater(binding.cookie_set_at, timezone.now() - timedelta(minutes=1))


class TestCluster01be_KeycloakOutage(_HandoffCase):
    """1.508: Keycloak unreachable never signs a dashboard out."""

    def setUp(self) -> None:
        super().setUp()
        self.op = FakeOP()
        self.stack.enter_context(override_settings(**self.op.django_settings()))
        self.op.install()
        self.addCleanup(self.op.uninstall)

    # -- 1.508 ---------------------------------------------------------
    def test_1_508_an_outage_keeps_the_valid_token_then_degrades_to_identity(self) -> None:
        """
        Scenario 1.508: Keycloak down -> the still-valid token, then identity only; never a 401.
        Given: a dashboard whose sign-in's token is due for refresh, with Keycloak answering 503
        When: the dashboard is used while the token is still valid for 40 s; and once it has no
              usable life left
        Then: first the still-valid token is forwarded; then identity only, marked
              `X-Lex-Auth-Degraded`, with no Authorization -- and the response is not a 401, the
              binding is not revoked, and /auth/token answers 503 with Retry-After.
        """
        # Keycloak is down from the start: no refresh succeeds, not even the app's own.
        self.op.script_token(*[(503, "<html>down</html>")] * 12)
        user, row, handle = self._signed_in_dashboard(access_expires_in=40)

        still = self.dash.get(DASH_PATH, headers=FRAME, follow_redirects=False)
        self.assertEqual(still.status_code, 200)
        self.assertEqual(self.upstream.values("authorization"), [f"Bearer {row.access_token}"],
                         "a token valid for 40 more seconds must keep being used")
        self.assertGreaterEqual(self.op.refresh_calls(), 1, "the refresh really was attempted, and failed")

        OIDCSession.objects.filter(pk=row.pk).update(access_expires_at=timezone.now() + timedelta(seconds=2))
        degraded = self.dash.get(DASH_PATH, headers=FRAME, follow_redirects=False)
        self.assertEqual(degraded.status_code, 200, "an outage must not sign the dashboard out")
        self.assertEqual(self.upstream.values("authorization"), [])
        self.assertEqual(self.upstream.values("x-lex-auth-degraded"), ["auth_unavailable"])
        self.assertEqual(self.upstream.values("x-streamlit-user-email"), [user.email], "identity still flows")
        self.assertIsNone(DashboardBinding.objects.get().revoked_at, "the binding must survive an outage")

        internal = TestClient(proxy.app, base_url="http://127.0.0.1:8501")
        pulled = internal.get("/auth/token", headers={proxy.INTERNAL_AUTH_HEADER: proxy.INTERNAL_AUTH_SECRET,
                                                       "cookie": f"{HANDLE}={handle}"})
        self.assertEqual(pulled.status_code, 503)
        self.assertEqual(pulled.json()["error"], "auth_unavailable")
        self.assertTrue(pulled.headers.get("retry-after"))


class TestCluster01be_BlockedCookiesAndPages(SimpleTestCase):
    """1.512-1.514: no redirect loop without cookies; the frame tells its frontend."""

    # -- 1.512 ---------------------------------------------------------
    def test_1_512_blocked_cookies_end_on_a_page_not_in_a_loop(self) -> None:
        """
        Scenario 1.512: a browser that keeps no cookie in the frame is shown a page, not looped.
        Given: a hand-off that comes back with a code but without the nonce cookie (blocked)
        When: the proxy sees the code, sends the frame back, and the view arrives with no handle
              a second time
        Then: the code is never redeemed without its nonce; the first return starts one more
              hand-off (a nonce can simply have expired), the second shows the blocked-cookie page
              (storage access, open in a new tab) -- 401, and no further redirect.
        """
        with streamlit_auth("handoff"):
            client = TestClient(proxy.app, base_url=DASH)
            back = client.get("/auth/handoff", params={"code": "c" * 43, "state": "s" * 32,
                                                       "next": "/?model=fund&lex_hs=1"},
                              headers=FRAME, follow_redirects=False)
            once_more = client.get("/?model=fund&lex_hs=1", headers=FRAME, follow_redirects=False)
            blocked = client.get("/?model=fund&lex_hs=2", headers=FRAME, follow_redirects=False)

        self.assertEqual(back.status_code, 303)
        self.assertEqual(back.headers["location"], "/?model=fund&lex_hs=1", "the code must not be redeemed")
        self.assertEqual(once_more.status_code, 303)
        self.assertEqual(urlsplit(once_more.headers["location"]).path, "/oidc/handoff")
        self.assertEqual(blocked.status_code, 401)
        self.assertNotIn("location", blocked.headers, "no third redirect")
        self.assertIn("requestStorageAccess", blocked.text)
        self.assertIn('target="_blank"', blocked.text)

    # -- 1.513 ---------------------------------------------------------
    def test_1_513_login_required_is_told_to_the_frontend_only(self) -> None:
        """
        Scenario 1.513: no sign-in -> the frame posts the v2 `required` message to its frontend.
        Given: the app answered `error=login_required` (no sign-in, in a frame)
        When: the proxy shows the sign-in-required page
        Then: 401 with `{"type": "lex-auth", "v": 2, "event": "required", "surface": "streamlit",
              "reason": "login_required"}` posted from the frame to the frontend origin the URL
              named -- only because the instance lists it -- never to "*" and with no token; a
              frontend origin the instance does not list gets nothing (its app origins instead).
        """
        listed = "/?lex_app_origin=" + quote(APP, safe="")
        foreign = "/?lex_app_origin=" + quote("https://evil.example.net", safe="")
        with streamlit_auth("handoff"):
            client = TestClient(proxy.app, base_url=DASH)
            page = client.get("/auth/handoff", params={"error": "login_required", "state": "s" * 32,
                                                       "next": listed}, headers=FRAME)
            other = client.get("/auth/handoff", params={"error": "login_required", "state": "s" * 32,
                                                        "next": foreign}, headers=FRAME)

        def targets(text):
            return json.loads(re.search(r"var targets=(\[[^\]]*\])", text).group(1))

        self.assertEqual(page.status_code, 401)
        self.assertEqual(page.headers.get("cache-control"), "no-store")
        for fragment in ('"event": "required"', '"surface": "streamlit"', '"type": "lex-auth"', '"v": 2',
                         '"reason": "login_required"'):
            self.assertIn(fragment, page.text)
        self.assertEqual(targets(page.text), [APP], "the listed frontend origin, and only it")
        self.assertNotIn("'*'", page.text)
        self.assertNotIn('"*"', page.text)
        self.assertEqual(targets(other.text), [APP], "an unlisted origin must never be a target")

    # -- 1.514 ---------------------------------------------------------
    def test_1_514_the_streamlit_recovery_page_posts_session_ended(self) -> None:
        """
        Scenario 1.514: /auth/required is the page a dashboard whose sign-in ended navigates to.
        Given: the Streamlit process found its sign-in ended (it navigates its document there)
        When: /auth/required?reason=session_ended&next=<view> is requested; and in legacy mode
        Then: the same message with reason `session_ended`, posted from the dashboard frame
              itself; in legacy mode the route does not exist (404).
        """
        next_path = "/?model=fund&lex_app_origin=" + quote(APP, safe="")
        with streamlit_auth("handoff"):
            page = TestClient(proxy.app, base_url=DASH).get(
                "/auth/required", params={"reason": "session_ended", "next": next_path}, headers=FRAME)
        with streamlit_auth("legacy"):
            legacy = TestClient(proxy.app, base_url=DASH).get("/auth/required", headers=FRAME)

        self.assertEqual(page.status_code, 401)
        self.assertIn('"reason": "session_ended"', page.text)
        self.assertIn("window.parent.postMessage", page.text)
        self.assertEqual(legacy.status_code, 404)


class TestCluster01be_Modes(_HandoffCase):
    """1.517-1.518, 1.520: dual and handoff mode, and the proxy-owned headers."""

    # -- 1.517 ---------------------------------------------------------
    def test_1_517_dual_mode_prefers_the_handoff_and_keeps_legacy_working(self) -> None:
        """
        Scenario 1.517: dual serves both, the hand-off first.
        Given: LEX_STREAMLIT_AUTH=dual
        When: a request carries both a live handle and a legacy proxy session; one carries only
              the legacy session; one carries nothing
        Then: the handle wins (hand-off headers, no refresh token); the legacy session still signs
              in as before (with its own refresh-token header -- legacy unchanged); and with no
              credential the frame is handed off, never sent to the proxy's own Keycloak login.
        """
        self.stack.enter_context(streamlit_auth("dual"))
        user, row, handle = self._signed_in_dashboard()

        async def _legacy_tokens(_session):
            return {"access_token": _jwt("legacy", int(time.time()) + 600), "refresh_token": "rt-legacy",
                    "expires_at": int(time.time()) + 600}

        with patch.object(proxy, "_ensure_valid_access_token", _legacy_tokens), \
             patch.object(proxy, "_claims_from_token_set", lambda tokens: {"sub": "legacy"}):
            self.dash.cookies.set("session", _legacy_session_cookie(), domain=DASH_HOST)
            both = self.dash.get(DASH_PATH, headers=FRAME, follow_redirects=False)
            self.assertEqual(both.status_code, 200)
            self.assertEqual(self.upstream.values("x-streamlit-auth-method"), ["handoff"])
            self.assertEqual(self.upstream.values("x-streamlit-refresh-token"), [])

            legacy_only = TestClient(proxy.app, base_url=DASH)
            legacy_only.cookies.set("session", _legacy_session_cookie(), domain=DASH_HOST)
            self.assertEqual(legacy_only.get(DASH_PATH, headers=FRAME, follow_redirects=False).status_code, 200)
            self.assertEqual(self.upstream.values("x-streamlit-auth-method"), ["session"])
            self.assertEqual(self.upstream.values("x-streamlit-refresh-token"), ["rt-legacy"])

        nothing = TestClient(proxy.app, base_url=DASH).get(DASH_PATH, headers=FRAME, follow_redirects=False)
        self.assertEqual(nothing.status_code, 303)
        self.assertEqual(urlsplit(nothing.headers["location"]).path, "/oidc/handoff")
        self.assertNotIn("/auth/login", nothing.headers["location"])

    # -- 1.518 ---------------------------------------------------------
    def test_1_518_handoff_mode_ignores_every_legacy_credential(self) -> None:
        """
        Scenario 1.518: in handoff mode no legacy credential signs anything in.
        Given: LEX_STREAMLIT_AUTH=handoff
        When: a frame arrives with a valid-looking `auth_token`; an old frontend POSTs a token to
              /auth/adopt; /auth/login and /auth/callback are requested
        Then: the token is ignored (the frame is handed off, and the return URL drops it);
              /auth/adopt answers 200 `{"adopted": false}` without reading the body (CORS kept);
              /auth/login goes into the hand-off; /auth/callback does not exist (404).
        """
        token = _jwt("u1", int(time.time()) + 600)
        with patch.object(proxy, "validate_jwt_token", lambda t: {"sub": "u1", "exp": int(time.time()) + 600}):
            page = self.dash.get(f"/?model=fund&auth_token={token}", headers=FRAME, follow_redirects=False)
        self.assertEqual(page.status_code, 303)
        returned = parse_qs(urlsplit(page.headers["location"]).query)["return"][0]
        self.assertNotIn("auth_token", returned, "a token must never travel on through the hand-off")
        self.assertEqual(self.upstream.calls, [], "nothing may reach Streamlit on a legacy token")

        allowed = next(iter(proxy.FRONTEND_ORIGINS))
        adopt = self.dash.post("/auth/adopt", content=b"not json", headers={"origin": allowed})
        self.assertEqual((adopt.status_code, adopt.json()), (200, {"adopted": False}))
        self.assertEqual(adopt.headers.get("access-control-allow-origin"), allowed)

        login = self.dash.get("/auth/login", params={"next": "/?model=fund"}, follow_redirects=False)
        self.assertEqual((login.status_code, login.headers["location"]), (303, "/?model=fund"))
        self.assertEqual(self.dash.get("/auth/callback", follow_redirects=False).status_code, 404)

    # -- 1.520 ---------------------------------------------------------
    def test_1_520_client_supplied_identity_headers_never_reach_streamlit(self) -> None:
        """
        Scenario 1.520: identity and token headers are the proxy's alone, in every mode (BUG-037).
        Given: a signed-in client that sends its own X-Streamlit-User-ID / -Email / -Access-Token
        When: its request and its WebSocket handshake are proxied -- with a legacy session, and
              with a hand-off handle
        Then: Streamlit receives exactly one of each, the proxy's. Before, the client's copy went
              first and Streamlit's `st.context.headers` answers the first of a repeated header --
              so any signed-in user could claim another's identity, or a token of their choosing.
        """
        spoof = {"X-Streamlit-User-ID": "victim-sub", "X-Streamlit-User-Email": "victim@example.com",
                 "X-Streamlit-Access-Token": "forged", "X-Forwarded-User": "victim@example.com"}

        async def _legacy_tokens(_session):
            return {"access_token": "proxy-token", "refresh_token": "rt", "expires_at": int(time.time()) + 600}

        with streamlit_auth("legacy"), \
             patch.object(proxy, "_ensure_valid_access_token", _legacy_tokens), \
             patch.object(proxy, "_claims_from_token_set", lambda tokens: {"sub": "real-sub", "email": "real@example.com"}):
            legacy = TestClient(proxy.app, base_url=DASH)
            legacy.cookies.set("session", _legacy_session_cookie(), domain=DASH_HOST)
            legacy.get(DASH_PATH, headers={**FRAME, **spoof})
            self.assertEqual(self.upstream.values("x-streamlit-user-id"), ["real-sub"])
            # One copy of the token since LEX-898 (#879): `Authorization`, never the client's
            # forged X-Streamlit-Access-Token.
            self.assertEqual(self.upstream.values("x-streamlit-access-token"), [])
            self.assertEqual(self.upstream.values("authorization"), ["Bearer proxy-token"])
            with legacy.websocket_connect(WS_URL, headers=spoof):
                pass
            self.assertEqual(_FakeUpstreamWS.values("x-streamlit-user-id"), ["real-sub"])
            self.assertEqual(_FakeUpstreamWS.values("x-streamlit-access-token"), [])
            self.assertEqual(_FakeUpstreamWS.values("authorization"), ["Bearer proxy-token"])

        user, row, handle = self._signed_in_dashboard()
        self.dash.get(DASH_PATH, headers={**FRAME, **spoof})
        self.assertEqual(self.upstream.values("x-streamlit-user-email"), [user.email])
        self.assertEqual(self.upstream.values("x-streamlit-access-token"), [])
        with self.dash.websocket_connect(WS_URL, headers=spoof):
            pass
        self.assertEqual(_FakeUpstreamWS.values("x-streamlit-user-email"), [user.email])
        self.assertEqual(_FakeUpstreamWS.values("x-streamlit-access-token"), [])


class TestCluster01be_LegacyAndFlag(SimpleTestCase):
    """1.519, 1.521-1.522: legacy untouched; the flag refused when wrong."""

    # -- 1.519 ---------------------------------------------------------
    def test_1_519_legacy_mode_has_no_handoff_at_all(self) -> None:
        """
        Scenario 1.519: legacy mode serves nothing of the hand-off.
        Given: LEX_STREAMLIT_AUTH=legacy (the default)
        When: /auth/handoff and /auth/required are requested, and a document arrives with a
              (well-formed) handle cookie and no legacy credential
        Then: both routes are 404, and the handle is ignored: the request is answered the legacy
              way (a top-level redirect to the proxy's own login).
        """
        with streamlit_auth("legacy"), patch.object(proxy, "_ensure_jwks_ready", _no_jwks):
            client = TestClient(proxy.app, base_url=DASH)
            self.assertEqual(client.get("/auth/handoff").status_code, 404)
            self.assertEqual(client.get("/auth/required").status_code, 404)
            client.cookies.set(HANDLE, "x" * 43, domain=DASH_HOST)
            page = client.get("/dashboard", headers={"accept": "text/html", "sec-fetch-dest": "document"},
                              follow_redirects=False)
        self.assertEqual(page.status_code, 307)
        self.assertTrue(page.headers["location"].startswith("/auth/login"))

    # -- 1.521 ---------------------------------------------------------
    def test_1_521_the_flag_is_strict_at_the_proxy(self) -> None:
        """
        Scenario 1.521: LEX_STREAMLIT_AUTH is legacy, dual or handoff -- nothing else.
        Given: the flag unset, in mixed case, and misspelt
        When: the proxy is imported
        Then: unset is legacy; case does not matter; a typo refuses to start, naming the
              variable -- silently picking a mode is worse than a readable error.
        """
        self.assertEqual(_reimport_proxy_with({"LEX_STREAMLIT_AUTH": None}).STREAMLIT_AUTH_MODE, "legacy")
        handoff_proxy = _reimport_proxy_with({"LEX_STREAMLIT_AUTH": " HandOff "})
        self.assertEqual((handoff_proxy.HANDOFF_ENABLED, handoff_proxy.LEGACY_ENABLED), (True, False))
        dual = _reimport_proxy_with({"LEX_STREAMLIT_AUTH": "dual"})
        self.assertEqual((dual.HANDOFF_ENABLED, dual.LEGACY_ENABLED), (True, True))
        with self.assertRaises(RuntimeError) as caught:
            _reimport_proxy_with({"LEX_STREAMLIT_AUTH": "sometimes"})
        self.assertIn("LEX_STREAMLIT_AUTH", str(caught.exception))
        replicated = _reimport_proxy_with({"LEX_STREAMLIT_AUTH": "handoff", "LEX_PROXY_REPLICAS": "3",
                                           "REDIS_URL": None, "TOKEN_REDIS_URL": None, "SESSION_SECRET": "x"})
        self.assertIsInstance(replicated.TOKEN_STORE, replicated.MemoryTokenStore,
                              "handoff keeps no token store, so replicas need no Redis")

    # -- 1.522 ---------------------------------------------------------
    def test_1_522_lex_streamlit_refuses_what_cannot_work(self) -> None:
        """
        Scenario 1.522: `lex streamlit` validates the hand-off's environment before starting.
        Given: various LEX_STREAMLIT_AUTH / LEX_INSTANCE_HOSTS / replica configurations
        When: the pre-flight runs
        Then: a typo is refused; a deployed hand-off without an app host, or without a dashboard
              host, is refused; replicas without Redis start in handoff (no token store) but are
              still refused in dual; a deployed hand-off that cannot tell it is on https warns.
        """
        from lex.bin.lex import _warn_if_sessions_are_not_durable

        base = {"SESSION_SECRET": "fixed", "LEX_PROXY_REPLICAS": "1", "DEPLOYMENT_ENVIRONMENT": None,
                "LEX_INSTANCE_HOSTS": None, "DOMAIN_HOSTED": None, "STREAMLIT_URL": None,
                "REDIS_URL": None, "TOKEN_REDIS_URL": None, "SESSION_HTTPS_ONLY": None}

        def run(**env):
            merged = {**base, **env}
            with patch.dict(os.environ, {k: v for k, v in merged.items() if v is not None}), \
                 patch("sys.stderr", new_callable=io.StringIO) as err:
                for key, value in merged.items():
                    if value is None:
                        os.environ.pop(key, None)
                _warn_if_sessions_are_not_durable()
                return err.getvalue()

        with self.assertRaises(click.ClickException):
            run(LEX_STREAMLIT_AUTH="sometimes")
        with self.assertRaises(click.ClickException) as no_app:
            run(LEX_STREAMLIT_AUTH="handoff", DEPLOYMENT_ENVIRONMENT="DEV")
        self.assertIn("app host", no_app.exception.message)
        with self.assertRaises(click.ClickException) as no_dash:
            run(LEX_STREAMLIT_AUTH="handoff", DEPLOYMENT_ENVIRONMENT="DEV", DOMAIN_HOSTED=APP_HOST)
        self.assertIn("dashboard host", no_dash.exception.message)
        deployed = dict(DEPLOYMENT_ENVIRONMENT="DEV", LEX_INSTANCE_HOSTS=HOSTS, STREAMLIT_URL=DASH,
                        LEX_PROXY_REPLICAS="3")
        run(LEX_STREAMLIT_AUTH="handoff", **deployed)  # must not raise
        with self.assertRaises(click.ClickException):
            run(LEX_STREAMLIT_AUTH="dual", **deployed)
        warned = run(LEX_STREAMLIT_AUTH="handoff", DEPLOYMENT_ENVIRONMENT="DEV", LEX_INSTANCE_HOSTS=HOSTS,
                     STREAMLIT_URL="http://dash.internal:8501")
        self.assertIn("https", warned)


class TestCluster01be_StreamlitProcess(SimpleTestCase):
    """1.523-1.526: the Streamlit side: 401 ends, 503 never does; no refresh token."""

    def _renew(self, state, answer):
        with patch.object(sa.st, "session_state", state), \
             patch.dict(os.environ, {"LEX_INTERNAL_AUTH_SECRET": "s3cret"}), \
             patch.object(sa, "_live_headers", lambda: {"cookie": f"{HANDLE}=h"}), \
             patch.object(sa.requests, "get", answer):
            return sa.renew_access_token()

    # -- 1.523 ---------------------------------------------------------
    def test_1_523_a_401_ends_the_session_a_503_never_does(self) -> None:
        """
        Scenario 1.523: with the hand-off, /auth/token's 401 asks for recovery at once; 503 never.
        Given: a hand-off session whose token has expired
        When: the pull answers 401; or 503 (or the proxy is unreachable) for longer than the old
              grace window
        Then: 401 -> recovery is needed now; 503 / unreachable -> never the recovery page, however
              long it lasts. A legacy session keeps its grace window (unchanged).
        """
        expired = int(time.time()) - 60
        ended = _FakeState(auth_method="handoff", access_token=_jwt("u", expired), token_exp=expired)
        self.assertFalse(self._renew(ended, lambda *a, **k: httpx.Response(401, json={"error": "session_expired"})))
        with patch.object(sa.st, "session_state", ended):
            self.assertTrue(sa.needs_session_recovery(), "an ended sign-in must ask for recovery at once")

        outage = _FakeState(auth_method="handoff", access_token=_jwt("u", expired), token_exp=expired,
                            token_renewal_failed=True, renewal_failing_since=int(time.time()) - 3600)
        self.assertFalse(self._renew(outage, lambda *a, **k: httpx.Response(503, json={"error": "auth_unavailable"})))
        with patch.object(sa.st, "session_state", outage):
            self.assertFalse(sa.needs_session_recovery(), "503 must never show the recovery page")

        def _unreachable(*a, **k):
            raise ConnectionError("proxy restarting")

        self.assertFalse(self._renew(outage, _unreachable))
        with patch.object(sa.st, "session_state", outage):
            self.assertFalse(sa.needs_session_recovery())

        legacy = _FakeState(auth_method="session", token_renewal_failed=True,
                            renewal_failing_since=int(time.time()) - (sa.RENEWAL_GRACE_SECONDS + 10))
        with patch.object(sa.st, "session_state", legacy):
            self.assertTrue(sa.needs_session_recovery(), "legacy keeps the grace window")

    # -- 1.524 ---------------------------------------------------------
    def test_1_524_recovery_sends_the_dashboard_document_to_auth_required(self) -> None:
        """
        Scenario 1.524: the recovery comes from the dashboard frame itself.
        Given: a hand-off session whose sign-in ended
        When: the page renders its recovery
        Then: the dashboard document -- only when it is framed -- is replaced by
              /auth/required?reason=session_ended&next=<its URL>, and it is the dashboard
              document that navigates: the component frame rendering the recovery is sandboxed,
              and the browser refuses a sandboxed frame that navigates its parent, so the
              component only adds a script to the (same-origin) dashboard document. Nothing is
              posted from the component (the frontend only listens to the dashboard frame).
              Standalone, the page stays, with its sign-in link. A legacy session keeps the
              legacy message.
        """
        emitted = []
        with patch.object(sa.st, "session_state", _FakeState(auth_method="handoff")), \
             patch.object(sa.components, "html", lambda body, height=0: emitted.append(body)), \
             patch.object(sa.st, "warning", lambda *a, **k: None), \
             patch.object(sa.st, "markdown", lambda *a, **k: None), \
             patch.object(sa, "_current_base_url", lambda: DASH):
            sa.render_session_recovery()
        script = emitted[0]
        self.assertIn("p.document.createElement('script')", script, "the navigation must run in the dashboard document")
        self.assertNotIn("p.location", script, "a sandboxed component frame may not navigate its parent")
        self.assertIn("/auth/required", script)
        self.assertIn("reason=session_ended", script)
        self.assertNotIn("postMessage", script)

        search = "?model=fund&lex_app_origin=" + quote(APP, safe="")
        framed = _run_component_scripts(script, framed=True, search=search)
        if framed is not None:  # node runs the scripts against the sandbox rule
            self.assertEqual(framed["refused"], [], "the component never tries to navigate its parent itself")
            self.assertEqual(framed["navigated"],
                             ["/auth/required?reason=session_ended&next=" + _encode_uri_component("/" + search)])
            standalone = _run_component_scripts(script, framed=False, search=search)
            self.assertEqual(standalone["navigated"], [], "a standalone dashboard stays, with its sign-in link")

        emitted.clear()
        with patch.object(sa.st, "session_state", _FakeState(auth_method="session")), \
             patch.object(sa.components, "html", lambda body, height=0: emitted.append(body)), \
             patch.object(sa.st, "warning", lambda *a, **k: None), \
             patch.object(sa.st, "markdown", lambda *a, **k: None), \
             patch.object(sa, "_current_base_url", lambda: DASH):
            sa.render_session_recovery()
        self.assertIn("lex-auth-required", emitted[0], "legacy recovery is unchanged")

    # -- 1.525 ---------------------------------------------------------
    def test_1_525_streamlit_keeps_no_refresh_token(self) -> None:
        """
        Scenario 1.525: no refresh token reaches Streamlit's session state; sign-out goes through the app.
        Given: a handshake that (from an old proxy) still carries X-Streamlit-Refresh-Token
        When: the headers are synced; and a hand-off session renders its sign-out link
        Then: the access token is taken, the refresh token is not; the link is /oauth2/sign_out.
        """
        state = _FakeState(access_token="", token_exp=0, refresh_token="")
        fresh = _jwt("u", int(time.time()) + 600)
        with patch.object(sa.st, "session_state", state):
            sa._sync_tokens_from_headers({"x-streamlit-access-token": fresh, "x-streamlit-refresh-token": "rt"})
        self.assertEqual(state["access_token"], fresh)
        self.assertEqual(state["refresh_token"], "", "Streamlit must never hold a refresh token")

        with patch.object(sa.st, "session_state", _FakeState(auth_method="handoff")), \
             patch.object(sa, "_current_base_url", lambda: DASH), patch.object(sa, "_base_path", lambda: ""):
            self.assertTrue(sa._logout_href().startswith("/oauth2/sign_out?rd="))

    # -- 1.526 ---------------------------------------------------------
    def test_1_526_streamlit_signs_a_handoff_request_in_from_the_proxy_headers(self) -> None:
        """
        Scenario 1.526: Streamlit takes identity and token from the hand-off's headers.
        Given: a WebSocket handshake as the proxy sends it in handoff mode
        When: the app authenticates
        Then: it is signed in as that user with auth method `handoff`, holding the Authorization
              token -- and no refresh token.
        """
        token = _jwt("sub-ada", int(time.time()) + 600)
        headers = {"Authorization": f"Bearer {token}", "X-Streamlit-User-ID": "sub-ada",
                   "X-Streamlit-User-Email": "ada@example.com", "X-Streamlit-User-Username": "ada",
                   "X-Streamlit-Auth-Method": "handoff"}
        state = _FakeState(authenticated=False, access_token="", token_exp=0, refresh_token="")
        with patch.object(sa.st, "session_state", state), \
             patch.object(sa.st, "context", type("C", (), {"headers": headers})()), \
             patch.object(sa, "start_token_refresh_thread_if_needed", lambda: None), \
             patch.object(sa, "sync_keycloak_context_from_access_token", lambda: None):
            sa.authenticate_from_proxy_or_jwt()
        self.assertTrue(state["authenticated"])
        self.assertEqual((state["auth_method"], state["user_email"]), ("handoff", "ada@example.com"))
        self.assertEqual(state["access_token"], token)
        self.assertEqual(state["refresh_token"], "")


class TestCluster01be_Frames(SimpleTestCase):
    """1.527-1.528: the component frames, and where lex_view points."""

    # -- 1.527 ---------------------------------------------------------
    def test_1_527_component_frames_let_popups_escape_the_sandbox(self) -> None:
        """
        Scenario 1.527: windows opened from an embedded app page are normal windows.
        Given: the lex_view and widget-host component frames
        When: their iframe sandbox is read
        Then: both allow popups to escape the sandbox (a sign-in popup or a file must work), and
              neither allows top navigation without a gesture.
        """
        base = Path(lex_embed.__file__).resolve().parent
        for name in ("_lex_view_component", "_widget_host_component"):
            text = (base / name / "frontend" / "index.html").read_text()
            sandbox = re.search(r'<iframe id="frame"[^>]*sandbox="([^"]+)"', text, re.S).group(1).split()
            self.assertIn("allow-popups", sandbox, name)
            self.assertIn("allow-popups-to-escape-sandbox", sandbox, name)
            self.assertNotIn("allow-top-navigation", sandbox, name)

    # -- 1.528 ---------------------------------------------------------
    def test_1_528_lex_view_embeds_from_the_validated_lex_app_origin(self) -> None:
        """
        Scenario 1.528: lex_view takes its base URL from the frontend's origin, once validated.
        Given: a dashboard framed with `lex_app_origin` -- a listed custom app domain, a foreign
               origin, malformed values, and loopback (deployed and not)
        When: the origin is remembered for the session and lex_view embeds a page
        Then: a listed origin becomes the embed's base (and the only origin whose events are
              accepted); anything else is ignored and the configured frontend stays the base;
              loopback is accepted only on an instance that is not deployed.
        """
        hosts = json.dumps({"app": {"canonical": "acme.lex-cloud.io", "extra": ["app.fund.example.com"]}})
        with patch.dict(os.environ, {"LEX_INSTANCE_HOSTS": hosts, "REACT_APP_URL": "https://acme.lex-cloud.io"}):
            os.environ.pop("DEPLOYMENT_ENVIRONMENT", None)
            self.assertEqual(lex_embed.validated_lex_app_origin("https://app.fund.example.com/"),
                             "https://app.fund.example.com")
            for bad in ("https://evil.example.net", "javascript:alert(1)", "https://app.fund.example.com/x",
                        "https://user@app.fund.example.com", "", None):
                self.assertIsNone(lex_embed.validated_lex_app_origin(bad), bad)
            self.assertEqual(lex_embed.validated_lex_app_origin("http://localhost:3000"), "http://localhost:3000")
            with patch.dict(os.environ, {"DEPLOYMENT_ENVIRONMENT": "DEV"}):
                self.assertIsNone(lex_embed.validated_lex_app_origin("http://localhost:3000"))

            state = _FakeState()
            frames, components = [], []
            with patch.object(sa.st, "session_state", state), \
                 patch.object(lex_embed.components, "iframe", lambda url, **kw: frames.append(url)), \
                 patch.object(lex_embed, "render_lex_view_component",
                              lambda **kw: components.append(kw) or None):
                lex_embed.remember_lex_app_origin({"lex_app_origin": "https://evil.example.net"})
                lex_embed.lex_view("investor")
                lex_embed.remember_lex_app_origin({"lex_app_origin": "https://app.fund.example.com"})
                lex_embed.lex_view("investor")
                lex_embed.lex_view("investor", on_select=True)
        self.assertTrue(frames[0].startswith("https://acme.lex-cloud.io/investor"), frames[0])
        self.assertTrue(frames[1].startswith("https://app.fund.example.com/investor"), frames[1])
        self.assertEqual(components[0]["expected_origin"], "https://app.fund.example.com")


class TestCluster01be_Housekeeping(TestCase):
    """1.529-1.530: pruning, and nothing replayable at rest."""

    def _row(self, username: str):
        user = get_user_model().objects.create_user(username=username, email=f"{username}@example.com")
        with override_settings(**oidc_test_settings()):
            return create_sign_in(Client(), user)

    # -- 1.529 ---------------------------------------------------------
    def test_1_529_prune_dashboard_sessions_deletes_only_the_unusable(self) -> None:
        """
        Scenario 1.529: `lex prune_dashboard_sessions` removes what can never sign anything in.
        Given: expired and live codes; revoked, ended, expired-embed and long-unused bindings; and
               a live binding
        When: the command runs with --dry-run, then for real; and a sign-in row is deleted
        Then: the dry run deletes nothing; the real run deletes every unusable row and keeps the
              live code and the live binding; deleting a sign-in deletes its codes and bindings.
        """
        with override_settings(**oidc_test_settings()):
            live_row, ended_row = self._row("ada"), self._row("bob")
            now = timezone.now()
            live_code = handoff.issue_code(live_row, aud="streamlit", return_origin=DASH, state="s" * 32)
            handoff.issue_code(live_row, aud="streamlit", return_origin=DASH, state="t" * 32)
            DashboardHandoffCode.objects.exclude(code_hash=handoff.digest(live_code)).update(
                expires_at=now - timedelta(seconds=5))

            def binding(row, **fields):
                b = DashboardBinding.objects.create(handle_hash=handoff.digest(handoff.new_secret()), session=row,
                                                    aud=fields.pop("aud", "streamlit"), origin=DASH,
                                                    cookie_set_at=fields.pop("cookie_set_at", now), **fields)
                return b

            keep = binding(live_row)
            binding(live_row, revoked_at=now, revoke_reason="logout")
            binding(live_row, aud="embed", expires_at=now - timedelta(seconds=1))
            binding(live_row, cookie_set_at=now - timedelta(days=31))
            binding(ended_row)
            oidc_sessions.end_session(ended_row, "logout")

            out = io.StringIO()
            call_command("prune_dashboard_sessions", "--dry-run", stdout=out)
            self.assertIn("would delete 1 hand-off code(s) and 4 binding(s)", out.getvalue())
            self.assertEqual(DashboardBinding.objects.count(), 5)

            call_command("prune_dashboard_sessions", stdout=io.StringIO())
            self.assertEqual(list(DashboardBinding.objects.values_list("pk", flat=True)), [keep.pk])
            self.assertEqual(list(DashboardHandoffCode.objects.values_list("code_hash", flat=True)),
                             [handoff.digest(live_code)])

            OIDCSession.objects.filter(pk=live_row.pk).delete()
            self.assertFalse(DashboardBinding.objects.exists(), "bindings go with their sign-in (CASCADE)")
            self.assertFalse(DashboardHandoffCode.objects.exists())

    # -- 1.530 ---------------------------------------------------------
    def test_1_530_codes_and_handles_are_stored_as_digests_only(self) -> None:
        """
        Scenario 1.530: nothing at rest can be replayed.
        Given: a code minted and redeemed into a binding
        When: the rows are read back
        Then: the database holds only SHA-256 digests -- neither the code, the state nor the handle
              appears in any column -- and the digest of the handle finds the binding.
        """
        with override_settings(**oidc_test_settings()):
            row = self._row("ada")
            code = handoff.issue_code(row, aud="streamlit", return_origin=DASH, state="state-" + "x" * 27)
            redemption = handoff.redeem(code, aud="streamlit", origin=DASH, state="state-" + "x" * 27)

        stored = json.dumps(list(DashboardHandoffCode.objects.values()) + list(DashboardBinding.objects.values()),
                            default=str)
        for secret in (code, redemption.handle, "state-" + "x" * 27):
            self.assertNotIn(secret, stored, "a secret must never be stored in clear")
        self.assertEqual(DashboardBinding.objects.get(handle_hash=hashlib.sha256(redemption.handle.encode()).hexdigest()),
                         redemption.binding)


_PROBE_HOSTS = (
    "import json\n"
    "from lex.lex_app import settings as s\n"
    "print(json.dumps({'ALLOWED_HOSTS': s.ALLOWED_HOSTS}))\n"
)

_AUTODETECT = (
    "import json\n"
    "from lex.tools.django_bootstrap import setup_django\n"
    "setup_django()\n"
    "from django.apps import apps\n"
    "from django.db.migrations.autodetector import MigrationAutodetector\n"
    "from django.db.migrations.loader import MigrationLoader\n"
    "from django.db.migrations.state import ProjectState\n"
    "loader = MigrationLoader(None, ignore_no_migrations=True)\n"
    "changes = MigrationAutodetector(loader.project_state(), ProjectState.from_apps(apps)).changes(\n"
    "    graph=loader.graph, trim_to_apps={'authentication'})\n"
    "print(json.dumps([op.describe() for m in changes.get('authentication', []) for op in m.operations]))\n"
)


class TestCluster01be_SettingsAndMigration(TestCase):
    """1.531-1.532: Django answers on every app host; the models match their migration."""

    # -- 1.531 ---------------------------------------------------------
    def test_1_531_django_answers_on_every_app_host_the_handoff_may_use(self) -> None:
        """
        Scenario 1.531: ALLOWED_HOSTS holds every app host of LEX_INSTANCE_HOSTS.
        Given: LEX_INSTANCE_HOSTS whose app has a platform host and two custom domains (one with
               a port)
        When: the settings are imported in a fresh interpreter, and a hand-off request reaches
              Django on the custom domain
        Then: every app host is allowed (without its port) while Streamlit's host is not added; the
              custom domain gets the hand-off's answer, and an unlisted host is still refused.
        """
        env = {k: v for k, v in os.environ.items() if k not in ("LEX_INSTANCE_HOSTS", "DOMAIN_HOSTED",
                                                                "DJANGO_SETTINGS_MODULE", "PROJECT_ROOT")}
        env["PYTHONPATH"] = _CHECKOUT.as_posix()
        env["LEX_INSTANCE_HOSTS"] = json.dumps({
            "app": {"canonical": "acme.lex-cloud.io", "extra": ["app.fund.example.com", "https://Portal.Fund.example.com:8443/"]},
            "streamlit": {"canonical": "dash.fund.example.com"},
        })
        result = subprocess.run([sys.executable, "-c", _PROBE_HOSTS], cwd=_CHECKOUT, env=env,
                                capture_output=True, text=True, timeout=180)
        self.assertEqual(result.returncode, 0, result.stderr[-2000:])
        allowed = json.loads(result.stdout.strip().splitlines()[-1])["ALLOWED_HOSTS"]
        for host in ("acme.lex-cloud.io", "app.fund.example.com", "portal.fund.example.com"):
            self.assertIn(host, allowed)
        self.assertNotIn("dash.fund.example.com", allowed, "only the app's own hosts are Django's")

        with streamlit_auth("handoff", hosts=env["LEX_INSTANCE_HOSTS"]), override_settings(ALLOWED_HOSTS=allowed):
            ok = Client().get("/oidc/handoff", {"aud": "streamlit", "state": "s" * 32,
                                                 "return": "https://dash.fund.example.com/"},
                              HTTP_HOST="app.fund.example.com", secure=True, **DJANGO_FRAME)
            refused = Client().get("/oidc/handoff", {"aud": "streamlit", "state": "s" * 32,
                                                      "return": "https://dash.fund.example.com/"},
                                   HTTP_HOST="unlisted.example.org", secure=True, **DJANGO_FRAME)
        self.assertEqual(ok.status_code, 302, "the custom app domain must reach the hand-off")
        self.assertEqual(refused.status_code, 400)

    # -- 1.532 ---------------------------------------------------------
    def test_1_532_the_models_match_their_migration(self) -> None:
        """
        Scenario 1.532: `authentication` ships the migration of its models.
        Given: DashboardHandoffCode and DashboardBinding, in a fresh process set up as `lex` does
        When: Django's autodetector compares them with lex/authentication/migrations
        Then: nothing is planned -- `lex init` writes no migration into the package.
        """
        env = dict(os.environ, PYTHONPATH=_CHECKOUT.as_posix())
        result = subprocess.run([sys.executable, "-c", _AUTODETECT], cwd=os.getcwd(), env=env,
                                capture_output=True, text=True, timeout=300)
        self.assertEqual(result.returncode, 0, result.stderr[-3000:])
        self.assertEqual(json.loads(result.stdout.strip().splitlines()[-1]), [])


# ---------------------------------------------------------------------------
# security review of the hand-off (2026-11-06)
# ---------------------------------------------------------------------------
async def _legacy_session_tokens(_session):
    """A live legacy proxy session (the legacy path's token store), with its refresh token."""
    return {"access_token": _jwt("legacy", int(time.time()) + 600), "refresh_token": "rt-legacy",
            "expires_at": int(time.time()) + 600, "email": "legacy@example.com"}


def _legacy_claims(_tokens):
    return {"sub": "legacy", "email": "legacy@example.com"}


class TestCluster01be_ReviewHardening(_HandoffCase):
    """1.533-1.540: what an adversarial review of the hand-off found."""

    def _pull(self, cookie: str):
        internal = TestClient(proxy.app, base_url="http://127.0.0.1:8501")
        return internal.get("/auth/token", headers={proxy.INTERNAL_AUTH_HEADER: proxy.INTERNAL_AUTH_SECRET,
                                                    "cookie": cookie})

    # -- 1.533 ---------------------------------------------------------
    def test_1_533_a_socket_opened_by_another_site_never_reaches_streamlit(self) -> None:
        """
        Scenario 1.533: cross-site WebSocket hijacking is refused, in every mode (BUG-039).
        Given: a dashboard handed off to, and a legacy dashboard signed in with its proxy session --
               both cookies are SameSite=None, so a browser sends them with a socket any page opens
        When: a page on another site opens Streamlit's socket (Origin https://evil.example.net), and
              a sandboxed page does (Origin `null`); the dashboard's own page opens it; a client
              that is not a browser opens it without an Origin
        Then: the foreign and `null` sockets are refused with 4403 before any credential is used --
              after accept() with the hand-off (the contract), in the handshake in legacy mode --
              and nothing reaches Streamlit; the dashboard's own origin and a handshake without an
              Origin are relayed, signed in as before.
        """
        user, row, handle = self._signed_in_dashboard()
        for origin in ("https://evil.example.net", "null"):
            with self.subTest(mode="handoff", origin=origin):
                _FakeUpstreamWS.headers = []
                with self.dash.websocket_connect(WS_URL, headers={"origin": origin}) as socket:
                    with self.assertRaises(WebSocketDisconnect) as refused:
                        socket.receive_text()
                self.assertEqual(refused.exception.code, 4403)
                self.assertEqual(_FakeUpstreamWS.headers, [], "nothing may reach Streamlit")
        for origin in (DASH, None):
            with self.subTest(mode="handoff", origin=origin):
                _FakeUpstreamWS.headers = []
                with self.dash.websocket_connect(WS_URL, headers={"origin": origin} if origin else {}):
                    pass
                self.assertEqual(_FakeUpstreamWS.values("x-streamlit-user-email"), [user.email])

        with streamlit_auth("legacy"), \
             patch.object(proxy, "_ensure_valid_access_token", _legacy_session_tokens), \
             patch.object(proxy, "_claims_from_token_set", _legacy_claims):
            legacy = TestClient(proxy.app, base_url=DASH)
            legacy.cookies.set("session", _legacy_session_cookie(), domain=DASH_HOST)
            _FakeUpstreamWS.headers = []
            with self.assertRaises(WebSocketDisconnect) as closed:
                with legacy.websocket_connect(WS_URL, headers={"origin": "https://evil.example.net"}):
                    pass
            self.assertEqual(closed.exception.code, 4403)
            self.assertEqual(_FakeUpstreamWS.headers, [], "a legacy session must not be relayed either")
            with legacy.websocket_connect(WS_URL, headers={"origin": DASH}):
                pass
            self.assertEqual(_FakeUpstreamWS.values("x-streamlit-user-email"), ["legacy@example.com"])

    # -- 1.534 ---------------------------------------------------------
    def test_1_534_in_dual_a_dead_handle_never_shadows_the_legacy_session(self) -> None:
        """
        Scenario 1.534: dual -- a handle whose sign-in ended falls through to the legacy session, and goes.
        Given: LEX_STREAMLIT_AUTH=dual; a browser with a live legacy proxy session and a handle cookie
               whose sign-in has ended
        When: it loads the dashboard; Streamlit pulls /auth/token with the Cookie header it froze at
              connect (both cookies); the socket connects with both
        Then: the page is served through the legacy session and its response clears the dead handle;
              the pull renews through the legacy session (200, its token) instead of answering 401
              for the handle; the socket is relayed as the legacy user. A dead handle with no legacy
              session is still 401 `session_expired`.
        """
        self.stack.enter_context(streamlit_auth("dual"))
        user, row, handle = self._signed_in_dashboard()
        oidc_sessions.end_sessions(sid=row.sid, reason="backchannel_logout")
        legacy_cookie = _legacy_session_cookie()

        with patch.object(proxy, "_ensure_valid_access_token", _legacy_session_tokens), \
             patch.object(proxy, "_claims_from_token_set", _legacy_claims):
            self.dash.cookies.set("session", legacy_cookie, domain=DASH_HOST)
            page = self.dash.get(DASH_PATH, headers=FRAME, follow_redirects=False)
            self.assertEqual(page.status_code, 200, page.text)
            self.assertEqual(self.upstream.values("x-streamlit-auth-method"), ["session"])
            self.assertEqual(_attributes(_cookie_line(page, HANDLE)).get("max-age"), "0",
                             "the legacy answer must clear the dead handle")

            pulled = self._pull(f"session={legacy_cookie}; {HANDLE}={handle}")
            self.assertEqual(pulled.status_code, 200, pulled.text)
            self.assertEqual(pulled.json()["email"], "legacy@example.com")

            frozen = TestClient(proxy.app, base_url=DASH)
            frozen.cookies.set("session", legacy_cookie, domain=DASH_HOST)
            frozen.cookies.set(HANDLE, handle, domain=DASH_HOST)
            with frozen.websocket_connect(WS_URL):
                pass
            self.assertEqual(_FakeUpstreamWS.values("x-streamlit-auth-method"), ["session"])

        alone = self._pull(f"{HANDLE}={handle}")
        self.assertEqual((alone.status_code, alone.json()["error"]), (401, "session_expired"))

    # -- 1.535 ---------------------------------------------------------
    def test_1_535_no_redirect_or_page_target_leaves_the_dashboard_origin(self) -> None:
        """
        Scenario 1.535: a path that decodes to `//host` never becomes an off-origin target (BUG-040).
        Given: request paths `/%2Fevil.example/` and `/%5Cevil.example/` -- they decode to
               `//evil.example/` and `/\\evil.example/`, which a browser reads as another host
        When: a signed-in dashboard document arrives with the hand-off marker (its clean-URL
              redirect); a hand-off starts (the `return` it gives the app); the database is
              unavailable for a document (the 503 page's refresh target); the cookie-blocked page
              is shown (its storage-access target); and, in legacy mode, a document arrives with a
              valid `auth_token` (its strip redirect)
        Then: a browser following any of them -- Location, return URL, refresh target, script
              target -- stays on the dashboard's own origin (`/evil.example/` is a path there;
              `//evil.example/` is another host).
        """
        import html as html_lib

        def _database_down(*_a, **_k):
            raise RuntimeError("database unavailable")

        user, row, handle = self._signed_in_dashboard()
        for raw in ("/%2Fevil.example/", "/%5Cevil.example/"):
            with self.subTest(path=raw):
                marker = self.dash.get(f"{raw}?lex_hs=1", headers=FRAME, follow_redirects=False)
                self.assertEqual(marker.status_code, 303)
                self.assertTrue(_stays_on(DASH, marker.headers["location"]), marker.headers["location"])

                fresh = TestClient(proxy.app, base_url=DASH)
                started = fresh.get(raw, headers=FRAME, follow_redirects=False)
                returned = urlsplit(parse_qs(urlsplit(started.headers["location"]).query)["return"][0])
                self.assertEqual(returned.netloc, DASH_HOST)
                self.assertTrue(_stays_on(DASH, returned.path), f"the app hands back to {returned.path!r}")

                blocked = fresh.get(f"{raw}?lex_hs=2", headers=FRAME, follow_redirects=False)
                self.assertEqual(blocked.status_code, 401)
                script_target = json.loads(re.search(r"var url=(\"[^\"]*\");", blocked.text).group(1))
                link_target = html_lib.unescape(re.search(r'href="([^"]*)" target="_blank"', blocked.text).group(1))
                for target in (script_target, link_target):
                    self.assertTrue(_stays_on(DASH, target), target)

                with patch.object(proxy, "_resolve_handle_blocking", _database_down):
                    down = self.dash.get(raw, headers=FRAME, follow_redirects=False)
                self.assertEqual(down.status_code, 503)
                refresh = html_lib.unescape(re.search(r'content="\d+;url=([^"]*)"', down.text).group(1))
                self.assertTrue(_stays_on(DASH, refresh), refresh)

        claims = {"sub": "u1", "email": "u@example.com", "exp": 4_000_000_000}
        with streamlit_auth("legacy"), patch.object(proxy, "validate_jwt_token", lambda _t: claims):
            for raw in ("/%2Fevil.example/", "/%5Cevil.example/"):
                with self.subTest(mode="legacy", path=raw):
                    legacy = TestClient(proxy.app, base_url=DASH)
                    stripped = legacy.get(f"{raw}?model=fund&auth_token=bootstrap-jwt", headers=FRAME,
                                          follow_redirects=False)
                    self.assertEqual(stripped.status_code, 303)
                    self.assertTrue(_stays_on(DASH, stripped.headers["location"]), stripped.headers["location"])
                    self.assertEqual(parse_qs(urlsplit(stripped.headers["location"]).query), {"model": ["fund"]})

    # -- 1.536 ---------------------------------------------------------
    def test_1_536_a_redeem_locks_the_code_not_the_sign_in(self) -> None:
        """
        Scenario 1.536: redeeming a code takes a row lock on the code only.
        Given: a code for a signed-in user's sign-in -- whose row the library locks (FOR UPDATE)
               across a refresh, which can take as long as Keycloak does
        When: the code is redeemed
        Then: the one locking read is `FOR UPDATE OF` the code table and names no other table: the
              sign-in row is read with it but never locked, so a redeem neither queues behind a
              refresh for its read nor holds up a refresh for the rest of its transaction.
        """
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        row = sign_in(self, self.web, self._user())
        code = handoff.issue_code(row, aud=handoff.AUD_STREAMLIT, return_origin=DASH, state="s" * 32)
        with CaptureQueriesContext(connection) as captured:
            handoff.redeem(code, aud=handoff.AUD_STREAMLIT, origin=DASH, state="s" * 32)
        locking = [query["sql"] for query in captured.captured_queries if "FOR UPDATE" in query["sql"]]
        self.assertEqual(len(locking), 1, locking)
        lock_clause = locking[0].split("FOR UPDATE", 1)[1]
        self.assertIn(f'OF "{DashboardHandoffCode._meta.db_table}"', lock_clause)
        self.assertNotIn(OIDCSession._meta.db_table, lock_clause, "the sign-in row must not be locked")
        self.assertIn(OIDCSession._meta.db_table, locking[0], "the sign-in is still read with the code")

    # -- 1.537 ---------------------------------------------------------
    def test_1_537_only_an_answer_that_can_set_the_cookie_claims_its_daily_re_set(self) -> None:
        """
        Scenario 1.537: the 30-day handle cookie keeps sliding, however Streamlit renews its token.
        Given: a dashboard whose handle cookie was set more than a day ago
        When: Streamlit pulls /auth/token (a loopback call: its answer never reaches the browser) and
              the socket reconnects (a handshake cannot set a cookie); then the page is loaded
        Then: neither the pull nor the handshake records the re-set; the page's response re-sets the
              cookie for 30 days and records it.
        """
        user, row, handle = self._signed_in_dashboard()
        before = timezone.now() - timedelta(days=2)
        DashboardBinding.objects.update(cookie_set_at=before)

        pulled = self._pull(f"{HANDLE}={handle}")
        self.assertEqual(pulled.status_code, 200, pulled.text)
        with self.dash.websocket_connect(WS_URL):
            pass
        self.assertEqual(DashboardBinding.objects.get().cookie_set_at, before,
                         "an answer that cannot set the cookie must not use the re-set up")

        page = self.dash.get(DASH_PATH, headers=FRAME, follow_redirects=False)
        line = _cookie_line(page, HANDLE)
        self.assertTrue(line, "the page must re-set the cookie")
        self.assertEqual(_attributes(line).get("max-age"), str(30 * 24 * 3600))
        self.assertGreater(DashboardBinding.objects.get().cookie_set_at, timezone.now() - timedelta(minutes=1))

    # -- 1.538 ---------------------------------------------------------
    def test_1_538_the_pull_honours_the_unprefixed_name_only_on_a_plain_http_proxy(self) -> None:
        """
        Scenario 1.538: on an https dashboard the token pull ignores a cookie name any host can plant.
        Given: a dashboard handed off to, behind a proxy configured for https (STREAMLIT_URL https,
               or SESSION_HTTPS_ONLY) -- its handle lives in `__Host-lex_dash`
        When: Streamlit's pull carries a handle only under the unprefixed `lex_dash` -- a name a
              sibling host can set with `Domain=`; and under `__Host-lex_dash`
        Then: the unprefixed name is ignored (401 `not_authenticated`), the prefixed one works --
              whatever the request itself looks like. On a proxy configured for plain http, where
              the unprefixed name is the one the proxy sets, it works.
        """
        user, row, handle = self._signed_in_dashboard()
        for configured in ({"PUBLIC_IS_HTTPS": True}, {"SESSION_HTTPS_ONLY": True}):
            flags = {"PUBLIC_IS_HTTPS": False, "SESSION_HTTPS_ONLY": False, **configured}
            with self.subTest(**configured), patch.multiple(proxy, **flags):
                planted = self._pull(f"lex_dash={handle}")
                self.assertEqual((planted.status_code, planted.json()["error"]), (401, "not_authenticated"))
                self.assertEqual(self._pull(f"{HANDLE}={handle}").status_code, 200)
        with patch.multiple(proxy, PUBLIC_IS_HTTPS=False, SESSION_HTTPS_ONLY=False):
            self.assertEqual(self._pull(f"lex_dash={handle}").status_code, 200)

    # -- 1.539 ---------------------------------------------------------
    def test_1_539_a_page_whose_socket_dropped_still_learns_its_sign_in_ended(self) -> None:
        """
        Scenario 1.539: an ended sign-in ends the frame on the v2 `required` message without a
        reload -- also when only a reconnect would notice it.
        Given: a dashboard handed off to, in a page whose socket has dropped: the reconnect is refused
               with 4401, which Streamlit retries forever, and the token pull only runs while the
               socket is up
        When: the page's sign-in watcher asks /auth/status while the sign-in stands; after it ended (a
              back-channel logout -- nothing needs Keycloak to learn it); and without a handle
        Then: /auth/status answers 200 `ok`, then 401 `session_expired` (revoking the binding), and
              401 `not_authenticated` without a handle -- no-store, no cookie; 404 in legacy mode.
              Every hand-off page carries the watcher (a legacy page does not). It runs in the
              dashboard document, once however often the page re-renders, asks every minute while
              visible and as soon as the page is visible again, and on 401 replaces the page with
              /auth/required?reason=session_ended&next=<its URL> -- which posts the v2 `required`
              message to the frontend (1.514). A 404 stops it.
        """
        user, row, handle = self._signed_in_dashboard()
        live = self.dash.get("/auth/status", headers=XHR)
        self.assertEqual((live.status_code, live.json()), (200, {"status": "ok"}))
        self.assertEqual(live.headers.get("cache-control"), "no-store")
        self.assertEqual(_set_cookie_headers(live), [])
        anonymous = TestClient(proxy.app, base_url=DASH).get("/auth/status", headers=XHR)
        self.assertEqual((anonymous.status_code, anonymous.json()["error"]), (401, "not_authenticated"))

        oidc_sessions.end_sessions(sid=row.sid, reason="backchannel_logout")
        for attempt in ("this tab", "another tab"):
            with self.subTest(attempt=attempt):
                ended = self.dash.get("/auth/status", headers=XHR)
                self.assertEqual((ended.status_code, ended.json()["error"]), (401, "session_expired"))
                self.assertEqual(_set_cookie_headers(ended), [], "the status read sets no cookie")
        self.assertIsNotNone(DashboardBinding.objects.get().revoked_at)
        with streamlit_auth("legacy"):
            self.assertEqual(TestClient(proxy.app, base_url=DASH).get("/auth/status").status_code, 404)

        def rendered(auth_method: str) -> str:
            emitted = []
            with patch.object(sa.st, "session_state", _FakeState(auth_method=auth_method)), \
                 patch.object(sa.components, "html", lambda body, height=0: emitted.append(body)):
                sa.render_theme_follower()
            return emitted[0]

        watcher = sa._auth_watch_js()
        self.assertIn(watcher, rendered("handoff"), "every hand-off page carries the watcher")
        self.assertNotIn("/auth/status", rendered("session"), "a legacy page has none")
        self.assertIn("p.document.createElement('script')", watcher, "it runs in the dashboard document")
        self.assertNotIn("p.location", watcher)
        for fragment in ("/auth/status", "/auth/required", "reason=session_ended", "setInterval(check,60000)",
                         "visibilitychange"):
            self.assertIn(fragment, watcher)

        search = "?model=fund&lex_app_origin=" + quote(APP, safe="")
        expected = "/auth/required?reason=session_ended&next=" + _encode_uri_component("/" + search)
        standing = _run_component_scripts(watcher, status=200, search=search,
                                          steps=["run", "run", "tick", "event:visibilitychange"])
        if standing is not None:  # node runs the watcher against the sandbox rule
            self.assertEqual(standing["intervals"], [60000], "installed once, however often the page re-renders")
            self.assertEqual([(f["url"], f["credentials"]) for f in standing["fetched"]],
                             [("/auth/status", "same-origin")] * 2)
            self.assertEqual(standing["navigated"], [])
            gone = _run_component_scripts(watcher, status=401, search=search, steps=["run", "tick"])
            self.assertEqual((gone["navigated"], gone["refused"], gone["liveTimers"]), ([expected], [], 0))
            hidden = _run_component_scripts(watcher, status=401, search=search,
                                            steps=["run", "hide", "tick", "show", "event:visibilitychange"])
            self.assertEqual(len(hidden["fetched"]), 1, "a hidden page does not ask; visible again, it asks at once")
            self.assertEqual(hidden["navigated"], [expected])
            legacy_proxy = _run_component_scripts(watcher, status=404, steps=["run", "tick"])
            self.assertEqual((legacy_proxy["navigated"], legacy_proxy["liveTimers"]), ([], 0))

    # -- 1.540 ---------------------------------------------------------
    def test_1_540_a_deactivated_user_s_dashboard_is_signed_out(self) -> None:
        """
        Scenario 1.540: deactivating a user ends their dashboards at the next use.
        Given: a dashboard handed off to, and then its user deactivated (is_active=False) -- the
               sign-in itself still looks alive and its token is fresh
        When: Streamlit pulls /auth/token; the socket reconnects; the watcher asks /auth/status; the
              dashboard loads a document
        Then: the binding is revoked (`user_inactive`); the pull is 401 `session_expired`; the socket
              is closed with 4401; /auth/status is 401; the document starts a fresh hand-off and
              clears the handle -- and nothing more reaches Streamlit, least of all the user's token.
        """
        user, row, handle = self._signed_in_dashboard()
        get_user_model().objects.filter(pk=user.pk).update(is_active=False)
        forwarded = len(self.upstream.calls)
        _FakeUpstreamWS.headers = []

        pulled = self._pull(f"{HANDLE}={handle}")
        self.assertEqual((pulled.status_code, pulled.json()["error"]), (401, "session_expired"))
        binding = DashboardBinding.objects.get(handle_hash=handoff.digest(handle))
        self.assertEqual(binding.revoke_reason, "user_inactive")

        with self.dash.websocket_connect(WS_URL) as socket:
            with self.assertRaises(WebSocketDisconnect) as closed:
                socket.receive_text()
        self.assertEqual(closed.exception.code, 4401)
        self.assertEqual(_FakeUpstreamWS.headers, [])
        self.assertEqual(self.dash.get("/auth/status", headers=XHR).status_code, 401)

        page = self.dash.get(DASH_PATH, headers=FRAME, follow_redirects=False)
        self.assertEqual(page.status_code, 303)
        self.assertEqual(urlsplit(page.headers["location"]).path, "/oidc/handoff")
        self.assertEqual(_attributes(_cookie_line(page, HANDLE)).get("max-age"), "0")
        self.assertEqual(len(self.upstream.calls), forwarded, "nothing may reach Streamlit for a deactivated user")
