"""An open Streamlit dashboard survives being left idle.

Intent: the auth proxy hands Streamlit its credentials in the headers of the
WebSocket handshake, and Streamlit reads those headers off the *session's
client* -- so they are a snapshot taken once, when the socket opened, and the
socket then stays open for as long as the tab does. Nothing in a running script
can produce a newer token by re-reading them. A dashboard left idle therefore
outlives the credential it started with, and the framework has to renew ahead of
expiry from a channel that is still live: the proxy's ``/auth/token``, which owns
the refresh token and stays the only component allowed to spend it.

A regression here is what the user actually sees. Before this batch an idle tab
died on the access token's own lifetime and reported "Authentication Error:
Missing user information" -- a message about identity headers that were in fact
present, on a page that could not recover, because the next rerun re-read the
same frozen headers and invalidated all over again. The two halves that make it
survivable are easy to break independently: the proxy must hand over the
*renewable* credential (its session, not the short-lived ``st_access`` cookie),
and Streamlit must never let the frozen handshake copy overwrite a token it has
just renewed.

Cluster 1ac — scenarios 1.230–1.246. Type: U.
Covers: lex/proxy.py (internal_token, proxy credential precedence, ws_proxy
        credential precedence), lex/streamlit_app.py (_adopt_header_token,
        _sync_tokens_from_headers, renew_access_token,
        authenticate_from_proxy_or_jwt, sync_keycloak_context_from_access_token,
        _token_refresher, start_token_refresh_thread_if_needed,
        _within_renewal_grace).
Run: python -m lex pytest lex/test_project/tests/init/test_1ac_streamlit_session_survival.py -v
"""

from __future__ import annotations

import json
import os
import time
from base64 import b64encode
from unittest.mock import patch

import httpx
import itsdangerous
import jwt
import pytest
from django.test import SimpleTestCase
from starlette.testclient import TestClient

import lex.proxy as proxy
import lex.streamlit_app as sa

pytestmark = pytest.mark.init


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _epoch(delta_seconds: int) -> int:
    return int(time.time()) + delta_seconds


def _token(exp: int, sub: str = "u1") -> str:
    """A Keycloak-shaped access token carrying ``exp`` (signature irrelevant here)."""
    return jwt.encode({"sub": sub, "email": "u@example.com", "exp": exp}, "irrelevant", algorithm="HS256")


def _session_cookie(sid: str = "sid-1", email: str = "u@example.com") -> str:
    """A cookie SessionMiddleware will accept, minted the way it mints them."""
    signer = itsdangerous.TimestampSigner(str(proxy.SESSION_SECRET))
    data = b64encode(json.dumps({"user": {"email": email, "sid": sid}}).encode("utf-8"))
    return signer.sign(data).decode("utf-8")


