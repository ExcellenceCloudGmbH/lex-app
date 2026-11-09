"""MCP embeds sign in with a single-use ticket, into a read-only cookie (lex-app 2.5, plan §6.5).

Intent: the MCP tool ``lex_embed_view`` puts the app inside an MCP host's iframe (Claude, ChatGPT,
VS Code ...), which has none of the user's cookies. It used to carry the MCP client's access token
in the frame's URL (``auth_token``) -- in history, in server logs and in every ``Referer``. Now the
URL carries ``lex_embed_ticket``: single-use, valid 60 seconds, worthless once the frame has used
it. The app keeps the token server-side (a refresh-less sign-in that ends with the token), redeems
the ticket on the frame's first request into a ``__Host-lex_embed`` cookie that lives no longer than
the token, and lets that cookie read -- never write: the cookie is ``SameSite=None``, so a write
through it would be a cross-site request forgery waiting to happen.

A regression either puts a bearer token back into URLs, lets a replayed or stale ticket sign
someone in, or turns the embed into a cross-site write channel. A security review added
1.554-1.556: the ticket's clean-URL redirect never leaves the app origin, the frame that holds an
embed may render its ticket again without losing it, and a HEAD (a prefetcher) never spends a
ticket; and 1.551 now keeps the ticketed URL out of everything the model sees (``_meta`` only).

Cluster 1bf -- scenarios 1.541-1.556. Type: I (transactional: the MCP tool issues tickets from a
worker thread, as it does under an MCP server).
Covers: lex/authentication/dashboards/embed.py (issue_embed_ticket, EmbedTicketMiddleware,
        _without_ticket), lex/authentication/dashboards/handoff.py (aud="embed", holder_handle),
        lex/authentication/dashboards/broker.py, lex/authentication/dashboards/sites.py (local_path),
        lex/mcp_server/tools/embed.py (_embed_view_inner, _issue_embed_ticket, the widget's metaUrl),
        lex/lex_app/settings.py (MIDDLEWARE).
Run: python -m lex pytest lex/test_project/tests/init/test_1bf_mcp_embed_ticket.py -v
"""

from __future__ import annotations

import asyncio
import hashlib
import importlib
import importlib.util
import json
import os
import sys
import time
import types
from datetime import timedelta
from unittest.mock import patch
from urllib.parse import parse_qs, urljoin, urlsplit

import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import Client, TransactionTestCase, override_settings
from django.utils import timezone
from oauth2_authcodeflow import sessions as oidc_sessions
from oauth2_authcodeflow.models import OIDCSession
from oauth2_authcodeflow.testing import FakeOP, make_signed_token

from lex.authentication.dashboards import embed as embed_tickets
from lex.authentication.models import DashboardBinding, DashboardHandoffCode
from lex.lex_app.tests.auth import oidc_test_settings, sign_in, stub_keycloak_uma

pytestmark = pytest.mark.init

APP_HOST = "app.example.com"
APP = f"https://{APP_HOST}"
COOKIE = "__Host-lex_embed"
TICKET = "lex_embed_ticket"


def _cookie_line(response, name: str = COOKIE) -> str:
    morsel = response.cookies.get(name)
    return morsel.output(header="").strip() if morsel is not None else ""


def _stays_on(origin: str, target: str) -> bool:
    """Whether a browser following ``target`` from a page on ``origin`` stays on it (browsers read
    ``\\`` as ``/`` in http(s) URLs and drop tabs and newlines)."""
    cleaned = "".join(ch for ch in target if ch not in "\t\n\r").replace("\\", "/")
    resolved = urlsplit(urljoin(origin + "/", cleaned))
    return f"{resolved.scheme}://{resolved.netloc}" == origin


def _attributes(line: str) -> dict:
    out = {}
    for part in line.split(";")[1:]:
        name, sep, value = part.strip().partition("=")
        out[name.lower()] = value if sep else True
    return out


