"""Which fields a list request may filter and order by (LEX_SEC_FILTERS).

Intent
------
A filter on a field reveals the field's value even if no response ever
contains it: ``?salary__gte=100000`` answers "who earns that much" one request
at a time. Since 3.0 a list request may only filter and order by fields a
client can already see — the fields of the model's registered serializers,
the ``limit_choices_to`` keys of foreign keys pointing at it, and
``lex_filterable_fields`` — and relations only into models the API exposes.
Anything else answers 400 ``FILTER_NOT_ALLOWED`` on the GET list; the AG Grid
POST treats such a column like one that does not exist. A model opts out with
``lex_filterable_fields = "__all__"``; framework models (audit log,
calculation log, history tables) are not restricted; ``LEX_SEC_FILTERS=false``
restores the old behaviour everywhere.

Cluster 14f — scenarios 14.36–14.42. Type: E.
Covers: lex/api/filter_policy.py, lex/api/views/model_entries/List.py
(apply_query_param_filters, apply_ordering, _is_valid_field_path).
Run: python -m lex pytest lex/test_project/tests/queries/test_14f_filter_allow_list.py -v
"""

from __future__ import annotations

import os
from unittest import mock

import pytest

from lex.test_project.tests._e2e_test_case import E2ETestCase

from .models import POLICY_ITEM, POLICY_MODELS, POLICY_OPEN, POLICY_OWNER, PolicyItem, PolicyOpenItem, PolicyOwner

pytestmark = pytest.mark.queries


def _env(**extra):
    clean = {k: v for k, v in os.environ.items() if not k.startswith(("LEX_SEC_", "LEX_LEGACY_SECURITY"))}
    clean.update(extra)
    return mock.patch.dict(os.environ, clean, clear=True)


