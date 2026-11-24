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


def _CLOSE():
    """A dangerous action; it asks first, as every dangerous action must (1.848)."""
    return vocab.button("Close fund", KindDesk.close, tone="danger", confirm="Close this fund?")


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
        bare = str(_record().build(CTX))
        self.assertNotIn("ui_loaded", bare, "without a load the page is shown at once")
        self.assertIn("name_rx_state_", bare, "the title comes from the state (this page has no other use of it)")
        odd = str(_record(sections=[kinds.section("2024 results", vocab.text("a")),
                                    kinds.section("Übersicht", vocab.text("b")),
                                    kinds.section("概要", vocab.text("c"))]).build(CTX))
        for anchor in ('"#section-2024-results"', '"#ubersicht"', '"#section-3"'):
            with self.subTest(anchor=anchor):
                self.assertIn(anchor, odd, "every title gives a section an id that is a valid anchor")
        gated = _record(sections=[kinds.section("Details", vocab.text("a"), visible=ui.edit(Group))])._sections(CTX)
        self.assertEqual([section.permission for section in gated], ["auth.Group:edit"],
                         "a section's permission is handed to RFDS, which keeps the heading and says why it is empty")
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
        Then:  each is a PageError naming the page and the slot and saying what to write --
               also two sections with one title, a section hidden by a state value, a blank
               title, a title that is not words, two badges, and an action called Save (one
               fact or one section given without brackets is a list of one); a limit of RFDS's
               own (five tables on one page) names the page and quotes RFDS's sentence.
        """
        cases = {
            "facts": (dict(facts=[rx.text("raw")]),
                      "Page `fund`, slot `facts`: got a raw component (`Text`).\n"
                      "Use `ui.fact(label, value)`."),
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
        cases["two badges"] = (dict(status=[vocab.status("Open"), vocab.status("Late")]),
                               "Page `fund`, slot `status`: got 2 badges.\n`status=` holds one `ui.status(word, tone=)`.")
        cases["same title"] = (dict(sections=[kinds.section("Notes", vocab.text("a")), kinds.section("Notes", vocab.text("b"))]),
                               "Page `fund`, slot `sections`: two sections are both called `Notes`.\n"
                               "Give each section its own title.")
        cases["section by state"] = (
            dict(sections=[kinds.section("Notes", vocab.text("a"), visible=KindDesk.name != "")]),
            "Page `fund`, slot `sections[0]`: `visible=` on a section of a record page takes a permission.\n"
            "A section's heading always shows. To hide the section from some users write `visible=ui.read(Model)`; "
            "to show different content by state, put `ui.switch(...)` inside the section, or give the record "
            "`tabs=True`, where a state value hides a tab.")
        cases["blank title"] = (dict(title="   "), "Page `fund`: the page has no title.\n"
                                                    "Give the kind `title=\"...\"`, or a state value.")
        cases["title not words"] = (dict(title=5), "Page `fund`: the page's title is `5`.\n"
                                                    "Give the kind `title=\"...\"`, or a state value.")
        cases["save"] = (dict(actions=[vocab.button("Save changes", KindDesk.close)]),
                         "Page `fund`, slot `actions`: an action is called `Save changes`.\n"
                         "RFDS shows a last-saved time beside a Save action, which a page header cannot give yet. "
                         "Name the action for what it does (`\"Apply changes\"`); saving belongs to a form.")
        for name, (slots, message) in cases.items():
            with self.subTest(case=name), self.assertRaises(PageError) as raised:
                _record(**slots).build(CTX)
            self.assertEqual(str(raised.exception), message, f"{name}: the message says what to write")
        one = str(kinds.record(title="Fund", facts=vocab.fact("Vintage", "2020"),
                               sections=kinds.section("Notes", vocab.text("a"))).build(CTX))
        self.assertIn('rfdsI18n.t("Vintage", "Vintage"', one, "one fact without brackets is taken as a list of one")
        crowded = [kinds.section(f"Table {n}", tables.table(Group, columns=["name"])) for n in "abcde"]
        with self.assertRaises(PageError) as limit:
            _record(sections=crowded).build(CTX)
        self.assertTrue(str(limit.exception).startswith("Page `fund`: RFDS's page layout refuses this page: rfds."),
                        f"a limit of RFDS's own names the page and quotes RFDS: {limit.exception}")

    # -- 1.801 ---------------------------------------------------------
    def test_1_801_actions_are_placed_as_the_page_header_places_them(self) -> None:
        """
        Scenario 1.801: actions=.
        Given: record pages with one action, with two, with three of which one is dangerous,
               and with a dangerous action first.
        When:  they are built.
        Then:  the first action is the primary button -- unless it is dangerous, which is never
               primary; exactly one further action is a button; two or more further actions
               are entries of the "More actions" menu, with their translated sentences and
               their tone; a primary action with visible= stays primary; the author's part is
               not changed; something that is not a button or a link is refused.
        """
        one = str(_record(actions=[vocab.button("Edit", KindDesk.close)]).build(CTX))
        self.assertIn('"data-rfds-intent":"primary"', one, "the first action is primary")
        two = str(_record(actions=[vocab.button("Edit", KindDesk.close),
                                   _CLOSE()]).build(CTX))
        self.assertIn('"data-rfds-intent":"danger"', two, "one further action stays a button")
        self.assertNotIn("rfds-menu-item-label", two, "and is not put in a menu")
        three = str(_record(actions=[vocab.button("Edit", KindDesk.close),
                                     _CLOSE(),
                                     vocab.link("All groups", to=group_page, key="1")]).build(CTX))
        self.assertIn('"rfds-menu-item-label"},rfdsI18n.t("Close fund", "Close fund"', three,
                      "two or more further actions are menu entries, translated")
        self.assertIn('"rfds-menu-item-label"},rfdsI18n.t("All groups", "All groups"', three, "the link is an entry too")
        self.assertIn('"data-rfds-tone":"danger"', three, "a dangerous action keeps its tone in the menu")
        calm = str(_record(actions=[vocab.button("Edit", KindDesk.close), vocab.button("Close fund", KindDesk.close),
                                    vocab.link("All groups", to=group_page, key="1")]).build(CTX))
        self.assertNotIn('"data-rfds-tone":"danger"', calm, "and only a dangerous one has it")
        edit = vocab.button("Edit", KindDesk.close, visible=ui.edit(Group))
        gated = str(_record(actions=[edit, _CLOSE()]).build(CTX))
        self.assertIn('"data-rfds-intent":"primary"', gated, "a primary action with visible= stays primary")
        self.assertIn('"data-rfds-intent":"danger"', gated, "and the action beside it is still built")
        self.assertFalse(edit.primary, "the kind marks a copy primary, never the author's part")
        danger_first = str(_record(actions=[_CLOSE()]).build(CTX))
        self.assertNotIn('"data-rfds-intent":"primary"', danger_first, "a dangerous action is never the primary one")
        with self.assertRaises(PageError) as wrong:
            _record(actions=[vocab.text("loose")]).build(CTX)
        self.assertIn("slot `actions`: got `ui.text`", str(wrong.exception), "what is not an action is named")

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
                         "Page `groups`, slot `table`: got `ui.text`.\n"
                         "`table=` holds one `ui.table(Model, columns=[...])`.", "a wrong table is named by its ui name")
        with self.assertRaises(PageError) as no_table:
            kinds.list_(title="Groups").build(Build(page="groups", route="/groups"))
        self.assertIn("got nothing (`None`).", str(no_table.exception), "a list without a table says so")