class _EmbedCase(TransactionTestCase):
    """A FakeOP the MCP tokens come from, and the app answering on its own host."""

    def setUp(self) -> None:
        super().setUp()
        self.op = FakeOP()
        overrides = override_settings(**self.op.django_settings(), **oidc_test_settings(),
                                      ALLOWED_HOSTS=[APP_HOST, "other.example.com", "testserver"])
        overrides.enable()
        self.addCleanup(overrides.disable)
        self.op.install()
        self.addCleanup(self.op.uninstall)
        uma = stub_keycloak_uma([])
        uma.start()
        self.addCleanup(uma.stop)
        self.client = Client()

    def token(self, *, sub: str = "mcp-user", email: str = "mcp.user@example.com", lifetime: int = 300,
              aud=None, iss: str = None, key=None, roles=("viewer",)) -> str:
        """An access token as the MCP client holds it (signed by the OP unless ``key`` says otherwise)."""
        now = int(time.time())
        cid = settings.OIDC_RP_CLIENT_ID
        claims = {
            "iss": iss or self.op.issuer, "sub": sub, "aud": aud or [cid, "account"], "azp": cid,
            "iat": now, "exp": now + lifetime, "typ": "Bearer", "sid": f"sid-{sub}", "email": email,
            "preferred_username": email, "given_name": "Mcp", "family_name": "User",
            "resource_access": {cid: {"roles": list(roles)}},
        }
        return make_signed_token(claims, key=key) if key is not None else self.op.sign(claims)

    def get(self, path: str, host: str = APP_HOST, **extra):
        return self.client.get(path, HTTP_HOST=host, secure=True, **extra)

    def redeem(self, ticket: str, path: str = "/fund?embed=true"):
        return self.get(f"{path}&{TICKET}={ticket}")


class TestCluster01bf_TicketIssue(_EmbedCase):
    """1.541, 1.550: a ticket for a valid MCP token, none for anything else."""

    # -- 1.541 ---------------------------------------------------------
    def test_1_541_a_valid_mcp_token_buys_a_single_use_ticket(self) -> None:
        """
        Scenario 1.541: the MCP token stays on the server; the URL gets a 60-second ticket.
        Given: an MCP client's valid access token for this app
        When: an embed ticket is issued for the frontend
        Then: the ticket is opaque (neither the token nor part of it); the token is kept in a
              refresh-less sign-in of that user, encrypted at rest; the ticket is stored as a
              digest, for audience `embed` and this frontend, valid 60 seconds.
        """
        token = self.token()
        ticket = embed_tickets.issue_embed_ticket(token, frontend_origin=APP + "/")

        self.assertTrue(ticket)
        self.assertNotIn(ticket, token)
        self.assertNotIn(token, ticket)
        row = OIDCSession.objects.get()
        self.assertEqual(row.user.email, "mcp.user@example.com")
        self.assertIsNone(row.refresh_token_enc, "an embed's sign-in has no refresh token")
        self.assertEqual(row.access_token, token)
        self.assertNotIn(token, row.access_token_enc, "the token is encrypted at rest")
        code = DashboardHandoffCode.objects.get()
        self.assertEqual(code.code_hash, hashlib.sha256(ticket.encode()).hexdigest())
        self.assertEqual((code.aud, code.return_origin), ("embed", APP))
        self.assertAlmostEqual((code.expires_at - timezone.now()).total_seconds(), 60, delta=5)

    # -- 1.550 ---------------------------------------------------------
    def test_1_550_anything_but_a_valid_long_enough_token_gets_no_ticket(self) -> None:
        """
        Scenario 1.550: the ticket issuer validates like a bearer request.
        Given: tokens signed by another key, for another audience, from another issuer, one
               expiring within 30 seconds, and no token at all
        When: a ticket is asked for each
        Then: none is issued and nothing is stored -- the embed then simply loads signed out.
        """
        stranger = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        refused = {
            "forged signature": self.token(key=stranger),
            "another audience": self.token(aud=["some-other-client"]),
            "another issuer": self.token(iss="https://evil.example.net/realms/x"),
            "about to expire": self.token(lifetime=10),
            "no token": None,
            "empty token": "",
        }
        for label, token in refused.items():
            with self.subTest(token=label):
                self.assertIsNone(embed_tickets.issue_embed_ticket(token, frontend_origin=APP), label)
        self.assertFalse(OIDCSession.objects.exists(), "a refused token must leave nothing behind")
        self.assertFalse(DashboardHandoffCode.objects.exists())


