"""Reflex dashboards sign in through lex-app's own sign-in: the dashboard hand-off, without Reflex Enterprise.

Intent: a Reflex dashboard needs no Reflex account and runs no OpenID Connect flow of its own.
It borrows the user's lex-app sign-in exactly as a Streamlit dashboard in hand-off mode does:
``/auth/start`` sends the browser to the app's ``oidc/handoff`` (audience ``reflex``),
``/auth/handoff`` redeems the code into a binding on the user's sign-in and keeps only an
opaque handle in an HttpOnly cookie. The event socket never carries that cookie into Reflex
state -- Reflex sends the socket's headers to the browser -- but the handle's digest, set by
the server. Before every event the binding's standing is read off the event loop; an anonymous
session is sent through the hand-off by a page load; RFDS sees the user through its identity
resolver, ``lex_event(auth=)`` checks run before their handlers, the access token for lex-app's
API comes from Django's sign-in, signing out revokes the binding, and an export is bound to the
user who asked for it.

A regression here is a dashboard nobody can sign in to, a sign-in cookie readable by page
scripts, a session that outlives a sign-out, a handler run for someone who may not run it, or
another user's download.

Cluster 1bx -- scenarios 1.728-1.735. Type: I (transactional: the routes redeem and resolve
bindings in worker threads, which must see committed rows); 1.731 and 1.735 partly U.
Covers: lex/lex_app/reflex/session.py, lex/lex_app/reflex/auth.py (current_access_token,
current_permissions, has_permission, lex_event, LexUser), lex/lex_app/reflex/access.py
(LexAccessState.load's marker cleanup), lex/authentication/dashboards (the reflex audience
end to end).
Run: python -m lex pytest lex/test_project/tests/init/test_1bx_reflex_sign_in_handoff.py -v
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import time
from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import parse_qs, parse_qsl, urlsplit

import pytest
from django.contrib.auth import get_user_model
from django.test import Client, TransactionTestCase, override_settings
from starlette.testclient import TestClient

from lex.authentication.dashboards import handoff
from lex.authentication.models import DashboardBinding
from lex.lex_app.reflex import session
from lex.lex_app.tests.auth import sign_in

pytestmark = pytest.mark.init

BACKEND = "http://localhost:8503"
FRONTEND = "http://localhost:8502"
APP = "http://localhost:8000"
FRAME = {"HTTP_SEC_FETCH_DEST": "iframe", "HTTP_SEC_FETCH_MODE": "navigate", "HTTP_ACCEPT": "text/html"}


class _Recorder:
    """The Reflex backend behind the sign-in layer: records what reaches it."""

    def __init__(self) -> None:
        self.scopes: list[dict] = []

    async def __call__(self, scope, receive, send) -> None:
        self.scopes.append(scope)
        if scope["type"] == "websocket":
            await receive()
            await send({"type": "websocket.accept"})
            await send({"type": "websocket.close", "code": 1000})
            return
        if scope["type"] != "http":
            return
        await send({"type": "http.response.start", "status": 200, "headers": [(b"content-type", b"text/plain")]})
        await send({"type": "http.response.body", "body": b"backend"})

    def headers(self, index: int = -1) -> dict[str, str]:
        return {k.decode(): v.decode() for k, v in self.scopes[index]["headers"]}


def _config(single_port: bool = False):
    return SimpleNamespace(frontend_port=8503 if single_port else 8502, prepend_backend_path=lambda path: path)


def _state(binding: str = "", url: str = FRONTEND + "/cashflows"):
    """A root state as an event of a session on ``url`` sees it (its socket carrying ``binding``)."""
    from reflex.istate.data import HeaderData, ReflexURL, RouterData
    from reflex.state import State

    headers = {"host": "localhost:8503", "origin": FRONTEND}
    if binding:
        headers["x-lex-binding"] = binding
    root = State()
    root.router = RouterData(url=ReflexURL(url), headers=HeaderData.from_router_data({"headers": headers}))
    return root


def _event(module: str = "lex.lex_app.reflex.access"):
    return SimpleNamespace(state_cls=type("S", (), {"__module__": module}), name="x.y", payload={})


def _run(coro):
    return asyncio.run(coro)


def _cookie_lines(response) -> list[str]:
    return [v for k, v in response.headers.multi_items() if k.lower() == "set-cookie"]


class _HandoffCase(TransactionTestCase):
    """lex-app (Django) and a Reflex backend behind lex-app's sign-in layer, in development."""

    def setUp(self) -> None:
        super().setUp()
        stack = ExitStack()
        self.addCleanup(stack.close)
        env = {k: v for k, v in os.environ.items() if k not in ("DEPLOYMENT_ENVIRONMENT", "LEX_INSTANCE_HOSTS",
                                                               "REFLEX_USE_SINGLE_PORT", "DOMAIN_HOSTED")}
        stack.enter_context(patch.dict(os.environ, env, clear=True))
        stack.enter_context(override_settings(ALLOWED_HOSTS=["localhost", "testserver"]))
        stack.enter_context(patch("reflex.config.get_config", return_value=_config()))
        self.backend = _Recorder()
        self.dash = TestClient(session.transformer(self.backend), base_url=BACKEND, follow_redirects=False)
        self.web = Client()
        session.forget()
        self.addCleanup(session.forget)

    def user(self, name: str = "ada"):
        return get_user_model().objects.create_user(username=name, email=f"{name}@example.com",
                                                    first_name=name.title())

    def app_get(self, location: str, **headers):
        parts = urlsplit(location)
        self.assertEqual(f"{parts.scheme}://{parts.netloc}", APP, location)
        return self.web.get(parts.path, dict(parse_qsl(parts.query, keep_blank_values=True)),
                            HTTP_HOST="localhost:8000", **(headers or FRAME))

    def dash_get(self, location: str):
        parts = urlsplit(location)
        return self.dash.get(parts.path + (f"?{parts.query}" if parts.query else ""))

    def signed_in_dashboard(self, name: str = "ada"):
        """A user signed in to lex-app, and a dashboard handed off to; (user, row, handle)."""
        user = self.user(name)
        row = sign_in(self, self.web, user)
        start = self.dash.get("/auth/start", params={"next": "/cashflows"})
        back = self.app_get(start.headers["location"])
        landed = self.dash_get(back["Location"])
        self.assertEqual(landed.status_code, 303, landed.text[:300])
        handle = self.dash.cookies.get("lex_rdash")
        self.assertTrue(handle, "the redeem must set the handle cookie")
        return user, row, handle


