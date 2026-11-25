"""A dashboard calls the Lex API with the token that is current when the request leaves.

Intent: a Streamlit dashboard calls the Lex API as its viewer, with the viewer's access
token. That token lives for minutes and lex-app renews it in the background, so the only
safe moment to read it is the moment a request is sent. Dashboards used to build
``{"Authorization": f"Bearer {token}"}`` once per page run and keep it in
``st.session_state.headers``. Inside ``st.dialog`` and ``st.fragment`` that copy is never
rebuilt, so a form filled in for longer than the token lasts submitted an expired token
and the API answered 401 ("id token is missing, user is not authenticated") -- observed
in production as new records saved without their document. ``lex_api()`` reads the token
per request, renews it when it is about to expire, and answers a 401 by renewing once
and resending; and for dashboards still holding a stored header dict, lex-app keeps that
dict on the current token in place.

The token lifecycle also moved out of ``lex/streamlit_app.py`` into an importable module:
Streamlit re-executes that file as a new ``__main__`` on every script run, so its lock
was a different object each run, and a dashboard could not import anything from it.

Cluster 1fc — scenarios 1.1123–1.1132. Type: U.
Covers: lex/lex_app/streamlit/auth.py (lex_api, LexApiSession, current_access_token,
        default_api_url, renew_access_token, _store_tokens, _adopt_header_token,
        _refresh_stored_api_headers), lex/lex_app/streamlit/__init__.py,
        lex/streamlit_app.py (re-exports).
Run: python -m lex pytest lex/test_project/tests/init/test_1fc_streamlit_lex_api.py -v
"""

from __future__ import annotations

import importlib
import io
import os
import time
from unittest.mock import patch

import jwt
import pytest
import requests
from django.test import SimpleTestCase
from requests.adapters import HTTPAdapter

import lex.streamlit_app as sa

# By name, not as a package attribute: model discovery also imports the file as
# `lex_app.streamlit.auth`, a second module object that the attribute may point at.
sauth = importlib.import_module("lex.lex_app.streamlit.auth")

pytestmark = pytest.mark.init

API = "https://app.example.test/api/api/"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _token(seconds_left: int, sub: str = "viewer") -> str:
    """A JWT whose ``exp`` is ``seconds_left`` from now (lex never verifies it here)."""
    exp = int(time.time()) + seconds_left
    return jwt.encode({"sub": sub, "exp": exp, "n": os.urandom(4).hex()}, "k", algorithm="HS256")


class _State(dict):
    """Stands in for ``st.session_state``: attribute and item access over one dict."""

    def __getattr__(self, name):
        try:
            return self[name]
        except KeyError as exc:
            raise AttributeError(name) from exc

    def __setattr__(self, name, value):
        self[name] = value


def _session(token: str) -> _State:
    return _State(access_token=token, token_exp=sauth._decode_exp_no_verify(token))


class _Wire(HTTPAdapter):
    """The network: records each request and answers with the next status in line."""

    def __init__(self, *statuses: int) -> None:
        super().__init__()
        self.statuses = list(statuses)
        self.sent: list[requests.PreparedRequest] = []

    def send(self, request, **kwargs):  # noqa: D401 - adapter API
        self.sent.append(request)
        response = requests.Response()
        response.status_code = self.statuses.pop(0) if self.statuses else 200
        response.request = request
        response.url = request.url
        response._content = b"{}"
        return response

    def bearers(self) -> list[str]:
        return [r.headers.get("Authorization", "") for r in self.sent]


def _api(wire: _Wire) -> sauth.LexApiSession:
    api = sauth.lex_api(API)
    api.mount("https://", wire)
    api.mount("http://", wire)
    return api


def _proxy_hands_out(*tokens: str):
    """A proxy pull that stores the next token in line, as the real one does on 200."""
    queue = list(tokens)
    calls: list[str] = []

    def pull(cookie_header: str) -> str:
        calls.append(cookie_header)
        if not queue:
            return sauth.PULL_UNAVAILABLE
        sauth._store_tokens(queue.pop(0), refresh=None, expires_in=None)
        return sauth.PULL_OK

    return pull, calls


