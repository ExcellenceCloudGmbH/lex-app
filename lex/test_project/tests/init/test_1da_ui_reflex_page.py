"""lex.ui links: a page written in raw Reflex as somewhere a link may lead.

Intent: a project moves to lex.ui a table at a time, so its pages are raw Reflex pages
(`app.add_page`) for a long while, and a lex.ui table on one of them must still link each row to
another of them. Links name pages, never route strings typed into a link, so text from data never
becomes an address. A regression is a converted table whose rows can no longer be opened in a new
tab, or a row link built from a string.

Cluster 1da -- scenario 1.824. Type: U.
Covers: lex/lex_app/reflex/ui/pages/links.py (reflex_page), lex/ui.py.
Run: python -m lex pytest lex/test_project/tests/init/test_1da_ui_reflex_page.py -v
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest
import reflex as rx
from django.test import SimpleTestCase

from lex import ui
from lex.lex_app.reflex.ui.pages import links
from lex.lex_app.reflex.ui.pages.links import Address
from lex.lex_app.reflex.ui.state import check_app_routes
from lex.test_project.tests.init.test_1cw_ui_rows_table import PeopleState

pytestmark = pytest.mark.init



class TestCluster01da_ReflexPage(SimpleTestCase):
    """1.824: ui.reflex_page."""

    # -- 1.824 ---------------------------------------------------------
    def test_1_824_a_raw_reflex_page_is_somewhere_a_link_may_lead(self) -> None:
        """
        Scenario 1.824: links to a raw Reflex page.
        Given: two raw Reflex pages declared with ui.reflex_page, one with a route argument and one
               with a query parameter.
        When:  a rows handler builds an address to one, a table opens one from every row, a link
               and a button lead to them, and the app starts with and without those pages.
        Then:  the address is the route with its value encoded as one piece, and is the kind of
               address a table follows; the table's rows link by their key; the link and the button
               lead there; a missing or unknown value, and a route that is no route, are refused
               with what to write; an app without a declared page does not start, and the message
               names the route.
        """
        registry = patch.dict(links._REFLEX_PAGES, clear=True)
        registry.start()
        self.addCleanup(registry.stop)
        ORDER = ui.reflex_page("/orders/[key]")
        SEARCH = ui.reflex_page("/search", params=("q",))
        address = ui.address(ORDER, key="R 1/2")
        self.assertEqual(address, "/orders/R%201%2F2", "the value stays one piece of the address")
        self.assertIsInstance(address, Address, "a table follows it as a link")
        self.assertEqual(ui.address(SEARCH, q="a&b=c"), "/search?q=a%26b%3Dc")
        self.assertEqual(ui.address(ORDER, key=None), "", "no value, no link")

        page = rx.box(ui.embed(ui.table(PeopleState.people, open=ORDER, columns=["name"]), name="orders"),
                      ui.embed(ui.section("Go", ui.link("Search", to=SEARCH, q="x"),
                                          ui.button("Open", to=ORDER, key="7")), name="go"))
        built = str(page)
        self.assertTrue('to:("/orders/"+(encodeURIComponent(String(r_rx_state_?.["id"]' in built,
                        "every row opens its request by its key, encoded")
        self.assertTrue('to:"/search?q=x"' in built, "a link leads to the raw page")
        self.assertTrue('["path"] : "/orders/7"' in built, "so does a button")

        with self.assertRaises(TypeError) as missing:
            ui.address(ORDER)
        self.assertIn("takes key", str(missing.exception), "a missing value says what the page takes")
        with self.assertRaises(TypeError):
            ui.address(ORDER, key="1", tab="x")
        with self.assertRaises(ValueError) as wrong:
            ui.reflex_page("orders/[key]")
        self.assertIn("starting with /", str(wrong.exception))
        with self.assertRaises(TypeError) as plain:
            ui.address("/orders/1")
        self.assertIn("ui.reflex_page", str(plain.exception), "a route string is no page")
        app = SimpleNamespace(_unevaluated_pages={"orders/[key]": None, "search": None})
        with patch("reflex_base.registry.RegistrationContext.ensure_context",
                   lambda: SimpleNamespace(decorated_pages=[])):
            check_app_routes(app)                       # both pages exist: the app starts
            del app._unevaluated_pages["orders/[key]"]
            with self.assertRaises(ValueError) as missing_page:
                check_app_routes(app)
        self.assertIn("'/orders/[key]'", str(missing_page.exception), "the message names the route")
