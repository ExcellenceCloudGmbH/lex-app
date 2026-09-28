"""Cluster 1as: a Keycloak access token too large for its cookie is stored compressed.

Intent
------
Reflex Enterprise keeps a user's session in cookies -- the access token in one of
them -- and keeps a user's tabs in step through those cookies. A browser refuses
a cookie over 4096 bytes, without an error, and a Keycloak access token carries
every role the user holds, in every client of the realm: in a realm with many
clients it is easily larger. Then nothing holds the session but the server's
memory -- a restart of the Reflex server or a new tab signs the user out, and one
tab's sign-in signs the others out -- and where Keycloak's configuration cannot
change, lex-app has to keep the token some other way. It stores it compressed (a
JWT's payload deflates to a fraction) and restores it, byte for byte, where the
plugin reads the cookie, so the rest of the plugin's session handling runs as it
always does:

* only a token that would not fit is compressed, only once, and only if that
  makes it smaller -- every other value is exactly the plugin's own;
* the restored token is the one Keycloak issued: the ``at_hash`` the ID token is
  checked against, the bearer token sent on, and the hash a user's tabs compare
  all come from it;
* a cookie the browser sends back -- after a restart, in a new tab -- is read the
  same way;
* a damaged or forged cookie is no token: never an error, never an unbounded
  allocation;
* the log says it happened, once, and warns when a token is too large even so.

Cluster 1as -- scenarios 1.376-1.380. Type: U.
Covers: lex/lex_app/reflex/auth.py (``LexKeycloakAuthState._set_tokens``,
``_fit_access_token_cookie``, ``_compress_token``/``_decompress_token``,
``_read_access_token_cookie`` as ``AccessTokenMetadata.from_cookie_value``,
``_report_oversized_token_cookies``).
Run: python -m lex pytest lex/test_project/tests/init/test_1as_reflex_token_cookie_compression.py -v
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import random
import secrets
import string
import zlib
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import AsyncMock, patch
from urllib.parse import parse_qsl, quote

import pytest
from asgiref.sync import async_to_sync
from reflex_enterprise.auth import OIDCAuthState
from reflex_enterprise.auth.oidc.state import AccessTokenMetadata
from reflex_enterprise.auth.oidc.utils import compute_at_hash

import lex.lex_app.reflex.auth as auth
from lex.lex_app.reflex.auth import LexKeycloakAuthState

pytestmark = pytest.mark.init

#: The access token's cookie, by the name a browser shows it under.
ACCESS_COOKIE = "_oidc_lex_keycloak_access_token_data_partitioned"
LIMIT = 4096

_WORDS = [
    "finance", "budget", "forecast", "portfolio", "risk", "ledger", "invoice", "treasury", "audit",
    "compliance", "payroll", "asset", "liability", "cashflow", "valuation", "reporting", "import",
    "export", "calculation", "approval", "planning", "controlling", "fund", "investor", "tax",
]


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _keycloak_access_token(clients: int, seed: int = 1) -> str:
    """A JWT shaped like a Keycloak access token, with the user's roles in ``clients`` clients.

    Client and role names vary, as in a realm that serves many projects -- the
    harder case for compression.
    """
    rnd = random.Random(seed)

    def client() -> str:
        return "-".join(rnd.sample(_WORDS, 2)) + "-" + "".join(rnd.choices(string.ascii_lowercase, k=5))

    def role() -> str:
        return rnd.choice(["ROLE_", "", "app-", "can-"]) + "-".join(rnd.sample(_WORDS, 2))

    names = [client() for _ in range(clients)]
    payload = {
        "exp": 1790000300, "iat": 1790000000, "jti": f"onrtac:{rnd.getrandbits(128):032x}",
        "iss": "https://kc.example.com/realms/lex", "aud": names + ["account"], "sub": "f1e2d3",
        "typ": "Bearer", "azp": "lex-rp", "sid": f"{rnd.getrandbits(128):032x}",
        "realm_access": {"roles": ["offline_access", "uma_authorization"] + [role() for _ in range(8)]},
        "resource_access": {name: {"roles": [role() for _ in range(6)]} for name in names},
        "scope": "openid email profile", "preferred_username": "ada",
    }
    header = {"alg": "RS256", "typ": "JWT", "kid": _b64url(rnd.randbytes(24))}
    return ".".join([
        _b64url(json.dumps(header).encode()),
        _b64url(json.dumps(payload, separators=(",", ":")).encode()),
        _b64url(rnd.randbytes(256)),  # an RS256 signature: incompressible
    ])


def _cookie_value(token: str) -> str:
    """The value the plugin stores for ``token`` (``_set_tokens_payload_from_exchange``)."""
    return AccessTokenMetadata(access_token=token, expires_at=1790000300.5).to_cookie_value()


def _provider(cookie_header: str = ""):
    """The provider of one session, whose connection sent ``cookie_header``."""
    from reflex.istate.data import HeaderData, ReflexURL, RouterData
    from reflex.state import State

    root = State()
    root.router = RouterData(url=ReflexURL("http://localhost:8502/"), headers=HeaderData(cookie=cookie_header))
    return root.get_substate(LexKeycloakAuthState.get_full_name().split("."))


def _access_token(provider) -> str:
    """The access token the plugin itself reads, and sends to Keycloak and the Lex App API."""

    async def read():
        return await provider._access_token

    return async_to_sync(read)()


def _stored_by(set_tokens: AsyncMock) -> str:
    """The access-token cookie's value lex-app handed the plugin to store."""
    return set_tokens.await_args.args[0]


