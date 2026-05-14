"""
Cluster 11b: List / filter / sort throughput.

Scenarios:

* 11.3 — paginated list endpoint returns pages in budget, with a hard
  cap on per-page query count (N+1 on the FK join is the regression
  this catches).
* 11.4 — list endpoint with filter + sort; asserts the planner picks
  an index (``EXPLAIN`` must not show ``Seq Scan``). PostgreSQL-only;
  skipped gracefully on SQLite.
"""

from __future__ import annotations

from django.db import connection

from ._stress_test_case import StressTestCase
from .models import ALL_MODELS, INVOICE, StressInvoice


class TestCluster11b_ListEndpoint(StressTestCase):
    """11.3 / 11.4 — paginated reads + filtered sorts at volume."""

    e2e_models = ALL_MODELS

    # -- 11.3 ----------------------------------------------------------
    # @unittest.expectedFailure  # BUG-011: list endpoint does O(n) queries per page
    def test_11_3_paginated_list_p95(self):
        """
        Scenario 11.3: list endpoint paginates invoices with a bounded
        query count per page.

        Intent: the list view does

            1 × SELECT (page) + 1 × COUNT + 1 × prefetch(counterparty)
              + 1 × prefetch(period)  =  4 queries

        regardless of page size. If a future change adds a per-row
        lookup for counterparty or period (classic N+1 on FK), this
        budget fails loudly.

        **Currently xfail — BUG-011**: the list endpoint runs ~``n + k``
        queries per page at SMALL volume (observed: 504 queries for a
        500-row page). Likely cause is per-row permission evaluation
        or a missing ``select_related`` in the list serializer. Do NOT
        weaken this test to paper over it — fixing the framework to
        match the documented intent is the correct resolution.
        """
        budgets = {"SMALL": 2.0, "MEDIUM": 5.0, "LARGE": 10.0}
        pages = 3
        page_size = 100

        for page in range(1, pages + 1):
            with self.assert_query_count_at_most(8, f"11.3_page_{page}_queries"):
                resp = self.list_get(
                    INVOICE,
                    query_params={
                        "page": page,
                        "page_size": page_size,
                    },
                )
            self.assertEqual(
                resp.status_code, 200,
                f"List page {page} must return 200; got "
                f"{resp.status_code}",
            )

        # One end-to-end timing pass on page 1 for trend reporting.
        with self.assert_runtime_under(
            budgets[self.volume], "11.3_page1_runtime",
        ), self.measure("11.3_page1_runtime"):
            self.list_get(INVOICE, query_params={"page": 1, "page_size": page_size})

    # -- 11.4 ----------------------------------------------------------
    def test_11_4_filter_sort_uses_index(self):
        """
        Scenario 11.4: filter + sort hits an indexed path.

        Intent: ``StressInvoice`` has composite indexes on
        ``(booked_on, category)`` and ``(period, is_posted)``. A
        filter on either must produce an index-backed query plan —
        never a ``Seq Scan`` on the 20k-row table.

        Implementation: run ``EXPLAIN`` through the raw cursor on the
        exact queryset we care about, scrape the plan text, and
        assert ``Index Scan`` or ``Bitmap Index Scan`` appears and
        ``Seq Scan`` does NOT. Postgres-only; skipped on SQLite
        because the planner output is structurally different and the
        cluster 11 release gate runs on Postgres anyway.
        """
        if connection.vendor != "postgresql":
            self.skipTest(
                "11.4 relies on a PostgreSQL EXPLAIN plan to assert "
                "index usage. SQLite's EXPLAIN output does not map "
                "cleanly to `Index Scan` markers. This scenario runs "
                "in the release-gate CI (Postgres) only."
            )

        qs = StressInvoice.objects.filter(
            booked_on__gte="2025-06-01",
            category="standard",
        ).order_by("-booked_on")

        sql, params = qs.query.sql_with_params()
        with connection.cursor() as cur:
            cur.execute(f"EXPLAIN {sql}", params)
            plan = "\n".join(row[0] for row in cur.fetchall())

        self.assertNotIn(
            "Seq Scan", plan,
            f"Filter on indexed column (booked_on, category) must NOT "
            f"fall through to a Seq Scan on {qs.model._meta.db_table}. "
            f"Plan was:\n{plan}",
        )
        self.assertTrue(
            "Index Scan" in plan or "Bitmap Index Scan" in plan,
            f"Expected the planner to pick an index for the composite "
            f"(booked_on, category) filter. Plan was:\n{plan}",
        )

