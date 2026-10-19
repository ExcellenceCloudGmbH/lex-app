"""filter_values follows the list under lex-app-v2's secure defaults.

Intent: a value list is a second view of the list, so it must apply the list's
permission rules -- including the secure defaults lex-app-v2 turned on. A
permission check that raises withholds its record from the list
(LEX_SEC_FAIL_CLOSED), so the value list must not count that row, whichever way
it counts: row by row, or in the database when no per-row check applies. A field
the caller may not filter by (LEX_SEC_FILTERS) is treated like a field that does
not exist: the list ignores a filter on it, so the value list for it is refused
like any unknown field, the fields endpoint says the column has no value list,
and the filter bar leaves it out -- otherwise a user could tick a value the grid
then silently ignores. A model whose own permission_list refuses the caller has
no list, so it has no value list either (LEX_SEC_NOT_FOUND). Every switch that
restores the list's old behaviour restores the value list's too. A regression
counts a row the grid withholds, or offers a filter the grid refuses.
Cluster 4r — scenarios 4.119–4.124. Type: E.
Covers: lex/api/views/model_entries/FilterValues.py (``_filterable_field``, ``_count_per_row``,
``_count_in_database``), lex/api/utils/filterable_fields.py (``value_list_field``),
lex/api/views/model_info/Fields.py (``value_list``, ``list_ui.filter_bar``),
lex/api/serializers/base_serializers.py (``readable_field_names``),
lex/api/views/model_entries/filter_backends.py (the read filter), lex/api/filter_policy.py.
Run: python -m lex pytest lex/test_project/tests/permissions/test_4r_filter_values_secure_defaults.py -v
"""

from __future__ import annotations

import importlib
import os
from unittest import mock

import pytest
from django.contrib.auth.models import Group
from rest_framework import serializers as drf_serializers
from rest_framework import status

from lex.test_project.tests._e2e_test_case import E2ETestCase, _MockContainer

from .models import (
    FIELD_LEVEL,
    RAISING_READ,
    READ_CHECK_RAISES,
    FieldLevelItem,
    RaisingReadItem,
    ReadCheckRaisesItem,
    RowFieldItem,
)

pytestmark = pytest.mark.permissions

ROWS = "rowfielditem"

# The module (not the class of the same name) that resolves a view's serializer.
provider_module = importlib.import_module("lex.api.views.model_entries.mixins.ModelEntryProviderMixin")


def _env(**extra):
    """The process environment without any security switch, plus ``extra``."""
    clean = {k: v for k, v in os.environ.items() if not k.startswith(("LEX_SEC_", "LEX_LEGACY_SECURITY"))}
    clean.update(extra)
    return mock.patch.dict(os.environ, clean, clear=True)


class _NoPerRowCheck(drf_serializers.ModelSerializer):
    """A serializer that is not a LexSerializer: it applies no per-row read check
    (as auth.User's does), so filter_values counts its model in the database."""

    class Meta:
        model = RaisingReadItem
        fields = ["id", "name"]


