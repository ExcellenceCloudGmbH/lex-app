"""lex.ui tables: columns a reader relies on -- the name pinned and given room, a code kept
narrow, columns chosen and sized by the user -- every row in one scroll, and a totals row that
says what it sums.

Intent: a wide table of customers or orders is read sideways and down. The name stays in place
while the user scrolls to the right; a name gets room and a short code does not take it; the user
can hide, order and size the columns (RFDS's "Columns" menu: a width typed in, never a drag); a
file's few hundred lines are read in one scroll rather than 25 at a time, up to the 200 rows RFDS
lets a table hold in the browser, and past that the table still pages; and a totals row says
whether it sums the rows the search left or every row. A regression is a name that scrolls away,
a row that cannot be reached, a width that only breaks in the browser, a user's column choice
that hides the row's name, or a total that silently changes meaning when the user searches.

Cluster 1dy -- scenarios 1.940-1.946, 1.953-1.957, 1.1126, 1.1129, 1.1130. Type: U.
Covers: lex/lex_app/reflex/ui/pages/tables.py (column kind="name"/"code", pin=; table page_size=,
choose_columns=, totals=, row_actions=, export=), lex/lex_app/reflex/ui/pages/vocab.py (a row's button and link, a menu
entry that says why it cannot be chosen), lex/lex_app/reflex/ui/pages/rows_table.py (page size, totals over all
rows, their label), lex/lex_app/reflex/data.py (ChosenColumns, the model table's page size).
Run: python -m lex pytest lex/test_project/tests/init/test_1dy_ui_table_columns_rows.py -v
"""

from __future__ import annotations

import asyncio

import pytest
from django.contrib.auth.models import Group
from django.test import SimpleTestCase
from reflex.compiler.compiler import compile_page

from lex import ui
from lex.lex_app.reflex.ui import testing
from lex.lex_app.reflex.ui.pages import rows_table
from lex.lex_app.reflex.ui.pages.parts import Build, PageError

pytestmark = pytest.mark.init

DATA = {"count": 3}


class CustomersState(ui.State):
    """A customers list read from a file: a name, a short code, a region and an amount."""

    @ui.rows
    def customers(self):
        return [{"id": n, "name": f"Customer {n:03}", "code": f"C{n:03}", "region": "North" if n % 2 else "South",
                 "amount": "1.00", "currency": "EUR" if n % 3 else "USD"} for n in range(1, DATA["count"] + 1)]


#: The search texts the searching customers handler was given, in order.
SEARCHED: list = []


class SearchingState(ui.State):
    """Customers searched by the handler itself (a ranked search in another system)."""

    @ui.rows
    def customers(self, search):
        SEARCHED.append(search)
        rows = [{"id": n, "name": f"Customer {n:03}", "amount": "1.00"} for n in range(1, 7)]
        return [row for row in rows if search.casefold() in row["name"].casefold()]


class ClosingFundsState(ui.State):
    """Funds with actions on each row: one dangerous, which says per row why it cannot be done."""

    @ui.rows
    def funds(self):
        return [{"id": 1, "name": "Alder", "why_not": ""}, {"id": 2, "name": "Birch", "why_not": "Already closed."}]

    @ui.action
    def close(self, key):
        return None

    @ui.action
    def edit(self, key):
        return None


CTX = Build(page="customers", route="/customers", caption="Customers")


def built(table) -> tuple[type, str]:
    text = str(table.build(CTX))
    return rows_table._CACHE_BY_TABLE[table], text


def spec_of(state: type, field: str) -> dict:
    return next(dict(spec) for spec in state.table_columns if spec["field"] == field)


