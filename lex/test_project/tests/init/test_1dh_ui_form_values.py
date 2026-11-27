"""lex.ui forms: what was entered reaches the handler as Python values, or as a message at its field.

Intent: a consultant writes `values["amount"] * 2` and `values["due"] > date.today()`. For that
the browser's strings must arrive as a `Decimal` and a `date`, checked on the server, before the
handler's body runs: a form that hands over `"12,5"` or lets an empty required field through makes
every handler validate by hand, differently each time. `kind=` is one word for showing and for
entering a value, so `ui.fact` takes the same kinds. A regression is a text where a number was
promised, a wrong value that reaches the body, a message nobody can act on, or one handler
silently receiving two different sets of fields.

Cluster 1dh -- scenarios 1.833-1.836. Type: U.
Covers: lex/lex_app/reflex/ui/forms.py (clean, check, register), lex/lex_app/reflex/ui/pages/vocab.py
(shape: the kinds date, year, bool, choice).
Run: python -m lex pytest lex/test_project/tests/init/test_1dh_ui_form_values.py -v
"""

from __future__ import annotations

import datetime
from decimal import Decimal

import pytest
from django.test import SimpleTestCase

from lex import ui
from lex.lex_app.reflex.ui import forms
from lex.lex_app.reflex.ui.forms import Expect, Receives
from lex.lex_app.reflex.ui.pages.parts import Build

pytestmark = pytest.mark.init


class ValuesState(ui.State):
    """A state whose handlers receive values."""

    done: bool = False
    due: str = "2026-12-31"
    year: int = 2026

    @ui.action
    def create(self, values):
        return None

    @ui.action
    def other(self, values):
        return None


