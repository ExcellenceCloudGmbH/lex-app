"""lex-app's pages and controls inside Reflex dashboards -- one embed contract for both frameworks.

Intent: a Reflex dashboard frames lex-app's own pages (``lex_view``) and controls
(``lex_widgets``, ``lex_calculation``) exactly as a Streamlit dashboard does, so
lex-app's React app sees one contract: the same URL for the same arguments, the
same events back, the same widget-host manifest. The contract lives in a
framework-free package (``lex.lex_app.embed``) both renderers use, so a fix lands
once. Reflex adds what a compiled page needs: a record named by a state var,
events delivered to event handlers, and a frame that opens lex-app on the origin
lex-app was reached on. A regression here shows as a frame that opens the wrong
page, events a dashboard never receives -- or receives from a stranger -- or a
Streamlit dashboard that broke when the code moved.
Cluster 1bv -- scenarios 1.717-1.723. Type: U.
Covers: lex/lex_app/embed/core.py (build_lex_view_url, EmbedRequest, origin_of,
Flow, ref, filters), lex/lex_app/embed/widgets.py, lex/lex_app/embed/__init__.py,
lex/lex_app/streamlit/embed.py (lex_view over the shared core),
lex/lex_app/streamlit/widgets/spec.py (re-export), lex/lex_app/reflex/embed.py,
lex/lex_app/reflex/_js/lex_frame.jsx.
Run: python -m lex pytest lex/test_project/tests/init/test_1bv_reflex_lex_view_and_widgets.py -v
"""

import json
import os
import shutil
import subprocess
from unittest.mock import patch

import pytest
import reflex as rx
from django.test import SimpleTestCase

from lex.lex_app import embed as shared
from lex.lex_app.embed import core as shared_core
from lex.lex_app.embed import widgets as shared_widgets
from lex.lex_app.reflex import embed
from lex.lex_app.reflex._js import HERE as JS_DIR

pytestmark = pytest.mark.init

_BASE = "https://app.example.com"


def _props(component) -> dict:
    """A frame component's props as Python values (literal vars unwrapped)."""
    out = {}
    for name in ("src", "expected_origin", "mode", "manifest", "filters", "url_filters_token", "height",
                 "min_height", "app_origins"):
        value = getattr(component, name, None)
        if value is None:
            continue
        literal = getattr(value, "_var_value", None)
        out[name] = literal if literal is not None else value
    return out


def _frame_of(component):
    if type(component).__name__ == "LexFrame":
        return component
    for child in component.children:
        found = _frame_of(child)
        if found is not None:
            return found
    return None


class _Scope(rx.State):
    pk: str = "5"
    team: dict = {}
    seen: list[dict] = []

    @rx.event
    def noted(self, payload: dict):
        self.seen.append(payload)


