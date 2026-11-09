"""Cluster 1bg: a Reflex dashboard stays signed in as long as Keycloak keeps the session.

Intent
------
A Reflex dashboard keeps its own Keycloak tokens (plan "long-lived sessions", decision D5), and
Reflex Enterprise 0.9.7 signed people out far too often: on any refresh failure, in a two-tab loop
that refreshed once a second all day (armira, 2026-10-28), after 7 days of cookies, and never when
Keycloak ended the session through the back channel. Plan section 6.4 says what a customer may
expect instead -- the same as from Django (principles 2-4: Keycloak decides the lifetime; fail
safe, not fail-logout; one refresh in flight per sign-in):

* **at_hash** -- where the tab checks one, an ID token is checked against the access token the
  tab holds *now*: another tab's refresh is accepted, a genuinely mismatched pair is not.
* **Refresh errors** -- only ``invalid_grant`` signs the dashboard out; ``invalid_client`` and the
  other configuration errors keep the tokens (ERROR log) and back off; 5xx, 429, timeouts and
  network errors keep the tokens and back off; every refresh that keeps the tokens re-arms the
  browser's refresh timer. A userinfo 401 gets one refresh; a userinfo outage serves the profile
  the session already has -- only the same user's, and only while the access token is valid.
* **Expired and not renewable** -- once the access token has expired and Keycloak cannot renew
  it, the dashboard serves nobody: no profile, no permissions, no token for the lex API, and
  ``/login`` says sign-in is temporarily unavailable. It is not signed out: the cookies stay, the
  refresh is retried, and the next success restores the same user without a new sign-in (the
  Reflex equivalent of the HTTP contract's 503).
* **Verified before stored** -- Keycloak's answer to a refresh is verified first, with 30 s of
  clock skew allowed and Keycloak's keys fetched again (at most once a minute) for a key the
  cached set lacks; an answer that fails keeps the previous tokens.
* **One refresh per refresh token**, across tabs (process memory without Redis, swept and
  bounded) and replicas (``lex:rx:refresh:{sha256(rt)}``, held as long as the refreshing tab
  works); the result is published Fernet-encrypted for 120 s at
  ``lex:rx:refreshed:{sha256(old rt)}``, sealed for that refresh token, and the other tabs adopt
  it through ``_set_tokens`` only when it names their own user and Keycloak session.
* **Cooldown** -- a second refresh within 30 s is suppressed, never turned into a sign-out; a
  clock that stepped back suppresses nothing.
* **Cookies last 30 days**, and a reflex-enterprise other than 0.9.7 is warned about.
* **Back-channel coherence** -- Keycloak's back-channel logout records ``lex:kc_sid_ended:{sid}``
  (90 days) and the dashboard holding tokens of that session signs out at its next validation,
  audited once; tokens Keycloak issued after the logout are left alone.

A regression signs users out of dashboards during a Keycloak blip, hammers Keycloak from every
tab, keeps a dashboard signed in after the user was signed out everywhere else, or serves an
identity -- possibly another user's -- that Keycloak no longer vouches for.

Keycloak is faked at the plugin's HTTP-client boundary (discovery, keys, token and userinfo
endpoints; tokens signed with django-oauth2-codeflow's test key); the browser at the events the
dashboard sends it. The Redis scenarios run against a real Redis when
``LEX_REFLEX_REDIS_TEST_URL`` is set (e.g. ``redis://127.0.0.1:6391/0``) and are skipped otherwise;
everything else runs without Redis.

Cluster 1bg -- scenarios 1.561-1.591. Type: U (1.561-1.576, 1.580-1.591), I (1.577-1.579).
1.580-1.591 and the revised 1.566 come from the security review of 2026-11-06.
Covers: lex/lex_app/reflex/auth.py (``LexKeycloakAuthState._verify_jwt``, ``_lex_verify``,
``_validate_tokens``, ``do_access_token_refresh``, ``_refresh_access_token``, ``_lex_refresh``,
``_lex_check_token_set``, ``_lex_adopt``, ``_lex_kept``, ``_fetch_userinfo``, ``_get_userinfo``,
``start_login``, ``SignInExpiryMiddleware``, ``lex_login_page``, ``current_access_token``, the
cookie lifetime), lex/lex_app/reflex/_token_guard.py (new), lex/lex_app/reflex/backchannel.py
(new), lex/lex_app/reflex/app.py (the middleware), lex/lex_app/urls.py (the receiver's import).
Run: python -m lex pytest lex/test_project/tests/init/test_1bg_reflex_session_hardening.py -v
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import time
import unittest
import uuid
from collections import deque
from inspect import getattr_static
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import httpx
import jwt
import pytest
from asgiref.sync import async_to_sync
from django.test import SimpleTestCase, TestCase, override_settings
from joserfc.errors import InvalidClaimError
from oauth2_authcodeflow.testing import FakeOP, make_signed_token
from reflex_enterprise.auth import GenericOIDCAuthState, OIDCAuthState
from reflex_enterprise.auth.cookie import HTTPCookie
from reflex_enterprise.auth.oidc import state as oidc_state
from reflex_enterprise.auth.oidc import utils as oidc_utils
from reflex_enterprise.auth.oidc.state import AccessTokenMetadata
from reflex_enterprise.auth.oidc.utils import compute_at_hash

import lex
import lex.lex_app.reflex.auth as auth
from lex.lex_app.reflex import _token_guard as guard
from lex.lex_app.reflex import backchannel  # noqa: F401 -- importing it connects the receiver
from lex.lex_app.reflex.auth import LexKeycloakAuthState

pytestmark = pytest.mark.init

#: The realm, as django-oauth2-codeflow's FakeOP serves it -- the same Keycloak the Django side
#: of these tests talks to, signing with the same test key.
ISSUER = FakeOP().issuer
CLIENT = "lex-dashboards"
TOKEN_ENDPOINT = f"{ISSUER}/protocol/openid-connect/token"
USERINFO_ENDPOINT = f"{ISSUER}/protocol/openid-connect/userinfo"
JWKS_URI = f"{ISSUER}/protocol/openid-connect/certs"
SID = "kc-session-ada"

#: The four sign-in cookies, by the provider's attribute names.
COOKIES = ("_access_token_data", "_id_token", "_refresh_token", "_granted_scopes")
DAY = 86400

REDIS_URL_ENV = "LEX_REFLEX_REDIS_TEST_URL"


class _Answer:
    """One HTTP answer, as the plugin's client protocol reads it."""

    def __init__(self, status: int, body=None, text: str | None = None) -> None:
        self.status = status
        self._body = body
        self._text = text if text is not None else (json.dumps(body) if body is not None else "")

    async def json(self):
        if self._body is None:
            raise ValueError("not JSON")
        return self._body

    async def text(self):
        return self._text


class _Call:
    """The async context manager the plugin's client returns for a request."""

    def __init__(self, answer, delay: float = 0.0) -> None:
        self._answer = answer
        self._delay = delay

    async def __aenter__(self):
        if self._delay:
            await asyncio.sleep(self._delay)
        answer = self._answer() if callable(self._answer) else self._answer
        if isinstance(answer, BaseException):
            raise answer
        return answer

    async def __aexit__(self, *exc) -> bool:
        return False