class TestCluster01bx_RoundTrip(_HandoffCase):
    """1.728: from a page without a binding to one with it, through lex-app."""

    # -- 1.728 ---------------------------------------------------------
    def test_1_728_a_dashboard_signs_in_through_lex_apps_hand_off(self) -> None:
        """
        Scenario 1.728: the hand-off round trip.
        Given: a user signed in to lex-app; a Reflex backend on :8503 serving pages from :8502.
        When:  the browser goes to /auth/start?next=/cashflows, follows lex-app's oidc/handoff in
               the frame, and comes back to /auth/handoff.
        Then:  /auth/start sets a nonce cookie and sends it to <app>/oidc/handoff?aud=reflex with
               the backend URL as `return` and the counter lex_hs=1; lex-app answers with a code;
               /auth/handoff redeems it into a `reflex` binding, sets `lex_rdash` (HttpOnly,
               SameSite=Lax, Path=/, 30 days), clears the nonce and sends the browser to the
               page on the frontend's port -- or, in single-port mode, on the backend's own;
               over https the cookies are `__Host-`, Secure, SameSite=None and Partitioned.
        """
        user = self.user()
        sign_in(self, self.web, user)
        start = self.dash.get("/auth/start", params={"next": "/cashflows"})
        self.assertEqual(start.status_code, 303)
        target = urlsplit(start.headers["location"])
        self.assertEqual(f"{target.scheme}://{target.netloc}{target.path}", APP + "/oidc/handoff")
        query = parse_qs(target.query)
        self.assertEqual(query["aud"], ["reflex"])
        self.assertEqual(query["return"], [BACKEND + "/cashflows?lex_hs=1"])
        nonce_line = next(line for line in _cookie_lines(start) if line.startswith("lex_rdash_hs="))
        self.assertIn("HttpOnly", nonce_line)
        self.assertIn(query["state"][0], nonce_line)

        back = self.app_get(start.headers["location"])
        self.assertEqual(back.status_code, 302)
        landed = self.dash_get(back["Location"])
        self.assertEqual(landed.status_code, 303, landed.text[:300])
        self.assertEqual(landed.headers["location"], FRONTEND + "/cashflows?lex_hs=1")
        handle_line = next(line for line in _cookie_lines(landed) if line.startswith("lex_rdash="))
        for attribute in ("HttpOnly", "SameSite=Lax", "Path=/", f"Max-Age={30 * 24 * 3600}"):
            self.assertIn(attribute, handle_line)
        self.assertTrue(any(line.startswith("lex_rdash_hs=;") for line in _cookie_lines(landed)),
                        "the spent nonce is cleared")
        binding = DashboardBinding.objects.get()
        self.assertEqual((binding.aud, binding.session.user_id, binding.origin), ("reflex", user.pk, BACKEND))
        self.assertEqual(binding.handle_hash, handoff.digest(self.dash.cookies.get("lex_rdash")))

        with patch("reflex.config.get_config", return_value=_config(single_port=True)), \
                patch.dict(os.environ, {"REFLEX_USE_SINGLE_PORT": "true"}):
            self.assertEqual(session.frontend_origin(BACKEND), BACKEND, "single port: the pages are the backend's")

        hosts = json.dumps({"app": {"canonical": "app.example.com"}, "reflex": {"canonical": "reflex.example.com"}})
        with patch.dict(os.environ, {"LEX_INSTANCE_HOSTS": hosts, "DEPLOYMENT_ENVIRONMENT": "test"}):
            secure = TestClient(session.transformer(self.backend), base_url="https://reflex.example.com",
                                follow_redirects=False).get("/auth/start", params={"next": "/"})
        self.assertTrue(secure.headers["location"].startswith("https://app.example.com/oidc/handoff?"))
        line = next(v for v in _cookie_lines(secure) if v.startswith("__Host-lex_rdash_hs="))
        for attribute in ("Secure", "SameSite=None", "Partitioned", "HttpOnly"):
            self.assertIn(attribute, line)


