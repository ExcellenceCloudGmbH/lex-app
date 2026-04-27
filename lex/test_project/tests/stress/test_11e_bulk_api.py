"""
Cluster 11e: Bulk delete API and ORM batch baseline at volume.

Scenarios:

* 11.10 — bulk DELETE to ``model-many-entries`` over a selected id set.
* 11.11 — ORM ``QuerySet.update()`` over a filtered subset. Must
  produce exactly one UPDATE query, not ``n``.
"""

from __future__ import annotations

from urllib.parse import urlencode

from django.db import connection
from rest_framework import status
from django.test.utils import CaptureQueriesContext

from ._stress_test_case import StressTestCase
from .models import ALL_MODELS, INVOICE, StressInvoice


class TestCluster11e_BulkAPI(StressTestCase):
    """11.10 / 11.11 — supported bulk delete + ORM update baseline."""

    e2e_models = ALL_MODELS

    def _many_url_for_ids(self, ids) -> str:
        query = urlencode([("ids", pk) for pk in ids])
        return f"{self.url_many(INVOICE)}?{query}"

    # -- 11.10 ---------------------------------------------------------
    def test_11_10_bulk_api_delete_at_volume(self):
        """
        Scenario 11.10: DELETE to ``/many/`` removes selected rows at volume.

        Intent: bulk delete is the supported batch-write API. The
        selected-id path must stay bounded and must not delete rows
        outside the explicit ``ids`` query-param set.
        """
        n_by_tier = {"SMALL": 10, "MEDIUM": 100, "LARGE": 250}
        n = n_by_tier[self.volume]
        budgets = {"SMALL": 5.0, "MEDIUM": 20.0, "LARGE": 45.0}

        target_ids = self.invoice_ids[:n]
        survivor_id = self.invoice_ids[-1]

        with self.assert_runtime_under(
            budgets[self.volume], "11.10_bulk_delete",
        ), self.measure("11.10_bulk_delete"):
            resp = self.client.delete(self._many_url_for_ids(target_ids))
        self.assertEqual(
            resp.status_code, status.HTTP_200_OK,
            f"Bulk DELETE must succeed; got {resp.status_code}: "
            f"{resp.content[:200]!r}",
        )
        self.assertEqual(
            set(resp.data), set(target_ids),
            "Bulk DELETE must report exactly the selected ids it deleted.",
        )
        self.assertFalse(StressInvoice.objects.filter(id__in=target_ids).exists())
        self.assertTrue(
            StressInvoice.objects.filter(id=survivor_id).exists(),
            "Rows outside the selected id set must survive bulk DELETE.",
        )

    # -- 11.11 ---------------------------------------------------------
    def test_11_11_orm_bulk_update_single_update(self):
        """
        Scenario 11.11: filtered ORM ``update()`` produces exactly one
        UPDATE, not one-per-row.

        Intent: the ``QuerySet.update()`` contract is a single SQL
        UPDATE with a WHERE clause — that's what customers rely on
        for month-end batch adjustments. If a future hook rewrites
        ``update()`` into a save-loop under the hood, this gate fires
        immediately.

        We mark half the invoices as reversed and assert

        * exactly 1 UPDATE in the CaptureQueriesContext,
        * the affected-row count equals the filter's row count,
        * the post-state in the DB matches.
        """
        budgets = {"SMALL": 2.0, "MEDIUM": 5.0, "LARGE": 10.0}

        target_ids = self.invoice_ids[: len(self.invoice_ids) // 2]
        qs = StressInvoice.objects.filter(id__in=target_ids)
        expected_affected = qs.count()

        with CaptureQueriesContext(connection) as ctx, \
                self.assert_runtime_under(
                    budgets[self.volume], "11.11_orm_bulk_update",
                ), self.measure("11.11_orm_bulk_update"):
            affected = qs.update(is_reversed=True)

        # Filter to UPDATE statements only — there will usually be one
        # BEGIN and possibly a COMMIT in the capture too.
        updates = [
            q for q in ctx.captured_queries
            if q["sql"].lstrip().upper().startswith("UPDATE")
        ]
        self.assertEqual(
            len(updates), 1,
            f"Bulk update must be a single UPDATE statement; got "
            f"{len(updates)}. Queries:\n"
            + "\n".join(u["sql"][:200] for u in updates),
        )
        self.assertEqual(
            affected, expected_affected,
            f"update() must report the filter's row count as affected; "
            f"got {affected}, expected {expected_affected}.",
        )
        self.assertEqual(
            StressInvoice.objects.filter(is_reversed=True).count(),
            expected_affected,
        )

