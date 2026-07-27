"""
Cluster 1r: Streamlit ``lex_view`` embed helper — URL building + bidirectional dispatch.

Intent: ``lex.lex_app.streamlit.embed.lex_view()`` is the framework's
Python-side helper that lets a Streamlit dashboard embed a page from
the lex-app React frontend. Two operating modes:

* **Plain iframe (legacy):** call with no callback / no ``serializer=``
  bidirectional-flagged kwarg → ``components.iframe`` renders the URL
  and the function returns ``None``. Every existing call site relies on
  this — a regression that always switched to the custom-component path
  would change the return type from ``None`` to ``dict | None`` and
  break user code that ignores the return value.

* **Bidirectional custom component:** any ``on_*`` opt-in flag set
  (``on_create`` / ``on_update`` / ``on_select`` / ``on_navigate`` /
  ``on_flow_step``) → ``render_lex_view_component`` is called and its
  return value (the latest ``postMessage`` envelope from the React app)
  is returned.

This cluster pins **both modes** end-to-end through the public
``lex_view`` entry point, per the Golden Rule (a surface is only
covered when a test drives it the way a real caller reaches it).

Cluster 1r — scenarios 1.148–1.156. Type: U (SimpleTestCase, no DB).
Covers: lex/lex_app/streamlit/embed.py (``lex_view`` query-param
        composition, mode branching, opt-in flag forwarding, serializer
        passthrough, expected_origin computation).

Run: python -m lex pytest lex/test_project/tests/init/test_1r_lex_view_embed_helper.py -v
"""

from __future__ import annotations

import unittest
import urllib.parse
from unittest.mock import patch

import pytest

from lex.lex_app.streamlit.embed import Flow, lex_view

pytestmark = pytest.mark.init


def _parse_url(url: str) -> tuple[str, dict, str]:
    """Return (path, params-as-dict-of-lists, fragment) from a final URL."""
    parsed = urllib.parse.urlparse(url)
    return parsed.path, urllib.parse.parse_qs(parsed.query), parsed.fragment


class TestCluster01r_LexViewLegacyIframeMode(unittest.TestCase):
    """Plain ``components.iframe`` path — no callbacks requested."""

    # -- 1.148 --------------------------------------------------------
    def test_1_148_no_callbacks_returns_none_and_calls_iframe(self) -> None:
        """Scenario 1.148: with no ``on_*`` flag, ``lex_view`` returns
        ``None`` and dispatches to ``components.iframe``.

        Pins the legacy contract: existing call sites that ignore the
        return value must keep working. A refactor that always routed
        through the custom component would change ``lex_view``'s return
        type to ``dict | None`` and quietly break any user code doing
        ``lex_view(...)``.
        """
        with patch("lex.lex_app.streamlit.embed.components.iframe") as iframe, \
             patch("lex.lex_app.streamlit.embed.render_lex_view_component") as component:
            result = lex_view("investor", base_url="https://app.example")

        self.assertIsNone(
            result,
            f"Legacy iframe path must return None, got {result!r}",
        )
        component.assert_not_called()
        iframe.assert_called_once()
        called_url = iframe.call_args.args[0]
        self.assertIn(
            "embed=true", called_url,
            f"Legacy iframe URL must carry the embed flag. Got: {called_url}",
        )

    # -- 1.149 --------------------------------------------------------
    def test_1_149_path_normalised_and_embed_fragment_added(self) -> None:
        """Scenario 1.149: leading ``/`` added if missing; ``#embed``
        fragment appended; ``embed=true`` query param set.

        These three transforms are what makes the React app render in
        embed mode (no sidebar / no appbar). Dropping any one would
        cause the embed to render the full React shell, ruining the
        dashboard layout.
        """
        with patch("lex.lex_app.streamlit.embed.components.iframe") as iframe:
            lex_view("investor/42", base_url="https://app.example")

        final_url = iframe.call_args.args[0]
        path, params, fragment = _parse_url(final_url)
        self.assertEqual(
            path, "/investor/42",
            f"Path must be normalised with leading /. Got: {path!r}",
        )
        self.assertEqual(
            params.get("embed"), ["true"],
            f"embed=true query param required for React embed mode. "
            f"Got params: {params!r}",
        )
        self.assertIn(
            "embed", fragment,
            f"#embed fragment required for React embed mode. "
            f"Got fragment: {fragment!r}",
        )

    # -- 1.150 --------------------------------------------------------
    def test_1_150_flow_and_redirect_params_forwarded(self) -> None:
        """Scenario 1.150: ``flow`` round-trips as JSON-encoded
        ``lex_flow``; ``redirect_after_*`` forwarded verbatim.

        These are the React app's contract: the routing decisions live
        in the URL params so the embed re-applies them after each
        in-iframe navigation. A regression that dropped ``lex_flow``
        would silently disable every multi-step workflow.
        """
        with patch("lex.lex_app.streamlit.embed.components.iframe") as iframe:
            lex_view(
                "investor/create",
                base_url="https://app.example",
                flow=Flow().after_create("investor", "/cashflow/{id}/edit"),
                redirect_after_update="/{resource}/{id}",
            )

        _, params, _ = _parse_url(iframe.call_args.args[0])
        self.assertIn("lex_flow", params, f"Flow param missing. Got: {params!r}")
        decoded = params["lex_flow"][0]
        self.assertIn(
            '"investor/create"', decoded,
            f"Flow JSON must contain the create key. Got: {decoded!r}",
        )
        self.assertEqual(
            params.get("redirect_after_update"), ["/{resource}/{id}"],
            f"redirect_after_update must be forwarded verbatim. "
            f"Got: {params!r}",
        )