class TestCluster01bf_Redemption(_EmbedCase):
    """1.542-1.545, 1.553-1.556: redeeming the ticket into the cookie, once, on this origin."""

    # -- 1.542 ---------------------------------------------------------
    def test_1_542_the_first_request_trades_the_ticket_for_a_cookie(self) -> None:
        """
        Scenario 1.542: the frame's first request redeems the ticket and leaves a clean URL.
        Given: a ticket for this frontend, from a token valid 5 more minutes
        When: the frame loads the embed URL carrying it
        Then: a redirect to the same URL without the ticket (other parameters kept), setting
              `__Host-lex_embed` -- HttpOnly; Secure; SameSite=None; Partitioned; Path=/ -- whose
              Max-Age is no longer than the token lives; the redirect is framable (it happens in
              the MCP host's frame).
        """
        ticket = embed_tickets.issue_embed_ticket(self.token(lifetime=300), frontend_origin=APP)
        response = self.redeem(ticket, "/fund?embed=true&hide_toolbar=true")

        self.assertEqual(response.status_code, 302)
        self.assertFalse(response.has_header("X-Frame-Options"), "the redeem runs inside a frame")
        location = urlsplit(response["Location"])
        self.assertEqual(location.path, "/fund")
        self.assertEqual(parse_qs(location.query), {"embed": ["true"], "hide_toolbar": ["true"]})
        attrs = _attributes(_cookie_line(response))
        for flag in ("httponly", "secure", "partitioned"):
            self.assertIs(attrs.get(flag), True, f"the embed cookie must be {flag}")
        self.assertEqual((attrs.get("samesite"), attrs.get("path")), ("None", "/"))
        self.assertTrue(0 < int(attrs["max-age"]) <= 300, f"Max-Age {attrs['max-age']} must not outlive the token")
        self.assertEqual(DashboardBinding.objects.get().aud, "embed")

    # -- 1.543 ---------------------------------------------------------
    def test_1_543_a_ticket_works_once(self) -> None:
        """
        Scenario 1.543: a used ticket is worthless, and replaying it ends the first embed too.
        Given: a ticket already redeemed by one frame
        When: it is presented again
        Then: no cookie is set; and the first frame's cookie stops working (a replay means the
              ticket may have been someone else's to begin with).
        """
        ticket = embed_tickets.issue_embed_ticket(self.token(), frontend_origin=APP)
        first = self.redeem(ticket)
        self.assertTrue(_cookie_line(first))
        self.assertEqual(self.get("/api/auth/session").status_code, 200)

        other = Client()
        replay = other.get(f"/fund?embed=true&{TICKET}={ticket}", HTTP_HOST=APP_HOST, secure=True)
        self.assertEqual(replay.status_code, 302)
        self.assertFalse(_cookie_line(replay), "a used ticket must not sign anything in")
        self.assertEqual(self.get("/api/auth/session").status_code, 401, "the replay revoked the first embed")

    # -- 1.544 ---------------------------------------------------------
    def test_1_544_a_ticket_lives_sixty_seconds(self) -> None:
        """
        Scenario 1.544: an old ticket is refused.
        Given: a ticket issued more than 60 seconds ago
        When: it is presented
        Then: no cookie.
        """
        ticket = embed_tickets.issue_embed_ticket(self.token(), frontend_origin=APP)
        DashboardHandoffCode.objects.update(expires_at=timezone.now() - timedelta(seconds=1))
        self.assertFalse(_cookie_line(self.redeem(ticket)))
        self.assertFalse(DashboardBinding.objects.exists())

    # -- 1.545 ---------------------------------------------------------
    def test_1_545_a_ticket_is_only_good_on_its_frontend(self) -> None:
        """
        Scenario 1.545: a ticket issued for one frontend origin is refused on another host.
        Given: a ticket for https://app.example.com
        When: it is presented to another host of the instance
        Then: no cookie (and the ticket is spent).
        """
        ticket = embed_tickets.issue_embed_ticket(self.token(), frontend_origin=APP)
        response = self.client.get(f"/fund?embed=true&{TICKET}={ticket}", HTTP_HOST="other.example.com", secure=True)
        self.assertEqual(response.status_code, 302)
        self.assertFalse(_cookie_line(response))
        self.assertIsNotNone(DashboardHandoffCode.objects.get().used_at)

    # -- 1.553 ---------------------------------------------------------
    def test_1_553_a_ticket_is_never_redeemed_by_a_write(self) -> None:
        """
        Scenario 1.553: only a GET navigation redeems (a HEAD neither: 1.556).
        Given: a fresh ticket
        When: it arrives on a POST
        Then: nothing is redeemed (no cookie; the ticket is still unused) -- and a GET then can.
        """
        ticket = embed_tickets.issue_embed_ticket(self.token(), frontend_origin=APP)
        posted = self.client.post(f"/api/auth/session?{TICKET}={ticket}", HTTP_HOST=APP_HOST, secure=True)
        self.assertFalse(_cookie_line(posted))
        self.assertIsNone(DashboardHandoffCode.objects.get().used_at, "a write must not spend the ticket")
        self.assertTrue(_cookie_line(self.redeem(ticket)))

    # -- 1.554 ---------------------------------------------------------
    def test_1_554_the_ticket_redirect_never_leaves_the_app_origin(self) -> None:
        """
        Scenario 1.554: the clean-URL redirect after a ticket is always a path on the app's own origin.
        Given: embed URLs whose path decodes to `//evil.example/` (`/%2Fevil.example/`,
               `/%2F%2Fevil.example/`) or to a backslash form (`/%5Cevil.example/`,
               `/%2F%5Cevil.example/`), carrying a ticket -- valid or not -- and OAuth-looking
               `code` / `state` parameters
        When: the frame requests them
        Then: the redirect keeps the frame on this origin, with the other parameters -- never the
              scheme-relative `//evil.example/`, which would send the frame, and the parameters it
              carries, to another host.
        """
        valid = embed_tickets.issue_embed_ticket(self.token(), frontend_origin=APP)
        cases = {"/%2Fevil.example/": valid, "/%2F%2Fevil.example/": "x", "/%5Cevil.example/": "x",
                 "/%2F%5Cevil.example/": "x"}
        for path, ticket in cases.items():
            with self.subTest(path=path):
                response = self.client.get(path, {TICKET: ticket, "code": "abc", "state": "s"},
                                           HTTP_HOST=APP_HOST, secure=True)
                self.assertEqual(response.status_code, 302)
                self.assertTrue(_stays_on(APP, response["Location"]), response["Location"])
                self.assertEqual(parse_qs(urlsplit(response["Location"]).query), {"code": ["abc"], "state": ["s"]})

    # -- 1.555 ---------------------------------------------------------
    def test_1_555_the_frame_holding_the_embed_may_render_its_ticket_again(self) -> None:
        """
        Scenario 1.555: a ticket presented again by the browser that holds its embed is not a replay attack.
        Given: a ticket redeemed by a frame -- an MCP host renders the same widget URL again (a reload,
               the conversation scrolled back) in that frame
        When: the same frame, with the embed cookie that ticket produced, presents it again; then a
              frame holding another embed's cookie presents it
        Then: the holder's request only loses the ticket from its URL -- no new cookie, the embed
              keeps working, nothing is revoked; anyone else presenting the used ticket is a replay
              as in 1.543: refused, and the embed it produced is revoked.
        """
        ticket = embed_tickets.issue_embed_ticket(self.token(), frontend_origin=APP)
        self.assertTrue(_cookie_line(self.redeem(ticket)))

        again = self.redeem(ticket, "/fund?embed=true&hide_toolbar=true")
        self.assertEqual(again.status_code, 302)
        location = urlsplit(again["Location"])
        self.assertEqual((location.path, parse_qs(location.query)), ("/fund", {"embed": ["true"], "hide_toolbar": ["true"]}))
        self.assertFalse(_cookie_line(again), "nothing new is set")
        self.assertEqual(self.get("/api/auth/session").status_code, 200, "the embed keeps working")
        self.assertIsNone(DashboardBinding.objects.get().revoked_at)

        other = Client()
        theirs = embed_tickets.issue_embed_ticket(self.token(sub="other", email="other@example.com"), frontend_origin=APP)
        self.assertTrue(_cookie_line(other.get(f"/fund?embed=true&{TICKET}={theirs}", HTTP_HOST=APP_HOST, secure=True)))
        replay = other.get(f"/fund?embed=true&{TICKET}={ticket}", HTTP_HOST=APP_HOST, secure=True)
        self.assertEqual(replay.status_code, 302)
        self.assertFalse(_cookie_line(replay))
        self.assertEqual(self.get("/api/auth/session").status_code, 401, "a replay by anyone else revokes the embed")

    # -- 1.556 ---------------------------------------------------------
    def test_1_556_a_head_request_never_spends_a_ticket(self) -> None:
        """
        Scenario 1.556: link prefetchers and unfurlers (HEAD) never burn a ticket.
        Given: a fresh ticket
        When: a HEAD request carries it -- as a prefetcher or a link preview sends one before the
              frame loads; then the frame's GET
        Then: the HEAD redeems nothing (no cookie, the ticket unused, no binding), and the GET then
              redeems it.
        """
        ticket = embed_tickets.issue_embed_ticket(self.token(), frontend_origin=APP)
        head = self.client.head(f"/fund?embed=true&{TICKET}={ticket}", HTTP_HOST=APP_HOST, secure=True)
        self.assertFalse(_cookie_line(head))
        self.assertIsNone(DashboardHandoffCode.objects.get().used_at, "a HEAD must not spend the ticket")
        self.assertFalse(DashboardBinding.objects.exists())
        self.assertTrue(_cookie_line(self.redeem(ticket)))