class _CapturingUpstream:
    """Stands in for the Streamlit upstream, recording what the proxy forwarded."""

    last_headers: dict = {}

    def __init__(self, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def request(self, method, url, content=None, headers=None):
        _CapturingUpstream.last_headers = {k.lower(): v for k, v in (headers or {}).items()}
        return httpx.Response(200, content=b"upstream-ok")


class _FakeUpstreamWS:
    """Stands in for the Streamlit WebSocket, recording the handshake headers."""

    captured: dict = {}
    subprotocol = None

    def __init__(self, url, **kwargs):
        _FakeUpstreamWS.captured = {
            k.lower(): v for k, v in (kwargs.get(proxy.WS_HEADER_KWARG) or [])
        }

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def send(self, data):
        pass

    async def recv(self):
        import websockets

        raise websockets.exceptions.ConnectionClosedOK(None, None)


class _FakeState(dict):
    """``st.session_state`` outside a script run."""

    def __getattr__(self, key):
        try:
            return self[key]
        except KeyError:
            raise AttributeError(key) from None

    def __setattr__(self, key, value):
        self[key] = value


# ---------------------------------------------------------------------------
# the proxy: handing over a renewable credential, and a way to pull a fresh one
# ---------------------------------------------------------------------------
class TestCluster01ac_ProxyRenewalChannel(SimpleTestCase):
    """Cluster 1ac: the proxy is the single refresh authority, readable on demand."""

    def setUp(self):
        self.client = TestClient(proxy.app)
        _CapturingUpstream.last_headers = {}

    def _get_token(self, cookies: dict, secret: str | None):
        headers = {}
        if secret is not None:
            headers[proxy.INTERNAL_AUTH_HEADER] = secret
        self.client.cookies.update(cookies)
        return self.client.get("/auth/token", headers=headers)

    def test_1_230_token_endpoint_refuses_a_caller_without_the_internal_secret(self):
        """
        Scenario 1.230: the browser cannot read the raw access token.
        Given: a valid proxy session cookie
        When: /auth/token is requested without the internal shared secret
        Then: 403 and no token in the body — the session cookie is HttpOnly but
              page script can still fetch with credentials, so cookie alone must
              not be enough to exfiltrate a bearer token
        """
        async def _tokens(_session):
            return {"access_token": _token(_epoch(300)), "expires_at": _epoch(300)}

        with patch.object(proxy, "_ensure_valid_access_token", _tokens):
            response = self._get_token({"session": _session_cookie()}, secret=None)

        self.assertEqual(response.status_code, 403)
        self.assertNotIn("access_token", response.json())

    def test_1_231_token_endpoint_returns_a_token_that_is_valid_now(self):
        """
        Scenario 1.231: the pull returns a *refreshed* token, not the stored one.
        Given: a session whose stored access token has already expired
        When: the co-located Streamlit process pulls from /auth/token
        Then: the response carries the renewed token and its expiry — a dashboard
              asks precisely because what it holds has aged out, so echoing the
              stale copy back would renew nothing
        """
        renewed_exp = _epoch(600)

        async def _tokens(_session):
            # This is _ensure_valid_access_token's contract: it refreshes first.
            return {"access_token": _token(renewed_exp), "expires_at": renewed_exp, "email": "u@example.com"}

        with patch.object(proxy, "_ensure_valid_access_token", _tokens):
            response = self._get_token({"session": _session_cookie()}, secret=proxy.INTERNAL_AUTH_SECRET)

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["access_token"], _token(renewed_exp))
        self.assertEqual(body["expires_at"], renewed_exp)

    def test_1_232_token_endpoint_never_hands_out_the_refresh_token(self):
        """
        Scenario 1.232: spending the refresh token stays single-writer.
        Given: a session whose token set includes a refresh token
        When: Streamlit pulls a fresh access token
        Then: the refresh token is absent from the response — two components
              rotating the same refresh token is exactly the race that made
              local refresh unusable, and handing it over would reintroduce it
        """
        async def _tokens(_session):
            return {
                "access_token": _token(_epoch(300)),
                "refresh_token": "rt-secret",
                "expires_at": _epoch(300),
            }

        with patch.object(proxy, "_ensure_valid_access_token", _tokens):
            response = self._get_token({"session": _session_cookie()}, secret=proxy.INTERNAL_AUTH_SECRET)

        self.assertEqual(response.status_code, 200)
        self.assertNotIn("refresh_token", response.json())
        self.assertNotIn("rt-secret", response.text)

    def test_1_233_token_endpoint_reports_an_unrenewable_session_as_401(self):
        """
        Scenario 1.233: a dead session is an auth answer, not an empty success.
        Given: a session whose tokens can no longer be refreshed
        When: Streamlit pulls
        Then: 401 — the caller has to distinguish "renewed" from "sign in again",
              and a 200 with no token would read as the former
        """
        async def _tokens(_session):
            return None

        with patch.object(proxy, "_ensure_valid_access_token", _tokens):
            response = self._get_token({"session": _session_cookie()}, secret=proxy.INTERNAL_AUTH_SECRET)

        self.assertEqual(response.status_code, 401)

    def test_1_234_session_outranks_the_short_lived_access_cookie(self):
        """
        Scenario 1.234: the handshake gets the renewable credential.
        Given: a request carrying both an st_access cookie and a proxy session
        When: it is proxied to Streamlit
        Then: it is forwarded as session auth *with* the refresh token — the
              cookie is set on every login, so consulting it first classified a
              freshly logged-in user as jwt with no refresh token at all, an
              unrenewable credential that expired minutes into the session
        """
        cookie_exp = _epoch(300)

        async def _tokens(_session):
            return {
                "access_token": _token(_epoch(600)),
                "refresh_token": "rt-session",
                "expires_at": _epoch(600),
            }

        with patch.object(proxy, "validate_jwt_token", lambda t: {"sub": "u1", "exp": cookie_exp}), \
             patch.object(proxy, "_ensure_valid_access_token", _tokens), \
             patch.object(proxy.httpx, "AsyncClient", _CapturingUpstream):
            self.client.cookies.update({"st_access": _token(cookie_exp), "session": _session_cookie()})
            response = self.client.get("/dashboard")

        self.assertEqual(response.status_code, 200)
        forwarded = _CapturingUpstream.last_headers
        self.assertEqual(
            forwarded.get("x-streamlit-auth-method"), "session",
            "A request with a live session must reach Streamlit as session auth.",
        )
        self.assertEqual(
            forwarded.get("x-streamlit-refresh-token"), "rt-session",
            "Without the refresh token the dashboard has nothing to renew against.",
        )

    def test_1_235_an_explicit_auth_token_still_outranks_the_session(self):
        """
        Scenario 1.235: the embedded renewal handoff keeps its precedence.
        Given: a request carrying both ?auth_token= and a proxy session
        When: it is proxied
        Then: the explicit token wins — re-sourcing the iframe with a fresh
              auth_token is how the embedded dashboard renews, and demoting it
              below the session would discard every renewal the frontend sends
        """
        handoff = _token(_epoch(900), sub="handoff")

        async def _tokens(_session):
            return {"access_token": _token(_epoch(600)), "refresh_token": "rt-session", "expires_at": _epoch(600)}

        with patch.object(proxy, "validate_jwt_token", lambda t: {"sub": "handoff", "exp": _epoch(900)}), \
             patch.object(proxy, "_ensure_valid_access_token", _tokens), \
             patch.object(proxy, "PERSIST_JWT_AUTH_TO_SESSION", False), \
             patch.object(proxy.httpx, "AsyncClient", _CapturingUpstream):
            self.client.cookies.update({"session": _session_cookie()})
            response = self.client.get(f"/dashboard?auth_token={handoff}")

        self.assertEqual(response.status_code, 200)
        forwarded = _CapturingUpstream.last_headers
        self.assertEqual(forwarded.get("x-streamlit-auth-method"), "jwt")
        self.assertEqual(forwarded.get("x-streamlit-access-token"), handoff)

    def test_1_246_the_websocket_handshake_carries_the_renewable_credential(self):
        """
        Scenario 1.246: the one moment that matters gets the right credential.
        Given: a WebSocket handshake carrying both an st_access cookie and a
               proxy session
        When: it is proxied to Streamlit
        Then: it is forwarded as session auth with the refresh token — the
              handshake is the *only* point at which a connection that then stays
              open for hours is handed anything, so classifying it from the
              short-lived cookie leaves the dashboard holding an unrenewable
              token for the rest of its life
        """
        async def _tokens(_session):
            return {
                "access_token": _token(_epoch(600)),
                "refresh_token": "rt-session",
                "expires_at": _epoch(600),
            }

        with patch.object(proxy, "validate_jwt_token", lambda t: {"sub": "u1", "exp": _epoch(300)}), \
             patch.object(proxy, "_ensure_valid_access_token", _tokens), \
             patch.object(proxy, "ws_connect", _FakeUpstreamWS):
            self.client.cookies.update({"st_access": _token(_epoch(300)), "session": _session_cookie()})
            with self.client.websocket_connect("/_stcore/stream"):
                pass

        forwarded = _FakeUpstreamWS.captured
        self.assertEqual(
            forwarded.get("x-streamlit-auth-method"), "session",
            "The handshake must classify a live session as session auth.",
        )
        self.assertEqual(
            forwarded.get("x-streamlit-refresh-token"), "rt-session",
            "Without a refresh token on the handshake the session cannot be renewed at all.",
        )