class _Keycloak:
    """Keycloak at the plugin's HTTP-client boundary.

    Discovery and keys like a realm; the token endpoint refreshes like Keycloak unless an answer
    is scripted (a dict body, ``(status, body)``, or an exception to raise); userinfo answers the
    profile of the bearer token unless scripted. Every call is recorded.
    """

    def __init__(self) -> None:
        self.keys = FakeOP().jwks()
        self.token_calls: list[dict] = []
        self.userinfo_calls: list[str] = []
        self.token_answers: deque = deque()
        self.userinfo_answers: deque = deque()
        self.token_delay = 0.0
        self.jwks_calls = 0

    def issue(self, *, sid: str = SID, sub: str = "ada", expires_in: int = 300, iat: int | None = None,
              sign=make_signed_token) -> dict:
        """A token set as Keycloak answers a code exchange or a refresh (``iat``: when issued;
        ``sign``: the signing key's function, FakeOP's test key by default)."""
        issued = int(time.time()) if iat is None else int(iat)
        access = sign({
            "iss": ISSUER, "sub": sub, "aud": ["account"], "azp": CLIENT, "typ": "Bearer", "sid": sid,
            "iat": issued, "exp": issued + expires_in, "jti": str(uuid.uuid4()),
        })
        id_token = sign({
            "iss": ISSUER, "sub": sub, "aud": CLIENT, "azp": CLIENT, "typ": "ID", "sid": sid,
            "iat": issued, "exp": issued + expires_in, "at_hash": compute_at_hash(access, "RS256"),
            "email": f"{sub}@example.test", "name": sub.title(), "preferred_username": sub,
        })
        refresh = sign({
            "iss": ISSUER, "sub": sub, "sid": sid, "typ": "Refresh", "iat": issued,
            "exp": issued + 1800, "jti": str(uuid.uuid4()),
        })
        return {"access_token": access, "id_token": id_token, "refresh_token": refresh,
                "expires_in": expires_in, "refresh_expires_in": 1800, "token_type": "Bearer",
                "scope": "openid email profile"}

    def get(self, url: str, *, headers=None) -> _Call:
        if url == f"{ISSUER}/.well-known/openid-configuration":
            return _Call(_Answer(200, {
                "issuer": ISSUER, "authorization_endpoint": f"{ISSUER}/protocol/openid-connect/auth",
                "token_endpoint": TOKEN_ENDPOINT, "userinfo_endpoint": USERINFO_ENDPOINT, "jwks_uri": JWKS_URI,
            }))
        if url == JWKS_URI:
            self.jwks_calls += 1
            return _Call(_Answer(200, self.keys))
        if url == USERINFO_ENDPOINT:
            token = (headers or {}).get("Authorization", "").partition(" ")[2]
            self.userinfo_calls.append(token)
            if self.userinfo_answers:
                scripted = self.userinfo_answers.popleft()
                return _Call(scripted if isinstance(scripted, BaseException) else _Answer(*scripted))
            claims = jwt.decode(token, options={"verify_signature": False})
            return _Call(_Answer(200, {"sub": claims["sub"], "email": f"{claims['sub']}@example.test",
                                       "name": claims["sub"].title(), "preferred_username": claims["sub"]}))
        return _Call(_Answer(404, None, "not found"))

    def post(self, url: str, *, headers=None, data=None) -> _Call:
        assert url == TOKEN_ENDPOINT, url
        form = dict(data or {})
        self.token_calls.append(form)
        if self.token_answers:
            scripted = self.token_answers.popleft()
            if isinstance(scripted, BaseException):
                return _Call(scripted, self.token_delay)
            if isinstance(scripted, dict):
                return _Call(_Answer(200, scripted), self.token_delay)
            status, body = scripted
            return _Call(_Answer(status, body if isinstance(body, dict) else None,
                                 body if isinstance(body, str) else None), self.token_delay)
        claims = jwt.decode(form["refresh_token"], options={"verify_signature": False})
        return _Call(_Answer(200, self.issue(sid=claims["sid"], sub=claims["sub"])), self.token_delay)


class _Browser:
    """What the dashboard tells the browser (events it chains), recorded."""

    def __init__(self) -> None:
        self.events: list = []

    async def chain(self, state, events) -> None:
        self.events.extend(events if isinstance(events, (list, tuple)) else [events])

    def told_to_sign_out(self) -> bool:
        """Whether the browser was sent ``reset_auth``: drop the session and its cookies."""
        return self.sent("reset_auth")

    def sent(self, handler: str) -> bool:
        """Whether the browser was sent the provider's event ``handler``."""
        return any(getattr(getattr(event, "fn", None), "__name__", "") == handler for event in self.events)

    def redirects(self) -> list[str]:
        """Where the browser was sent (``rx.redirect`` events), in order."""
        paths = []
        for event in self.events:
            args = {str(name): getattr(value, "_var_value", value) for name, value in getattr(event, "args", ())}
            if "path" in args:
                paths.append(str(args["path"]))
        return paths


class _Clock:
    """The guard's clocks, which a test can move forward."""

    def __init__(self) -> None:
        self.offset = 0.0

    def now(self) -> float:
        return time.time() + self.offset

    def monotonic(self) -> float:
        return time.monotonic() + self.offset

    def advance(self, seconds: float) -> None:
        self.offset += seconds


def _tab(name: str, url: str = "http://localhost:8502/"):
    """One browser tab's dashboard session: a root state whose provider substate is returned."""
    from reflex.istate.data import ReflexURL, RouterData, SessionData
    from reflex.state import State

    root = State()
    root.router = RouterData(
        session=SessionData(client_token=f"tab-{name}", session_id=f"socket-{name}"),
        url=ReflexURL(url),
    )
    return root.get_substate(LexKeycloakAuthState.get_full_name().split("."))


def _store(tab, token_set: dict) -> None:
    """The tab stores a token set itself, as after its own sign-in or refresh (the plugin's path)."""

    async def store():
        await tab._set_tokens(**await tab._set_tokens_payload_from_exchange(token_set))

    async_to_sync(store)()


def _browser_jar(source, target) -> None:
    """The browser's cookie sync: ``target`` now holds the cookies ``source`` stored."""
    for name in COOKIES:
        getattr_static(LexKeycloakAuthState, name).set(target, str(getattr(source, name)), from_browser=True)


def _access_token(tab) -> str:
    async def read():
        return await tab._access_token

    return async_to_sync(read)()


def _userinfo(tab):
    """The profile the ``userinfo`` var gives -- what every event's auth gate reads."""

    async def read():
        return await tab.userinfo

    return async_to_sync(read)()


def _refresh(tab) -> auth.RefreshResult:
    return async_to_sync(tab._lex_try_refresh)()


def _cookies(tab) -> dict:
    return {name: str(getattr(tab, name)) for name in COOKIES}


#: Keycloak's authorization endpoint: a redirect there is a new sign-in.
AUTHORIZE = f"{ISSUER}/protocol/openid-connect/auth"

#: A UMA grant, as Keycloak answers it.
GRANT = [{"rsname": "lex_app.Fund", "resource_set_id": None, "scopes": ["read"]}]


def _next_event(tab) -> None:
    """What runs before every event's auth gate: lex's per-event check of the sign-in."""
    async_to_sync(auth.SignInExpiryMiddleware().preprocess)(None, tab.parent_state, None)


def _signed_in_until_expiry(case, name: str, *, url: str = "http://localhost:8502/", sub: str = "ada"):
    """A tab that signed in, resolved its profile and a UMA grant, and whose access token then
    expired: the cookie's expiry was one second (the JWTs stay verifiable), and it has passed."""
    token_set = case.keycloak.issue(sub=sub)
    token_set["expires_in"] = 1
    tab = _tab(name, url)
    _store(tab, token_set)
    with mock.patch.object(auth, "_fetch_uma_permissions", return_value=[dict(entry) for entry in GRANT]):
        case.assertEqual(_userinfo(tab)["sub"], sub, "precondition: signed in")
        case.assertEqual(async_to_sync(auth.current_permissions)(tab), GRANT, "precondition: granted")
    time.sleep(1.1)
    case.assertFalse(tab._lex_access_valid(), "precondition: the access token has expired")
    return tab


class _Fakes:
    """Keycloak faked at the HTTP boundary, the browser recorded, the guard's clocks movable, and
    Redis at ``redis_url`` (``None``: no Redis). Undone through ``test_case``'s cleanups."""

    def __init__(self, test_case, redis_url: str | None = None) -> None:
        self.keycloak = _Keycloak()
        self.browser = _Browser()
        self.clock = _Clock()
        patchers = [
            mock.patch.dict(os.environ, {
                "LEX_KEYCLOAK_ISSUER_URI": ISSUER, "LEX_KEYCLOAK_CLIENT_ID": CLIENT,
                "LEX_KEYCLOAK_CLIENT_SECRET": "dashboard-secret", "OIDC_VERIFY_SSL": "true",
            }),
            mock.patch("reflex_enterprise.auth.oidc.config.DEFAULT_HTTP_CLIENT", self.keycloak),
            mock.patch.dict(oidc_utils._OIDC_CACHE, clear=True),
            mock.patch.object(auth, "chain_event_out_of_band", self.browser.chain),
            mock.patch.object(auth, "call_event_from_computed_var", self.browser.chain),
            mock.patch.object(oidc_state, "chain_event_out_of_band", self.browser.chain),
            mock.patch.object(oidc_state, "call_event_from_computed_var", self.browser.chain),
            mock.patch.object(guard, "now", self.clock.now),
            mock.patch.object(guard, "monotonic", self.clock.monotonic),
            # The cookie sync's HTTP route lives on the running Reflex app; the event telling the
            # browser to sync is still built (and recorded above).
            mock.patch.object(HTTPCookie, "_has_registered_handlers", True),
        ]
        for patcher in patchers:
            patcher.start()
            test_case.addCleanup(patcher.stop)
        overrides = override_settings(CELERY_BROKER_URL=redis_url)
        overrides.enable()
        test_case.addCleanup(overrides.disable)
        guard.reset()
        test_case.addCleanup(guard.reset)

    def signed_in(self, name: str, token_set: dict | None = None):
        """A tab that signed in itself with ``token_set`` (a fresh one by default)."""
        tab = _tab(name)
        _store(tab, token_set or self.keycloak.issue())
        return tab

    def calls(self) -> int:
        return len(self.keycloak.token_calls)


class _ReflexSessionCase(SimpleTestCase):
    """The fakes above, no Redis unless a class says so."""

    redis_url: str | None = None

    def setUp(self) -> None:
        super().setUp()
        self.fakes = _Fakes(self, self.redis_url)
        self.keycloak, self.browser, self.clock = self.fakes.keycloak, self.fakes.browser, self.fakes.clock

    def _signed_in(self, name: str, token_set: dict | None = None):
        return self.fakes.signed_in(name, token_set)

    def _calls(self) -> int:
        return self.fakes.calls()


