"""Cluster 1ar: a Reflex dashboard signs its visitor in by itself -- no click,
and the Keycloak session carries over.

Intent
------
A user signed in to lex-app is signed in to Keycloak, and a Reflex dashboard
should not ask them anything. Reflex Enterprise's ``/login`` is a palette of
buttons that waits for a click -- deliberately, even with one provider -- and
lex-app has exactly one, so lex-app's ``/login`` (``lex_login_page``) starts
the sign-in itself:

* **At the top level**, the plugin's own redirect to Keycloak. With a Keycloak
  session -- signing in to lex-app leaves one -- Keycloak answers without a
  form, so a new tab, or a restart of the Reflex server that cost the session
  its tokens, signs in again without anyone noticing.
* **In a frame** -- lex-app's sidebar frames the dashboards -- where Keycloak
  cannot be shown and a popup needs a click, a silent ``prompt=none`` request,
  which Keycloak answers without a page. Only when it cannot (no session, or a
  browser that withholds Keycloak's cookie from frames) does ``/login`` show
  the button, whose popup is the one way left -- and Keycloak's "sign in first"
  is then the expected answer, not an error to report.
* **Never a loop.** An automatic attempt that did not stick (the visitor backed
  out of Keycloak) gives the button for a while instead of another bounce;
  React's development mode mounting the page twice starts one sign-in, not two;
  a sign-in that cannot start leaves the button beside the plugin's error; a
  sign-out makes the next sign-in automatic again at once.

A token too large for its cookie is why a session would not survive a restart:
browsers drop a cookie over 4096 bytes without a word, and the dashboard signs
in again each time. It is reported once, with what to change in Keycloak.

Cluster 1ar -- scenarios 1.370-1.375. Type: U.
Covers: lex/lex_app/reflex/auth.py (``LexKeycloakAuthState.start_login``,
``_silent_login_redirect``, ``_handle_auth_callback_error_response``,
``_redirect_to_logout_payload``, ``_set_tokens``; ``lex_login_page``;
``_report_oversized_token_cookies``), lex/lex_app/reflex/config.py
(``LOGIN_PAGE``, ``lex_auth_plugin``).
Run: python -m lex pytest lex/test_project/tests/init/test_1ar_reflex_automatic_sign_in.py -v
"""

from __future__ import annotations

import base64
import contextlib
import hashlib
import logging
import os
import tempfile
import time
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import AsyncMock, MagicMock, patch
from urllib.parse import parse_qs, urlparse

import pytest
from asgiref.sync import async_to_sync
from reflex_enterprise.auth import GenericOIDCAuthState, OIDCAuthState
from reflex_enterprise.auth.enforcement import login_url_for
from reflex_enterprise.auth.oidc.state import IsIframedState

import lex.lex_app.reflex.auth as auth
from lex.lex_app.reflex.auth import LexKeycloakAuthState, lex_login_page

pytestmark = pytest.mark.init

AUTHORIZE = "https://kc.example.com/realms/lex/protocol/openid-connect/auth"
ACCESS_COOKIE = "_oidc_lex_keycloak_access_token_data_partitioned"


def _root_state(url: str):
    """A root Reflex state whose router is on ``url``, the way an event sees it."""
    from reflex.istate.data import ReflexURL, RouterData
    from reflex.state import State

    root = State()
    root.router = RouterData(url=ReflexURL(url))
    return root


def _substate(root, state_cls):
    return root.get_substate(state_cls.get_full_name().split("."))


def _redirect(spec) -> tuple[str, bool]:
    """Where an ``rx.redirect`` sends the browser, and whether it replaces the entry."""
    assert spec is not None, "expected a redirect, got nothing"
    args = {str(name): value._var_value for name, value in spec.args}
    assert "path" in args, f"expected a redirect, got {spec}"
    return args["path"], bool(args.get("replace"))


@contextlib.contextmanager
def _keycloak():
    """The provider's client, and Keycloak's authorization endpoint, with no network."""
    endpoints = {"authorization_endpoint": AUTHORIZE}
    with patch.dict(os.environ, {"LEX_KEYCLOAK_CLIENT_ID": "lex-rp"}), \
            patch.object(LexKeycloakAuthState, "_issuer_endpoint",
                         AsyncMock(side_effect=lambda name: endpoints[name])):
        yield


