"""lex.ui tables: a date-range filter and a "contains" filter.

Intent: a history (exports run, documents uploaded) is filtered by when something happened and by
words in a name, besides the dropdowns of fixed values. The filtering runs on the server over every
row, like the dropdowns, and only on the fields the table offers to filter: a filter on any other
field would let the browser learn a hidden value from the number of rows that match. A regression
is a history that cannot be narrowed to a month, a date filter that drops the last day, or a filter
on a field the table does not offer.

Cluster 1dd -- scenario 1.828. Type: U.
Covers: lex/lex_app/reflex/ui/pages/tables.py (filter, the toolbar), lex/lex_app/reflex/ui/pages/rows_table.py
(set_dates, set_text, matching).
Run: python -m lex pytest lex/test_project/tests/init/test_1dd_ui_table_filters.py -v
"""

from __future__ import annotations

import asyncio
import datetime

import pytest
from django.contrib.auth.models import Group
from django.test import SimpleTestCase

from lex import ui
from lex.lex_app.reflex.ui import testing
from lex.lex_app.reflex.ui.pages import rows_table
from lex.lex_app.reflex.ui.pages.parts import Build, PageError

pytestmark = pytest.mark.init


class HistoryDesk(ui.State):
    """A history whose rows a table filters."""

    @ui.rows
    def runs(self):
        return [
            {"id": 1, "company": "Fund One GmbH", "created": datetime.date(2026, 3, 1), "kind": "export", "secret": "x"},
            {"id": 2, "company": "Fund Two SE", "created": datetime.datetime(2026, 3, 31, 23, 59), "kind": "export",
             "secret": "y"},
            {"id": 3, "company": "Other One AG", "created": "2026-04-01T08:00:00", "kind": "import", "secret": "z"},
            {"id": 4, "company": "Undated", "created": None, "kind": "import", "secret": "w"},
        ]


def history_table() -> tuple[type, str]:
    table = ui.table(HistoryDesk.runs, columns=["company", ui.column("created", kind="date"), "kind"],
                     filters=["kind", ui.filter("created", kind="dates", label="Created"),
                              ui.filter("company", kind="text")])
    built = str(table.build(Build(page="history", route="/history", caption="History")))
    return rows_table._CACHE_BY_TABLE[id(table)], built


class TestCluster01dd_TableFilters(SimpleTestCase):
    """1.828: ui.filter(kind="dates" | "text")."""

    # -- 1.828 ---------------------------------------------------------
    def test_1_828_a_history_is_filtered_by_dates_and_by_words(self) -> None:
        """
        Scenario 1.828: a date range and a "contains" filter.
        Given: a history whose rows carry a date, a datetime, an ISO text and no date, and a table
               with a dropdown, a date-range filter and a text filter.
        When:  the table is built, and the user picks March, types words, combines both, and
               clears them; and filters on fields the table does not offer.
        Then:  the toolbar has a date-range picker and a text box beside the dropdown; March keeps
               the rows from its first to its last day (a datetime on the last evening included,
               an undated row never); a start or an end alone is open on its other side; the words
               match a part of the field in any case and all must match; both filters together
               keep the rows both allow; clearing brings every row back and empties the boxes; a
               range or words for a field the table does not offer, and a date that is not a day,
               change nothing; a model table, and an unknown kind, are refused with what to write.
        """
        state, built = history_table()
        self.assertTrue("rfds-date-range" in built or "DateRange" in built or "date_range" in built,
                        "a date-range picker in the toolbar")
        self.assertTrue('"filter_company"' in built, "a text box for the words")
        self.assertTrue('"filter_kind"' in built, "and the dropdown beside them")
        self.assertEqual((state.date_fields, state.text_fields, state.choice_fields),
                         (("created",), ("company",), ("kind",)), "only the offered fields can be filtered")

        async def scenario():
            async with testing.Harness(user="ada") as h:
                await h.open_tab("/history")

                async def names(event, **kwargs):
                    if event:
                        await (await h.send(state, event, **kwargs))
                    return [row["company"] for row in (await h._state(state)).page_rows]

                seen = {"all": await names("open")}
                seen["march"] = await names("set_dates", field="created", value={"start": "2026-03-01", "end": "2026-03-31"})
                seen["from april"] = await names("set_dates", field="created", value={"start": "2026-04-01", "end": ""})
                seen["until march 1"] = await names("set_dates", field="created", value={"start": "", "end": "2026-03-01"})
                await names("set_dates", field="created", value={"start": "2026-03-01", "end": "2026-03-31"})
                seen["march and one"] = await names("set_text", field="company", value="  ONE ")
                seen["words"] = await names("set_text", field="company", value="fund one")
                table = await h._state(state)
                boxes = (dict(table.dates), dict(table.texts))
                seen["cleared"] = await names("clear_filters")
                table = await h._state(state)
                cleared_boxes = (dict(table.dates), dict(table.texts))
                seen["foreign"] = await names("set_dates", field="kind", value={"start": "2026-03-01", "end": ""})
                seen["foreign text"] = await names("set_text", field="secret", value="x")
                seen["not a day"] = await names("set_dates", field="created", value={"start": "March", "end": "soon"})
                return seen, boxes, cleared_boxes

        seen, boxes, cleared_boxes = asyncio.run(scenario())
        everyone = ["Fund One GmbH", "Fund Two SE", "Other One AG", "Undated"]
        self.assertEqual(seen["all"], everyone)
        self.assertEqual(seen["march"], ["Fund One GmbH", "Fund Two SE"], "from its first to its last day, evening included")
        self.assertEqual(seen["from april"], ["Other One AG"], "a start alone is open on its other side")
        self.assertEqual(seen["until march 1"], ["Fund One GmbH"], "an end alone too; an undated row never matches")
        self.assertEqual(seen["march and one"], ["Fund One GmbH"], "both filters keep the rows both allow")
        self.assertEqual(seen["words"], ["Fund One GmbH"], "every word must match, in any case")
        self.assertEqual(boxes, ({"created": {"start": "2026-03-01", "end": "2026-03-31"}}, {"company": "fund one"}),
                         "the picker and the box show what filters the rows")
        self.assertEqual((seen["cleared"], cleared_boxes), (everyone, ({}, {})), "clearing empties both")
        for case in ("foreign", "foreign text", "not a day"):
            self.assertEqual(seen[case], everyone, f"{case}: nothing changes")

        model = ui.table(Group, columns=["name"], filters=[ui.filter("name", kind="text")])
        with self.assertRaises(PageError) as refused:
            model.build(Build(page="groups", route="/groups"))
        self.assertIn("give the name alone", str(refused.exception))
        with self.assertRaises(ValueError) as kind:
            ui.filter("created", kind="range")
        self.assertIn("'dates'", str(kind.exception), "the message lists the kinds")
        with self.assertRaises(TypeError):
            ui.table(HistoryDesk.runs, columns=["company"], filters=[{"created": "dates"}])
