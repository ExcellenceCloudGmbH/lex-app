"""Value lists and the list's GET lookups under lex-app-v2's filter allow-list.

Intent: lex-app-v2's secure defaults (LEX_SEC_FILTERS) let a list request filter
only by fields a client can already see; a field the caller cannot see is treated
like one that does not exist. A value list (``filter_values``) is a filter of the
same list, so the same rule holds there: a ``filterModel`` entry on such a field
is ignored exactly as the list POST ignores it, and the GET list's ``__blank``,
``__exact`` and ``__in`` lookups are refused like any other filter on it.
``LEX_SEC_FILTERS=false`` restores the old behaviour on both. A model that
declares no permission methods at all must still list every row and field: its
value list counts in the database on the strength of that. A regression either
lets a value list narrow by a field the list refuses to filter by (answering
"which rows have salary > X" one request at a time), or stops a model without
permission methods from listing at all.
Cluster 14l — scenarios 14.84–14.86. Type: E.
Covers: lex/api/serializers/base_serializers.py (``_resolve_target_and_visible_fields``),
lex/api/views/model_entries/FilterValues.py (inherited ``_apply_filter_model``),
lex/api/views/model_entries/List.py (``apply_query_param_filters``, ``_build_query_from_values``),
lex/api/filter_policy.py (``path_allowed``).
Run: python -m lex pytest lex/test_project/tests/queries/test_14l_value_lists_and_the_filter_allow_list.py -v
"""

from __future__ import annotations

import os
from collections import Counter
from unittest import mock

import pytest
from rest_framework import status

from lex.api.views.model_entries.FilterValues import uses_default_read_permission
from lex.test_project.tests._e2e_test_case import E2ETestCase

from .models import (
    ALL_MODELS,
    OPEN_ITEM,
    POLICY_ITEM,
    POLICY_MODELS,
    QUERY_STATUS_ACTIVE,
    QUERY_STATUS_ARCHIVED,
    PolicyItem,
    QueryOpenItem,
)

pytestmark = pytest.mark.queries


def _env(**extra):
    """The process environment without any security switch, plus ``extra``."""
    clean = {k: v for k, v in os.environ.items() if not k.startswith(("LEX_SEC_", "LEX_LEGACY_SECURITY"))}
    clean.update(extra)
    return mock.patch.dict(os.environ, clean, clear=True)


def _ag_request(filter_model: dict) -> dict:
    return {
        "request": {
            "startRow": 0,
            "endRow": 100,
            "rowGroupCols": [],
            "valueCols": [],
            "pivotCols": [],
            "pivotMode": False,
            "groupKeys": [],
            "sortModel": [],
            "filterModel": filter_model,
        }
    }


