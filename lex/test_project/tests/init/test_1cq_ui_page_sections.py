"""lex.ui pages: a section shows its own loading and failure; repetition and choice without raw Reflex.

Intent: the author never writes a loading, failed or empty branch. A section with its own load
shows a skeleton until that load has finished and its failure in place; without a load it is
just its content. What a page does not already handle is covered by ui.each and ui.switch, and
anything outside the vocabulary goes through ui.custom_block, which must say why. A regression
is content shown before its data, a failed section that leaves a blank, or raw components
entering a page unmarked.

Cluster 1cq -- scenarios 1.794-1.796, 1.1125. Type: U.
Covers: lex/lex_app/reflex/ui/pages/kinds.py (section, states, _stacked), ui/pages/vocab.py (each, switch,
custom_block).
Run: python -m lex pytest lex/test_project/tests/init/test_1cq_ui_page_sections.py -v
"""

from __future__ import annotations

import pytest
import reflex as rx
from django.contrib.auth.models import Group
from django.test import SimpleTestCase

from lex import ui
from lex.lex_app.reflex.ui.pages import kinds, parts, tables, vocab
from lex.lex_app.reflex.ui.pages.parts import Build, PageError

pytestmark = pytest.mark.init


class SectionDesk(ui.State):
    documents: list[str] = []
    mode: str = "a"

    @ui.load
    def load_documents(self):
        self.documents = ["x"]

    @ui.action
    def start(self):
        return None


CTX = Build(page="fund", route="/funds/[key]")