class TestCluster01bf_TheCookie(_EmbedCase):
    """1.546-1.549, 1.552: what the cookie may do, and when it stops."""

    def _embedded(self, **token_kwargs) -> str:
        token = self.token(**token_kwargs)
        ticket = embed_tickets.issue_embed_ticket(token, frontend_origin=APP)
        self.assertTrue(_cookie_line(self.redeem(ticket)))
        return token

    # -- 1.546 ---------------------------------------------------------
    def test_1_546_the_cookie_signs_reads_in_as_the_mcp_user(self) -> None:
        """
        Scenario 1.546: the embed reads as the MCP client's user, with the token's roles.
        Given: an embed whose ticket was redeemed
        When: the frame asks who it is, and reads a login-protected endpoint
        Then: api/auth/session is that user with the roles of the token; the protected read is
              served (without the cookie it is the contract's 401).
        """
        self._embedded(roles=("viewer", "fund-reader"))
        session = self.get("/api/auth/session")
        self.assertEqual(session.status_code, 200)
        self.assertEqual(session.json()["user"]["email"], "mcp.user@example.com")
        self.assertEqual(session.json()["user"]["client_roles"], ["viewer", "fund-reader"])
        self.assertEqual(self.get("/api/model-structure").status_code, 200)
        self.assertEqual(Client().get("/api/model-structure", HTTP_HOST=APP_HOST, secure=True).status_code, 401)

    # -- 1.547 ---------------------------------------------------------
    def test_1_547_the_cookie_never_writes(self) -> None:
        """
        Scenario 1.547: every unsafe method through the embed cookie is refused before the view.
        Given: an embed whose ticket was redeemed
        When: it POSTs, PUTs, PATCHes or DELETEs
        Then: 403 `{"code": "permission_denied"}` (no-store), the embed's own refusal -- the view
              never answers (a POST to api/auth/streamlit-token/ would otherwise hand out a token).
        """
        self._embedded()
        for method in ("post", "put", "patch", "delete"):
            with self.subTest(method=method):
                response = getattr(self.client, method)("/api/auth/streamlit-token/", HTTP_HOST=APP_HOST, secure=True)
                self.assertEqual(response.status_code, 403)
                self.assertEqual(response.json(), {"detail": "This embedded view is read-only.",
                                                   "code": "permission_denied"})
                self.assertEqual(response["Cache-Control"], "no-store")
        preflight = self.client.options("/api/auth/session", HTTP_HOST=APP_HOST, secure=True)
        self.assertNotEqual(preflight.status_code, 403, "OPTIONS is safe and passes the embed's rule to the view")

    # -- 1.548 ---------------------------------------------------------
    def test_1_548_the_cookie_dies_with_the_token(self) -> None:
        """
        Scenario 1.548: once the MCP token has expired the cookie signs nothing in.
        Given: an embed whose token has since expired
        When: the frame asks who it is
        Then: the contract's 401, and the response removes the cookie.
        """
        self._embedded()
        past = timezone.now() - timedelta(seconds=1)
        OIDCSession.objects.update(access_expires_at=past)
        DashboardBinding.objects.update(expires_at=past)
        response = self.get("/api/auth/session")
        self.assertEqual(response.status_code, 401)
        self.assertEqual(_attributes(_cookie_line(response)).get("max-age"), "0", "the dead cookie is removed")
        self.assertIsNotNone(DashboardBinding.objects.get().revoked_at)

    # -- 1.549 ---------------------------------------------------------
    def test_1_549_a_real_sign_in_wins_over_the_embed_cookie(self) -> None:
        """
        Scenario 1.549: a browser that is signed in is never demoted to the read-only embed.
        Given: a client holding an embed cookie of the MCP user and its own sign-in as someone else
        When: it asks who it is, and writes
        Then: it is its own user, and its write is not refused by the embed's rule.
        """
        self._embedded()
        owner = get_user_model().objects.create_user(username="owner", email="owner@example.com")
        sign_in(self, self.client, owner)
        session = self.get("/api/auth/session")
        self.assertEqual(session.json()["user"]["email"], "owner@example.com")
        write = self.client.post("/api/auth/streamlit-token/", HTTP_HOST=APP_HOST, secure=True)
        self.assertNotEqual(write.status_code, 403, "a real sign-in is not read-only")

    # -- 1.552 ---------------------------------------------------------
    def test_1_552_a_back_channel_logout_ends_the_embed(self) -> None:
        """
        Scenario 1.552: logging the MCP user's Keycloak session out ends the embed.
        Given: an embed, and then a back-channel logout of the token's Keycloak session (sid)
        When: the frame asks who it is
        Then: 401 and the cookie is removed.
        """
        self._embedded(sub="mcp-user")
        matched = oidc_sessions.end_sessions(sid="sid-mcp-user", reason="backchannel_logout")
        self.assertEqual(matched, 1)
        response = self.get("/api/auth/session")
        self.assertEqual(response.status_code, 401)
        self.assertEqual(_attributes(_cookie_line(response)).get("max-age"), "0")


