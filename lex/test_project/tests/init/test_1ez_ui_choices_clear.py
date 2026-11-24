"""lex.ui forms: a field of several choices can be emptied at once -- its "Clear".

Intent: `ui.field("regions", kind="choices", options=[...])` lets the user tick several options; past
twelve it is RFDS's multi-select, which has no way to empty it at once, so a user who picked thirty
regions removed them one by one. The chosen values live in the browser until the form is sent (a
client state), so a "Clear" beside the field empties them there, with no round trip and no change to
RFDS; it shows only while something is chosen. A regression is a choice that cannot be undone
without thirty clicks, or a Clear that leaves values behind.

Cluster 1ez -- scenario 1.1036. Type: U.
Covers: lex/lex_app/reflex/ui/pages/forms.py (Field.input for kind="choices").
Run: python -m lex pytest lex/test_project/tests/init/test_1ez_ui_choices_clear.py -v
"""

from __future__ import annotations

import pytest
from django.test import SimpleTestCase
from reflex.compiler.compiler import compile_page

from lex import ui
from lex.lex_app.reflex.ui.pages.parts import Build

pytestmark = pytest.mark.init

REGIONS = [f"Region {n}" for n in range(1, 31)]


class RegionsState(ui.State):
    @ui.action
    def report(self, values):
        return None


def _clears(node, under_cond: bool = False) -> list[tuple[str, bool]]:
    """Each Clear button: the field it empties, and whether a condition holds it."""
    found: list[tuple[str, bool]] = []
    if type(node).__name__ == "Cond":
        under_cond = True
    key = (getattr(node, "custom_attrs", None) or {}).get("data-lex-clear")
    if key:
        found.append((key, under_cond))
    for child in getattr(node, "children", ()) or ():
        found.extend(_clears(child, under_cond))
    return found


class TestCluster01ez_ChoicesClear(SimpleTestCase):
    """1.1036: a Clear beside several choices."""

    # -- 1.1036 --------------------------------------------------------
    def test_1_1036_several_choices_can_be_emptied_at_once(self) -> None:
        """
        Scenario 1.1036: kind="choices" over 30 options (RFDS's multi-select) and over 3 (a checkbox group).
        Given: a form with both, and a single choice.
        When:  it is built and compiled.
        Then:  each several-choice field has a Clear, shown only while something is chosen; its click
               sets the field's browser value to the empty list; the single choice has none.
        """
        form = ui.form(title="Regions", submit=RegionsState.report, fields=[
            ui.field("regions", kind="choices", options=REGIONS),
            ui.field("kinds", kind="choices", options=["A", "B", "C"]),
            ui.field("main", options=["A", "B", "C"])])
        built = form.build(Build(page="regions", route="/regions"))
        self.assertEqual(sorted(_clears(built)), [("kinds", True), ("regions", True)],
                         "a Clear for each several-choice field, under a condition, none for a single choice")
        _, module = compile_page("/regions", built)
        self.assertIn("multiopen", module.lower(), "30 options: RFDS's multi-select")
        self.assertRegex(module, r"_client_state_setRfdslexfield\d+'\]\(\[\]\)", "Clear empties the browser's value")
        self.assertRegex(module, r"\.length > 0", "shown while something is chosen")
