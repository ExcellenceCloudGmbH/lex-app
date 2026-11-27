"""lex.ui pages: a tool's result as tabs -- one section at a time, as on a record page.

Intent: a tool's result often has two or more views of one outcome: a table per region, or the
figures and a chart. Side by side they push each other off the screen; as tabs the user picks one.
A record page already shows its sections one at a time with `tabs=True`; a form takes the same
argument for its `result=`, each section's title its tab (RFDS's tabs, switched in the browser).
A regression is a second way to write tabs, a tab whose content is lost, a section a user may not
see shown as a tab anyway, or a mistake that only shows in the browser.

Cluster 1ea -- scenarios 1.950-1.952. Type: U.
Covers: lex/lex_app/reflex/ui/pages/forms.py (form(tabs=), Form._result, Form._tabs),
lex/lex_app/reflex/ui/pages/kinds.py (the record's tabs=, unchanged).
Run: python -m lex pytest lex/test_project/tests/init/test_1ea_ui_form_result_tabs.py -v
"""

from __future__ import annotations

import importlib
from unittest.mock import patch

import pytest
from django.contrib.auth.models import Group
from django.test import SimpleTestCase
from reflex.compiler.compiler import compile_page

from lex import ui
from lex.lex_app.reflex.access import permission
from lex.lex_app.reflex.ui.pages.parts import Build, PageError

pytestmark = pytest.mark.init

#: RFDS's navigation module, whose permission checker decides which tabs are drawn.
side_nav = importlib.import_module("rfds.components.navigation.side_nav")


class RegionReportState(ui.State):
    """A tool that reports orders per region."""

    done_note: str = "Built from 3 files."

    @ui.action
    def build_report(self, values):
        return None

    @ui.rows
    def north(self):
        return [{"id": 1, "name": "Order one"}]

    @ui.rows
    def south(self):
        return [{"id": 2, "name": "Order two"}]

    @ui.load
    def load_south(self):
        return None


CTX = Build(page="region_report", route="/orders/regions")


def report(*result, tabs=True):
    return ui.form(title="Region report", submit=RegionReportState.build_report, fields=[ui.field("year", kind="year")],
                   result=list(result), tabs=tabs)


def north():
    return ui.section("North", ui.table(RegionReportState.north, columns=["name"]))


def south(**more):
    return ui.section("South", ui.table(RegionReportState.south, columns=["name"]), **more)


