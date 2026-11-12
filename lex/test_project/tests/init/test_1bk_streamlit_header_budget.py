"""The Streamlit handshake stays inside everyone's header budget.

Intent: the auth proxy hands Streamlit the user's Keycloak access token in the
headers of the WebSocket handshake, so the token *is* a header line -- and three
header lines, because the same bytes went out as ``Authorization``,
``X-Forwarded-Access-Token`` and ``X-Streamlit-Access-Token``. Two limits sit on
that line: the ``websockets`` library refuses a header line over
``WEBSOCKETS_MAX_LINE_LENGTH`` (8192 by default), and the ingress refuses one
over 16 KB.

In LEX-894 a user with access to many instances carried an ~8.3 KB token,
because every per-instance Keycloak client ran with full scope and so put every
client's roles and audience into every instance's token. The dashboard then
never loaded, and what made it an outage rather than an error was the shape of
the failure: uvicorn's ``websockets-sansio`` handler neither answered the
handshake nor closed the socket when the parse failed, so the browser waited on
an open connection with nothing in any log naming the cause.

LEX-898 removes the cause in Keycloak (the client no longer runs with full
scope, so the token carries only that instance's roles). The two guards here are
what keeps the symptom from coming back for some other reason:

* a header-line ceiling installed before anything can import ``websockets`` --
  worthless if it is installed one import too late, which is why the ordering is
  asserted on the source, not just the effect;
* one copy of the credential upstream instead of three, which also removes a
  second ``Authorization`` header the proxy used to send alongside a caller's
  own.

A regression is invisible until a token grows: every one of these tests passes
trivially with a small token and the old code, which is exactly why they assert
the mechanism rather than a size.

Cluster 1bk — scenarios 1.619–1.625. Type: U.
Covers: lex/ws_header_limits.py (apply_websocket_header_limits,
        websockets_line_limit_shortfall), lex/bin/lex.py (import order,
        `streamlit` launch diagnostic), lex/proxy.py (import order, ws_proxy and
        proxy credential forwarding), lex/streamlit_app.py (bearer_from_headers).
Run: python -m lex pytest lex/test_project/tests/init/test_1bk_streamlit_header_budget.py -v
"""

from __future__ import annotations

import importlib
import json
import os
import re
import time
from base64 import b64encode
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

import httpx
import itsdangerous
import jwt
import pytest
from django.test import SimpleTestCase
from starlette.testclient import TestClient

import lex.proxy as proxy
import lex.streamlit_app as sa
import lex.ws_header_limits as ws_header_limits

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


class _RawCapturingUpstream:
    """Stands in for the Streamlit upstream, keeping the header names verbatim.

    Deliberately *not* lower-cased on the way in, unlike the stub in 1ac: the
    defect this batch closes is two headers that differ only in case
    (Starlette hands the proxy ``authorization``; the proxy used to add
    ``Authorization`` beside it), and a dict keyed on the lowered name cannot
    see it.
    """

    last_raw: dict = {}

    @classmethod
    async def send(cls, method, url, *, content=None, headers=None):
        cls.last_raw = dict(headers or {})
        return httpx.Response(200, content=b"upstream-ok")

    @classmethod
    def names(cls, header: str) -> list:
        """Every value sent under ``header``, however each copy was spelled."""
        return [v for k, v in cls.last_raw.items() if k.lower() == header.lower()]


class _FakeUpstreamWS:
    """Stands in for the Streamlit WebSocket, keeping the handshake list as sent."""

    captured: list = []
    subprotocol = None

    def __init__(self, url, **kwargs):
        _FakeUpstreamWS.captured = list(kwargs.get(proxy.WS_HEADER_KWARG) or [])

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def send(self, data):
        pass

    async def recv(self):
        import websockets

        raise websockets.exceptions.ConnectionClosedOK(None, None)

    @classmethod
    def names(cls, header: str) -> list:
        return [v for k, v in cls.captured if k.lower() == header.lower()]


def _source_lines(module_relative_path: str) -> list:
    """The source of a shipped module, read from the checkout under test."""
    root = Path(__file__).resolve().parents[3]  # .../lex
    return (root / module_relative_path).read_text(encoding="utf-8").splitlines()