@contextlib.contextmanager
def _no_running_app():
    """What building and rendering ``/login`` asks of the running app -- 1.366 compiles the real one.

    A login button registers its provider's routes on the app, an Enterprise
    component checks, as it renders, that the app is one, and Reflex copies the
    components' shared assets into the working directory -- a project's, which
    here is a temporary one.
    """
    from reflex_enterprise.app import AppEnterprise

    running = SimpleNamespace(app=MagicMock(spec=AppEnterprise))
    with tempfile.TemporaryDirectory() as project, contextlib.chdir(project), \
            patch.object(OIDCAuthState, "_register_auth_endpoints"), \
            patch("reflex.utils.prerequisites.get_app", return_value=running):
        yield


def _login_page(path: str = "/login?redirect_to=%2F%3Fmodel%3Dfund%26pk%3D1"):
    """``/login`` for ``path``: the root state, the provider, and the frame check's state."""
    root = _root_state(f"http://localhost:8502{path}")
    return root, _substate(root, LexKeycloakAuthState), _substate(root, IsIframedState)


def _start(provider, *, framed: bool = False, repeat: bool = False):
    """What ``/login`` sends when it opens (``_LOGIN_PAGE_JS``)."""
    return async_to_sync(provider.start_login)({"framed": framed, "repeat": repeat})


class TestCluster01ar_TheLoginPage(TestCase):
    """``/login`` is lex-app's page: it signs in by itself."""

    # -- 1.370 ---------------------------------------------------------
    def test_1_370_login_is_lex_apps_page_which_signs_in_by_itself(self) -> None:
        """
        Scenario 1.370: the plugin's ``/login`` is ``lex_login_page``, which starts the sign-in.
        Given: the AuthPlugin ``lex_config()`` configures.
        When:  it builds ``/login``.
        Then:  the page is lex-app's, named by import path (importing it would
               create states while rxconfig.py loads); it shows that it is
               signing in, with the button only under ``login_needs_click``;
               opening it runs the browser check and hands the answer to
               ``start_login``. A project's own ``login_page`` wins, and with any
               provider but lex-app's alone the plugin's palette stays.
        """
        from reflex_enterprise.auth.pages import default_login_page

        from lex.lex_app.reflex.config import LOGIN_PAGE, lex_auth_plugin

        plugin = lex_auth_plugin()
        self.assertEqual(plugin.login_page, "lex.lex_app.reflex.auth.lex_login_page")
        self.assertEqual(plugin.login_page, LOGIN_PAGE)
        with _no_running_app():
            page = plugin._page_component(plugin.login_page, default_login_page)()
            rendered = str(page)
        self.assertIn("Signing you in", rendered, "the page says it is signing in")
        self.assertIn("login_needs_click", rendered, "the button is behind login_needs_click")
        self.assertIn("Login with Keycloak", rendered, "the button is there for when a click is needed")
        self.assertLess(
            rendered.index("login_needs_click"), rendered.index("Login with Keycloak"),
            "the button renders only under the condition",
        )

        (on_mount,) = page.event_triggers["on_mount"].events
        mount = {str(name): str(value) for name, value in on_mount.args}
        self.assertEqual(mount.get("function"), auth._LOGIN_PAGE_JS,
                         f"opening the page runs the browser check first, got {on_mount}")
        self.assertIn("history.state", auth._LOGIN_PAGE_JS,
                      "the check tells one visit from React mounting the page twice")
        self.assertIn("window.top", auth._LOGIN_PAGE_JS, "and a frame from the top level")
        self.assertIn(f"{LexKeycloakAuthState.get_full_name()}.start_login", mount.get("callback", ""),
                      "the answer goes to start_login")

        custom = lex_auth_plugin(login_page="myproject.pages.login")
        self.assertEqual(custom.login_page, "myproject.pages.login", "a project's own login page wins")

        for providers in ([GenericOIDCAuthState], [LexKeycloakAuthState, GenericOIDCAuthState]):
            with self.subTest(providers=[p.__name__ for p in providers]), _no_running_app():
                palette = str(lex_login_page(providers=providers))
                self.assertEqual(palette, str(default_login_page(providers=providers)),
                                 "with no single lex provider there is no one sign-in to start")
                self.assertNotIn("Signing you in", palette)


