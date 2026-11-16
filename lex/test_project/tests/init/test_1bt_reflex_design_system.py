"""RFDS in lex-app's Reflex dashboards: configuration, permissions, chrome, the host bridge.

Intent: a project's Reflex dashboards get RFDS -- its components, tokens and
templates -- with nothing configured by hand, while lex-app keeps the runtime.
``lex_config()`` lists RFDS's plugins in the order ``rfds.install()`` demands,
with lex-app's brand, locale and currency; ``create_app()`` installs RFDS with
lex-app's sign-in as the only identity, Keycloak's grants as RFDS's
permissions, and security headers that let lex-app -- and only lex-app --
frame a dashboard. A page added with ``@lex_page`` is signed-in, in lex-app's
chrome (hidden inside lex-app's own frame), with the user's grants loaded; and
a framed dashboard follows lex-app's light/dark mode and tells lex-app's shell
when it needs a click to sign in. A regression here shows as dashboards that
will not compile, render blank inside lex-app, look like another product, or
show controls a user has no grant for.
Cluster 1bt -- scenarios 1.695-1.706. Type: U.
Covers: lex/lex_app/reflex/design.py, access.py, shell.py, host.py, app.py,
config.py (lex_config), auth.py (LexKeycloakAuthState's RFDS mixin, the login
page's notice), _js/lex_host.jsx, _js/lex_rfds.css.
Run: python -m lex pytest lex/test_project/tests/init/test_1bt_reflex_design_system.py -v
"""

from __future__ import annotations

import asyncio
import contextlib
import importlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from django.test import SimpleTestCase, TestCase

from lex.lex_app.reflex import access, design, shell
from lex.lex_app.reflex._js import HERE as JS_DIR

pytestmark = pytest.mark.init

_PROJECT_APP = patch("lex.lex_app.reflex.dashboards._project_app_label", return_value="lex_app")
_NO_LEX_ENV = {
    key: ""
    for key in (
        "LEX_FORMAT_LOCALE", "LEX_UI_LANGUAGE", "LEX_BASE_CURRENCY", "LEX_TIME_ZONE",
        "LEX_RFDS_CONFORMANCE", "CI", "LEX_APP_HOSTS", "DOMAIN_HOSTED",
        "LEX_DASHBOARD_TITLE", "LEX_ENVIRONMENT",
    )
}


def _identity(sub: str = "user-1", issuer: str = "https://kc.example/realms/lex", email: str = "a@example.com"):
    from rfds.auth import Identity

    return Identity(sub=sub, issuer=issuer, email=email, name="A", roles=frozenset(), groups=frozenset(),
                    auth_time=None)