class TestCluster01bx_SocketGuard(_HandoffCase):
    """1.729: no cookie reaches Reflex state; the binding's digest does."""

    # -- 1.729 ---------------------------------------------------------
    def test_1_729_the_event_socket_carries_the_digest_never_a_cookie(self) -> None:
        """
        Scenario 1.729: what the event socket hands Reflex.
        Given: a browser holding the handle cookie and lex-app's session cookie, sending its own
               X-Lex-Binding as well.
        When:  it opens the event socket (and an HTTP request to /_event), and requests another
               backend path.
        Then:  /_event reaches Reflex with no Cookie header and X-Lex-Binding set to the handle's
               SHA-256 -- the browser's own X-Lex-Binding dropped; no cookie, no handle; other
               paths keep their cookies (an export download needs them); a POST to a sign-in
               route is not answered by it.
        """
        handle = "h" * 43
        cookies = f"lex_rdash={handle}; sessionid=django-session; other=1"
        with self.dash.websocket_connect("/_event/?EIO=4&transport=websocket",
                                         headers={"cookie": cookies, "x-lex-binding": "f" * 64}):
            pass
        seen = self.backend.headers()
        self.assertNotIn("cookie", seen)
        self.assertEqual(seen["x-lex-binding"], hashlib.sha256(handle.encode()).hexdigest())
        self.assertNotIn(handle, json.dumps(seen))

        self.dash.get("/_event/", headers={"cookie": cookies})
        self.assertNotIn("cookie", self.backend.headers())

        self.dash.get("/_event/", headers={"x-lex-binding": "f" * 64})
        self.assertNotIn("x-lex-binding", self.backend.headers(), "a browser cannot claim a binding")

        self.dash.get("/_rfds/exports/t", headers={"cookie": cookies})
        self.assertEqual(self.backend.headers()["cookie"], cookies)

        self.assertEqual(self.dash.post("/auth/start").text, "backend")


