"""lex.ui pages: the record and list kinds place their slots; the author only fills them.

Intent: a page kind fixes where everything sits, on RFDS's own floorplan for that shape -- the
object page for one record, the list report for finding things -- so pages of one kind look
alike and nobody writes layout. The kind also shows a skeleton until the page's load has
finished, places the actions as RFDS's page header does (one primary button; one more as a
button, more in a "More actions" menu), and refuses what does not belong in a slot. A regression
is a page that renders without its data, a slot that accepts anything, or actions in the wrong
place.

Cluster 1cs -- scenarios 1.799-1.802. Type: U.
Covers: lex/lex_app/reflex/ui/pages/kinds.py (record, list_, Kind).
Run: python -m lex pytest lex/test_project/tests/init/test_1cs_ui_page_kinds.py -v
"""

from __future__ import annotations

import pytest
import reflex as rx
from django.contrib.auth.models import Group
from django.test import SimpleTestCase

from lex import ui
from lex.lex_app.reflex.ui.pages import kinds, links, tables, vocab
from lex.lex_app.reflex.ui.pages.parts import Build, PageError

pytestmark = pytest.mark.init


class KindDesk(ui.State):
    name: str = ""
    amount: float = 0.0

    @ui.load
    def open(self):
        self.name = "Fund One"

    @ui.load
    def load_documents(self):
        self.name = self.name

    @ui.action
    def close(self):
        return ui.toast("Closed")


def group_page():
    """Stands for a page declared with @ui.page("/groups/[key]")."""


group_page.__ui_page__ = links.info_of("/groups/[key]", "group_page")

CTX = Build(page="fund", route="/funds/[key]")


def _record(**slots):
    slots.setdefault("title", KindDesk.name)
    slots.setdefault("sections", [kinds.section("Details", vocab.text("Hello"))])
    return kinds.record(**slots)