@contextmanager
def _only_limit_attribute(module, attribute: str, value: int):
    """``module`` carrying exactly one of the two limit names, set to ``value``.

    A release carries one or the other, never both, and which one it is *is* the
    signal: websockets 13 renamed ``MAX_LINE`` to ``MAX_LINE_LENGTH`` in the
    release that started reading the environment. Patching one name while the
    other is still on the module would test a version that has never existed.
    """
    sentinel = object()
    originals = {name: getattr(module, name, sentinel) for name in ws_header_limits.WEBSOCKETS_LIMIT_ATTRIBUTES}
    try:
        for name in originals:
            if hasattr(module, name):
                delattr(module, name)
        setattr(module, attribute, value)
        yield
    finally:
        for name, original in originals.items():
            if hasattr(module, name):
                delattr(module, name)
            if original is not sentinel:
                setattr(module, name, original)


def _first_import_matching(lines, pattern: str) -> int:
    """Line index of the first *import statement* matching ``pattern``.

    An import statement, not a mention: ``lex/proxy.py`` imports
    ``starlette.websockets`` (an ASGI type, nothing to do with the library)
    eighteen lines before the import this ordering is about, and a substring
    search would anchor on it and pass whatever the file does.
    """
    expression = re.compile(pattern)
    for index, line in enumerate(lines):
        if expression.match(line.strip()):
            return index
    return -1