class TestCluster01bx_Middleware(_HandoffCase):
    """1.730: before every event, the binding's standing; the hand-off for a session without one."""

    # -- 1.730 ---------------------------------------------------------
    def test_1_730_anonymous_sessions_go_through_the_hand_off_signed_in_ones_pass(self) -> None:
        """
        Scenario 1.730: LexSessionMiddleware, and the identity it leaves for RFDS.
        Given: a session with a live binding; one without; one whose sign-in then ended; one
               whose database read fails; a page after two hand-offs that left no binding.
        When:  an event arrives (a dashboard's own, or Reflex's hydrate).
        Then:  signed in: it passes, and the identity is the user's (sub, e-mail, name,
               issuer), cached for a minute; anonymous: it is replaced by a page load of
               <backend>/auth/start?next=<page> -- Reflex's own events pass; the ended sign-in
               is found at the next check, its binding revoked, and the session goes through
               the hand-off again; a failed read is anonymous, never an error; after two
               hand-offs the page goes to /auth/blocked instead of looping; once signed in,
               the grants' load event takes the counter off the address bar.
        """
        from lex.lex_app.reflex.access import LexAccessState

        _, row, handle = self.signed_in_dashboard()
        digest = handoff.digest(handle)
        middleware = session.LexSessionMiddleware()

        self.assertIsNone(_run(middleware.preprocess(None, _state(digest), _event())))
        who = session.identity(_state(digest))
        self.assertEqual((who.sub, who.email, who.name), (row.sub, "ada@example.com", "Ada"))
        self.assertTrue(who.issuer)

        update = _run(middleware.preprocess(None, _state(), _event()))
        script = json.dumps(update.events[0].payload)
        self.assertIn("window.location.assign", script)
        self.assertIn("http://localhost:8503/auth/start?next=%2Fcashflows", script)
        self.assertIsNone(_run(middleware.preprocess(None, _state(), _event("reflex.state"))),
                          "Reflex's own events (hydrating the page) pass")

        from django.utils import timezone

        row.ended_at = timezone.now()
        row.end_reason = "logout"
        row.save()
        self.assertIsNone(_run(middleware.preprocess(None, _state(digest), _event())), "cached for a minute")
        with patch.object(session, "CHECK_SECONDS", 0.0):
            update = _run(middleware.preprocess(None, _state(digest), _event()))
        self.assertIn("/auth/start", json.dumps(update.events[0].payload))
        self.assertEqual(DashboardBinding.objects.get().revoke_reason, "ended:logout")

        session.forget()
        with patch("lex.lex_app.reflex.django_orm.run_orm", side_effect=RuntimeError("db down")):
            update = _run(middleware.preprocess(None, _state("a" * 64), _event()))
        self.assertIn("/auth/start", json.dumps(update.events[0].payload))

        update = _run(middleware.preprocess(None, _state(url=FRONTEND + "/cashflows?lex_hs=2"), _event()))
        self.assertIn("/auth/blocked", json.dumps(update.events[0].payload))

        self.assertIsNone(session.marker_cleanup(_state()), "nothing to clean without a sign-in")
        live = "d" * 64
        session._remember(live, session.Standing(time.monotonic(), 9, SimpleNamespace(sub="s"), {"sub": "s"}))
        cleanup = session.marker_cleanup(_state(live, url=FRONTEND + "/cashflows?page=2&lex_hs=1"))
        self.assertIn("replaceState", json.dumps(cleanup.payload if hasattr(cleanup, "payload") else str(cleanup)))
        self.assertIn('"/cashflows?page=2"', str(cleanup), "the counter goes, the page's own query stays")
        self.assertTrue(hasattr(LexAccessState, "load"))


