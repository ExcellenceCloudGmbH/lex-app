"""lex.ui's frame: breadcrumbs, from the area and the page, written by no page.

Intent: a user deep in a project's dashboards -- on one order, opened from the orders list in the
Sales area -- sees where they are and goes back up with one click. lex-app derives the trail from
what the pages already say: the area (`nav=`), the page's title, and, for a page that is not in the
navigation, the listed page whose table opens it or whose button leads to it. A regression is a page
that loses its trail when it becomes a lex.ui page, a trail that needs per-page code, a parent link
that leads to the wrong list, or a record page whose title (a state value) breaks the page.

Cluster 1ec -- scenarios 1.965-1.967, 1.979. Type: U.
Covers: lex/lex_app/reflex/shell.py (Place, place_of, set_place, lex_chrome), lex/lex_app/reflex/ui/pages/declare.py
(finalize, leads_to).
Run: python -m lex pytest lex/test_project/tests/init/test_1ec_ui_breadcrumbs.py -v
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
import reflex as rx
from reflex.compiler.compiler import compile_page

from lex import ui
from lex.lex_app.reflex import shell
from lex.test_project.tests.init.test_1eb_ui_frame_nav import FrameCase

pytestmark = pytest.mark.init


class CrumbOrdersState(ui.State):
    """An orders list and one order."""

    name: str = ""

    @ui.rows
    def orders(self):
        return [{"id": 7, "name": "Order seven"}]

    @ui.load
    def open(self):
        self.name = "Order seven"


def _about(title):
    return ui.record(title=title, sections=[ui.section("About", ui.text("All about it."))])


class TestCluster01ec_Breadcrumbs(FrameCase):
    """1.965-1.967, 1.979: the trail of a listed page, of a record and of a form; which parent; compiled;
    a calculation's run under its page."""

    def declare_sales(self):
        @ui.page("/orders/[key]")
        def order():
            return ui.record(title=CrumbOrdersState.name, load=CrumbOrdersState.open,
                             sections=[ui.section("About", ui.text(CrumbOrdersState.name))])

        @ui.page("/orders/new")
        def new_order():
            return _about("New order")

        @ui.page("/orders", nav="Sales")
        def orders():
            return ui.list(title="Orders", table=ui.table(CrumbOrdersState.orders, columns=["name"], open=order),
                           actions=[ui.button("New order", to=new_order)])

        @ui.page("/help")
        def help_page():
            return _about("Help")

        return order, new_order, orders

    # -- 1.965 ---------------------------------------------------------
    def test_1_965_the_trail_comes_from_the_area_the_parent_and_the_page(self) -> None:
        """
        Scenario 1.965: the trail.
        Given: the orders list in the Sales area, whose table opens an order and whose button opens
               the new-order form; and a help page no listed page leads to.
        When:  lex-app registers the pages.
        Then:  the list's trail is Sales / Orders; the order's is Sales / Orders (a link to the list)
               / its title; the form's is Sales / Orders / New order; the help page has none; no page
               wrote a crumb.
        """
        self.declare_sales()
        from lex.lex_app.reflex.ui.pages import declare

        declare.finalize()
        listed = shell.place_of("/orders")
        self.assertEqual((listed.area, listed.parent), ("Sales", None), "a listed page: its area, then itself")
        record = shell.place_of("/orders/[key]")
        self.assertEqual((record.area, record.parent, record.parent_title), ("Sales", "/orders", "Orders"),
                         "the record's parent is the list whose table opens it, and its area is the list's")
        self.assertIsInstance(record.title, rx.Var, "its title is the record's, a state value")
        form = shell.place_of("/orders/new")
        self.assertEqual((form.area, form.parent, form.title), ("Sales", "/orders", "New order"),
                         "a page a listed page's button leads to has that page as its parent")
        self.assertIsNone(shell.place_of("/help"), "no area, no trail")

    # -- 1.966 ---------------------------------------------------------
    def test_1_966_a_table_that_opens_a_page_is_its_parent_before_a_link(self) -> None:
        """
        Scenario 1.966: which parent.
        Given: an order page a dashboard links to (declared first, in Reports) and the orders list
               opens from its table (declared after, in Sales); and an order page with an area of
               its own.
        When:  lex-app registers the pages.
        Then:  the list whose table opens it is the parent, though the link came first; a page's own
               nav= is its area, its parent still the list that opens it.
        """
        @ui.page("/orders/[key]")
        def order():
            return _about("Order")

        @ui.page("/invoices/[key]", nav="Billing")
        def invoice():
            return _about("Invoice")

        @ui.page("/overview", nav="Reports")
        def overview():
            return ui.record(title="Overview", sections=[ui.section("Latest", ui.link("Latest order", to=order, key=7))])

        @ui.page("/orders", nav="Sales")
        def orders():
            return ui.list(title="Orders", table=ui.table(CrumbOrdersState.orders, columns=["name"], open=order))

        @ui.page("/invoices", nav="Sales")
        def invoices():
            return ui.list(title="Invoices", table=ui.table(CrumbOrdersState.orders, columns=["name"], open=invoice))

        from lex.lex_app.reflex.ui.pages import declare

        declare.finalize()
        self.assertEqual(shell.place_of("/orders/[key]").parent, "/orders", "a table that opens it comes first")
        own = shell.place_of("/invoices/[key]")
        self.assertEqual((own.area, own.parent), ("Billing", "/invoices"), "its own area, its list as parent")

    # -- 1.967 ---------------------------------------------------------
    def test_1_967_the_frame_draws_the_trail_and_it_compiles(self) -> None:
        """
        Scenario 1.967: drawn by the frame.
        Given: the Sales pages, registered; and a raw @lex_page in an area.
        When:  the order page and the raw page are built in lex-app's frame and compiled with
               Reflex's page compiler.
        Then:  each holds RFDS's breadcrumbs: the order's with the area as text, a link to the list
               and the record's title from the state, its own page last; the raw page's with its
               area and title; a page outside any area, and the frame without a route, have none.
        """
        self.declare_sales()
        from lex.lex_app.reflex.ui.pages import declare

        declare.finalize()
        _, module = compile_page("/orders/[key]", self.page_named("/orders/[key]")())
        for expected in ("rfds-breadcrumbs", '"Sales"', '"/orders"', "crumb_orders_state", "aria-current"):
            self.assertIn(expected, module, f"the order's trail: {expected}")
        self.assertNotIn("rfds-breadcrumbs", compile_page("/help", self.page_named("/help")())[1], "no area, no trail")
        self.assertNotIn("rfds-breadcrumbs", str(shell.lex_chrome(rx.text("body"))), "no route, no trail")

        with patch("reflex.page", lambda **kw: (lambda fn: self.pages.append((kw, fn)) or fn)):
            shell.lex_page("/cash", title="Cash", nav="Finance")(lambda: rx.text("cash"))
        _, module = compile_page("/cash", self.page_named("/cash")())
        for expected in ("rfds-breadcrumbs", '"Finance"', '"Cash"'):
            self.assertIn(expected, module, f"a raw page in an area: {expected}")

    # -- 1.979 ---------------------------------------------------------
    def test_1_979_a_calculation_page_in_an_area_and_its_runs_view_under_it(self) -> None:
        """
        Scenario 1.979: ui.calculation in the frame (merge of the frame with the calculation page).
        Given: ui.frame(nav={"Reports": [], "Sales": []}) and a calculation page declared with nav="Reports".
        When:  lex-app registers it, and the page is built in lex-app's frame and compiled.
        Then:  the page is listed in Reports by its title; its trail is the area and the page; while a
               run is open (``?run=``, the same route) the trail goes on: the page becomes a link back to
               its form, and the run's name -- its key until lex-app names it -- is the last crumb. A
               page with one view keeps the plain trail.
        """
        from lex.lex_app.reflex.ui.pages import declare
        from lex.test_project.tests.init.models import UiRegionReport

        ui.frame(nav={"Reports": [], "Sales": []})

        @ui.page("/reports/regions", nav="Reports")
        def region_reports():
            return ui.calculation(UiRegionReport, title="Region report",
                                  fields=[ui.field("region", required=True), ui.field("year", kind="year", required=True)],
                                  history=ui.table(UiRegionReport, columns=["region", "year"]))

        self.declare_sales()
        declare.finalize()
        [entry] = [one for one in shell.nav_entries() if one.route == "/reports/regions"]
        self.assertEqual((entry.section, entry.label), ("Reports", "Region report"), "listed in its area by its title")
        place = shell.place_of("/reports/regions")
        self.assertEqual((place.area, place.title, place.parent), ("Reports", "Region report", None))
        state = declare._DECLARED["/reports/regions"].kind.state
        showing, words = place.view
        self.assertIn("run_id", str(showing), "the run's crumb shows while a run is open")
        self.assertIn("run_name", str(words), "named as lex-app names the run")
        self.assertIn("run_id", str(words), "its key until then")
        trail = str(shell.breadcrumbs("/reports/regions"))
        self.assertIn('"/reports/regions"', trail, "in the run's view the page is the way back to its form")
        _, module = compile_page("/reports/regions", self.page_named("/reports/regions")())
        for expected in ("rfds-breadcrumbs", '"Reports"', '"Region report"', state.get_full_name().split(".")[-1]):
            self.assertIn(expected, module, f"compiled: {expected}")
        self.assertIsNone(shell.place_of("/orders").view, "a page with one view keeps the plain trail")
        self.assertNotIn("run_id", str(shell.breadcrumbs("/orders")))
