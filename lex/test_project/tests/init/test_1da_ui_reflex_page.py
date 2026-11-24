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

import pytest
import reflex as rx
from django.test import SimpleTestCase

from lex import ui
from lex.lex_app.reflex.ui.pages.links import Address
from lex.test_project.tests.init.test_1cw_ui_rows_table import TableDesk

pytestmark = pytest.mark.init

REQUEST = ui.reflex_page("/requests/[req]")
SEARCH = ui.reflex_page("/search", params=("q",))


class TestCluster01da_ReflexPage(SimpleTestCase):
    """1.824: ui.reflex_page."""

    # -- 1.824 ---------------------------------------------------------
    def test_1_824_a_raw_reflex_page_is_somewhere_a_link_may_lead(self) -> None:
        """
        Scenario 1.824: links to a raw Reflex page.
        Given: two raw Reflex pages declared with ui.reflex_page, one with a route argument and one
               with a query parameter.
        When:  a rows handler builds an address to one, a table opens one from every row, and a link
               and a button lead to them.
        Then:  the address is the route with its value encoded as one piece, and is the kind of
               address a table follows; the table's rows link by their key; the link and the button
               lead there; a missing or unknown value, and a route that is no route, are refused
               with what to write.
        """
        address = ui.address(REQUEST, req="R 1/2")
        self.assertEqual(address, "/requests/R%201%2F2", "the value stays one piece of the address")
        self.assertIsInstance(address, Address, "a table follows it as a link")
        self.assertEqual(ui.address(SEARCH, q="a&b=c"), "/search?q=a%26b%3Dc")
        self.assertEqual(ui.address(REQUEST, req=None), "", "no value, no link")

        page = rx.box(ui.embed(ui.table(TableDesk.people, open=REQUEST, columns=["name"]), name="requests"),
                      ui.embed(ui.section("Go", ui.link("Search", to=SEARCH, q="x"),
                                          ui.button("Open", to=REQUEST, req="7")), name="go"))
        built = str(page)
        self.assertTrue('to:("/requests/"+(encodeURIComponent(String(r_rx_state_?.["id"]' in built,
                        "every row opens its request by its key, encoded")
        self.assertTrue('to:"/search?q=x"' in built, "a link leads to the raw page")
        self.assertTrue('["path"] : "/requests/7"' in built, "so does a button")

        with self.assertRaises(TypeError) as missing:
            ui.address(REQUEST)
        self.assertIn("takes req", str(missing.exception), "a missing value says what the page takes")
        with self.assertRaises(TypeError):
            ui.address(REQUEST, req="1", tab="x")
        with self.assertRaises(ValueError) as wrong:
            ui.reflex_page("requests/[req]")
        self.assertIn("starting with /", str(wrong.exception))
        with self.assertRaises(TypeError) as plain:
            ui.address("/requests/1")
        self.assertIn("ui.reflex_page", str(plain.exception), "a route string is no page")