class TestCluster01bg_AtHash(_ReflexSessionCase):
    """1.561-1.562: an ID token is checked against the access token the tab holds now."""

    # -- 1.561 ---------------------------------------------------------
    def test_1_561_a_tab_accepts_the_tokens_another_tab_refreshed_without_refreshing_again(self) -> None:
        """
        Scenario 1.561: the two-tab at_hash loop is gone.
        Given: two tabs that each stored the first exchange themselves (each remembers its
               at_hash); tab A refreshes, and the browser's cookie sync hands tab B the new pair.
        When: tab B validates its tokens and resolves who is signed in.
        Then: the plugin alone rejects B's perfectly good pair (``at_hash``: the incident, where B
              refreshed and made A's memory stale in turn); lex's provider accepts it, B's
              profile resolves, and Keycloak's token endpoint was asked once in all -- by A.
        """
        first = self.keycloak.issue()
        tab_a, tab_b = self._signed_in("a", first), self._signed_in("b", first)
        self.assertEqual(_refresh(tab_a).outcome, "refreshed")
        _browser_jar(tab_a, tab_b)
        self.assertNotEqual(tab_b._expected_at_hash, compute_at_hash(_access_token(tab_b), "RS256"),
                            "precondition: B remembers the first exchange")

        with self.assertRaises(InvalidClaimError) as raised:
            async_to_sync(OIDCAuthState.__dict__["_verify_jwt"])(tab_b, str(tab_b._id_token))
        self.assertIn("at_hash", str(raised.exception))

        self.assertTrue(async_to_sync(tab_b._validate_tokens)(), "the other tab's refreshed pair is valid")
        self.assertEqual(_userinfo(tab_b)["sub"], "ada")
        self.assertEqual(self._calls(), 1, "one refresh in all: B did not refresh in turn")
        self.assertFalse(self.browser.told_to_sign_out())

    # -- 1.562 ---------------------------------------------------------
    def test_1_562_a_mismatched_pair_fails_and_no_check_is_invented(self) -> None:
        """
        Scenario 1.562: the at_hash check binds the ID token to the access token held now.
        Given: a tab holding the ID token of one exchange and the access token of another; and a
               tab that never stored tokens itself (it only has the browser's cookies).
        When: each validates its tokens.
        Then: the mismatched pair fails (the plugin alone compared the ID token with the at_hash
              it remembered, and accepted this pair); the cookie-only tab validates its
              consistent pair and still has no remembered at_hash -- the fix derives an
              expectation, never invents one.
        """
        mixed = self._signed_in("mixed", self.keycloak.issue())
        other = self.keycloak.issue()
        getattr_static(LexKeycloakAuthState, "_access_token_data").set(
            mixed, AccessTokenMetadata.from_exchange(other).to_cookie_value(), from_browser=True)
        self.assertFalse(async_to_sync(mixed._validate_tokens)(), "an ID token from another exchange fails")

        cookie_only = _tab("cookies")
        _browser_jar(self._signed_in("source"), cookie_only)
        self.assertIsNone(cookie_only._expected_at_hash)
        self.assertTrue(async_to_sync(cookie_only._validate_tokens)())
        self.assertIsNone(cookie_only._expected_at_hash, "no check asked for stays no check")


class TestCluster01bg_RefreshErrors(_ReflexSessionCase):
    """1.563-1.568: only ``invalid_grant`` signs the dashboard out."""

    def _due(self, name: str = "due"):
        """A tab whose access token expires within the minute, so the scheduled refresh runs."""
        return self._signed_in(name, self.keycloak.issue(expires_in=30))

    # -- 1.563 ---------------------------------------------------------
    def test_1_563_invalid_grant_signs_the_dashboard_out(self) -> None:
        """
        Scenario 1.563: Keycloak says the session is over.
        Given: a tab whose access token is due, and Keycloak answering the refresh with
               ``invalid_grant`` ("Session not active").
        When: the browser's refresh timer fires (``do_access_token_refresh``).
        Then: the browser is told to drop the session (``reset_auth``) and one INFO line names
              ``invalid_grant``; nothing is left to back off from.
        """
        tab = self._due()
        self.keycloak.token_answers.append((400, {"error": "invalid_grant", "error_description": "Session not active"}))
        with self.assertLogs("lex.lex_app.reflex.auth", logging.INFO) as logged:
            async_to_sync(tab.do_access_token_refresh)()
        self.assertTrue(self.browser.told_to_sign_out(), "the sign-in is over")
        self.assertTrue(any("invalid_grant" in line and "INFO" in line for line in logged.output), logged.output)
        self.assertEqual(guard.retry_in(str(tab._refresh_token)), 0.0)

    # -- 1.564 ---------------------------------------------------------
    def test_1_564_a_misconfigured_client_keeps_the_tokens_logs_an_error_and_backs_off(self) -> None:
        """
        Scenario 1.564: ``invalid_client`` is a configuration problem, not the user's.
        Given: a tab whose access token is due; Keycloak answers the refresh with a configuration
               error -- ``invalid_client``, ``unauthorized_client``, ``invalid_scope``,
               ``invalid_request``, or an ``invalid_grant`` about the issuer.
        When: the refresh timer fires, then fires again a minute later, then after 5 minutes.
        Then: the tokens are kept (the cookies are unchanged, the browser is not told to sign
              out), one ERROR names the error and the client settings to check, the second
              timer asks Keycloak nothing (back-off), the third asks again.
        """
        errors = {
            "invalid_client": (401, {"error": "invalid_client", "error_description": "Invalid client credentials"}),
            "unauthorized_client": (400, {"error": "unauthorized_client"}),
            "invalid_scope": (400, {"error": "invalid_scope"}),
            "invalid_request": (400, {"error": "invalid_request"}),
            "issuer": (400, {"error": "invalid_grant", "error_description": "Invalid token issuer. Expected 'x'"}),
        }
        for what, answer in errors.items():
            with self.subTest(error=what):
                guard.reset()
                self.browser.events.clear()
                tab = self._due(what)
                kept, calls = _cookies(tab), self._calls()
                self.keycloak.token_answers.append(answer)
                with self.assertLogs("lex.lex_app.reflex.auth", logging.ERROR) as logged:
                    async_to_sync(tab.do_access_token_refresh)()
                self.assertEqual(_cookies(tab), kept, "the tokens are kept")
                self.assertFalse(self.browser.told_to_sign_out())
                self.assertIn("OIDC_RP_CLIENT_SECRET", logged.output[0])
                self.assertEqual(self._calls(), calls + 1)
                self.clock.advance(60)
                async_to_sync(tab.do_access_token_refresh)()
                self.assertEqual(self._calls(), calls + 1, "backing off: Keycloak was not asked again")
                self.clock.advance(300)
                async_to_sync(tab.do_access_token_refresh)()
                self.assertEqual(self._calls(), calls + 2, "after the back-off it asks again")
                self.assertFalse(self.browser.told_to_sign_out())

    # -- 1.565 ---------------------------------------------------------
    def test_1_565_an_unreachable_keycloak_keeps_the_tokens_and_backs_off(self) -> None:
        """
        Scenario 1.565: a Keycloak or network blip never signs anyone out.
        Given: a tab whose access token is due; Keycloak answers 503 (an HTML page), 502
               ``temporarily_unavailable``, 429, or not at all (connect timeout, connection refused).
        When: the refresh timer fires; again within the back-off; again after it, and once more.
        Then: the tokens are kept and the browser is never told to sign out; a WARNING says so;
              within the back-off Keycloak is not asked; the back-off doubles (5 s, then 10 s);
              once Keycloak answers again the tab refreshes and the back-off is cleared.
        """
        failures = {
            "503 page": (503, "<html>Service Unavailable</html>"),
            "502 temporarily_unavailable": (502, {"error": "temporarily_unavailable"}),
            "429": (429, "Too Many Requests"),
            "connect timeout": httpx.ConnectTimeout("timed out"),
            "connection refused": httpx.ConnectError("refused"),
        }
        for what, failure in failures.items():
            with self.subTest(failure=what):
                guard.reset()
                self.browser.events.clear()
                tab = self._due(what)
                kept, calls = _cookies(tab), self._calls()
                self.keycloak.token_answers.extend([failure, failure])
                with self.assertLogs("lex.lex_app.reflex.auth", logging.WARNING) as logged:
                    async_to_sync(tab.do_access_token_refresh)()
                self.assertEqual(_cookies(tab), kept, "the tokens are kept")
                self.assertIn("next attempt in 5 s", logged.output[0])
                async_to_sync(tab.do_access_token_refresh)()
                self.assertEqual(self._calls(), calls + 1, "within the back-off Keycloak is not asked")
                self.clock.advance(6)
                with self.assertLogs("lex.lex_app.reflex.auth", logging.WARNING) as again:
                    async_to_sync(tab.do_access_token_refresh)()
                self.assertIn("next attempt in 10 s", again.output[0], "the back-off doubles")
                self.clock.advance(11)
                async_to_sync(tab.do_access_token_refresh)()
                self.assertEqual(self._calls(), calls + 3)
                self.assertNotEqual(_cookies(tab)["_access_token_data"], kept["_access_token_data"],
                                    "Keycloak answered again: new tokens")
                self.assertEqual(guard.retry_in(str(tab._refresh_token)), 0.0)
                self.assertFalse(self.browser.told_to_sign_out(), "never a sign-out")

    # -- 1.566 ---------------------------------------------------------
    def test_1_566_an_expired_sign_in_keycloak_cannot_renew_serves_nobody_and_stays_signed_in(self) -> None:
        """
        Scenario 1.566: the Reflex equivalent of the HTTP contract's 503.
        Given: a signed-in tab with its profile and a UMA grant cached; its access token then
               expires while every refresh fails -- Keycloak unreachable, or the client refused
               as misconfigured (a secret rotation gone wrong).
        When: the next event resolves who is signed in (lex's per-event check, then the gate's
              profile), what the user may do, and the token for the lex API.
        Then: nobody -- no profile, no permissions (``has_permission`` refuses), no access token
              -- yet not signed out: the cookies are kept, the browser is never told to drop them,
              the tab reports the sign-in as temporarily unavailable and the refresh is retried.
              When Keycloak answers again, the next refresh restores the same user without a new
              sign-in. (Until review, this scenario served the cached profile indefinitely.)
        """
        failures = {
            "unreachable": httpx.ConnectError("refused"),
            "misconfigured": (401, {"error": "invalid_client", "error_description": "Invalid client credentials"}),
        }
        for what, failure in failures.items():
            with self.subTest(failure=what):
                guard.reset()
                self.browser.events.clear()
                tab = _signed_in_until_expiry(self, what)
                kept = _cookies(tab)
                self.keycloak.token_answers.append(failure)
                _next_event(tab)
                self.assertEqual(async_to_sync(auth.current_permissions)(tab), [],
                                 "no permissions -- also not the grant cached for the expired token")
                self.assertFalse(async_to_sync(auth.has_permission("lex_app.Fund", "read"))(
                    SimpleNamespace(auth_user_state=tab)), "has_permission refuses")
                self.assertEqual(async_to_sync(auth.current_access_token)(tab), "", "no token for the lex API")
                self.assertIsNone(_userinfo(tab), "no identity on an expired access token")
                self.assertEqual(async_to_sync(auth.current_permissions)(tab), [], "still no permissions")
                self.assertEqual(_cookies(tab), kept, "the cookies are kept")
                self.assertFalse(self.browser.told_to_sign_out(), "not signed out")
                self.assertTrue(tab.sign_in_unavailable)
                self.assertTrue(self.browser.sent("trigger_access_token_refresh"), "the refresh is retried")

                self.clock.advance(400)  # past the back-off; Keycloak answers again
                async_to_sync(tab.do_access_token_refresh)()
                self.assertFalse(tab.sign_in_unavailable)
                self.assertEqual(_userinfo(tab)["sub"], "ada", "the same user, back")
                self.assertTrue(async_to_sync(auth.current_access_token)(tab))
                self.assertNotIn(AUTHORIZE, " ".join(self.browser.redirects()), "no new sign-in")

    # -- 1.567 ---------------------------------------------------------
    def test_1_567_a_userinfo_401_gets_one_refresh_and_one_retry(self) -> None:
        """
        Scenario 1.567: Keycloak refuses the access token at userinfo.
        Given: a signed-in tab; Keycloak's userinfo answers 401 once.
        When: the profile is resolved.
        Then: exactly one refresh (with the tab's refresh token), and one retry of userinfo with
              the new access token, whose profile is the answer; no sign-out.
        """
        tab = self._signed_in("a")
        refresh_token = str(tab._refresh_token)
        self.keycloak.userinfo_answers.append((401, {"error": "invalid_token"}))
        self.assertEqual(_userinfo(tab)["sub"], "ada")
        self.assertEqual(self._calls(), 1, "one refresh")
        self.assertEqual(self.keycloak.token_calls[0]["refresh_token"], refresh_token)
        self.assertEqual(len(self.keycloak.userinfo_calls), 2, "one retry")
        self.assertEqual(self.keycloak.userinfo_calls[1], _access_token(tab), "with the new access token")
        self.assertFalse(self.browser.told_to_sign_out())

    # -- 1.568 ---------------------------------------------------------
    def test_1_568_a_userinfo_outage_serves_the_profile_the_session_has(self) -> None:
        """
        Scenario 1.568: Keycloak's userinfo answers 5xx or not at all.
        Given: a tab whose profile was resolved once, then refreshed (so the profile is looked up
               again); a new tab with no profile yet; Keycloak's userinfo failing for both.
        When: each resolves its profile.
        Then: the first is served its cached profile, the second the claims of its verified ID
              token; neither refreshes nor signs out.
        """
        tab = self._signed_in("a")
        self.assertEqual(_userinfo(tab)["name"], "Ada")
        self.assertEqual(_refresh(tab).outcome, "refreshed")
        calls = self._calls()
        self.keycloak.userinfo_answers.extend([(503, "<html>down</html>"), httpx.ReadTimeout("slow")])
        self.assertEqual(_userinfo(tab)["name"], "Ada", "the cached profile")

        fresh = self._signed_in("b")
        profile = _userinfo(fresh)
        self.assertEqual((profile["sub"], profile["email"]), ("ada", "ada@example.test"), "the ID token's claims")
        self.assertEqual(self._calls(), calls, "an outage is not a reason to refresh")
        self.assertFalse(self.browser.told_to_sign_out())


