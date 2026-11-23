"""lex.ui tables: what a column and a table refuse, and a model table narrowed with where=.

Intent: everything about a table that can be checked before a row is read stops the app from
starting, with the page, the slot and what to write; a model table is narrowed to the record on
the page with ``where=`` and reads through lex-app's API, so the model's permissions decide which
rows a user sees. A regression is a table that breaks only in the browser, a column kind drawn
differently from page to page, or a model table that cannot be narrowed without leaving the API.

Cluster 1cx -- scenarios 1.818-1.820. Type: U.
Covers: lex/lex_app/reflex/ui/pages/tables.py (column, table, the checks, the model table),
lex/lex_app/reflex/data.py (the filter dropdown's choice).
Run: python -m lex pytest lex/test_project/tests/init/test_1cx_ui_table_checks.py -v
"""

from __future__ import annotations

import pytest
from django.contrib.auth.models import Group, User
from django.test import SimpleTestCase

from lex import ui
from lex.lex_app.reflex.ui.pages.parts import Build, PageError

pytestmark = pytest.mark.init

CTX = Build(page="fund", route="/funds/[key]", slot="sections[0]", caption="Holdings")


def mount_of(component) -> object:
    """The on_mount event of the first component under ``component`` that has one."""
    found = getattr(component, "event_triggers", {}).get("on_mount")
    if found is not None:
        return found
    for child in [*getattr(component, "children", []), getattr(component, "comp1", None)]:
        if child is not None and (inner := mount_of(child)) is not None:
            return inner
    return None


class CheckDesk(ui.State):
    """A state with a rows handler and a field a model table is narrowed by."""

    group: int = 0

    @ui.rows
    def holdings(self):
        return []

    @ui.action
    def end(self, key: str):
        self.group = 0