# ---------------------------------------------------------------------------
# Streamlit: never lose a renewal, never lose the user
# ---------------------------------------------------------------------------
class TestCluster01ac_StreamlitTokenLifecycle(SimpleTestCase):
    """Cluster 1ac: a running dashboard renews itself and keeps its user."""

    def test_1_236_a_stale_handshake_token_never_replaces_a_renewed_one(self):
        """
        Scenario 1.236: renewals are not undone on the next rerun.
        Given: session state holding a token renewed after the socket opened
        When: the script reruns and re-reads the frozen handshake headers
        Then: the renewed token is kept — the headers cannot change without a new
              handshake, so adopting whatever differs from what we hold reverts
              every renewal and puts the session back on the token it escaped
        """
        renewed = _token(_epoch(600))
        stale = _token(_epoch(-60))
        state = _FakeState(access_token=renewed, token_exp=_epoch(600), refresh_token="")

        with patch.object(sa.st, "session_state", state):
            sa._sync_tokens_from_headers({"x-streamlit-access-token": stale})

        self.assertEqual(state["access_token"], renewed, "A renewal must not be reverted by frozen headers.")

    def test_1_237_a_genuinely_newer_handshake_token_is_adopted(self):
        """
        Scenario 1.237: a reconnect or a re-sourced iframe is picked up.
        Given: session state holding a token that is about to expire
        When: a new handshake delivers one with a later expiry
        Then: the newer token is adopted — a dropped socket reconnecting through
              the proxy is a legitimate renewal path and must not be ignored just
              because we already hold something
        """
        newer_exp = _epoch(900)
        newer = _token(newer_exp)
        state = _FakeState(access_token=_token(_epoch(30)), token_exp=_epoch(30), refresh_token="")

        with patch.object(sa.st, "session_state", state):
            sa._sync_tokens_from_headers({"x-streamlit-access-token": newer})

        self.assertEqual(state["access_token"], newer)
        self.assertEqual(state["token_exp"], newer_exp)

    def test_1_238_renewal_pulls_from_the_proxy_and_leaves_the_refresh_token_alone(self):
        """
        Scenario 1.238: the proxy renews; Streamlit does not.
        Given: an expired access token, a reachable proxy, and a refresh token in
               session state
        When: renewal runs
        Then: the token comes from the proxy and the refresh token is untouched —
              both sides spending the same refresh token is the rotation race the
              pull channel exists to avoid
        """
        pulled = _token(_epoch(600))
        state = _FakeState(access_token=_token(_epoch(-60)), token_exp=_epoch(-60), refresh_token="rt-original")

        def _fake_get(url, headers=None, timeout=None):
            self.assertIn("/auth/token", url)
            self.assertEqual(headers["Cookie"], "session=abc")
            return httpx.Response(200, json={"access_token": pulled, "expires_at": _epoch(600)})

        def _must_not_refresh(_rt):
            self.fail("Streamlit must not spend the refresh token while the proxy answers.")

        with patch.object(sa.st, "session_state", state), \
             patch.dict(os.environ, {"LEX_INTERNAL_AUTH_SECRET": "s3cret"}), \
             patch.object(sa, "_live_headers", lambda: {"cookie": "session=abc"}), \
             patch.object(sa.requests, "get", _fake_get), \
             patch.object(sa, "_post_refresh", _must_not_refresh):
            self.assertTrue(sa.renew_access_token())

        self.assertEqual(state["access_token"], pulled)
        self.assertEqual(state["refresh_token"], "rt-original")

    def test_1_239_renewal_falls_back_to_the_refresh_token_when_no_proxy_answers(self):
        """
        Scenario 1.239: a deployment without the proxy still renews.
        Given: an expired token, an unreachable proxy, and a refresh token
        When: renewal runs
        Then: the refresh token is spent and a new token stored — with the proxy
              silent there is no second writer to race, and `streamlit run`
              without the proxy must not be left with a dead session
        """
        refreshed = _token(_epoch(600))
        state = _FakeState(access_token=_token(_epoch(-60)), token_exp=_epoch(-60), refresh_token="rt-original")

        def _unreachable(*a, **kw):
            raise ConnectionError("proxy down")

        with patch.object(sa.st, "session_state", state), \
             patch.dict(os.environ, {"LEX_INTERNAL_AUTH_SECRET": "s3cret"}), \
             patch.object(sa, "_live_headers", lambda: {"cookie": "session=abc"}), \
             patch.object(sa.requests, "get", _unreachable), \
             patch.object(sa, "_post_refresh", lambda rt: {"access_token": refreshed, "refresh_token": "rt-next"}):
            self.assertTrue(sa.renew_access_token())

        self.assertEqual(state["access_token"], refreshed)
        self.assertEqual(state["refresh_token"], "rt-next")

    def test_1_240_identity_survives_a_token_that_cannot_be_renewed(self):
        """
        Scenario 1.240: an expired credential is not a missing user.
        Given: an authenticated session whose token has expired and whose every
               renewal path fails
        When: the script reruns
        Then: the user stays authenticated and identified, flagged only as
              needing renewal — clearing identity here produced the "Missing user
              information" dead end, a message about headers that were present,
              on a page that re-entered the same failure on every rerun
        """
        state = _FakeState(
            authenticated=True,
            auth_method="session",
            user_id="u1",
            user_email="u@example.com",
            user_username="u",
            access_token=_token(_epoch(-60)),
            token_exp=_epoch(-60),
            refresh_token="",
            permissions=["read"],
        )

        with patch.object(sa.st, "session_state", state), \
             patch.object(sa, "_live_headers", lambda: {}), \
             patch.object(sa.st, "context", type("C", (), {"headers": {}})()), \
             patch.object(sa, "start_token_refresh_thread_if_needed", lambda: None), \
             patch.object(sa, "sync_keycloak_context_from_access_token", lambda: None), \
             patch.object(sa.requests, "get", lambda *a, **kw: httpx.Response(401)):
            sa.authenticate_from_proxy_or_jwt()

        self.assertTrue(state["authenticated"], "An aged-out token must not log the user out.")
        self.assertEqual(state["user_id"], "u1")
        self.assertEqual(state["user_email"], "u@example.com")
        self.assertTrue(state["token_renewal_failed"], "The renewal problem must still be recorded.")

    def test_1_241_permissions_are_kept_when_the_keycloak_lookup_fails(self):
        """
        Scenario 1.241: a Keycloak blip does not demote the user.
        Given: a valid token and a UMA permission lookup that raises
        When: the Keycloak context is synced
        Then: the permissions already resolved are kept — replacing them with an
              empty list silently strips the user's access mid-session, which
              reads as a broken dashboard rather than a transient outage
        """
        state = _FakeState(
            access_token=_token(_epoch(600)),
            token_exp=_epoch(600),
            refresh_token="",
            permissions=["read", "write"],
            keycloak_context_token="",
            user_id="u1",
            user_email="u@example.com",
            user_username="u",
            auth_method="session",
        )

        def _boom(*a, **kw):
            raise RuntimeError("keycloak unreachable")

        with patch.object(sa.st, "session_state", state), \
             patch.object(sa, "get_user_info", lambda t: None), \
             patch("lex.api.views.authentication.KeycloakManager.KeycloakManager.get_uma_permissions", _boom):
            sa.sync_keycloak_context_from_access_token()

        self.assertEqual(state["permissions"], ["read", "write"])

    def test_1_242_the_refresher_runs_for_proxy_managed_session_auth(self):
        """
        Scenario 1.242: session auth is renewed too.
        Given: session auth and no refresh token of our own
        When: the app starts its token refresher
        Then: a refresher thread is running — "the proxy handles it" meant nobody
              handled it, because the proxy can only deliver a credential at
              handshake time and the handshake happens once
        """
        state = _FakeState(auth_method="session", refresh_token="", access_token="", token_exp=0,
                           stop_token_refresher=False, token_refresher_thread=None,
                           token_refresher_started=False)

        with patch.object(sa.st, "session_state", state), \
             patch.object(sa, "RENEW_POLL_SECONDS", 0.01), \
             patch.object(sa, "get_script_run_ctx", lambda: None), \
             patch.object(sa, "add_script_run_ctx", lambda th, ctx: th):
            sa.start_token_refresh_thread_if_needed()
            thread = state["token_refresher_thread"]
            try:
                self.assertIsNotNone(thread, "Session auth must get a refresher, not be exempted from one.")
                self.assertTrue(thread.is_alive())
            finally:
                state["stop_token_refresher"] = True
                thread.join(timeout=5)

        self.assertFalse(thread.is_alive())

    def test_1_243_the_refresher_stops_once_the_streamlit_session_is_gone(self):
        """
        Scenario 1.243: a closed tab does not leave a thread renewing forever.
        Given: a refresher whose Streamlit session has been torn down
        When: it next checks
        Then: it returns — closing a tab sets no session-state flag, so a
              refresher that only watched that flag outlived every abandoned
              session it was started for
        """
        state = _FakeState(stop_token_refresher=False, token_exp=_epoch(600))

        with patch.object(sa.st, "session_state", state), \
             patch.object(sa, "_session_is_live", lambda sid: False):
            started = time.time()
            sa._token_refresher("dead-session")
            elapsed = time.time() - started

        self.assertLess(elapsed, 2.0, "A refresher for a dead session must exit immediately.")

    def test_1_244_a_transient_renewal_failure_stays_invisible(self):
        """
        Scenario 1.244: a proxy restart does not interrupt the reader.
        Given: renewal has just started failing
        When: the page renders
        Then: it renders normally — a failure that the refresher is still
              retrying must not put a re-authentication notice in front of
              someone who is simply reading a chart
        """
        state = _FakeState(token_renewal_failed=True, renewal_failing_since=0)

        with patch.object(sa.st, "session_state", state):
            self.assertTrue(sa._within_renewal_grace())
            self.assertTrue(state["renewal_failing_since"], "The failure must be stamped so the grace can expire.")

    def test_1_245_a_persistent_renewal_failure_surfaces_recovery(self):
        """
        Scenario 1.245: a structurally dead session asks for a sign-in.
        Given: renewal that has been failing for longer than the grace window
        When: the page renders
        Then: the grace is over — past this point the failure is Keycloak's SSO
              max lifetime or a revoked session, and retrying silently forever
              leaves the user staring at data that can no longer be refreshed
        """
        state = _FakeState(
            token_renewal_failed=True,
            renewal_failing_since=_epoch(-(sa.RENEWAL_GRACE_SECONDS + 10)),
        )

        with patch.object(sa.st, "session_state", state):
            self.assertFalse(sa._within_renewal_grace())

        # ...and a renewal that recovers clears the stamp, so the next blip gets
        # its own full grace window rather than inheriting a stale deadline.
        recovered = _FakeState(token_renewal_failed=False, renewal_failing_since=_epoch(-999))
        with patch.object(sa.st, "session_state", recovered):
            self.assertTrue(sa._within_renewal_grace())
        self.assertEqual(recovered["renewal_failing_since"], 0)