class TestCluster01bg_SingleFlight(_ReflexSessionCase):
    """1.569-1.571: one refresh per refresh token; the others adopt it (no Redis here)."""

    # -- 1.569 ---------------------------------------------------------
    def test_1_569_tabs_refreshing_at_once_make_one_call_and_end_with_the_same_tokens(self) -> None:
        """
        Scenario 1.569: one refresh in flight per sign-in, across a process's tabs.
        Given: three tabs holding the same tokens; Keycloak takes 0.3 s to answer.
        When: all three refresh at the same moment.
        Then: Keycloak's token endpoint is called once; one tab refreshed and the others adopted
              its result through the plugin's ``_set_tokens`` -- all hold the same new access
              token, and each remembers the at_hash of the pair it now holds.
        """
        first = self.keycloak.issue()
        tabs = [self._signed_in(name, first) for name in ("a", "b", "c")]
        self.keycloak.token_delay = 0.3

        async def together():
            return await asyncio.gather(*(tab._lex_try_refresh() for tab in tabs))

        results = async_to_sync(together)()
        self.assertEqual(self._calls(), 1, "one call to Keycloak")
        self.assertEqual(sorted(result.outcome for result in results), ["adopted", "adopted", "refreshed"])
        tokens = {_access_token(tab) for tab in tabs}
        self.assertEqual(len(tokens), 1, "every tab holds the new access token")
        self.assertNotIn(AccessTokenMetadata.from_exchange(first).access_token, tokens)
        for tab in tabs:
            self.assertEqual(tab._expected_at_hash, compute_at_hash(_access_token(tab), "RS256"))

    # -- 1.570 ---------------------------------------------------------
    def test_1_570_a_tab_with_the_old_refresh_token_adopts_the_published_set_for_120_s(self) -> None:
        """
        Scenario 1.570: the published token set is adopted, for 120 seconds.
        Given: tab A refreshed; tab B still holds the old tokens (its browser has not synced).
        When: B refreshes 100 s later; tab C, with the old tokens too, refreshes 130 s later.
        Then: B adopts A's set without calling Keycloak; C, after the set expired, asks Keycloak.
        """
        first = self.keycloak.issue()
        tab_a, tab_b, tab_c = (self._signed_in(name, first) for name in ("a", "b", "c"))
        self.assertEqual(_refresh(tab_a).outcome, "refreshed")
        self.clock.advance(100)
        self.assertEqual(_refresh(tab_b).outcome, "adopted")
        self.assertEqual(_access_token(tab_b), _access_token(tab_a))
        self.assertEqual(self._calls(), 1)
        self.clock.advance(30)
        self.assertEqual(_refresh(tab_c).outcome, "refreshed")
        self.assertEqual(self._calls(), 2, "the published set expired after 120 s")

    # -- 1.571 ---------------------------------------------------------
    def test_1_571_when_the_refreshing_tab_fails_the_others_keep_their_tokens(self) -> None:
        """
        Scenario 1.571: a failed refresh in flight costs the waiting tabs nothing.
        Given: two tabs with the same tokens; Keycloak answers the one refresh with 503 slowly.
        When: both refresh at once, then the waiting one asks again.
        Then: one call; the waiting tab keeps its tokens (no sign-out) and, backing off with the
              failed refresh token, does not call Keycloak again.
        """
        first = self.keycloak.issue()
        tab_a, tab_b = self._signed_in("a", first), self._signed_in("b", first)
        kept = _cookies(tab_b)
        self.keycloak.token_delay = 0.3
        self.keycloak.token_answers.append((503, "<html>down</html>"))

        async def together():
            return await asyncio.gather(tab_a._lex_try_refresh(), tab_b._lex_try_refresh())

        results = async_to_sync(together)()
        self.assertEqual(sorted(result.outcome for result in results), ["lock_wait", "transient"])
        self.assertEqual(_cookies(tab_b), kept)
        self.assertEqual(_refresh(tab_b).outcome, "backoff")
        self.assertEqual(self._calls(), 1)
        self.assertFalse(self.browser.told_to_sign_out())


