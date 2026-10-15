"""A column's values with counts, under every filter except the column's own.

Intent: a value-list filter must offer what the grid would show — the same rows
(``as_of`` and read permissions included) under the other active filters — with a
count per value and a readable label. A column's own filter is left out, so a
ticked value can be unticked and more added; a ticked value the other filters
removed stays listed at 0. A regression offers values that lead to empty grids,
hides values the user needs, or shows ids where names belong.
Cluster 14g — scenarios 14.42–14.51. Type: E.
Covers: lex/api/views/model_entries/FilterValues.py, lex/api/utils/filterable_fields.py,
lex/process_admin/sites/process_admin_site.py (route).
Run: python -m lex pytest lex/test_project/tests/queries/test_14g_filter_values_endpoint.py -v
"""

from __future__ import annotations

import time
import urllib.parse
from datetime import date
from unittest.mock import patch

import pytest
from django.utils import timezone
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


class TestCluster14g_FilterValuesEndpoint(E2ETestCase):
    """Cluster 14g: POST api/model_entries/<model>/filter_values."""

    e2e_models = ALL_MODELS

    def setUp(self):
        super().setUp()
        self.alpha = QueryCategory.objects.create(name="alpha")
        self.beta = QueryCategory.objects.create(name="beta")
        rows = [
            ("alpha-small", QUERY_STATUS_ACTIVE, True, 1, date(2026, 1, 15), self.alpha),
            ("alpha-mid", QUERY_STATUS_ARCHIVED, False, 5, date(2026, 3, 10), self.alpha),
            ("beta-mid", QUERY_STATUS_DRAFT, True, 0, date(2026, 2, 20), self.beta),
            ("beta-big", QUERY_STATUS_ACTIVE, True, 9, date(2026, 2, 20), self.beta),
            ("loose", QUERY_STATUS_ACTIVE, True, 7, None, None),
        ]
        for name, state, active, count, created_on, category in rows:
            QueryItem.objects.create(
                name=name, status=state, is_active=active, count=count,
                created_on=created_on, category=category,
            )

    def _post(self, body: dict, query: str = ""):
        return self.client.post(self.url_filter_values(ITEM) + query, data=body, format="json")

    def _values(self, field: str, filter_model=None, query: str = "", **extra) -> dict:
        resp = self._post({"field": field, "filterModel": filter_model or {}, **extra}, query)
        self.assertEqual(resp.status_code, status.HTTP_200_OK, resp.data)
        return resp.data

    def _counts(self, field: str, filter_model=None, query: str = "", **extra) -> dict:
        data = self._values(field, filter_model, query, **extra)
        return {item["key"]: item["count"] for item in data["values"]}

    # -- 14.42 ---------------------------------------------------------
    def test_14_42_counts_follow_the_other_filters_not_the_columns_own(self):
        """
        Scenario 14.42: narrowing
        Given: five rows across three statuses and two categories
        When: status values are asked for under a status filter, then under a category filter
        Then: the status filter is ignored for its own list; the category filter narrows it
        """
        own = {"status": {"filterType": "set", "values": [QUERY_STATUS_ARCHIVED]}}
        self.assertEqual(
            self._counts("status", own),
            {QUERY_STATUS_ACTIVE: 3, QUERY_STATUS_ARCHIVED: 1, QUERY_STATUS_DRAFT: 1},
            "a column must not narrow its own list",
        )
        other = {"category": {"filterType": "set", "values": [str(self.beta.pk)]}}
        self.assertEqual(
            self._counts("status", other),
            {QUERY_STATUS_ACTIVE: 1, QUERY_STATUS_DRAFT: 1},
            "another column's filter must narrow the list",
        )

    # -- 14.43 ---------------------------------------------------------
    def test_14_43_keys_and_labels_per_type(self):
        """
        Scenario 14.43: what a value list shows
        Given: choice, foreign-key, boolean, date and integer columns, one blank row
        When: each column's values are asked for
        Then: keys are the wire form and labels are readable
        """
        status_items = {i["key"]: i["label"] for i in self._values("status")["values"]}
        self.assertEqual(status_items[QUERY_STATUS_ACTIVE], "Active", "choices use their labels")

        categories = {i["key"]: (i["label"], i["count"]) for i in self._values("category")["values"]}
        self.assertEqual(categories[str(self.alpha.pk)], ("QCat<alpha>", 2), "a foreign key shows str(related)")
        self.assertEqual(categories[None], ("(Blank)", 1), "blank is listed with its own label")

        flags = {i["key"]: i["label"] for i in self._values("is_active")["values"]}
        self.assertEqual(flags, {"true": "Yes", "false": "No"}, "booleans are true/false keys, Yes/No labels")

        self.assertEqual(self._counts("created_on")["2026-02-20"], 2, "a date key is ISO")
        self.assertEqual(self._counts("count")["5"], 1, "an integer key is its string form")

    # -- 14.44 ---------------------------------------------------------
    def test_14_44_a_ticked_value_the_other_filters_removed_stays_at_zero(self):
        """
        Scenario 14.44: a ticked value can still be unticked
        Given: a category filter that leaves no archived row
        When: status values are asked for with "archived" ticked
        Then: archived is listed with count 0
        """
        other = {"category": {"filterType": "set", "values": [str(self.beta.pk)]}}
        counts = self._counts("status", other, selected=[QUERY_STATUS_ARCHIVED])
        self.assertEqual(counts.get(QUERY_STATUS_ARCHIVED), 0, "a ticked value must stay listed")

    # -- 14.45 ---------------------------------------------------------
    def test_14_45_search_matches_labels(self):
        """
        Scenario 14.45: searching a long list
        Given: text and foreign-key columns
        When: values are searched
        Then: text matches its values; a foreign key matches its labels
        """
        self.assertEqual(set(self._counts("name", search="MID")), {"alpha-mid", "beta-mid"}, "case-insensitive")
        self.assertEqual(set(self._counts("category", search="bet")), {str(self.beta.pk)}, "matched on str(related)")

    # -- 14.46 ---------------------------------------------------------
    def test_14_46_limit_cuts_the_list_and_says_so(self):
        """
        Scenario 14.46: a long list is cut, and a ticked value survives the cut
        Given: five distinct names
        When: two are asked for, then two with "loose" ticked
        Then: two come back with truncated=true; the ticked one is added after the cut
        """
        data = self._values("name", limit=2)
        self.assertEqual(len(data["values"]), 2, "the limit applies")
        self.assertTrue(data["truncated"], "a cut list must say so")
        ticked = {i["key"] for i in self._values("name", limit=2, selected=["loose"])["values"]}
        self.assertIn("loose", ticked, "a ticked value is never cut away")

    # -- 14.47 ---------------------------------------------------------
    def test_14_47_values_are_sorted_by_label_with_blank_last(self):
        """
        Scenario 14.47: a predictable order
        Given: two categories and one blank
        When: category values are asked for
        Then: labels ascend, case-insensitively, and blank comes last
        """
        labels = [i["label"] for i in self._values("category")["values"]]
        self.assertEqual(labels, ["QCat<alpha>", "QCat<beta>", "(Blank)"])

    # -- 14.48 ---------------------------------------------------------
    def test_14_48_only_the_lists_own_columns_can_be_listed(self):
        """
        Scenario 14.48: what is not filterable
        Given: the item model
        When: an unknown field, a JSON field, a related path or a serializer-only field is asked for
        Then: each is a 400 "is not filterable"
        """
        for field in ("nope", "metadata", "category__name", "lex_reserved_scopes", ""):
            resp = self._post({"field": field, "filterModel": {}})
            self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST, f"{field!r}: {resp.data}")
            self.assertIn("not filterable", str(resp.data), field)

    # -- 14.49 ---------------------------------------------------------
    def test_14_49_as_of_lists_the_values_of_that_moment(self):
        """
        Scenario 14.49: time travel
        Given: a row whose status changed after a recorded instant
        When: status values are asked for as of that instant
        Then: the earlier status is counted, not the current one
        """
        time.sleep(0.05)
        moment = timezone.now()
        time.sleep(0.05)
        row = QueryItem.objects.get(name="beta-mid")
        row.status = QUERY_STATUS_ARCHIVED
        row.save()

        self.assertEqual(
            self._counts("status"),
            {QUERY_STATUS_ACTIVE: 3, QUERY_STATUS_ARCHIVED: 2},
            "now: the draft row is archived",
        )
        query = "?" + urllib.parse.urlencode({"as_of": moment.isoformat()})
        self.assertEqual(
            self._counts("status", query=query),
            {QUERY_STATUS_ACTIVE: 3, QUERY_STATUS_ARCHIVED: 1, QUERY_STATUS_DRAFT: 1},
            "as of the instant: still a draft",
        )

    # -- 14.50 ---------------------------------------------------------
    def test_14_50_a_bad_key_in_another_filter_is_a_400(self):
        """
        Scenario 14.50: the other filters are translated like the list's
        Given: an integer filter with a key that is not an integer
        When: another column's values are asked for under it
        Then: 400 naming the integer field
        """
        resp = self._post({"field": "status", "filterModel": {"count": {"filterType": "set", "values": ["x"]}}})
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST, resp.data)
        self.assertIn("count", str(resp.data))

    # -- 14.51 ---------------------------------------------------------
    def test_14_51_the_database_count_and_the_per_row_count_agree(self):
        """
        Scenario 14.51: two ways to count, one answer
        Given: a model whose read permission allows every field
        When: values are counted per row (overridden permission) and in the database
        Then: the answers are identical
        """
        other = {"is_active": {"filterType": "set", "values": ["true"]}}
        per_row = self._values("category", other)
        with patch(
            "lex.api.views.model_entries.FilterValues.uses_default_read_permission",
            return_value=True,
        ):
            in_database = self._values("category", other)
        self.assertEqual(per_row, in_database)