class TestCluster01bv_OneContract(SimpleTestCase):
    """The embed contract, shared by Streamlit and Reflex."""

    # -- 1.717 ---------------------------------------------------------
    def test_1_717_streamlit_and_reflex_open_the_same_url(self) -> None:
        """
        Scenario 1.717: both frameworks put the same URL in front of lex-app's React app.
        Given: the same lex_view arguments -- a path, toolbar off, a sequence
               flow, a serializer, filters and an on_select handler.
        When:  Streamlit's lex_view and Reflex's lex_view render them.
        Then:  both open exactly ``build_lex_view_url``'s URL (Reflex adding the
               filters token Streamlit's shim adds in the browser); every name a
               Streamlit page imported from ``streamlit.embed`` and
               ``streamlit.widgets.spec`` still imports, as the same object; and
               Django's model discovery never imported the contract a second
               time under its app-relative name (two ``Flow`` classes would fail
               each other's ``isinstance`` checks).
        """
        import sys

        self.assertFalse([name for name in sys.modules if name.startswith("lex_app.embed")],
                         "discovery must skip lex's embed package (_LEX_PACKAGE_EXCLUDED_DIRS)")
        from lex.lex_app.streamlit import embed as st_embed
        from lex.lex_app.streamlit.widgets import spec as st_spec

        flow = (shared.Flow().create("fund", as_="fund").create("investor")
                .update("fund", id=shared.ref("fund")).table("investor"))
        args = dict(hide_toolbar=True, flow=flow, serializer="Brief", filters={"status": ["open", "held"]})
        captured = {}

        def render(**kwargs):
            captured.update(kwargs)

        with patch.object(st_embed, "render_lex_view_component", render):
            st_embed.lex_view("investor", base_url=_BASE, on_select=True, **args)
        expected = shared.build_lex_view_url(_BASE, "investor", on_select=True, **args)
        self.assertEqual(captured["url"], expected.url)
        self.assertEqual(captured["expected_origin"], _BASE)
        self.assertEqual(captured["url_filters_token"], expected.url_filters_token)

        reflex_src = _props(_frame_of(embed.lex_view("investor", base_url=_BASE, on_select=_Scope.noted,
                                                     **args)))["src"]
        base, fragment = expected.url.split("#", 1)
        self.assertEqual(reflex_src, f"{base}&filters={expected.url_filters_token}#{fragment}")
        for name in ("Flow", "FlowError", "Ref", "ref", "STAY", "FLOW_WIRE_VERSION", "normalise_filters",
                     "encode_filters_token", "MAX_URL_FILTERS_TOKEN"):
            self.assertIs(getattr(st_embed, name), getattr(shared_core, name), name)
        for name in ("build_manifest", "calculation_spec", "calculation_log_spec", "WidgetSpecError",
                     "MANIFEST_VERSION", "KNOWN_OPTIONS"):
            self.assertIs(getattr(st_spec, name), getattr(shared_widgets, name), name)


class TestCluster01bv_ReflexLexView(SimpleTestCase):
    """``lex_view`` in Reflex."""

    # -- 1.718 ---------------------------------------------------------
    def test_1_718_handlers_ask_for_their_events_and_receive_them(self) -> None:
        """
        Scenario 1.718: a handler is what asks the page for its kind of event.
        Given: lex_view with an on_create handler; then with on_event alone; then with none.
        When:  each is built.
        Then:  the first asks only for create events and wires the handler;
               on_event alone asks for every kind; with none the URL asks for
               nothing; events are believed only from lex-app's origin.
        """
        only_create = _props(_frame_of(embed.lex_view("investor/create", base_url=_BASE,
                                                      on_create=_Scope.noted)))
        self.assertIn("emit_create=true", only_create["src"])
        self.assertNotIn("emit_update", only_create["src"])
        self.assertEqual(only_create["expected_origin"], _BASE)
        self.assertEqual(only_create["mode"], "view")
        frame = _frame_of(embed.lex_view("investor/create", base_url=_BASE, on_create=_Scope.noted))
        self.assertIn("on_create", frame.event_triggers)
        everything = _props(_frame_of(embed.lex_view("investor", base_url=_BASE, on_event=_Scope.noted)))
        for param in shared.EVENT_FLAGS.values():
            self.assertIn(f"{param}=true", everything["src"])
        plain = _props(_frame_of(embed.lex_view("investor", base_url=_BASE)))
        self.assertNotIn("emit_", plain["src"])
        self.assertTrue(plain["src"].endswith("#embed"))

    # -- 1.719 ---------------------------------------------------------
    def test_1_719_a_record_named_by_a_state_var_is_joined_in_the_browser(self) -> None:
        """
        Scenario 1.719: a compiled page frames the record the URL names at run time.
        Given: a path that is a state var, an f-string embedding one, and filters held in state.
        When:  lex_view is built.
        Then:  the frame's URL is a var joining lex-app's address, the record's
               path and the embed query in the browser; the filters are the
               state var itself, posted when they change, not frozen into the URL.
        """
        for path in (_Scope.pk, f"investor/{_Scope.pk}/show"):
            with self.subTest(path=str(path)):
                src = _frame_of(embed.lex_view(path, base_url=_BASE)).src
                self.assertIsInstance(src, rx.Var)
                rendered = str(src)
                self.assertIn(_BASE, rendered)
                self.assertIn("pk", rendered)
                self.assertIn("embed=true", rendered)
        frame = _frame_of(embed.lex_view("investor", base_url=_BASE, filters=_Scope.team))
        self.assertIn("team", str(frame.filters))
        self.assertEqual(_props(frame)["url_filters_token"], "")

    # -- 1.720 ---------------------------------------------------------
    def test_1_720_lex_app_is_framed_from_the_app_address_beside_the_dashboards(self) -> None:
        """
        Scenario 1.720: where lex-app's frames point.
        Given: REACT_APP_URL set; a dashboard on a customer's domain; nothing set.
        When:  the base URL is resolved.
        Then:  the explicit URL; else the app hostname on the dashboard's own
               registrable domain (a third-party frame would lose the session);
               else a local ``lex start``.
        """
        cases = [
            ({"REACT_APP_URL": "https://lex.example.com/", "LEX_FRONTEND_URL": ""}, "https://lex.example.com"),
            ({"REACT_APP_URL": "", "LEX_FRONTEND_URL": "", "REFLEX_URL": "https://reflex.app.customer.example",
              "LEX_APP_HOSTS": "app.platform.example,app.customer.example", "LEX_REFLEX_HOSTS": ""},
             "https://app.customer.example"),
            ({"REACT_APP_URL": "", "LEX_FRONTEND_URL": "", "REFLEX_URL": "", "LEX_APP_HOSTS": "",
              "DOMAIN_HOSTED": "", "LEX_REFLEX_HOSTS": ""}, "http://localhost:8000"),
        ]
        for env, expected in cases:
            with self.subTest(env=env), patch.dict(os.environ, env):
                self.assertEqual(embed.lex_app_base_url(), expected)


