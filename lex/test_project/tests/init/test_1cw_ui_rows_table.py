"""lex.ui tables: a table over a @ui.rows handler, end to end.

Intent: lex-app generates the table's state; it reads every row as the user, searches, filters,
sorts and pages them on the server, and keeps only the visible page -- cut down to the fields
the table uses -- in the state the browser receives. It reads again after a handler of the same
state, on ``ui.refresh``, and when its page opens again; it computes totals over all matching
rows, one per currency, and exports what it shows. A regression is a field the table does not
show reaching the browser, a link built from data, a table that keeps showing what a handler has
just changed, or a broken handler that breaks the page instead of the table.

Cluster 1cw -- scenarios 1.812-1.817. Type: I.
Covers: lex/lex_app/reflex/ui/pages/rows_table.py, ui/pages/tables.py (the rows table),
ui/handlers.py (reading again after an action), ui/state.py (ui_open).
Run: python -m lex pytest lex/test_project/tests/init/test_1cw_ui_rows_table.py -v
"""

from __future__ import annotations

import asyncio
from unittest.mock import patch

import pytest
from django.test import SimpleTestCase

from lex import ui
from lex.lex_app.reflex.ui import handlers, testing
from lex.lex_app.reflex.ui import state as ui_state
from lex.lex_app.reflex.ui.pages import links, rows_table
from lex.lex_app.reflex.ui.pages.parts import Build

pytestmark = pytest.mark.init

DATA: dict[str, list] = {}


def fresh() -> None:
    DATA["rows"] = [
        {"id": 1, "name": "Ada Example", "roles": ["Director", "Signatory"], "amount": "4", "currency": "EUR",
         "kind": "person", "email": "ada@example.test"},
        {"id": 2, "name": "Fund One", "roles": ["Prokura"], "amount": "10.50", "currency": "USD", "kind": "company",
         "email": "fund@example.test"},
        {"id": 3, "name": "Bob Example", "roles": [], "amount": "1.50", "currency": "EUR", "kind": "person",
         "email": "bob@example.test"}]


def person():
    """Stands for a page declared with @ui.page("/people/[key]")."""


person.__ui_page__ = links.info_of("/people/[key]", "person")


class PeopleState(ui.State):
    """A page state whose rows a table reads."""

    reads: int = 0

    @ui.rows
    def people(self):
        out = []
        for row in DATA["rows"]:
            target = ui.address(person, key=row["id"]) if row["id"] != 3 else "/somewhere/else"
            out.append({**row, "goes_to": target})
        return out

    @ui.rows
    def refusing(self):
        raise ui.Refuse("The directory is closed today.")

    @ui.rows
    def failing(self):
        raise RuntimeError("secret detail")

    @ui.rows
    def crowded(self):
        return [{"id": n, "name": f"Name {n}", "kind": f"kind {n}"} for n in range(60)]

    @ui.action
    def end(self, key: str):
        DATA["rows"] = [row for row in DATA["rows"] if str(row["id"]) != str(key)]


class OtherState(ui.State):
    """Another state, whose handler refreshes the first one's table."""

    @ui.action
    def touch(self):
        DATA["rows"] = DATA["rows"][:1]
        return ui.refresh(PeopleState.people)


def build_people_table(route: str = "/people/[key]", slot: str = "sections", listing: bool = False) -> type:
    table = ui.table(PeopleState.people, open="goes_to", search=True, filters=["kind", "roles"], export=True,
                     totals={"amount": "sum"},
                     columns=["name", ui.column("roles", kind="tags"),
                              ui.column("amount", kind="money", currency="currency"), ui.column("kind", kind="status")],
                     row_actions=lambda row: [ui.button("End role", PeopleState.end, row["id"])])
    table.build(Build(page="person", route=route, slot=slot, caption="Roles", page_search=listing))
    return rows_table._CACHE_BY_TABLE[id(table)]  # this table's state, whatever other test built one before