class TestCluster01cq_PageSections(SimpleTestCase):
    """1.794-1.796: a section's own states; each and switch; the marked way out."""

    # -- 1.794 ---------------------------------------------------------
    def test_1_794_a_section_with_a_load_shows_its_skeleton_then_its_content_or_its_failure(self) -> None:
        """
        Scenario 1.794: ui.section.
        Given: a section without a load, one with a load on a page, and the same on a dashboard.
        When:  they are built.
        Then:  without a load the section is its content; with one, the content is shown only
               once ui.loaded says so, before that the failure message when there is one, else
               a skeleton (a table's, when the section is one table); on a page nothing is
               mounted (the opener starts the load); on a dashboard the load starts when the
               section mounts; a section's title is a sentence and comes first. Facts that
               follow one another form one run, each in a key-value list; a section inside a section, and
               empty= without a table, are refused.
        """
        plain = str(kinds.section("Notes", vocab.text("Hello")).build(CTX))
        self.assertIn('rfdsI18n.t("Hello", "Hello"', plain, "the content is built")
        self.assertNotIn("ui_loaded", plain, "no load: no loading state")

        loading = kinds.section("Documents", vocab.text("Hello"), load=SectionDesk.load_documents)
        on_page = loading.build(CTX)
        text = str(on_page)
        loaded = text.index('ui_loaded_rx_state_?.["SectionDesk.load_documents"]')
        self.assertLess(loaded, text.index('rfdsI18n.t("Hello"'), "the content sits behind the loaded check")
        self.assertIn('ui_failed_rx_state_?.["SectionDesk.load_documents"]', text, "the failure is shown in place")
        self.assertIn("rfds-skeleton", text, "a skeleton until then")
        self.assertNotIn("on_mount", on_page.event_triggers, "on a page the opener starts the load")
        on_dashboard = loading.build(Build(page="main", mounts=True))
        self.assertIn("on_mount", on_dashboard.event_triggers, "on a dashboard the load starts when the section mounts")
        with self.assertRaises(TypeError, msg="a section's title comes first and is a sentence"):
            kinds.section(vocab.text("Hello"))
        facts = str(kinds.section("Key facts", vocab.fact("A", 1), vocab.fact("B", 2), vocab.text("Hello")).build(CTX))
        self.assertEqual(facts.count('"data-slot":"rfds-grid"'), 1, "facts that follow one another are one run (1.1124)")
        self.assertEqual(facts.count('"data-slot":"rfds-key-value"'), 2,
                         "each fact inside a key-value list, which is what RFDS's items need")
        only_table = kinds.section("Groups", tables.table(Group, columns=["name"]), load=SectionDesk.load_documents)
        self.assertIn("rfds-skeleton-row", str(only_table.build(CTX)), "a section that is one table waits as a table")
        self.assertNotIn("rfds-skeleton-row", text, "any other section waits as a block")
        with self.assertRaises(PageError) as nested:
            kinds.section("Outer", kinds.section("Inner", vocab.text("x"))).build(CTX.at("sections[0]"))
        self.assertEqual(str(nested.exception),
                         "Page `fund`, slot `sections[0]`: a section inside a section.\n"
                         "Sections do not nest: give the page each of them, in `sections=[...]`.",
                         "a nested section is refused in its own words")
        with self.assertRaises(PageError) as no_table:
            kinds.section("Notes", vocab.text("x"), empty="Nothing yet.").build(CTX.at("sections[0]"))
        self.assertEqual(str(no_table.exception),
                         "Page `fund`, slot `sections[0]`: `empty=` is the sentence for a table without rows, and this "
                         "section has no table.\nRemove `empty=`, or put a `ui.table(...)` in the section.",
                         "empty= without a table is refused")

    # -- 1.795 ---------------------------------------------------------
    def test_1_795_each_repeats_and_switch_chooses(self) -> None:
        """
        Scenario 1.795: ui.each and ui.switch.
        Given: a list in the state and a mode in the state.
        When:  ui.each renders a part per item and ui.switch picks a part by the mode.
        Then:  each item is rendered through the part the function returns; a function that
               returns a raw component is refused with what to return; switch shows the case
               matching the value and nothing otherwise; a case that is not a lex component is
               refused.
        """
        repeated = str(vocab.each(SectionDesk.documents, lambda name: vocab.text(name)).build(CTX))
        self.assertIn("documents_rx_state_", repeated, "the list is iterated")
        self.assertIn("RadixThemesText", repeated, "each item is the part the function returned")
        with self.assertRaises(PageError) as raw:
            vocab.each(SectionDesk.documents, lambda name: rx.text(name)).build(CTX)
        self.assertIn("did not return a lex component", str(raw.exception), "the refusal says what was wrong")
        with self.assertRaises(PageError) as sectioned:
            vocab.each(SectionDesk.documents, lambda name: kinds.section("One", vocab.text(name))).build(CTX)
        self.assertEqual(str(sectioned.exception),
                         "Page `fund`: `ui.each` produced `ui.section`, and sections do not nest.\n"
                         "Give the page each section itself, in `sections=[...]`.", "each cannot produce sections")
        with self.assertRaises(TypeError, msg="each takes a function"):
            vocab.each(SectionDesk.documents, vocab.text("x"))
        with self.assertRaises(ValueError, msg="switch needs a case"):
            vocab.switch(SectionDesk.mode, {})
        chosen = str(vocab.switch(SectionDesk.mode, {"a": vocab.text("First"), "b": vocab.text("Second")}).build(CTX))
        self.assertIn("mode_rx_state_", chosen, "the value decides")
        self.assertIn('rfdsI18n.t("First"', chosen, "the first case is built")
        self.assertIn('rfdsI18n.t("Second"', chosen, "and the second")
        with self.assertRaises(PageError, msg="a case must be a lex component"):
            vocab.switch(SectionDesk.mode, {"a": rx.text("raw")}).build(CTX)

    # -- 1.796 ---------------------------------------------------------
    def test_1_796_anything_else_goes_through_the_marked_way_out(self) -> None:
        """
        Scenario 1.796: ui.custom_block and the section's slot check.
        Given: a raw component.
        When:  it is put into a section directly, and through ui.custom_block with and without
               a reason.
        Then:  directly it is refused, with the page, the slot and what to write; through
               ui.custom_block with a reason it is accepted and built as it is, and an error
               names it by its public name; without a reason, or with something that is not a
               component, custom_block is refused. A section hands its empty= sentence to a
               copy of its table, never to the author's part.
        """
        with self.assertRaises(PageError) as direct:
            kinds.section("Notes", rx.text("raw")).build(CTX.at("sections[0]"))
        self.assertEqual(str(direct.exception).splitlines()[0],
                         "Page `fund`, slot `sections[0]`: got a raw component (`Text`).",
                         "a raw component in a section is named")
        self.assertIn("ui.custom_block", str(direct.exception), "and the way out is named")
        marked = kinds.section("Notes", vocab.custom_block(rx.text("raw"), reason="A chart type the vocabulary lacks."))
        self.assertIn("raw", str(marked.build(CTX)), "a marked block is built as it is")
        block = vocab.custom_block(rx.text("raw"), reason="A chart type the vocabulary lacks.")
        self.assertEqual(parts.called(block), "`ui.custom_block`", "an error names it by its public name")
        shared = tables.table(Group, columns=["name"])
        kinds.section("First", shared, empty="No groups yet.").build(CTX)
        self.assertIsNone(shared.empty, "a section hands its empty sentence to a copy of the table, not to the part")
        with self.assertRaises(ValueError, msg="a custom block must say why"):
            vocab.custom_block(rx.text("raw"), reason=" ")
        with self.assertRaises(TypeError, msg="a custom block holds a component"):
            vocab.custom_block("not a component", reason="x")

    # -- 1.1125 --------------------------------------------------------
    def test_1_1125_buttons_and_badges_keep_their_own_width(self) -> None:
        """
        Scenario 1.1125: a button or a badge in a page is as wide as its words.
        Given: a section holding a status, facts, a text, and a Start and a Cancel button (Cancel
               shown only while a state value holds), then a text and one more button; and a
               record page with a status.
        When:  they are built.
        Then:  buttons and badges that follow one another sit in one RFDS cluster -- a row that
               wraps, each as wide as its words -- never stretched across the page by the stack
               that holds them; a part between them starts a new row; a button shown by state
               keeps its place in its row; the record page's badge is in a cluster too.
        """
        section = str(kinds.section(
            "Commitments", vocab.status("Investing", tone="info"), vocab.fact("Checked", 0, kind="number"),
            vocab.text("Working"), vocab.button("Start", SectionDesk.start),
            vocab.button("Cancel", SectionDesk.start, visible=SectionDesk.mode == "a"),
            vocab.text("Then"), vocab.button("Again", SectionDesk.start)).build(CTX))
        self.assertEqual(section.count('"data-slot":"rfds-cluster"'), 3,
                         "the badge, the two buttons together, and the last button: three rows")
        rows = section.split('"data-slot":"rfds-cluster"')
        self.assertIn('rfdsI18n.t("Investing"', rows[1].split('rfdsI18n.t("Checked"')[0], "the badge in its row")
        both = rows[2].split('rfdsI18n.t("Then"')[0]
        self.assertIn('rfdsI18n.t("Start"', both, "Start in the second row")
        self.assertIn('rfdsI18n.t("Cancel"', both, "with Cancel beside it, shown by state")
        self.assertIn('rfdsI18n.t("Again"', rows[3], "a part between buttons starts a new row")
        record = str(ui.record(title="Fund", status=vocab.status("Investing", tone="info"),
                               sections=[ui.section("Details", ui.text("Hi"))]).build(CTX))
        self.assertLess(record.index('"data-slot":"rfds-cluster"'), record.index('"data-slot":"rfds-lozenge"'),
                        "a record page's badge sits in a cluster, as wide as its word")