class TestCluster01bg_Cooldown(_ReflexSessionCase):
    """1.572: a second refresh within 30 s is suppressed."""

    # -- 1.572 ---------------------------------------------------------
    def test_1_572_a_second_refresh_within_30_s_is_suppressed_not_a_sign_out(self) -> None:
        """
        Scenario 1.572: the cooldown.
        Given: a tab that just refreshed.
        When: it asks for another refresh 10 s later (the scheduled path and a userinfo-driven
              one), then 31 s after the first.
        Then: within 30 s Keycloak is not asked, the tokens are kept and nobody is signed out;
              after 30 s the tab refreshes normally.
        """
        tab = self._signed_in("a")
        self.assertEqual(_refresh(tab).outcome, "refreshed")
        kept = _cookies(tab)
        self.clock.advance(10)
        with self.assertLogs("lex.lex_app.reflex.auth", logging.INFO) as logged:
            result = _refresh(tab)
        self.assertEqual(result.outcome, "suppressed")
        self.assertEqual(result.token, _access_token(tab), "the caller gets the current token, not a sign-out")
        self.assertIn("suppressed", logged.output[0])
        self.assertEqual(async_to_sync(tab._refresh_access_token)(None), _access_token(tab))
        self.assertEqual((_cookies(tab), self._calls()), (kept, 1))
        self.clock.advance(21)
        self.assertEqual(_refresh(tab).outcome, "refreshed")
        self.assertEqual(self._calls(), 2)
        self.assertFalse(self.browser.told_to_sign_out())


class TestCluster01bg_CookiesAndVersion(SimpleTestCase):
    """1.573-1.574: 30-day cookies for lex's provider only; the supported plugin release."""

    # -- 1.573 ---------------------------------------------------------
    def test_1_573_lex_provider_cookies_last_30_days_and_the_plugins_own_do_not_change(self) -> None:
        """
        Scenario 1.573: cookie lifetime 30 days.
        Given: lex's provider class, created while the plugin's cookie settings were patched.
        When: its four sign-in cookies' ``max_age`` (what the cookie sync sends as Max-Age) are
              read, and the plugin's own provider's.
        Then: lex's last 30 days; the plugin's provider and the plugin's module settings keep
              their 7 days (the patch was scoped to lex's class); the post-import check accepts
              lex's class and logs an ERROR for a class whose cookies are short.
        """
        self.assertEqual(guard.cookie_lifetimes(LexKeycloakAuthState),
                         {name: 30 * DAY for name in COOKIES})
        self.assertEqual(guard.cookie_lifetimes(GenericOIDCAuthState), {name: 7 * DAY for name in COOKIES})
        for name in guard.PLUGIN_COOKIES:
            self.assertEqual(getattr(oidc_state, name).max_age, 7 * DAY, f"{name} is the plugin's own again")
        self.assertTrue(guard.check_cookie_lifetime(LexKeycloakAuthState))
        with self.assertLogs("lex.lex_app.reflex._token_guard", logging.ERROR):
            self.assertFalse(guard.check_cookie_lifetime(GenericOIDCAuthState))

    # -- 1.574 ---------------------------------------------------------
    def test_1_574_another_reflex_enterprise_release_is_warned_about(self) -> None:
        """
        Scenario 1.574: the overrides know which plugin they were written for.
        Given: the supported release (0.9.7) and requirements.txt.
        When: the version check sees 0.9.7, then 0.9.8.
        Then: 0.9.7 passes silently and is the release requirements.txt pins; 0.9.8 logs a
              WARNING naming both releases and the module to check.
        """
        self.assertTrue(guard.check_plugin_version(guard.SUPPORTED_REFLEX_ENTERPRISE))
        requirements = (Path(lex.__file__).resolve().parents[1] / "requirements.txt").read_text()
        self.assertIn(f"reflex-enterprise=={guard.SUPPORTED_REFLEX_ENTERPRISE}", requirements)
        with self.assertLogs("lex.lex_app.reflex._token_guard", logging.WARNING) as logged:
            self.assertFalse(guard.check_plugin_version("0.9.8"))
        self.assertIn("0.9.8", logged.output[0])
        self.assertIn("0.9.7", logged.output[0])
        self.assertIn("lex/lex_app/reflex/auth.py", logged.output[0])


class TestCluster01bg_RedisFallback(_ReflexSessionCase):
    """1.575: Redis configured but not answering: the process still makes one refresh."""

    redis_url = "redis://127.0.0.1:1/0"  # nothing listens on port 1

    # -- 1.575 ---------------------------------------------------------
    def test_1_575_without_redis_tabs_still_share_one_refresh_and_it_is_said_once(self) -> None:
        """
        Scenario 1.575: the local-lock fallback.
        Given: Redis configured (the instance's broker URL) but not answering; two tabs with
               the same tokens.
        When: both refresh at once, twice over (two token lifetimes).
        Then: each time one call to Keycloak and both tabs on the new tokens; one WARNING says
              Redis did not answer -- not one per operation.
        """
        first = self.keycloak.issue()
        self.keycloak.token_delay = 0.2
        with self.assertLogs("lex.lex_app.reflex._token_guard", logging.WARNING) as logged:
            # Signing in already reads the shared store (the back-channel marker check).
            tab_a, tab_b = self._signed_in("a", first), self._signed_in("b", first)

            async def together():
                return await asyncio.gather(tab_a._lex_try_refresh(), tab_b._lex_try_refresh())

            async_to_sync(together)()
            self.clock.advance(60)
            async_to_sync(together)()
        self.assertEqual(self._calls(), 2, "one call per round")
        self.assertEqual(_access_token(tab_a), _access_token(tab_b))
        warnings = [line for line in logged.output if line.startswith("WARNING")]
        self.assertEqual(len(warnings), 1, logged.output)
        self.assertIn("Redis did not answer", warnings[0])
        self.assertEqual(guard.shared_backend(), "local")


def _redis_test_url() -> str:
    """The opt-in Redis (``LEX_REFLEX_REDIS_TEST_URL``); set but unreachable is a failure."""
    url = os.getenv(REDIS_URL_ENV, "")
    if not url:
        raise unittest.SkipTest(f"Real-Redis scenarios are opt-in: set {REDIS_URL_ENV}=redis://127.0.0.1:6391/0.")
    import redis

    try:
        redis.Redis.from_url(url, socket_connect_timeout=1, socket_timeout=1).ping()
    except Exception as exc:  # opted in, so an unreachable Redis is an outright failure
        raise AssertionError(f"{REDIS_URL_ENV}={url!r} is set but Redis does not answer: {exc}") from exc
    return url


