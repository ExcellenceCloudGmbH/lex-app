"""lex.ui tables: the rows a table shows reach the browser and are never written to the state store.

Intent: `ui.private` keeps a tool's result (persons' names, amounts) in the server's memory only,
never in a state Reflex writes to Redis (or to disk in development). A table over a `@ui.rows`
handler that reads that value put the page it shows into its own state's `page_rows` -- and Reflex
stores that state after every event, so the names landed in Redis after all. A project with
data-protection rules ("no person in any state's pickle") needs the shown rows to reach the browser
without being stored with the state. Every lex.ui table now leaves its rows, totals and filter
choices out of what Reflex stores; the browser gets them in the event's update, and a table whose
stored state lost them reads them again when the page opens or reconnects (`restore`). A regression
is a person's name in a state pickle, or a table that shows nothing after its state was stored.

Cluster 1er -- scenarios 1.1024-1.1025, 1.1103, 1.1106. Type: U.
Covers: lex/lex_app/reflex/data.py (RowsNotStored, LexTableState), lex/lex_app/reflex/ui/pages/rows_table.py
(LexRowsTableState, go, restore_events), lex/lex_app/reflex/ui/pages/tables.py (shown_on),
lex/lex_app/reflex/ui/state.py (ui_open sends restore; ui_matches never stored).
Run: python -m lex pytest lex/test_project/tests/init/test_1er_ui_table_rows_not_stored.py -v
"""

from __future__ import annotations

import asyncio
import pickle

import pytest
from django.contrib.auth.models import Group
from django.test import SimpleTestCase

from lex import ui
from lex.lex_app.reflex import data
from lex.lex_app.reflex.ui import private, testing
from lex.lex_app.reflex.ui.pages import rows_table
from lex.lex_app.reflex.ui.pages.parts import Build
from lex.lex_app.reflex.ui.state import State

pytestmark = pytest.mark.init

PERSON, AMOUNT = "Grace Hopper", "4711.25"


class SalaryState(ui.State):
    """A tool whose result -- persons and their pay -- is private, and a table that shows it."""

    _found: list = ui.private()

    @ui.action
    def find(self):
        self._found = [{"id": 1, "name": PERSON, "pay": AMOUNT, "team": "Compilers"},
                       {"id": 2, "name": "Alan Turing", "pay": "3900.00", "team": "Theory"}]

    @ui.rows
    def people(self):
        return list(self._found) if self._found else []


ROUTE = "/salaries"


def salary_table():
    return ui.table(SalaryState.people, columns=["name", ui.column("pay", kind="money", currency="EUR"), "team"],
                    filters=["team"], totals={"pay": "sum"}, search=True)


def _every_state(root):
    yield root
    for sub in root.substates.values():
        yield from _every_state(sub)


def _stored(root) -> list[bytes]:
    """Every state of the tab as Reflex's Redis and disk managers store it, and as pickle writes it."""
    from reflex.istate.manager.token import BaseStateToken

    return ([BaseStateToken.serialize(one) for one in _every_state(root)]
            + [pickle.dumps(one) for one in _every_state(root)])


