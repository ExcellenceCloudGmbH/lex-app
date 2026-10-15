"""A value list never shows what the user could not read in the grid.

Intent: value lists come from a new endpoint, outside the serializer that hides
fields per row in the grid. A model's permission_read can hide a row, or one
field on some rows; a value list must count neither, and a field the user can
read on no row must give an empty list rather than an error. A field the
serializer does not expose must be refused even though the model has it. A
regression leaks values of hidden fields to anyone who opens a filter.
Cluster 4o — scenarios 4.80–4.83. Type: E.
Covers: lex/api/views/model_entries/FilterValues.py (``_filterable_field``, ``_count_per_row``).
Run: python -m lex pytest lex/test_project/tests/permissions/test_4o_filter_values_permissions.py -v
"""

from __future__ import annotations

import pytest
from rest_framework import serializers as drf_serializers
from rest_framework import status

from lex.test_project.tests._e2e_test_case import E2ETestCase

from .models import FieldLevelItem, RowFieldItem

pytestmark = pytest.mark.permissions

ROWS = "rowfielditem"
FIELD_LEVEL = "fieldlevelitem"


class TestCluster04o_FilterValuesPermissions(E2ETestCase):
    """Cluster 4o: filter_values honours row and field read permissions."""

    e2e_models = [RowFieldItem, FieldLevelItem]

    def setUp(self):
        super().setUp()
        RowFieldItem.objects.create(title="open-1", region="north")
        RowFieldItem.objects.create(title="open-2", region="north")
        RowFieldItem.objects.create(title="private-1", region="north", is_private=True)
        RowFieldItem.objects.create(title="private-2", region="south", is_private=True)
        RowFieldItem.objects.create(title="hidden-1", region="west")
        FieldLevelItem.objects.create(public_name="a", sensitive_salary=10)
        FieldLevelItem.objects.create(public_name="b", sensitive_salary=20)

    def _post(self, model: str, body: dict, query: str = ""):
        return self.client.post(self.url_filter_values(model) + query, data=body, format="json")

    def _counts(self, model: str, field: str, query: str = "") -> dict:
        resp = self._post(model, {"field": field, "filterModel": {}}, query)
        self.assertEqual(resp.status_code, status.HTTP_200_OK, resp.data)
        return {item["key"]: item["count"] for item in resp.data["values"]}

    # -- 4.80 ----------------------------------------------------------
    def test_04_80_a_field_hidden_on_some_rows_is_counted_only_where_readable(self):
        """
        Scenario 4.80: per-row field hiding
        Given: two open rows in north, two private rows (north, south) that hide region
        When: region values are asked for
        Then: only the open rows count: north 2; south never appears
        """
        self.assertEqual(self._counts(ROWS, "region"), {"north": 2})

    # -- 4.81 ----------------------------------------------------------
    def test_04_81_rows_the_user_cannot_see_are_never_counted(self):
        """
        Scenario 4.81: hidden rows stay hidden
        Given: a row whose permission_read denies it
        When: title and is_private values are asked for
        Then: the hidden row is in neither list
        """
        self.assertNotIn("hidden-1", self._counts(ROWS, "title"))
        self.assertEqual(self._counts(ROWS, "is_private"), {"false": 2, "true": 2})

    # -- 4.82 ----------------------------------------------------------
    def test_04_82_a_field_readable_on_no_row_gives_an_empty_list(self):
        """
        Scenario 4.82: nothing to show is not an error
        Given: a regular user, who may read only id and public_name
        When: sensitive_salary values are asked for
        Then: 200 with no values
        """
        resp = self._post(FIELD_LEVEL, {"field": "sensitive_salary", "filterModel": {}})
        self.assertEqual(resp.status_code, status.HTTP_200_OK, resp.data)
        self.assertEqual(resp.data["values"], [], "no salary may leak")

    # -- 4.83 ----------------------------------------------------------
    def test_04_83_a_field_the_serializer_does_not_expose_is_refused(self):
        """
        Scenario 4.83: the serializer decides which columns exist
        Given: a named serializer exposing only title
        When: region values are asked for through it
        Then: 400 "is not filterable", although the model has region
        """

        class TitleOnly(drf_serializers.ModelSerializer):
            class Meta:
                model = RowFieldItem
                fields = ["id", "title"]

        had = hasattr(RowFieldItem, "api_serializers")
        original = getattr(RowFieldItem, "api_serializers", None)

        def _restore():
            if had:
                RowFieldItem.api_serializers = original
            elif hasattr(RowFieldItem, "api_serializers"):
                delattr(RowFieldItem, "api_serializers")

        self.addCleanup(_restore)
        RowFieldItem.api_serializers = {"title_only": TitleOnly}

        resp = self._post(ROWS, {"field": "region", "filterModel": {}}, "?serializer=title_only")
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST, resp.data)
        self.assertIn("not filterable", str(resp.data))