class TestCluster01bg_AcrossReplicas(_ReflexSessionCase):
    """1.576: Redis carries the lock and the published set between replicas."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.redis_url = _redis_test_url()
        super().setUpClass()

    def setUp(self) -> None:
        super().setUp()
        import redis

        self.redis = redis.Redis.from_url(self.redis_url)
        for key in self.redis.scan_iter("lex:*"):
            self.redis.delete(key)
        self.addCleanup(lambda: [self.redis.delete(key) for key in self.redis.scan_iter("lex:*")])

    # -- 1.576 ---------------------------------------------------------
    def test_1_576_replicas_share_the_lock_and_adopt_the_encrypted_set_from_redis(self) -> None:
        """
        Scenario 1.576: one refresh per refresh token across replicas.
        Given: Redis shared by the replicas; tabs on replica 1 and replica 2 hold the same
               tokens (each replica has its own process memory).
        When: both refresh at once; afterwards a third tab on another replica, still holding the
              old tokens, refreshes.
        Then: one call to Keycloak; the set is in Redis at ``lex:rx:refreshed:{sha256(old rt)}``
              for at most 120 s, Fernet-encrypted (no token in clear); the lock
              ``lex:rx:refresh:{sha256(rt)}`` is released; the third tab adopts it from Redis.
        """
        first = self.keycloak.issue()
        tab_a, tab_b, tab_c = (self._signed_in(name, first) for name in ("a", "b", "c"))
        self.assertEqual(guard.shared_backend(), "redis")
        self.keycloak.token_delay = 0.3

        async def replica(tab, other_process_memory: bool):
            if other_process_memory:
                guard._LOCAL.clear()
            return await tab._lex_try_refresh()

        async def together():
            return await asyncio.gather(replica(tab_a, False), replica(tab_b, True))

        results = async_to_sync(together)()
        self.assertEqual(self._calls(), 1)
        self.assertEqual(sorted(result.outcome for result in results), ["adopted", "refreshed"])
        old = first["refresh_token"]
        digest = hashlib.sha256(old.encode()).hexdigest()
        sealed = self.redis.get(f"lex:rx:refreshed:{digest}")
        self.assertIsNotNone(sealed, "published under the frozen key")
        self.assertTrue(0 < self.redis.pttl(f"lex:rx:refreshed:{digest}") <= 120_000)
        self.assertTrue(sealed.startswith(b"gAAAAA"), "a Fernet token")
        for token in (_access_token(tab_a), str(tab_a._refresh_token), old):
            self.assertNotIn(token.encode(), sealed)
        self.assertIsNone(self.redis.get(f"lex:rx:refresh:{digest}"), "the lock is released")

        guard._LOCAL.clear()  # tab C's replica never saw any of it
        self.assertEqual(_refresh(tab_c).outcome, "adopted")
        self.assertEqual(self._calls(), 1)
        self.assertEqual(_access_token(tab_c), _access_token(tab_a))


class TestCluster01bg_BackChannel(TestCase):
    """1.577-1.579: Keycloak's back-channel logout reaches the dashboards."""

    redis_url: str | None = None

    def setUp(self) -> None:
        super().setUp()
        # The Reflex side of the test, as in the other classes.
        self.reflex = _Fakes(self, self.redis_url)
        self.keycloak, self.browser = self.reflex.keycloak, self.reflex.browser
        # The Django side: FakeOP is the same realm, so its logout tokens validate.
        self.op = FakeOP()
        overrides = override_settings(**self.op.django_settings(), OIDC_RP_CLIENT_ID="lex-backend")
        overrides.enable()
        self.addCleanup(overrides.disable)
        self.op.install()
        self.addCleanup(self.op.uninstall)

    def _logout(self, **claims) -> None:
        """Keycloak posts a back-channel logout to the instance (no Django sign-in matches)."""
        response = self.client.post("/oidc/backchannel-logout", {"logout_token": self.op.logout_token(**claims)})
        self.assertEqual(response.status_code, 200, response.content[:300])

    # -- 1.577 ---------------------------------------------------------
    def test_1_577_a_back_channel_logout_signs_out_the_dashboards_of_that_session(self) -> None:
        """
        Scenario 1.577: an admin ends Ada's Keycloak session; her dashboards follow.
        Given: Ada's dashboard tab (Keycloak session ``kc-session-ada``) and Bob's (another).
        When: Keycloak posts the back-channel logout for Ada's session -- no Django sign-in
              matches it -- and then Ada's tab validates its tokens, and another of her tabs
              runs its scheduled refresh.
        Then: ``lex:kc_sid_ended:kc-session-ada`` is recorded; Ada's tabs are signed out (tokens
              cleared, the browser told to drop them) without asking Keycloak; Bob's tab is
              untouched.
        """
        ada = self.reflex.signed_in("ada", self.keycloak.issue(sid="kc-session-ada"))
        ada_due = self.reflex.signed_in("ada-2", self.keycloak.issue(sid="kc-session-ada", expires_in=30))
        bob = self.reflex.signed_in("bob", self.keycloak.issue(sid="kc-session-bob", sub="bob"))
        self.assertTrue(async_to_sync(ada._validate_tokens)(), "signed in before the logout")

        self._logout(sid="kc-session-ada")
        self.assertTrue(guard.sid_ended("kc-session-ada"), "recorded for the dashboards")

        self.assertFalse(async_to_sync(ada._validate_tokens)())
        self.assertFalse(async_to_sync(ada._has_any_token_present)(), "the tab's tokens are gone")
        self.assertTrue(self.browser.told_to_sign_out())
        async_to_sync(ada_due.do_access_token_refresh)()
        self.assertFalse(async_to_sync(ada_due._has_any_token_present)())
        self.assertEqual(self.keycloak.token_calls, [], "nobody asked Keycloak")
        self.assertTrue(async_to_sync(bob._validate_tokens)(), "another session is untouched")

    # -- 1.578 ---------------------------------------------------------
    def test_1_578_tokens_issued_after_the_logout_and_logouts_without_sid_are_left_alone(self) -> None:
        """
        Scenario 1.578: no sign-out loop from a stale marker.
        Given: a back-channel logout of ``kc-session-ada`` two minutes ago.
        When: a tab signs in again on the same Keycloak session id (tokens issued now -- Keycloak
              honours the session again); and Keycloak posts a logout naming only a user (no sid).
        Then: the new tab validates; the logout without sid records nothing (that user's
              dashboards end at their next refresh, which Keycloak refuses).
        """
        self.reflex.clock.advance(-120)
        self._logout(sid="kc-session-ada")
        self.reflex.clock.advance(120)
        again = self.reflex.signed_in("ada", self.keycloak.issue(sid="kc-session-ada"))
        self.assertTrue(guard.sid_ended("kc-session-ada", issued_at=time.time() - 300), "older tokens are over")
        self.assertTrue(async_to_sync(again._validate_tokens)(), "tokens issued after the logout are not")
        self.assertFalse(self.browser.told_to_sign_out())

        with self.assertLogs("lex.lex_app.reflex.backchannel", logging.DEBUG) as logged:
            self._logout(sub="bob")
        self.assertIn("without sid", logged.output[0])
        self.assertEqual(guard._LOCAL._values.keys() - {guard.sid_ended_key("kc-session-ada")}, set())

    # -- 1.579 ---------------------------------------------------------
    def test_1_579_the_marker_is_in_the_shared_redis_for_90_days(self) -> None:
        """
        Scenario 1.579: the marker reaches every Reflex replica.
        Given: Redis shared by the instance's pods (the Django pod receives the logout, the
               Reflex pods read the marker).
        When: Keycloak posts the back-channel logout of ``kc-session-ada``.
        Then: another Redis client -- another process -- reads ``lex:kc_sid_ended:kc-session-ada``,
              kept for 90 days, holding the time of the logout.
        """
        url = _redis_test_url()
        import redis

        settings_override = override_settings(CELERY_BROKER_URL=url)
        settings_override.enable()
        self.addCleanup(settings_override.disable)
        guard.reset()
        other_process = redis.Redis.from_url(url)
        other_process.delete("lex:kc_sid_ended:kc-session-ada")
        self.addCleanup(other_process.delete, "lex:kc_sid_ended:kc-session-ada")

        before = time.time()
        self._logout(sid="kc-session-ada")
        marker = other_process.get("lex:kc_sid_ended:kc-session-ada")
        self.assertIsNotNone(marker)
        self.assertGreaterEqual(float(marker), before - 1)
        ttl = other_process.ttl("lex:kc_sid_ended:kc-session-ada")
        self.assertTrue(90 * DAY - 60 <= ttl <= 90 * DAY, ttl)


# --- review fixes (security review, 2026-11-06) -----------------------------------------
class _Audits:
    """The plugin's audit sink (``emit_audit``), recorded: ``(action, outcome, reason)`` each."""

    def __init__(self) -> None:
        self.entries: list[tuple[str, str, str | None]] = []

    async def emit(self, state, action, outcome, **kwargs) -> None:
        self.entries.append((getattr(action, "value", action), getattr(outcome, "value", outcome), kwargs.get("reason")))

    def expired(self) -> list[str | None]:
        return [reason for action, _, reason in self.entries if action == "session_expired"]


def _rotated_key():
    """A second RSA signing key, published by Keycloak under kid ``rotated``: (sign, jwk)."""
    from cryptography.hazmat.primitives.asymmetric import rsa
    from jwt.algorithms import RSAAlgorithm

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public = json.loads(RSAAlgorithm.to_jwk(key.public_key()))
    public.update({"kid": "rotated", "use": "sig", "alg": "RS256"})
    return (lambda claims: make_signed_token(claims, key=key, kid="rotated")), public


class TestCluster01bg_ReviewExpiredSignIn(_ReflexSessionCase):
    """1.580-1.581: identity only while the access token is valid; ``/login`` waits for a renewal."""

    # -- 1.580 ---------------------------------------------------------
    def test_1_580_every_event_re_resolves_a_profile_whose_access_token_expired(self) -> None:
        """
        Scenario 1.580: the plugin's 30-minute profile cache is not a sign-in.
        Given: a signed-in tab whose cached profile is fresh, and whose access token expires.
        When: events arrive -- before expiry, then after it with Keycloak answering.
        Then: before expiry lex's per-event check leaves the profile alone; after it, the check
              drops it and the gate's next read renews the tokens silently -- the same user,
              one refresh, no new sign-in. Without the check the plugin served the stale
              profile and asked Keycloak nothing.
        """
        token_set = self.keycloak.issue()
        token_set["expires_in"] = 1
        tab = self._signed_in("a", token_set)
        self.assertEqual(_userinfo(tab)["sub"], "ada")
        _next_event(tab)
        self.assertIsNotNone(tab._cached_userinfo, "a valid sign-in keeps its cached profile")
        self.assertEqual(self._calls(), 0)
        time.sleep(1.1)
        _next_event(tab)
        self.assertIsNone(tab._cached_userinfo, "dropped once the access token expired")
        self.assertEqual(_userinfo(tab)["sub"], "ada", "re-resolved: the same user")
        self.assertEqual(self._calls(), 1, "through one silent refresh")
        self.assertTrue(tab._lex_access_valid())
        self.assertNotIn(AUTHORIZE, " ".join(self.browser.redirects()), "no new sign-in")
        self.assertFalse(self.browser.told_to_sign_out())

    # -- 1.581 ---------------------------------------------------------
    def test_1_581_login_waits_for_the_renewal_and_sends_the_user_back(self) -> None:
        """
        Scenario 1.581: "temporarily unavailable", not "please sign in".
        Given: a signed-in tab, sent to ``/login?redirect_to=/fund`` because its access token
               expired while Keycloak was unreachable.
        When: ``/login`` opens; later a scheduled refresh succeeds.
        Then: the page shows that sign-in is temporarily unavailable (no Keycloak redirect, no
              button) and re-arms the refresh; when the refresh succeeds the browser goes back to
              ``/fund`` -- no new sign-in.
        """
        from lex.test_project.tests.init.test_1ar_reflex_automatic_sign_in import _no_running_app

        with _no_running_app():
            page = str(auth.lex_login_page(providers=[LexKeycloakAuthState]))
        self.assertIn("sign_in_unavailable", page)
        self.assertIn("Sign-in is temporarily unavailable. Retrying", page)

        tab = _signed_in_until_expiry(self, "login", url="http://localhost:8502/login?redirect_to=%2Ffund")
        self.keycloak.token_answers.append(httpx.ConnectError("refused"))
        async_to_sync(tab.do_access_token_refresh)()
        self.assertTrue(tab.sign_in_unavailable)
        self.browser.events.clear()
        result = async_to_sync(tab.start_login)({"framed": False, "repeat": False})
        handler = getattr(getattr(result, "handler", None), "fn", None)
        self.assertEqual(getattr(handler, "__name__", ""), "trigger_access_token_refresh", f"retries, got {result}")
        self.assertFalse(tab.login_needs_click, "no button")
        self.assertEqual(self.browser.redirects(), [], "no redirect to Keycloak")

        self.clock.advance(10)
        async_to_sync(tab.do_access_token_refresh)()
        self.assertFalse(tab.sign_in_unavailable)
        self.assertIn("/fund", self.browser.redirects(), "back to the page asked for")
        self.assertNotIn(AUTHORIZE, " ".join(self.browser.redirects()))