class TestCluster01r_LexViewBidirectionalMode(unittest.TestCase):
    """Custom-component path — at least one ``on_*`` flag set."""

    # -- 1.151 --------------------------------------------------------
    def test_1_151_on_select_routes_to_component_returns_value(self) -> None:
        """Scenario 1.151: ``on_select=True`` invokes the custom
        component (not ``components.iframe``) and returns the
        component's return value.

        This is the user-facing reactive contract: ``lex_view`` becomes
        a value-returning call so the dashboard can switch on the event
        envelope. A regression that still rendered the plain iframe
        would silently break every AG-Grid-selection-driven dashboard.
        """
        sentinel_event = {"source": "lex-app", "type": "select",
                          "payload": {"ids": [42]}}
        with patch("lex.lex_app.streamlit.embed.components.iframe") as iframe, \
             patch(
                 "lex.lex_app.streamlit.embed.render_lex_view_component",
                 return_value=sentinel_event,
             ) as component:
            result = lex_view(
                "investor", on_select=True, base_url="https://app.example",
            )

        self.assertEqual(
            result, sentinel_event,
            f"lex_view must return the component's value when callbacks "
            f"are opted in. Got: {result!r}",
        )
        iframe.assert_not_called()
        component.assert_called_once()
        kw = component.call_args.kwargs
        self.assertIn("url", kw, f"Component must receive url kwarg. Got: {kw!r}")
        self.assertIn("expected_origin", kw)

    # -- 1.152 --------------------------------------------------------
    def test_1_152_each_on_flag_forwards_emit_query_param(self) -> None:
        """Scenario 1.152: each ``on_*`` flag forwards a matching
        ``emit_<event>=true`` query param.

        The React side only attaches event handlers for the flags it
        sees here, so off-by-default cost stays zero for unflagged
        events. A regression that dropped one of these mappings would
        silently disable that event type — the dashboard would never
        re-run on, say, AG Grid selection.
        """
        with patch(
            "lex.lex_app.streamlit.embed.render_lex_view_component",
        ) as component:
            lex_view(
                "investor",
                on_create=True,
                on_update=True,
                on_select=True,
                on_navigate=True,
                on_flow_step=True,
                base_url="https://app.example",
            )

        _, params, _ = _parse_url(component.call_args.kwargs["url"])
        for flag in (
            "emit_create", "emit_update", "emit_select",
            "emit_navigate", "emit_flow_step",
        ):
            self.assertEqual(
                params.get(flag), ["true"],
                f"{flag} must be forwarded as true. Got params: {params!r}",
            )

    # -- 1.153 --------------------------------------------------------
    def test_1_153_serializer_kwarg_forwards_query_param(self) -> None:
        """Scenario 1.153: ``serializer="X"`` forwards as
        ``?serializer=X`` on the embed URL.

        This is the surface paired with the backend Cluster-12h
        contract: an unknown name surfaces as HTTP 400 from the API.
        A regression that dropped this kwarg would silently render
        every embed through the default serializer regardless of what
        the dashboard asked for — no error, just wrong data.
        """
        with patch("lex.lex_app.streamlit.embed.components.iframe") as iframe:
            lex_view(
                "investor",
                serializer="InvestorWithFundSerializer",
                base_url="https://app.example",
            )

        _, params, _ = _parse_url(iframe.call_args.args[0])
        self.assertEqual(
            params.get("serializer"), ["InvestorWithFundSerializer"],
            f"serializer kwarg must round-trip as ?serializer=. "
            f"Got: {params!r}",
        )

    # -- 1.154 --------------------------------------------------------
    def test_1_154_expected_origin_computed_from_base_url(self) -> None:
        """Scenario 1.154: ``expected_origin`` passed to the component
        is the scheme + netloc of the resolved base URL.

        The component uses ``expected_origin`` to drop inbound
        ``postMessage`` events whose origin doesn't match. A regression
        that passed the full URL (with path/query) instead of the
        origin would cause every legitimate message to be dropped —
        the dashboard would never react to anything.
        """
        with patch(
            "lex.lex_app.streamlit.embed.render_lex_view_component",
        ) as component:
            lex_view(
                "investor/create",
                on_create=True,
                base_url="https://app.example/with/extra/path",
            )

        kw = component.call_args.kwargs
        self.assertEqual(
            kw["expected_origin"], "https://app.example",
            f"expected_origin must be scheme+netloc only (no path). "
            f"Got: {kw['expected_origin']!r}",
        )

    # -- 1.155 --------------------------------------------------------
    def test_1_155_serializer_alone_does_not_switch_to_component(self) -> None:
        """Scenario 1.155: ``serializer=`` alone (without any ``on_*``
        flag) keeps the legacy iframe path.

        Documents the boundary of the mode switch: ``serializer=`` is
        a backend-shaping concern that works in both modes; only the
        explicit ``on_*`` flags trigger the bidirectional component.
        A regression that flipped any param into "callbacks mode" would
        change return types unexpectedly and break legacy callers that
        innocently added ``serializer="…"``.
        """
        with patch("lex.lex_app.streamlit.embed.components.iframe") as iframe, \
             patch(
                 "lex.lex_app.streamlit.embed.render_lex_view_component",
             ) as component:
            result = lex_view(
                "investor",
                serializer="X",
                base_url="https://app.example",
            )

        self.assertIsNone(
            result,
            f"serializer alone must not switch to bidirectional mode "
            f"(would change return type). Got: {result!r}",
        )
        component.assert_not_called()
        iframe.assert_called_once()

    # -- 1.156 --------------------------------------------------------
    def test_1_156_demo_dashboard_module_contract(self) -> None:
        """Scenario 1.156: the published demo dashboard at
        ``lex/test_project/dashboards/lex_view_callbacks_demo.py``
        imports cleanly and calls ``lex_view`` with every opt-in flag
        plus a ``serializer=`` and a ``Flow``.

        This is the published example developers copy from. A
        refactor that renames an ``on_*`` kwarg, drops the
        ``serializer=`` kwarg, or breaks the ``Flow`` constructor
        would silently invalidate the docs and every dashboard built
        from them. Pinning the example here makes that a CI failure.
        """
        from lex.test_project.dashboards import lex_view_callbacks_demo as demo

        with patch(
            "lex.test_project.dashboards.lex_view_callbacks_demo.lex_view",
            return_value={"type": "create", "payload": {"id": 1}},
        ) as lv, patch(
            "lex.test_project.dashboards.lex_view_callbacks_demo.st"
        ):
            event = demo.render()

        self.assertEqual(
            event, {"type": "create", "payload": {"id": 1}},
            f"render() must return the lex_view event verbatim. "
            f"Got: {event!r}",
        )
        lv.assert_called_once()
        kwargs = lv.call_args.kwargs
        for flag in (
            "on_create", "on_update", "on_select",
            "on_navigate", "on_flow_step",
        ):
            self.assertTrue(
                kwargs.get(flag),
                f"Demo dashboard must opt in to {flag} so the published "
                f"example actually exercises that surface. Got: {kwargs!r}",
            )
        self.assertEqual(
            kwargs.get("serializer"), "default",
            f"Demo must demonstrate the serializer= kwarg. Got: {kwargs!r}",
        )
        self.assertIn(
            "flow", kwargs,
            f"Demo must demonstrate a Flow. Got kwargs: {list(kwargs)!r}",
        )