class TestCluster01fc_LexApiSendsTheCurrentToken(SimpleTestCase):
    """Cluster 1fc: ``lex_api()`` reads, renews and retries the viewer's token per request."""

    # -- 1.1123 --------------------------------------------------------
    def test_1_1123_a_request_carries_the_token_current_when_it_is_sent(self) -> None:
        """
        Scenario 1.1123: the token is read when the request leaves, not when the session was made.
        Given: a session created while token A was current, and the refresher then storing token B
        When: the dashboard sends a PATCH through that session (as from a dialog, minutes later)
        Then: the PATCH carries token B, joined onto the API base URL.
        """
        first, renewed = _token(600), _token(900)
        state = _session(first)
        wire = _Wire(200)
        with patch.object(sauth.st, "session_state", state):
            api = _api(wire)
            sauth._store_tokens(renewed)  # what the background refresher does
            response = api.patch("model_entries/provision/s/one/7", files={"doc": ("a.pdf", b"%PDF")})

        self.assertEqual(response.status_code, 200, "the request must go through")
        self.assertEqual(wire.bearers(), [f"Bearer {renewed}"], "the request must carry the renewed token, not the one current at creation")
        self.assertEqual(wire.sent[0].url, API + "model_entries/provision/s/one/7", "a relative path joins onto the API base URL")

    # -- 1.1124 --------------------------------------------------------
    def test_1_1124_a_token_about_to_expire_is_renewed_before_the_request(self) -> None:
        """
        Scenario 1.1124: a token with less than a minute left is renewed first.
        Given: a token with 30 seconds left, and a proxy able to hand out a fresh one
        When: the dashboard sends a request
        Then: the proxy is asked once and the request carries the fresh token.
        """
        expiring, fresh = _token(30), _token(600)
        state = _session(expiring)
        wire = _Wire(200)
        pull, calls = _proxy_hands_out(fresh)
        with patch.object(sauth.st, "session_state", state), \
             patch.object(sauth, "_live_headers", lambda: {"cookie": "session=abc"}), \
             patch.object(sauth, "_pull_from_proxy", pull):
            _api(wire).get("model_entries/provision/s/one/7")

        self.assertEqual(len(calls), 1, "a token expiring within a minute must be renewed before sending")
        self.assertEqual(wire.bearers(), [f"Bearer {fresh}"], "the request must carry the renewed token")

    # -- 1.1125 --------------------------------------------------------
    def test_1_1125_a_401_renews_once_and_resends_once(self) -> None:
        """
        Scenario 1.1125: a refused token is renewed and the request resent -- once, never in a loop.
        Given: a token the client thinks valid, which the API refuses with 401
        When: the request is sent (a) with a proxy that can renew, the API then accepting;
              (b) the API refusing the renewed token too; (c) the proxy unable to renew
        Then: (a) two requests, the second with the new token, and 200 returned;
              (b) still two requests, the second 401 returned; (c) one request, its 401 returned.
        """
        cases = {
            "renewed and accepted": ((401, 200), (_token(900),), 200, 2),
            "renewed and refused again": ((401, 401), (_token(900), _token(1200)), 401, 2),
            "proxy cannot renew": ((401, 200), (), 401, 1),
        }
        for label, (statuses, handed_out, status, sends) in cases.items():
            with self.subTest(label):
                held = _token(600)
                wire = _Wire(*statuses)
                pull, _calls = _proxy_hands_out(*handed_out)
                with patch.object(sauth.st, "session_state", _session(held)), \
                     patch.object(sauth, "_live_headers", lambda: {"cookie": "session=abc"}), \
                     patch.object(sauth, "_pull_from_proxy", pull):
                    response = _api(wire).patch("x/one/1", data={"a": "1"}, files={"f": ("n.pdf", b"x")})

                self.assertEqual(response.status_code, status, f"{label}: wrong final status")
                self.assertEqual(len(wire.sent), sends, f"{label}: a 401 is retried at most once")
                self.assertEqual(wire.bearers()[0], f"Bearer {held}", f"{label}: the first attempt uses the held token")
                if sends == 2:
                    self.assertEqual(wire.bearers()[1], f"Bearer {handed_out[0]}", f"{label}: the retry uses the renewed token")

    # -- 1.1126 --------------------------------------------------------
    def test_1_1126_a_body_that_cannot_be_replayed_is_not_resent(self) -> None:
        """
        Scenario 1.1126: a streamed body is never sent a second time.
        Given: an upload passed as an open file object, refused with 401
        When: the request is sent
        Then: exactly one request goes out and the 401 is returned to the caller.
        """
        wire = _Wire(401, 200)
        pull, calls = _proxy_hands_out(_token(900))
        with patch.object(sauth.st, "session_state", _session(_token(600))), \
             patch.object(sauth, "_pull_from_proxy", pull):
            response = _api(wire).patch("x/one/1", files={"f": ("n.pdf", io.BytesIO(b"x"))})

        self.assertEqual(response.status_code, 401, "the 401 must reach the caller")
        self.assertEqual(len(wire.sent), 1, "a file object has been read to its end; it must not be resent")
        self.assertEqual(calls, [], "no renewal is spent on a request that cannot be retried")

    # -- 1.1127 --------------------------------------------------------
    def test_1_1127_no_usable_token_raises_instead_of_sending_an_empty_bearer(self) -> None:
        """
        Scenario 1.1127: without a token there is nobody to call the API as.
        Given: a session with no token, and one whose token has expired with no proxy to renew it
        When: the dashboard sends a request
        Then: LexAuthRequired is raised and nothing is sent.
        """
        for label, token in (("no token", ""), ("expired, not renewable", _token(-60))):
            with self.subTest(label):
                wire = _Wire(200)
                pull, _calls = _proxy_hands_out()
                with patch.object(sauth.st, "session_state", _session(token) if token else _State()), \
                     patch.object(sauth, "_live_headers", lambda: {}), \
                     patch.object(sauth, "_pull_from_proxy", pull):
                    with self.assertRaises(sauth.LexAuthRequired, msg=f"{label}: must raise"):
                        _api(wire).get("x")
                self.assertEqual(wire.sent, [], f"{label}: no request may leave without a token")

    # -- 1.1128 --------------------------------------------------------
    def test_1_1128_the_token_goes_to_the_lex_api_only(self) -> None:
        """
        Scenario 1.1128: the viewer's token never leaves for another origin, and the caller cannot stale it.
        Given: a session holding token A
        When: the dashboard calls (a) another origin, absolute or scheme-relative; (b) a relative
              path with a URL in its query; (c) the API's own origin by absolute URL, passing its
              own stale ``authorization`` header
        Then: (a) ValueError and nothing sent; (b) joined onto the API; (c) sent, with exactly one
              Authorization header, token A.
        """
        held = _token(600)
        wire = _Wire(200)
        with patch.object(sauth.st, "session_state", _session(held)):
            api = _api(wire)
            for foreign in ("https://elsewhere.example.test/api/api/x", "//elsewhere.example.test/x"):
                with self.assertRaises(ValueError, msg=f"{foreign} must be refused"):
                    api.get(foreign)
            self.assertEqual(wire.sent, [], "nothing may be sent to another origin")

            api.get("x/?next=https://elsewhere.example.test/")
            self.assertEqual(
                wire.sent.pop().url, API + "x/?next=https://elsewhere.example.test/",
                "a relative path with a URL in its query is still relative",
            )

            api.get("https://app.example.test/api/api/x", headers={"authorization": "Bearer stale", "X-Trace": "1"})

        sent = wire.sent[0]
        self.assertEqual(sent.headers.get("Authorization"), f"Bearer {held}", "the current token replaces the caller's")
        self.assertEqual(sent.headers.get("X-Trace"), "1", "the caller's other headers are kept")
        self.assertEqual(
            [k for k in sent.headers if k.lower() == "authorization"], ["Authorization"],
            "exactly one Authorization header must be sent",
        )

    # -- 1.1129 --------------------------------------------------------
    def test_1_1129_the_default_base_url_follows_the_instance(self) -> None:
        """
        Scenario 1.1129: with no base URL given, lex_api() finds the instance's API.
        Given: LEX_API_URL set; DOMAIN_HOSTED as a bare host or with a scheme; neither
        When: default_api_url() is read
        Then: LEX_API_URL wins; else https://<host>/api/api/ (the documented REST path);
              else the development server's http://localhost:8000/api/.
        """
        cases = (
            ({"LEX_API_URL": "http://backend:8000/api", "DOMAIN_HOSTED": "app.acme.com"}, "http://backend:8000/api/"),
            ({"DOMAIN_HOSTED": "app.acme.com"}, "https://app.acme.com/api/api/"),
            ({"DOMAIN_HOSTED": "http://localhost:3000/"}, "http://localhost:3000/api/api/"),
            ({}, "http://localhost:8000/api/"),
        )
        for env, expected in cases:
            with self.subTest(env=env):
                clean = {k: v for k, v in os.environ.items() if k not in ("LEX_API_URL", "DOMAIN_HOSTED")}
                with patch.dict(os.environ, {**clean, **env}, clear=True):
                    self.assertEqual(sauth.default_api_url(), expected, f"wrong base URL for {env}")
                    self.assertEqual(sauth.lex_api().base_url, expected, "lex_api() must use the default")