class TestCluster01er_TableRowsNotStored(SimpleTestCase):
    """1.1024-1.1025: shown rows reach the browser, never the store; read again when the store lost them."""

    def setUp(self) -> None:
        private.clear()
        self.addCleanup(private.clear)

    # -- 1.1024 --------------------------------------------------------
    def test_1_1024_a_tables_rows_reach_the_browser_and_no_stored_state(self) -> None:
        """
        Scenario 1.1024: a table over a ui.private value has shown its page.
        Given: a state whose private result names persons and their pay, and a table over it with
               search, a filter and a total; the page shown.
        When:  every state of the tab is serialised as Reflex stores it (Redis, disk) and pickled.
        Then:  the browser's updates carry the person's name and pay; no stored state holds the
               name, the pay, the total or a filter's choice; the row count is kept; a stored table
               state restored says its rows were left out, and `restore` reads them again; a table
               whose rows were not left out does nothing on `restore`.
        """
        table = salary_table()
        table.build(Build(page="salaries", route=ROUTE, caption="Salaries"))
        state = rows_table._CACHE_BY_TABLE[table]
        self.assertIn(state.restore, rows_table.restore_events(ROUTE), "the page's opener tells it to restore")

        async def scenario():
            async with testing.Harness(user="ada") as h:
                await h.open_tab(ROUTE)
                await (await h.send(SalaryState, "find"))
                await (await h.send(state, "open"))
                await asyncio.sleep(0.05)
                live = await h._state(state)
                shown = [dict(row) for row in live.page_rows]
                blobs = _stored(live._get_root_state())
                kept = pickle.loads(pickle.dumps(live))
                before = len(h.updates)
                await (await h.send(state, "restore"))            # nothing was left out of the live state
                untouched = len(h.updates) == before
                live._rows_dropped = True                         # as a state read back from the store
                live.page_rows = []
                await (await h.send(state, "restore"))
                again = [row["name"] for row in (await h._state(state)).page_rows]
                return shown, blobs, kept, str(h.updates), untouched, again

        shown, blobs, kept, updates, untouched, again = asyncio.run(scenario())
        self.assertEqual([row["name"] for row in shown], [PERSON, "Alan Turing"], "the table shows the rows")
        for value in (PERSON, AMOUNT, "Compilers"):
            self.assertIn(value, updates, f"the browser receives {value!r}")
            for blob in blobs:
                self.assertNotIn(value.encode(), blob, f"no stored state holds {value!r}")
        self.assertNotIn(b"8611.25", b"".join(blobs), "nor the total")
        self.assertEqual((kept.page_rows, kept.totals_rows, kept.choices, kept._rows_dropped, kept.total_rows),
                         ([], [], {}, True, 2), "stored: no rows, the count kept, marked as left out")
        self.assertTrue(untouched, "restore reads nothing when the rows were not left out")
        self.assertEqual(again, [PERSON, "Alan Turing"], "restore reads the rows again")

    # -- 1.1025 --------------------------------------------------------
    def test_1_1025_every_ui_table_is_not_stored_and_a_row_link_survives_the_store(self) -> None:
        """
        Scenario 1.1025: the rule for every table.
        Given: a model table built by ui.table; lex_table's state (a raw page, which no opener tells
               to restore); a rows table whose rows each open their own page.
        When:  their states are stored; a row is clicked after the store left the addresses out.
        Then:  ui.table's model table leaves its rows out and is told to restore by its page; lex_table
               stores its rows as before; the click still opens the row's own address, found by
               reading the rows again.
        """
        model = ui.table(Group, columns=["name"], visible=True)
        model.build(Build(page="groups", route="/groups", caption="Groups"))
        model_state = rows_table._CACHE_BY_TABLE[model]
        self.assertTrue(model_state.rows_not_stored)
        self.assertIn(model_state.restore, rows_table.restore_events("/groups"))
        raw = data.table_state(Group, columns=data.lex_columns(Group, ["name"]))
        self.assertFalse(raw.rows_not_stored, "lex_table on a raw page stores its rows as before")

        @ui.page("/salaries/[key]")
        def salary():
            return ui.record(title="Salary", sections=[ui.section("About", ui.text("Pay."))])

        class LinkedState(ui.State):
            _found: list = ui.private()

            @ui.action
            def find(self):
                self._found = [{"id": 7, "name": PERSON, "goes_to": ui.address(salary, key=7)}]

            @ui.rows
            def people(self):
                return list(self._found or [])

        linked = ui.table(LinkedState.people, columns=["name"], open="goes_to")
        linked.build(Build(page="linked", route="/linked", caption="Linked"))
        state = rows_table._CACHE_BY_TABLE[linked]

        async def scenario():
            async with testing.Harness(user="ada") as h:
                await h.open_tab("/linked")
                await (await h.send(LinkedState, "find"))
                await (await h.send(state, "open"))
                live = await h._state(state)
                stored = pickle.loads(pickle.dumps(live))
                live._row_to = {}                     # as the store left it
                await (await h.send(state, "go", key="7"))
                return stored._row_to, str(h.updates)

        dropped, updates = asyncio.run(scenario())
        self.assertEqual(dropped, {}, "the row's address is not stored")
        self.assertIn("/salaries/7", updates, "the click still opens the row's address")


class StaffState(ui.State):
    """A form whose person is chosen by a search over a private list of persons."""

    _staff: list = ui.private()

    @ui.action
    def find(self):
        self._staff = [{"id": 11, "label": PERSON}, {"id": 12, "label": "Alan Turing"}]

    @ui.rows
    def people(self, search):
        return [one for one in (self._staff or []) if search.lower() in one["label"].lower()]

    @ui.action
    def assign(self, values):
        return None