class TestCluster01r_LexViewComponentLazyDeclaration(unittest.TestCase):
    """Cluster 1r: the lex_view custom component is declared lazily, on
    first ``render_lex_view_component()`` call, **not** at module import.

    Intent: Streamlit (≥1.30) only registers a custom component if
    ``get_script_run_ctx()`` returns non-None at the moment of
    ``declare_component`` (see
    ``streamlit/components/v1/component_registry.py``, the
    ``if ctx is not None:`` guard). Calling ``declare_component`` at
    *module import* time means whichever thread happens to import the
    module first wins: if it has no ctx the call is a silent no-op,
    the module is cached in ``sys.modules``, and the registration is
    never retried — so every subsequent
    ``/component/<name>/index.html`` HTTP request returns
    ``404 "Component not found"`` and the iframe stays blank.

    These scenarios pin the lazy-declaration contract: import never
    declares; the first render-side call declares; subsequent calls
    re-use the cached declaration.
    """

    def setUp(self) -> None:
        # Each test starts from a clean slate so the order is irrelevant
        # and the cached declaration from a previous test in the same
        # process doesn't mask a regression that re-introduces eager
        # registration.
        import lex.lex_app.streamlit._lex_view_component as comp_mod
        self._comp_mod = comp_mod
        self._saved_func = comp_mod._component_func
        comp_mod._component_func = None

    def tearDown(self) -> None:
        self._comp_mod._component_func = self._saved_func

    # -- 1.157 --------------------------------------------------------
    def test_1_157_import_does_not_declare_component(self) -> None:
        """Scenario 1.157: importing
        ``lex.lex_app.streamlit._lex_view_component`` must NOT call
        ``components.declare_component`` — the holder ``_component_func``
        starts ``None``.

        Documents the white-screen bug guard: an eager
        ``declare_component`` at import time silently no-ops when the
        importing thread has no ScriptRunContext, leaving the component
        permanently un-registered and producing the "Component not found"
        404 the user reported. The test fails the moment someone
        re-introduces a top-level ``declare_component(...)`` call.
        """
        # The setUp already reset _component_func; assert the post-import
        # invariant: nothing in the module body should have populated it.
        self.assertIsNone(
            self._comp_mod._component_func,
            "Module import must not call declare_component(). "
            "Got cached _component_func: {!r}".format(
                self._comp_mod._component_func
            ),
        )

    # -- 1.158 --------------------------------------------------------
    def test_1_158_first_render_declares_then_caches(self) -> None:
        """Scenario 1.158: the first ``render_lex_view_component()``
        call invokes ``components.declare_component`` exactly once;
        the second call re-uses the cache and does NOT re-declare.

        Re-declaring on every render would (a) re-register the
        component with Streamlit's registry every script run
        (harmless but wasteful) and (b) break the
        ``LEX_VIEW_COMPONENT_URL`` dev-override contract, which
        assumes the URL is read once at first declaration.
        """
        from lex.lex_app.streamlit._lex_view_component import (
            render_lex_view_component,
        )

        sentinel_value = {"source": "lex-app", "type": "create"}

        def fake_declare(name, path=None, url=None):
            def fake_func(**kwargs):
                return sentinel_value
            fake_func._declared_name = name
            return fake_func

        with patch(
            "lex.lex_app.streamlit._lex_view_component.components.declare_component",
            side_effect=fake_declare,
        ) as declare:
            result1 = render_lex_view_component(url="https://x/", height=10)
            result2 = render_lex_view_component(url="https://x/", height=10)

        self.assertEqual(
            declare.call_count, 1,
            "declare_component must be called exactly once across "
            "multiple renders (lazy + cached). Got call_count={}".format(
                declare.call_count
            ),
        )
        self.assertEqual(result1, sentinel_value)
        self.assertEqual(result2, sentinel_value)