class TestCluster01fc_StoredHeadersStayCurrent(SimpleTestCase):
    """Cluster 1fc: a dashboard's stored ``st.session_state.headers`` follows every renewal."""

    # -- 1.1130 --------------------------------------------------------
    def test_1_1130_a_stored_header_dict_is_updated_in_place_on_every_renewal(self) -> None:
        """
        Scenario 1.1130: existing dashboards keep working without a code change.
        Given: st.session_state.headers built from token A, and a reference to that dict held
               elsewhere (as a dashboard's own helper holds it)
        When: the refresher stores token B, then a reconnected socket delivers a newer token C
        Then: the held reference carries B, then C; its other headers are untouched; and the
              deprecation warning pointing at lex_api() is logged once, not per renewal.
        """
        a, b, c = _token(300), _token(600), _token(900)
        state = _session(a)
        state.headers = {"Authorization": f"Bearer {a}", "Accept": "application/json"}
        held = state.headers
        with patch.object(sauth.st, "session_state", state), \
             self.assertLogs(sauth.log, level="WARNING") as logs:
            sauth._store_tokens(b)
            self.assertEqual(held["Authorization"], f"Bearer {b}", "a renewal must reach the dict a dashboard holds")
            self.assertTrue(sauth._adopt_header_token({"authorization": f"Bearer {c}"}), "a newer handshake token is adopted")
            self.assertEqual(held["Authorization"], f"Bearer {c}", "an adopted token must reach the dict too")

        self.assertIs(state.headers, held, "the dict must be updated in place, not replaced")
        self.assertEqual(held["Accept"], "application/json", "other headers are the dashboard's own")
        warnings = [r for r in logs.output if "lex_api()" in r]
        self.assertEqual(len(warnings), 1, "the deprecation warning is logged once per session")

    # -- 1.1131 --------------------------------------------------------
    def test_1_1131_a_header_with_any_other_credential_is_left_alone(self) -> None:
        """
        Scenario 1.1131: only the exact token being replaced is rewritten.
        Given: stored headers carrying another bearer token; an API-key scheme; not a dict at all
        When: the session's token is renewed
        Then: none of them changes, and renewal itself still succeeds.
        """
        a, b = _token(300), _token(600)
        for label, headers in (
            ("another bearer token", {"Authorization": "Bearer someone-elses"}),
            ("an API key", {"Authorization": f"Api-Key {a}"}),
            ("not a dict", f"Bearer {a}"),
        ):
            with self.subTest(label):
                state = _session(a)
                state.headers = headers
                snapshot = dict(headers) if isinstance(headers, dict) else headers
                with patch.object(sauth.st, "session_state", state):
                    sauth._store_tokens(b)
                self.assertEqual(state.headers, snapshot, f"{label}: must not be rewritten")
                self.assertEqual(state.access_token, b, f"{label}: the renewal itself must still land")


