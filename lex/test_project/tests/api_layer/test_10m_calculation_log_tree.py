"""CalculationLogTreeView pagination + N+1 avoidance.

Intent: this APIView feeds the frontend's calculation-log tree. It used to load
the *entire* CalculationLog table (or every row for a calculation) into memory
and ran one query per node to resolve children — both contributed to backend
OOMs when a customer opened the log on a large calculation. The endpoint must
now (1) bound how many rows a single request materializes, (2) expose
limit/offset pagination with a has_more flag, and (3) resolve children in a
constant number of queries regardless of page size. A regression reintroduces
the unbounded load.
Cluster 10m — scenarios 10.61–10.66. Type: I.
Covers: lex/api/views/model_entries/CalculationLogTreeView.py and
        lex/api/views/model_entries/serializers/CalculationLogTreeSerializer.py.
Run: python -m lex pytest lex/test_project/tests/api_layer/test_10m_calculation_log_tree.py -v
"""

from __future__ import annotations

from django.test import TestCase
from rest_framework.test import APIRequestFactory

from lex.api.views.model_entries.CalculationLogTreeView import CalculationLogTreeView
from lex.audit_logging.models.CalculationLog import CalculationLog

import pytest

pytestmark = pytest.mark.api_layer


def _get(params: dict):
    """Drive the view through a DRF request and return the Response."""
    request = APIRequestFactory().get("/api/calculationlog/tree/", params)
    return CalculationLogTreeView.as_view()(request)


class TestCluster10m_TreeViewPagination(TestCase):
    """Cluster 10m: the tree endpoint paginates and avoids N+1 child queries."""

    databases = {"default"}

    def _make_rows(self, calc_id: str, n: int):
        return [
            CalculationLog.objects.create(calculationId=calc_id, calculation_log=f"row {i}")
            for i in range(n)
        ]

    # -- 10.61 ---------------------------------------------------------
    def test_10_61_default_limit_bounds_the_page(self) -> None:
        """
        Scenario 10.61: the page is bounded even with no limit param
        Given: more rows than a tiny configured default limit
        When: the tree endpoint is called without pagination params
        Then: at most DEFAULT_LIMIT rows come back and has_more is True
        """
        self._make_rows("calc-61", 5)
        with self.settings():
            # Shrink the default so the test stays fast and explicit.
            CalculationLogTreeView.DEFAULT_LIMIT, original = 2, CalculationLogTreeView.DEFAULT_LIMIT
            try:
                resp = _get({"calculation_id": "calc-61"})
            finally:
                CalculationLogTreeView.DEFAULT_LIMIT = original
        self.assertEqual(len(resp.data["data"]), 2, "page must respect DEFAULT_LIMIT")
        self.assertEqual(resp.data["pagination"]["total"], 5)
        self.assertTrue(resp.data["pagination"]["has_more"], "has_more must flag remaining rows")

    # -- 10.62 ---------------------------------------------------------
    def test_10_62_offset_walks_the_dataset(self) -> None:
        """
        Scenario 10.62: limit + offset return the next slice
        Given: 5 rows for a calculation
        When: the endpoint is called with limit=2&offset=4
        Then: only the final row returns and has_more is False
        """
        self._make_rows("calc-62", 5)
        resp = _get({"calculation_id": "calc-62", "limit": "2", "offset": "4"})
        self.assertEqual(len(resp.data["data"]), 1, "last slice holds the single trailing row")
        self.assertEqual(resp.data["pagination"]["offset"], 4)
        self.assertFalse(resp.data["pagination"]["has_more"], "no rows remain past the end")

    # -- 10.63 ---------------------------------------------------------
    def test_10_63_children_ids_resolved_for_each_parent(self) -> None:
        """
        Scenario 10.63: a parent reports its child row ids
        Given: one root with two children under the same calculationId
        When: the endpoint serializes the page
        Then: the root's 'children' lists both child ids; a child reports none
        """
        root = CalculationLog.objects.create(calculationId="calc-63", calculation_log="root")
        c1 = CalculationLog.objects.create(calculationId="calc-63", parent_log=root, calculation_log="c1")
        c2 = CalculationLog.objects.create(calculationId="calc-63", parent_log=root, calculation_log="c2")
        resp = _get({"calculation_id": "calc-63"})
        by_id = {row["id"]: row for row in resp.data["data"]}
        self.assertCountEqual(
            by_id[root.id]["children"], [c1.id, c2.id],
            "root must expose both child ids from the prefetched map",
        )
        self.assertEqual(by_id[c1.id]["children"], [], "leaf node has no children")

    # -- 10.64 ---------------------------------------------------------
    def test_10_64_isroot_flag_distinguishes_root_from_child(self) -> None:
        """
        Scenario 10.64: isRoot is True only for parentless rows
        Given: a root and a child
        When: serialized
        Then: the root carries isRoot=True; the child has isRoot stripped out
        """
        root = CalculationLog.objects.create(calculationId="calc-64", calculation_log="root")
        child = CalculationLog.objects.create(calculationId="calc-64", parent_log=root, calculation_log="child")
        resp = _get({"calculation_id": "calc-64"})
        by_id = {row["id"]: row for row in resp.data["data"]}
        self.assertTrue(by_id[root.id]["isRoot"], "root row must report isRoot=True")
        self.assertNotIn("isRoot", by_id[child.id], "non-root rows omit isRoot")

    # -- 10.65 ---------------------------------------------------------
    def test_10_65_child_query_count_is_constant(self) -> None:
        """
        Scenario 10.65: children resolve in a constant number of queries
        Given: many parent rows, each with a child (would be N+1 per node)
        When: the endpoint serializes a full page
        Then: the whole request stays within a small fixed query budget
              (count + page + children), proving the N+1 is gone
        """
        for i in range(25):
            parent = CalculationLog.objects.create(calculationId="calc-65", calculation_log=f"p{i}")
            CalculationLog.objects.create(calculationId="calc-65", parent_log=parent, calculation_log=f"k{i}")
        # count(1) + page(1) + children(1) = 3 queries, independent of row count.
        with self.assertNumQueries(3):
            _get({"calculation_id": "calc-65", "limit": "100"})

    # -- 10.66 ---------------------------------------------------------
    def test_10_66_calculation_id_filter_scopes_rows(self) -> None:
        """
        Scenario 10.66: calculation_id filters the page to that calculation
        Given: rows under two different calculationIds
        When: the endpoint is called for one of them
        Then: only that calculation's rows are returned
        """
        self._make_rows("calc-66-a", 3)
        self._make_rows("calc-66-b", 4)
        resp = _get({"calculation_id": "calc-66-a"})
        self.assertEqual(resp.data["pagination"]["total"], 3, "filter must scope the total")
        self.assertEqual(len(resp.data["data"]), 3)
