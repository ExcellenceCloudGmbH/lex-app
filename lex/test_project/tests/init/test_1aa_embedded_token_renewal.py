"""The embedded Streamlit token can be renewed before it expires.

Intent: an embedded Streamlit dashboard authenticates to the auth proxy with a
short-lived Keycloak access token handed to the iframe as ``?auth_token=``. The
proxy stores that token with **no refresh token** -- deliberately, because a
refresh token would have to travel through the browser and into an iframe URL,
where it lands in access logs, history and ``Referer`` headers. Renewal is
therefore the caller's job, and it can only work if two things hold: the token
endpoint tells the caller *when* to renew, and the proxy *adopts* the renewed
token when it arrives. The iframe-breakout batch made the expiry dead end graceful; this batch
removes the dead end.

A regression here is silent. If the endpoint stops publishing an expiry the
caller has nothing to schedule against; if the proxy keeps the older token, every
renewal is discarded and the session dies at the original expiry anyway -- with
no error anywhere, just a user sent back to the login page mid-work.

Cluster 1aa — scenarios 1.217–1.222. Type: U.
Covers: lex/authentication/views/token_views.py (StreamlitTokenView.post,
        _access_token_expiry), lex/proxy.py (_persist_jwt_to_session_if_needed).
Run: python -m lex pytest lex/test_project/tests/init/test_1aa_embedded_token_renewal.py -v
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

import jwt
import pytest
from django.test import SimpleTestCase
from rest_framework import status
from rest_framework.test import APIRequestFactory, force_authenticate

import lex.proxy as proxy
from lex.authentication.views.token_views import (
    TOKEN_REFRESH_SKEW_SECONDS,
    StreamlitTokenView,
)

pytestmark = pytest.mark.init


def _epoch(delta_seconds: int) -> int:
    """Epoch seconds ``delta_seconds`` from now."""
    return int((datetime.now(timezone.utc) + timedelta(seconds=delta_seconds)).timestamp())


def _access_token(exp: int) -> str:
    """A Keycloak-shaped access token carrying ``exp`` (signature irrelevant here)."""
    return jwt.encode({"sub": "u1", "email": "u@example.com", "exp": exp}, "irrelevant", algorithm="HS256")


def _post_token(session: dict):
    """Drive the real endpoint the frontend calls, with ``session`` on the request."""
    request = APIRequestFactory().post("/api/auth/streamlit-token/")
    force_authenticate(request, user=SimpleNamespace(id=1, email="u@example.com", is_authenticated=True))
    request.session = session
    return StreamlitTokenView.as_view()(request)


class _FakeTokenStore:
    """Stands in for the proxy's Redis-backed token store."""

    def __init__(self):
        self.tokens: dict[str, dict] = {}
        self.dropped: list[str] = []

    async def get(self, sid):
        return self.tokens.get(sid)

    async def put(self, sid, email, token_set):
        self.tokens[sid] = dict(token_set)

    async def drop(self, sid):
        self.dropped.append(sid)
        self.tokens.pop(sid, None)