class TestCluster04r_FilterValuesSecureDefaults(E2ETestCase):
    """Cluster 4r: filter_values under LEX_SEC_FAIL_CLOSED, LEX_SEC_FILTERS and LEX_SEC_NOT_FOUND."""

    e2e_models = [RowFieldItem, FieldLevelItem, RaisingReadItem, ReadCheckRaisesItem]

    def setUp(self):
        super().setUp()
        defaults = _env()
        defaults.start()
        self.addCleanup(defaults.stop)

    # -- helpers -------------------------------------------------------

    def _post_values(self, model: str, field: str, filter_model=None):
        return self.client.post(
            self.url_filter_values(model), {"field": field, "filterModel": filter_model or {}}, format="json",
        )

    def _counts(self, model: str, field: str) -> dict:
        resp = self._post_values(model, field)
        self.assertEqual(resp.status_code, status.HTTP_200_OK, resp.content[:300])
        return {item["key"]: item["count"] for item in resp.json()["values"]}

    def _list_names(self, model: str) -> list:
        resp = self.client.post(
            self.url_list(model), {"filterModel": {}, "startRow": 0, "endRow": 100}, format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK, resp.content[:300])
        return sorted(row["name"] for row in resp.json()["rowData"] if "name" in row)

    def _fields(self, model: str) -> dict:
        resp = self.client.get(self._url("model-info-fields", model_container=_MockContainer(model)))
        self.assertEqual(resp.status_code, status.HTTP_200_OK, resp.content[:300])
        return resp.json()

    def _join_hr(self) -> None:
        self.user.groups.add(Group.objects.get_or_create(name="hr")[0])

    # -- 4.119 ---------------------------------------------------------
    def test_04_119_a_raising_permission_read_withholds_the_row_row_by_row(self):
        """
        Scenario 4.119: per-row counting, a permission_read that raises
        Given: RaisingReadItem rows "fine" and "boom"; permission_read raises for "boom"
        When: name values are asked for, by default and with LEX_SEC_FAIL_CLOSED=false
        Then: by default "boom" is counted nowhere and the counts match the list, which shows
              "fine" only; with LEX_SEC_FAIL_CLOSED=false (where the list itself fails) the value
              list still leaves "boom" out, as it did before the switch existed
        """
        RaisingReadItem.objects.create(name="fine")
        RaisingReadItem.objects.create(name="boom")

        self.assertEqual(self._list_names(RAISING_READ), ["fine"], "premise: the list withholds the row")
        self.assertEqual(self._counts(RAISING_READ, "name"), {"fine": 1}, "a withheld row is never counted")
        with _env(LEX_SEC_FAIL_CLOSED="false"):
            self.assertEqual(
                self._counts(RAISING_READ, "name"), {"fine": 1},
                "legacy: a check that raises still keeps the row out of the value list",
            )

    # -- 4.120 ---------------------------------------------------------
    def test_04_120_a_raising_can_read_withholds_the_row_row_by_row(self):
        """
        Scenario 4.120: per-row counting, a can_read that raises
        Given: ReadCheckRaisesItem rows "fine" and "boom"; permission_read admits both, so the read
               filter keeps both, and can_read raises for "boom"
        When: name values are asked for
        Then: "boom" is counted nowhere, matching the list, whose serializer withholds the record
        """
        ReadCheckRaisesItem.objects.create(name="fine")
        ReadCheckRaisesItem.objects.create(name="boom")

        self.assertEqual(self._list_names(READ_CHECK_RAISES), ["fine"], "premise: the list withholds the record")
        self.assertEqual(self._counts(READ_CHECK_RAISES, "name"), {"fine": 1}, "a withheld record is never counted")

    # -- 4.121 ---------------------------------------------------------
    def test_04_121_a_raising_permission_read_withholds_the_row_in_the_database_count(self):
        """
        Scenario 4.121: database counting, a permission_read that raises
        Given: RaisingReadItem rows "fine" and "boom", served by a serializer with no per-row read
               check, so filter_values counts in the database what the read filter leaves
        When: name values are asked for, by default and with LEX_SEC_FAIL_CLOSED=false
        Then: by default the read filter withholds "boom" from the list and from the count alike;
              with LEX_SEC_FAIL_CLOSED=false the list shows "boom" again, and so does the count
        """
        RaisingReadItem.objects.create(name="fine")
        RaisingReadItem.objects.create(name="boom")
        original = provider_module.resolve_serializer_class

        def _resolve(model_class, mapping, choice):
            if model_class is RaisingReadItem:
                return _NoPerRowCheck
            return original(model_class, mapping, choice)

        with mock.patch.object(provider_module, "resolve_serializer_class", _resolve):
            self.assertEqual(self._list_names(RAISING_READ), ["fine"], "premise: the read filter withholds it")
            self.assertEqual(self._counts(RAISING_READ, "name"), {"fine": 1}, "the database count withholds it too")
            with _env(LEX_SEC_FAIL_CLOSED="false"):
                self.assertEqual(self._list_names(RAISING_READ), ["boom", "fine"], "premise: legacy lists it")
                self.assertEqual(
                    self._counts(RAISING_READ, "name"), {"boom": 1, "fine": 1},
                    "legacy: the count follows the list",
                )

    # -- 4.122 ---------------------------------------------------------
    def test_04_122_a_field_the_caller_may_not_filter_by_has_no_value_list(self):
        """
        Scenario 4.122: LEX_SEC_FILTERS -- a field withheld on every record is treated as absent
        Given: FieldLevelItem rows with salaries 10 and 20; a regular user's permission_read admits
               every row but withholds sensitive_salary on all of them, so the list ignores a
               filter on it
        When: sensitive_salary and public_name values are asked for, by default, with
              LEX_SEC_FILTERS=false or LEX_LEGACY_SECURITY=true, and as a member of "hr", who may
              read salaries
        Then: by default sensitive_salary is the standard 400 "is not filterable" -- the answer for
              a field that does not exist -- with no salary in the body, while public_name is
              listed; with either switch it is 200 with an empty list (no row shows it); the hr
              member gets both salaries
        """
        FieldLevelItem.objects.create(public_name="a", sensitive_salary=10)
        FieldLevelItem.objects.create(public_name="b", sensitive_salary=20)

        resp = self._post_values(FIELD_LEVEL, "sensitive_salary")
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST, resp.content[:300])
        body = resp.content.decode()
        self.assertIn("not filterable", body)
        for salary in ("10", "20"):
            self.assertNotIn(salary, body, "no salary may appear")
        self.assertEqual(self._counts(FIELD_LEVEL, "public_name"), {"a": 1, "b": 1}, "a visible field keeps its list")

        for switch in ({"LEX_SEC_FILTERS": "false"}, {"LEX_LEGACY_SECURITY": "true"}):
            with self.subTest(switch=switch), _env(**switch):
                self.assertEqual(self._counts(FIELD_LEVEL, "sensitive_salary"), {}, "legacy: 200 with nothing to show")

        self._join_hr()
        self.assertEqual(
            self._counts(FIELD_LEVEL, "sensitive_salary"), {"10": 1, "20": 1},
            "a caller who may read the field gets its values",
        )

    # -- 4.123 ---------------------------------------------------------
    def test_04_123_the_fields_endpoint_and_the_bar_agree_with_filter_values(self):
        """
        Scenario 4.123: the frontend is told the same thing filter_values answers
        Given: FieldLevelItem with filter_bar = ["public_name", "sensitive_salary"], and a regular
               user whose permission_read withholds sensitive_salary on every record
        When: GET .../fields is asked by default, with LEX_SEC_FILTERS=false, and as an "hr" member
        Then: by default sensitive_salary has value_list false and the bar holds public_name only;
              with LEX_SEC_FILTERS=false, and for the hr member, the column has a value list and
              the bar holds both
        """

        def _flags_and_bar():
            body = self._fields(FIELD_LEVEL)
            flags = {info["name"]: info["value_list"] for info in body["fields"]}
            return flags, body["list_ui"]["filter_bar"]

        with mock.patch.object(FieldLevelItem, "filter_bar", ["public_name", "sensitive_salary"], create=True):
            flags, bar = _flags_and_bar()
            self.assertIs(flags["public_name"], True, "a visible column keeps its value list")
            self.assertIs(flags["sensitive_salary"], False, "a column the caller may not filter by has none")
            self.assertEqual(bar, ["public_name"], "the bar never names a column filter_values refuses")

            with _env(LEX_SEC_FILTERS="false"):
                flags, bar = _flags_and_bar()
            self.assertIs(flags["sensitive_salary"], True, "legacy: the column is listed again")
            self.assertEqual(bar, ["public_name", "sensitive_salary"], "legacy: the bar holds both")

            self._join_hr()
            flags, bar = _flags_and_bar()
            self.assertIs(flags["sensitive_salary"], True, "a caller who may read the field gets a value list")
            self.assertEqual(bar, ["public_name", "sensitive_salary"])

    # -- 4.124 ---------------------------------------------------------
    def test_04_124_no_list_means_no_value_list(self):
        """
        Scenario 4.124: LEX_SEC_NOT_FOUND -- permission_list refuses the caller
        Given: RowFieldItem rows, and a permission_list that refuses this user
        When: the list and filter_values are asked, by default and with LEX_SEC_NOT_FOUND=false or
              LEX_LEGACY_SECURITY=true
        Then: both answer 404 by default and no value appears; with either switch filter_values
              answers 200 as before
        """
        RowFieldItem.objects.create(title="open-1", region="north")
        with mock.patch.object(RowFieldItem, "permission_list", lambda self, uc: False):
            listed = self.client.post(
                self.url_list(ROWS), {"filterModel": {}, "startRow": 0, "endRow": 100}, format="json",
            )
            self.assertEqual(listed.status_code, status.HTTP_404_NOT_FOUND, "premise: the list answers 404")
            values = self._post_values(ROWS, "region")
            self.assertEqual(values.status_code, status.HTTP_404_NOT_FOUND, values.content[:300])
            self.assertNotIn("north", values.content.decode(), "no value may appear")

            for switch in ({"LEX_SEC_NOT_FOUND": "false"}, {"LEX_LEGACY_SECURITY": "true"}):
                with self.subTest(switch=switch), _env(**switch):
                    self.assertEqual(self._counts(ROWS, "region"), {"north": 1}, "legacy: the value list answers again")
