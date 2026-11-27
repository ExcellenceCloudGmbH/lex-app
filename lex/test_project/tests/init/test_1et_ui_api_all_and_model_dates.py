"""lex.ui: every record through self.api.all, and a model table filtered by a range of days.

Intent: a model table reads one page at a time through lex-app's API, so it cannot export or total
every row, and projects copied a paging helper into a `@ui.rows` handler to get those. Now one call
reads every record the user may read, page after page in a stable order, with a ceiling that is
refused in words: `self.api.all(Model, filters=...)`, returned by a `@ui.rows` handler, gives a
table export, totals, search and date filters with no helper. A model table itself gains what costs
nothing on the server: a date field in `filters=` is a range of days, sent as the API's lookups. A
regression is a row read twice or missed across pages, an unbounded read, or a date filter the API
ignores.

Cluster 1et -- scenarios 1.1028-1.1029, 1.1114. Type: U.
Covers: lex/lex_app/reflex/ui/context.py (Api.all, ALL_LIMIT), lex/lex_app/reflex/data.py (LexTableState
set_dates, date_lookups), lex/lex_app/reflex/ui/pages/tables.py (a model table's date filters, the
export/totals refusal).
Run: python -m lex pytest lex/test_project/tests/init/test_1et_ui_api_all_and_model_dates.py -v
"""

from __future__ import annotations

import asyncio
from unittest.mock import patch

import httpx
import pytest
from django.test import SimpleTestCase
from reflex.compiler.compiler import compile_page

from lex import ui
from lex.lex_app.reflex import data
from lex.lex_app.reflex.ui import context, results
from lex.lex_app.reflex.ui.pages import rows_table
from lex.lex_app.reflex.ui.pages.parts import Build, PageError
from lex.test_project.tests.init.models import IncidentDatetimeItem, UiRegionReportLine
from lex.test_project.tests.init.test_1cf_ui_context import _in_thread, _session

pytestmark = pytest.mark.init


class LinesState(ui.State):
    @ui.rows
    def lines(self):
        return self.api.all(UiRegionReportLine, filters={"report": self.params.get("key")})


