"""An export under a value-list filter holds exactly the rows the grid shows.

Intent: the grid, its export and "Select All Records" read one filter model, so a
value-list (Set) filter must narrow the export exactly as it narrows the list:
ticking blank finds the empty cells (SQL ``IN`` alone never does), and ticking
nothing exports nothing. A regression exports rows the user filtered away -- or
misses the blank ones -- while the grid on screen looks right. The export has
two ways to build a sheet (the pandas path for a model with its own export
permission, the streaming path for the default one); both must agree with the
list.
Cluster 13i — scenario 13.46. Type: E.
Covers: lex/api/views/file_operations/ModelExport.py (the AG export paths, which filter through
ListModelEntries._apply_filter_model), lex/api/views/model_entries/List.py (``_build_filter_q``'s
Set branch).
Run: python -m lex pytest lex/test_project/tests/exports/test_13i_value_list_filters.py -v
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from rest_framework import status

from lex.test_project.tests._e2e_test_case import E2ETestCase

from .models import (
    ALL_MODELS,
    EXPORT_STATUS_ACTIVE,
    EXPORT_STATUS_ARCHIVED,
    FAST,
    ITEM,
    ExportCategory,
    ExportItem,
    FastExportItem,
)
from .test_13a_legacy_export import _assert_msg, _read_xlsx_response
from .test_13e_streaming_fast_export import _read_xlsx_stream

pytestmark = pytest.mark.exports


def _ag_export_payload(filter_model: dict) -> dict:
    """What the grid sends for *Export to Excel*: its request, filter model included."""
    return {
        "ag_export": {
            "request": {
                "startRow": 0,
                "endRow": 100,
                "rowGroupCols": [],
                "groupKeys": [],
                "pivotCols": [],
                "pivotMode": False,
                "valueCols": [],
                "sortModel": [],
                "filterModel": filter_model,
            },
            "columns": [{"dataKey": "name", "headerName": "Name"}],
            "includeColumnLabels": True,
        }
    }


class TestCluster13i_ValueListFilters(E2ETestCase):
    """Cluster 13i: the AG export honours Set filters like the list POST."""

    e2e_models = ALL_MODELS

    def setUp(self):
        super().setUp()
        # FastExportItem keeps the default permission_export, which needs the
        # Keycloak "export" scope (as in 13e); ExportItem grants its own.
        scopes = patch(
            "lex.core.models.LexModel.UserContext._resolve_keycloak_scopes",
            return_value={"export", "read", "list", "edit"},
        )
        scopes.start()
        self.addCleanup(scopes.stop)

        self.alpha = ExportCategory.objects.create(name="alpha")
        self.beta = ExportCategory.objects.create(name="beta")
        for name, category, state in (
            ("a1", self.alpha, EXPORT_STATUS_ACTIVE),
            ("a2", self.alpha, EXPORT_STATUS_ARCHIVED),
            ("b1", self.beta, EXPORT_STATUS_ACTIVE),
            ("loose", None, EXPORT_STATUS_ACTIVE),
        ):
            ExportItem.objects.create(name=name, amount="1.00", category=category, status=state)
            FastExportItem.objects.create(name=name, category=category)

    def _listed(self, model: str, filter_model: dict) -> list:
        resp = self.client.post(
            self.url_list(model),
            data={"filterModel": filter_model, "startRow": 0, "endRow": 100},
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK, resp.data)
        return sorted(row["name"] for row in resp.data["rowData"])

    def _export(self, model: str, filter_model: dict):
        return self.client.post(self.url_export(model), data=_ag_export_payload(filter_model), format="json")

    # -- 13.46 ---------------------------------------------------------
    def test_13_46_an_export_holds_the_rows_the_list_shows_under_a_value_list_filter(self):
        """
        Scenario 13.46: export and grid agree on value-list filters
        Given: items in two categories and one with no category, exported through the pandas path
               (ExportItem) and the streaming path (FastExportItem)
        When: each is listed and exported under a Set filter ticking one category and blank, and
              under one ticking nothing
        Then: the sheet holds exactly the rows the list POST returns -- that category's rows and the
              blank one; ticking nothing lists no rows and exports none (the export's "No data"
              answer)
        """
        with_blank = {"category": {"filterType": "set", "values": [str(self.alpha.pk), None]}}
        nothing = {"category": {"filterType": "set", "values": []}}

        for model, read_sheet in ((ITEM, _read_xlsx_response), (FAST, _read_xlsx_stream)):
            with self.subTest(model=model):
                listed = self._listed(model, with_blank)
                self.assertEqual(listed, ["a1", "a2", "loose"], "premise: the list shows alpha and blank")
                resp = self._export(model, with_blank)
                self.assertEqual(resp.status_code, status.HTTP_200_OK, _assert_msg(resp))
                exported = sorted(read_sheet(resp)["Name"].astype(str))
                self.assertEqual(exported, listed, "the sheet must hold exactly the rows the list returns")

                self.assertEqual(self._listed(model, nothing), [], "premise: nothing ticked lists no rows")
                resp = self._export(model, nothing)
                self.assertEqual(
                    resp.status_code, status.HTTP_404_NOT_FOUND,
                    f"nothing ticked must export no rows, not every row: {_assert_msg(resp)}",
                )
                self.assertEqual(resp.json(), {"error": "No data available for export"})
