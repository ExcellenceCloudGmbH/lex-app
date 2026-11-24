"""lex.ui pages: a closed section mounts what it holds when opened; a title from the state; actions.

Intent: a closed section is RFDS's native disclosure, and the browser lays out what a closed
`<details>` holds at zero size: a chart in it fits itself to nothing and never fits again. What a
closed section holds now mounts when the user first opens it -- except a table, which reads its rows
at once so the section's title can say how many there are. A section's title may be a state value
("Lines (12)") where the section has a heading of its own, and `actions=[ui.button(...)]` puts
buttons in that heading (RFDS's section actions; in a closed section they show when it is opened,
never inside the disclosure's summary). A record page and a tab name their sections with fixed
words and have no heading to hold actions, so there these are refused with what to write. A
regression is a chart drawn at zero size, a count lost, or an action or a title silently dropped.

Cluster 1ew -- scenarios 1.1032-1.1033. Type: U.
Covers: lex/lex_app/reflex/ui/pages/kinds.py (section title=, actions=, Section.titled, _content opened,
fixed), lex/lex_app/reflex/ui/pages/forms.py (a tab's section), lex/lex_app/reflex/ui/pages/frame.py
(a navigation section).
Run: python -m lex pytest lex/test_project/tests/init/test_1ew_ui_section_opened_title_actions.py -v
"""

from __future__ import annotations

import pytest
import reflex as rx
from django.test import SimpleTestCase
from reflex.compiler.compiler import compile_page

from lex import ui
from lex.lex_app.reflex.ui.pages.parts import Build, PageError

pytestmark = pytest.mark.init


class FiguresState(ui.State):
    """A tool's result: figures, a chart of them, their lines, and a title that counts them."""

    lines_title: str = "Lines"

    @ui.action
    def read(self, values):
        self.lines_title = "Lines (12)"

    @ui.action
    def again(self):
        return None

    @ui.rows
    def lines(self):
        return [{"id": n, "name": f"Line {n}"} for n in range(12)]


CTX = Build(page="figures", route="/figures")


def _under_opened(node, wanted: str, opened: bool = False) -> list[bool]:
    """For each component whose rendered props mention ``wanted``: whether a cond on the section's opened state holds it."""
    found: list[bool] = []
    if type(node).__name__ == "Cond" and "rfdsLexopened" in str(getattr(node, "cond", "")):
        opened = True
    own = " ".join(str(value) for value in (getattr(node, "id", None), getattr(node, "class_name", None)))
    if wanted in own:
        found.append(opened)
    for child in getattr(node, "children", ()) or ():
        found.extend(_under_opened(child, wanted, opened))
    return found


class TestCluster01ew_SectionOpenedTitleActions(SimpleTestCase):
    """1.1032-1.1033."""

    # -- 1.1032 --------------------------------------------------------
    def test_1_1032_a_closed_section_mounts_its_content_when_opened_but_its_table_at_once(self) -> None:
        """
        Scenario 1.1032: ui.section("Chart", <a chart>, ui.table(...), collapsed=True).
        Given: a closed section holding a chart (a custom block) and a table.
        When:  it is built and compiled.
        Then:  the chart sits under a condition on the section's "opened" client state, which a
               click on the section sets; the table does not (it reads its rows at once for the
               count in the title); an open section has no such condition.
        """
        chart = ui.custom_block(rx.box(id="figures-chart"), reason="a chart lex.ui has no part for")
        closed = ui.section("Chart", chart, ui.table(FiguresState.lines, columns=["name"]), collapsed=True)
        built = closed.build(CTX)
        self.assertEqual(_under_opened(built, "figures-chart"), [True], "the chart mounts when the section opens")
        self.assertIn(False, _under_opened(built, "rfds-data-table"), "the table reads at once, for the count")
        self.assertNotIn(True, _under_opened(built, "rfds-data-table"))
        _, module = compile_page("/figures", built)
        self.assertIn("rfdsLexopened", module, "the section's opened state lives in the browser")
        self.assertIn("onClick", module, "a click on the closed section opens it for good")
        open_section = ui.section("Chart", ui.custom_block(rx.box(id="open-chart"), reason="a chart"),
                                  actions=[ui.button("Again", FiguresState.again)])
        self.assertEqual(_under_opened(open_section.build(CTX), "open-chart"), [False], "an open section mounts at once")

    # -- 1.1033 --------------------------------------------------------
    def test_1_1033_a_sections_title_may_be_a_state_value_and_it_takes_actions(self) -> None:
        """
        Scenario 1.1033: ui.section(FiguresState.lines_title, ..., actions=[ui.button(...)]).
        Given: a form whose result is a section titled by a state value with an action, closed and open.
        When:  the page is built and compiled; then the same on a record page, in a tab and in the
               frame's navigation; then actions= that is not a button.
        Then:  the title is the state value and the button sits in RFDS's section actions; on a record
               page and in a tab a state title and actions are refused, saying what to write; the
               navigation refuses a state title; a text in actions= is refused.
        """
        form = ui.form(title="Figures", submit=FiguresState.read, fields=[ui.field("name")], result=[
            ui.section(FiguresState.lines_title, ui.table(FiguresState.lines, columns=["name"]),
                       actions=[ui.button("Read again", FiguresState.again)], collapsed=True),
            ui.section(FiguresState.lines_title, ui.text("All lines."), actions=[ui.button("Again", FiguresState.again)])])
        _, module = compile_page("/figures", form.build(CTX))
        self.assertIn("lines_title", module, "the title is the state value")
        self.assertIn("rfds-section-actions", module, "the button sits in the section's actions")
        self.assertIn('"Read again"', str(form.build(CTX)))
        cases = {
            "record title": (lambda: ui.record(title="Figure", sections=[
                ui.section(FiguresState.lines_title, ui.text("x")), ui.section("Other", ui.text("y"))]),
                "names its sections with fixed words"),
            "record actions": (lambda: ui.record(title="Figure", sections=[
                ui.section("Lines", ui.text("x"), actions=[ui.button("Again", FiguresState.again)])]),
                "Put the buttons inside the section, or give the page `actions=[...]`"),
            "tab actions": (lambda: ui.form(title="Figures", submit=FiguresState.read, fields=[ui.field("name")],
                                            tabs=True, result=[ui.section("A", ui.text("a"),
                                                                          actions=[ui.button("Again", FiguresState.again)]),
                                                               ui.section("B", ui.text("b"))]),
                            "a tab has no heading of its own"),
            "not a button": (lambda: ui.form(title="Figures", submit=FiguresState.read, fields=[ui.field("name")],
                                             result=[ui.section("A", ui.text("a"), actions=[ui.text("no")])]),
                             "A section's `actions=` holds `ui.button(...)`"),
        }
        for name, (make, says) in cases.items():
            with self.subTest(name), self.assertRaises(PageError) as wrong:
                make().build(CTX)
            self.assertIn(says, str(wrong.exception), name)
        with self.assertRaises(TypeError) as nav:
            ui.frame(nav=[ui.section(FiguresState.lines_title, ui.link("Help", to="https://example.com"))])
        self.assertIn("fixed words", str(nav.exception))
        with self.assertRaises(TypeError):
            ui.section(3, ui.text("x"))
