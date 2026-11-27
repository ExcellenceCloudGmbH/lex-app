"""lex.ui: a search through self.api matches the model's own text, never lex-app's audit field.

Intent: `self.api.list(Model, search="acme")` is how a handler finds records by what a user typed.
Every LexModel inherits lex-app's audit fields (`created_at`, `edited_at`, `created_by`,
`edited_by`), and they come first in the model's fields, so "the first text field" was always
`created_by` -- who created the record -- and a search for a customer's name found nothing.
lex-app's API has no search field of its own (its grid filters a column, its global search reads
every field), so the search matches the first plain text field the model itself declares. A
regression is a search that silently returns no rows on every LexModel.

Cluster 1eq -- scenario 1.1023. Type: U.
Covers: lex/lex_app/reflex/data.py (_default_search_field, _framework_fields, lex_table's default),
lex/lex_app/reflex/ui/context.py (Api.list search=), lex/lex_app/reflex/ui/pages/tables.py (a model
table's search column).
Run: python -m lex pytest lex/test_project/tests/init/test_1eq_ui_api_search_field.py -v
"""

from __future__ import annotations

from unittest.mock import patch

import httpx
import pytest
from django.contrib.auth.models import Group
from django.test import SimpleTestCase

from lex import ui
from lex.lex_app.reflex import data
from lex.lex_app.reflex.ui import context
from lex.lex_app.reflex.ui.pages import rows_table
from lex.lex_app.reflex.ui.pages.parts import Build
from lex.test_project.tests.init.models import IncidentDatetimeItem, UiRegionReport, UiRegionReportLine
from lex.test_project.tests.init.test_1cf_ui_context import _in_thread, _session

pytestmark = pytest.mark.init


class TestCluster01eq_ApiSearchField(SimpleTestCase):
    """1.1023: the search field is the model's own."""

    # -- 1.1023 --------------------------------------------------------
    def test_1_1023_a_search_matches_the_models_own_text_not_created_by(self) -> None:
        """
        Scenario 1.1023: self.api.list(Model, search=...) on models that inherit created_by.
        Given: a LexModel whose first own text field is `customer` (after a foreign key), a
               calculation model whose own `is_calculated` has choices and whose first own text
               field is `region`, a LexModel with `name`, a Django model with `name`; lex-app's
               API behind a recording transport.
        When:  a handler lists each with search="acme"; a table over the model searches; a model
               whose only text fields are lex-app's is searched.
        Then:  the search goes to `customer__icontains`, `region__icontains`, `name__icontains` --
               never `created_by__icontains`; lex_table's default and a model table showing
               created_by first search the model's own column; the model without a text field of
               its own is refused, saying to filter by a field instead.
        """
        seen: list[dict] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(dict(request.url.params.multi_items()))
            return httpx.Response(200, json={"count": 0, "results": []})

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

        def body(inv):
            api = context.Api(inv)
            for model in (UiRegionReportLine, UiRegionReport, IncidentDatetimeItem, Group):
                api.list(model, search="acme")

        _in_thread(_session(["tok"], client=client), body)
        searched = [[key for key in query if key.endswith("__icontains")] for query in seen]
        self.assertEqual(searched, [["customer__icontains"], ["region__icontains"], ["name__icontains"],
                                    ["name__icontains"]], "each model's own text field, never created_by")
        self.assertEqual(data._default_search_field(UiRegionReportLine), "customer", "lex_table's default too")
        self.assertIn("created_by", data._framework_fields(UiRegionReportLine))
        self.assertNotIn("customer", data._framework_fields(UiRegionReportLine))

        shown = ui.table(UiRegionReportLine, columns=["created_by", "customer"], search=True, visible=True)
        shown.build(Build(page="lines", route="/lines", caption="Lines"))
        self.assertEqual(rows_table._CACHE_BY_TABLE[shown].search_field, "customer",
                         "a model table searches the model's own column, though created_by is shown first")


        with self.assertRaises(ValueError) as refused, patch.object(data, "_default_search_field", lambda model: ""):
            _in_thread(_session(["tok"], client=client),
                       lambda inv: context.Api(inv).list(UiRegionReportLine, search="acme"))
        message = str(refused.exception)
        self.assertIn("UiRegionReportLine declares no text field to search", message)
        self.assertIn("filters={'<field>__icontains': text}", message, "it says what to write instead")
