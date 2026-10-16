"""A value list never shows what the user could not read in the grid.

Intent: value lists come from a new endpoint, outside the serializer that hides
fields per row in the grid. A model's permission_read can hide a row, or one
field on some rows; a value list must count neither, and a field the user can
read on no row must give an empty list rather than an error. A field the
serializer does not expose must be refused even though the model has it, and so
must a field the grid never shows as stored: a write-only field, a field whose
class rewrites its value, and every field of a serializer that rewrites its
rows. On the database path (a LexModel with the default read permission) the
read filter alone decides which rows count, so a record-level grant must limit
the list to that record. A regression leaks values of hidden fields or rows to
anyone who opens a filter.
Cluster 4o — scenarios 4.80–4.87. Type: E.
Covers: lex/api/views/model_entries/FilterValues.py (``_filterable_field``, ``_count_per_row``),
lex/api/utils/filterable_fields.py (``value_list_field``),
lex/api/views/model_entries/filter_backends.py (the read filter 4.81 and 4.87 rely on).
Run: python -m lex pytest lex/test_project/tests/permissions/test_4o_filter_values_permissions.py -v
"""

from __future__ import annotations

from collections import Counter
from unittest.mock import patch

import pytest
from rest_framework import serializers as drf_serializers
from rest_framework import status

from lex.api.middleware.keycloak_permissions import KeycloakPermissionsMiddleware
from lex.api.views.model_entries.FilterValues import uses_default_read_permission
from lex.test_project.tests._e2e_test_case import E2ETestCase

from .models import FieldLevelItem, KeycloakItem, RowFieldItem

pytestmark = pytest.mark.permissions

ROWS = "rowfielditem"
FIELD_LEVEL = "fieldlevelitem"
KEYCLOAK = "keycloakitem"
REGIONS = ("north", "south", "west")
TITLES = ("open-1", "open-2", "private-1", "private-2", "hidden-1")


class _WriteOnlyRegion(drf_serializers.ModelSerializer):
    """Accepts region on write and never renders it."""

    class Meta:
        model = RowFieldItem
        fields = ["id", "title", "region"]
        extra_kwargs = {"region": {"write_only": True}}


class _HiddenTitle(drf_serializers.ModelSerializer):
    """title is a HiddenField: set from its default on write, never rendered."""

    title = drf_serializers.HiddenField(default="(set on save)")

    class Meta:
        model = RowFieldItem
        fields = ["id", "title", "region"]


class _RedactedText(drf_serializers.CharField):
    """A field class that renders every value as the same placeholder."""

    def to_representation(self, value):
        return "(redacted)"


class _PlainText(drf_serializers.CharField):
    """A field subclass that keeps DRF's own rendering."""


class _RedactedRegion(drf_serializers.ModelSerializer):
    region = _RedactedText()
    title = _PlainText()

    class Meta:
        model = RowFieldItem
        fields = ["id", "title", "region"]


class _BlanksPrivateTitles(drf_serializers.ModelSerializer):
    """Rewrites its rows: a private row's title is rendered empty."""

    class Meta:
        model = RowFieldItem
        fields = ["id", "title", "region", "is_private"]

    def to_representation(self, instance):
        data = super().to_representation(instance)
        if instance.is_private:
            data["title"] = None
        return data


