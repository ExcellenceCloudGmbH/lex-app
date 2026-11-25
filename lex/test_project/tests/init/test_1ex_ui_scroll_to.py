"""lex.ui: a handler brings a section or a table into view -- ui.scroll_to.

Intent: after a check finds problems, or a long form is sent, the user should see the part that
answers -- the section listing the errors, the table of new lines -- without scrolling to find it.
A handler names the part as the page names it: a section by its title, a table by its `@ui.rows`
handler; lex-app gives those parts their element ids and turns the result into Reflex's scroll
event. Before, only `rx.scroll_to("<dom id>")` in a `@ui.custom` handler could, with a DOM id the
author had to guess. A regression is a scroll that goes nowhere because the id the result names is
not the id the part was built with.

Cluster 1ex -- scenarios 1.1034, 1.1120. Type: U.
Covers: lex/lex_app/reflex/ui/results.py (scroll_to, ScrollTo, split, to_events),
lex/lex_app/reflex/ui/pages/kinds.py (section_anchor, table_anchor, a titled section's id),
lex/lex_app/reflex/ui/pages/tables.py (a rows table's id), lex/ui.py.
Run: python -m lex pytest lex/test_project/tests/init/test_1ex_ui_scroll_to.py -v
"""

from __future__ import annotations

import pytest
from django.test import SimpleTestCase
from reflex.compiler.compiler import compile_page

from lex import ui
from lex.lex_app.reflex.ui import results
from lex.lex_app.reflex.ui.pages.parts import Build

pytestmark = pytest.mark.init


class CheckState(ui.State):
    """A check of an order file: its errors, and the lines it read."""

    @ui.action
    def check(self, values):
        return ui.scroll_to("Errors found")

    @ui.action
    def show_lines(self):
        return [ui.toast("Read"), ui.scroll_to(CheckState.lines)]

    @ui.rows
    def lines(self):
        return [{"id": 1, "name": "Line one"}]


CTX = Build(page="check", route="/check")


class TestCluster01ex_ScrollTo(SimpleTestCase):
    """1.1034: a section or a table into view."""

    # -- 1.1034 --------------------------------------------------------
    def test_1_1034_a_handler_brings_a_section_or_a_table_into_view(self) -> None:
        """
        Scenario 1.1034: return ui.scroll_to("Errors found") / ui.scroll_to(CheckState.lines).
        Given: a form whose result has a section "Errors found" and a table over CheckState.lines; a
               record page with a section of the same title.
        When:  the page is built and compiled; each handler runs; its result becomes Reflex events.
        Then:  the section's element id and the table's are the ids the results name, on a form's
               result and on a record page alike; the events are Reflex's scroll_to of those ids,
               after a toast in order; anything but a title or a rows handler is refused with what
               to write.
        """
        form = ui.form(title="Check orders", submit=CheckState.check, fields=[ui.field("name")], result=[
            ui.section("Errors found", ui.text("None.")),
            ui.section("Lines", ui.table(CheckState.lines, columns=["name"]))])
        _, module = compile_page("/check", form.build(CTX))
        section_id = results.scroll_to("Errors found").target
        table_id = results.scroll_to(CheckState.lines).target
        self.assertEqual(section_id, "errors-found")
        self.assertIn(f'"{section_id}"', module, "the section carries the id the result names")
        self.assertIn(f'"{table_id}"', module, "the table carries the id the result names")
        record = ui.record(title="Order", sections=[ui.section("Errors found", ui.text("None."))])
        _, record_module = compile_page("/order", record.build(Build(page="order", route="/order")))
        self.assertIn(f'"{section_id}"', record_module, "a record page's section has the same id")

        ran = ui.testing.run(CheckState, "show_lines")
        self.assertEqual([type(one).__name__ for one in ran.results], ["Toast", "ScrollTo"])
        events = results.to_events([results.scroll_to("Errors found"), results.scroll_to(CheckState.lines)])
        self.assertEqual(len(events), 2)
        for event, wanted in zip(events, (section_id, table_id)):
            self.assertIn("scroll", str(event).lower(), "Reflex's scroll event")
            self.assertIn(wanted, str(event), "Reflex scrolls to that id")
        for wrong in ("", 3, CheckState.check):
            with self.subTest(wrong=wrong), self.assertRaises(TypeError) as refused:
                ui.scroll_to(wrong)
            self.assertIn("ui.scroll_to(\"Errors\")", str(refused.exception))


    # -- 1.1120 --------------------------------------------------------
    def test_1_1120_two_sections_of_one_title_have_two_ids(self) -> None:
        """
        Scenario 1.1120: two sections called alike on one page (review 2, minor: one DOM id twice).
        Given: a form's result with two sections titled "Errors found" and one titled "Lines".
        When:  the page is built and compiled.
        Then:  each section has an id of its own -- the second "errors-found-2" -- and ui.scroll_to("Errors
               found") reaches the first, as documented.
        """
        form = ui.form(title="Check", submit=CheckState.check, fields=[ui.field("name")], result=[
            ui.section("Errors found", ui.text("First.")), ui.section("Errors found", ui.text("Second.")),
            ui.section("Lines", ui.text("Lines."))])
        _, module = compile_page("/check", form.build(Build(page="check", route="/check")))
        for ident in ('"errors-found"', '"errors-found-2"', '"lines"'):
            self.assertEqual(module.count(f"id:{ident}"), 1, f"{ident} once")
        self.assertEqual(results.scroll_to("Errors found").target, "errors-found")