class TestCluster01cx_TableChecks(SimpleTestCase):
    """1.818-1.820: ui.column's checks; ui.table's refusals; a model table with where=."""

    # -- 1.818 ---------------------------------------------------------
    def test_1_818_a_column_says_what_it_is_and_is_checked_when_declared(self) -> None:
        """
        Scenario 1.818: ui.column.
        Given: columns of each kind, with a currency code, a currency field, tones, a link
               field and a show= function, and columns declared wrongly.
        When:  they are declared and built into a rows table.
        Then:  each kind is accepted; a currency code and a currency field are told apart; tones
               go with status only; show= and kind= are not given together; a link field must be
               a field name; a wrong kind names the kinds.
        """
        for kind in ("text", "number", "money", "percent", "date", "datetime", "status", "tag", "tags"):
            with self.subTest(kind=kind):
                self.assertEqual(ui.column("x", kind=kind).kind, kind, f"{kind} is a column kind")
        table = ui.table(CheckDesk.holdings, columns=[
            "name", ui.column("amount", kind="money", currency="EUR"), ui.column("net", kind="money", currency="ccy"),
            ui.column("state", kind="status", tones={"Open": "info"}), ui.column("fund", open="fund_page"),
            ui.column("portal", show=lambda row: ui.status(row["portal"], tone="success"))])
        built = str(table.build(CTX))
        self.assertIn("rfds-data-table", built, "a rows table is RFDS's data table")
        self.assertIn('rfdsI18n.t("Open", "Open"', built, "a toned status word is translated like every sentence")
        wrong = {
            "kind": (dict(kind="colour"), "must be one of text, number, money, percent, date, datetime, status, tag, tags"),
            "tones without status": (dict(kind="text", tones={"a": "info"}), "tones=) goes with kind=\"status\""),
            "a tone": (dict(kind="status", tones={"a": "red"}), "takes the tones"),
            "show and kind": (dict(kind="text", show=lambda row: ui.text("x")), "give kind= or show=, not both"),
            "open": (dict(open="/funds/1"), "names the row's field that holds a ui.address"),
            "currency": (dict(kind="money", currency="E-R"), "takes a code such as \"EUR\""),
            "a code in small letters": (dict(kind="money", currency="eur"), "a code is written in capitals"),
        }
        for name, (options, words) in wrong.items():
            with self.subTest(case=name), self.assertRaises(ValueError) as refused:
                ui.column("x", **options)
            self.assertIn(words, str(refused.exception), f"{name}: says what to write")

    # -- 1.819 ---------------------------------------------------------
    def test_1_819_a_table_declared_wrongly_names_the_page_the_slot_and_what_to_write(self) -> None:
        """
        Scenario 1.819: ui.table's refusals.
        Given: tables declared wrongly in each way the spec lists.
        When:  they are built (as when the pages are registered).
        Then:  each is a PageError naming the page and the slot, saying what it got and what to
               write.
        """
        rows = CheckDesk.holdings
        cases = {
            "not a source": (lambda: ui.table(CheckDesk.end, columns=["name"]),
                             "Return the rows from a `@ui.rows` handler on the state"),
            "where on rows": (lambda: ui.table(rows, columns=["name"], where={"fund": 1}),
                              "Filter inside the handler."),
            "filter not a column": (lambda: ui.table(rows, columns=["name"], filters=["kind"]),
                                    "`filters=` names kind, which is not a column."),
            "total not a column": (lambda: ui.table(rows, columns=["name"], totals={"amount": "sum"}),
                                   "`totals=` names amount, which is not a column."),
            "total kind": (lambda: ui.table(rows, columns=["name", "amount"], totals={"amount": "count"}),
                           "A total is the \"sum\" of a number, money or percent column"),
            "sum of text": (lambda: ui.table(rows, columns=["name", "amount"], totals={"amount": "sum"}),
                            "`totals=` sums amount, which is not a number."),
            "show returns": (lambda: ui.table(rows, columns=["name", ui.column("x", show=lambda row: "x")]),
                             "Return one of `ui.text(...)`, `ui.status(...)`, `ui.link(...)`."),
            "row actions return": (lambda: ui.table(rows, columns=["name"], row_actions=lambda row: [ui.text("x")]),
                                   "`row_actions=` returned `ui.text`."),
            "search without text": (lambda: ui.table(rows, columns=[ui.column("n", kind="number")], search=True),
                                    "the table has no text column to search"),
            "first column drawn": (lambda: ui.table(rows, columns=[ui.column("roles", kind="tags"), "name"]),
                                   "is a `tags` column.\nThe first column names the row and is the row's link"),
            "model where": (lambda: ui.table(Group, columns=["name"], where={"colour": 1}),
                            "`where=` names colour, which Group does not have."),
            "model export": (lambda: ui.table(Group, columns=["name"], export=True),
                             "a model table cannot export or total yet."),
            "model link from data": (lambda: ui.table(Group, columns=["name", ui.column("id", open="name")]),
                                     "A model's field is data and never becomes a link."),
            "id column beside another key": (lambda: ui.table(rows, columns=["name", "id"], key="code"),
                                             "a column is called `id`, and the table's key is another field."),
            "model filter": (lambda: ui.table(Group, columns=["name"], filters=["name"]),
                             "`filters=` names name, whose values are not a fixed set."),
            "no columns": (lambda: ui.table(rows), "the table has no columns."),
        }
        for name, (make, words) in cases.items():
            with self.subTest(case=name), self.assertRaises(PageError) as refused:
                make().build(CTX)
            message = str(refused.exception)
            self.assertTrue(message.startswith("Page `fund`, slot `sections[0]`: "),
                            f"{name}: names the page and the slot: {message}")
            self.assertIn(words, message, f"{name}: says what it got and what to write")
        for name, call in {"columns": lambda: ui.table(rows, columns="name"),
                           "filters": lambda: ui.table(rows, columns=["name"], filters="kind"),
                           "totals": lambda: ui.table(rows, columns=["name"], totals=["amount"]),
                           "row actions": lambda: ui.table(rows, columns=["name"], row_actions="x")}.items():
            with self.subTest(case=name), self.assertRaises(TypeError, msg=f"{name}: refused when declared"):
                call()

    # -- 1.820 ---------------------------------------------------------
    def test_1_820_a_model_table_is_narrowed_with_where_and_searched_through_the_api(self) -> None:
        """
        Scenario 1.820: a model table with where=, search and a filter.
        Given: a model table narrowed by a state value, with search, and a model table filtered
               on a field with a fixed set of values.
        When:  they are built.
        Then:  the table's state receives the where= values when it appears (a state value
               included); the toolbar has a search box; a filter offers the field's choices after
               "All"; two tables narrowed differently never share a state; a column keeps its
               field's kind; the field a row button reads reaches the browser; building the same
               table twice finds one state class; its state takes only its where= keys and its own
               filters, while lex_table's own state still filters on any field; search covers a
               shown text column only.
        """
        component = ui.table(Group, columns=["name"], where={"id": CheckDesk.group}, search=True, visible=True).build(CTX)
        mount = str(mount_of(component))
        self.assertIn("check_desk", mount, "the where= value is the state's field, read when the table appears")
        self.assertIn("group", mount, "the field itself, not its value at build time")
        self.assertIn("set_query", str(component), "the toolbar has a search box")
        filtered = str(ui.table(User, columns=["username", "is_active"], filters=["is_active"]).build(CTX))
        self.assertIn('"All"', filtered, "a filter starts with All")
        self.assertIn('"true"', filtered, "and offers the yes/no field's values")
        self.assertIn("set_choice", filtered, "a choice is sent to the table's state")
        from lex.lex_app.reflex import data

        one = data.table_state(Group, columns=[{"field": "name"}], filters={"id": 1})
        other = data.table_state(Group, columns=[{"field": "name"}], filters={"id": 2})
        self.assertIsNot(one, other, "two tables narrowed differently never share rows")
        self.assertTrue(hasattr(one, "set_choice"), "a model table's filter dropdown has its event")
        kinds = str(ui.table(User, columns=["username", "date_joined"], visible=True).build(CTX))
        self.assertIn("instant", kinds, "a model column keeps the kind its field gives it (a date-time)")

        def make():
            return ui.table(User, columns=["username", ui.column("is_active", kind="status", tones={"true": "success"})],
                            row_actions=lambda row: [ui.button("Lock", CheckDesk.end, row["email"])], visible=True)

        make().build(CTX)
        before = len(data._STATE_CACHE)
        make().build(CTX)
        self.assertEqual(len(data._STATE_CACHE), before, "building a page twice finds one state class")
        state = next(cls for cls in data._STATE_CACHE.values() if "email" in getattr(cls, "row_fields", ()))
        self.assertIn("email", state.row_fields, "the field a row button reads reaches the browser")
        plain = data.table_state(Group, columns=[{"field": "name"}])
        self.assertIsNone(plain.choice_fields, "lex_table's own state still filters on any field, as before")
        narrowed_state = next(cls for cls in data._STATE_CACHE.values() if getattr(cls, "scope_fields", None) == ("id",))
        self.assertEqual(narrowed_state.choice_fields, (), "a ui.table without filters= accepts no filter from the browser")
        with self.assertRaises(PageError, msg="a model table searches a shown text column, or refuses search"):
            ui.table(User, columns=["date_joined"], search=True).build(CTX)