class TestCluster01ar_TopLevel(TestCase):
    """At the top level: the plugin's own redirect, at once, but never in a loop."""

    # -- 1.371 ---------------------------------------------------------
    def test_1_371_top_level_goes_to_keycloak_without_a_click(self) -> None:
        """
        Scenario 1.371: signed out at the top level, ``/login`` goes straight to Keycloak.
        Given: ``/login?redirect_to=<a record's dashboard>``, no tokens.
        When:  the page opens (``start_login``, not framed).
        Then:  the plugin's own redirect to Keycloak's authorization endpoint --
               interactive, since a top-level page can show Keycloak's form --
               with the plugin's PKCE and the page asked for kept for after;
               the frame check recorded where the plugin's flows read it; no
               button. Already signed in, straight on to the page asked for,
               replacing ``/login``. The repeat call React's development mode
               makes does nothing.
        """
        root, provider, frame = _login_page()
        with _keycloak():
            target, replace = _redirect(_start(provider))

        self.assertTrue(target.startswith(f"{AUTHORIZE}?"), f"expected Keycloak, got {target}")
        query = parse_qs(urlparse(target).query)
        self.assertNotIn("prompt", query, "a top-level page lets Keycloak show its form")
        self.assertEqual(query["client_id"], ["lex-rp"])
        self.assertEqual(query["redirect_uri"], [provider._redirect_uri()])
        self.assertEqual(query["state"], [provider.app_state])
        self.assertFalse(replace)
        self.assertEqual(provider.redirect_to_url, "/?model=fund&pk=1", "back to the page asked for")
        self.assertFalse(provider.login_needs_click, "no button while signing in")
        self.assertFalse(frame.is_iframed)
        self.assertTrue(frame.checked_this_session, "the plugin must not re-check the frame")

        started = provider._auto_login_at
        state_nonce = provider.app_state
        with _keycloak():
            self.assertIsNone(_start(provider, repeat=True), "one visit starts one sign-in")
        self.assertEqual(provider._auto_login_at, started)
        self.assertEqual(provider.app_state, state_nonce, "the running sign-in's nonce is untouched")
        self.assertFalse(provider.login_needs_click, "the repeat does not bring up the button")

        root, provider, frame = _login_page()
        with _keycloak(), patch.object(LexKeycloakAuthState, "_signed_in", AsyncMock(return_value=True)):
            target, replace = _redirect(_start(provider))
        self.assertEqual(target, "/?model=fund&pk=1", "a signed-in visitor goes on to the page")
        self.assertTrue(replace, "and /login leaves the history")
        self.assertEqual(provider._auto_login_at, 0.0, "no sign-in was started")

    # -- 1.372 ---------------------------------------------------------
    def test_1_372_an_attempt_that_did_not_stick_gives_the_button(self) -> None:
        """
        Scenario 1.372: back on ``/login`` soon after an automatic attempt, the button.
        Given: an automatic sign-in started moments ago (the visitor backed out
               of Keycloak), one started longer ago than the retry window, a
               sign-out since, and a redirect that cannot be built.
        When:  ``/login`` opens again.
        Then:  within the window, the button and no redirect -- another would
               bounce the visitor straight back; after it, automatic again; after
               a sign-out, automatic at once; a sign-in that cannot start leaves
               the button, and the plugin reports why.
        """
        root, provider, _ = _login_page()
        provider._auto_login_at = time.time() - 5
        with _keycloak():
            self.assertIsNone(_start(provider), "no second bounce")
        self.assertTrue(provider.login_needs_click)

        provider._auto_login_at = time.time() - auth.AUTO_LOGIN_RETRY_SECONDS - 1
        with _keycloak():
            target, _ = _redirect(_start(provider))
        self.assertTrue(target.startswith(AUTHORIZE), "after the window, automatic again")
        self.assertFalse(provider.login_needs_click)

        with _keycloak():
            async_to_sync(provider._redirect_to_logout_payload)()
            target, _ = _redirect(_start(provider))
        self.assertTrue(target.startswith(AUTHORIZE),
                        "the sign-in after a sign-out is a new one, not a retry")

        root, provider, _ = _login_page()
        broken = AsyncMock(side_effect=RuntimeError("Keycloak is unreachable"))
        with _keycloak(), patch.object(LexKeycloakAuthState, "_issuer_endpoint", broken), \
                patch("reflex_enterprise.auth.oidc.state.chain_event_out_of_band", AsyncMock()) as toast, \
                self.assertLogs("reflex_enterprise.auth.oidc", logging.ERROR) as logged:
            self.assertIsNone(_start(provider))
        self.assertTrue(provider.login_needs_click, "the button, rather than a spinner for ever")
        toast.assert_awaited()
        self.assertIn("Keycloak is unreachable", "\n".join(logged.output), "the plugin says why")


