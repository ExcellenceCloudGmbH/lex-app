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
    tags: list[str] = []

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
        self.assertIn("fund_rx_state_", joined, "the state value is in the address")
        self.assertIn("encodeURIComponent(String(", joined, "and the browser encodes it, so it stays one piece")
        self.assertIn("?? ''", str(links.href(CTX, fund_page, {"key": LinksDesk.count})),
                      "a 0 from the state stays a 0 in the address (only a missing value is empty)")
        self.assertEqual(links.href(CTX, fund_page, {"key": "a/b", "tab": "x&y=1"}), "/funds/a%2Fb?tab=x%26y%3D1",
                         "a fixed value is encoded: it cannot change the page or add a parameter")
        for bad in (None, True, "", ".."):
            with self.subTest(value=bad), self.assertRaises(PageError) as refused:
                links.href(CTX, fund_page, {"key": bad})
            self.assertEqual(str(refused.exception),
                             f"Page `home`: the value for `key` in a link is {bad!r}.\n"
                             "Pass text or a number: `key=row.id`, `key=7`.", "a value that cannot be an address piece is refused")
        with self.assertRaises(PageError, msg="a list from the state cannot be an address piece"):
            links.href(CTX, fund_page, {"key": LinksDesk.tags})
        for route in ("/docs/[[...slug]]", "/docs/[...slug]", "/posts/[[v]]"):
            with self.subTest(route=route), self.assertRaises(ValueError, msg="only plain route arguments"):
                links.info_of(route, "docs")
        with self.assertRaises(ValueError) as reserved:
            links.info_of("/funds/[to]", "fund", ["label"])
        self.assertIn("cannot be named to, label", str(reserved.exception), "a parameter named like a link's own word is refused")
        self.assertIn("count_rx_state_", str(links.href(CTX, fund_page, {"key": LinksDesk.count})),
                      "a number from the state is accepted")
        with self.assertRaises(PageError) as wrong:
            links.href(CTX, fund_page, {"tab": "x", "page": 2})
        self.assertEqual(str(wrong.exception),
                         "Page `home`: a link to page `fund_page` (/funds/[key]) is missing key and does not accept "
                         "page.\nPage `fund_page` takes: key, tab. Write `to=fund_page, key=...`.",
                         "the error names both pages, what the target takes and what to write")
        with self.assertRaises(PageError) as not_a_page:
            links.href(CTX, lambda: None, {})
        self.assertIn("Pass a function declared with `@ui.page`", str(not_a_page.exception), "what to pass instead")
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
        self.assertTrue(str(where).endswith("/group/5/show"), f"record= leads to lex-app's record page: {where}")
        self.assertTrue(outside, "which opens outside the dashboards")
        self.assertIn("_blank", str(record.build(CTX)), "an outside link opens a new tab")
        self.assertNotIn("_blank", inside, "a link to a page stays in the app")
        for bad_record in (Group, (Group,), (Group, None), ("group", 5)):
            with self.subTest(record=str(bad_record)[:20]), self.assertRaises(ValueError, msg="record= takes (Model, pk)"):
                vocab.link("X", record=bad_record)
        with self.assertRaises(ValueError, msg="parameters belong to to="):
            vocab.link("Docs", href="https://example.org", key="7")
        with self.assertRaises(ValueError) as mail:
            vocab.link("Mail", href="mailto:a@example.org")
        self.assertIn("Mail and phone links are not supported yet", str(mail.exception), "the refusal says so")
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
        self.assertIn('rfdsI18n.t("Close fund", "Close fund"', str(built), "the label is a sentence")
        self.assertNotIn('"data-rfds-intent":"primary"', str(built), "a button is not primary unless the kind says so")
        self.assertIs(run.click(CTX), LinksDesk.close, "a handler button calls the handler")
        go = vocab.button("New fund", to=fund_page, key="new")
        self.assertIn("/funds/new", str(go.click(CTX)), "a to= button goes to the page")
        for bad in ((LinksDesk.close, {"to": funds_page}), (None, {})):
            with self.subTest(bad=str(bad)[:30]), self.assertRaises(ValueError, msg="a handler or to=, one of them"):
                vocab.button("X", bad[0], **bad[1])
        danger = vocab.button("Delete", LinksDesk.close, tone="danger", confirm="Delete this fund?")
        self.assertIn('"data-rfds-intent":"danger"', str(danger.build(CTX)), "danger intent")
        first = vocab.button("Save", LinksDesk.close)
        first.primary = True
        self.assertIn('"data-rfds-intent":"primary"', str(first.build(CTX)), "the kind marks the first action primary")
        with self.assertRaises(ValueError, msg="parameters belong to to="):
            vocab.button("X", LinksDesk.close, key="7")
        with self.assertRaises(PageError, msg="a to= button checks the page's parameters like a link"):
            vocab.button("New", to=fund_page).click(CTX)
        from rfds.components.actions._common import get_meta

        with self.assertRaises(PageError) as no_words:
            vocab.button(3, LinksDesk.close).menu_entry(CTX.at("actions", "actions"))
        self.assertEqual(str(no_words.exception),
                         "Page `home`, slot `actions`: an action's label is `3`.\n"
                         "Write the words: `ui.button(\"Close fund\", ...)`.", "a menu entry without words names its page")
        entry = run.menu_entry(CTX)
        self.assertIn('rfdsI18n.t("Close fund", "Close fund"', str(entry), "a menu entry is translated too")
        self.assertEqual(get_meta(entry).get("kind"), "menu_item", "RFDS recognises it as a menu entry")
        hidden = vocab.button("Close fund", LinksDesk.close, visible=LinksDesk.fund != "").menu_entry(CTX)
        self.assertEqual(get_meta(hidden).get("kind"), "menu_item", "and still does when visible= wraps it")
        self.assertIn("fund_rx_state_", str(hidden), "the wrapper carries the condition")
        self.assertNotEqual(str(danger.menu_entry(CTX)).replace("Delete", "Close fund"), str(entry),
                            "a dangerous action keeps its tone in the menu")