class TestCluster04o_FilterValuesPermissions(E2ETestCase):
    """Cluster 4o: filter_values honours row and field read permissions."""

    e2e_models = [RowFieldItem, FieldLevelItem, KeycloakItem]

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

    def _list_rows(self, model: str, query: str = "") -> list:
        """What the grid itself renders: the list POST's rows."""
        resp = self.client.post(
            self.url_list(model) + query,
            data={"filterModel": {}, "startRow": 0, "endRow": 100},
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK, resp.data)
        return resp.data["rowData"]

    def _use_serializers(self, serializers: dict) -> None:
        """Register named serializers on RowFieldItem for this test only.

        The restore is registered before the attribute changes. The model
        container rebuilds its serializer map when the registration changes.
        """
        had = hasattr(RowFieldItem, "api_serializers")
        original = getattr(RowFieldItem, "api_serializers", None)

        def _restore():
            if had:
                RowFieldItem.api_serializers = original
            elif hasattr(RowFieldItem, "api_serializers"):
                delattr(RowFieldItem, "api_serializers")

        self.addCleanup(_restore)
        RowFieldItem.api_serializers = serializers

    def _assert_refused(self, model: str, field: str, query: str, stored_values=()) -> None:
        resp = self._post(model, {"field": field, "filterModel": {}}, query)
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST, f"{field!r} {query}: {resp.data}")
        self.assertIn("not filterable", str(resp.data), f"{field!r} {query}")
        body = resp.content.decode()
        for value in stored_values:
            self.assertNotIn(value, body, f"{field!r} {query}: a stored value must never appear")

    def _grant_record_read(self, model_class, pk) -> None:
        """A Keycloak-UMA read grant on ONE record of ``model_class``.

        ``KeycloakPermissionsMiddleware.__call__`` is replaced for this test
        only: the request carries the grant a real Keycloak would answer for a
        user allowed to read that single record (``resource_set_id`` set), and
        nothing else. No call reaches Keycloak.
        """
        grant = [{
            "rsname": f"{model_class._meta.app_label}.{model_class.__name__}",
            "scopes": ["read"],
            "resource_set_id": str(pk),
        }]

        def _fake_call(mw_self, request):
            request.user_permissions = grant
            request.userinfo = {}
            request.client_roles = []
            return mw_self.get_response(request)

        patcher = patch.object(KeycloakPermissionsMiddleware, "__call__", _fake_call)
        patcher.start()
        self.addCleanup(patcher.stop)

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

    # -- 4.84 ----------------------------------------------------------
    def test_04_84_a_write_only_field_is_refused(self):
        """
        Scenario 4.84: a field the grid never renders has no value list
        Given: a serializer that makes region write-only (extra_kwargs), and one whose title is a
               HiddenField -- the list rows carry neither column
        When: their values are asked for through those serializers
        Then: each is the standard 400 "is not filterable", and none of the stored values appears
              in the response; an ordinary field of the same serializer is still listed
        """
        self._use_serializers({"write_only": _WriteOnlyRegion, "hidden_title": _HiddenTitle})
        self.assertTrue(
            all("region" not in row for row in self._list_rows(ROWS, "?serializer=write_only")),
            "premise: the grid never renders a write-only field",
        )
        self.assertTrue(
            all("title" not in row for row in self._list_rows(ROWS, "?serializer=hidden_title")),
            "premise: the grid never renders a HiddenField",
        )

        self._assert_refused(ROWS, "region", "?serializer=write_only", REGIONS)
        self._assert_refused(ROWS, "title", "?serializer=hidden_title", TITLES)
        self.assertEqual(
            self._counts(ROWS, "title", "?serializer=write_only"),
            {"open-1": 1, "open-2": 1, "private-1": 1, "private-2": 1},
            "a readable field of the same serializer keeps its value list",
        )

    # -- 4.85 ----------------------------------------------------------
    def test_04_85_a_field_class_that_rewrites_its_value_is_refused(self):
        """
        Scenario 4.85: the grid shows what the field class renders, not the column
        Given: a serializer whose region field class renders every value as "(redacted)", and
               whose title field class only inherits DRF's rendering
        When: region and title values are asked for through it
        Then: region is the standard 400 and no stored region appears; title is listed
        """
        self._use_serializers({"redacted": _RedactedRegion})
        rows = self._list_rows(ROWS, "?serializer=redacted")
        rendered = {row["region"] for row in rows if "region" in row}
        self.assertEqual(rendered, {"(redacted)"}, "premise: the grid shows only the placeholder")

        self._assert_refused(ROWS, "region", "?serializer=redacted", REGIONS)
        self.assertIn(
            "open-1", self._counts(ROWS, "title", "?serializer=redacted"),
            "a field class that only inherits DRF's to_representation keeps its value list",
        )

    # -- 4.86 ----------------------------------------------------------
    def test_04_86_a_serializer_that_rewrites_its_rows_lists_no_column(self):
        """
        Scenario 4.86: a serializer's own to_representation can mask any column on any row
        Given: a serializer that blanks a private row's title in its to_representation
        When: title -- a normally filterable column -- and is_private are asked for through it,
              and title through the default serializer
        Then: both are the standard 400 through the rewriting serializer, and no private title
              appears; the default serializer still lists title
        """
        self._use_serializers({"blanking": _BlanksPrivateTitles})
        rendered = {row["title"] for row in self._list_rows(ROWS, "?serializer=blanking")}
        self.assertNotIn("private-1", rendered, "premise: the grid shows no private title")

        self._assert_refused(ROWS, "title", "?serializer=blanking", ("private-1", "private-2"))
        self._assert_refused(ROWS, "is_private", "?serializer=blanking")
        self.assertEqual(
            self._counts(ROWS, "title"),
            {"open-1": 1, "open-2": 1, "private-1": 1, "private-2": 1},
            "the default serializer renders title as stored, so it keeps its value list",
        )

    # -- 4.87 ----------------------------------------------------------
    def test_04_87_a_record_level_grant_limits_the_database_count(self):
        """
        Scenario 4.87: on the database path the read filter alone decides which rows count
        Given: a LexModel keeping both default permission_read and can_read (the database path,
               unpatched), three rows, and a Keycloak read grant on ONE of them
        When: label values are asked for
        Then: only the granted row's label is counted, exactly as the list shows it; the other
              rows' labels appear nowhere in the response
        """
        granted = KeycloakItem.objects.create(label="granted-row")
        KeycloakItem.objects.create(label="withheld-row-1")
        KeycloakItem.objects.create(label="withheld-row-2")
        self._grant_record_read(KeycloakItem, granted.pk)
        self.assertTrue(
            uses_default_read_permission(KeycloakItem),
            "premise: both read methods are the LexModel default, so the database path is taken",
        )

        grid = dict(Counter(row["label"] for row in self._list_rows(KEYCLOAK)))
        self.assertEqual(grid, {"granted-row": 1}, "premise: the grid shows only the granted row")

        resp = self._post(KEYCLOAK, {"field": "label", "filterModel": {}})
        self.assertEqual(resp.status_code, status.HTTP_200_OK, resp.data)
        counts = {item["key"]: item["count"] for item in resp.data["values"]}
        self.assertEqual(counts, grid, "the value list must count exactly the rows the grid shows")
        self.assertNotIn("withheld-row", resp.content.decode(), "an ungranted row's value must never appear")