class TestCluster01ar_Framed(TestCase):
    """In a frame: a silent request, and the button only when Keycloak cannot answer."""

    # -- 1.373 ---------------------------------------------------------
    def test_1_373_a_frame_asks_keycloak_silently(self) -> None:
        """
        Scenario 1.373: framed, ``/login`` asks Keycloak with ``prompt=none``.
        Given: ``/login`` inside a frame, signed out.
        When:  the page opens (``start_login``, framed).
        Then:  a redirect to Keycloak's authorization endpoint with
               ``prompt=none`` -- answered without a page, which a frame could
               not show -- carrying the plugin's own request: its client, its
               callback, its ``state`` nonce and a PKCE challenge of its
               verifier; the page asked for kept for after; the frame recorded,
               so the button's flow is the popup. Once Keycloak has said it
               cannot sign this visitor in silently, the button at once.
        """
        root, provider, frame = _login_page("/login?redirect_to=%2F%3Flex_embed%3D1")
        with _keycloak():
            target, _ = _redirect(_start(provider, framed=True))

        self.assertTrue(target.startswith(f"{AUTHORIZE}?"), f"expected Keycloak, got {target}")
        query = parse_qs(urlparse(target).query)
        self.assertEqual(query["prompt"], ["none"], "Keycloak must answer without a page")
        self.assertEqual(query["response_type"], ["code"])
        self.assertEqual(query["client_id"], ["lex-rp"])
        self.assertEqual(query["redirect_uri"], [provider._redirect_uri()],
                         "the plugin's own callback handles the answer")
        self.assertEqual(query["state"], [provider.app_state])
        challenge = base64.urlsafe_b64encode(
            hashlib.sha256(provider.code_verifier.encode("ascii")).digest()
        ).decode("ascii").strip("=")
        self.assertEqual(query["code_challenge"], [challenge])
        self.assertEqual(query["code_challenge_method"], ["S256"])
        self.assertTrue(provider._silent_login_pending)
        self.assertEqual(provider.redirect_to_url, "/?lex_embed=1")
        self.assertTrue(frame.is_iframed, "the button's flow is the popup")
        self.assertFalse(provider.login_needs_click)

        provider._silent_login_failed = True
        provider._auto_login_at = 0.0
        with _keycloak():
            self.assertIsNone(_start(provider, framed=True), "no second silent request")
        self.assertTrue(provider.login_needs_click, "the popup is the one way left")

        root, provider, _ = _login_page()
        broken = AsyncMock(side_effect=RuntimeError("Keycloak is unreachable"))
        with _keycloak(), patch.object(LexKeycloakAuthState, "_issuer_endpoint", broken), \
                patch("reflex_enterprise.auth.oidc.state.chain_event_out_of_band", AsyncMock()), \
                self.assertLogs("reflex_enterprise.auth.oidc", logging.ERROR):
            self.assertIsNone(_start(provider, framed=True))
        self.assertTrue(provider.login_needs_click)
        self.assertFalse(provider._silent_login_pending, "nothing is on its way to Keycloak")

    # -- 1.374 ---------------------------------------------------------
    def test_1_374_sign_in_first_brings_up_the_button_not_an_error(self) -> None:
        """
        Scenario 1.374: Keycloak's "sign in first" to a silent request is the button.
        Given: a silent request on its way, the page asked for recorded.
        When:  Keycloak answers ``/callback`` with each "cannot without a page"
               error, through the plugin's own callback handler.
        Then:  back to ``/login`` for the same page, replacing the callback, with
               the button and no error reported. Any other error -- and any error
               with no silent request behind it -- is the plugin's to report.
        """
        for error in sorted(auth.SILENT_LOGIN_ERRORS):
            with self.subTest(error=error):
                root, provider, _ = _login_page(f"/callback?error={error}&state=anything")
                provider._silent_login_pending = True
                provider.redirect_to_url = "/?model=fund&pk=1"
                target, replace = _redirect(async_to_sync(provider.auth_callback)())
                self.assertEqual(target, login_url_for("/?model=fund&pk=1"))
                self.assertTrue(replace, "the callback leaves the history")
                self.assertTrue(provider._silent_login_failed)
                self.assertTrue(provider.login_needs_click)
                self.assertFalse(provider._silent_login_pending)
                self.assertEqual(provider._last_error_message, "", "not an error: the expected answer")

        root, provider, _ = _login_page("/callback?error=access_denied&state=anything")
        provider._silent_login_pending = True
        with self.assertLogs("reflex_enterprise.auth.oidc", logging.WARNING):
            reported = async_to_sync(provider.auth_callback)()
        self.assertNotIn("path", {str(name) for name, _ in reported.args}, "reported, not redirected")
        self.assertIn("access_denied", provider._last_error_message)
        self.assertFalse(provider._silent_login_failed)
        self.assertFalse(provider.login_needs_click)

        root, provider, _ = _login_page("/callback?error=login_required&state=anything")
        with self.assertLogs("reflex_enterprise.auth.oidc", logging.WARNING):
            async_to_sync(provider.auth_callback)()
        self.assertIn("login_required", provider._last_error_message,
                      "without a silent request behind it, the plugin reports it")
        self.assertFalse(provider.login_needs_click)