class TestCluster01cs_PageKinds(SimpleTestCase):
    """1.799-1.802: the record page; its errors; where actions go; the list page."""

    # -- 1.799 ---------------------------------------------------------
    def test_1_799_a_record_page_places_status_facts_sections_and_waits_for_its_load(self) -> None:
        """
        Scenario 1.799: ui.record.
        Given: a record with a title from the state, a load, a status, two facts and two
               sections, one of which loads on its own.
        When:  it is built for a page.
        Then:  it is RFDS's object page: the title in the page header, the status and the facts
               in the header area, each section under its translated heading; the whole page
               sits behind the load's loaded check, with a skeleton and the failure in place
               before it; the kind reports its page load and its sections' loads separately;
               tabs=True with two sections uses RFDS's tab navigation.
        """
        page = _record(load=KindDesk.open, status=vocab.status("Open", tone="info"),
                       facts=[vocab.fact("Net asset value", KindDesk.amount, kind="money"),
                              vocab.fact("Name", KindDesk.name)],
                       sections=[kinds.section("Holdings", vocab.text("Hello")),
                                 kinds.section("Documents", vocab.text("Later"), load=KindDesk.load_documents)])
        text = str(page.build(CTX))
        for needle, why in (("rfds-page-header", "RFDS's page header"), ("rfds-key-value", "the facts"),
                            ('rfdsI18n.t("Net asset value"', "a fact's label"), ("rfds-lozenge", "the status"),
                            ('rfdsI18n.t("Holdings", "Holdings"', "a section heading"),
                            ('rfdsI18n.t("Documents", "Documents"', "the second section heading"),
                            ("name_rx_state_", "the title from the state")):
            with self.subTest(needle=needle):
                self.assertIn(needle, text, why)
        self.assertLess(text.index('ui_loaded_rx_state_?.["KindDesk.open"]'), text.index("rfds-page-header"),
                        "the page sits behind its load's loaded check")
        self.assertIn("rfds-skeleton", text, "a skeleton before the load has finished")
        self.assertEqual(page.loads(), ([KindDesk.open], [KindDesk.load_documents]),
                         "the page load and the sections' loads are reported separately")
        self.assertNotIn("ui_loaded", str(_record().build(CTX)), "without a load the page is shown at once")
        tabbed = str(_record(tabs=True, sections=[kinds.section("A", vocab.text("a")),
                                                   kinds.section("B", vocab.text("b"))]).build(CTX))
        self.assertIn("rfds-tabs", tabbed, "tabs=True shows one section at a time")

    # -- 1.800 ---------------------------------------------------------
    def test_1_800_a_record_page_refuses_what_does_not_belong(self) -> None:
        """
        Scenario 1.800: the record page's checks.
        Given: record pages with a raw component among the facts, no section, no title, a
               status that is not a ui.status, and a section slot holding a text.
        When:  each is built.
        Then:  each is a PageError naming the page and the slot and saying what to write.
        """
        cases = {
            "facts": (dict(facts=[rx.text("raw")]),
                      "Page `fund`, slot `facts`: got a raw component (`Text`).\n"
                      "Use `ui.fact(label, value)`, or wrap it in `ui.custom_block(..., reason=\"...\")`."),
            "no section": (dict(sections=[]),
                           "Page `fund`, slot `sections`: a record page needs at least one section.\n"
                           "Add `sections=[ui.section(\"Details\", ...)]`."),
            "no title": (dict(title=""), "Page `fund`: the page has no title.\n"
                                         "Give the kind `title=\"...\"`, or a state value."),
            "status": (dict(status=vocab.text("Open")),
                       "Page `fund`, slot `status`: got `ui.text`.\n`status=` holds one `ui.status(word, tone=)`."),
            "sections": (dict(sections=[vocab.text("loose")]),
                         "Page `fund`, slot `sections`: got `ui.text`.\n`sections=` holds `ui.section(title, ...)`."),
        }
        for name, (slots, message) in cases.items():
            with self.subTest(case=name), self.assertRaises(PageError) as raised:
                _record(**slots).build(CTX)
            self.assertEqual(str(raised.exception), message, f"{name}: the message says what to write")

    # -- 1.801 ---------------------------------------------------------
    def test_1_801_actions_are_placed_as_the_page_header_places_them(self) -> None:
        """
        Scenario 1.801: actions=.
        Given: record pages with one action, with two, with three of which one is dangerous,
               and with a dangerous action first.
        When:  they are built.
        Then:  the first action is the primary button -- unless it is dangerous, which is never
               primary; exactly one further action is a button; two or more further actions
               are entries of the "More actions" menu, with their translated sentences;
               something that is not a button or a link is refused.
        """
        one = str(_record(actions=[vocab.button("Edit", KindDesk.close)]).build(CTX))
        self.assertIn('"data-rfds-intent":"primary"', one, "the first action is primary")
        two = str(_record(actions=[vocab.button("Edit", KindDesk.close),
                                   vocab.button("Close fund", KindDesk.close, tone="danger")]).build(CTX))
        self.assertIn('"data-rfds-intent":"danger"', two, "one further action stays a button")
        self.assertNotIn("rfds-menu-item-label", two, "and is not put in a menu")
        three = str(_record(actions=[vocab.button("Edit", KindDesk.close),
                                     vocab.button("Close fund", KindDesk.close, tone="danger"),
                                     vocab.link("All groups", to=group_page, key="1")]).build(CTX))
        self.assertIn('"rfds-menu-item-label"},rfdsI18n.t("Close fund", "Close fund"', three,
                      "two or more further actions are menu entries, translated")
        self.assertIn('"rfds-menu-item-label"},rfdsI18n.t("All groups", "All groups"', three, "the link is an entry too")
        calm = str(_record(actions=[vocab.button("Edit", KindDesk.close), vocab.button("Close fund", KindDesk.close),
                                    vocab.link("All groups", to=group_page, key="1")]).build(CTX))
        self.assertNotEqual(calm, three, "a dangerous action keeps its tone in the menu")
        danger_first = str(_record(actions=[vocab.button("Close fund", KindDesk.close, tone="danger")]).build(CTX))
        self.assertNotIn('"data-rfds-intent":"primary"', danger_first, "a dangerous action is never the primary one")
        with self.assertRaises(PageError) as wrong:
            _record(actions=[vocab.text("loose")]).build(CTX)
        self.assertIn("slot `actions`: got `ui.text`", str(wrong.exception))

    # -- 1.802 ---------------------------------------------------------
    def test_1_802_a_list_page_is_a_title_a_table_and_its_actions(self) -> None:
        """
        Scenario 1.802: ui.list.
        Given: a list with a fixed title, a table over a model and one action that opens a page.
        When:  it is built.
        Then:  it is RFDS's list report with the translated title, the table and the primary
               action; a table slot holding anything but a ui.table is refused.
        """
        page = kinds.list_(title="Groups", table=tables.table(Group, columns=["name"], open=group_page),
                           actions=[vocab.button("First group", to=group_page, key="1")])
        text = str(page.build(Build(page="groups", route="/groups")))
        self.assertIn('rfdsI18n.t("Groups", "Groups"', text, "the title is a sentence")
        self.assertIn("rfds-data-table", text, "the table")
        self.assertIn('"data-rfds-intent":"primary"', text, "the action")
        self.assertEqual(page.loads(), ([], []), "a list without a load has none")
        with self.assertRaises(PageError) as wrong:
            kinds.list_(title="Groups", table=vocab.text("loose")).build(Build(page="groups", route="/groups"))
        self.assertEqual(str(wrong.exception),
                         "Page `groups`, slot `table`: got Text.\n`table=` holds one `ui.table(Model, columns=[...])`.")