class TestCluster01bt_DesignSystemConfig(SimpleTestCase):
    """What ``lex_config()`` and ``create_app()`` take from :mod:`lex.lex_app.reflex.design`."""

    # -- 1.695 ---------------------------------------------------------
    def test_1_695_lex_config_lists_rfds_after_radix_with_lex_brand(self) -> None:
        """
        Scenario 1.695: the config carries RFDS, in the order RFDS requires, with lex-app's brand.
        Given: a project's rxconfig.py returning ``lex_config()``.
        When:  the configuration is built.
        Then:  the plugins are lex-app's AuthPlugin, Radix with RFDS's root theme,
               and RFDSPlugin -- Radix before RFDS, so RFDS's stylesheets win;
               RFDS's brand is the LEX teal; the badge is off; and RFDS's own
               configuration check finds nothing to refuse.
        """
        from reflex_enterprise.plugins.auth import AuthPlugin
        from rfds import RFDSPlugin
        from rfds.bootstrap import validate_config

        from lex.lex_app.design_system.lex_tokens import TEAL
        from lex.lex_app.reflex.config import lex_config

        with patch.dict(os.environ, _NO_LEX_ENV):
            config = lex_config()
        names = [type(plugin).__name__ for plugin in config.plugins]
        self.assertEqual(names, ["AuthPlugin", "RadixThemesPlugin", "RFDSPlugin"],
                         "RFDS must follow Radix, or rfds.install() refuses to start (FW-09)")
        self.assertIsInstance(config.plugins[0], AuthPlugin)
        rfds_plugin = config.plugins[2]
        self.assertIsInstance(rfds_plugin, RFDSPlugin)
        self.assertEqual(rfds_plugin.brand.primary, TEAL, "the brand is lex-app's teal, not RFDS's indigo")
        self.assertIs(config.show_built_with_reflex, False)
        self.assertIs(config.telemetry_enabled, False)
        with patch("reflex_base.config.get_config", return_value=config):
            self.assertEqual(validate_config(), [], "RFDS's own configuration check must pass")

    # -- 1.696 ---------------------------------------------------------
    def test_1_696_locale_currency_and_time_zone_come_from_lex_settings(self) -> None:
        """
        Scenario 1.696: RFDS formats values the way the instance is configured.
        Given: no ``LEX_*`` formatting variables, then each one set.
        When:  ``rfds_plugin()`` is built.
        Then:  the defaults are en-US formatting, English labels, EUR and the
               time zone Django defaults to; each variable overrides its own
               field; and an explicit keyword overrides both.
        """
        from lex.lex_app.reflex.design import DEFAULT_TIME_ZONE

        with patch.dict(os.environ, _NO_LEX_ENV):
            plugin = design.rfds_plugin()
        self.assertEqual((plugin.format_locale, plugin.ui_language, plugin.base_currency, plugin.timezone),
                         ("en-US", "en", "EUR", DEFAULT_TIME_ZONE))
        settings_source = Path(importlib.import_module("lex").__file__).parent / "lex_app" / "settings.py"
        self.assertIn(f'os.getenv("LEX_TIME_ZONE", "{DEFAULT_TIME_ZONE}")', settings_source.read_text(),
                      "the default must stay Django's TIME_ZONE default")
        env = {**_NO_LEX_ENV, "LEX_FORMAT_LOCALE": "de-DE", "LEX_UI_LANGUAGE": "de",
               "LEX_BASE_CURRENCY": "CHF", "LEX_TIME_ZONE": "Europe/Zurich"}
        with patch.dict(os.environ, env):
            plugin = design.rfds_plugin()
            overridden = design.rfds_plugin(base_currency="USD")
        self.assertEqual((plugin.format_locale, plugin.ui_language, plugin.base_currency, plugin.timezone),
                         ("de-DE", "de", "CHF", "Europe/Zurich"))
        self.assertEqual(overridden.base_currency, "USD", "a keyword wins over the environment")

    # -- 1.697 ---------------------------------------------------------
    def test_1_697_conformance_warns_while_developing_and_fails_in_ci(self) -> None:
        """
        Scenario 1.697: RFDS's compile-time checks never stop the edit loop, and always stop CI.
        Given: a developer's environment, a CI run, an explicit level, a typo.
        When:  ``conformance_level()`` is read.
        Then:  warn, error, the explicit level, and the default for the typo.
        """
        cases = [
            ({}, "warn"),
            ({"CI": "true"}, "error"),
            ({"CI": "1", "LEX_RFDS_CONFORMANCE": "warn"}, "warn"),
            ({"LEX_RFDS_CONFORMANCE": "off"}, "off"),
            ({"LEX_RFDS_CONFORMANCE": "strict"}, "warn"),
        ]
        for env, expected in cases:
            with self.subTest(env=env), patch.dict(os.environ, {**_NO_LEX_ENV, **env}):
                self.assertEqual(design.conformance_level(), expected)

    # -- 1.698 ---------------------------------------------------------
    def test_1_698_only_lex_app_may_frame_a_dashboard(self) -> None:
        """
        Scenario 1.698: the framing policy names exactly lex-app's origins.
        Given: an instance whose app answers on two hostnames, and a local run with none.
        When:  the policy is built and sent through the app's transformers.
        Then:  a response carries no ``X-Frame-Options`` (it cannot name an
               origin) and an enforcing ``frame-ancestors 'self'`` plus each
               ``https://`` app origin; a local run allows any localhost port;
               RFDS's other headers (nosniff, its report-only CSP) stay.
        """
        from rfds.platform import security_headers_with

        with patch.dict(os.environ, {**_NO_LEX_ENV, "LEX_APP_HOSTS": "app.example.com,lex.customer.example"}):
            origins = design.lex_app_origins()
            transformer = design.framing_policy_transformer()
        self.assertEqual(origins, ["https://app.example.com", "https://lex.customer.example"])
        with patch.dict(os.environ, _NO_LEX_ENV):
            self.assertEqual(design.lex_app_origins(), ["http://localhost:*", "http://127.0.0.1:*"])

        async def upstream(scope, receive, send):
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b""})

        wrapped = transformer(security_headers_with(design.lex_security_headers())(upstream))
        sent: list[dict] = []

        async def send(message):
            sent.append(message)

        asyncio.run(wrapped({"type": "http"}, None, send))
        headers = {}
        for name, value in sent[0]["headers"]:
            headers.setdefault(name.decode(), []).append(value.decode())
        self.assertNotIn("x-frame-options", headers, "X-Frame-Options DENY would blank every framed dashboard")
        self.assertEqual(headers["content-security-policy"],
                         ["frame-ancestors 'self' https://app.example.com https://lex.customer.example"])
        self.assertNotIn("frame-ancestors", headers["content-security-policy-report-only"][0])
        self.assertEqual(headers["x-content-type-options"], ["nosniff"])