class TestCluster01bx_Checks(_HandoffCase):
    """1.731: RFDS sees the user; lex_event's checks run before their handlers."""

    # -- 1.731 ---------------------------------------------------------
    def test_1_731_rfds_sees_the_user_and_lex_event_checks_run_first(self) -> None:
        """
        Scenario 1.731: identity, permissions and handler checks on RFDS's open path.
        Given: RFDS's identity resolver set to lex-app's session (as create_app sets it); a
               handler guarded with lex_event(auth=has_permission(Group, "edit")), and one with
               no check; a signed-in and an anonymous session.
        When:  RFDS's PolicyMiddleware (signed-in users allowed by default) decides.
        Then:  rfds.auth.current_identity is the binding's user; the guarded handler is denied
               without the grant and allowed with it; the unguarded one is allowed; an
               anonymous session is sent to sign in; lex_event without auth is plain rx.event.
        """
        import reflex as rx
        from django.contrib.auth.models import Group
        from rfds.auth import PolicyMiddleware, current_identity, set_identity_resolver

        from lex.lex_app.reflex.auth import has_permission, lex_event

        _, row, handle = self.signed_in_dashboard()
        digest = handoff.digest(handle)
        _run(session.standing(digest))
        set_identity_resolver(session.identity)
        self.addCleanup(set_identity_resolver, None)
        self.assertEqual(current_identity(_state(digest)).sub, row.sub)
        self.assertIsNone(current_identity(_state()))

        class Checked131(rx.State):
            @lex_event(auth=has_permission(Group, "edit"))
            def guarded(self):
                pass

            @lex_event
            def open(self):
                pass

        gate = PolicyMiddleware(session.proxy_auth(), default="allow")
        identity = current_identity(_state(digest))

        def decide(name, grants, who=identity):
            async def permissions(_state):
                return grants
            with patch("lex.lex_app.reflex.auth.current_permissions", permissions):
                return _run(gate.decide(Checked131.event_handlers[name], identity=who, state=_state(digest),
                                        state_cls=Checked131, name=name))

        self.assertEqual(decide("guarded", []), "deny")
        self.assertEqual(decide("guarded", [{"rsname": "auth.Group", "scopes": ["edit"]}]), "allow")
        self.assertEqual(decide("open", []), "allow", "a signed-in user passes a handler without a check")
        self.assertEqual(decide("guarded", [], who=None), "sign_in")
        self.assertIsNone(getattr(Checked131.event_handlers["open"].fn, "_rfds_policy", None))


