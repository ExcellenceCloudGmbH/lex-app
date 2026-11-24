"""lex.ui calculation pages: the past runs -- a ``ui.table`` over the model, each row opening its run.

Intent: a calculation keeps a record people come back to. Under the form, the page lists the past
runs: ``history=ui.table(Model, columns=[...])``, written as any table over a model (search, filters,
page size), to which lex-app adds the run's status, a link from each row to that run's view
(``?run=<key>``), and the newest run first. Who sees which runs is the model's own permission_list
and permission_read, as on every model table. A regression is a past run nobody can open, a list
that starts with the oldest run, or runs shown that the model would not show the reader.

Cluster 1eh -- scenarios 1.991-1.993. Type: E (1.993 reads the rows through lex-app's real list view).
Covers: lex/lex_app/reflex/ui/pages/calculation.py (history=), lex/lex_app/reflex/ui/pages/tables.py
(a model table's row link and order given by its page kind), lex/lex_app/reflex/data.py
(LexTableState's default ordering).
Run: python -m lex pytest lex/test_project/tests/init/test_1eh_ui_calculation_history.py -v
"""

from __future__ import annotations

import asyncio
from unittest.mock import patch

import pytest
from django.contrib.auth.models import Group
from reflex.compiler.compiler import compile_page

from lex import ui
from lex.lex_app.reflex import data
from lex.lex_app.reflex.ui.pages import rows_table
from lex.lex_app.reflex.ui.pages.parts import Build, PageError
from lex.test_project.tests._e2e_test_case import E2ETestCase
from lex.test_project.tests.init._ui_calc_aids import ViewsClient
from lex.test_project.tests.init.models import UI_CALC_MODELS, UiRegionReport

pytestmark = pytest.mark.init

ROUTE = "/reports/regions"
CTX = Build(page="region_reports", route=ROUTE)
FIELDS = [ui.field("region", required=True), ui.field("year", kind="year", required=True)]


def region_reports(history):
    return ui.calculation(UiRegionReport, title="Region report", fields=FIELDS, history=history)


def _built_history(page):
    """The history table's state, as the page built it."""
    page.build(CTX)
    return rows_table._CACHE_BY_TABLE[id(page.built_history)]


class TestCluster01eh_CalculationHistory(E2ETestCase):
    """1.991-1.993: the past runs as declared; written wrongly; read as the user, newest first."""

    e2e_models = UI_CALC_MODELS

    def setUp(self) -> None:
        super().setUp()
        UiRegionReport.may_read = True
        self.addCleanup(setattr, UiRegionReport, "may_read", True)

    # -- 1.991 ---------------------------------------------------------
    def test_1_991_the_past_runs_are_a_model_table_lex_app_completes(self) -> None:
        """
        Scenario 1.991: history=, as a reader writes it.
        Given: history=ui.table(Model, columns=["region", "year"], search=True).
        When:  the page is built and compiled.
        Then:  the page shows the table under the form, titled "Past runs"; lex-app added the run's
               status as the last column, in the badge's words; each row opens its run on the same
               page (?run=<key>); the newest run comes first; the table's own words still work (its
               search); the table the author wrote is unchanged.
        """
        written = ui.table(UiRegionReport, columns=["region", "year"], search=True)
        page = region_reports(written)
        _, module = compile_page(ROUTE, page.build(CTX))
        state = _built_history(page)
        self.assertIn('"Past runs"', str(page.build(CTX)), "titled as the past runs")
        self.assertEqual([c["field"] for c in state.table_columns], ["region", "year", "is_calculated"],
                         "the status is added last")
        self.assertIn('"Done"', module, "in the badge's words")
        self.assertIn(f'to:("{ROUTE}?run="+(encodeURIComponent(String(r_rx_state_?.["id"]', module,
                      "each row is a link to its run on the same page")
        self.assertEqual(state.default_ordering, "-id", "the newest run first")
        self.assertEqual(state.search_field, "region", "the table's own search")
        self.assertEqual(written.columns, ("region", "year"), "the author's table is not changed")

    # -- 1.992 ---------------------------------------------------------
    def test_1_992_past_runs_written_wrongly_say_what_to_write(self) -> None:
        """
        Scenario 1.992: what history= refuses.
        Given: history= given a table of another model, a table with open= or where=, or no table.
        When:  the page is built.
        Then:  each is refused, naming the slot and saying what to write.
        """
        cases = {
            "a table of Group": (ui.table(Group, columns=["name"]), "the past runs are UiRegionReport's"),
            "open=": (ui.table(UiRegionReport, columns=["region"], open=region_reports), "Leave `open=` out"),
            "where=": (ui.table(UiRegionReport, columns=["region"], where={"region": "north"}), "Leave `where=` out"),
            "not a table": (["region", "year"], "history=ui.table(UiRegionReport"),
        }
        for name, (history, says) in cases.items():
            with self.subTest(name), self.assertRaises(PageError) as wrong:
                region_reports(history).build(CTX)
            self.assertIn("slot `history`", str(wrong.exception), name)
            self.assertIn(says, str(wrong.exception), name)

    # -- 1.993 ---------------------------------------------------------
    def test_1_993_the_past_runs_are_read_as_the_user_newest_first(self) -> None:
        """
        Scenario 1.993: the rows the table reads.
        Given: three runs; the history table of the page.
        When:  its first page is read through lex-app's list view as the signed-in user, unsorted;
               then by a reader the model's permission_read refuses.
        Then:  the newest run is the first row, with its status; the refused reader gets no row.
        """
        for year in (2024, 2025, 2026):
            response = self.client.post(self.url_create("uiregionreport"), data={"region": "north", "year": year},
                                        format="json")
            self.assertEqual(response.status_code, 201)
        page = region_reports(ui.table(UiRegionReport, columns=["region", "year"]))
        state = _built_history(page)
        api = data.LexApi("test-token", base_url="http://testserver", client=ViewsClient(self.client))

        class Reading:
            scope: dict = {}

        Reading.resource, Reading.search_field = state.resource, state.search_field
        Reading.default_ordering = state.default_ordering

        async def first_page():
            async def as_user(_state):
                return api

            with patch.object(data, "lex_api", as_user):
                return await state._fetch_page(Reading(), offset=0, limit=25, sort=[], filters={}, query="")

        rows, count = asyncio.run(first_page())
        self.assertEqual(count, 3)
        self.assertEqual([row["year"] for row in rows], [2026, 2025, 2024], "the newest run first")
        self.assertEqual(rows[0]["is_calculated"], "NOT_CALCULATED", "with its status")
        UiRegionReport.may_read = False
        refused, _ = asyncio.run(first_page())
        self.assertEqual(refused, [], "the model's permission_read decides which runs a reader sees")