class TestCluster01bt_Access(SimpleTestCase):
    """Keycloak's grants as RFDS's permission strings."""

    def tearDown(self) -> None:
        access.forget_grants()

    # -- 1.699 ---------------------------------------------------------
    def test_1_699_grants_become_permission_strings_model_wide_only(self) -> None:
        """
        Scenario 1.699: one string per model-wide grant, as RFDS's checks read them.
        Given: UMA grants on a model (two scopes), on single records, and malformed entries.
        When:  they are turned into strings, and ``permission()`` spells one for a model.
        Then:  ``"<app_label>.<Model>:<scope>"`` for the model-wide grant only --
               a record-scoped grant does not open the whole model, as in
               ``has_permission`` -- and ``permission()`` matches that spelling.
        """
        from django.contrib.auth.models import Group

        grants = [
            {"rsname": "lex_app.Fund", "scopes": ["read", "edit"]},
            {"rsname": "lex_app.Fund", "resource_set_id": "42", "scopes": ["delete"]},
            {"rsname": "", "scopes": ["read"]},
            "not a grant",
            {"rsname": "auth.Group", "scopes": ["read"]},
        ]
        self.assertEqual(access.permission_strings(grants),
                         frozenset({"lex_app.Fund:read", "lex_app.Fund:edit", "auth.Group:read"}))
        self.assertEqual(access.permission(Group, "read"), "auth.Group:read")
        self.assertEqual(access.permission("lex_app.Fund", "edit"), "lex_app.Fund:edit")
        with self.assertRaises(ValueError):
            access.permission(Group, "a:b")

    # -- 1.700 ---------------------------------------------------------
    def test_1_700_the_resolver_answers_from_fresh_grants_and_fails_closed(self) -> None:
        """
        Scenario 1.700: RFDS's resolver hands out only what Keycloak granted, recently.
        Given: grants remembered for one identity.
        When:  the resolver is asked for that identity, another one with the
               same e-mail, an anonymous one, and the first after the TTL.
        Then:  the first gets its strings; nobody else gets anything; and after
               the TTL the answer is empty until the next page load.
        """
        user = _identity()
        access.remember_grants(user, [{"rsname": "lex_app.Fund", "scopes": ["read"]}], now=1000.0)
        self.assertEqual(access.lex_permission_resolver(user, now=1001.0), frozenset({"lex_app.Fund:read"}))
        other = _identity(sub="user-2")
        self.assertEqual(access.lex_permission_resolver(other, now=1001.0), frozenset(),
                         "grants are keyed on issuer and subject, never the e-mail address")
        self.assertEqual(access.lex_permission_resolver(None), frozenset())
        later = 1000.0 + access.GRANTS_TTL_SECONDS + 1
        self.assertEqual(access.lex_permission_resolver(user, now=later), frozenset())

    # -- 1.701 ---------------------------------------------------------
    def test_1_701_lex_access_state_loads_grants_then_hands_over_to_rfds(self) -> None:
        """
        Scenario 1.701: a page load fetches the grants, then RFDS's AccessState loads from them.
        Given: a signed-in identity whose Keycloak grants are known, and an anonymous visitor.
        When:  ``LexAccessState.load`` runs.
        Then:  the signed-in user's grants are remembered for the resolver and
               the handler continues with ``AccessState.load``; for nobody
               signed in nothing is remembered.
        """
        from rfds.auth import AccessState

        user = _identity()

        async def grants(_state):
            return [{"rsname": "lex_app.Fund", "scopes": ["edit"]}]

        handler = access.LexAccessState.load.fn
        with patch("rfds.auth.current_identity", return_value=user), \
                patch("lex.lex_app.reflex.auth.current_permissions", grants):
            follow_up = asyncio.run(handler(SimpleNamespace()))
        self.assertIs(follow_up, AccessState.load)
        self.assertEqual(access.lex_permission_resolver(user), frozenset({"lex_app.Fund:edit"}))

        access.forget_grants()
        with patch("rfds.auth.current_identity", return_value=None), \
                patch("lex.lex_app.reflex.auth.current_permissions", grants):
            asyncio.run(handler(SimpleNamespace()))
        self.assertEqual(access._GRANTS, {})