class TestCluster01bx_Token(_HandoffCase):
    """1.732: the access token comes from Django's sign-in, never from the browser."""

    # -- 1.732 ---------------------------------------------------------
    def test_1_732_the_token_comes_from_the_sign_in_and_follows_it(self) -> None:
        """
        Scenario 1.732: current_access_token through the binding.
        Given: a session handed off to from a sign-in with a valid access token.
        When:  a handler asks for the token twice; Keycloak becomes unreachable; the sign-in ends.
        Then:  it is the sign-in's token, read once and then cached while it stays valid;
               unreachable Keycloak with no valid token left is "" (no sign-out); an ended
               sign-in is "" and the session counts as anonymous; no session, no token.
        """
        from oauth2_authcodeflow.exceptions import OIDCAuthUnavailable, OIDCSessionEnded

        from lex.lex_app.reflex.auth import current_access_token

        _, row, handle = self.signed_in_dashboard()
        digest = handoff.digest(handle)
        state = _state(digest)
        token = _run(current_access_token(state))
        self.assertTrue(token)
        self.assertEqual(token, row.access_token)
        with patch("lex.authentication.dashboards.broker.access_token") as broker:
            self.assertEqual(_run(current_access_token(state)), token)
        broker.assert_not_called()

        session.forget()
        with patch("lex.authentication.dashboards.broker.access_token", side_effect=OIDCAuthUnavailable("down")):
            self.assertEqual(_run(current_access_token(state)), "")
        self.assertIsNotNone(session.identity(state), "an outage is not a sign-out")

        session.forget()
        with patch("lex.authentication.dashboards.broker.access_token", side_effect=OIDCSessionEnded("logout")):
            self.assertEqual(_run(current_access_token(state)), "")
        self.assertIsNone(session.identity(state))
        self.assertEqual(_run(current_access_token(_state())), "")


class TestCluster01bx_Pages(_HandoffCase):
    """1.733: what the routes answer when the hand-off cannot finish."""

    # -- 1.733 ---------------------------------------------------------
    def test_1_733_without_a_sign_in_lex_app_is_told_and_nothing_loops(self) -> None:
        """
        Scenario 1.733: the hand-off's other outcomes.
        Given: a browser with no lex-app sign-in in a frame; a code meant for another browser; a
               code presented twice; a host the instance does not list; a database outage.
        When:  the routes answer.
        Then:  login_required: a 401 page posting {"type": "lex-auth", "v": 2, "event":
               "required", "surface": "reflex"} to lex-app's origins and marking the tab for
               the `restored` notice; a code without our nonce is never redeemed (back to the
               page, no binding); a replayed code is refused; an unlisted host is a 400 with no
               redirect; an outage is a 503 that retries; /auth/blocked offers the dashboard in
               its own tab.
        """
        start = self.dash.get("/auth/start", params={"next": "/cashflows"})
        back = self.app_get(start.headers["location"])
        required = self.dash_get(back["Location"])
        self.assertEqual(required.status_code, 401)
        self.assertIn('"event": "required"', required.text)
        self.assertIn('"surface": "reflex"', required.text)
        self.assertIn('"http://localhost:8000"', required.text, "posted to lex-app, not to *")
        self.assertIn("lex.reflex.authRequired", required.text)

        user = self.user("bob")
        sign_in(self, self.web, user)
        foreign = self.app_get(start.headers["location"].replace(
            parse_qs(urlsplit(start.headers["location"]).query)["state"][0], "x" * 32))
        stray = TestClient(session.transformer(self.backend), base_url=BACKEND,
                           follow_redirects=False).get(urlsplit(foreign["Location"]).path + "?"
                                                       + urlsplit(foreign["Location"]).query)
        self.assertEqual(stray.status_code, 303, "no nonce of ours: never redeemed")
        self.assertFalse(DashboardBinding.objects.exists())

        start = self.dash.get("/auth/start", params={"next": "/"})
        back = self.app_get(start.headers["location"])
        self.assertEqual(self.dash_get(back["Location"]).status_code, 303)
        self.dash.cookies.set("lex_rdash_hs", parse_qs(urlsplit(back["Location"]).query)["state"][0])
        replay = self.dash_get(back["Location"])
        self.assertEqual(replay.status_code, 401, "a code presented twice is refused")

        unlisted = TestClient(session.transformer(self.backend), base_url="http://evil.example.org",
                              follow_redirects=False).get("/auth/start", params={"next": "/"})
        self.assertEqual(unlisted.status_code, 400)
        self.assertNotIn("location", unlisted.headers)

        fresh = TestClient(session.transformer(self.backend), base_url=BACKEND, follow_redirects=False)
        start = fresh.get("/auth/start", params={"next": "/"})
        back = self.app_get(start.headers["location"])
        parts = urlsplit(back["Location"])
        with patch("lex.lex_app.reflex.django_orm.run_orm", side_effect=RuntimeError("db down")):
            outage = fresh.get(parts.path + "?" + parts.query)
        self.assertEqual((outage.status_code, outage.headers["retry-after"]), (503, "5"))

        blocked = self.dash.get("/auth/blocked", params={"next": "/cashflows?lex_hs=2"})
        self.assertIn('href="http://localhost:8502/cashflows"', blocked.text)


