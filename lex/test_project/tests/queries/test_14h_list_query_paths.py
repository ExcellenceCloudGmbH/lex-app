"""
Cluster 14h: List endpoint — pagination, ordering, filter+search, pk_only.

Targets ``lex/api/views/model_entries/List.py`` — the GET-list path
the AG Grid UI hits on every page load. Cluster 14a covered single-key
filters; 14h pins **multi-feature combinations** the customer hits
in real use:

* Pagination shape (the ``{count, results, next, previous}`` envelope)
* Ordering combined with a filter
* Filter combined with the AG Grid ``perPage=-1`` "give me everything"
* ``pk_only=true`` combined with a filter (the bulk-selection path)

A regression in any of these is silently visible: the grid loads
fewer rows than there are, or 'select all + bulk delete' deletes the
wrong rows.

Scenarios numbered per the Coverage Roadmap **Tier 2.2** in
``docs/test-plan/test-clusters.md``.
"""

from __future__ import annotations

import unittest
from datetime import date
from decimal import Decimal

from lex.tests.e2e._e2e_test_case import E2ETestCase
from rest_framework import status

from .models import (
    ALL_MODELS,
    ITEM,
    QUERY_STATUS_ACTIVE,
    QUERY_STATUS_ARCHIVED,
    QueryCategory,
    QueryItem,
)


class TestCluster14h_ListQueryPaths(E2ETestCase):
    """List endpoint multi-feature combinations."""

    e2e_models = ALL_MODELS

    def _seed(self):
        self.cat = QueryCategory.objects.create(name="14h-cat")
        self.rows = [
            QueryItem.objects.create(
                name=f"row-{i:02d}", amount=Decimal(str(i * 10)), count=i,
                status=QUERY_STATUS_ACTIVE if i % 2 == 0 else QUERY_STATUS_ARCHIVED,
                created_on=date(2026, 1, 1),
                category=self.cat,
            )
            for i in range(10)
        ]

    # -- 14.30 ---------------------------------------------------------
    def test_14_30_pagination_envelope_shape(self) -> None:
        """
        Scenario 14.30: ``?perPage=4`` returns the AG Grid pagination
        envelope — a dict with ``count`` and ``results`` (+ ``next`` /
        ``previous`` links). The frontend's row-count display depends
        on ``count`` matching the un-paginated total, not the page size.
        """
        self._seed()

        resp = self.list_get(ITEM, {"perPage": "4"})
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertIsInstance(
            resp.data, dict,
            "perPage=N must return a paginated dict envelope, not a raw list",
        )
        self.assertEqual(
            resp.data.get("count"), 10,
            "Pagination 'count' must reflect the *full* matched row count, "
            "not the size of this page",
        )
        results = resp.data.get("results", [])
        self.assertEqual(
            len(results), 4,
            f"perPage=4 must return at most 4 rows on the first page; got {len(results)}",
        )

    # -- 14.31 ---------------------------------------------------------
    def test_14_31_ordering_descending_by_field(self) -> None:
        """
        Scenario 14.31: ``?ordering=-amount`` returns rows sorted
        descending by the named field. A bug in
        :func:`apply_ordering` here makes every clicked header in the
        grid silently no-op.
        """
        self._seed()

        resp = self.list_get(ITEM, {"ordering": "-amount"})
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        rows = self.extract_results(resp.data)
        amounts = [Decimal(str(row["amount"])) for row in rows]
        self.assertEqual(
            amounts, sorted(amounts, reverse=True),
            f"`-amount` must produce descending order; got {amounts!r}",
        )

    # -- 14.32 ---------------------------------------------------------
    def test_14_32_filter_combined_with_ordering(self) -> None:
        """
        Scenario 14.32: filter AND order applied together.

        ``?status=active&ordering=-count`` — the grid sends both at the
        same time when a user has a column filter active and clicks a
        header to sort. Both must apply; order matters for the result.
        """
        self._seed()

        resp = self.list_get(
            ITEM, {"status": QUERY_STATUS_ACTIVE, "ordering": "-count"},
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        rows = self.extract_results(resp.data)

        # Filter half: every row's status is active.
        statuses = {row["status"] for row in rows}
        self.assertEqual(
            statuses, {QUERY_STATUS_ACTIVE},
            f"Filter must restrict to active rows; got statuses {statuses!r}",
        )
        # Sort half: descending by count.
        counts = [row["count"] for row in rows]
        self.assertEqual(
            counts, sorted(counts, reverse=True),
            f"Filtered subset must still be sorted by `-count`; got {counts!r}",
        )

    # -- 14.33 ---------------------------------------------------------
    def test_14_33_pk_only_with_filter(self) -> None:
        """
        Scenario 14.33: ``?pk_only=true&status=active`` returns the id
        list of the *filtered* subset, with a matching ``count``. This
        is the bulk-selection fast path — if the filter is silently
        ignored, 'select all + bulk delete' would wipe rows the user
        cannot even see in their filtered grid.
        """
        self._seed()
        expected_ids = list(
            QueryItem.objects
            .filter(status=QUERY_STATUS_ACTIVE)
            .values_list("pk", flat=True)
        )

        resp = self.list_get(
            ITEM, {"pk_only": "true", "status": QUERY_STATUS_ACTIVE},
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertIn(
            "ids", resp.data,
            "pk_only response must carry an 'ids' key",
        )
        self.assertEqual(
            sorted(resp.data["ids"]), sorted(expected_ids),
            "pk_only must honour the filter — only filtered ids may "
            "be returned",
        )
        self.assertEqual(
            resp.data["count"], len(expected_ids),
            "pk_only 'count' must reflect the filtered subset size",
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()