class TestCluster01bt_Chrome(SimpleTestCase):
    """``@lex_page`` and the chrome it draws."""

    def setUp(self) -> None:
        # The navigation is process-wide; a compile earlier in the run (the
        # reference example's pages) filled it. Each test starts empty.
        self._nav = shell.nav_entries()
        shell.clear_nav()

    def tearDown(self) -> None:
        shell.clear_nav()
        shell._NAV.extend(self._nav)

    # -- 1.702 ---------------------------------------------------------
    def test_1_702_lex_page_registers_a_signed_in_page_that_loads_grants_first(self) -> None:
        """
        Scenario 1.702: ``@lex_page`` is ``@rx.page`` for lex-app.
        Given: a page function decorated with a route, a title, a heading, an
               icon, a required permission and a load event of its own.
        When:  the decorator runs.
        Then:  the page is registered with ``rxe.page`` (signed-in), its loads
               are the grants first and then its own; it is listed under its
               heading with the permission that hides it; the original function
               is returned unchanged; and a route without ``/`` is refused.
        """
        captured = {}

        def fake_page(**kwargs):
            def register(fn):
                captured.update(kwargs, fn=fn)
                return fn
            return register

        def own_load():
            return None

        def cash():
            return "body"

        with patch("reflex_enterprise.page", fake_page):
            returned = shell.lex_page("/cash", title="Cash overview", nav="Finance", icon="money",
                                      requires="lex_app.Fund:read", on_load=own_load)(cash)
        self.assertIs(returned, cash)
        self.assertEqual(captured["route"], "/cash")
        self.assertEqual(captured["title"], "Cash overview")
        self.assertEqual(captured["on_load"], [access.LexAccessState.load, own_load])
        self.assertEqual(shell.nav_entries(), [shell.NavEntry("/cash", "Cash overview", "Finance", "money",
                                                              "lex_app.Fund:read")])
        with self.assertRaises(ValueError):
            shell.lex_page("cash", title="x")

    # -- 1.703 ---------------------------------------------------------
    def test_1_703_navigation_lists_overview_then_each_heading(self) -> None:
        """
        Scenario 1.703: the side navigation groups the project's pages under their headings.
        Given: three pages under two headings, one registered twice.
        When:  the sections are built.
        Then:  the overview comes first, then each heading in registration order
               with its pages, a page registered twice is listed once and keeps
               its place, and a project without pages has no navigation at all.
        """
        self.assertEqual(shell.nav_sections(), ())
        with patch("reflex_enterprise.page", lambda **kw: (lambda fn: fn)):
            for route, heading in (("/a", "Finance"), ("/b", "Risk"), ("/c", "Finance"), ("/a", "Finance")):
                shell.lex_page(route, title=route.upper(), nav=heading)(lambda: None)
        sections = shell.nav_sections()
        self.assertEqual([s.label_default for s in sections], [None, "Finance", "Risk"])
        self.assertEqual([i.href for i in sections[0].items], ["/"])
        self.assertEqual([i.href for i in sections[1].items], ["/a", "/c"],
                         "a page registered again (a hot reload) keeps its place")
        self.assertEqual([i.href for i in sections[2].items], ["/b"])

    # -- 1.704 ---------------------------------------------------------
    def test_1_704_the_chrome_hides_itself_inside_lex_app_and_follows_its_theme(self) -> None:
        """
        Scenario 1.704: lex-app's chrome around any page.
        Given: a page body.
        When:  it is wrapped in ``lex_chrome``.
        Then:  it carries RFDS's shell with lex-app's header and user menu, a
               class that hides them under ``?lex_embed=1``, the theme follower,
               and the "restored" sign-in notice; the user menu gives way to
               the bare name under ``?is_logout_enabled=false``.
        """
        import reflex as rx

        with patch.dict(os.environ, {**_NO_LEX_ENV, "LEX_DASHBOARD_TITLE": "Treasury"}):
            rendered = str(shell.lex_chrome(rx.text("body")))
        for expected in ("LexThemeFollower", "LexAuthNotice", '"restored"', shell.EMBEDDED_CLASS,
                         "Treasury", "data-rfds-shell", "embedded", "logout_enabled",
                         "allowedOrigins", "http://localhost:*"):
            self.assertIn(expected, rendered, f"the chrome must carry {expected!r}")
        css = (JS_DIR / "lex_rfds.css").read_text()
        self.assertIn(f'.{shell.EMBEDDED_CLASS} [data-rfds-shell="header"]', css)
        self.assertIn("--rfds-color-chrome-bg: #283c50", css.lower(), "the chrome is lex-app's navy")