class TestCluster01dh_FormValues(SimpleTestCase):
    """1.833-1.836: a field's kind decides the Python value; problems; one set of fields; display."""

    # -- 1.833 ---------------------------------------------------------
    def test_1_833_each_kind_reaches_the_handler_as_its_python_value(self) -> None:
        """
        Scenario 1.833: what the handler receives.
        Given: one field of every kind, and what a browser sends for it.
        When:  the values are checked.
        Then:  text is stripped text; a number is an int, or a Decimal when it has decimals;
               money and percent are Decimals; a date a date; a date and time is in the app's
               time zone; a year an int; a box a bool; a choice its text. Nothing entered is ""
               for text, False for a box and None otherwise; a value already of its kind is
               kept; what the page did not declare is dropped.
        """
        cases = (
            ("text", "  Ada  ", "Ada"), ("number", "3", 3), ("number", "3.50", Decimal("3.50")),
            ("money", "1250.5", Decimal("1250.5")), ("percent", "0.0525", Decimal("0.0525")),
            ("date", "2026-12-31", datetime.date(2026, 12, 31)), ("year", "2026", 2026),
            ("bool", "on", True), ("bool", False, False), ("choice", "open", "open"),
            ("money", Decimal("7"), Decimal("7")), ("date", datetime.date(2026, 1, 2), datetime.date(2026, 1, 2)),
            ("text", "", ""), ("bool", None, False), ("number", "", None), ("date", None, None), ("choice", " ", None),
        )
        for kind, sent, expected in cases:
            with self.subTest(kind=kind, sent=sent):
                value, problem = forms.clean(Expect("f", kind), sent)
                self.assertEqual((value, problem), (expected, ""), f"{kind}: {sent!r}")
                self.assertIs(type(value), type(expected), f"{kind}: the Python type the handler can compute with")
        moment, problem = forms.clean(Expect("at", "datetime"), "2026-12-31T10:30")
        self.assertEqual((moment.isoformat(), problem), ("2026-12-31T10:30:00+01:00", ""),
                         "a date and time typed without a zone is meant in the app's (Europe/Berlin)")
        values, problems = forms.check(Receives(fields=(Expect("name"), Expect("amount", "money"))),
                                       {"name": "Ada", "amount": "5", "role": "admin"})
        self.assertEqual((values, problems), ({"name": "Ada", "amount": Decimal("5")}, {}),
                         "only the fields the page declared reach the handler")

    # -- 1.834 ---------------------------------------------------------
    def test_1_834_a_wrong_or_missing_value_is_a_message_for_its_field(self) -> None:
        """
        Scenario 1.834: required and kind, checked on the server.
        Given: required fields left empty, and values that are not of their field's kind.
        When:  they are checked.
        Then:  each has a sentence that says what to enter; a choice outside its options is
               refused, also when the options are a field of the handler's state; not-a-number
               and infinity are no numbers; a box that must be ticked says so.
        """
        cases = (
            (Expect("name", required=True), "", "Enter a value."),
            (Expect("due", "date", required=True), None, "Enter a value."),
            (Expect("status", "choice", required=True, options=("open",)), "", "Choose one."),
            (Expect("terms", "bool", required=True), False, "Tick this box to continue."),
            (Expect("amount", "money"), "12,5", "Enter a number, such as 1250.50."),
            (Expect("count", "number"), "NaN", "Enter a number, such as 1250.50."),
            (Expect("count", "number"), "Infinity", "Enter a number, such as 1250.50."),
            (Expect("count", "number"), True, "Enter a number, such as 1250.50."),
            (Expect("due", "date"), "31.12.2026", "Enter a date."),
            (Expect("at", "datetime"), "soon", "Enter a date and a time."),
            (Expect("year", "year"), "26", "Enter a year with four digits, such as 2026."),
            (Expect("year", "year"), "2026.0", "Enter a year with four digits, such as 2026."),
            (Expect("status", "choice", options=("open", "closed")), "deleted", "Choose one of the options."),
        )
        for expect, sent, message in cases:
            with self.subTest(field=expect.key, sent=sent):
                self.assertEqual(forms.clean(expect, sent)[1], message, f"{expect.kind}: {sent!r}")

        class Holder:
            kinds = ["a", "b"]

        receives = Receives(fields=(Expect("kind", "choice", options_field="kinds"),))
        self.assertEqual(forms.check(receives, {"kind": "c"}, Holder())[1], {"kind": "Choose one of the options."},
                         "options held in the state are checked against the state")
        self.assertEqual(forms.check(receives, {"kind": "b"}, Holder()), ({"kind": "b"}, {}))
        self.assertEqual(forms.check(receives, "not a dict")[0], {"kind": None}, "anything but a dictionary is empty")

    # -- 1.835 ---------------------------------------------------------
    def test_1_835_a_handler_receives_one_set_of_fields(self) -> None:
        """
        Scenario 1.835: which handler receives what.
        Given: a handler registered as receiving two fields.
        When:  it is registered again with the same fields, and then with others.
        Then:  the same fields are accepted (a page is built more than once); other fields are
               refused, naming the handler and where the first set was declared; a handler
               nothing was registered for receives nothing.
        """
        first = Receives(fields=(Expect("name", required=True), Expect("amount", "money")), where="page `new_order`")
        forms.register(ValuesState.create, first)
        forms.register(ValuesState.create, Receives(fields=first.fields, where="page `new_order`"))
        self.assertIs(forms.of(ValuesState.create.fn.__ui_fn__).fields, first.fields)
        with self.assertRaises(ValueError) as twice:
            forms.register(ValuesState.create, Receives(fields=(Expect("name"),), where="page `other`"))
        self.assertEqual(str(twice.exception),
                         "`ValuesState.create` already receives the fields of page `new_order`, which are not the "
                         "same as these. A handler receives one set of fields: give this form or button a handler "
                         "of its own.")
        self.assertIsNone(forms.of(ValuesState.other.fn.__ui_fn__), "a handler no form names receives nothing")

    # -- 1.836 ---------------------------------------------------------
    def test_1_836_a_fact_shows_the_kinds_a_field_takes(self) -> None:
        """
        Scenario 1.836: kind= means the same for showing and for entering.
        Given: facts of the kinds that came with forms: date, year, bool and choice.
        When:  they are built.
        Then:  a date is formatted by RFDS for the user's locale; a year is shown without a
               thousands separator; a yes/no value as the translated words Yes and No; a choice
               as it is; an unknown kind is refused with the kinds there are.
        """
        ctx = Build(page="order")
        self.assertIn("rfdsFmt", str(ui.fact("Due", ValuesState.due, kind="date").build(ctx)), "RFDS formats the date")
        year = str(ui.fact("Year", ValuesState.year, kind="year").build(ctx))
        self.assertNotIn("RfdsValue", year, "a year is no quantity: RFDS's number format would write 2,026")
        self.assertIn("year_rx_state_", year)
        self.assertIn('"2026"', str(ui.fact("Year", 2026, kind="year").build(ctx)))
        yes_no = str(ui.fact("Done", ValuesState.done, kind="bool").build(ctx))
        self.assertIn('rfdsI18n.t("Yes", "Yes"', yes_no)
        self.assertIn('rfdsI18n.t("No", "No"', yes_no)
        self.assertIn('"open"', str(ui.fact("Status", "open", kind="choice").build(ctx)))
        with self.assertRaises(ValueError) as unknown:
            ui.fact("Colour", "red", kind="colour")
        self.assertEqual(str(unknown.exception), "kind= must be one of text, number, money, percent, date, datetime, "
                                                 "year, bool, choice, choices; got 'colour'")
