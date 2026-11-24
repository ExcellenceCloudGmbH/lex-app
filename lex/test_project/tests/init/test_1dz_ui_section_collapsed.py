"""lex.ui pages: a section that is closed until the user opens it, saying how many rows it holds.

Intent: a tool's result often has a main table and a detail most users skip -- the fields a file
was read with, the lines that were left out. That detail is a section closed until the user opens
it (RFDS's section as a native disclosure: no round trip to open or close it), and its title says
how much is inside, "Fields read (12)", so the user knows whether opening it is worth it. A
regression is a detail that shows open and buries the result, a count that is wrong or shows
before the rows are read, or a closed section on a page where RFDS cannot close one.

Cluster 1dz -- scenarios 1.947-1.949. Type: U.
Covers: lex/lex_app/reflex/ui/pages/kinds.py (section collapsed=, Section.titled, the record's
refusal), lex/lex_app/reflex/ui/pages/forms.py (a form's result= sections),
lex/lex_app/reflex/ui/pages/declare.py (ui.embed of a closed section).
Run: python -m lex pytest lex/test_project/tests/init/test_1dz_ui_section_collapsed.py -v
"""

from __future__ import annotations

import pytest
from django.contrib.auth.models import Group
from django.test import SimpleTestCase
from reflex.compiler.compiler import compile_page

from lex import ui
from lex.lex_app.reflex.ui.pages.parts import Build, PageError

pytestmark = pytest.mark.init


class ImportState(ui.State):
    """A tool that reads a file of orders: the orders it made, and the fields it read."""

    @ui.action
    def read_file(self, values):
        return None

    @ui.rows
    def orders(self):
        return [{"id": 1, "name": "Order one"}]

    @ui.rows
    def fields_read(self):
        return [{"id": n, "name": f"Field {n}"} for n in range(12)]


CTX = Build(page="import_orders", route="/orders/import")


def tool(*result):
    return ui.form(title="Import orders", submit=ImportState.read_file, fields=[ui.field("name")], result=list(result))


class TestCluster01dz_SectionCollapsed(SimpleTestCase):
    """1.947-1.949: a closed section, its count, and where it cannot be."""

    # -- 1.947 ---------------------------------------------------------
    def test_1_947_a_closed_section_opens_in_the_browser_and_says_how_many_rows_it_holds(self) -> None:
        """
        Scenario 1.947: ui.section(..., collapsed=True) in a form's result=.
        Given: a tool whose result is the orders table, open, and a closed section of the
               fields read; a closed section holding only text; a closed section over a model.
        When:  the page is built.
        Then:  the closed section is RFDS's collapsible section, closed at first, opened in the
               browser without a round trip; its title is the section's title and, once its
               table has read its rows, the table's row count, through RFDS's translation; before
               that, the title alone; the open section is as before, with no disclosure; a closed
               section without a table has its title alone; a model table's count works the same.
        """
        text = str(tool(ui.section("Orders", ui.table(ImportState.orders, columns=["name"])),
                        ui.section("Fields read", ui.table(ImportState.fields_read, columns=["name"]),
                                   collapsed=True)).build(CTX))
        self.assertEqual(text.count('"data-rfds-collapsible":"true"'), 1, "only the closed section is a disclosure")
        self.assertIn('jsx("details"', text, "RFDS's section as a native disclosure")
        self.assertIn("open:false", text, "closed at first")
        self.assertIn("{title} ({count, number})", text, "the title and the count, translated by RFDS")
        self.assertIn(".loaded_at", text, "the count shows once the table has read its rows")
        self.assertIn(".total_rows", text, "it is the table's row count")

        plain = str(tool(ui.section("Notes", ui.text("Read with the default settings."), collapsed=True)).build(CTX))
        self.assertIn('"data-rfds-collapsible":"true"', plain, "a closed section without a table")
        self.assertNotIn("{count, number}", plain, "has its title alone")

        model = str(tool(ui.section("Groups", ui.table(Group, columns=["name"]), collapsed=True)).build(CTX))
        self.assertIn("{title} ({count, number})", model, "a model table's count the same way")

        embedded = str(ui.embed(ui.section("Fields read", ui.table(ImportState.fields_read, columns=["name"]),
                                           collapsed=True), name="Fields read"))
        self.assertIn('"data-rfds-collapsible":"true"', embedded, "a closed section embedded in a raw page too")

    # -- 1.948 ---------------------------------------------------------
    def test_1_948_a_closed_section_compiles_to_a_real_page(self) -> None:
        """
        Scenario 1.948: compiled with Reflex's page compiler.
        Given: the tool of 1.947.
        When:  Reflex compiles it.
        Then:  the module is built with the disclosure and the counting title.
        """
        page = tool(ui.section("Fields read", ui.table(ImportState.fields_read, columns=["name"]), collapsed=True))
        _, module = compile_page("/orders/import", page.build(CTX))
        self.assertIn('"data-rfds-collapsible":"true"', module, "compiled: the disclosure")
        self.assertIn("{title} ({count, number})", module, "compiled: the counting title")

    # -- 1.949 ---------------------------------------------------------
    def test_1_949_a_closed_section_where_rfds_cannot_close_one_is_refused(self) -> None:
        """
        Scenario 1.949: collapsed= refused.
        Given: a closed section on a record page, and collapsed= that is not True or False.
        When:  the page is built, or the section declared.
        Then:  the record page says its sections are always open, as RFDS lists them under "On
               this page", and how to show one at a time; the wrong value says what to write.
        """
        page = ui.record(title="Order", sections=[ui.section("Lines", ui.text("x"), collapsed=True)])
        with self.assertRaises(PageError) as raised:
            page.build(Build(page="order", route="/orders/[key]"))
        self.assertIn("Page `order`, slot `sections[0]`: the section `Lines` is collapsed, and a record page's "
                      "sections are always open.", str(raised.exception))
        self.assertIn("RFDS lists them under \"On this page\". To show one section at a time, give the record "
                      "`tabs=True`.", str(raised.exception))
        with self.assertRaisesRegex(TypeError, r"ui.section\(collapsed=\) takes True or False"):
            ui.section("Lines", ui.text("x"), collapsed="yes")
