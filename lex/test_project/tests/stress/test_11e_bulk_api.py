"""
Cluster 11e: Bulk API endpoints at volume.

Scenarios:

* 11.10 — bulk POST to ``model-many-entries`` at MEDIUM volume.
  Depends on BUG-006 being fixed (the endpoint currently rejects POST
  with 405). Marked ``@unittest.expectedFailure`` until then.
* 11.11 — bulk PATCH over a filtered subset. Must produce exactly
  one UPDATE query, not ``n``.
"""

from __future__ import annotations

import json
import unittest

from django.db import connection
from django.db.models import F
from django.test.utils import CaptureQueriesContext

from ._stress_test_case import StressTestCase
from .models import ALL_MODELS, INVOICE, StressInvoice


class TestCluster11e_BulkAPI(StressTestCase):
    """11.10 / 11.11 — bulk API write paths."""

    e2e_models = ALL_MODELS

    # -- 11.10 ---------------------------------------------------------
    # @unittest.expectedFailure  # BUG-006: many endpoint rejects POST with 405
    def test_11_10_bulk_api_post_at_volume(self):
        """
        Scenario 11.10: POST to ``/many/`` inserts n rows in one call.

        Intent: the documented bulk endpoint should accept a list of
        records, validate them once, and insert them in a single
        transaction. Until BUG-006 is fixed, the endpoint returns 405
        and this scenario is xfail.
        """
        n_by_tier = {"SMALL": 100, "MEDIUM": 5_000, "LARGE": 5_000}
        n = n_by_tier[self.volume]
        budgets = {"SMALL": 2.0, "MEDIUM": 10.0, "LARGE": 10.0}

        payload = [
            {
                "invoice_number": f"INV-API-{i:06d}",
                "counterparty": self.counterparty_ids[
                    i % len(self.counterparty_ids)
                ],
                "period": self.period_ids[i % len(self.period_ids)],
                "booked_on": "2025-06-01",
                "due_on": "2025-07-01",
                "amount_net": "10.00",
                "amount_tax": "2.00",
                "amount_gross": "12.00",
            }
            for i in range(n)
        ]

        with self.assert_runtime_under(
            budgets[self.volume], "11.10_bulk_post",
        ), self.measure("11.10_bulk_post"):
            resp = self.client.post(
                self.url_many(INVOICE),
                data=json.dumps(payload),
                content_type="application/json",
            )
        self.assertIn(
            resp.status_code, (200, 201),
            f"Bulk POST must succeed; got {resp.status_code}: "
            f"{resp.content[:200]!r}",
        )
        # New row count = seeded + n.
        self.assertEqual(
            StressInvoice.objects.count(),
            len(self.invoice_ids) + n,
        )

    # -- 11.11 ---------------------------------------------------------
    def test_11_11_bulk_patch_single_update(self):
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
                    budgets[self.volume], "11.11_bulk_patch",
                ), self.measure("11.11_bulk_patch"):
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

