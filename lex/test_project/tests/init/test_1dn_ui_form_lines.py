"""lex.ui forms: a text field of several lines -- ``ui.field(key, lines=6)``.

Intent: notes, a comment, a reason, a pasted list of codes -- a form often asks for more than one
line. A consultant writes `ui.field("notes", lines=6)` and gets RFDS's text area, six lines high;
the handler finds a `str` under `values["notes"]` with its line breaks, trimmed at the ends, as
for any text. `lines=` belongs to text only: any other kind refuses it with a sentence saying so,
and a field without `lines=` is the one-line input it always was. A regression is a pasted list
squashed onto one line, a field that calls the server on every keystroke, or `lines=` silently
ignored on a number.

Cluster 1dn -- scenarios 1.860-1.862. Type: U.
Covers: lex/lex_app/reflex/ui/pages/forms.py (field, Field.check, Field.control),
lex/lex_app/reflex/ui/forms.py (clean: text).
Run: python -m lex pytest lex/test_project/tests/init/test_1dn_ui_form_lines.py -v
"""

from __future__ import annotations

import asyncio

import pytest
import reflex as rx
from django.test import SimpleTestCase
from reflex.compiler.compiler import compile_page

from lex import ui
from lex.lex_app.reflex.ui import forms, testing
from lex.lex_app.reflex.ui.pages.parts import Build, PageError
from lex.lex_app.reflex.ui.state import State

pytestmark = pytest.mark.init


class OrdersNotesState(ui.State):
    """Orders a user annotates, and codes pasted one per line."""

    draft: str = "Deliver before noon\nRing twice"
    got: dict = {}
    runs: int = 0

    @ui.action
    def annotate(self, values):
        self.runs += 1
        self.got = {"notes": values["notes"], "type": type(values["notes"]).__name__}

    @ui.action
    def paste(self, values):
        self.got = {"codes": values["codes"].splitlines(), "region": values["region"]}

    @ui.action
    def explain(self, key, values):
        self.got = {"key": key, "reason": values["reason"]}

    @ui.action
    def draw(self, values):
        return None

    @ui.action
    def wrong(self, values):
        return None

    @ui.rows
    def orders(self):
        return [{"id": 1, "name": "Order one"}]


def annotate_order():
    return ui.form(title="Annotate order", submit=OrdersNotesState.annotate,
                   fields=[ui.field("notes", lines=6, required=True)])


def paste_codes():
    return ui.form(title="Paste codes", submit=OrdersNotesState.paste, steps=[
        ui.step("Codes", fields=[ui.field("codes", lines=10, help="One code per line.")]),
        ui.step("Region", fields=[ui.field("region")])])


def _explaining():
    return ui.embed(ui.table(OrdersNotesState.orders, columns=["name"], row_actions=lambda row: [
        ui.button("Hold", OrdersNotesState.explain, row["id"], ask=[ui.field("reason", lines=3, required=True)])]),
        name="orders")


def _build() -> None:
    """What lex-app does when it registers the pages: each is built, which tells the handlers their fields."""
    for page in (annotate_order, paste_codes):
        page().build(Build(page=page.__name__, route=f"/{page.__name__}"))
    _explaining()


def _text(component: rx.Component) -> str:
    """The built page as the browser gets it: its elements, and the functions its forms call."""
    return str(component) + "\n".join(component._get_all_hooks())