class TestCluster01bg_ReviewForeignProfile(_ReflexSessionCase):
    """1.582: a cached profile is served only for the user the verified ID token names."""

    # -- 1.582 ---------------------------------------------------------
    def test_1_582_a_userinfo_outage_never_serves_another_users_cached_profile(self) -> None:
        """
        Scenario 1.582: Ada's profile is not Bob's.
        Given: a tab that cached Ada's profile; the browser's cookie jar then holds Bob's
               sign-in (another tab signed Bob in); Keycloak's userinfo answers 503 once.
        When: the profile is resolved.
        Then: Bob's verified ID-token claims are served and Ada's cached profile is dropped --
              before review the tab served Ada's profile on Bob's tokens.
        """
        ada = self._signed_in("tab", self.keycloak.issue(sub="ada", sid="s-ada"))
        self.assertEqual(_userinfo(ada)["sub"], "ada")
        bob = self._signed_in("bob-tab", self.keycloak.issue(sub="bob", sid="s-bob"))
        _browser_jar(bob, ada)
        self.keycloak.userinfo_answers.append((503, "<html>down</html>"))
        profile = _userinfo(ada)
        self.assertEqual(profile["sub"], "bob", "the user the tokens belong to")
        self.assertEqual(profile["sid"], "s-bob")
        self.assertEqual(ada._cached_userinfo.userinfo["sub"], "bob", "Ada's profile is no longer cached")


class TestCluster01bg_ReviewVerification(_ReflexSessionCase):
    """1.583-1.584: Keycloak's answer is verified before it is stored; skew and key rotation."""

    # -- 1.583 ---------------------------------------------------------
    def test_1_583_clock_skew_within_30_s_is_accepted_beyond_it_the_tokens_are_kept(self) -> None:
        """
        Scenario 1.583: a Keycloak whose clock is ahead.
        Given: a tab whose access token is due; Keycloak's clock is 2 s ahead of this host.
        When: the refresh timer fires; 31 s later Keycloak answers with tokens issued 10 minutes
              in the future (a clock far off).
        Then: the first answer is stored (30 s leeway): refreshed, signed in. The second fails
              verification before it is stored: the previous tokens are kept, an ERROR names the
              clock, the refresh backs off -- never a sign-out (before review a 2 s skew ended a
              refresh Keycloak accepted as ``rejected`` and cleared the refresh cookie).
        """
        tab = self._signed_in("skew", self.keycloak.issue(expires_in=30))
        self.keycloak.token_answers.append(self.keycloak.issue(iat=int(time.time()) + 2))
        async_to_sync(tab.do_access_token_refresh)()
        self.assertEqual(self._calls(), 1)
        self.assertTrue(_cookies(tab)["_refresh_token"], "kept signed in")
        self.assertEqual(_userinfo(tab)["sub"], "ada")

        self.clock.advance(31)
        kept = _cookies(tab)
        self.keycloak.token_answers.append(self.keycloak.issue(iat=int(time.time()) + 600))
        with self.assertLogs("lex.lex_app.reflex.auth", logging.ERROR) as logged:
            result = _refresh(tab)
        self.assertEqual(result.outcome, "config")
        self.assertEqual(_cookies(tab), kept, "the unverifiable answer was not stored")
        self.assertIn("clock", logged.output[0])
        self.assertGreater(guard.retry_in(str(tab._refresh_token)), 0, "backing off")
        self.assertFalse(self.browser.told_to_sign_out())

    # -- 1.584 ---------------------------------------------------------
    def test_1_584_a_keycloak_key_rotation_is_followed_without_signing_anyone_out(self) -> None:
        """
        Scenario 1.584: Keycloak rotates its signing key.
        Given: a tab signed in (Keycloak's key set cached); Keycloak then signs with a new key it
               publishes beside the old one.
        When: the tab refreshes; a new tab signs in with tokens of the new key; later an answer
              signed with a key Keycloak does not publish arrives.
        Then: the key set is fetched once more and the refresh is stored; the new sign-in is
              valid; the unknown key is refused before storing -- the tokens are kept, and the
              key set is not fetched again within the minute (before review a rotation signed
              everyone out at their next refresh and failed every sign-in until a restart).
        """
        tab = self._signed_in("rot", self.keycloak.issue(expires_in=30))
        fetched = self.keycloak.jwks_calls
        sign, public = _rotated_key()
        self.keycloak.keys = {"keys": list(self.keycloak.keys["keys"]) + [public]}
        self.keycloak.token_answers.append(self.keycloak.issue(sign=sign))
        self.assertEqual(_refresh(tab).outcome, "refreshed")
        self.assertEqual(self.keycloak.jwks_calls, fetched + 1, "the key set was fetched once more")
        self.assertEqual(_userinfo(tab)["sub"], "ada")

        newcomer = self._signed_in("new", self.keycloak.issue(sign=sign))
        self.assertTrue(async_to_sync(newcomer._validate_tokens)(), "a new sign-in on the new key is valid")

        stray_sign, _ = _rotated_key()  # a key Keycloak does not publish
        self.clock.advance(31)
        kept = _cookies(tab)
        self.keycloak.token_answers.append(self.keycloak.issue(sign=stray_sign))
        self.assertEqual(_refresh(tab).outcome, "config")
        self.assertEqual(_cookies(tab), kept, "the tokens are kept")
        self.assertEqual(self.keycloak.jwks_calls, fetched + 1, "no second fetch within the minute")
        self.assertFalse(self.browser.told_to_sign_out())


class TestCluster01bg_ReviewRetries(_ReflexSessionCase):
    """1.585-1.586: a kept refresh is retried; the lock lasts as long as its leader works."""

    # -- 1.585 ---------------------------------------------------------
    def test_1_585_a_refresh_that_keeps_the_tokens_re_arms_the_timer(self) -> None:
        """
        Scenario 1.585: an outage at the scheduled refresh is retried.
        Given: tabs whose access token is due.
        When: the refresh timer fires and Keycloak answers 503, refuses the client, or the
              refresh is backing off or suppressed.
        Then: each time the browser is sent ``trigger_access_token_refresh`` (its script retries
              at most once a minute) -- before review nothing re-armed the timer and the token
              stayed expired for up to 30 minutes.
        """
        cases = {
            "transient": [(503, "<html>down</html>")],
            "config": [(401, {"error": "invalid_client"})],
            "backoff": [(503, "<html>down</html>")],
            "suppressed": [],
        }
        for outcome, answers in cases.items():
            with self.subTest(outcome=outcome):
                guard.reset()
                tab = self._signed_in(outcome, self.keycloak.issue(expires_in=30))
                self.keycloak.token_answers.extend(answers)
                if outcome == "backoff":
                    async_to_sync(tab.do_access_token_refresh)()
                elif outcome == "suppressed":
                    self.assertEqual(_refresh(tab).outcome, "refreshed")
                self.browser.events.clear()
                result = _refresh(tab)
                self.assertEqual(result.outcome, outcome)
                self.assertTrue(self.browser.sent("trigger_access_token_refresh"), f"{outcome}: re-armed")

    # -- 1.586 ---------------------------------------------------------
    def test_1_586_the_refresh_lock_lasts_as_long_as_its_leader_works(self) -> None:
        """
        Scenario 1.586: a slow Keycloak does not let a second tab refresh the same token.
        Given: two tabs with the same tokens; a refresh lock that lives 0.2 s; Keycloak taking
               0.6 s to answer; the second tab asking 0.3 s after the first.
        When: both refresh.
        Then: one call to Keycloak; the second tab waits and adopts the first's result -- before
              review the lock expired under the slow leader, the second tab sent the same
              refresh token again, and a rotating Keycloak's ``invalid_grant`` signed it out.
        """
        first = self.keycloak.issue()
        tab_a, tab_b = self._signed_in("a", first), self._signed_in("b", first)
        self.keycloak.token_delay = 0.6
        self.keycloak.token_answers.extend([
            self.keycloak.issue(), (400, {"error": "invalid_grant", "error_description": "Stale token"}),
        ])

        async def b_later():
            await asyncio.sleep(0.3)
            return await tab_b._lex_try_refresh()

        async def together():
            return await asyncio.gather(tab_a._lex_try_refresh(), b_later())

        with mock.patch.object(guard, "REFRESH_LOCK_SECONDS", 0.2):
            results = async_to_sync(together)()
        self.assertEqual(self._calls(), 1)
        self.assertEqual([result.outcome for result in results], ["refreshed", "adopted"])
        self.assertFalse(self.browser.told_to_sign_out())