class TestCluster01aa_EmbeddedTokenRenewal(SimpleTestCase):
    """Cluster 1aa: the token endpoint publishes an expiry and the proxy adopts renewals."""

    # -- the issuer side -------------------------------------------------

    def test_1_217_token_response_carries_expiry_metadata(self):
        """
        Scenario 1.217: the endpoint tells the caller when to renew.
        Given: an authenticated session holding a Keycloak access token
        When: the frontend POSTs to the streamlit-token endpoint
        Then: the response carries the token plus expires_in / expires_at /
              refresh_interval, so the caller can schedule a renewal instead of
              discovering the expiry as a failed request
        """
        exp = _epoch(300)
        response = _post_token({"oidc_access_token": _access_token(exp), "oidc_access_expires_at": exp})

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data.get("token"), "The access token must still be returned.")
        self.assertAlmostEqual(
            response.data["expires_in"], 300, delta=5,
            msg="expires_in must reflect the real remaining lifetime of the token.",
        )
        self.assertIn("expires_at", response.data)
        self.assertEqual(
            response.data["refresh_interval"],
            response.data["expires_in"] - TOKEN_REFRESH_SKEW_SECONDS,
            "The caller must be told to renew before expiry, not at it.",
        )

    def test_1_218_expiry_falls_back_to_the_token_exp(self):
        """
        Scenario 1.218: expiry still published when the session lacks the hint.
        Given: a session with an access token but no oidc_access_expires_at
        When: the endpoint is called
        Then: the expiry is read from the token's own exp claim — the caller is
              never left without a renewal deadline just because the auth library
              stored its bookkeeping differently
        """
        exp = _epoch(180)
        response = _post_token({"oidc_access_token": _access_token(exp)})

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertAlmostEqual(
            response.data["expires_in"], 180, delta=5,
            msg="Expiry must fall back to the token's exp claim.",
        )

    def test_1_219_missing_session_token_is_a_clean_401(self):
        """
        Scenario 1.219: no OIDC token on the session is an auth problem, not a crash.
        Given: a user authenticated to Django whose session carries no OIDC token
        When: the endpoint is called
        Then: a 401 is returned rather than a KeyError surfacing as a 500 — only
              re-authentication can fix this, and a 500 tells the caller nothing
        """
        response = _post_token({})

        self.assertEqual(
            response.status_code, status.HTTP_401_UNAUTHORIZED,
            "A session without an OIDC token must ask the caller to re-authenticate.",
        )
        self.assertNotIn("token", response.data)

    # -- the proxy side --------------------------------------------------

    def _persist(self, store, session, exp):
        """Run the proxy's persistence path for a token expiring at ``exp``."""
        request = SimpleNamespace(session=session)
        with patch.object(proxy, "_get_tokens", store.get), \
             patch.object(proxy, "_put_tokens", store.put), \
             patch.object(proxy, "_drop_tokens", store.drop), \
             patch.object(proxy, "PERSIST_JWT_AUTH_TO_SESSION", True):
            asyncio.run(
                proxy._persist_jwt_to_session_if_needed(
                    request, _access_token(exp), {"exp": exp, "email": "u@example.com"}, "query"
                )
            )

    def test_1_220_newer_token_supersedes_a_still_valid_one(self):
        """
        Scenario 1.220: a renewal arriving early is adopted, not discarded.
        Given: a stored token that is still valid for another 2 minutes
        When: the frontend re-sources the iframe with a token valid for an hour
        Then: the stored token becomes the newer one — renewal necessarily arrives
              *before* expiry, so keeping the old token would throw away every
              renewal and let the session die at the original deadline
        """
        store = _FakeTokenStore()
        old_exp = _epoch(120)
        store.tokens["sid-old"] = {"access_token": _access_token(old_exp), "expires_at": old_exp}
        session = {"user": {"email": "u@example.com", "sid": "sid-old"}}

        new_exp = _epoch(3600)
        self._persist(store, session, new_exp)

        new_sid = session["user"]["sid"]
        self.assertNotEqual(new_sid, "sid-old", "A renewal must replace the stored session token.")
        self.assertEqual(
            store.tokens[new_sid]["expires_at"], new_exp,
            "The proxy must adopt the newer token, or the session dies at the old expiry.",
        )
        self.assertIn("sid-old", store.dropped, "The superseded entry must not be left behind.")

    def test_1_221_older_or_equal_token_leaves_the_session_alone(self):
        """
        Scenario 1.221: a repeat of the same token does not churn the session.
        Given: a stored token valid for another hour
        When: the same token arrives again (an iframe reload re-sending auth_token)
        Then: the stored entry and the session id are untouched — rotating the
              session on every iframe load would invalidate the cookie the
              already-loaded frame is using
        """
        store = _FakeTokenStore()
        exp = _epoch(3600)
        store.tokens["sid-keep"] = {"access_token": _access_token(exp), "expires_at": exp}
        session = {"user": {"email": "u@example.com", "sid": "sid-keep"}}

        self._persist(store, session, exp)

        self.assertEqual(session["user"]["sid"], "sid-keep", "An equal token must not rotate the session.")
        self.assertEqual(store.dropped, [], "Nothing should be dropped for a no-op re-send.")

    def test_1_222_expired_token_is_replaced(self):
        """
        Scenario 1.222: an expired stored token is still replaced (unchanged behaviour).
        Given: a stored token that expired a minute ago
        When: a fresh token arrives
        Then: the stale entry is dropped and the fresh one stored — this is the
              pre-existing recovery path and must survive the renewal change
        """
        store = _FakeTokenStore()
        dead_exp = _epoch(-60)
        store.tokens["sid-dead"] = {"access_token": _access_token(dead_exp), "expires_at": dead_exp}
        session = {"user": {"email": "u@example.com", "sid": "sid-dead"}}

        fresh_exp = _epoch(3600)
        self._persist(store, session, fresh_exp)

        self.assertIn("sid-dead", store.dropped, "An expired entry must be dropped.")
        self.assertEqual(store.tokens[session["user"]["sid"]]["expires_at"], fresh_exp)