class TestCluster01ea_FormResultTabs(SimpleTestCase):
    """1.950-1.952: a form's result as tabs; compiled; mistakes."""

    # -- 1.950 ---------------------------------------------------------
    def test_1_950_the_results_sections_are_tabs_each_title_its_tab(self) -> None:
        """
        Scenario 1.950: ui.form(..., tabs=True).
        Given: a tool whose result is a sentence every view shares, then a section per region.
        When:  it is built, with tabs=True and without.
        Then:  with tabs, the sections are RFDS's tabs -- each title a tab, each table in its tab,
               switched in the browser -- the shared sentence above them, and the section titles
               are not repeated as headings; without, the sections are below one another as
               before; a record page's tabs=True is the same argument, unchanged.
        """
        tabbed = str(report(ui.text(RegionReportState.done_note), north(), south()).build(CTX))
        self.assertIn("rfds-tabs", tabbed, "RFDS's tabs component")
        for title in ("North", "South"):
            self.assertIn(f'"{title}"', tabbed, f"the tab {title} is labelled by its section's title")
        self.assertNotIn("rfds-section-title", tabbed, "a tab's title is not repeated as a heading")
        self.assertLess(tabbed.index("done_note"), tabbed.index("rfds-tabs"), "what every view shares is above")

        stacked = str(report(ui.text(RegionReportState.done_note), north(), south(), tabs=False).build(CTX))
        self.assertNotIn("rfds-tabs", stacked, "without tabs= the sections are below one another")
        self.assertIn("rfds-section-title", stacked, "each with its heading")
        record = str(ui.record(title="Region", tabs=True, sections=[north(), south()]).build(
            Build(page="region", route="/regions/[key]")))
        self.assertIn("rfds-object-tabs", record, "a record page's tabs=True is unchanged")

    # -- 1.951 ---------------------------------------------------------
    def test_1_951_tabs_compile_and_keep_who_may_see_a_tab(self) -> None:
        """
        Scenario 1.951: compiled, with a permission and a load.
        Given: the tool of 1.950 with one tab shown only to users who may read a model, and one
               section that loads on its own.
        When:  Reflex compiles it, and its loads are listed.
        Then:  the module is built with RFDS's tabs; the permission is the tab's own (RFDS's tab
               permission): where RFDS's permission checker refuses it, neither the tab nor its
               table is drawn, the other tabs are; the section's load is among the page's part loads.
        """
        page = report(north(), ui.section("Groups", ui.table(Group, columns=["name"]), visible=ui.read(Group)),
                      south(load=RegionReportState.load_south))
        _, module = compile_page("/orders/regions", page.build(CTX))
        self.assertIn("rfds-tabs", module, "compiled: RFDS's tabs")
        refused = permission(Group, "read")
        with patch.object(side_nav, "_CHECKER", [lambda needed: needed != refused]):
            denied = str(page.build(CTX))
        self.assertNotIn('"Groups"', denied, "a tab the user may not see is not drawn")
        self.assertIn('"North"', denied, "the others are")
        _, part_loads = page.loads()
        self.assertEqual([load.fn.__name__ for load in part_loads], ["load_south"], "the tab's load is a part load")

    # -- 1.952 ---------------------------------------------------------
    def test_1_952_tabs_written_wrongly_say_what_to_write(self) -> None:
        """
        Scenario 1.952: tabs= refused.
        Given: tabs on a form without a result, with one section, with a part after a section,
               with two sections of one title, with a closed section, with the first tab shown by a
               state value (1.1035: a later tab may be), with visible= neither; and tabs= that is not True or False.
        When:  the page is built, or the form declared.
        Then:  each is refused with the page, the slot and what to write.
        """
        cases = {
            "no result": (ui.form(title="Region report", submit=RegionReportState.build_report,
                                  fields=[ui.field("year", kind="year")], tabs=True),
                          "`tabs=True` shows the result's sections one at a time, and it has 0.\n"
                          "Give `result=` two or more `ui.section(title, ...)`s, one per tab, or leave `tabs=` out."),
            "one section": (report(north()), "`tabs=True` shows the result's sections one at a time, and it has 1."),
            "after a section": (report(north(), ui.text("Done."), south()),
                                "`ui.text` comes after a section, and with `tabs=True` the sections are the tabs.\n"
                                "Put what every tab shares first, then the sections."),
            "same title": (report(north(), north()), "two sections are both called `North`."),
            "closed": (report(north(), south(collapsed=True)),
                       "the section `South` is collapsed, and a tab is opened by its tab.\nLeave `collapsed=` out."),
            "state visible first": (report(south(visible=RegionReportState.done_note != ""), north()),
                                    "`visible=` on the first tab is a state value, and the page opens on the first "
                                    "tab.\nPut a tab that is always there first."),
            "visible not a permission": (report(north(), south(visible="editors")),
                                         "`visible=` on a tab takes a permission or a state value."),
        }
        for name, (page, expected) in cases.items():
            with self.subTest(case=name), self.assertRaises(PageError) as raised:
                page.build(CTX)
            self.assertIn("Page `region_report`, slot `result", str(raised.exception), f"{name}: page and slot")
            self.assertIn(expected, str(raised.exception), name)
        with self.assertRaisesRegex(TypeError, r"ui.form\(tabs=\) takes True or False"):
            report(north(), south(), tabs="yes")