class TestCluster01bg_ReviewStore(_ReflexSessionCase):
    """1.587-1.589: what a tab adopts, what memory keeps, and the cooldown's clock."""

    # -- 1.587 ---------------------------------------------------------
    def test_1_587_a_published_set_is_adopted_only_for_its_refresh_token_and_sign_in(self) -> None:
        """
        Scenario 1.587: a set planted in the shared store is not adopted.
        Given: Bob's tab; under his refresh token's key someone who can write the shared store
               put (a) Ada's set, sealed with the instance key for Bob's refresh token; (b) a set
               sealed for another refresh token; (c) Bob's set of another Keycloak session.
        When: Bob's tab refreshes.
        Then: none is adopted: the tab asks Keycloak itself and stays Bob, in the same session
              (before review the tab adopted Ada's set and became Ada).
        """
        def plant(target_rt: str, token_set: dict, *, sealed_for: str | None = None) -> None:
            payload = async_to_sync(_tab("planter")._set_tokens_payload_from_exchange)(token_set)
            guard.publish_refreshed(sealed_for or target_rt, payload)
            if sealed_for:  # copy the sealed value under the target's key
                value = guard._LOCAL.get(guard.refreshed_key(sealed_for))
                guard._LOCAL.set(guard.refreshed_key(target_rt), value, guard.PUBLISHED_TTL_SECONDS)

        cases = {
            "another user's set": lambda rt: plant(rt, self.keycloak.issue(sub="ada", sid="s-ada")),
            "sealed for another refresh token": lambda rt: plant(
                rt, self.keycloak.issue(sub="bob", sid="s-bob"), sealed_for="another-refresh-token"),
            "another session of the same user": lambda rt: plant(rt, self.keycloak.issue(sub="bob", sid="s-other")),
        }
        for what, planted in cases.items():
            with self.subTest(case=what):
                guard.reset()
                bob = self._signed_in("bob", self.keycloak.issue(sub="bob", sid="s-bob"))
                calls = self._calls()
                planted(str(bob._refresh_token))
                result = _refresh(bob)
                self.assertEqual(result.outcome, "refreshed", "Keycloak was asked instead")
                self.assertEqual(self._calls(), calls + 1)
                claims = jwt.decode(str(bob._id_token), options={"verify_signature": False})
                self.assertEqual((claims["sub"], claims["sid"]), ("bob", "s-bob"), "still Bob, same session")

    # -- 1.588 ---------------------------------------------------------
    def test_1_588_process_memory_drops_expired_entries_and_stays_bounded(self) -> None:
        """
        Scenario 1.588: the in-memory fallback does not grow without end.
        Given: no Redis; 500 refreshed sets published, all long expired; a store capped at 3.
        When: one more set is published; five entries are written to the capped store.
        Then: the expired sets are gone (one entry left -- before review all 501, 2 MB, stayed);
              the capped store holds 3, the ones that expire last.
        """
        self.assertEqual(guard.shared_backend(), "local")
        for index in range(500):
            guard.publish_refreshed(f"refresh-token-{index}", {"access_token": "x" * 1500})
        self.clock.advance(guard.PUBLISHED_TTL_SECONDS * 10)
        guard.publish_refreshed("one-more", {"access_token": "x"})
        self.assertEqual(len(guard._LOCAL), 1)

        capped = guard.LocalStore(max_entries=3)
        for index, ttl in enumerate((50, 10, 40, 30, 20)):
            capped.set(f"k{index}", "v", ttl)
        self.assertEqual(len(capped), 3)
        self.assertEqual({key for key in ("k0", "k1", "k2", "k3", "k4") if capped.get(key)}, {"k0", "k2", "k4"})

    # -- 1.589 ---------------------------------------------------------
    def test_1_589_a_clock_that_stepped_back_suppresses_no_refresh(self) -> None:
        """
        Scenario 1.589: the cooldown on a wall clock that moved back.
        Given: a tab that refreshed; the host's clock then steps back one hour.
        When: the tab needs a refresh.
        Then: it refreshes -- a refresh "in the future" is no cooldown (before review every
              refresh was suppressed for the length of the step).
        """
        tab = self._signed_in("cool", self.keycloak.issue(expires_in=30))
        self.assertEqual(_refresh(tab).outcome, "refreshed")
        self.clock.advance(-3600)
        self.assertEqual(_refresh(tab).outcome, "refreshed")
        self.assertEqual(self._calls(), 2)


class TestCluster01bg_ReviewAudit(_ReflexSessionCase):
    """1.590: one sign-out, one audit entry."""

    # -- 1.590 ---------------------------------------------------------
    def test_1_590_a_back_channel_sign_out_on_the_scheduled_path_is_audited_once(self) -> None:
        """
        Scenario 1.590: exactly one ``session_expired`` entry.
        Given: an audit hook (the plugin's sink); a tab whose access token is due; Keycloak
               ended its session through the back channel.
        When: the refresh timer fires.
        Then: the tab is signed out without asking Keycloak, and the audit holds one
              ``session_expired`` entry, reason ``backchannel_logout`` (before review a second
              one, ``refresh_failed``, followed).
        """
        audits = _Audits()
        for patcher in (
            mock.patch("reflex_enterprise.plugins.guards.has_plugin", lambda plugin_type: True),
            mock.patch.object(oidc_state, "emit_audit", audits.emit),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        tab = self._signed_in("bc", self.keycloak.issue(sid="kc-session-gone", expires_in=30))
        guard.mark_sid_ended("kc-session-gone")
        async_to_sync(tab.do_access_token_refresh)()
        self.assertTrue(self.browser.told_to_sign_out())
        self.assertEqual(self.keycloak.token_calls, [])
        self.assertEqual(audits.expired(), ["backchannel_logout"])


class TestCluster01bg_ReviewLockAcrossReplicas(_ReflexSessionCase):
    """1.591: the lock in Redis, too, lasts as long as its leader works (opt-in Redis)."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.redis_url = _redis_test_url()
        super().setUpClass()

    def setUp(self) -> None:
        super().setUp()
        import redis

        self.redis = redis.Redis.from_url(self.redis_url)
        for key in self.redis.scan_iter("lex:*"):
            self.redis.delete(key)
        self.addCleanup(lambda: [self.redis.delete(key) for key in self.redis.scan_iter("lex:*")])

    # -- 1.591 ---------------------------------------------------------
    def test_1_591_the_redis_lock_lasts_as_long_as_its_leader_works(self) -> None:
        """
        Scenario 1.591: 1.586 across replicas.
        Given: Redis shared by two replicas, a tab on each with the same tokens; a refresh lock
               that lives 0.2 s; Keycloak taking 0.6 s; the second replica asking 0.3 s after
               the first.
        When: both refresh.
        Then: the lock is still in Redis at 0.45 s; one call to Keycloak; the second replica
              adopts the first's result; the lock is gone afterwards (before review Redis
              expired it under the slow leader and the second replica refreshed the same
              refresh token again).
        """
        first = self.keycloak.issue()
        tab_a, tab_b = self._signed_in("a", first), self._signed_in("b", first)
        self.assertEqual(guard.shared_backend(), "redis")
        lock_key = guard.refresh_lock_key(first["refresh_token"])
        self.keycloak.token_delay = 0.6
        self.keycloak.token_answers.extend([
            self.keycloak.issue(), (400, {"error": "invalid_grant", "error_description": "Stale token"}),
        ])
        held: list[bool] = []

        async def other_replica():
            await asyncio.sleep(0.3)
            guard._LOCAL.clear()  # another process: its own memory, the same Redis
            return await tab_b._lex_try_refresh()

        async def look_at_redis():
            await asyncio.sleep(0.45)
            held.append(await asyncio.to_thread(self.redis.exists, lock_key) == 1)

        async def together():
            return await asyncio.gather(tab_a._lex_try_refresh(), other_replica(), look_at_redis())

        with mock.patch.object(guard, "REFRESH_LOCK_SECONDS", 0.2):
            results = async_to_sync(together)()
        self.assertEqual(held, [True], "the lock outlived its 0.2 s while the leader waited for Keycloak")
        self.assertEqual(self._calls(), 1)
        self.assertEqual([result.outcome for result in results[:2]], ["refreshed", "adopted"])
        self.assertEqual(self.redis.exists(lock_key), 0, "released")
        self.assertFalse(self.browser.told_to_sign_out())
