"""lex.ui tables: a date column is filtered by a range of days.

Intent: a list with dates (orders, runs, uploads) is narrowed to a period. `filters=` names
columns; lex-app decides the control from what the column is: a dropdown of the column's values,
or, for a date or datetime column, a range of days - a dropdown of every day would be useless
and soon exceed what a dropdown offers. The filtering runs on the server over every row, and only
on the fields the table offers to filter. A regression is a list that cannot be narrowed to a
month, a range that drops its last day, or a filter on a field the table does not offer.

Cluster 1dd -- scenario 1.828. Type: U.
Covers: lex/lex_app/reflex/ui/pages/tables.py (the toolbar, filters by column kind),
lex/lex_app/reflex/ui/pages/rows_table.py (set_dates, matching).
Run: python -m lex pytest lex/test_project/tests/init/test_1dd_ui_table_filters.py -v
"""

from __future__ import annotations

import asyncio
import datetime

import pytest
from django.test import SimpleTestCase

from lex import ui
from lex.lex_app.reflex.ui import testing
from lex.lex_app.reflex.ui.pages import rows_table
from lex.lex_app.reflex.ui.pages.parts import Build

pytestmark = pytest.mark.init


class OrdersState(ui.State):
    """Orders whose rows a table filters."""

    @ui.rows
    def orders(self):
        return [
            {"id": 1, "name": "Order one", "created": datetime.date(2026, 3, 1), "kind": "web", "secret": "x"},
            {"id": 2, "name": "Order two", "created": datetime.datetime(2026, 3, 31, 23, 59), "kind": "web", "secret": "y"},
            {"id": 3, "name": "Order three", "created": "2026-04-01T08:00:00", "kind": "shop", "secret": "z"},
            {"id": 4, "name": "Undated", "created": None, "kind": "shop", "secret": "w"},
        ]


def orders_table() -> tuple[type, str]:
    table = ui.table(OrdersState.orders, columns=["name", ui.column("created", kind="date"), "kind"],
                     filters=["kind", "created"])
    built = str(table.build(Build(page="orders", route="/orders", caption="Orders")))
    return rows_table._CACHE_BY_TABLE[id(table)], built


class TestCluster01dd_TableFilters(SimpleTestCase):
    """1.828: a date column's filter is a range of days."""

    # -- 1.828 ---------------------------------------------------------
    def test_1_828_a_date_column_is_filtered_by_a_range_of_days(self) -> None:
        """
        Scenario 1.828: filters on a kind column and a date column.
        Given: rows carrying a date, a datetime, an ISO text and no date, and a table whose
               filters name its kind column and its date column.
        When:  the table is built, and the user picks March, an open start, an open end, combines
               the range with the dropdown, and clears; and sends a range for other fields.
        Then:  the kind column has a dropdown and the date column a date-range picker; March keeps
               the rows from its first to its last day (a datetime on the last evening included,
               an undated row never); a start or an end alone is open on its other side; the range
               and the dropdown together keep the rows both allow; clearing brings every row back
               and empties the picker; a range for a field that is no date filter, and a date that
               is not a day, change nothing.
        """
        state, built = orders_table()
        self.assertEqual((state.date_fields, state.choice_fields), (("created",), ("kind",)),
                         "the date column is a range, the other a dropdown; only they can be filtered")
        self.assertTrue('"filter_created"' in built and '"filter_kind"' in built, "both controls are in the toolbar")

        async def scenario():
            async with testing.Harness(user="ada") as h:
                await h.open_tab("/orders")

                async def names(event, **kwargs):
                    if event:
                        await (await h.send(state, event, **kwargs))
                    return [row["name"] for row in (await h._state(state)).page_rows]

                seen = {"all": await names("open")}
                seen["march"] = await names("set_dates", field="created", value={"start": "2026-03-01", "end": "2026-03-31"})
                picker = dict((await h._state(state)).dates)
                seen["march, web"] = await names("set_choice", field="kind", value="web")
                seen["march, shop"] = await names("set_choice", field="kind", value="shop")
                await names("clear_filters")
                seen["from april"] = await names("set_dates", field="created", value={"start": "2026-04-01", "end": ""})
                seen["until march 1"] = await names("set_dates", field="created", value={"start": "", "end": "2026-03-01"})
                seen["cleared"] = await names("clear_filters")
                cleared = dict((await h._state(state)).dates)
                seen["other field"] = await names("set_dates", field="kind", value={"start": "2026-03-01", "end": ""})
                seen["hidden field"] = await names("set_dates", field="secret", value={"start": "2026-03-01", "end": ""})
                seen["not a day"] = await names("set_dates", field="created", value={"start": "March", "end": "soon"})
                return seen, picker, cleared

        seen, picker, cleared = asyncio.run(scenario())
        everyone = ["Order one", "Order two", "Order three", "Undated"]
        self.assertEqual(seen["all"], everyone)
        self.assertEqual(seen["march"], ["Order one", "Order two"], "from its first to its last day, evening included")
        self.assertEqual(picker, {"created": {"start": "2026-03-01", "end": "2026-03-31"}}, "the picker shows the range")
        self.assertEqual((seen["march, web"], seen["march, shop"]), (["Order one", "Order two"], []),
                         "the range and the dropdown keep the rows both allow")
        self.assertEqual(seen["from april"], ["Order three"], "a start alone is open on its other side")
        self.assertEqual(seen["until march 1"], ["Order one"], "an end alone too; an undated row never matches")
        self.assertEqual((seen["cleared"], cleared), (everyone, {}), "clearing empties the picker")
        for case in ("other field", "hidden field", "not a day"):
            self.assertEqual(seen[case], everyone, f"{case}: nothing changes")