class TestCluster01dy_TableColumnsRows(SimpleTestCase):
    """1.940-1.946: pinned and sized columns, the Columns menu, every row at once, totals that say what they sum."""

    def setUp(self) -> None:
        DATA["count"] = 3

    # -- 1.940 ---------------------------------------------------------
    def test_1_940_the_name_stays_in_place_and_has_room_and_a_code_stays_narrow(self) -> None:
        """
        Scenario 1.940: kind="name" and kind="code" on a column; the first column stays in place.
        Given: a rows table whose first column, the name, is of kind name, with a column of kind
               code; and a model table whose first field is the name, and one whose field is of
               kind name.
        When:  they are built.
        Then:  RFDS's column spec of the name says sticky at the start, RFDS's entity kind and its
               wide field width; the code is RFDS's identifier kind at its narrow field width; a
               plain column has no width and is not pinned; the table's cells carry RFDS's sticky
               marker; the model table's first field keeps its own label and kind and stays in
               place; a model field of kind name keeps its label and gets the room.
        """
        state, text = built(ui.table(CustomersState.customers, columns=[
            ui.column("name", kind="name"), ui.column("code", kind="code"), "region"]))
        name, code, region = (spec_of(state, field) for field in ("name", "code", "region"))
        self.assertEqual((name.get("sticky"), name["kind"], name.get("width")),
                         ("start", "entity", "var(--rfds-size-field-md)"),
                         "the name stays in place while the table scrolls sideways, and has RFDS's wide width")
        self.assertEqual((code.get("sticky"), code["kind"], code.get("width")), (None, "id", "var(--rfds-size-field-xs)"),
                         "a short code is RFDS's identifier, narrow")
        self.assertEqual((region.get("sticky"), region.get("width")), (None, None),
                         "a plain column takes the room its values need and scrolls")
        self.assertIn('"data-rfds-sticky":"start"', text, "RFDS marks the pinned header and cells")

        groups = ui.table(Group, columns=["name"])
        text = str(groups.build(Build(page="groups", route="/groups", caption="Groups")))
        spec = spec_of(rows_table._CACHE_BY_TABLE[groups], "name")
        self.assertEqual((spec.get("sticky"), spec["header"], spec["kind"]), ("start", "Name", "text"),
                         "a model table's first field stays in place and keeps its own label and kind")
        self.assertIn('"data-rfds-sticky":"start"', text, "RFDS marks it")
        named = ui.table(Group, columns=["id", ui.column("name", kind="name")])
        named.build(Build(page="groups", route="/groups", caption="Groups"))
        spec = spec_of(rows_table._CACHE_BY_TABLE[named], "name")
        self.assertEqual((spec["header"], spec["kind"], spec.get("width")), ("Name", "entity", "var(--rfds-size-field-md)"),
                         "a model's field of kind name keeps its label and gets the room")

    # -- 1.941 ---------------------------------------------------------
    def test_1_941_a_column_takes_no_width(self) -> None:
        """
        Scenario 1.941: a column takes no width.
        Given: a width given to a column.
        When:  the column is declared.
        Then:  it is refused: a column has no width argument; its kind says what the values are,
               and the kinds name and code set the room. (Which column stays in place is 1.955.)
        """
        with self.assertRaises(TypeError):
            ui.column("name", width="wide")
        self.assertIn("``name`` (a person's or a company's name: given room", ui.column.__doc__,
                      "the kinds that set a width are documented on ui.column")

    # -- 1.942 ---------------------------------------------------------
    def test_1_942_the_user_chooses_orders_and_sizes_the_columns(self) -> None:
        """
        Scenario 1.942: choose_columns=True.
        Given: a rows table with choose_columns=True, and a model table with it.
        When:  they are built, and the user hides, reorders and sizes columns -- among them
               unknown columns, the row's name, widths too small or not numbers.
        Then:  the toolbar holds RFDS's column chooser and the table follows the chosen columns,
               order and widths; the name cannot be hidden or moved from first place; only the
               table's own columns are kept, widths only from 40 to 1,200 pixels; a table
               without choose_columns has no chooser; the model table has the chooser too.
        """
        state, text = built(ui.table(CustomersState.customers, columns=["name", "code", "region"],
                                     choose_columns=True))
        self.assertIn("column_chooser", text, "RFDS's Columns menu is in the toolbar")
        plain_state, plain = built(ui.table(CustomersState.customers, columns=["name", "region"]))
        self.assertNotIn("column_chooser", plain, "without choose_columns= there is none")

        async def scenario():
            async with testing.Harness(user="ada") as h:
                await h.open_tab("/customers")
                await (await h.send(state, "open"))
                await (await h.send(state, "set_view_columns", ids=["region", "secret", "code"]))
                await (await h.send(state, "set_column_order", ids=["region", "name", "secret", "code"]))
                await (await h.send(state, "set_column_widths",
                                    widths={"code": 120, "region": 12, "name": "wide", "secret": 300, "amount": 90}))
                table = await h._state(state)
                return list(table.view_columns), list(table.column_order), dict(table.column_widths)

        shown, order, widths = asyncio.run(scenario())
        self.assertEqual(shown, ["name", "region", "code"], "the name is always shown; an unknown column is dropped")
        self.assertEqual(order, ["name", "region", "code"], "the name stays first; an unknown column is dropped")
        self.assertEqual(widths, {"code": 120}, "a width from 40 to 1,200 pixels of a column of the table")

        model = str(ui.table(Group, columns=["name"], choose_columns=True).build(
            Build(page="groups", route="/groups", caption="Groups")))
        self.assertIn("column_chooser", model, "a model table offers the Columns menu too")

    # -- 1.943 ---------------------------------------------------------
    def test_1_943_page_size_sets_the_rows_a_page_up_to_every_row(self) -> None:
        """
        Scenario 1.943: page_size=.
        Given: rows tables with page_size=50, page_size=200 and none given; a model table with
               page_size=200; and page sizes that are not a number of rows from 1 to 200.
        When:  they are built.
        Then:  50 opens at 50 rows, among the sizes the user can pick; none given is 25 as before;
               200 opens at 200 -- RFDS's limit for a table in the browser, every row of a file in
               one scroll; a model table does the same; a wrong size is refused with what to write
               (the pager is 1.954's).
        """
        fifty, _ = built(ui.table(CustomersState.customers, columns=["name"], page_size=50))
        default, _ = built(ui.table(CustomersState.customers, columns=["name", "code"]))
        every, _ = built(ui.table(CustomersState.customers, columns=["name", "region"], page_size=200))

        def opens_at(state: type) -> int:
            return state.get_fields()["page_size"].default

        self.assertEqual((opens_at(fifty), 50 in fifty.page_size_options), (50, True), "50 a page, and offered")
        self.assertEqual(opens_at(default), 25, "the default stays 25 a page")
        self.assertEqual(opens_at(every), 200, "every row: as many as RFDS lets a table hold in the browser")
        groups = ui.table(Group, columns=["name"], page_size=200)
        groups.build(Build(page="groups", route="/groups", caption="Groups"))
        self.assertEqual(opens_at(rows_table._CACHE_BY_TABLE[groups]), 200, "a model table the same way")
        for wrong in (0, 201, "all", True, 2.5, None):
            with self.assertRaisesRegex(ValueError, r"page_size=\) takes a number of rows from 1 to 200"):
                ui.table(CustomersState.customers, columns=["name"], page_size=wrong)

    # -- 1.944 ---------------------------------------------------------
    def test_1_944_every_row_in_one_scroll_and_past_200_the_rest_is_still_reached(self) -> None:
        """
        Scenario 1.944: reading a table with page_size=200.
        Given: a file of 150 rows, then one of 250.
        When:  the table appears, and the user moves to the next page of the second.
        Then:  the 150 rows are on one page; of the 250, the first 200 are on the first page and
               the last 50 on the next: no row is out of reach.
        """
        state, _ = built(ui.table(CustomersState.customers, columns=["name", "amount"], page_size=200))

        async def scenario():
            async with testing.Harness(user="ada") as h:
                await h.open_tab("/customers")
                DATA["count"] = 150
                await (await h.send(state, "open"))
                small = (len((await h._state(state)).page_rows), (await h._state(state)).total_rows)
                DATA["count"] = 250
                await (await h.send(state, "reread"))
                first = (len((await h._state(state)).page_rows), (await h._state(state)).total_rows)
                await (await h.send(state, "set_page", value=1))
                rest = [row["name"] for row in (await h._state(state)).page_rows]
                return small, first, rest

        small, first, rest = asyncio.run(scenario())
        self.assertEqual(small, (150, 150), "a file of 150 rows is read in one scroll")
        self.assertEqual(first, (200, 250), "past 200 the first page holds 200 and the count says 250")
        self.assertEqual((len(rest), rest[0], rest[-1]), (50, "Customer 201", "Customer 250"),
                         "the rest is on the next page")

    # -- 1.945 ---------------------------------------------------------
    def test_1_945_a_totals_row_says_what_it_sums_and_can_sum_every_row(self) -> None:
        """
        Scenario 1.945: totals= "sum", over the matching rows or (totals_of_all=True, 1.953) every row.
        Given: a searchable table with a region filter whose amounts are in two currencies,
               totalled once over the matching rows and once over all rows.
        When:  the table appears, and the user picks a region and searches.
        Then:  with nothing narrowing the rows both say "Total"; once a filter or a search narrows
               them, "sum" totals the rows left and says "Total of matching rows", and with
               totals_of_all=True keeps the total of every row and says "Total of all rows" -- each
               per currency; another word is refused with what to write.
        """
        DATA["count"] = 6
        columns = ["name", "region", ui.column("amount", kind="money", currency="currency")]
        matching, _ = built(ui.table(CustomersState.customers, columns=columns, search=True, filters=["region"],
                                     totals={"amount": "sum"}))
        every, _ = built(ui.table(CustomersState.customers, columns=columns, search=True, filters=["region"],
                                  totals={"amount": "sum"}, totals_of_all=True))

        async def totals_of(state):
            async with testing.Harness(user="ada") as h:
                await h.open_tab("/customers")

                async def now():
                    return [(row["name"], row["amount"]) for row in (await h._state(state)).totals_rows]

                await (await h.send(state, "open"))
                seen = {"all": await now()}
                await (await h.send(state, "set_choice", field="region", value="North"))
                seen["north"] = await now()
                await (await h.send(state, "clear_filters"))
                await (await h.send(state, "set_query", value="Customer 001"))
                seen["search"] = await now()
                return seen

        sums = asyncio.run(totals_of(matching))
        alls = asyncio.run(totals_of(every))
        everything = [("Total (EUR)", "4.00"), ("Total (USD)", "2.00")]
        self.assertEqual(sums["all"], everything, "nothing narrows the rows: Total, per currency")
        self.assertEqual(alls["all"], everything, "the same for the total of all rows")
        self.assertEqual(sums["north"], [("Total of matching rows (EUR)", "2.00"), ("Total of matching rows (USD)", "1.00")],
                         "a filter on: the rows left, and the label says so")
        self.assertEqual(sums["search"], [("Total of matching rows", "1.00")],
                         "a search too; one currency left needs no code")
        self.assertEqual(alls["north"], [("Total of all rows (EUR)", "4.00"), ("Total of all rows (USD)", "2.00")],
                         "totals_of_all=True keeps every row, and says so")
        self.assertEqual(alls["search"], [("Total of all rows (EUR)", "4.00"), ("Total of all rows (USD)", "2.00")],
                         "also while searching")

        other = ui.table(CustomersState.customers, totals={"amount": "average"},
                         columns=["name", ui.column("amount", kind="money")])
        with self.assertRaises(PageError) as raised:
            other.build(CTX)
        self.assertIn("A total is the \"sum\" of a number, money or percent column", str(raised.exception))

    # -- 1.946 ---------------------------------------------------------
    def test_1_946_a_table_with_all_of_it_compiles_to_a_real_page(self) -> None:
        """
        Scenario 1.946: compiled with Reflex's page compiler.
        Given: a list page whose table has a pinned name with room, a narrow code, the Columns menu,
               every row at once and a total of all rows.
        When:  Reflex compiles it.
        Then:  the module is built, with RFDS's sticky column, column chooser and pagination.
        """
        page = ui.list(title="Customers", table=ui.table(
            CustomersState.customers, search=True, choose_columns=True, page_size=200,
            totals={"amount": "sum"}, totals_of_all=True,
            columns=[ui.column("name", kind="name"), ui.column("code", kind="code"),
                     ui.column("amount", kind="money", currency="currency")]))
        _, module = compile_page("/customers/all", page.build(Build(page="customers", route="/customers/all")))
        for expected in ('"data-rfds-sticky":"start"', "column_chooser", "rfds-pagination"):
            self.assertIn(expected, module, f"compiled: {expected}")

    # -- 1.953 ---------------------------------------------------------
    def test_1_953_a_total_of_every_row_is_one_table_option_read_once(self) -> None:
        """
        Scenario 1.953: `totals={"amount": "sum"}, totals_of_all=True`.
        Given: a rows handler that searches by itself, its table totalling every row; the old
               per-column sentence "sum of all rows"; totals_of_all= without totals=, or not a bool.
        When:  the table appears and the user types three searches.
        Then:  the total stays the total of every row, "Total of all rows" while searching; every
               row is read once, when the table reads its rows -- not again on each keystroke;
               the old sentence is refused saying to write `totals_of_all=True`; the option
               without totals, or not True/False, is refused with what to write.
        """
        SEARCHED.clear()
        state, _ = built(ui.table(SearchingState.customers, search=True, totals={"amount": "sum"},
                                  totals_of_all=True, columns=["name", ui.column("amount", kind="number")]))

        async def scenario():
            async with testing.Harness(user="ada") as h:
                await h.open_tab("/customers")
                await (await h.send(state, "open"))
                for text in ("Customer 00", "Customer 001", "Customer 002"):
                    await (await h.send(state, "set_query", value=text))
                return [(row["name"], row["amount"]) for row in (await h._state(state)).totals_rows]

        totals = asyncio.run(scenario())
        self.assertEqual(totals, [("Total of all rows", "6.00")], "every row's total, and the label says so")
        self.assertEqual(SEARCHED.count(""), 1, "every row was read once, not on each keystroke")
        self.assertEqual([one for one in SEARCHED if one], ["Customer 00", "Customer 001", "Customer 002"])

        old = ui.table(CustomersState.customers, totals={"amount": "sum of all rows"},
                       columns=["name", ui.column("amount", kind="money")])
        with self.assertRaises(PageError) as raised:
            old.build(CTX)
        self.assertIn('`totals={"amount": "sum"}, totals_of_all=True`', str(raised.exception))
        alone = ui.table(CustomersState.customers, totals_of_all=True, columns=["name", ui.column("amount", kind="money")])
        with self.assertRaises(PageError) as raised:
            alone.build(CTX)
        self.assertIn("`totals_of_all=True` and no `totals=`", str(raised.exception))
        with self.assertRaisesRegex(TypeError, r"ui.table\(totals_of_all=\) takes True or False"):
            ui.table(CustomersState.customers, totals={"amount": "sum"}, totals_of_all="yes", columns=["name"])

    # -- 1.954 ---------------------------------------------------------
    def test_1_954_the_pager_shows_only_when_the_rows_do_not_fit_one_page(self) -> None:
        """
        Scenario 1.954: `page_size=` is a number; the pager comes when it is needed.
        Given: rows and model tables with the default page size, and `page_size=200`; the old
               `page_size=None`.
        When:  they are built and Reflex compiles them.
        Then:  no table carries RFDS's always-on pager; each has RFDS's pagination, with its
               rows-per-page choice, shown only while there are more rows than a page holds;
               `page_size=None` is refused saying to write `page_size=200`.
        """
        for table, ctx in ((ui.table(CustomersState.customers, columns=["name"]), CTX),
                           (ui.table(CustomersState.customers, columns=["name"], page_size=200), CTX),
                           (ui.table(Group, columns=["name"]), Build(page="groups", route="/groups", caption="Groups"))):
            with self.subTest(table=table.page_size, source=str(table.source)):
                text = str(table.build(ctx))
                self.assertNotIn('"data-slot":"rfds-pager-size"', text, "no pager when every row fits")
                self.assertIn("rfds-pagination", text, "RFDS's pagination")
                self.assertRegex(text, r"\.total_rows_rx_state_ > [\w.]+\.page_size_rx_state_",
                                 "shown only while the rows do not fit one page")
                self.assertIn("Rows per page", text, "with its rows-per-page choice")
        with self.assertRaisesRegex(ValueError, r"page_size=\) takes a number of rows from 1 to 200; got None\. "
                                                r"For every row on one page write page_size=200"):
            ui.table(CustomersState.customers, columns=["name"], page_size=None)

    # -- 1.955 ---------------------------------------------------------
    def test_1_955_the_first_column_always_stays_in_place_and_pin_is_gone(self) -> None:
        """
        Scenario 1.955: the column that names the row stays in place, with nothing to write.
        Given: a rows table and a model table whose columns say nothing about pinning; and
               `ui.column(..., pin=True)`, the first build's way to ask for it.
        When:  they are built.
        Then:  the first column of each is RFDS's start-sticky column and no other is; `pin=` is
               no argument of ui.column any more.
        """
        state, _ = built(ui.table(CustomersState.customers, columns=["name", "code", "region"]))
        self.assertEqual([spec.get("sticky") for spec in state.table_columns], ["start", None, None],
                         "the first column stays in place while the table scrolls sideways; the others scroll")
        groups = ui.table(Group, columns=["name", "id"])
        groups.build(Build(page="groups", route="/groups", caption="Groups"))
        self.assertEqual([spec.get("sticky") for spec in rows_table._CACHE_BY_TABLE[groups].table_columns],
                         ["start", None], "a model table the same way")
        with self.assertRaises(TypeError):
            ui.column("name", pin=True)

    # -- 1.956 ---------------------------------------------------------
    def test_1_956_the_users_column_choice_lasts_until_the_tab_is_closed(self) -> None:
        """
        Scenario 1.956: how long the Columns menu's choice lasts, and what lex-app says about it.
        Given: a rows table with choose_columns=True, and the user's choice of columns.
        When:  the user opens another page and comes back (a new visit; the table appears again).
        Then:  the choice is still there: it lives in the tab's state until the tab is closed;
               ChosenColumns says so (it said a new visit starts from the table as declared).
        """
        from lex.lex_app.reflex.data import ChosenColumns
        from lex.lex_app.reflex.ui.state import State

        state, _ = built(ui.table(CustomersState.customers, columns=["name", "code", "region"], choose_columns=True))

        async def scenario():
            async with testing.Harness(user="ada") as h:
                await h.open_tab("/customers")
                await (await h.send(state, "open"))
                await (await h.send(state, "set_view_columns", ids=["name", "region"]))
                await (await h.send(State, "ui_open", page="/elsewhere"))
                await (await h.send(state, "close"))
                await (await h.send(State, "ui_open", page="/customers"))
                await (await h.send(state, "open"))
                return list((await h._state(state)).view_columns)

        self.assertEqual(asyncio.run(scenario()), ["name", "region"], "the choice survives a new visit")
        self.assertIn("until the tab is closed: a new visit keeps them", ChosenColumns.__doc__,
                      "and the class says what it does")

    # -- 1.957 ---------------------------------------------------------
    def test_1_957_a_built_tables_state_is_held_only_while_the_table_is(self) -> None:
        """
        Scenario 1.957: lex-app's note of which state each built table got.
        Given: 200 tables built (as pages are, once per compile) and dropped; then a new table
               that was never built.
        When:  Python collects the dropped tables.
        Then:  the note does not grow with them, and the new table -- which may reuse a dropped
               table's memory address -- is not taken for one of them.
        """
        import gc

        gc.collect()
        before = len(rows_table._CACHE_BY_TABLE)
        for _ in range(200):
            ui.table(CustomersState.customers, columns=["name"]).build(CTX)
        gc.collect()
        self.assertLessEqual(len(rows_table._CACHE_BY_TABLE), before, "dropped tables leave the note")
        fresh = [ui.table(CustomersState.customers, columns=["name"]) for _ in range(50)]
        self.assertEqual([table for table in fresh if table in rows_table._CACHE_BY_TABLE], [],
                         "a table never built has no state, whatever its address")

    # -- 1.1126 --------------------------------------------------------
    def test_1_1126_a_rows_actions_are_rfds_row_actions(self) -> None:
        """
        Scenario 1.1126: row_actions as RFDS's row actions.
        Given: a table whose rows each have an Edit button, a dangerous Close that asks first and
               says per row why it cannot be done, a Note and a Remind button, and a table whose
               rows have a link.
        When:  the tables are built.
        Then:  the first two plain actions are RFDS's small ghost buttons in the row (tertiary,
               size sm) -- never a full-size solid button filling the row's height -- in the
               order written, each still showing that row's run; the dangerous one is a danger
               entry of the row's "More actions" menu, as RFDS's row actions place destructive
               actions, still asking first, and saying the row's reason when it cannot be chosen
               (doing nothing then); a third plain action goes into that menu too; a link in a
               row is the same small ghost button.
        """
        table = ui.table(ClosingFundsState.funds, columns=["name"], row_actions=lambda row: [
            ui.button("Edit", ClosingFundsState.edit, row["id"]),
            ui.button("Close", ClosingFundsState.close, row["id"], tone="danger", confirm="Close this fund?",
                      disabled=row["why_not"]),
            ui.button("Note", ClosingFundsState.edit, row["id"]), ui.button("Remind", ClosingFundsState.edit, row["id"])])
        text = str(ui.embed(table, name="funds"))
        row = text[text.index('"data-slot":"rfds-row-actions"'):]
        inline, menu = row.split("rfds-row-actions-more", 1)
        self.assertEqual(inline.count('"data-rfds-intent":"tertiary"'), 2, "two ghost buttons in the row")
        self.assertEqual(inline.count('className:"rfds-button rfds-size-sm"'), 2, "small ones, RFDS's size for a row")
        self.assertNotIn('"data-rfds-intent":"danger"', inline, "no solid danger button in the row")
        self.assertLess(inline.index('rfdsI18n.t("Edit"'), inline.index('rfdsI18n.t("Note"'), "in the order written")
        self.assertIn('ui_running_rx_state_?.["ClosingFundsState.edit"]', inline, "each shows that row's run")
        close = menu[menu.index('"data-rfds-tone":"danger"') - 400:menu.index('rfdsI18n.t("Close"')]
        self.assertIn('"aria-disabled":(!((r_rx_state_?.["why_not"]', close, "the danger entry is blocked per row")
        self.assertIn('"rfds-menu-item-reason"', menu, "and says why")
        self.assertIn('(!((r_rx_state_?.["why_not"]?.valueOf?.() === ""?.valueOf?.())) ? (ReflexEvent("_call_function"',
                      menu, "doing nothing while it cannot be chosen")
        self.assertIn("ui_ask", menu, "else it asks first")
        self.assertIn('rfdsI18n.t("Remind"', menu, "a third plain action is in the menu")
        self.assertNotIn('rfdsI18n.t("Remind"', inline, "not in the row")
        linked = str(ui.embed(ui.table(ClosingFundsState.funds, columns=["name"], row_actions=lambda row: [
            ui.link("Website", href="https://example.com")]), name="funds"))
        linked = linked[linked.index('"data-slot":"rfds-row-actions"'):]
        self.assertIn('"data-rfds-intent":"tertiary"', linked, "a link in a row is a ghost button")
        self.assertIn("rfds-size-sm", linked, "a small one")

    # -- 1.1129 --------------------------------------------------------
    def test_1_1129_the_export_split_button_is_one_control(self) -> None:
        """
        Scenario 1.1129: the table's Export, one split button.
        Given: a table with export=True.
        When:  it is built.
        Then:  its toolbar holds RFDS's attached export group - "Export" (CSV, the first format)
               and the menu of every format behind "More export formats" - and both halves are
               drawn as the same secondary button, so they read as one control: RFDS's own
               export_menu leaves the menu's half a bare ghost icon pressed against the filled
               "Export", which reads as two buttons overlapping.
        """
        text = str(ui.embed(ui.table(ClosingFundsState.funds, columns=["name"], export=True), name="funds"))
        group = text[text.index('"data-slot":"rfds-export-menu"') - 1500:]
        group = group[:group.index("More export formats") + 400]
        self.assertIn("rfds-button-group-attached", group, "RFDS's attached group")
        self.assertEqual(group.count('"data-rfds-intent":"secondary"'), 2, "both halves the same secondary button")
        self.assertNotIn('"data-rfds-intent":"tertiary"', group, "no bare ghost half")

    # -- 1.1130 --------------------------------------------------------
    def test_1_1130_a_number_columns_header_stands_level_with_the_others(self) -> None:
        """
        Scenario 1.1130: header labels on one line.
        Given: a table of rows and a model's table, each with a number column.
        When:  they are built.
        Then:  each carries the rule that sets RFDS's sort buttons on the header cell's bottom - a
               number column's header (RFDS reverses its sort button and isolates its digits) sat
               6 px above the text columns' headers, measured in the browser; with the rule every
               header label stands on one line. The rule is the table's own, so it holds in any
               app, whatever stylesheets that app loads.
        """
        rule = "th .rfds-sort-button { vertical-align: bottom; }"
        rows = str(ui.embed(ui.table(ClosingFundsState.funds, columns=["name"]), name="funds"))
        self.assertIn(rule, rows, "a rows table carries the rule")
        model = str(ui.embed(ui.table(Group, columns=["name"]), name="groups"))
        self.assertIn(rule, model, "and so does a model's table")