def simple_table(handler) -> type:
    crowded = handler is PeopleState.crowded
    ui.table(handler, columns=["name", "kind"] if crowded else ["name"], filters=["kind"] if crowded else ()).build(
        Build(page="person", route="/people/[key]", caption="People"))
    return rows_table._BY_HANDLER[rows_table.handler_key(handler)][-1]


async def page_of(harness, state) -> list[dict]:
    return [dict(row) for row in (await harness._state(state)).page_rows]


class TestCluster01cw_RowsTable(SimpleTestCase):
    """1.812-1.817: a table over a @ui.rows handler, through Reflex's own event processor."""

    def setUp(self) -> None:
        fresh()

    # -- 1.812 ---------------------------------------------------------
    def test_1_812_the_browser_gets_one_page_of_the_fields_the_table_uses(self) -> None:
        """
        Scenario 1.812: what the table holds when it appears.
        Given: a rows handler whose rows carry an e-mail address no column shows, and a link
               field that holds a ui.address on two rows and a plain string on the third.
        When:  the table appears.
        Then:  its state holds the rows with only the columns' fields, the currency field, the
               key and the link field; the e-mail address stays on the server; the plain string
               is no link; the table's state is reachable for later reads.
        """
        state = build_people_table()

        async def scenario():
            async with testing.Harness(user="ada", params={"key": "1"}) as h:
                await h.open_tab("/people/1")
                await (await h.send(state, "open"))
                return await page_of(h, state), (await h._state(state)).shown

        page, shown = asyncio.run(scenario())
        self.assertEqual([row["name"] for row in page], ["Ada Example", "Fund One", "Bob Example"], "the rows, in order")
        self.assertEqual(set(page[0]), {"id", "name", "roles", "amount", "currency", "kind", "goes_to"},
                         "only the fields the table uses reach the browser")
        self.assertNotIn("email", page[0], "a field no column shows stays on the server")
        self.assertEqual([row["goes_to"] for row in page], ["/people/1", "/people/2", ""],
                         "only an address made by ui.address is a link")
        self.assertTrue(shown, "the table knows it is on screen")

    # -- 1.813 ---------------------------------------------------------
    def test_1_813_search_filters_and_sort_run_on_the_server(self) -> None:
        """
        Scenario 1.813: search, filters, sort.
        Given: the table of 1.812.
        When:  the user searches for a tag, filters by kind and by a tag, and sorts by amount.
        Then:  a search finds a row by its tag; each filter keeps the matching rows; the filter
               choices are the column's values after "All"; the first sort of a money column is
               high to low; clearing the filters clears the dropdowns; a filter the browser sends
               on a field the table does not show is refused.
        """
        state = build_people_table()

        async def scenario():
            async with testing.Harness(user="ada", params={"key": "1"}) as h:
                await h.open_tab("/people/1")
                await (await h.send(state, "open"))
                choices = {k: [dict(c) for c in v] for k, v in dict((await h._state(state)).choices).items()}
                await (await h.send(state, "set_query", value="signatory"))
                searched = [row["name"] for row in await page_of(h, state)]
                await (await h.send(state, "set_query", value=""))
                await (await h.send(state, "set_choice", field="kind", value="person"))
                by_kind = [row["name"] for row in await page_of(h, state)]
                await (await h.send(state, "set_choice", field="kind", value=""))
                await (await h.send(state, "set_choice", field="roles", value="Prokura"))
                by_tag = [row["name"] for row in await page_of(h, state)]
                await (await h.send(state, "clear_filters"))
                chosen_after_clear = dict((await h._state(state)).chosen)
                try:
                    await (await h.send(state, "apply_filter", key="email", value={"op": "starts_with", "value": "a"}))
                except Exception:  # noqa: BLE001 - the refusal is the event's own error
                    pass
                hidden_filter = dict((await h._state(state)).filters)
                await (await h.send(state, "sort_by", key="amount"))
                ordered = [row["name"] for row in await page_of(h, state)]
                return choices, searched, by_kind, by_tag, ordered, chosen_after_clear, hidden_filter

        choices, searched, by_kind, by_tag, ordered, chosen_after_clear, hidden_filter = asyncio.run(scenario())
        self.assertEqual(searched, ["Ada Example"], "a search finds a row by one of its tags")
        self.assertEqual(by_kind, ["Ada Example", "Bob Example"], "a filter keeps the rows of that kind")
        self.assertEqual(by_tag, ["Fund One"], "a filter on tags keeps the rows that carry the tag")
        self.assertEqual([c["label"] for c in choices["kind"]], ["All", "company", "person"],
                         "the choices are the column's values after All")
        self.assertEqual(ordered, ["Fund One", "Ada Example", "Bob Example"], "money sorts high to low first")
        self.assertEqual(chosen_after_clear, {}, "clearing the filters also clears the dropdowns that show them")
        self.assertEqual(hidden_filter, {}, "the browser cannot filter on a field the table does not show")

    # -- 1.814 ---------------------------------------------------------
    def test_1_814_totals_per_currency_and_export_of_what_is_shown(self) -> None:
        """
        Scenario 1.814: totals and export.
        Given: the table of 1.812, with amounts in two currencies, filtered to persons.
        When:  the totals are computed and the table is exported.
        Then:  there is one totals row per currency over all matching rows; the export has the
               columns' labels and each amount's currency, the matching rows only, and tags joined;
               each exported column keeps its own cell type.
        """
        state = build_people_table()

        async def scenario():
            async with testing.Harness(user="ada", params={"key": "1"}) as h:
                await h.open_tab("/people/1")
                await (await h.send(state, "open"))
                totals = [dict(row) for row in (await h._state(state)).totals_rows]
                await (await h.send(state, "set_choice", field="kind", value="person"))
                export = await (await h._state(state))._export_rows("csv")
                return totals, export

        totals, (labels, lines, _about) = asyncio.run(scenario())
        self.assertEqual([(row["name"], row["amount"]) for row in totals], [("Total (EUR)", "5.50"), ("Total (USD)", "10.50")],
                         "one totals row per currency, over every matching row")
        self.assertEqual(labels, ["Name", "Roles", "Amount", "Currency", "Kind"],
                         "the export's columns are the table's labels, with each amount's currency beside it")
        self.assertEqual(lines[0]["Currency"], "EUR", "so amounts in two currencies are told apart in the file")
        state = build_people_table()
        self.assertEqual(state.export_kinds, {"Amount": "amount"},
                         "each exported column keeps its own cell type, the currency column added after money included")
        self.assertEqual([line["Name"] for line in lines], ["Ada Example", "Bob Example"], "it exports the matching rows")
        self.assertEqual(lines[0]["Roles"], "Director; Signatory", "tags are joined in a file")

    # -- 1.815 ---------------------------------------------------------
    def test_1_815_a_handler_of_the_state_or_ui_refresh_makes_the_table_read_again(self) -> None:
        """
        Scenario 1.815: reading again.
        Given: the table of 1.812 on screen.
        When:  its row button runs a handler of the same state that removes a row, and then a
               handler of another state changes the rows and returns ui.refresh.
        Then:  after each the table shows the new rows without being asked; a table that is
               not on screen does not read.
        """
        state = build_people_table()

        async def scenario():
            async with testing.Harness(user="ada", params={"key": "1"}) as h:
                await h.open_tab("/people/1")
                await (await h.send(state, "open"))
                await (await h.send(PeopleState, "end", key="2"))
                await asyncio.sleep(0.2)
                after_action = [row["name"] for row in await page_of(h, state)]
                await (await h.send(OtherState, "touch"))
                await asyncio.sleep(0.2)
                after_refresh = [row["name"] for row in await page_of(h, state)]
                await (await h.send(state, "close"))
                DATA["rows"] = []
                await (await h.send(state, "reread"))
                while_hidden = [row["name"] for row in await page_of(h, state)]
                return after_action, after_refresh, while_hidden

        after_action, after_refresh, while_hidden = asyncio.run(scenario())
        self.assertEqual(after_action, ["Ada Example", "Bob Example"], "a handler of the same state: read again")
        self.assertEqual(after_refresh, ["Ada Example"], "ui.refresh from another state: read again")
        self.assertEqual(while_hidden, [], "a table that is not on screen does not read (and keeps no row, 1.823)")

    # -- 1.816 ---------------------------------------------------------
    def test_1_816_opening_the_page_again_and_a_list_pages_search_in_the_address(self) -> None:
        """
        Scenario 1.816: the page opens again; a list page's search.
        Given: the table on a record page, and a table with search on a list page.
        When:  the page's opener runs again (another record of the same page), and the list page
               is opened with ?q= in its address.
        Then:  the opener tells the page's table to read its first page again (not a table that
               appears only after a load, which reads when it appears); the list page's table
               starts with the address's search, and typing a search writes it back.
        """
        record = build_people_table()
        listing = build_people_table(route="/people", slot="table", listing=True)

        async def scenario():
            with patch.object(ui_state, "_page_permitted", return_value=True), patch.dict(handlers._PAGES):
                handlers.register_page("/people/[key]", [], [], "")
                async with testing.Harness(user="ada", params={"key": "1"}) as h:
                    await h.open_tab("/people/1")
                    await (await h.send(record, "open"))
                    await (await h.send(record, "set_page", value=0))
                    DATA["rows"] = DATA["rows"][1:]
                    opener = await h.send(ui.State, "ui_open", page="/people/[key]")
                    await opener
                    for child in list(opener.children):
                        await child
                    reopened = [row["name"] for row in await page_of(h, record)]
                    await h.navigate("/people?q=fund")
                    await (await h.send(listing, "open", _router_data={"pathname": "/people", "asPath": "/people?q=fund",
                                                                         "query": {"q": "fund"}}))
                    listed = [row["name"] for row in await page_of(h, listing)]
                    query = (await h._state(listing)).query
                    return reopened, listed, query

        reopened, listed, query = asyncio.run(scenario())
        self.assertEqual(reopened, ["Fund One", "Bob Example"], "the page's opener makes its table read again")
        self.assertEqual(query, "fund", "a list page's table starts with the address's search")
        self.assertEqual(listed, ["Fund One"], "and shows what it finds")
        self.assertEqual(listing.query_param, "q",
                         "a list page keeps its table's search in the address as ?q=")
        self.assertEqual(record.query_param, "", "a table in a record's section does not")
        gated = ui.table(PeopleState.people, columns=["name"], filters=["name"]).build(
            Build(page="later", route="/later/[key]", caption="Later", under_load=True))
        self.assertNotIn("/later/[key]", rows_table._BY_ROUTE,
                         "a table that appears only after a load is not read again by the page's opener")
        self.assertIsNotNone(gated, "it builds all the same")

    # -- 1.817 ---------------------------------------------------------
    def test_1_817_a_broken_rows_handler_breaks_only_its_table(self) -> None:
        """
        Scenario 1.817: failures.
        Given: rows handlers that refuse, fail, and offer a filter of 60 values.
        When:  their tables appear.
        Then:  a refusal shows its own words in the table; a failure shows a message with a
               reference and never its detail; a filter of more than 50 values is the table's
               error too (the advice to search goes to the log, with the reference).
        """
        refusing, failing, crowded = (simple_table(PeopleState.refusing), simple_table(PeopleState.failing),
                                      simple_table(PeopleState.crowded))

        async def scenario():
            async with testing.Harness(user="ada", params={"key": "1"}) as h:
                await h.open_tab("/people/1")
                errors = []
                for state in (refusing, failing, crowded):
                    await (await h.send(state, "open"))
                    errors.append((await h._state(state)).error)
                return errors

        refused, failed, too_many = asyncio.run(scenario())
        self.assertEqual(refused, "The directory is closed today.", "a refusal shows its own words")
        self.assertIn("Reference:", failed, "a failure shows a reference")
        self.assertNotIn("secret detail", failed, "and never the exception's text")
        self.assertIn("Reference:", too_many, "a filter of too many values is the table's error, logged")