# ---------------------------------------------------------------------------
# the ceiling: high enough, overridable, and installed in time
# ---------------------------------------------------------------------------
class TestCluster01bk_WebsocketHeaderLimit(SimpleTestCase):
    """Cluster 1bk: a header line can never be what silently hangs a handshake."""

    def tearDown(self):
        # The module applies its default at import time; leave the process with
        # the value it had before a test reloaded it under a patched env.
        importlib.reload(ws_header_limits)

    def test_1_619_the_line_limit_defaults_well_above_any_plausible_token(self):
        """
        Scenario 1.619: an unconfigured deployment gets the ceiling anyway.
        Given: an environment with no WEBSOCKETS_MAX_LINE_LENGTH
        When: lex.ws_header_limits is imported
        Then: the variable is set to 65536 — the library's own 8192 default is
              smaller than a token this platform has actually issued, and the
              failure it produces is an open socket and a silent page, so the
              safe value has to be the one nobody has to know to set
        """
        with patch.dict(os.environ):
            os.environ.pop(ws_header_limits.WEBSOCKETS_MAX_LINE_LENGTH_ENV, None)
            importlib.reload(ws_header_limits)

            self.assertEqual(
                os.environ.get(ws_header_limits.WEBSOCKETS_MAX_LINE_LENGTH_ENV), "65536",
                "Without the default, an 8 KB header line hangs the handshake with nothing in the log.",
            )
            self.assertGreaterEqual(
                int(os.environ[ws_header_limits.WEBSOCKETS_MAX_LINE_LENGTH_ENV]), 8 * 1024 * 2,
                "A ceiling at or below the library's own default would not be a ceiling.",
            )

    def test_1_620_an_operators_own_limit_is_never_overwritten(self):
        """
        Scenario 1.620: the default is a default, not a policy.
        Given: WEBSOCKETS_MAX_LINE_LENGTH already set in the environment
        When: lex.ws_header_limits is imported
        Then: the configured value survives — lex-1483 was brought back up by
              setting this very variable by hand, and a release that silently
              replaced an operator's value would make the incident workaround
              and the fix fight each other
        """
        with patch.dict(os.environ, {ws_header_limits.WEBSOCKETS_MAX_LINE_LENGTH_ENV: "131072"}):
            importlib.reload(ws_header_limits)

            self.assertEqual(
                os.environ[ws_header_limits.WEBSOCKETS_MAX_LINE_LENGTH_ENV], "131072",
                "setdefault, not set: the deployment's own value has to win.",
            )

    def test_1_621_the_limit_is_installed_before_websockets_can_be_imported(self):
        """
        Scenario 1.621: the ordering the whole guard rests on.
        Given: the two modules that reach the websockets library — lex/proxy.py
               imports it, lex/bin/lex.py imports the uvicorn that will
        When: their import blocks are read
        Then: lex.ws_header_limits is imported first — websockets fixes the
              limit as websockets.http11 is imported and never re-reads it, so a
              correct value applied one import too late has no effect at all,
              and nothing observable at runtime would say so
        """
        reaches_websockets = {
            # The CLI never imports the library itself; uvicorn does, when a
            # server starts. This is the import that leads there.
            "bin/lex.py": r"import\s+uvicorn\b",
            "proxy.py": r"(from|import)\s+websockets\b",
        }
        for module_path, later_import in reaches_websockets.items():
            with self.subTest(module=module_path):
                lines = _source_lines(module_path)
                limits_at = _first_import_matching(lines, r"from\s+lex\s+import\s+ws_header_limits\b")
                websockets_at = _first_import_matching(lines, later_import)

                self.assertNotEqual(
                    limits_at, -1,
                    f"lex/{module_path} no longer installs the header-line limit at all.",
                )
                self.assertNotEqual(
                    websockets_at, -1,
                    f"lex/{module_path} no longer reaches websockets this way; re-aim this guard.",
                )
                self.assertLess(
                    limits_at, websockets_at,
                    f"lex/{module_path} can reach websockets before the limit is applied, "
                    "which leaves the limit with no effect.",
                )

    def test_1_625_an_installed_websockets_that_ignores_the_limit_is_reported(self):
        """
        Scenario 1.625: a ceiling that is not in force says so, under either name.
        Given: a websockets release that parses at its own hard-coded limit
        When: the shortfall check runs
        Then: it returns a sentence naming both numbers — the variable is only
              read from websockets 13, and an older one leaves the handshake
              dying at 8 KB while the environment says 65536; silence there is
              the LEX-894 failure all over again, with the fix apparently applied.
              Checked for both attribute names: websockets 13 renamed MAX_LINE to
              MAX_LINE_LENGTH in the very release that started reading the
              variable, so a check that knows one name is a no-op on exactly the
              versions it has to tell apart
        """
        from websockets import http11

        for attribute in ws_header_limits.WEBSOCKETS_LIMIT_ATTRIBUTES:
            with self.subTest(attribute=attribute):
                with _only_limit_attribute(http11, attribute, 8192), \
                     patch.dict(os.environ, {ws_header_limits.WEBSOCKETS_MAX_LINE_LENGTH_ENV: "65536"}):
                    shortfall = ws_header_limits.websockets_line_limit_shortfall()

                self.assertIsNotNone(shortfall, "An ignored limit must not pass as an applied one.")
                self.assertIn("8192", shortfall)
                self.assertIn("65536", shortfall)

                with _only_limit_attribute(http11, attribute, 65536), \
                     patch.dict(os.environ, {ws_header_limits.WEBSOCKETS_MAX_LINE_LENGTH_ENV: "65536"}):
                    self.assertIsNone(
                        ws_header_limits.websockets_line_limit_shortfall(),
                        "A limit in force must not warn; a warning nobody can act on gets ignored.",
                    )