class TestCluster01bx_SignOut(_HandoffCase):
    """1.734: signing out ends the binding, then lex-app's sign-in."""

    # -- 1.734 ---------------------------------------------------------
    def test_1_734_signing_out_revokes_the_binding_then_signs_out_of_lex_app(self) -> None:
        """
        Scenario 1.734: /auth/logout and LexUser.sign_out.
        Given: a signed-in dashboard.
        When:  the user signs out (RFDS's user menu opens /logout, whose load event is
               LexUser.sign_out).
        Then:  sign_out loads <backend>/auth/logout; it revokes the binding (`logout`), forgets
               it, deletes the cookie and sends the browser to lex-app's total logout, which
               comes back to the dashboard; the next event of that session is anonymous.
        """
        from lex.lex_app.reflex.auth import LexUser

        _, _, handle = self.signed_in_dashboard()
        digest = handoff.digest(handle)
        _run(session.standing(digest))
        root = _state(digest)
        spec = LexUser.sign_out.fn(root.get_substate(LexUser.get_full_name().split(".")))
        self.assertIn("http://localhost:8503/auth/logout", json.dumps(spec.payload if hasattr(spec, "payload")
                                                                    else str(spec)) + str(spec))

        out = self.dash.get("/auth/logout")
        self.assertEqual(out.status_code, 303)
        self.assertEqual(out.headers["location"], APP + "/oidc/total_logout?next=http%3A%2F%2Flocalhost%3A8502%2F")
        self.assertTrue(any(line.startswith("lex_rdash=;") for line in _cookie_lines(out)))
        self.assertEqual(DashboardBinding.objects.get().revoke_reason, "logout")
        self.assertIsNone(session.cached(digest))
        self.assertIsNone(_run(session.standing(digest)).identity)


class TestCluster01bx_Exports(_HandoffCase):
    """1.735: an export is bound to the user who asked for it."""

    # -- 1.735 ---------------------------------------------------------
    def test_1_735_an_export_download_answers_only_its_owner(self) -> None:
        """
        Scenario 1.735: RFDS's export tickets read the same binding from the state and the request.
        Given: two users' dashboards, and RFDS's ticket principal resolvers set by create_app.
        When:  an export is started from Ada's session, and the download is requested with Ada's
               cookie, Bob's, and none.
        Then:  the owner recorded from the state is Ada's subject; only Ada's request names the
               same subject; Bob's names his own; no cookie names nobody.
        """
        from starlette.requests import Request

        _, ada_row, ada = self.signed_in_dashboard("ada")
        self.dash.cookies.clear()
        self.web = Client()
        _, bob_row, bob = self.signed_in_dashboard("bob")
        for handle in (ada, bob):
            _run(session.standing(handoff.digest(handle)))

        def request(cookie: str = "") -> Request:
            headers = [(b"host", b"localhost:8503")] + ([(b"cookie", f"lex_rdash={cookie}".encode())] if cookie else [])
            return Request({"type": "http", "method": "GET", "path": "/_rfds/exports/t", "headers": headers,
                            "scheme": "http", "query_string": b""})

        owner = session._state_principal(_state(handoff.digest(ada)))
        self.assertEqual(owner, ada_row.sub)
        self.assertEqual(_run(session._request_principal(request(ada))), owner)
        self.assertEqual(_run(session._request_principal(request(bob))), bob_row.sub)
        self.assertNotEqual(bob_row.sub, owner)
        self.assertEqual(_run(session._request_principal(request())), "")