class TestCluster01er_MatchesNotStored(SimpleTestCase):
    """1.1103: what a searched field offers reaches the browser, never the store."""

    def setUp(self) -> None:
        private.clear()
        self.addCleanup(private.clear)

    # -- 1.1103 --------------------------------------------------------
    def test_1_1103_a_searched_fields_matches_reach_no_stored_state(self) -> None:
        """
        Scenario 1.1103: a searched choice over a ui.private value has offered its matches.
        Given: a form whose person field is searched by a @ui.rows handler reading a private list of
               persons; the user typed part of a name.
        When:  every state of the tab is serialised as Reflex stores it (Redis, disk) and pickled.
        Then:  the browser's update carries the person's name; no stored state holds it; what the form
               accepts is still known (the offered ids are stored), so a value chosen is accepted.
        """
        form = ui.form(title="Assign", submit=StaffState.assign, fields=[ui.field("person", options=StaffState.people)])
        form.build(Build(page="assign", route="/assign"))

        async def scenario():
            async with testing.Harness(user="ada") as h:
                await h.open_tab("/assign")
                await (await h.send(StaffState, "find"))
                offered = await h.search(StaffState.assign, "person", "grace")
                root = (await h._state(State))._get_root_state()
                blobs = _stored(root)
                kept = pickle.loads(pickle.dumps(await h._state(State)))
                await (await h.send(StaffState, "assign", values={"person": "11"}))
                invalid = dict((await h._state(State)).ui_invalid)
                return offered, blobs, kept, str(h.updates), invalid

        offered, blobs, kept, updates, invalid = asyncio.run(scenario())
        self.assertEqual(offered, [{"value": "11", "label": PERSON}])
        self.assertIn(PERSON, updates, "the browser is offered the person")
        for blob in blobs:
            self.assertNotIn(PERSON.encode(), blob, "no stored state holds a match")
        self.assertEqual(kept.ui_matches, {}, "the matches are left out when the state is stored")
        self.assertEqual(invalid, {}, "the value chosen is still accepted: the offered ids are kept")


class TestCluster01er_EveryTableRestores(SimpleTestCase):
    """1.1106: a table reads its rows again after a reconnect on every page, routed or not."""

    def setUp(self) -> None:
        private.clear()
        self.addCleanup(private.clear)

    # -- 1.1106 --------------------------------------------------------
    def test_1_1106_a_table_on_a_dashboard_or_in_a_raw_page_restores_its_rows(self) -> None:
        """
        Scenario 1.1106: a reconnect where no opener runs.
        Given: the salary table in ui.embed inside a raw Reflex page, and in a @ui.dashboard (neither has
               lex.ui's opener, which tells a @ui.page's tables to restore); a model table in ui.embed.
        When:  each page is compiled with Reflex's page compiler; and a stored state of the table (its rows
               left out) receives what the page sends when it is hydrated again.
        Then:  each compiled table sends its own `restore` whenever the page is hydrated (Reflex's
               is_hydrated, which a reconnect turns off and on again), so the rows come back without an
               opener; restore reads them again.
        """
        import re

        import reflex as rx
        from reflex.compiler.compiler import compile_page

        embedded = salary_table()
        raw_page = rx.box(rx.heading("Salaries"), ui.embed(embedded, name="Salaries"))
        _, raw_module = compile_page("/raw-salaries", raw_page)

        @ui.dashboard
        def main():
            return ui.list(title="Salaries", table=salary_table())

        _, dashboard_module = compile_page("/", main())
        groups = ui.table(Group, columns=["name"])
        _, model_module = compile_page("/raw-groups", ui.embed(groups, name="Groups"))
        for module, table in ((raw_module, embedded), (dashboard_module, embedded), (model_module, groups)):
            state = rows_table._CACHE_BY_TABLE[table]
            restore = f"{state.get_full_name()}.restore"
            found = re.search(r"const (on_hydrated_\w+) = useCallback\([^\n]*" + re.escape(f'"{restore}"'), module)
            self.assertIsNotNone(found, f"{state.__name__} sends its own restore")
            hydrated = "reflex___state____state.is_hydrated_rx_state_"
            self.assertIn(f"useEffect(() => {{ if (!{hydrated}) return; ({found.group(1)})(); }}, [{hydrated}]);", module,
                          "each time the page has been hydrated, so a reconnect sends it again")

        state = rows_table._CACHE_BY_TABLE[embedded]

        async def scenario():
            async with testing.Harness(user="ada") as h:
                await h.open_tab("/raw-salaries")
                await (await h.send(SalaryState, "find"))
                await (await h.send(state, "open"))
                live = await h._state(state)
                stored = pickle.loads(pickle.dumps(live))
                live.page_rows, live._rows_dropped = stored.page_rows, stored._rows_dropped
                await (await h.send(state, "restore"))
                return [row["name"] for row in (await h._state(state)).page_rows]

        self.assertEqual(asyncio.run(scenario()), [PERSON, "Alan Turing"], "restore reads the rows again")