class TestCluster01ar_Tokens(TestCase):
    """Storing tokens settles the sign-in, and a token too large for its cookie is named."""

    # -- 1.375 ---------------------------------------------------------
    def test_1_375_tokens_end_the_click_and_an_oversized_cookie_is_reported_once(self) -> None:
        """
        Scenario 1.375: a stored sign-in ends the need for a click; an oversized token cookie is reported.
        Given: a session waiting for a click after a failed silent request.
        When:  a sign-in stores its tokens -- the popup's, the callback's, a refresh.
        Then:  the button goes, and the next frame may try silently again. A
               token whose cookie would exceed 4096 bytes -- which a browser
               drops, so the session dies with the server and each new tab signs
               in again -- is reported once per cookie, naming it and Keycloak's
               "Full scope allowed"; tokens that fit are not.
        """
        root, provider, _ = _login_page()
        provider._silent_login_pending = True
        provider._silent_login_failed = True
        provider.login_needs_click = True
        stored = AsyncMock()
        with patch.object(OIDCAuthState, "_set_tokens", stored), patch.object(auth, "_OVERSIZED_REPORTED", set()):
            with self.assertNoLogs("lex.lex_app.reflex.auth", logging.WARNING):
                async_to_sync(provider._set_tokens)("a" * 2000, id_token="i" * 1500, refresh_token="r" * 500)
            stored.assert_awaited_once()
            self.assertFalse(provider.login_needs_click)
            self.assertFalse(provider._silent_login_failed)
            self.assertFalse(provider._silent_login_pending)

            with self.assertLogs("lex.lex_app.reflex.auth", logging.WARNING) as logged:
                async_to_sync(provider._set_tokens)("a" * 5000, id_token="i" * 1500, refresh_token="r" * 500)
            (warning,) = logged.output
            self.assertIn(ACCESS_COOKIE, warning, "the cookie the browser drops is named")
            self.assertIn("4096", warning)
            self.assertIn("Full scope allowed", warning, "and what to change in Keycloak")

            with self.assertNoLogs("lex.lex_app.reflex.auth", logging.WARNING):
                async_to_sync(provider._set_tokens)("a" * 5000, id_token="i" * 1500, refresh_token="r" * 500)