class TestCluster14l_ValueListsAndTheFilterAllowList(E2ETestCase):
    """Cluster 14l: filter_values and the GET list follow LEX_SEC_FILTERS like the list POST."""

    e2e_models = [*ALL_MODELS, *POLICY_MODELS]

    def setUp(self):
        super().setUp()
        defaults = _env()
        defaults.start()
        self.addCleanup(defaults.stop)
        self.alice = PolicyItem.objects.create(name="alice", salary=150000)
        self.bob = PolicyItem.objects.create(name="bob", salary=50000)
        self.carol = PolicyItem.objects.create(name="carol", salary=150000)

    # -- helpers -------------------------------------------------------

    def _list_post_names(self, model: str, filter_model: dict) -> list:
        resp = self.client.post(self.url_list(model), _ag_request(filter_model), format="json")
        self.assertEqual(resp.status_code, status.HTTP_200_OK, resp.content[:300])
        return sorted(row["name"] for row in resp.json()["rowData"])

    def _value_counts(self, model: str, field: str, filter_model: dict) -> dict:
        resp = self.client.post(
            self.url_filter_values(model), {"field": field, "filterModel": filter_model}, format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK, resp.content[:300])
        return {item["key"]: item["count"] for item in resp.json()["values"]}

    def _get_names(self, model: str, params: dict) -> list:
        resp = self.client.get(self.url_list(model), params)
        self.assertEqual(resp.status_code, status.HTTP_200_OK, resp.content[:300])
        body = resp.json()
        rows = body["results"] if isinstance(body, dict) else body
        return sorted(row["name"] for row in rows)

    # -- 14.84 ---------------------------------------------------------
    def test_14_84_a_model_without_permission_methods_lists_every_row(self):
        """
        Scenario 14.84: nothing to check means every row and field is shown
        Given: QueryOpenItem, a plain Django model with no permission_read and no can_read
        When: its list is fetched by GET and by the AG Grid POST, one row by its detail
              endpoint, and its status values are asked for
        Then: every row comes back with its fields, 200 each time, and the value list counts
              the same rows in the database
        """
        QueryOpenItem.objects.create(name="open-a", status=QUERY_STATUS_ACTIVE)
        QueryOpenItem.objects.create(name="open-b", status=QUERY_STATUS_ACTIVE)
        last = QueryOpenItem.objects.create(name="open-c", status=QUERY_STATUS_ARCHIVED)
        expected = ["open-a", "open-b", "open-c"]

        self.assertEqual(self._get_names(OPEN_ITEM, {}), expected, "the GET list shows every row")
        resp = self.client.post(self.url_list(OPEN_ITEM), _ag_request({}), format="json")
        self.assertEqual(resp.status_code, status.HTTP_200_OK, resp.content[:300])
        rows = resp.json()["rowData"]
        self.assertEqual(sorted(row["name"] for row in rows), expected, "the AG Grid POST shows every row")
        self.assertTrue(all("status" in row for row in rows), "every field of every row is shown")

        detail = self.client.get(self.url_detail(OPEN_ITEM, last.pk))
        self.assertEqual(detail.status_code, status.HTTP_200_OK, detail.content[:300])
        self.assertEqual(detail.json().get("name"), "open-c", "the detail shows the record")

        self.assertTrue(uses_default_read_permission(QueryOpenItem), "premise: the database path")
        grid = dict(Counter(row["status"] for row in rows))
        self.assertEqual(
            self._value_counts(OPEN_ITEM, "status", {}), grid,
            "the database count is exact because the list shows every row",
        )

    # -- 14.85 ---------------------------------------------------------
    def test_14_85_a_filter_on_a_hidden_field_is_ignored_like_on_the_list(self):
        """
        Scenario 14.85: filter_values' other filters obey the same allow-list as the list POST
        Given: PolicyItem rows whose salary no serializer returns, and a filterModel that keeps
               only salary > 100000
        When: the list POST and filter_values (for name) are asked with that filterModel, by
              default and with LEX_SEC_FILTERS=false
        Then: by default both ignore the salary entry -- every row is listed and every name
              counted; with LEX_SEC_FILTERS=false both apply it (alice and carol). Either way the
              value list counts exactly the rows the list shows
        """
        filter_model = {"salary": {"filterType": "number", "type": "greaterThan", "filter": 100000}}

        listed = self._list_post_names(POLICY_ITEM, filter_model)
        counts = self._value_counts(POLICY_ITEM, "name", filter_model)
        self.assertEqual(listed, ["alice", "bob", "carol"], "premise: the list ignores a filter on salary")
        self.assertEqual(counts, dict(Counter(listed)), "the value list ignores the salary entry as the list does")

        with _env(LEX_SEC_FILTERS="false"):
            legacy_listed = self._list_post_names(POLICY_ITEM, filter_model)
            legacy_counts = self._value_counts(POLICY_ITEM, "name", filter_model)
        self.assertEqual(legacy_listed, ["alice", "carol"], "premise: LEX_SEC_FILTERS=false applies the salary filter")
        self.assertEqual(
            legacy_counts, dict(Counter(legacy_listed)), "with LEX_SEC_FILTERS=false the value list applies it too",
        )

    # -- 14.86 ---------------------------------------------------------
    def test_14_86_get_blank_exact_and_in_obey_the_allow_list(self):
        """
        Scenario 14.86: the GET list's value-list lookups are filters like any other
        Given: PolicyItem rows; salary is returned by no serializer, name is
        When: the GET list is asked salary__blank, salary__exact and salary__in (and a bare
              salary for comparison), then the same lookups on name, then the salary lookups
              with LEX_SEC_FILTERS=false
        Then: every salary lookup answers 400 FILTER_NOT_ALLOWED naming the field, as the bare
              salary does; the name lookups filter; with LEX_SEC_FILTERS=false the salary
              lookups filter again
        """
        refused = (
            {"salary": "150000"},
            {"salary__blank": "false"},
            {"salary__exact": "150000"},
            {"salary__in": "150000,50000"},
        )
        for params in refused:
            with self.subTest(params=params):
                resp = self.client.get(self.url_list(POLICY_ITEM), params)
                self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST, resp.content[:300])
                body = resp.json()
                self.assertEqual(body.get("code"), "FILTER_NOT_ALLOWED", body)
                self.assertIn("salary", body.get("error", ""), "the refusal names the field")

        self.assertEqual(self._get_names(POLICY_ITEM, {"name__blank": "false"}), ["alice", "bob", "carol"])
        self.assertEqual(self._get_names(POLICY_ITEM, {"name__exact": "bob"}), ["bob"])
        self.assertEqual(self._get_names(POLICY_ITEM, {"name__in": "alice,carol"}), ["alice", "carol"])

        with _env(LEX_SEC_FILTERS="false"):
            self.assertEqual(self._get_names(POLICY_ITEM, {"salary__exact": "50000"}), ["bob"])
            self.assertEqual(
                self._get_names(POLICY_ITEM, {"salary__in": "150000"}), ["alice", "carol"],
            )
            self.assertEqual(
                self._get_names(POLICY_ITEM, {"salary__blank": "false"}), ["alice", "bob", "carol"],
            )