class TestCluster01dn_FormLines(SimpleTestCase):
    """1.860-1.862: a field of several lines -- on the page, through a run, and declared wrongly."""

    def setUp(self) -> None:
        _build()

    # -- 1.860 ---------------------------------------------------------
    def test_1_860_lines_makes_the_field_rfds_text_area_and_it_sends_nothing_while_typing(self) -> None:
        """
        Scenario 1.860: ui.field(key, lines=6) on a page.
        Given: a form with a field of six lines (required, with help), one of three lines that
               starts with a text, one that starts with a state value, and a one-line field; a
               wizard step and a button's ask= each holding a field of several lines.
        When:  they are built, as the browser gets them, and compiled.
        Then:  a field with lines= is RFDS's text area with that many rows, at the form's full
               width, with its label, help, required mark and the server's message; it starts
               with value=; a field without lines= is still RFDS's one-line text input; nothing
               on the page reaches the server but the submit; the step and the dialog draw it the
               same way.
        """
        page = ui.form(title="Draw orders", submit=OrdersNotesState.draw, fields=[
            ui.field("notes", lines=6, required=True, help="What the driver should know."),
            ui.field("summary", lines=3, value="Two pallets"),
            ui.field("held", lines=4, value=OrdersNotesState.draft),
            ui.field("reference"),
        ])
        text = _text(page.build(Build(page="draw_orders", route="/orders/draw")))
        self.assertEqual(text.count('"data-rfds-component":"textarea"'), 3, "each field with lines= is a text area")
        self.assertEqual(text.count('"data-rfds-component":"text_input"'), 1, "a field without lines= is one line")
        for rows in ("6", "3", "4"):
            self.assertIn(f'rows:"{rows}"', text, f"lines={rows} gives {rows} rows")
        for key in ("notes", "summary", "held", "reference"):
            with self.subTest(field=key):
                self.assertIn(f'ui_invalid_rx_state_?.["OrdersNotesState.draw|{key}"]', text,
                              f"{key} shows the server's message for it")
                self.assertIn(f'name:"{key}"', text, f"{key} is sent under its key")
        for needle, why in (('rfdsI18n.t("Notes", "Notes"', "the label defaults to the key with a capital"),
                            ('rfdsI18n.t("What the driver should know."', "help="),
                            ("rfds-field-required", "the required mark"),
                            ('defaultValue:"Two pallets"', "value= is where the field starts"),
                            ("orders_notes_state.draft_rx_state_", "or a state value the page's load filled"),
                            ('"data-rfds-width":"full"', "a text area takes the form's full width")):
            with self.subTest(needle=needle):
                self.assertIn(needle, text, why)
        self.assertEqual(text.count("ReflexEvent("), 1, "nothing reaches the server while the user types")
        received = {one.key: one for one in forms.of(OrdersNotesState.draw.fn.__ui_fn__).fields}
        self.assertEqual((received["notes"].kind, received["notes"].required), ("text", True),
                         "the handler is told a text field")

        _, module = compile_page("/orders/draw", page.build(Build(page="draw_orders", route="/orders/draw")))
        self.assertIn("TextArea as RadixThemesTextArea", module, "compiled: Radix's text area, via RFDS")
        self.assertEqual(module.count("ReflexEvent("), 1, "the compiled page reaches the server only on submit")

        wizard = _text(paste_codes().build(Build(page="paste_codes", route="/paste_codes")))
        self.assertIn('"data-rfds-component":"textarea"', wizard, "a wizard's step draws it")
        self.assertIn('rows:"10"', wizard)
        dialog = _text(_explaining())
        self.assertIn('"data-rfds-component":"textarea"', dialog, "a button's ask= draws it")
        self.assertIn('ui_invalid_rx_state_?.["OrdersNotesState.explain|reason"]', dialog)

    # -- 1.861 ---------------------------------------------------------
    def test_1_861_the_handler_gets_the_text_with_its_line_breaks_trimmed_at_the_ends(self) -> None:
        """
        Scenario 1.861: through Reflex's event processor, as the browser sends it.
        Given: the annotation form (notes required), the wizard pasting codes, and the row
               button asking for a reason.
        When:  each is sent, with text over several lines and with only blank lines.
        Then:  the body gets a str with the line breaks between the lines, the blank space at the
               ends trimmed; a required field of only spaces and line breaks is a message at the
               field and the body does not run; a pasted list splits into its lines; the dialog
               hands the row's key and the text.
        """
        sent = "  \nDeliver before noon\n\n  Ring twice\n\n"
        right = ui.testing.run(OrdersNotesState, "annotate", values={"notes": sent})
        self.assertEqual((right.invalid, right.fields["runs"]), ({}, 1), "the body ran once")
        self.assertEqual(right.fields["got"], {"notes": "Deliver before noon\n\n  Ring twice", "type": "str"},
                         "the lines as typed, trimmed at the ends")
        blank = ui.testing.run(OrdersNotesState, "annotate", values={"notes": " \n\n \n"})
        self.assertEqual((blank.invalid, blank.fields["runs"]), ({"notes": "Enter a value."}, 0),
                         "only blank lines is nothing entered")
        self.assertEqual(forms.clean(forms.Expect("notes"), "a\r\nb\n"), ("a\r\nb", ""),
                         "a text is trimmed at its ends only, whatever its line breaks")

        async def wizard(harness):
            await (await harness.send(OrdersNotesState, "paste", values={"codes": "A1\nB2\nC3\n", "region": ""}))
            await (await harness.send(OrdersNotesState, "paste", values={"codes": "A1\nB2\nC3\n", "region": "north"}))
            return await harness.fields(OrdersNotesState)

        async def explained(harness):
            await (await harness.send(State, "ui_ask", dialog="orders-ask-1", args=[1]))
            await (await harness.send(OrdersNotesState, "explain", key=1,
                                      values={"reason": "Customer asked\nto wait\n"}))
            return await harness.fields(OrdersNotesState)

        async def session(steps):
            async with testing.Harness() as harness:
                await harness.open_tab()
                return await steps(harness)

        self.assertEqual(asyncio.run(session(wizard))["got"], {"codes": ["A1", "B2", "C3"], "region": "north"},
                         "a pasted list arrives as its lines")
        self.assertEqual(asyncio.run(session(explained))["got"], {"key": 1, "reason": "Customer asked\nto wait"},
                         "the dialog hands the row's key, then the text")

    # -- 1.862 ---------------------------------------------------------
    def test_1_862_lines_belongs_to_text_and_is_a_number_of_lines(self) -> None:
        """
        Scenario 1.862: lines= declared wrongly.
        Given: lines= on a number, a date, a yes/no and a choice (by options= or by kind=), and
               lines= that is no number of lines -- 1, 0, a text, True.
        When:  the page is built, or the field declared.
        Then:  on another kind the page refuses it, naming the page, the slot and the field and
               saying that lines= goes with text, the default kind; kind="text" with lines= is
               accepted; a value that is no whole number of two or more is refused when the
               field is declared, saying what to write.
        """
        ctx = Build(page="orders", route="/orders")
        cases = {
            "number": (ui.field("amount", kind="number", lines=3), "`number`"),
            "date": (ui.field("due", kind="date", lines=3), "`date`"),
            "bool": (ui.field("urgent", kind="bool", lines=3), "`bool`"),
            "options": (ui.field("region", options=["north", "south"], lines=3), "`choice`"),
            "choices": (ui.field("regions", kind="choices", options=["north", "south"], lines=3), "`choices`"),
        }
        for name, (field, kind) in cases.items():
            with self.subTest(case=name), self.assertRaises(PageError) as wrong:
                ui.form(title="Orders", submit=OrdersNotesState.wrong, fields=[field]).build(ctx)
            self.assertEqual(str(wrong.exception),
                             f"Page `orders`, slot `fields`: field `{field.key}` has `lines=` and the kind {kind}.\n"
                             "`lines=` goes with text, the default kind: leave `kind=` out.", name)
        text = _text(ui.form(title="Orders", submit=OrdersNotesState.wrong,
                             fields=[ui.field("notes", kind="text", lines=2)]).build(ctx))
        self.assertIn('"data-rfds-component":"textarea"', text, "kind=\"text\" said aloud is text too")

        for given in (1, 0, -3, "6", True, 2.5):
            with self.subTest(lines=given), self.assertRaises(TypeError) as wrong:
                ui.field("notes", lines=given)
            self.assertEqual(str(wrong.exception),
                             f"ui.field('notes', lines=) is how many lines the field shows, 2 or more; got {given!r}. "
                             "A field of one line is the default: leave `lines=` out.")