class TestCluster01et_ApiAllAndModelDates(SimpleTestCase):
    """1.1028-1.1029."""

    # -- 1.1028 --------------------------------------------------------
    def test_1_1028_api_all_reads_every_record_in_a_stable_order_up_to_a_ceiling(self) -> None:
        """
        Scenario 1.1028: self.api.all(Model, ...).
        Given: lex-app's API holding 2,345 lines behind a recording transport.
        When:  a handler reads them all, ordered by amount; then a model holding more than the ceiling;
               then a table over a @ui.rows handler that returns self.api.all(...) with export and totals.
        Then:  every line comes back once, in order, read 1,000 a page with the key after the asked
               order (amount,id); the count is the rows read; past the ceiling a refusal in words
               says how many there are and to narrow them, before more than one page is read; the
               table over the handler builds with export and totals (a model table refuses them,
               pointing to self.api.all).
        """
        total = 2345
        seen: list[dict] = []

        def handler(request: httpx.Request) -> httpx.Response:
            query = dict(request.url.params.multi_items())
            seen.append(query)
            page, size = int(query["page"]), int(query["perPage"])
            ids = range((page - 1) * size + 1, min(page * size, total) + 1)
            return httpx.Response(200, json={"count": total, "results": [{"id": i, "amount": str(i)} for i in ids]})

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        rows = _in_thread(_session(["tok"], client=client),
                          lambda inv: context.Api(inv).all(UiRegionReportLine, ordering="amount"))
        self.assertEqual([row["id"] for row in rows], list(range(1, total + 1)), "every row once, in order")
        self.assertEqual(rows.count, total)
        self.assertEqual([(q["page"], q["perPage"], q["ordering"]) for q in seen],
                         [("1", "1000", "amount,id"), ("2", "1000", "amount,id"), ("3", "1000", "amount,id")],
                         "pages of 1,000, the key after the asked order")
        seen.clear()
        with patch.object(context, "ALL_LIMIT", 2000), self.assertRaises(results.Refuse) as refused:
            _in_thread(_session(["tok"], client=client), lambda inv: context.Api(inv).all(UiRegionReportLine))
        self.assertIn("There are 2,345", str(refused.exception))
        self.assertIn("Narrow them down first", str(refused.exception))
        self.assertEqual(len(seen), 1, "refused after the first page")

        table = ui.table(LinesState.lines, columns=["customer", ui.column("amount", kind="money", currency="EUR")],
                         export=True, totals={"amount": "sum"}, search=True)
        compile_page("/lines", table.build(Build(page="lines", route="/lines", caption="Lines")))
        with self.assertRaises(PageError) as model_refused:
            ui.table(UiRegionReportLine, columns=["customer", "amount"], totals={"amount": "sum"}).build(
                Build(page="lines", route="/lines", caption="Lines"))
        self.assertIn("self.api.all(UiRegionReportLine", str(model_refused.exception), "the refusal says what to write")

    # -- 1.1029 --------------------------------------------------------
    def test_1_1029_a_model_table_filters_a_date_field_by_a_range_of_days(self) -> None:
        """
        Scenario 1.1029: ui.table(Model, filters=["event_at"]) on a date-and-time field.
        Given: a model table with a date-and-time filter, and lex-app's API behind a recording stand-in.
        When:  the table is built and compiled; a range is chosen; then cleared.
        Then:  the toolbar holds RFDS's date range picker; the read sends `event_at__date__gte` and
               `event_at__date__lte`; a malformed day is dropped; clearing the filters removes it; a
               date field that is not a filter is refused by set_dates.
        """
        table = ui.table(IncidentDatetimeItem, columns=["name", "event_at"], filters=["event_at"], visible=True)
        _, module = compile_page("/incidents", table.build(Build(page="incidents", route="/incidents",
                                                                 caption="Incidents")))
        state = rows_table._CACHE_BY_TABLE[table]
        self.assertEqual(state.date_lookups, {"event_at": "event_at__date"})
        self.assertEqual(state.choice_fields, ())
        self.assertIn("filter_event_at", module, "the date range picker in the toolbar")
        sent: list[list] = []

        class Recording:
            async def list(self, model, **kwargs):
                sent.append(list(kwargs["params"]))
                return {"count": 0, "results": []}

        async def as_user(_state):
            return Recording()

        async def scenario():
            from lex.lex_app.reflex.ui import testing

            async with testing.Harness(user="ada") as h:
                await h.open_tab("/incidents")
                await (await h.send(state, "open", scope={}))
                await (await h.send(state, "set_dates", field="event_at",
                                    value={"start": "2026-01-01", "end": "2026-01-31"}))
                await (await h.send(state, "set_dates", field="name", value={"start": "2026-01-01"}))
                await (await h.send(state, "set_dates", field="event_at", value={"start": "1.1.2026", "end": ""}))
                await (await h.send(state, "set_dates", field="event_at", value={"start": "2026-02-01", "end": ""}))
                await (await h.send(state, "clear_filters"))
                return dict((await h._state(state)).dates)

        with patch.object(data, "lex_api", as_user):
            dates = asyncio.run(scenario())
        self.assertEqual(sent[1], [("event_at__date__gte", "2026-01-01"), ("event_at__date__lte", "2026-01-31")])
        self.assertEqual(len(sent), 5, "a field that is no date filter reads nothing")
        self.assertEqual(sent[2], [], "a malformed day is dropped")
        self.assertEqual(sent[3], [("event_at__date__gte", "2026-02-01")])
        self.assertEqual((sent[4], dates), ([], {}), "clearing the filters removes the range")


ASKED: list = []


class StockState(ui.State):
    """Every stock line through one read, and a search the handler does itself."""

    @ui.rows
    def stock(self):
        ASKED.append("stock")
        return [{"id": n, "item": f"Item {n:03d}", "store": "North" if n % 2 else "South"} for n in range(1, 61)]

    @ui.rows
    def found(self, search):
        ASKED.append(f"found:{search}")
        return [{"id": n, "item": f"Item {n:03d}"} for n in range(1, 61) if search in f"Item {n:03d}"]

    @ui.action
    def restock(self):
        return None