class TestCluster01bv_ReflexWidgets(SimpleTestCase):
    """``lex_widgets`` and ``lex_calculation`` in Reflex."""

    # -- 1.721 ---------------------------------------------------------
    def test_1_721_widgets_post_one_validated_manifest_with_runtime_records(self) -> None:
        """
        Scenario 1.721: several controls, one frame, one manifest lex-app's widget host accepts.
        Given: a calculation of a state-var record reporting its status, and the
               log of a literal record; then a single calculation; then bad specs.
        When:  they are framed.
        Then:  one widget-host frame (``/embed.html?embed=true``) carries a version-1
               manifest whose record is the state var, with ids unique per
               widget; ``on_status`` is wired; a log request opens the default
               log dialog unless the page handles it; an empty frame, an unknown
               option and a duplicate id are refused when the page is built.
        """
        component = embed.lex_widgets(
            embed.calculation("fund", _Scope.pk, on_status=True, show_log=True),
            embed.calculation_log("fund", 7),
            on_status=_Scope.noted,
            base_url=_BASE,
        )
        frame = _frame_of(component)
        props = _props(frame)
        self.assertEqual(props["src"], f"{_BASE}/embed.html?embed=true")
        self.assertEqual(props["mode"], "widgets")
        manifest = frame.manifest._var_value
        self.assertEqual(manifest["version"], shared.MANIFEST_VERSION)
        calc, log = manifest["widgets"]
        self.assertIsInstance(calc["pk"], rx.Var, "the record is the state var, read in the browser")
        self.assertIn("pk", str(calc["pk"]))
        self.assertTrue(calc["options"]["on_status"])
        self.assertEqual((log["id"], log["type"], log["pk"]), ("calculation_log_fund_7", "calculation_log", 7))
        self.assertIn("on_status", frame.event_triggers)
        self.assertIn("on_open_log", frame.event_triggers)
        self.assertIn("open_log", str(frame.event_triggers["on_open_log"]))
        self.assertIn("RadixThemesDialog", str(component), "the default log dialog is on the page")
        handled = embed.lex_widgets(embed.calculation("fund", 1), on_open_log=_Scope.noted, base_url=_BASE)
        self.assertNotIn("RadixThemesDialog", str(handled))
        single = _frame_of(embed.lex_calculation("fund", 1, on_status=_Scope.noted))
        self.assertIn("on_status", single.event_triggers)
        a, b = embed.calculation("fund", _Scope.pk), embed.calculation("fund", _Scope.pk)
        self.assertNotEqual(a["id"], b["id"])
        with self.assertRaises(shared.WidgetSpecError):
            embed.lex_widgets()
        with self.assertRaises(shared.WidgetSpecError):
            embed.lex_widgets(embed.calculation("fund", 1, id="x"), embed.calculation("fund", 2, id="x"))
        with self.assertRaises(TypeError):
            embed.calculation("fund", 1, height=10)

    # -- 1.722 ---------------------------------------------------------
    def test_1_722_a_calculations_log_opens_over_the_page(self) -> None:
        """
        Scenario 1.722: the default answer to a widget's log request.
        Given: the log state.
        When:  a widget asks for the log of fund 7's run abc, then sends a request without a record.
        Then:  the dialog opens on a one-widget manifest for that run's log;
               the incomplete request changes nothing.
        """
        state = type("Log", (), {})()
        state.__dict__.update(log_open=False, log_manifest={}, log_title="")
        embed.LexWidgetLogState.open_log.fn(state, {"model": "fund", "pk": 7, "calculationId": "abc"})
        self.assertTrue(state.log_open)
        widget = state.log_manifest["widgets"][0]
        self.assertEqual((widget["type"], widget["model"], str(widget["pk"]), widget["options"]["calculation_id"]),
                         ("calculation_log", "fund", "7", "abc"))
        state.log_open = False
        embed.LexWidgetLogState.open_log.fn(state, {"model": "fund"})
        self.assertFalse(state.log_open)


