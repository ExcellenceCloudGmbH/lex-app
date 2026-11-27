"""lex.ui forms: a field where the user chooses several of a list of options.

Intent: "which regions?", "which funds?" -- a form often asks for several of a list. A consultant
writes `ui.field("regions", kind="choices", options=[...])` and the handler finds a `list[str]`
under `values["regions"]`: in the order of the options, `[]` when nothing is chosen, every value
one of the options, checked on the server like a choice. The field is RFDS's own input for it --
boxes to tick for a short list, RFDS's multi-select for a long one or one from the state -- and,
like every input, it sends nothing while the user picks. A regression is a handler that gets a
text where a list was promised, a value that is no option reaching the body, a required field
passing with nothing ticked, or a field that calls the server on every tick.

Cluster 1dl -- scenarios 1.852-1.855. Type: U.
Covers: lex/lex_app/reflex/ui/forms.py (KINDS, clean, check), lex/lex_app/reflex/ui/pages/forms.py
(field, Field.check, Field.control), lex/lex_app/reflex/ui/pages/vocab.py (shape: choices),
lex/lex_app/reflex/ui/testing.py (run with a list among the values).
Run: python -m lex pytest lex/test_project/tests/init/test_1dl_ui_form_choices.py -v
"""

from __future__ import annotations

import asyncio

import pytest
import reflex as rx
from django.test import SimpleTestCase
from reflex.compiler.compiler import compile_page

from lex import ui
from lex.lex_app.reflex.ui import forms
from lex.lex_app.reflex.ui import testing
from lex.lex_app.reflex.ui.forms import Expect, Receives
from lex.lex_app.reflex.ui.pages import links
from lex.lex_app.reflex.ui.pages.parts import Build, PageError
from lex.lex_app.reflex.ui.state import State

pytestmark = pytest.mark.init

REGIONS = ("north", "south", "east", "west")


class FundsState(ui.State):
    """Funds reported on by region; the funds a user may pick are loaded into the state."""

    funds: list[str] = ["Alpha", "Beta", "Gamma"]
    picked: list[str] = ["north"]
    got: dict = {}
    runs: int = 0

    @ui.action
    def report(self, values):
        self.runs += 1
        self.got = {"regions": values["regions"], "type": type(values["regions"]).__name__}

    @ui.action
    def compare(self, values):
        self.got = {"funds": values["funds"]}

    @ui.action
    def tag(self, key, values):
        self.got = {"key": key, "regions": values["regions"]}

    @ui.action
    def plan(self, values):
        self.got = dict(values)

    @ui.action
    def draw(self, values):
        return None

    @ui.action
    def wrong(self, values):
        return None

    @ui.rows
    def orders(self):
        return [{"id": 1, "name": "Order one"}]


def funds_report():
    return ui.form(title="Funds report", submit=FundsState.report,
                   fields=[ui.field("regions", kind="choices", options=list(REGIONS), required=True)])


def compare_funds():
    return ui.form(title="Compare funds", submit=FundsState.compare,
                   fields=[ui.field("funds", kind="choices", options=FundsState.funds)])


def plan_orders():
    return ui.form(title="Plan orders", submit=FundsState.plan, steps=[
        ui.step("Where", fields=[ui.field("regions", kind="choices", options=list(REGIONS), required=True)]),
        ui.step("When", fields=[ui.field("year", kind="year", required=True)])])


def orders_page():
    """Stands for a page declared with @ui.page("/orders")."""


orders_page.__ui_page__ = links.info_of("/orders", "orders_page")


def _tagging():
    return ui.embed(ui.table(FundsState.orders, columns=["name"], row_actions=lambda row: [
        ui.button("Tag", FundsState.tag, row["id"],
                  ask=[ui.field("regions", kind="choices", options={"north": "North", "south": "South"})])]),
        name="orders")


def _build() -> None:
    """What lex-app does when it registers the pages: each is built, which tells the handlers their fields."""
    for page in (funds_report, compare_funds, plan_orders):
        page().build(Build(page=page.__name__, route=f"/{page.__name__}"))
    _tagging()


def _text(component: rx.Component) -> str:
    """The built page as the browser gets it: its elements, and the functions its forms call."""
    return str(component) + "\n".join(component._get_all_hooks())


