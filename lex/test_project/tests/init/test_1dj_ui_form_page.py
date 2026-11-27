"""lex.ui pages: the form kind -- fields, one submit, a result; a wizard; a tool.

Intent: most pages of a working application are tools: a few inputs, a run that may take a
while, a result with a table and a file. `ui.form` is that page in a dozen lines: the author
lists `ui.field`s and names the handler; lex-app draws RFDS's input for each kind, sends the
form once, shows the server's message at the field, the handler running, a job's progress, and
the result after a success. A regression is a field whose value is sent while the user types, a
submit button that can be pressed twice, a result that shows before there is one, or an error
that does not say which page and slot is wrong and what to write.

Cluster 1dj -- scenarios 1.842-1.846. Type: U.
Covers: lex/lex_app/reflex/ui/pages/forms.py (field, step, form, Field, Form),
lex/lex_app/reflex/ui/pages/kinds.py (Kind.build, _buttons), lex/lex_app/reflex/ui/pages/declare.py.
Run: python -m lex pytest lex/test_project/tests/init/test_1dj_ui_form_page.py -v
"""

from __future__ import annotations

import datetime

import pytest
import reflex as rx
from django.test import SimpleTestCase

from lex import ui
from lex.lex_app.reflex.ui import forms as rules
from lex.lex_app.reflex.ui import handlers
from lex.lex_app.reflex.ui.pages import declare
from lex.lex_app.reflex.ui.pages.parts import Build, PageError

pytestmark = pytest.mark.init


class InvoicesState(ui.State):
    """An entry form's state and a tool's."""

    name: str = ""
    currencies: list[str] = ["EUR", "USD"]
    _lines: list[dict] = []

    @ui.load
    def open(self):
        self.name = "Invoice one"

    @ui.action
    def create(self, values):
        return None

    @ui.action
    def update(self, values):
        return None

    @ui.action
    def rename(self, values):
        return None

    @ui.job
    def build_report(self, values):
        yield ui.progress(0.5, "Reading invoices")
        self._lines = [{"id": 1, "name": "Invoice one"}]

    @ui.rows
    def lines(self):
        return self._lines

    @ui.action
    def download(self):
        return ui.download(data=b"name\n", filename="invoices.csv")

    @ui.action
    def enrol(self, values):
        return None

    @ui.action
    def plain(self):
        return None

    @ui.action
    def keyed(self, key, values):
        return None

    @ui.action
    def choose(self, values):
        return None

    @ui.load
    def other_load(self):
        return None


class OtherState(ui.State):
    kinds: list[str] = ["a"]


CTX = Build(page="new_invoice", route="/invoices/new")


def _text(component: rx.Component) -> str:
    """The built page as the browser gets it: its elements, and the functions its forms call."""
    return str(component) + "\n".join(component._get_all_hooks())


def _every_kind():
    return ui.form(title="New invoice", submit=InvoicesState.create, fields=[
        ui.field("name", required=True, help="As printed on the invoice."),
        ui.field("lines", label="Number of lines", kind="number"),
        ui.field("amount", kind="money", currency="USD"),
        ui.field("discount", kind="percent"),
        ui.field("due_date", kind="date", value=datetime.date(2026, 12, 31)),
        ui.field("sent_at", kind="datetime"),
        ui.field("year", kind="year", value=2026),
        ui.field("paid", kind="bool"),
        ui.field("status", options={"open": "Open", "closed": "Closed"}),
        ui.field("currency", options=InvoicesState.currencies),
        ui.field("country", options=[f"C{number}" for number in range(13)]),
    ])


