"""lex.ui pages: a tab shown only while the state says so -- visible= a state value on a tab.

Intent: a result shown as tabs often has a tab that only makes sense for some results -- a chart tab
when the result has figures, an errors tab when there were errors. `visible=` on a section already
decides what is shown; on a tab it took a permission only (RFDS's tab permission). It now also takes
a state value: the tab's trigger and its panel are drawn only while the value is true, on a form's
result and on a record page with `tabs=True`. The page opens on its first tab, so the first tab may
not be hidden by state. A regression is a tab shown with nothing to say, or a hidden tab the user can
still open.

Cluster 1ey -- scenario 1.1035. Type: U.
Covers: lex/lex_app/reflex/ui/pages/kinds.py (hide_tabs, tab_visible, Record's tabs),
lex/lex_app/reflex/ui/pages/forms.py (a form's result tabs).
Run: python -m lex pytest lex/test_project/tests/init/test_1ey_ui_tab_shown_by_state.py -v
"""

from __future__ import annotations

import pytest
from django.test import SimpleTestCase
from reflex.compiler.compiler import compile_page

from lex import ui
from lex.lex_app.reflex.ui.pages.parts import Build, PageError

pytestmark = pytest.mark.init


class ResultState(ui.State):
    """A report whose figures tab makes sense only when it has figures."""

    has_figures: bool = False

    @ui.action
    def build(self, values):
        self.has_figures = True


CTX = Build(page="report", route="/report")


def _hidden_by(node, value: str, under: bool = False) -> list[bool]:
    """For each tab trigger or panel of ``value``: whether a cond on ``has_figures`` holds it."""
    found: list[bool] = []
    if type(node).__name__ == "Cond" and "has_figures" in str(getattr(node, "cond", "")):
        under = True
    attrs = getattr(node, "custom_attrs", None) or {}
    if attrs.get("data-rfds-tab-value") == value or (
            type(node).__name__ == "TabsContent" and str(getattr(node, "value", "")).strip('"') == value):
        found.append(under)
    for child in getattr(node, "children", ()) or ():
        found.extend(_hidden_by(child, value, under))
    return found


class TestCluster01ey_TabShownByState(SimpleTestCase):
    """1.1035: a tab shown by state."""

    # -- 1.1035 --------------------------------------------------------
    def test_1_1035_a_tab_is_drawn_only_while_its_state_value_is_true(self) -> None:
        """
        Scenario 1.1035: ui.section("Figures", ..., visible=ResultState.has_figures) as a tab.
        Given: a form's result as tabs, Summary then Figures shown by state; a record page the same.
        When:  each is built and compiled.
        Then:  the Figures tab's trigger and panel are each under a condition on has_figures, the
               Summary tab's are not; the page compiles; on a record page with tabs the same; on a
               record page without tabs a state value is still refused, pointing to tabs=True.
        """
        sections = [ui.section("Summary", ui.text("The report.")),
                    ui.section("Figures", ui.text("The figures."), visible=ResultState.has_figures)]
        form = ui.form(title="Report", submit=ResultState.build, fields=[ui.field("name")], tabs=True,
                       result=sections)
        built = form.build(CTX)
        self.assertEqual(_hidden_by(built, "figures"), [True, True], "the trigger and the panel, by state")
        self.assertEqual(_hidden_by(built, "summary"), [False, False], "the first tab always")
        _, module = compile_page("/report", built)
        self.assertIn("has_figures", module)
        record = ui.record(title="Report", tabs=True, sections=list(sections))
        built_record = record.build(Build(page="report", route="/report"))
        self.assertEqual(_hidden_by(built_record, "figures"), [True, True], "a record page's tab too")
        compile_page("/report", built_record)
        with self.assertRaises(PageError) as anchors:
            ui.record(title="Report", sections=list(sections)).build(Build(page="report", route="/report"))
        self.assertIn("give the record `tabs=True`, where a state value hides a tab", str(anchors.exception))