class TestCluster01dl_FormChoices(SimpleTestCase):
    """1.852-1.855: several of a list -- on the server, on the page, through a run, and shown."""

    def setUp(self) -> None:
        _build()

    # -- 1.852 ---------------------------------------------------------
    def test_1_852_several_choices_reach_the_handler_as_a_list_of_the_options(self) -> None:
        """
        Scenario 1.852: what the handler receives, and what is refused.
        Given: a field of several choices among four regions, and what a browser sends for it:
               a list, one value alone (a form sends a single value as a text), nothing.
        When:  the values are checked.
        Then:  the handler gets a list of texts, in the order of the options, each once; []
               when nothing is chosen; a required field with nothing chosen says to choose at
               least one; a value that is no option -- also when the options are a field of the
               handler's state -- is refused with a sentence, and so is anything but texts.
        """
        expect = Expect("regions", "choices", options=REGIONS)
        cases = ((["west", "north"], ["north", "west"]), (("south",), ["south"]), ("east", ["east"]),
                 (["north", "north", "east"], ["north", "east"]), ([], []), (None, []), ("", []), (False, []))
        for sent, expected in cases:
            with self.subTest(sent=sent):
                value, problem = forms.clean(expect, sent)
                self.assertEqual((value, problem), (expected, ""), f"{sent!r}: the options chosen, in their order")
                self.assertIs(type(value), list, "always a list the handler can loop over")
        required = Expect("regions", "choices", required=True, options=REGIONS)
        for sent in ([], None, ""):
            with self.subTest(required=sent):
                self.assertEqual(forms.clean(required, sent), ([], "Choose at least one."))
        for sent in (["north", "Atlantis"], "Atlantis", [""], 3, {"north": True}, [["north"]]):
            with self.subTest(refused=sent):
                self.assertEqual(forms.clean(expect, sent), ([], "Choose from the options."), f"{sent!r}")

        class Holder:
            funds = ["Alpha", "Beta"]

        receives = Receives(fields=(Expect("funds", "choices", options_field="funds"), Expect("note")))
        self.assertEqual(forms.check(receives, {"funds": ["Beta", "Alpha"], "note": "x"}, Holder()),
                         ({"funds": ["Alpha", "Beta"], "note": "x"}, {}), "in the order the state holds them")
        self.assertEqual(forms.check(receives, {"funds": ["Delta"]}, Holder())[1],
                         {"funds": "Choose from the options."}, "options held in the state are checked against it")
        self.assertEqual(forms.check(receives, {})[0], {"funds": [], "note": ""}, "a field not sent is empty")

    # -- 1.853 ---------------------------------------------------------
    def test_1_853_the_field_is_rfds_input_for_several_and_sends_nothing_while_picking(self) -> None:
        """
        Scenario 1.853: ui.field(kind="choices") on a page.
        Given: a form with several choices among four worded regions, among thirteen codes,
               among the funds in the handler's state, and one that starts with chosen values;
               a wizard step and a button's ask= holding one each.
        When:  they are built, as the browser gets them.
        Then:  a short fixed list is RFDS's checkbox group -- its wording translated -- and a
               long one or the state's is RFDS's multi-select, as RFDS advises (more than 12);
               each has its label, the required mark and the server's message for it; a field
               starts with value=, a list or a state value; nothing on the page reaches the
               server but the submit; the handler is told the field and its options; the page
               compiles with Reflex's page compiler. The step and the dialog draw it the same way.
        """
        page = ui.form(title="Draw orders", submit=FundsState.draw, fields=[
            ui.field("regions", kind="choices", options={"north": "North", "south": "South"}, required=True),
            ui.field("codes", kind="choices", options=[f"C{number}" for number in range(13)]),
            ui.field("funds", kind="choices", options=FundsState.funds, help="Up to three."),
            ui.field("start", kind="choices", options=list(REGIONS), value=["south"]),
            ui.field("held", kind="choices", options=list(REGIONS), value=FundsState.picked),
        ])
        text = _text(page.build(Build(page="draw_orders", route="/orders/draw")))
        drawn = {"regions": "checkbox_group", "codes": "multi_select", "funds": "multi_select",
                 "start": "checkbox_group", "held": "checkbox_group"}
        for key, component in drawn.items():
            with self.subTest(field=key):
                self.assertIn(f'"data-rfds-component":"{component}"', text, f"{key} is RFDS's {component}")
                self.assertIn(f'ui_invalid_rx_state_?.["FundsState.draw|{key}"]', text,
                              f"{key} shows the server's message for it")
        self.assertEqual(text.count('"data-rfds-component":"checkbox_group"'), 3, "three short fixed lists")
        for needle, why in (('rfdsI18n.t("North", "North"', "a worded option is translated"),
                            ('rfdsI18n.t("Regions", "Regions"', "the label defaults to the key with a capital"),
                            ('rfdsI18n.t("Up to three.", "Up to three."', "help="),
                            ("rfds-field-required", "the required mark"),
                            ('["south"]', "value= is where the field starts"),
                            ("funds_state.picked_rx_state_", "or a state value the page's load filled"),
                            ("funds_state.funds_rx_state_", "the state's options")):
            with self.subTest(needle=needle):
                self.assertIn(needle, text, why)
        self.assertEqual(text.count("ReflexEvent("), 1, "nothing reaches the server while the user picks")
        self.assertIn('funds_state.draw", ({ ["values"] : form_data })', text, "the submit sends the whole form")
        received = {one.key: one for one in forms.of(FundsState.draw.fn.__ui_fn__).fields}
        self.assertEqual((received["regions"].kind, received["regions"].options, received["regions"].required),
                         ("choices", ("north", "south"), True), "the handler is told the field and its options")
        self.assertEqual(received["funds"].options_field, "funds", "or the state field holding them")

        _, module = compile_page("/orders/draw", page.build(Build(page="draw_orders", route="/orders/draw")))
        self.assertIn("CheckboxGroup as RadixThemesCheckboxGroup", module, "compiled: the boxes are Radix's, via RFDS")
        self.assertIn('import {Command} from "cmdk"', module, "and the multi-select's list is cmdk's")
        self.assertEqual(module.count("ReflexEvent("), 1, "the compiled page reaches the server only on submit")

        wizard = _text(plan_orders().build(Build(page="plan_orders", route="/plan_orders")))
        self.assertIn('"data-rfds-component":"checkbox_group"', wizard, "a wizard's step draws it")
        dialog = _text(_tagging())
        self.assertIn('"data-rfds-component":"checkbox_group"', dialog, "a button's ask= draws it")
        self.assertIn('ui_invalid_rx_state_?.["FundsState.tag|regions"]', dialog)

    # -- 1.854 ---------------------------------------------------------
    def test_1_854_a_run_hands_the_body_the_checked_list(self) -> None:
        """
        Scenario 1.854: through Reflex's event processor, as the browser sends it.
        Given: the funds report (required regions), the comparison (funds from the state), the
               wizard and the row button that asks, each built on its page.
        When:  each is sent, with good values and with wrong ones.
        Then:  the body runs once with a list in the order of the options; nothing ticked on a
               required field, or a value that is no option, is a message at the field and the
               body does not run; the wizard carries the list to its last step; the dialog
               hands the row's key and the list to the handler.
        """
        right = ui.testing.run(FundsState, "report", values={"regions": ["west", "north"]})
        self.assertEqual((right.invalid, right.fields["runs"]), ({}, 1), "the body ran once")
        self.assertEqual(right.fields["got"], {"regions": ["north", "west"], "type": "list"},
                         "a list, in the order of the options")
        lone = ui.testing.run(FundsState, "report", values={"regions": "east"})
        self.assertEqual(lone.fields["got"], {"regions": ["east"], "type": "list"}, "one value alone is a list of one")
        empty = ui.testing.run(FundsState, "report", values={"regions": []})
        self.assertEqual((empty.invalid, empty.fields["runs"]), ({"regions": "Choose at least one."}, 0))
        other = ui.testing.run(FundsState, "report", values={"regions": ["north", "Atlantis"]})
        self.assertEqual((other.invalid, other.fields["runs"]), ({"regions": "Choose from the options."}, 0),
                         "a value that is no option never reaches the body")

        state = ui.testing.run(FundsState, "compare", values={"funds": ["Gamma", "Alpha"]})
        self.assertEqual((state.invalid, state.fields["got"]), ({}, {"funds": ["Alpha", "Gamma"]}),
                         "options from the state, in the state's order")
        unknown = ui.testing.run(FundsState, "compare", fields={"funds": ["Alpha"]}, values={"funds": ["Gamma"]})
        self.assertEqual(unknown.invalid, {"funds": "Choose from the options."}, "checked against the state as it is")
        none = ui.testing.run(FundsState, "compare", values={})
        self.assertEqual(none.fields["got"], {"funds": []}, "nothing chosen is an empty list")

        async def wizard(harness):
            await (await harness.send(FundsState, "plan", values={"regions": ["south"], "year": ""}))
            await (await harness.send(FundsState, "plan", values={"regions": ["south", "north"], "year": "2026"}))
            return await harness.fields(FundsState)

        async def tagged(harness):
            await (await harness.send(State, "ui_ask", dialog="orders-ask-1", args=[1]))
            await (await harness.send(FundsState, "tag", key=1, values={"regions": ["south", "north"]}))
            return await harness.fields(FundsState)

        async def session(steps):
            async with testing.Harness() as harness:
                await harness.open_tab()
                return await steps(harness)

        planned = asyncio.run(session(wizard))
        self.assertEqual(planned["got"], {"regions": ["north", "south"], "year": 2026}, "the wizard's last step")
        self.assertEqual(asyncio.run(session(tagged))["got"], {"key": 1, "regions": ["north", "south"]},
                         "the dialog hands the row's key, then the list")

    # -- 1.855 ---------------------------------------------------------
    def test_1_855_several_choices_declared_wrongly_and_shown(self) -> None:
        """
        Scenario 1.855: the build-time errors, and kind= for showing.
        Given: several choices without options, with options that are not texts, starting from a
               value that is no list; options on a field that is no choice; an unknown kind;
               and facts of the kind choices.
        When:  each is built.
        Then:  each error names the page and the slot and says what to write; the kinds listed
               include choices; a fact shows the chosen values joined by commas, also a state
               value.
        """
        ctx = Build(page="funds", route="/funds")
        cases = {
            "no options": ([ui.field("regions", kind="choices")],
                           "Page `funds`, slot `fields`: field `regions` is a choice without `options=`.\n"
                           "Give what can be chosen: `options=[\"open\", \"closed\"]`, or "
                           "`options={\"open\": \"Open\", \"closed\": \"Closed\"}` to word them."),
            "numbers": ([ui.field("regions", kind="choices", options=[1.5, 2.5])],
                        "Page `funds`, slot `fields`: field `regions` got `[1.5, 2.5]` as its options.\n"
                        "Give a list of texts or whole numbers, each once: `options=[\"open\", \"closed\"]`, or "
                        "`options={\"open\": \"Open\", \"closed\": \"Closed\"}` to word them. The handler "
                        "receives the option itself: `options=[2024, 2025]` gives an int."),
            "start": ([ui.field("regions", kind="choices", options=list(REGIONS), value="north")],
                      "Page `funds`, slot `fields`: field `regions` chooses several and starts as `'north'`.\n"
                      "`value=` is a list of options, such as `value=[\"north\"]`, or a state value."),
            "options": ([ui.field("amount", kind="money", options=["1", "2"])],
                        "Page `funds`, slot `fields`: field `amount` has `options=` and the kind `money`.\n"
                        "Only a choice has options: leave `kind=` out for one of them, or write "
                        "`kind=\"choices\"` for several."),
            "kind": ([ui.field("colour", kind="colour")],
                     "Page `funds`, slot `fields`: field `colour` has the kind `colour`.\n`kind=` is one of text, "
                     "number, money, percent, date, datetime, year, bool, choice, choices."),
        }
        for name, (fields, message) in cases.items():
            with self.subTest(case=name), self.assertRaises(PageError) as wrong:
                ui.form(title="Funds", submit=FundsState.wrong, fields=fields).build(ctx)
            self.assertEqual(str(wrong.exception), message, name)

        fixed = str(ui.fact("Regions", ["north", "south"], kind="choices").build(ctx))
        self.assertIn('"north, south"', fixed, "the chosen values, joined")
        held = str(ui.fact("Funds", FundsState.funds, kind="choices").build(ctx))
        self.assertIn("funds_state.funds_rx_state_", held)
        self.assertIn('.join(", ")', held, "a state value joined in the browser")