class TestCluster01dj_FormPage(SimpleTestCase):
    """1.842-1.846: the form page; a tool; a wizard; its load and registration; its errors."""

    # -- 1.842 ---------------------------------------------------------
    def test_1_842_a_form_page_draws_each_field_and_sends_the_form_once(self) -> None:
        """
        Scenario 1.842: ui.form with one field of every kind.
        Given: a form with a field of each kind -- one required with help, one with its own
               label, a choice among two worded options, one among a state's values and one
               among thirteen -- and a handler called `create`.
        When:  it is built.
        Then:  the page is RFDS's page header over RFDS's form at form width; each kind is
               drawn by RFDS's input for it; a label defaults to the key with a capital; help
               and the required mark are there; every field shows the server's message kept
               under the handler and its key; few fixed options are a dropdown, many or a
               state's a box to type in; the submit button is called after the handler and
               shows it running; submitting sends the whole form to the handler as `values`,
               and no field has a handler of its own on the server.
        """
        built = ui.form(title="New invoice", submit=InvoicesState.rename, fields=[ui.field("name")]).build(CTX)
        small = _text(built)
        self.assertIn('"data-rfds-width":"form"', small, "a form is narrow")
        self.assertIn("rfds-page-header", small, "RFDS's page header")
        self.assertIn('rfdsI18n.t("New invoice", "New invoice"', small, "the title, translated")
        self.assertIn('"data-slot":"rfds-form"', small, "RFDS's form")
        text = _text(_every_kind().build(CTX))
        drawn = {"name": "text_input", "lines": "number_input", "amount": "money_input", "discount": "percent_input",
                 "due_date": "date_input", "sent_at": "field", "year": "text_input", "paid": "checkbox",
                 "status": "select", "currency": "combobox", "country": "combobox"}
        for key, component in drawn.items():
            with self.subTest(field=key):
                self.assertIn(f'"data-rfds-component":"{component}"', text, f"{key} is RFDS's {component}")
                self.assertIn(f'htmlFor:"invoicesstate-create-{key}"', text, f"{key}'s label names its input")
                self.assertIn(f'ui_invalid_rx_state_?.["InvoicesState.create|{key}"]', text,
                              f"{key} shows the server's message for it")
        for needle, why in (('rfdsI18n.t("Due date", "Due date"', "a label defaults to the key with a capital"),
                            ('rfdsI18n.t("Number of lines", "Number of lines"', "label= words it otherwise"),
                            ('rfdsI18n.t("As printed on the invoice.", "As printed on the invoice."', "help="),
                            ("rfds-field-required", "the required mark"),
                            ('type:"datetime-local"', "a day and a time is the browser's own input"),
                            ('currency:"USD"', "money in its currency"),
                            ('defaultValue:"2026"', "value= is where a field starts"),
                            ('"2026-12-31"', "a date's start value"),
                            ('rfdsI18n.t("Open", "Open"', "a worded option is translated"),
                            ('rfdsI18n.t("Create", "Create"', "the submit button is called after the handler"),
                            ('type:"submit"', "it submits the form"),
                            ('ui_running_rx_state_?.["InvoicesState.create"]', "and shows the handler running")):
            with self.subTest(needle=needle):
                self.assertIn(needle, text, why)
        self.assertIn('invoices_state.create", ({ ["values"] : form_data })', text,
                      "submitting sends the whole form to the handler, as its values")
        self.assertEqual(text.count("ReflexEvent("), 1, "nothing else on the page reaches the server: no field does")
        received = rules.of(InvoicesState.create.fn.__ui_fn__)
        self.assertEqual([(one.key, one.kind) for one in received.fields][:3],
                         [("name", "text"), ("lines", "number"), ("amount", "money")], "the handler is told its fields")
        by_key = {one.key: one for one in received.fields}
        self.assertEqual((by_key["status"].options, by_key["currency"].options_field, by_key["name"].required),
                         (("open", "closed"), "currencies", True), "with what the server checks")

    # -- 1.843 ---------------------------------------------------------
    def test_1_843_a_tool_is_a_form_with_a_result(self) -> None:
        """
        Scenario 1.843: a job as submit, a table and a download as the result.
        Given: a form whose submit is a job, with a result of a sentence, a table of the job's
               rows under a heading and a download button, and two actions.
        When:  it is built.
        Then:  the job's progress and a Stop button show while it runs; the result sits behind
               the handler's result flag, outside the narrow column; its table reads the rows
               handler; its section has its heading; the page's actions are in the header and
               none of them is the primary button -- that is the submit button.
        """
        page = ui.form(
            title="Invoice report", submit=InvoicesState.build_report, fields=[ui.field("year", kind="year")],
            result=[ui.text("The report is ready."),
                    ui.section("Lines", ui.table(InvoicesState.lines, columns=["name"])),
                    ui.button("Download", InvoicesState.download)],
            actions=[ui.button("Edit", InvoicesState.plain), ui.link("Help", href="https://example.org/help")])
        text = _text(page.build(Build(page="invoice_report", route="/invoices/report")))
        for needle, why in (('rfdsI18n.t("Build report", "Build report"', "the button is called after the job"),
                            ("rfds-progress", "the job's progress"),
                            ('ui_progress_rx_state_?.["InvoicesState.build_report"]', "its fraction"),
                            ('ui_progress_message_rx_state_?.["InvoicesState.build_report"]', "its message"),
                            ('rfdsI18n.t("Stop", "Stop"', "a way to stop it"),
                            ('ui_cancel", ({ ["key"] : "InvoicesState.build_report" })', "which cancels the job"),
                            ('ui_done_rx_state_?.["InvoicesState.build_report"]', "the result waits for a success"),
                            ('rfdsI18n.t("The report is ready.", "The report is ready."', "the result's sentence"),
                            ('rfdsI18n.t("Lines", "Lines"', "the result's section heading"),
                            ("rfds-data-table", "the result's table"),
                            ('rfdsI18n.t("Download", "Download"', "the result's button"),
                            ("rfds-menu-item-label", "two actions are entries of the header's menu")):
            with self.subTest(needle=needle):
                self.assertIn(needle, text, why)
        self.assertEqual(text.count('"data-rfds-intent":"primary"'), 1, "one primary button on the page: the submit")
        result = text[text.index('ui_done_rx_state_?.["InvoicesState.build_report"]'):]
        self.assertIn('"data-rfds-width":"full"', result, "the result takes the page's width")
        self.assertNotIn("rfds-progress", _text(ui.form(title="New", submit=InvoicesState.rename,
                                                        fields=[ui.field("name")]).build(CTX)),
                         "an action has no progress to show")

    # -- 1.844 ---------------------------------------------------------
    def test_1_844_steps_make_the_same_form_a_wizard(self) -> None:
        """
        Scenario 1.844: ui.form(steps=[ui.step(...)]).
        Given: a wizard of two steps.
        When:  it is built.
        Then:  RFDS's stepper names the steps and follows the step kept for the handler; both
               steps' fields are in the one form, each step hidden unless it is the current
               one; the button reads Continue until the last step and then the handler's name;
               Back appears after the first step; the handler is told which step holds which
               field.
        """
        page = ui.form(title="Enrol", submit=InvoicesState.enrol, steps=[
            ui.step("Who", fields=[ui.field("name", required=True)]),
            ui.step("Where", fields=[ui.field("street"), ui.field("city")])])
        text = _text(page.build(Build(page="enrol", route="/enrol")))
        for needle, why in (("rfds-stepper", "RFDS's stepper"), ('"Who"', "the first step's title"),
                            ('"Where"', "the second step's title"),
                            ('ui_step_rx_state_?.["InvoicesState.enrol"]', "the step is kept for the handler"),
                            ("hidden:", "a step that is not the current one is hidden"),
                            ('rfdsI18n.t("Continue", "Continue"', "Continue until the last step"),
                            ('rfdsI18n.t("Enrol", "Enrol"', "then the handler's name"),
                            ('rfdsI18n.t("Back", "Back"', "Back"),
                            ('ui_step_back", ({ ["key"] : "InvoicesState.enrol" })', "which goes one step back")):
            with self.subTest(needle=needle):
                self.assertIn(needle, text, why)
        self.assertEqual(text.count('"data-slot":"rfds-form"'), 1, "one form: it is sent whole, once")
        self.assertEqual(rules.of(InvoicesState.enrol.fn.__ui_fn__).steps, (("name",), ("street", "city")))

    # -- 1.845 ---------------------------------------------------------
    def test_1_845_a_form_waits_for_its_load_and_is_a_page_like_any_other(self) -> None:
        """
        Scenario 1.845: load= and @ui.page.
        Given: an edit form declared with @ui.page, whose load fills the state a field starts from.
        When:  the pages are registered.
        Then:  the form is registered under its route with its load as the page's required
               load; the page shows a skeleton until the load has finished; the field starts
               with the state's value; and a result section with a load of its own is one of
               the page's part loads.
        """
        @declare.page("/invoices/[key]/edit")
        def edit_invoice():
            return ui.form(title=InvoicesState.name, load=InvoicesState.open, submit=InvoicesState.update,
                           fields=[ui.field("name", value=InvoicesState.name, required=True)])

        try:
            declare.finalize()
            entry = handlers.page_of("/invoices/[key]/edit")
            self.assertEqual([one.fn.__qualname__ for one in entry.required], ["InvoicesState.open"])
        finally:
            declare._DECLARED.pop("/invoices/[key]/edit")
        text = _text(edit_invoice().build(Build(page="edit_invoice", route="/invoices/[key]/edit")))
        self.assertIn('ui_loaded_rx_state_?.["InvoicesState.open"]', text, "the page waits for its load")
        self.assertIn("rfds-skeleton", text, "with a skeleton")
        self.assertIn("defaultValue:reflex___state____state__lex___lex_app___reflex___ui___state____state__"
                      "lex___test_project___tests___init___test_1dj_ui_form_page____invoices_state.name_rx_state_",
                      text, "the field starts with what the load read")
        tool = ui.form(title="Report", submit=InvoicesState.rename, fields=[ui.field("name")],
                       result=[ui.section("Later", ui.text("x"), load=InvoicesState.other_load)])
        self.assertEqual(tool.loads(), ([], [InvoicesState.other_load]))

    # -- 1.846 ---------------------------------------------------------
    def test_1_846_a_form_declared_wrongly_says_the_page_the_slot_and_what_to_write(self) -> None:
        """
        Scenario 1.846: the build-time errors.
        Given: forms with a field key used twice, an unknown kind, a file field, options on a
               field that is no choice, a choice without options or with options that are not
               texts, options from another state, a currency on a text, both fields and steps,
               neither, a wizard of one step, a step without fields, no submit, a button, a
               load or a handler without `values` as submit, a raw component as a field, and a
               handler that already receives other fields; and a field put in a section.
        When:  each is built.
        Then:  each is a PageError that names the page and the slot and says what to write.
        """
        ok = [ui.field("name")]
        cases = {
            "twice": (dict(fields=[ui.field("name"), ui.field("name")]),
                      "Page `new_invoice`, slot `fields`: two fields have the key `name`.\n"
                      "The handler finds each value under its field's key: give each field its own."),
            "kind": (dict(fields=[ui.field("colour", kind="colour")]),
                     "Page `new_invoice`, slot `fields`: field `colour` has the kind `colour`.\n`kind=` is one of text, "
                     "number, money, percent, date, datetime, year, bool, choice, choices."),
            "file": (dict(fields=[ui.field("scan", kind="file")]),
                     "Page `new_invoice`, slot `fields`: field `scan` is a file.\nA file field is not built yet: an "
                     "upload is a `ui.custom_block(..., reason=\"...\")`."),
            "options": (dict(fields=[ui.field("amount", kind="money", options=["1", "2"])]),
                        "Page `new_invoice`, slot `fields`: field `amount` has `options=` and the kind `money`.\n"
                        "Only a choice has options: leave `kind=` out for one of them, or write "
                        "`kind=\"choices\"` for several."),
            "choice": (dict(fields=[ui.field("status", kind="choice")]),
                       "Page `new_invoice`, slot `fields`: field `status` is a choice without `options=`.\n"
                       "Give what can be chosen: `options=[\"open\", \"closed\"]`, or "
                       "`options={\"open\": \"Open\", \"closed\": \"Closed\"}` to word them."),
            "numbers": (dict(fields=[ui.field("status", options=[1.5, 2.5])]),
                        "Page `new_invoice`, slot `fields`: field `status` got `[1.5, 2.5]` as its options.\n"
                        "Give a list of texts or whole numbers, each once: `options=[\"open\", \"closed\"]`, or "
                        "`options={\"open\": \"Open\", \"closed\": \"Closed\"}` to word them. The handler "
                        "receives the option itself: `options=[2024, 2025]` gives an int."),
            "elsewhere": (dict(fields=[ui.field("kind", options=OtherState.kinds)]),
                          "Page `new_invoice`, slot `fields`: field `kind` takes its options from another state.\n"
                          "The options are a fixed list, or a field of the state whose handler receives the form: "
                          "lex-app checks the chosen value against it."),
            "currency": (dict(fields=[ui.field("name", currency="EUR")]),
                         "Page `new_invoice`, slot `fields`: field `name` has `currency=` and the kind `text`.\n"
                         "`currency=` goes with `kind=\"money\"`."),
            "both": (dict(fields=ok, steps=[ui.step("A", fields=ok)]),
                     "Page `new_invoice`, slot `fields`: a form has `fields=` or `steps=`, one of them.\n"
                     "Write `fields=[ui.field(\"name\"), ...]`, or for a wizard "
                     "`steps=[ui.step(\"Details\", fields=[...]), ...]`."),
            "neither": (dict(), "Page `new_invoice`, slot `fields`: the form has no fields.\n"
                                "Write `fields=[ui.field(\"name\"), ...]`, or for a wizard "
                                "`steps=[ui.step(\"Details\", fields=[...]), ...]`."),
            "one step": (dict(steps=[ui.step("A", fields=ok)]),
                         "Page `new_invoice`, slot `steps`: a wizard with one step.\nOne step is a form: write "
                         "`fields=[...]`."),
            "empty step": (dict(steps=[ui.step("A", fields=ok), ui.step("B", fields=[])]),
                           "Page `new_invoice`, slot `steps[1]`: the step `B` has no fields.\n"
                           "Give it `fields=[ui.field(...)]`, or leave the step out."),
            "step twice": (dict(steps=[ui.step("A", fields=ok), ui.step("B", fields=[ui.field("name")])]),
                           "Page `new_invoice`, slot `steps[1].fields`: two fields have the key `name`.\n"
                           "The handler finds each value under its field's key: give each field its own."),
            "raw": (dict(fields=[rx.text("Name")]),
                    "Page `new_invoice`, slot `fields`: got a raw component (`Text`).\n`fields=` holds "
                    "`ui.field(key, label=, kind=, options=, required=, help=, value=, currency=, lines=)`."),
            "no submit": (dict(fields=ok, submit=None),
                          "Page `new_invoice`, slot `submit`: the form has no `submit=`.\n"
                          "Name the handler that receives the values: `submit=OrdersState.create`."),
            "button": (dict(fields=ok, submit=ui.button("Save", InvoicesState.create)),
                       "Page `new_invoice`, slot `submit`: `submit=` got a `ui.button`.\nName the handler itself: "
                       "`submit=OrdersState.create`. The button is called after it (\"Create\")."),
            "load": (dict(fields=ok, submit=InvoicesState.open),
                     "Page `new_invoice`, slot `submit`: got `InvoicesState.open`, which is an `@ui.load`.\n"
                     "The values go to a handler declared with `@ui.action` or `@ui.job`: "
                     "`def create(self, values): ...`."),
            "no values": (dict(fields=ok, submit=InvoicesState.plain),
                          "Page `new_invoice`, slot `submit`: `InvoicesState.plain` takes (self).\n"
                          "It receives what was entered as its last argument, called `values`: "
                          "`def plain(self, values): ...`; `values[\"name\"]` is the field `ui.field(\"name\")`."),
            "more": (dict(fields=ok, submit=InvoicesState.keyed),
                     "Page `new_invoice`, slot `submit`: `InvoicesState.keyed` takes (self, key, values).\n"
                     "It receives what was entered as its last argument, called `values`: "
                     "`def keyed(self, values): ...`; `values[\"name\"]` is the field `ui.field(\"name\")`."),
        }
        for name, (slots, message) in cases.items():
            with self.subTest(case=name), self.assertRaises(PageError) as wrong:
                ui.form(**{"title": "New invoice", "submit": InvoicesState.choose, **slots}).build(CTX)
            self.assertEqual(str(wrong.exception), message, name)
        ui.form(title="One", submit=InvoicesState.choose, fields=[ui.field("name")]).build(Build(page="one", route="/one"))
        with self.assertRaises(PageError) as taken:
            ui.form(title="Two", submit=InvoicesState.choose, fields=[ui.field("title")]).build(Build(page="two", route="/two"))
        self.assertEqual(str(taken.exception),
                         "Page `two`, slot `submit`: `InvoicesState.choose` already receives the fields of page `one`, "
                         "which are not the same as these.\nA handler receives one set of fields: give this one a "
                         "handler of its own.")
        with self.assertRaises(PageError) as loose:
            ui.record(title="Invoice", sections=[ui.section("Details", ui.field("name"))]).build(CTX)
        self.assertIn("slot `sections[0]`: got `ui.field`", str(loose.exception), "a field belongs to a form")
        for bad in ("", "_secret", "two words", 3):
            with self.subTest(key=bad), self.assertRaises(TypeError, msg="a key is a name the handler can use"):
                ui.field(bad)
        with self.assertRaises(TypeError):
            ui.step("", fields=ok)