class TestCluster01as_TokenCookieCompression(TestCase):
    """A token too large for its cookie is kept compressed, and read back as issued."""

    # -- 1.376 ---------------------------------------------------------
    def test_1_376_a_token_too_large_is_stored_compressed_and_read_back_as_issued(self) -> None:
        """
        Scenario 1.376: a Keycloak token too large for its cookie is stored compressed.
        Given: a Keycloak-like access token whose cookie a browser would refuse.
        When:  the plugin stores it (its own ``_set_tokens``, as after the
               callback or a refresh).
        Then:  the cookie holds it compressed, small enough for a browser to
               keep; the token the plugin reads back is the one Keycloak issued,
               byte for byte; the ``at_hash`` the ID token is checked against is
               computed from it, and so is the hash a user's tabs compare.
        """
        token = _keycloak_access_token(30)
        value = _cookie_value(token)
        self.assertGreater(auth._cookie_size(ACCESS_COOKIE, value), LIMIT, "precondition: too large as issued")

        provider = _provider()
        # The ID token's signature is checked against Keycloak's keys; everything
        # else is the plugin's own storing of the tokens.
        verified = SimpleNamespace(header={"alg": "RS256"})
        with patch.object(LexKeycloakAuthState, "_verify_jwt", AsyncMock(return_value=verified)), \
                patch.object(LexKeycloakAuthState, "_validate_tokens", AsyncMock(return_value=True)):
            async_to_sync(provider._set_tokens)(value, id_token="header.claims.signature", refresh_token="refresh")

        stored = str(provider._access_token_data)
        self.assertTrue(dict(parse_qsl(stored))["access_token"].startswith(auth._COMPRESSED_TOKEN_MARK))
        self.assertLessEqual(auth._cookie_size(ACCESS_COOKIE, stored), LIMIT, "a browser keeps it now")
        self.assertEqual(_access_token(provider), token, "the token read back is the one issued")
        self.assertEqual(provider._expected_at_hash, compute_at_hash(token, "RS256"),
                         "the ID token is checked against the token as issued")
        self.assertEqual(AccessTokenMetadata.from_cookie_value(stored).sha256_hash(),
                         hashlib.sha256(token.encode("utf-8")).hexdigest(),
                         "tabs compare the token as issued, so a compressed one agrees with itself")
        self.assertEqual(AccessTokenMetadata.from_cookie_value(stored).expires_at, 1790000300.5)

    # -- 1.377 ---------------------------------------------------------
    def test_1_377_only_a_token_that_would_not_fit_is_compressed_only_once_only_if_smaller(self) -> None:
        """
        Scenario 1.377: compression only where it is needed, and helps.
        Given: a token that fits, the stored value the popup hands its opener,
               a random opaque token too large to fit, and a compressible
               opaque one.
        When:  each is stored.
        Then:  one that fits is stored exactly as the plugin would; the popup's
               value, compressed already, as it came -- even one still too large,
               which compressing again would corrupt; a random token, which
               compressing would only enlarge, as the plugin would; an opaque
               token that does compress, compressed whole, and read back intact.
        """
        provider = _provider()
        with patch.object(OIDCAuthState, "_set_tokens", AsyncMock()) as set_tokens:
            fits = _cookie_value(_keycloak_access_token(3))
            async_to_sync(provider._set_tokens)(fits)
            self.assertEqual(_stored_by(set_tokens), fits, "a token that fits is the plugin's own")

            async_to_sync(provider._set_tokens)(_cookie_value(_keycloak_access_token(30)))
            compressed = _stored_by(set_tokens)
            async_to_sync(provider._set_tokens)(compressed)
            self.assertEqual(_stored_by(set_tokens), compressed, "the popup's handoff is not compressed twice")

            # Too large even compressed: its base64 would shrink once more, and a
            # token compressed twice no longer restores to the one issued.
            huge = _keycloak_access_token(160)
            with self.assertLogs("lex.lex_app.reflex.auth", logging.INFO), \
                    patch.object(auth, "_COMPRESSION_REPORTED", set()), patch.object(auth, "_OVERSIZED_REPORTED", set()):
                async_to_sync(provider._set_tokens)(_cookie_value(huge))
                still_too_large = _stored_by(set_tokens)
                async_to_sync(provider._set_tokens)(still_too_large)
            self.assertEqual(_stored_by(set_tokens), still_too_large, "not even when it is still too large")
            self.assertEqual(AccessTokenMetadata.from_cookie_value(still_too_large).access_token, huge)

            random_token = _cookie_value(secrets.token_urlsafe(4000))
            async_to_sync(provider._set_tokens)(random_token)
            self.assertEqual(_stored_by(set_tokens), random_token, "compressing only enlarges a random token")

            opaque = "opaque-token:" + "scope=report-designer;" * 250
            async_to_sync(provider._set_tokens)(_cookie_value(opaque))
            stored = _stored_by(set_tokens)
        self.assertLessEqual(auth._cookie_size(ACCESS_COOKIE, stored), LIMIT)
        self.assertEqual(AccessTokenMetadata.from_cookie_value(stored).access_token, opaque)

    # -- 1.378 ---------------------------------------------------------
    def test_1_378_a_damaged_or_forged_compressed_cookie_is_no_token(self) -> None:
        """
        Scenario 1.378: a compressed cookie is read defensively.
        Given: cookie values that carry the compressed mark but not a token
               lex-app stored -- not base64, not a whole stream, too many parts,
               an unknown form, a stream that inflates far beyond any token --
               and the plugin's own values.
        When:  the plugin reads them, and a session receives one from the browser.
        Then:  each marked one is no token at all -- never an error, never an
               unbounded allocation -- so the session is simply signed out; the
               plugin's own values read exactly as before.
        """
        mark = auth._COMPRESSED_TOKEN_MARK
        good = dict(parse_qsl(_cookie_value(_keycloak_access_token(30))))
        with patch.object(OIDCAuthState, "_set_tokens", AsyncMock()) as set_tokens:
            async_to_sync(_provider()._set_tokens)(_cookie_value(_keycloak_access_token(30)))
        header, payload, signature = dict(parse_qsl(_stored_by(set_tokens)))["access_token"][len(mark) + 4:].split(".")
        bomb = _b64url(zlib.compress(b"0" * (auth._INFLATED_TOKEN_LIMIT + 1)))
        forged = {
            "not base64": f"{mark}jwt.{header}.!!!.{signature}",
            "truncated": f"{mark}jwt.{header}.{payload[: len(payload) // 2]}.{signature}",
            "too many parts": f"{mark}jwt.{header}.{payload}.{signature}.extra",
            "unknown form": f"{mark}zip.{payload}",
            "inflates past the cap": f"{mark}raw.{bomb}",
            "not UTF-8": f"{mark}raw.{_b64url(zlib.compress(bytes([0xff, 0xfe, 0x00])))}",
        }
        for what, stored in forged.items():
            with self.subTest(what):
                value = f"access_token={quote(stored)}&expires_at=1790000300.5"
                self.assertIsNone(AccessTokenMetadata.from_cookie_value(value))
                self.assertEqual(_access_token(_provider(f'{ACCESS_COOKIE}="{value}"')), "",
                                 "a session holding it is signed out, not broken")

        for value in (_cookie_value(_keycloak_access_token(3)), f"access_token={good['access_token']}", "", "junk"):
            with self.subTest(plugin_value=value[:24]):
                self.assertEqual(AccessTokenMetadata.from_cookie_value(value),
                                 auth._PLUGIN_READ_ACCESS_TOKEN_COOKIE(AccessTokenMetadata, value))

    # -- 1.379 ---------------------------------------------------------
    def test_1_379_the_log_says_so_once_and_warns_when_too_large_even_compressed(self) -> None:
        """
        Scenario 1.379: compressing is logged once; a token too large even so is a warning.
        Given: a token that fits only compressed, one too large even compressed,
               and a random one compressing cannot shrink.
        When:  each is stored, twice.
        Then:  the first is logged once, with both sizes, and warns of nothing;
               the second warns once that its cookie is too large even
               compressed; the third warns once that it is too large -- each
               naming the cookie and Keycloak's "Full scope allowed".
        """
        provider = _provider()
        with patch.object(OIDCAuthState, "_set_tokens", AsyncMock()), \
                patch.object(auth, "_COMPRESSION_REPORTED", set()), patch.object(auth, "_OVERSIZED_REPORTED", set()):
            fits_compressed = _cookie_value(_keycloak_access_token(30))
            with self.assertLogs("lex.lex_app.reflex.auth", logging.INFO) as logged:
                async_to_sync(provider._set_tokens)(fits_compressed)
                async_to_sync(provider._set_tokens)(fits_compressed)
            (info,) = logged.output
            self.assertIn("INFO", info)
            self.assertIn("stored compressed", info)
            self.assertIn(str(auth._cookie_size(ACCESS_COOKIE, fits_compressed)), info, "the size as issued")

        with patch.object(OIDCAuthState, "_set_tokens", AsyncMock()), \
                patch.object(auth, "_COMPRESSION_REPORTED", set()), patch.object(auth, "_OVERSIZED_REPORTED", set()):
            with self.assertLogs("lex.lex_app.reflex.auth", logging.WARNING) as logged:
                for _ in range(2):
                    async_to_sync(provider._set_tokens)(_cookie_value(_keycloak_access_token(160)))
            (warning,) = logged.output
            self.assertIn("even compressed", warning)
            self.assertIn(ACCESS_COOKIE, warning)
            self.assertIn("Full scope allowed", warning)

        with patch.object(OIDCAuthState, "_set_tokens", AsyncMock()), \
                patch.object(auth, "_COMPRESSION_REPORTED", set()), patch.object(auth, "_OVERSIZED_REPORTED", set()):
            with self.assertLogs("lex.lex_app.reflex.auth", logging.WARNING) as logged:
                for _ in range(2):
                    async_to_sync(provider._set_tokens)(_cookie_value(secrets.token_urlsafe(4000)))
            (warning,) = logged.output
            self.assertNotIn("even compressed", warning, "it was not compressed")
            self.assertIn("Full scope allowed", warning)

    # -- 1.380 ---------------------------------------------------------
    def test_1_380_a_cookie_the_browser_sends_back_is_read_the_same_way(self) -> None:
        """
        Scenario 1.380: after a restart or in a new tab, the browser's cookie restores the token.
        Given: a session that holds no tokens of its own -- a new tab, or the
               Reflex server just restarted -- whose browser sends the
               compressed access-token cookie.
        When:  the plugin reads the access token, as the page guard does.
        Then:  it is the token Keycloak issued: the plugin reads the cookie a
               browser sends through the same function it reads its own with.
        """
        token = _keycloak_access_token(30)
        with patch.object(OIDCAuthState, "_set_tokens", AsyncMock()) as set_tokens:
            async_to_sync(_provider()._set_tokens)(_cookie_value(token))
        stored = _stored_by(set_tokens)

        new_tab = _provider(f'other=1; {ACCESS_COOKIE}="{stored}"; theme=dark')
        self.assertEqual(_access_token(new_tab), token)

        async def signed_in_tokens():
            return await new_tab.has_any_token

        self.assertTrue(async_to_sync(signed_in_tokens)())
