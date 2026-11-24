"""lex.ui tables: @ui.rows, a handler that returns a table's rows; ui.address; ui.refresh.

Intent: a table over rows that are not a lex model gets them from a plain function on the page's
state, read as the user and off the event loop, on a read-only copy of the state; a row that
leads to a page of its own carries an address built in Python by ``ui.address``; a handler of
another state makes a table read again with ``ui.refresh``. A regression is a rows handler that
changes the state it reads, runs without its declared permission, returns something a table
cannot show without saying so, or a link built from a string in the data.

Cluster 1cv -- scenarios 1.808-1.811. Type: I.
Covers: lex/lex_app/reflex/ui/rows.py, ui/pages/links.py (address), ui/results.py (refresh),
ui/testing.py (rows).
Run: python -m lex pytest lex/test_project/tests/init/test_1cv_ui_rows_handler.py -v
"""

from __future__ import annotations

import pytest
from django.contrib.auth.models import Group
from django.test import SimpleTestCase

from lex import ui
from lex.lex_app.reflex.ui import results, rows, testing
from lex.lex_app.reflex.ui.pages import links, rows_table

pytestmark = pytest.mark.init

PEOPLE = [{"id": 1, "name": "Ada Example", "kind": "person"}, {"id": 2, "name": "Fund One", "kind": "company"},
          {"id": 3, "name": "Bob Example", "kind": "person"}]


def person():
    """Stands for a page declared with @ui.page("/people/[key]", params=["tab"])."""


person.__ui_page__ = links.info_of("/people/[key]", "person", ["tab"])


class RowsState(ui.State):
    """A state with the rows handlers the scenarios read."""

    label: str = "people"

    @ui.rows
    def people(self):
        return [{**row, "seen_by": self.user.name, "label": self.label} for row in PEOPLE]

    @ui.rows
    def found(self, search: str):
        return [row for row in PEOPLE if search.casefold() in row["name"].casefold()][:1]

    @ui.rows
    def writes(self):
        self.label = "changed"
        return []

    @ui.rows
    def broken(self):
        return {"id": 1}

    @ui.rows(requires=ui.read(Group))
    def groups(self):
        return [{"id": 1, "name": "x"}]