class TestCluster01et_RowsReadOnce(SimpleTestCase):
    """1.1114: a table over a @ui.rows handler reads its rows once, not on every page turn."""

    def setUp(self) -> None:
        ASKED.clear()
        rows_table._KEPT.clear()
        self.addCleanup(rows_table._KEPT.clear)

    # -- 1.1114 --------------------------------------------------------
    def test_1_1114_page_turns_sorts_filters_and_searches_read_the_kept_rows(self) -> None:
        """
        Scenario 1.1114: a table over every record of a model (self.api.all through a @ui.rows handler).
        Given: a searchable, filterable table of 60 stock lines, and a table whose handler searches itself.
        When:  the user turns pages, sorts, filters, searches among the rows; a handler of the state runs;
               the page opens again (a new visit); the rows stay unused past the idle time; and the
               searching handler's table is searched twice and paged.
        Then:  the handler was asked when the table appeared, after the state's handler, on the new visit
               and after the idle time -- never for a page turn, a sort, a filter or a search among the
               rows; the searching handler is asked once per search, not per page; what is shown is right
               each time; the kept rows are in no state Reflex stores.
        """
        import pickle

        from lex.lex_app.reflex.ui import testing
        from lex.lex_app.reflex.ui.state import State

        stock = ui.table(StockState.stock, columns=["item", "store"], filters=["store"], search=True, page_size=25)
        found = ui.table(StockState.found, columns=["item"], search=True, page_size=25)
        for one in (stock, found):
            one.build(Build(page="stock", route="/stock", caption="Stock"))
        S, F = rows_table._CACHE_BY_TABLE[stock], rows_table._CACHE_BY_TABLE[found]

        async def scenario():
            async with testing.Harness(user="ada") as h:
                await h.open_tab("/stock")
                await (await h.send(S, "open"))
                await (await h.send(S, "set_page", value=1))
                await (await h.send(S, "sort_by", key="item"))
                await (await h.send(S, "set_choice", field="store", value="North"))
                await (await h.send(S, "set_query", value="Item 05"))
                shown = [row["item"] for row in (await h._state(S)).page_rows]
                browsed = list(ASKED)
                await (await h.send(StockState, "restock"))
                await asyncio.sleep(0.05)
                after_handler = list(ASKED)
                await (await h.send(State, "ui_open", page="/stock"))
                await (await h.send(S, "set_query", value=""))
                after_visit = list(ASKED)
                with patch.object(rows_table, "ROWS_IDLE", -1):
                    await (await h.send(S, "set_page", value=1))
                after_idle = list(ASKED)
                ASKED.clear()
                await (await h.send(F, "open"))
                await (await h.send(F, "set_query", value="Item 0"))
                await (await h.send(F, "set_page", value=1))
                await (await h.send(F, "set_query", value="Item 01"))
                searched = list(ASKED)
                stored = b"".join(pickle.dumps(one) for one in (await h._state(S), await h._state(F)))
                return shown, browsed, after_handler, after_visit, after_idle, searched, stored

        shown, browsed, after_handler, after_visit, after_idle, searched, stored = asyncio.run(scenario())
        self.assertEqual(shown, ["Item 005", "Item 051", "Item 053", "Item 055", "Item 057", "Item 059"])
        self.assertEqual(browsed, ["stock"], "read once for a page turn, a sort, a filter and a search")
        self.assertEqual(after_handler, ["stock"] * 2, "read again after a handler of its state")
        self.assertEqual(after_visit, ["stock"] * 3, "read again on a new visit")
        self.assertEqual(after_idle, ["stock"] * 4, "read again after the idle time")
        self.assertEqual(searched, ["found:", "found:Item 0", "found:Item 01"], "a search it does: once per search")
        self.assertNotIn(b"Item 059", stored, "the kept rows are in no stored state")
