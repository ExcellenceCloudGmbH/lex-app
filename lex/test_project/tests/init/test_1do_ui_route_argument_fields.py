"""lex.ui: a state field named like a route argument stops the app at start-up, not the frontend build.

Intent: Reflex makes every dynamic route argument (`/regions/[region]`) a value of every state, and
refuses to build the app's pages when any state already has a field or a computed var of that
name -- at export, long after the project's tests passed. lex-app's start-up route check finds the
same collision, for lex.ui states and plain Reflex states alike, and stops the app with a sentence
naming the field, the state and the route and saying to rename the field. It refuses exactly what
Reflex refuses (`BaseState._check_overwritten_dynamic_args`) and nothing more. A regression is a
project whose tests are green and whose deploy fails on a frontend build error, or a start-up that
refuses an app Reflex would build.

Cluster 1do -- scenarios 1.863-1.865. Type: U.
Covers: lex/lex_app/reflex/ui/state.py (check_app_routes, check_route_fields).
Run: python -m lex pytest lex/test_project/tests/init/test_1do_ui_route_argument_fields.py -v
"""

from __future__ import annotations

import inspect
from types import SimpleNamespace
from unittest.mock import patch

import pytest
import reflex as rx
from django.test import SimpleTestCase
from reflex.state import State as ReflexRoot
from reflex_base.registry import RegistrationContext

from lex import ui
from lex.lex_app.reflex.ui import state as ui_state

pytestmark = pytest.mark.init


class RegionsBoard(ui.State):
    """A lex.ui state whose field is named like a route's argument."""

    region_code: str = ""
    _fiscal_week: int = 0                    # a backend field: Reflex lets an argument of this name through

    @rx.var
    def quarter_label(self) -> str:
        return ""

    @ui.action
    def warehouse_slot(self):                # a handler: Reflex lets an argument of this name through
        return None


class RawOrders(rx.State):
    """A plain Reflex state, outside lex.ui."""

    order_batch: str = ""


class RawOrderLines(RawOrders):
    """A plain Reflex state inheriting the field: Reflex names the state that declares it."""

    line_count: int = 0


def _check(app_routes=(), declared=()) -> None:
    """``check_app_routes`` on an app with these pages added, and these declared with a page decorator."""
    registry = SimpleNamespace(decorated_pages=[(lambda: None, {"route": route}) for route in declared])
    with patch.object(RegistrationContext, "ensure_context", return_value=registry):
        ui_state.check_app_routes(SimpleNamespace(_unevaluated_pages=dict.fromkeys(app_routes)))


def _reflex_refuses(name: str) -> bool:
    """Whether Reflex itself refuses a dynamic route argument ``name`` for the states defined now."""
    from reflex.utils.exceptions import DynamicRouteArgShadowsStateVarError

    try:
        ReflexRoot._check_overwritten_dynamic_args([name])
    except DynamicRouteArgShadowsStateVarError:
        return True
    return False


class TestCluster01do_RouteArgumentFields(SimpleTestCase):
    """1.863-1.865: a field named like a route argument -- refused at start-up, exactly as Reflex refuses."""

    # -- 1.863 ---------------------------------------------------------
    def test_1_863_a_field_named_like_a_route_argument_stops_the_app_with_a_sentence(self) -> None:
        """
        Scenario 1.863: /regions/[region_code] beside a state field region_code.
        Given: a lex.ui state with a field and a computed var, and a plain Reflex state with a
               field, each named like a dynamic argument of a page -- one the app has added, one
               declared with a page decorator, an optional argument.
        When:  the app's routes are checked at start-up.
        Then:  the app stops with a ValueError naming the field, its state (and module) and the
               route, saying Reflex would not build the pages, and saying to rename the field;
               Reflex itself refuses each of these names.
        """
        module = RegionsBoard.__module__
        cases = (
            ({"app_routes": ["/regions/[region_code]"]}, "/regions/[region_code]", "the field RegionsBoard.region_code"),
            ({"declared": ["/quarters/[quarter_label]/report"]}, "/quarters/[quarter_label]/report",
             "the computed var RegionsBoard.quarter_label"),
            ({"declared": ["/orders/[[order_batch]]"]}, "/orders/[[order_batch]]", "the field RawOrders.order_batch"),
            ({"app_routes": ["lines/[line_count]"]}, "/lines/[line_count]", "the field RawOrderLines.line_count"),
        )
        for pages, route, named in cases:
            with self.subTest(route=route), self.assertRaises(ValueError) as stopped:
                _check(**pages)
            name = named.rsplit(".", 1)[1]
            self.assertEqual(
                str(stopped.exception),
                f"route {route!r}: its dynamic argument [{name}] has the name of {named} (in {module}). Reflex "
                f"makes a route argument a value of every state, and does not build the app's pages while a state "
                f"has a field of that name. Rename the field, e.g. {name}_value.")
            self.assertTrue(_reflex_refuses(name), f"Reflex itself refuses [{name}]")

    # -- 1.864 ---------------------------------------------------------
    def test_1_864_what_reflex_lets_through_starts(self) -> None:
        """
        Scenario 1.864: only what Reflex refuses.
        Given: routes whose arguments are named like a backend field (_fiscal_week), an event
               handler (warehouse_slot), a static part of a path, or nothing any state has.
        When:  the app's routes are checked.
        Then:  the app starts; Reflex lets each of these names through too.
        """
        routes = ["/", "/weeks/[_fiscal_week]", "/slots/[warehouse_slot]", "/region_code/list", "/tickets/[ticket_id]"]
        _check(app_routes=routes, declared=["/reports/[report_id]"])          # nothing to refuse
        for name in ("_fiscal_week", "warehouse_slot", "ticket_id", "report_id"):
            with self.subTest(name=name):
                self.assertFalse(_reflex_refuses(name), f"Reflex lets [{name}] through")

    # -- 1.865 ---------------------------------------------------------
    def test_1_865_the_route_check_runs_before_the_pages_are_built(self) -> None:
        """
        Scenario 1.865: where the check sits.
        Given: lex-app's start-up (ui.install, called by create_app once the project's pages are
               declared) and a route with both a helper's name and a field's name.
        When:  the routes are checked.
        Then:  install checks the routes with check_app_routes, which checks the fields; a
               helper's name ([user]) is still refused first, with its own sentence.
        """
        from lex.lex_app.reflex.ui.pages import declare

        self.assertIn("check_app_routes(app)", inspect.getsource(declare.install), "start-up checks the routes")
        self.assertIn("check_route_fields(", inspect.getsource(ui_state.check_app_routes),
                      "and the route check checks the state fields")
        with self.assertRaises(ValueError) as helper:
            _check(app_routes=["/users/[user]/[region_code]"])
        self.assertIn("ui.State helper self.user", str(helper.exception), "a helper's name has its own sentence")
