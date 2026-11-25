"""lex.ui: a foreign key shows the related record's name, in a model table, a run's history and its facts.

Intent: lex-app's API sends a foreign key as the related record's id and, beside it,
`<fk>__short_description`, the related record's name -- what lex-app's own grid shows. A lex.ui table
over a model showed the bare id ("17") in a "Customer" column, as did a calculation's past runs and a
run's input facts. A foreign key's column now shows the name, sorts by the key (as lex-app's grid
does), and the row still carries the id for a row's action. A regression is an id where a person reads
a name, or a sort that the API refuses.

Cluster 1es -- scenarios 1.1026-1.1027. Type: E (the rows are read through lex-app's real list view).
Covers: lex/lex_app/reflex/data.py (shown_field, lex_columns, lex_grid_columns, LexTableState's ordering),
lex/lex_app/reflex/ui/pages/tables.py (a model table's columns), lex/lex_app/reflex/ui/pages/calculation.py
(a run's facts), lex/lex_app/reflex/ui/context.py (Api.list fields=).
Run: python -m lex pytest lex/test_project/tests/init/test_1es_ui_foreign_key_names.py -v
"""

from __future__ import annotations

import asyncio
import re
from unittest.mock import patch

import pytest
from reflex.compiler.compiler import compile_page

from lex import ui
from lex.lex_app.reflex import data
from lex.lex_app.reflex.ui import context
from lex.lex_app.reflex.ui.pages import rows_table
from lex.lex_app.reflex.ui.pages.parts import Build
from lex.test_project.tests._e2e_test_case import E2ETestCase
from lex.test_project.tests.init._ui_calc_aids import ViewsClient
from lex.test_project.tests.init.test_1cf_ui_context import _in_thread, _session
from lex.test_project.tests.init.models import UI_FK_MODELS, UiCustomer, UiCustomerReport

pytestmark = pytest.mark.init

ROUTE = "/reports/customers"
CTX = Build(page="customer_reports", route=ROUTE, requires=())      # a @ui.page without requires=


class TestCluster01es_ForeignKeyNames(E2ETestCase):
    """1.1026-1.1027: the related record's name, in a model table and on a calculation's page."""

    e2e_models = UI_FK_MODELS

    def _read(self, state, sort):
        api = data.LexApi("test-token", base_url="http://testserver", client=ViewsClient(self.client))

        class Reading:
            scope: dict = {}

        Reading.resource, Reading.search_field = state.resource, state.search_field
        Reading.default_ordering = state.default_ordering

        async def page():
            async def as_user(_state):
                return api

            with patch.object(data, "lex_api", as_user):
                return await state._fetch_page(Reading(), offset=0, limit=25, sort=sort, filters={}, query="")

        return asyncio.run(page())

    # -- 1.1026 --------------------------------------------------------
    def test_1_1026_a_model_tables_foreign_key_shows_the_related_records_name(self) -> None:
        """
        Scenario 1.1026: ui.table(Model, columns=["year", "customer"]).
        Given: two customers and a report on each, read through lex-app's list view as the user.
        When:  the table is built and compiled, its first page read, then sorted by the customer.
        Then:  the customer column shows `customer__short_description`, headed "Customer"; the rows
               carry the names and still the ids; sorting by the column sends `ordering=-customer`
               (the key, as lex-app's grid sorts) and the API answers; lex_columns and the grid's
               columns show the name too; `self.api.list(fields=["customer"])` keeps the name.
        """
        acme, beta = UiCustomer.objects.create(name="Acme Ltd"), UiCustomer.objects.create(name="Beta plc")
        UiCustomerReport.objects.create(customer=acme, year=2025)
        UiCustomerReport.objects.create(customer=beta, year=2026)
        table = ui.table(UiCustomerReport, columns=["year", ui.column("customer", label="Customer")], visible=True)
        _, module = compile_page("/customers", table.build(Build(page="reports", route="/customers",
                                                                  caption="Reports")))
        state = rows_table._CACHE_BY_TABLE[table]
        self.assertEqual([(c["field"], c["header"]) for c in state.table_columns],
                         [("year", "Year"), ("customer__short_description", "Customer")])
        self.assertIn("customer__short_description", module, "the cell shows the name")
        rows, count = self._read(state, [])
        self.assertEqual(count, 2)
        self.assertEqual(sorted((row["customer__short_description"], row["customer"]) for row in rows),
                         [("Acme Ltd", acme.pk), ("Beta plc", beta.pk)], "the name, and the id beside it")
        newest_customer_first, _ = self._read(state, [{"col_id": "customer__short_description", "sort": "desc"}])
        self.assertEqual([row["customer"] for row in newest_customer_first], [beta.pk, acme.pk],
                         "sorted by the key, as lex-app's grid sorts a foreign key")
        self.assertEqual([c["field"] for c in data.lex_columns(UiCustomerReport, ["customer"])],
                         ["customer__short_description"])
        self.assertEqual([c["field"] for c in data.lex_grid_columns(UiCustomerReport, ["customer"])],
                         ["customer__short_description"])
        self.assertEqual(data.shown_field(UiCustomerReport._meta.get_field("year")), "year")
        listed = _in_thread(_session(["tok"], client=ViewsClient(self.client)),
                            lambda inv: context.Api(inv).list(UiCustomerReport, fields=["customer"], ordering="year"))
        self.assertEqual([dict(row) for row in listed],
                         [{"customer": acme.pk, "customer__short_description": "Acme Ltd"},
                          {"customer": beta.pk, "customer__short_description": "Beta plc"}],
                         "self.api.list(fields=[fk]) keeps the related record's name beside the id")

    # -- 1.1027 --------------------------------------------------------
    def test_1_1027_a_calculations_history_and_run_show_the_customers_name(self) -> None:
        """
        Scenario 1.1027: a calculation whose input is a foreign key.
        Given: ui.calculation(UiCustomerReport) with the customer as a field and in the result, and
               history=ui.table(UiCustomerReport, columns=["year", "customer"]).
        When:  the page is built and compiled.
        Then:  the past runs' customer column shows the name; the run's input fact and its result
               fact read `customer__short_description` from the run, never the bare id.
        """
        page = ui.calculation(UiCustomerReport, title="Customer report",
                              fields=[ui.field("customer", options={1: "Acme Ltd"}, required=True),
                                      ui.field("year", kind="year", required=True)],
                              result=[UiCustomerReport.customer],
                              history=ui.table(UiCustomerReport, columns=["year", "customer"]))
        _, module = compile_page(ROUTE, page.build(CTX))
        history = rows_table._CACHE_BY_TABLE[page.built_history]
        self.assertIn("customer__short_description", [c["field"] for c in history.table_columns],
                      "the past runs show the customer's name")
        self.assertGreaterEqual(len(re.findall(r"run_rx_state_\?\.\[\"customer__short_description\"\]", module)), 2,
                                "the run's input fact and its result fact read the name")
