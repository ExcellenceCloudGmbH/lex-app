"""lex.ui pages: a link names a page function or a record, never a route string.

Intent: routes copied between modules break silently when one is renamed. A link points at the
page function; its parameters are checked against the page's route and declared query
parameters when the page is built, and the error names both pages. A button that opens a page
follows the same rule, and an outside site is the only place an address is written out. A
regression is a link that builds a wrong address without complaint, or an in-app link that
opens a new tab.

Cluster 1cp -- scenarios 1.791-1.793. Type: U.
Covers: lex/lex_app/reflex/ui/pages/links.py, ui/pages/vocab.py (link, button, menu_entry).
Run: python -m lex pytest lex/test_project/tests/init/test_1cp_ui_page_links.py -v
"""

from __future__ import annotations

import pytest
from django.contrib.auth.models import Group
from django.test import SimpleTestCase

from lex import ui
from lex.lex_app.reflex.ui.pages import links, vocab
from lex.lex_app.reflex.ui.pages.parts import Build, PageError

pytestmark = pytest.mark.init


class LinksDesk(ui.State):
    fund: str = ""
    count: int = 0

    @ui.action
    def close(self):
        return ui.toast("Closed")


def fund_page():
    """Stands for a page declared with @ui.page("/funds/[key]", params=["tab"])."""


fund_page.__ui_page__ = links.info_of("/funds/[key]", "fund_page", ["tab"])


def funds_page():
    """Stands for a page declared with @ui.page("/funds")."""


funds_page.__ui_page__ = links.info_of("/funds", "funds_page")

CTX = Build(page="home", route="/")


class TestCluster01cp_PageLinks(SimpleTestCase):
    """1.791-1.793: addresses from page functions; the three kinds of link; buttons."""

    # -- 1.791 ---------------------------------------------------------
    def test_1_791_an_address_is_built_from_the_page_and_its_parameters(self) -> None:
        """
        Scenario 1.791: links.href.
        Given: a page with one route argument and one declared query parameter.
        When:  an address is asked for with fixed values, with state values, with a missing
               argument, with an unknown parameter, and for something that is not a page.
        Then:  fixed values give a plain address; state values give an expression that joins
               them into it (a number is turned into text); a missing or unknown parameter is
               an error naming the linking page, the target page, its route and what it takes;
               a function that is not a page is refused with what to pass instead; a table's
               row link is the route with its one argument replaced by the row's key.
        """
        self.assertEqual(links.href(CTX, fund_page, {"key": "7"}), "/funds/7", "a fixed value gives a plain address")
        self.assertEqual(links.href(CTX, fund_page, {"key": 7, "tab": "docs"}), "/funds/7?tab=docs",
                         "a declared query parameter is appended")
        self.assertEqual(links.href(CTX, funds_page, {}), "/funds", "a page without arguments needs none")
        joined = str(links.href(CTX, fund_page, {"key": LinksDesk.fund}))
        self.assertIn('"/funds/"+', joined, "a state value is joined into the address")
        self.assertIn("fund_rx_state_", joined)
        self.assertIn("count_rx_state_", str(links.href(CTX, fund_page, {"key": LinksDesk.count})),
                      "a number from the state is accepted")
        with self.assertRaises(PageError) as wrong:
            links.href(CTX, fund_page, {"tab": "x", "page": 2})
        self.assertEqual(str(wrong.exception),
                         "Page `home`: a link to page `fund_page` (/funds/[key]) is missing key and does not accept "
                         "page.\nPage `fund_page` takes: key, tab.", "the error names both pages and what the target takes")
        with self.assertRaises(PageError) as not_a_page:
            links.href(CTX, lambda: None, {})
        self.assertIn("Pass a function declared with `@ui.page`", str(not_a_page.exception))
        self.assertEqual(links.row_href(CTX, fund_page, "id"), "/funds/{id}", "a row link fills the page's one argument")
        with self.assertRaises(PageError, msg="a page without exactly one argument cannot be opened from a row"):
            links.row_href(CTX, funds_page, "id")

    # -- 1.792 ---------------------------------------------------------
    def test_1_792_a_link_leads_to_a_page_a_record_or_an_outside_site(self) -> None:
        """
        Scenario 1.792: ui.link.
        Given: links with to=, record= and href=.
        When:  they are declared and built, in text and among a page's actions.
        Then:  to= stays in the app; record= opens lex-app's own page of that record; href=
               takes only a full outside address and opens it as external; exactly one of the
               three is required; among actions the same link is drawn as a button.
        """
        inside = str(vocab.link("Open", to=fund_page, key="7").build(CTX))
        self.assertIn('"/funds/7"', inside, "to= builds the page's address")
        self.assertIn('rfdsI18n.t("Open", "Open"', inside, "the label is a sentence")
        record = vocab.link("Open in lex-app", record=(Group, 5))
        where, outside = record.target(CTX)
        self.assertTrue(str(where).endswith("/group/5/show") and outside, f"record= leads to lex-app's record page: {where}")
        docs = vocab.link("Docs", href="https://example.org/docs")
        self.assertEqual(docs.target(CTX), ("https://example.org/docs", True), "href= is an outside site")
        for bad in ({}, {"to": fund_page, "href": "https://example.org"}):
            with self.subTest(given=sorted(bad)), self.assertRaises(ValueError, msg="exactly one target"):
                vocab.link("X", **bad)
        with self.assertRaises(ValueError, msg="href= is not for in-app paths"):
            vocab.link("X", href="/funds")
        as_action = str(vocab.link("All funds", to=funds_page).build(CTX.at("actions", "actions")))
        self.assertIn("rfds-button", as_action, "among actions a link is drawn as a button")
        self.assertNotIn("rfds-button", inside, "and in text as a link")

    # -- 1.793 ---------------------------------------------------------
    def test_1_793_a_button_runs_a_handler_or_opens_a_page(self) -> None:
        """
        Scenario 1.793: ui.button.
        Given: buttons with a handler, with to=, with both and with neither; a danger tone;
               and an action drawn as an entry of the page header's menu.
        When:  they are declared and built.
        Then:  a handler button calls the handler on click; a to= button goes to the page's
               address; both or neither is refused; tone="danger" gives RFDS's danger intent;
               the first action of a page is the primary one; a menu entry carries the
               translated sentence and the same click.
        """
        run = vocab.button("Close fund", LinksDesk.close)
        built = run.build(CTX)
        self.assertIn('rfdsI18n.t("Close fund", "Close fund"', str(built))
        self.assertIs(run.click(CTX), LinksDesk.close, "a handler button calls the handler")
        go = vocab.button("New fund", to=fund_page, key="new")
        self.assertIn("/funds/new", str(go.click(CTX)), "a to= button goes to the page")
        for bad in ((LinksDesk.close, {"to": funds_page}), (None, {})):
            with self.subTest(bad=str(bad)[:30]), self.assertRaises(ValueError, msg="a handler or to=, one of them"):
                vocab.button("X", bad[0], **bad[1])
        self.assertIn("danger", str(vocab.button("Delete", LinksDesk.close, tone="danger").build(CTX)), "danger intent")
        first = vocab.button("Save", LinksDesk.close)
        first.primary = True
        self.assertIn("primary", str(first.build(CTX)), "the kind marks the first action primary")
        entry = str(vocab.menu_entry(CTX, "Close fund", run.click(CTX), None))
        self.assertIn('rfdsI18n.t("Close fund", "Close fund"', entry, "a menu entry is translated too")
