"""Value-list filters: Set keys are converted, blanks match, nothing ticked means no rows.

Intent: the grid's value-list (Set) filter sends each ticked value as a string key,
a blank as ``null``, and a Multi filter as a condition plus a value list. The list
endpoint must turn exactly those into SQL: keys converted to the column's type,
``null`` matching blank cells (SQL ``IN`` never does), an empty selection matching
no rows, and both halves of a Multi filter applied. A regression silently widens or
empties the grid: the user sees rows they did not ask for, or none, and export and
"Select All Records" inherit the same answer.
Cluster 14f — scenarios 14.36–14.41. Type: E.
Covers: lex/api/views/model_entries/List.py (``_build_filter_q``, ``_set_filter_values``).
Run: python -m lex pytest lex/test_project/tests/queries/test_14f_value_list_filters.py -v
"""

from __future__ import annotations

from datetime import date

import pytest
from rest_framework import status

from lex.test_project.tests._e2e_test_case import E2ETestCase

from .models import (
    ALL_MODELS,
    ITEM,
    QUERY_STATUS_ACTIVE,
    QUERY_STATUS_ARCHIVED,
    QUERY_STATUS_DRAFT,
    QueryCategory,
    QueryItem,
)

pytestmark = pytest.mark.queries

EVERY_NAME = {"alpha-small", "alpha-mid", "beta-mid", "loose"}


def _request(filter_model: dict) -> dict:
    return {
        "startRow": 0,
        "endRow": 100,
        "rowGroupCols": [],
        "groupKeys": [],
        "pivotCols": [],
        "pivotMode": False,
        "valueCols": [],
        "sortModel": [],
        "filterModel": filter_model,
    }


class TestCluster14f_ValueListFilters(E2ETestCase):
    """Cluster 14f: Set and Multi filter models on the AG Grid list endpoint."""

    e2e_models = ALL_MODELS

    def setUp(self):
        super().setUp()
        self.alpha = QueryCategory.objects.create(name="alpha")
        self.beta = QueryCategory.objects.create(name="beta")
        QueryItem.objects.create(
            name="alpha-small", count=1, is_active=True, status=QUERY_STATUS_ACTIVE,
            created_on=date(2026, 1, 15), category=self.alpha,
        )
        QueryItem.objects.create(
            name="alpha-mid", count=5, is_active=False, status=QUERY_STATUS_ARCHIVED,
            created_on=date(2026, 3, 10), category=self.alpha,
        )
        QueryItem.objects.create(
            name="beta-mid", count=0, is_active=True, status=QUERY_STATUS_DRAFT,
            created_on=date(2026, 2, 20), category=self.beta,
        )
        QueryItem.objects.create(
            name="loose", count=7, is_active=True, status=QUERY_STATUS_ACTIVE,
            created_on=None, category=None,
        )

    def _post(self, filter_model: dict):
        return self.client.post(
            self.url_list(ITEM), data={"request": _request(filter_model)}, format="json",
        )

    def _names(self, filter_model: dict) -> set[str]:
        resp = self._post(filter_model)
        self.assertEqual(resp.status_code, status.HTTP_200_OK, resp.data)
        return {row["name"] for row in resp.data["rowData"]}

    # -- 14.36 ---------------------------------------------------------
    def test_14_36_keys_are_converted_to_the_column_type(self):
        """
        Scenario 14.36: a ticked value arrives as a string and still matches
        Given: rows with integer, boolean, date and foreign-key columns
        When: Set filters send their keys as strings
        Then: each key matches the rows holding that value
        """
        self.assertEqual(
            self._names({"count": {"filterType": "set", "values": ["1", "5"]}}),
            {"alpha-small", "alpha-mid"}, "integer keys arrive as strings",
        )
        self.assertEqual(
            self._names({"is_active": {"filterType": "set", "values": ["false"]}}),
            {"alpha-mid"}, '"false" is the wire key for False; Django would reject it as is',
        )
        self.assertEqual(
            self._names({"created_on": {"filterType": "set", "values": ["2026-02-20"]}}),
            {"beta-mid"}, "a date key is ISO",
        )
        self.assertEqual(
            self._names({"category": {"filterType": "set", "values": [str(self.beta.pk)]}}),
            {"beta-mid"}, "a foreign key's key is the related primary key",
        )

    # -- 14.37 ---------------------------------------------------------
    def test_14_37_null_matches_blank_cells(self):
        """
        Scenario 14.37: "(Blank)" in a value list finds the empty cells
        Given: a row with no date and no category
        When: a Set filter carries null, alone or beside a value
        Then: the blank row matches — SQL IN alone never matches NULL
        """
        self.assertEqual(
            self._names({"created_on": {"filterType": "set", "values": [None]}}), {"loose"},
            "null alone must find the blank row",
        )
        self.assertEqual(
            self._names({"category": {"filterType": "set", "values": [str(self.alpha.pk), None]}}),
            {"alpha-small", "alpha-mid", "loose"}, "a value and null together OR their matches",
        )

    # -- 14.38 ---------------------------------------------------------
    def test_14_38_nothing_ticked_means_no_rows(self):
        """
        Scenario 14.38: an empty selection is "match nothing", as AG Grid means it
        Given: four rows
        When: a Set filter arrives with an empty values list
        Then: no row matches, rather than every row
        """
        self.assertEqual(
            self._names({"status": {"filterType": "set", "values": []}}), set(),
            "an empty selection used to be dropped, which returned every row",
        )

    # -- 14.39 ---------------------------------------------------------
    def test_14_39_both_halves_of_a_multi_filter_apply(self):
        """
        Scenario 14.39: a condition and a value list on one column are ANDed
        Given: a text column with a Multi filter
        When: the condition says "contains mid" and the list ticks two names
        Then: only rows passing both match; an empty half is ignored
        """
        both = {"name": {"filterType": "multi", "filterModels": [
            {"filterType": "text", "type": "contains", "filter": "mid"},
            {"filterType": "set", "values": ["alpha-mid", "loose"]},
        ]}}
        self.assertEqual(self._names(both), {"alpha-mid"}, "both halves must apply")
        list_only = {"name": {"filterType": "multi", "filterModels": [
            None, {"filterType": "set", "values": ["loose"]},
        ]}}
        self.assertEqual(self._names(list_only), {"loose"}, "a null half is skipped")

    # -- 14.40 ---------------------------------------------------------
    def test_14_40_an_unconvertible_key_is_a_400_naming_the_field(self):
        """
        Scenario 14.40: a bad key is refused, not silently widened
        Given: an integer column
        When: a Set filter sends a key that is not an integer
        Then: 400, and the error names the column
        """
        resp = self._post({"count": {"filterType": "set", "values": ["many"]}})
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST, resp.data)
        self.assertIn("count", str(resp.data), "the error must name the field")

    # -- 14.41 ---------------------------------------------------------
    def test_14_41_an_unknown_filter_type_is_ignored_and_logged(self):
        """
        Scenario 14.41: compatibility for shapes the server does not know
        Given: a filter model with an unknown filterType
        When: the list is requested
        Then: the filter is ignored as before, and the server logs the field and type
        """
        with self.assertLogs("lex.api.views.model_entries.List", level="INFO") as logs:
            names = self._names({"name": {"filterType": "fancy", "filter": "x"}})
        self.assertEqual(names, EVERY_NAME, "unknown types keep their old meaning: no filter")
        self.assertTrue(
            any("fancy" in line and "name" in line for line in logs.output),
            f"the log must name the field and the type; got {logs.output!r}",
        )