class TestCluster01cv_RowsHandler(SimpleTestCase):
    """1.808-1.811: declaring @ui.rows; reading rows; ui.address; ui.refresh."""

    # -- 1.808 ---------------------------------------------------------
    def test_1_808_a_rows_handler_is_a_plain_function_that_tables_read(self) -> None:
        """
        Scenario 1.808: declaring @ui.rows.
        Given: plain functions on a ui.State, some declared wrongly.
        When:  they are decorated with @ui.rows.
        Then:  a plain function with no argument, or with `search` alone, is a rows handler; a
               generator, an async function and any other argument are refused with what to write;
               the browser cannot send it as an event; `lex reflex check` accepts it.
        """
        self.assertTrue(rows.is_rows(RowsState.people), "a plain function is a rows handler")
        self.assertFalse(rows.takes_search(RowsState.people), "without `search`, lex-app does the search")
        self.assertTrue(rows.takes_search(RowsState.found), "with `search`, the handler searches itself")
        self.assertIs(rows.owner_of(RowsState.people), RowsState, "a rows handler knows its state")

        def generator(self):
            yield []

        async def asynchronous(self):
            return []

        def arguments(self, fund: int):
            return []

        cases = {
            "generator": (generator, "returns its rows; it does not yield"),
            "async": (asynchronous, "takes a plain function"),
            "arguments": (arguments, "takes no arguments, or `search` alone; got fund"),
        }
        for name, (fn, words) in cases.items():
            with self.subTest(case=name), self.assertRaises(TypeError) as wrong:
                ui.rows(fn)
            self.assertIn(words, str(wrong.exception), f"{name}: refused with what to write")
        with self.assertRaises(RuntimeError) as sent:
            RowsState.people.fn(object())
        self.assertIn("it is not an event to send", str(sent.exception), "the browser cannot run a rows handler")

        from lex.lex_app.reflex.ui import checks

        self.assertIn("ui.rows", checks._UI_HANDLERS, "lex reflex check knows @ui.rows as a handler")

    # -- 1.809 ---------------------------------------------------------
    def test_1_809_rows_are_read_as_the_user_on_a_read_only_copy(self) -> None:
        """
        Scenario 1.809: reading rows.
        Given: rows handlers that read the user and a field, search for themselves, write a
               field, return a dict, and require a permission.
        When:  ui.testing.rows reads them, as a table would.
        Then:  the rows carry the user and the field; lex-app searches and filters a handler
               without `search`, and a handler with `search` gets the text; writing a field fails
               the read and leaves the field as it was; a dict is refused with what to return;
               a missing permission refuses the read (a PermissionError in a test); given the
               table, the rows come back as its browser gets them.
        """
        read = testing.rows(RowsState.people, user="ada")
        self.assertEqual([(r["name"], r["seen_by"], r["label"]) for r in read][:1], [("Ada Example", "ada", "people")],
                         "the handler runs as the user and reads the state's fields")
        self.assertEqual([r["name"] for r in testing.rows(RowsState.people, search="example")],
                         ["Ada Example", "Bob Example"], "lex-app searches the rows of a handler without `search`")
        self.assertEqual([r["name"] for r in testing.rows(RowsState.people, filters={"kind": "company"})],
                         ["Fund One"], "and applies the filters")
        self.assertEqual([r["name"] for r in testing.rows(RowsState.found, search="bob")], ["Bob Example"],
                         "a handler with `search` receives the text and does the search")
        with self.assertRaises(AttributeError) as wrote:
            testing.rows(RowsState.writes)
        self.assertEqual(str(wrote.exception), "RowsState.label: `@ui.rows` reads; change the state in a `@ui.load` "
                                               "or an `@ui.action`.", "a write to the state fails the read")
        with self.assertRaises(rows.RowsError) as shape:
            testing.rows(RowsState.broken)
        self.assertIn("returned dict; a @ui.rows handler returns a list of dicts", str(shape.exception),
                      "a value that is not a list says what to return")
        with self.assertRaises(PermissionError, msg="a rows handler that requires a permission does not run without it"):
            testing.rows(RowsState.groups)
        self.assertEqual(testing.rows(RowsState.groups, grants=["auth.Group:read"]), [{"id": 1, "name": "x"}],
                         "and runs with it")
        shown = testing.rows(RowsState.people, table=ui.table(RowsState.people, columns=["name"], search=True), search="bob")
        self.assertEqual(shown, [{"id": "3", "name": "Bob Example"}],
                         "with table=, the rows come back as that table's browser gets them: its fields only")

    # -- 1.810 ---------------------------------------------------------
    def test_1_810_an_address_is_built_in_python_from_a_page(self) -> None:
        """
        Scenario 1.810: ui.address.
        Given: a page with a route argument and a query parameter.
        When:  addresses are built for it.
        Then:  the argument and the query value are encoded into the page's route; an empty key
               gives no address; a value the page does not take, a missing argument, and
               something that is not a page are refused.
        """
        self.assertEqual(ui.address(person, key="a/b c", tab="roles"), "/people/a%2Fb%20c?tab=roles",
                         "the values are encoded: a value cannot change the page or add a parameter")
        self.assertIsInstance(ui.address(person, key=7), links.Address, "an address is marked as lex-app's own")
        self.assertEqual(ui.address(person, key=None), "", "a row without a key is not a link")
        for name, call in {"unknown value": lambda: ui.address(person, key=1, colour="red"),
                           "missing argument": lambda: ui.address(person),
                           "not a page": lambda: ui.address(len, key=1)}.items():
            with self.subTest(case=name), self.assertRaises(TypeError):
                call()

    # -- 1.811 ---------------------------------------------------------
    def test_1_811_refresh_names_rows_handlers_and_becomes_their_tables_events(self) -> None:
        """
        Scenario 1.811: ui.refresh.
        Given: a rows handler with a table registered for it.
        When:  a handler returns ui.refresh(handler).
        Then:  the result is accepted beside ui.toast; it becomes the event that makes that
               table read again, and is itself that list of events for a raw Reflex handler;
               something that is not a rows handler is refused.
        """
        state = rows_table.state_for(RowsState.people, columns=[{"field": "name", "header": "Name", "kind": "text"}],
                                     row_fields=["id", "name"], needed=("name",))
        found, _ = results.split([ui.toast("Saved."), ui.refresh(RowsState.people)], handler="X.save")
        self.assertEqual(len(found), 2, "ui.refresh is a result like ui.toast")
        events = results.to_events([found[1]])
        self.assertIn(state.reread, events, "it becomes the table's read-again event")
        self.assertIn(state.reread, list(ui.refresh(RowsState.people)),
                      "and is itself that list of events, so a raw Reflex handler can return it as it is")
        with self.assertRaises(TypeError, msg="only rows handlers are refreshed"):
            ui.refresh(RowsState.label)