class TestCluster14f_FilterAllowList(E2ETestCase):
    """Cluster 14f: filter and ordering allow-list."""

    e2e_models = POLICY_MODELS

    def setUp(self) -> None:
        super().setUp()
        defaults = _env()
        defaults.start()
        self.addCleanup(defaults.stop)
        self.alice = PolicyItem.objects.create(name="alice", salary=150000)
        self.bob = PolicyItem.objects.create(name="bob", salary=50000)

    def _rows(self, response):
        self.assertEqual(response.status_code, 200, response.content[:300])
        body = response.json()
        return body["results"] if isinstance(body, dict) else body

    def _names(self, response):
        return sorted(row["name"] for row in self._rows(response))

    # -- 14.36 ---------------------------------------------------------
    def test_14_36_serialized_fields_filter_hidden_fields_refused(self) -> None:
        """
        Scenario 14.36: only fields a response contains can filter.
        Given: PolicyItem, whose serializer returns name/owner but not salary
        When: the list is filtered by name, then by salary
        Then: the name filter works; the salary filter answers 400
              FILTER_NOT_ALLOWED naming the field.
        """
        self.assertEqual(self._names(self.client.get(self.url_list(POLICY_ITEM), {"name": "alice"})), ["alice"])
        response = self.client.get(self.url_list(POLICY_ITEM), {"salary__gte": 100000})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["code"], "FILTER_NOT_ALLOWED")
        self.assertIn("salary", response.json()["error"])

    # -- 14.37 ---------------------------------------------------------
    def test_14_37_ordering_by_hidden_field_refused(self) -> None:
        """
        Scenario 14.37: ordering by a hidden field reveals its order, so it is refused.
        Given: the same rows
        When: the list is ordered by -name, then by salary
        Then: -name answers 200 in that order; salary answers 400.
        """
        response = self.client.get(self.url_list(POLICY_ITEM), {"ordering": "-name"})
        self.assertEqual([row["name"] for row in self._rows(response)], ["bob", "alice"])
        self.assertEqual(self.client.get(self.url_list(POLICY_ITEM), {"ordering": "salary"}).status_code, 400)

    # -- 14.38 ---------------------------------------------------------
    def test_14_38_relations_follow_the_related_models_rules(self) -> None:
        """
        Scenario 14.38: a filter through a relation obeys the related model's allow-list.
        Given: an owner with a name (serialized) and an internal_code (not serialized)
        When: items are filtered by owner__name, then by owner__internal_code
        Then: owner__name works; owner__internal_code answers 400.
        """
        owner = PolicyOwner.objects.create(name="acme", internal_code="X-9")
        self.alice.owner = owner
        self.alice.save()
        self.assertEqual(self._names(self.client.get(self.url_list(POLICY_ITEM), {"owner__name": "acme"})), ["alice"])
        self.assertEqual(
            self.client.get(self.url_list(POLICY_ITEM), {"owner__internal_code": "X-9"}).status_code, 400
        )

    # -- 14.39 ---------------------------------------------------------
    def test_14_39_limit_choices_to_keys_stay_filterable(self) -> None:
        """
        Scenario 14.39: the frontend's FK pickers keep working.
        Given: PolicyItem.owner with limit_choices_to={"is_active": True}, and
               PolicyOwner's serializer not returning is_active
        When: owners are filtered by is_active (what the picker sends)
        Then: HTTP 200 with the active owners only.
        """
        PolicyOwner.objects.create(name="active", is_active=True)
        PolicyOwner.objects.create(name="gone", is_active=False)
        response = self.client.get(self.url_list(POLICY_OWNER), {"is_active": "true", "perPage": -1})
        self.assertEqual(self._names(response), ["active"])

    # -- 14.40 ---------------------------------------------------------
    def test_14_40_model_opt_out_and_global_switch(self) -> None:
        """
        Scenario 14.40: "__all__" and LEX_SEC_FILTERS=false restore the old behaviour.
        Given: PolicyOpenItem (lex_filterable_fields = "__all__") and PolicyItem
        When: each is filtered by its unserialized field, the latter with LEX_SEC_FILTERS=false
        Then: both filters apply.
        """
        PolicyOpenItem.objects.create(name="high", hidden_score=9)
        PolicyOpenItem.objects.create(name="low", hidden_score=1)
        self.assertEqual(self._names(self.client.get(self.url_list(POLICY_OPEN), {"hidden_score__gte": 5})), ["high"])
        with _env(LEX_SEC_FILTERS="false"):
            response = self.client.get(self.url_list(POLICY_ITEM), {"salary__gte": 100000})
        self.assertEqual(self._names(response), ["alice"])

    # -- 14.41 ---------------------------------------------------------
    def test_14_41_ag_grid_ignores_hidden_columns(self) -> None:
        """
        Scenario 14.41: the AG Grid POST treats a hidden column as nonexistent.
        Given: a filterModel on salary and a sortModel on salary
        When: the AG Grid list request is posted
        Then: HTTP 200 with every row — the hidden column neither filters nor sorts.
        """
        payload = {
            "request": {
                "startRow": 0,
                "endRow": 100,
                "rowGroupCols": [],
                "valueCols": [],
                "pivotCols": [],
                "pivotMode": False,
                "groupKeys": [],
                "filterModel": {"salary": {"filterType": "number", "type": "greaterThan", "filter": 100000}},
                "sortModel": [{"colId": "salary", "sort": "desc"}],
            }
        }
        response = self.client.post(self.url_list(POLICY_ITEM), payload, format="json")
        self.assertEqual(response.status_code, 200, response.content[:300])
        self.assertEqual(response.json()["rowCount"], 2)

    # -- 14.42 ---------------------------------------------------------
    def test_14_42_primary_key_always_filterable(self) -> None:
        """
        Scenario 14.42: the primary key stays filterable (selection, deep links).
        Given: two rows
        When: the list is filtered by id
        Then: HTTP 200 with that row.
        """
        response = self.client.get(self.url_list(POLICY_ITEM), {"id": self.bob.pk})
        self.assertEqual(self._names(response), ["bob"])