class TestCluster01fc_OneHomeForTheTokenLifecycle(SimpleTestCase):
    """Cluster 1fc: the token lifecycle lives in one importable module."""

    # -- 1.1132 --------------------------------------------------------
    def test_1_1132_the_lifecycle_is_importable_and_survives_a_script_rerun(self) -> None:
        """
        Scenario 1.1132: dashboards import the API from the package; a rerun shares the refresher's lock.
        Given: lex.streamlit_app, which Streamlit executes afresh on every script run
        When: it is executed again (a rerun) and the package's public names are imported
        Then: the refresh lock is the same object before and after, and is the auth module's;
              lex_api / current_access_token / LexAuthRequired are exported by lex.lex_app.streamlit.
        """
        before = sa._TOKEN_REFRESH_LOCK
        rerun = importlib.reload(sa)
        self.assertIs(rerun._TOKEN_REFRESH_LOCK, before, "a rerun must not create a second refresh lock")
        self.assertIs(rerun._TOKEN_REFRESH_LOCK, sauth._TOKEN_REFRESH_LOCK, "streamlit_app must use the auth module's lock")
        self.assertIs(rerun.renew_access_token, sauth.renew_access_token, "streamlit_app must re-export, not redefine")

        package = importlib.import_module("lex.lex_app.streamlit")

        for name in ("lex_api", "current_access_token", "LexApiSession", "LexAuthRequired"):
            self.assertIn(name, package.__all__, f"{name} must be public")
            self.assertIs(getattr(package, name), getattr(sauth, name), f"{name} must come from the auth module")