# ---------------------------------------------------------------------------
# the credential: one copy upstream, and still readable there
# ---------------------------------------------------------------------------
class TestCluster01bk_SingleCredentialHeader(SimpleTestCase):
    """Cluster 1bk: the access token crosses to Streamlit exactly once."""

    def setUp(self):
        self.client = TestClient(proxy.app)
        _RawCapturingUpstream.last_raw = {}
        _FakeUpstreamWS.captured = []

    @staticmethod
    async def _tokens(_session):
        return {"access_token": _token(_epoch(600)), "refresh_token": "rt-session", "expires_at": _epoch(600)}

    def test_1_622_a_proxied_request_carries_the_token_once(self):
        """
        Scenario 1.622: three copies of one credential became one.
        Given: an authenticated request to the dashboard
        When: it is forwarded to Streamlit
        Then: the token appears only as Authorization — X-Forwarded-Access-Token
              and X-Streamlit-Access-Token repeated the same bytes for no reader,
              which on a pre-LEX-898 token was ~25 KB of headers against the
              ingress's 16 KB line budget
        """
        handoff = _token(_epoch(900), sub="handoff")

        with patch.object(proxy, "validate_jwt_token", lambda t: {"sub": "handoff", "exp": _epoch(900)}), \
             patch.object(proxy, "_ensure_valid_access_token", self._tokens), \
             patch.object(proxy, "PERSIST_JWT_AUTH_TO_SESSION", False), \
             patch.object(proxy, "_upstream_send", _RawCapturingUpstream.send):
            response = self.client.get(f"/dashboard?auth_token={handoff}")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            _RawCapturingUpstream.names("authorization"), [f"Bearer {handoff}"],
            "Streamlit's reader takes Authorization; it must be there, exactly once.",
        )
        for retired in ("x-forwarded-access-token", "x-streamlit-access-token"):
            with self.subTest(header=retired):
                self.assertEqual(
                    _RawCapturingUpstream.names(retired), [],
                    f"{retired} is a second copy of the same credential and must not be sent.",
                )

    def test_1_623_a_callers_own_authorization_does_not_travel_beside_ours(self):
        """
        Scenario 1.623: one Authorization header, not two that differ in case.
        Given: a request that already carries an Authorization header
        When: the proxy forwards it with the token it authenticated
        Then: only the proxy's copy goes upstream — `authorization` is in
              _HOP_BY_HOP, which was housekeeping while a third header outranked
              Authorization in the upstream's reader and is load-bearing now that
              this is the only copy: Starlette hands header names lower-cased, so
              the caller's and the proxy's are different dict keys, both would be
              sent, and the upstream joins them with a comma into a credential
              that parses as neither
        """
        handoff = _token(_epoch(900), sub="handoff")

        with patch.object(proxy, "validate_jwt_token", lambda t: {"sub": "handoff", "exp": _epoch(900)}), \
             patch.object(proxy, "_ensure_valid_access_token", self._tokens), \
             patch.object(proxy, "PERSIST_JWT_AUTH_TO_SESSION", False), \
             patch.object(proxy, "_upstream_send", _RawCapturingUpstream.send):
            response = self.client.get(
                f"/dashboard?auth_token={handoff}",
                headers={"Authorization": "Bearer a-callers-own-token"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            _RawCapturingUpstream.names("authorization"), [f"Bearer {handoff}"],
            "Two Authorization headers reach the upstream as one unparseable value.",
        )

    def test_1_624_the_handshake_carries_one_header_the_dashboard_can_read(self):
        """
        Scenario 1.624: the single copy survives the hop that matters.
        Given: a WebSocket handshake from a live proxy session
        When: it is proxied to Streamlit
        Then: the token is on the handshake once, as Authorization, and
              lex/streamlit_app.py's own reader still finds it — the handshake
              is the only point at which a connection that then stays open for
              hours is handed anything, so a credential that is sent but not
              read is indistinguishable from one that was never sent
        """
        with patch.object(proxy, "validate_jwt_token", lambda t: {"sub": "u1", "exp": _epoch(300)}), \
             patch.object(proxy, "_ensure_valid_access_token", self._tokens), \
             patch.object(proxy, "ws_connect", _FakeUpstreamWS):
            self.client.cookies.update({"st_access": _token(_epoch(300)), "session": _session_cookie()})
            with self.client.websocket_connect("/_stcore/stream"):
                pass

        sent = _FakeUpstreamWS.names("authorization")
        self.assertEqual(len(sent), 1, "The handshake must carry the credential exactly once.")
        self.assertTrue(sent[0].startswith("Bearer "))

        for retired in ("x-forwarded-access-token", "x-streamlit-access-token"):
            with self.subTest(header=retired):
                self.assertEqual(
                    _FakeUpstreamWS.names(retired), [],
                    f"{retired} tripled the longest line on the handshake and must not be sent.",
                )

        handshake = {k.lower(): v for k, v in _FakeUpstreamWS.captured}
        self.assertEqual(
            sa.bearer_from_headers(handshake), sent[0].split(" ", 1)[1],
            "Streamlit reads the handshake headers once, at connect; an unread credential is no credential.",
        )