_FRAME_HARNESS = r"""
const fs = require("fs");
const strip = (src) => src.split("\n").filter((l) => !l.startsWith("import ")).join("\n")
  .replace(/export function/g, "function");
const hostSrc = strip(fs.readFileSync(process.argv[1], "utf8"));
const frameSrc = strip(fs.readFileSync(process.argv[2], "utf8"));
const listeners = {};
const posted = [];
const child = { postMessage: (m, o) => posted.push({ m, o }) };
global.window = {
  self: {}, top: {}, location: { ancestorOrigins: ["https://app.customer.example"] },
  addEventListener: (t, f) => { listeners[t] = f; }, removeEventListener: () => {},
};
const effects = [];
const React = {
  useRef: (v) => ({ current: v }),
  useState: (v) => { const box = [v, (n) => { box[0] = typeof n === "function" ? n(box[0]) : n; }]; return box; },
  useMemo: (f) => f(), useCallback: (f) => f, useEffect: (f) => effects.push(f),
  useContext: () => ({ resolvedColorMode: "dark" }),
  createElement: (type, props) => ({ type, props }),
};
const api = new Function("window", "React", "TextEncoder", "btoa",
  "const {useRef,useState,useMemo,useCallback,useEffect,useContext,createElement}=React;" +
  "const ColorModeContext=null;" + hostSrc + frameSrc +
  "\nreturn {filtersToken, effectiveOrigin, rebase, LexFrame};")(global.window, React, TextEncoder, btoa);
const got = [];
const handlers = { onCreate: (p) => got.push(["create", p]), onStatus: (p) => got.push(["status", p]),
                   onEvent: (e) => got.push(["event", e.type]) };
const out = { token: api.filtersToken({ b: [2, 1], a: "x", c: null }),
              origin: api.effectiveOrigin("https://app.platform.example", ["https://app.customer.example"]),
              stranger: api.effectiveOrigin("https://app.platform.example", ["https://other.example"]),
              rebased: api.rebase("https://app.platform.example/fund?embed=true", "https://app.platform.example",
                                  "https://app.customer.example") };
// a view frame with filters the URL does not carry
let el = api.LexFrame({ src: "https://app.platform.example/fund?embed=true#embed",
  expectedOrigin: "https://app.platform.example", appOrigins: ["https://app.customer.example"], mode: "view",
  filters: { status: ["open"] }, urlFiltersToken: "", height: 600, ...handlers });
out.src = el.props.src;
el.props.ref.current = { contentWindow: child };
effects.forEach((f) => f());
el.props.onLoad();
out.afterLoad = posted.map((p) => [p.m.type, p.o]);
const msg = (data, origin, source) => listeners.message({ data, origin: origin || "https://app.customer.example",
                                                          source: source || child });
msg({ source: "lex-app", version: 1, type: "create", id: "1", payload: { id: 9 } });
msg({ source: "lex-app", version: 1, type: "create", id: "1", payload: { id: 9 } });
msg({ source: "lex-app", version: 1, type: "create", id: "2", payload: { id: 10 } }, "https://evil.example");
msg({ source: "lex-app", version: 1, type: "create", id: "3", payload: { id: 11 } }, null, {});
msg({ source: "lex-app", version: 2, type: "create", id: "4", payload: { id: 12 } });
out.got = got.slice();
// a widgets frame
posted.length = 0; effects.length = 0; got.length = 0;
el = api.LexFrame({ src: "https://app.platform.example/embed.html?embed=true",
  expectedOrigin: "https://app.platform.example", appOrigins: [], mode: "widgets", minHeight: 48,
  manifest: { version: 1, widgets: [] }, ...handlers });
el.props.ref.current = { contentWindow: child };
effects.forEach((f) => f());
el.props.onLoad();
listeners.message({ data: { source: "lex-app", version: 1, type: "widget_host_ready" },
                    origin: "https://app.platform.example", source: child });
listeners.message({ data: { source: "lex-app", version: 1, type: "calculation_status", id: "s1",
                            payload: { widget_id: "w", status: "SUCCESS" } },
                    origin: "https://app.platform.example", source: child });
out.widgets = posted.map((p) => [p.m.type, p.o]);
out.widgetsGot = got.slice();
console.log(JSON.stringify(out));
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="node is needed to run the frame")
class TestCluster01bv_TheFrame(SimpleTestCase):
    """``_js/lex_frame.jsx``, run under node with React's hooks stubbed."""

    # -- 1.723 ---------------------------------------------------------
    def test_1_723_the_frame_speaks_lex_apps_contract_and_trusts_only_lex_app(self) -> None:
        """
        Scenario 1.723: the frame opens lex-app where the user is, and believes only lex-app.
        Given: a dashboard framed by lex-app on a customer's domain, framing a
               lex-app page with filters, then lex-app's widget host.
        When:  the frames load and messages arrive -- an event, its duplicate, one
               from a stranger's origin, one from another window, one of a newer
               version; the host saying it is ready, a calculation's status.
        Then:  the filters token is Python's, byte for byte; the page opens on the
               customer's domain lex-app was reached on (a stranger's never); each
               load posts the theme, then the filters the URL lacked; only the
               first, genuine event reaches its handler and the catch-all; the
               widget host gets its manifest and the theme when ready, and the
               status reaches ``on_status``.
        """
        result = subprocess.run(
            ["node", "-e", _FRAME_HARNESS, str(JS_DIR / "lex_host.jsx"), str(JS_DIR / "lex_frame.jsx")],
            capture_output=True, text=True, timeout=60, check=True,
        )
        out = json.loads(result.stdout)
        self.assertEqual(out["token"], shared.encode_filters_token({"b": [2, 1], "a": "x", "c": None}))
        self.assertEqual(out["origin"], "https://app.customer.example")
        self.assertEqual(out["stranger"], "https://app.platform.example")
        self.assertEqual(out["rebased"], "https://app.customer.example/fund?embed=true")
        self.assertEqual(out["src"], "https://app.customer.example/fund?embed=true#embed")
        self.assertEqual(out["afterLoad"], [["theme", "https://app.customer.example"],
                                            ["filter", "https://app.customer.example"]])
        self.assertEqual(out["got"], [["event", "create"], ["create", {"id": 9}]])
        self.assertEqual(out["widgets"], [["theme", "https://app.platform.example"],
                                          ["manifest", "https://app.platform.example"],
                                          ["theme", "https://app.platform.example"]])
        self.assertEqual(out["widgetsGot"], [["event", "calculation_status"],
                                             ["status", {"widget_id": "w", "status": "SUCCESS"}]])