_HARNESS = r"""
const source = require("fs").readFileSync(process.argv[1], "utf8")
  .split("\n").filter((line) => !line.startsWith("import ")).join("\n")
  .replace(/export function/g, "function");
const posted = [];
const listeners = {};
const parent = { postMessage: (message, target) => posted.push({ message, target }) };
const storage = {};
global.window = {
  self: {}, top: {}, parent,
  location: { search: process.argv[2], ancestorOrigins: ["https://app.example.com"] },
  addEventListener: (type, fn) => { listeners[type] = fn; },
  removeEventListener: () => {},
  sessionStorage: { getItem: (k) => storage[k] || null, setItem: (k, v) => { storage[k] = v; },
                    removeItem: (k) => { delete storage[k]; } },
};
const modes = [];
const useContext = () => ({ setColorMode: (mode) => modes.push(mode) });
const useEffect = (fn) => fn();
const ColorModeContext = null;
const api = new Function("window", "useContext", "useEffect", "ColorModeContext",
  source + "\nreturn {originAllowed, LexThemeFollower, LexAuthNotice};")(
  global.window, useContext, useEffect, ColorModeContext);
api.LexThemeFollower({ allowedOrigins: ["https://app.example.com", "http://localhost:*"] });
const send = (origin, source, data) => listeners.message({ origin, source, data });
const theme = (mode, extra) => ({ source: "lex-app-host", type: "theme", payload: { mode }, ...extra });
send("https://app.example.com", parent, theme("dark"));
send("https://evil.example", parent, theme("light"));
send("https://app.example.com", {}, theme("light"));
send("https://app.example.com", parent, theme("sepia"));
send("https://app.example.com", parent, theme("light", { version: 2 }));
send("http://localhost:5173", parent, theme("light"));
api.LexAuthNotice({ event: "restored" });
api.LexAuthNotice({ event: "required" });
api.LexAuthNotice({ event: "restored" });
api.LexAuthNotice({ event: "restored" });
console.log(JSON.stringify({
  modes, posted,
  allowed: [api.originAllowed("http://localhost:3000", ["http://localhost:*"]),
            api.originAllowed("http://localhost.evil:3000", ["http://localhost:*"]),
            api.originAllowed("https://a.example", ["https://a.example"])],
}));
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="node is needed to run the browser bridge")
class TestCluster01bt_HostBridge(SimpleTestCase):
    """``_js/lex_host.jsx``, run under node with the browser stubbed."""

    # -- 1.705 ---------------------------------------------------------
    def test_1_705_the_bridge_obeys_only_lex_app_and_reports_sign_in_state(self) -> None:
        """
        Scenario 1.705: a framed dashboard takes lex-app's theme and reports its sign-in, safely.
        Given: the bridge in a frame opened with ``?lex_theme=light``.
        When:  theme messages arrive from lex-app's origin, a stranger's origin,
               a window that is not the parent, with an unknown mode, from a
               newer protocol version, and from a local dev port; then the
               sign-in notices are rendered.
        Then:  the URL's mode applies first, then only the parent's messages at
               an allowed origin and version 1 with a known mode; a ``restored``
               notice is posted only after a ``required`` one, once, to the
               parent's exact origin, in the lex-auth v2 shape.
        """
        result = subprocess.run(
            ["node", "-e", _HARNESS, str(JS_DIR / "lex_host.jsx"), "?lex_theme=light"],
            capture_output=True, text=True, timeout=60, check=True,
        )
        outcome = json.loads(result.stdout)
        self.assertEqual(outcome["modes"], ["light", "dark", "light"])
        self.assertEqual(outcome["allowed"], [True, False, True])
        events = [p["message"]["event"] for p in outcome["posted"]]
        self.assertEqual(events, ["required", "restored"])
        for post in outcome["posted"]:
            self.assertEqual(post["target"], "https://app.example.com")
            self.assertEqual({k: post["message"][k] for k in ("type", "v", "surface")},
                             {"type": "lex-auth", "v": 2, "surface": "reflex"})


class TestCluster01bt_TheCompiledApp(TestCase):
    """``lex.reflex_app``, with RFDS installed, as ``reflex run`` compiles it."""

    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        # One compile per process, shared with 1aq: see _reflex_compiled.py.
        from lex.test_project.tests.init._reflex_compiled import compiled_app

        compiled = compiled_app()
        cls.app = compiled.app
        cls.head = compiled.head
        cls.resolver = compiled.identity_resolver
        cls.permission_resolver = compiled.permission_resolver
        cls.checker = compiled.nav_checker

    # -- 1.706 ---------------------------------------------------------
    def test_1_706_rfds_is_installed_with_lex_identity_permissions_and_headers(self) -> None:
        """
        Scenario 1.706: the compiled app is RFDS on lex-app's runtime.
        Given: a project whose rxconfig.py is the one ``lex reflex`` writes.
        When:  ``lex.reflex_app`` is imported and compiled.
        Then:  RFDS is installed; its identity is Reflex Enterprise's signed-in
               user and its permissions are lex-app's resolver; the side
               navigation is gated on them; the head carries lex-app's styles;
               the transformers serve RFDS's routes, its headers without
               ``X-Frame-Options``, and lex-app's framing policy; and the
               Keycloak provider carries RFDS's provider behaviour.
        """
        from rfds.enterprise.auth.plugin import rxe_identity
        from rfds.enterprise.auth.providers import RfdsProviderMixin

        from lex.lex_app.reflex.auth import LexKeycloakAuthState

        self.assertTrue(getattr(self.app, "_rfds_installed", False), "rfds.install() must have run")
        self.assertEqual(self.resolver, [rxe_identity])
        self.assertEqual(self.permission_resolver, [access.lex_permission_resolver])
        self.assertEqual(len(self.checker), 1, "the navigation's permissions must be checked")
        self.assertTrue(any("--rfds-color-chrome-bg" in head for head in self.head),
                        "lex-app's styles over RFDS's must be in every page's head")
        transformers = list(self.app.api_transformer)
        self.assertEqual(type(transformers[0]).__name__, "RfdsApi", "RFDS's routes are mounted first")
        configs = [getattr(t, "__rfds_security_headers__", None) for t in transformers]
        headers = [c for c in configs if c not in (None, True)]
        self.assertEqual(len(headers), 1)
        self.assertIsNone(headers[0].frame_options)
        self.assertTrue(any(getattr(t, "lex_framing_policy", False) for t in transformers))
        self.assertTrue(issubclass(LexKeycloakAuthState, RfdsProviderMixin))