# ---------------------------------------------------------------------------
# the MCP tool
# ---------------------------------------------------------------------------
class _Content:
    def __init__(self, type: str, text: str) -> None:  # noqa: A002 (the MCP field name)
        self.type, self.text = type, text


class _Placeholder:
    def __init__(self, *args, **kwargs) -> None:
        pass

    @classmethod
    def from_function(cls, *args, **kwargs):
        return cls()


class _ToolResult:
    """fastmcp's ToolResult: ``content`` and ``structured_content`` (both reach the model) and
    ``meta`` (the result's ``_meta``: the widget's alone)."""

    def __init__(self, content=None, structured_content=None, meta=None) -> None:
        self.content, self.structured_content, self.meta = content, structured_content, meta


def _missing(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is None
    except (ImportError, ValueError):
        return True


def _mcp_runtime(principal) -> dict:
    """Stand-ins for what the tool imports and this line does not ship.

    The MCP server runtime (lex.mcp_server.config/registry/context) lives on an unmerged branch,
    and neither fastmcp nor the MCP SDK is installed here; each stand-in is used only when the
    real module is missing, and carries just the names the tool imports.
    """
    def module(name, **attrs):
        mod = types.ModuleType(name)
        mod.__dict__.update(attrs)
        return mod

    def current_principal():
        if principal is None:
            raise RuntimeError("No MCP principal bound")
        return principal

    stubs = {}
    if _missing("fastmcp"):
        stubs.update({"fastmcp": module("fastmcp", FastMCP=_Placeholder),
                      "fastmcp.resources": module("fastmcp.resources", FunctionResource=_Placeholder),
                      "fastmcp.tools": module("fastmcp.tools", Tool=_Placeholder, ToolResult=_ToolResult)})
    if _missing("mcp"):
        stubs.update({"mcp": module("mcp"),
                      "mcp.types": module("mcp.types", TextContent=_Content, ToolAnnotations=_Placeholder)})
    for name, attrs in (
        ("lex.mcp_server.config", {"mcp_setting": lambda key: None}),
        ("lex.mcp_server.registry", {"get_container": lambda resource: None,
                                     "container_is_writable": lambda resource: False}),
        ("lex.mcp_server.context", {"current_principal": current_principal}),
    ):
        if _missing(name):
            stubs[name] = module(name, **attrs)
    return stubs


class TestCluster01bf_McpTool(_EmbedCase):
    """1.551: lex_embed_view puts a ticket in the widget's URL, never a token -- and never in front of the model."""

    # -- 1.551 ---------------------------------------------------------
    def test_1_551_the_tool_hands_out_a_ticket_never_the_token(self) -> None:
        """
        Scenario 1.551: the embed URL carries `lex_embed_ticket`, for the widget's eyes only; no token anywhere.
        Given: an MCP principal holding a valid access token, and a caller that tries to pass its
               own `auth_token`
        When: the lex_embed_view tool builds the embed
        Then: the widget's URL -- only in the result's `_meta`, which MCP hosts hand to the widget
              and never to the model -- has a ticket and no `auth_token`; everything the model sees
              (the narration, the content, the structured content) carries the clean view URL and
              neither the token nor the ticket; other parameters survive; and the ticket signs the
              frame in as the principal's user.
        """
        token = self.token(sub="claude", email="claude.user@example.com")
        principal = types.SimpleNamespace(access_token=token, user=None, auth_kind="oidc_bearer")
        if not _missing("lex.mcp_server.context"):  # pragma: no cover - once the MCP runtime is merged
            self.skipTest("the real MCP runtime is present: bind its principal instead of the stand-in")
        with patch.dict(sys.modules, _mcp_runtime(principal)), patch.dict(os.environ, {"REACT_APP_URL": APP}):
            sys.modules.pop("lex.mcp_server.tools.embed", None)
            tool = importlib.import_module("lex.mcp_server.tools.embed")
            result = asyncio.run(tool._embed_view_inner(path="fund", extra_params={"auth_token": "smuggled",
                                                                                    "keep": "1"}))
            sys.modules.pop("lex.mcp_server.tools.embed", None)

        embed_url = result.meta["embed_url"]
        url = urlsplit(embed_url)
        query = parse_qs(url.query)
        self.assertEqual(f"{url.scheme}://{url.netloc}", APP)
        self.assertNotIn("auth_token", query, "no token may travel in the URL")
        self.assertEqual(query["keep"], ["1"])
        ticket = query[TICKET][0]
        self.assertNotIn(token, embed_url)

        model_visible = [block.text for block in result.content] + [json.dumps(result.structured_content)]
        self.assertEqual(json.loads(result.content[1].text), result.structured_content)
        for text in model_visible:
            self.assertNotIn(token, text)
            self.assertNotIn(ticket, text, "the model never sees the ticket")
            self.assertNotIn(TICKET, text)
        view = urlsplit(result.structured_content["view_url"])
        self.assertEqual((f"{view.scheme}://{view.netloc}", view.path), (APP, url.path))
        self.assertEqual(parse_qs(view.query)["keep"], ["1"])

        self.assertTrue(_cookie_line(self.redeem(ticket)))
        self.assertEqual(self.get("/api/auth/session").json()["user"]["email"], "claude.user@example.com")
