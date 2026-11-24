"""lex.ui tables: a table that leaves the screen keeps none of the rows it showed.

Intent: a table's rows are often people - names, e-mail addresses, amounts - read for one user
from another system. While the table is on screen the tab's state holds its visible page; once
the table is gone (another step of a tool, a new run, another page) nothing of those rows may
stay in the tab's state, which Reflex keeps for an hour and pickles to Redis with two workers. A
regression is a person's data left in a state nobody looks at any more.

Cluster 1cz -- scenario 1.823. Type: U.
Covers: lex/lex_app/reflex/ui/pages/rows_table.py (close).
Run: python -m lex pytest lex/test_project/tests/init/test_1cz_ui_table_close.py -v
"""

from __future__ import annotations

import asyncio

import pytest
from django.test import SimpleTestCase

from lex.lex_app.reflex.ui import testing
from lex.test_project.tests.init.test_1cw_ui_rows_table import build_people_table, fresh, page_of

pytestmark = pytest.mark.init


class TestCluster01cz_TableClose(SimpleTestCase):
    """1.823: a closed table forgets its rows."""

    def setUp(self) -> None:
        fresh()

    # -- 1.823 ---------------------------------------------------------
    def test_1_823_a_table_that_leaves_the_screen_keeps_none_of_its_rows(self) -> None:
        """
        Scenario 1.823: a table leaves the screen.
        Given: the people table of 1.812 on screen, with its page, its count, its totals per
               currency and its filter choices.
        When:  the table leaves the screen, and later appears again.
        Then:  once it has left, its state holds no row, no count, no totals and no filter
               choice; when it appears again it reads its rows afresh.
        """
        state = build_people_table()

        async def scenario():
            async with testing.Harness(user="ada", params={"key": "1"}) as h:
                await h.open_tab("/people/1")
                await (await h.send(state, "open"))
                table = await h._state(state)
                shown = (len(table.page_rows), table.total_rows, len(table.totals_rows), bool(table.choices))
                await (await h.send(state, "close"))
                table = await h._state(state)
                closed = (table.page_rows, table.total_rows, table.totals_rows, table.choices, table._row_to)
                await (await h.send(state, "open"))
                return shown, closed, [row["name"] for row in await page_of(h, state)]

        shown, closed, again = asyncio.run(scenario())
        self.assertEqual(shown[:3], (3, 3, 2), "on screen: three rows, their count, a total per currency")
        self.assertTrue(shown[3], "on screen: the filters' choices")
        self.assertEqual(closed, ([], 0, [], {}, {}), "gone from the screen: nothing of the rows stays in the tab")
        self.assertEqual(again, ["Ada Example", "Fund One", "Bob Example"], "back on screen: read afresh")
