"""
Cluster 11f: History-table growth, permission filtering at scale,
audit-log throughput.

Scenarios:

* 11.12 — concurrent writes from 10 clients. SKIPPED — needs a
  dedicated threading / connection-pool fixture. Documented
  separately for a future pass.
* 11.13 — one row, many revisions. 20k ``.save()`` calls on a single
  invoice → 20k history rows. History paginates quickly; no runaway
  on ``.history.all()``.
* 11.14 — permission filtering at scale. ``permission_read`` is
  patched with a counter; the list endpoint must invoke it exactly
  ONCE per request, not once per row.
* 11.15 — audit-log write throughput. SKIPPED — depends on the
  middleware-level audit hook (same gap as scenario 6.4).
"""

from __future__ import annotations

import unittest
from unittest.mock import patch

from lex.core.models.LexModel import PermissionResult

from ._stress_test_case import StressTestCase
from .models import ALL_MODELS, INVOICE, StressInvoice


class TestCluster11f_Concurrency(StressTestCase):
    """11.12 — concurrent writes."""

    e2e_models = ALL_MODELS

    @unittest.skip(
        "Scenario 11.12: '10 parallel clients each PATCH their own "
        "subset' needs a threaded fixture with its own connection per "
        "thread and a barrier to ensure real overlap. Django's "
        "TransactionTestCase is not thread-safe by default — a naive "
        "implementation would race on the shared test DB cursor. "
        "Tracked for a future pass once a ``concurrent_clients(n)`` "
        "harness helper exists in E2ETestCase."
    )
    def test_11_12_concurrent_writes(self):
        """Scenario 11.12: 10 clients, no deadlocks, no lost updates."""


class TestCluster11f_HistoryGrowth(StressTestCase):
    """11.13 — many revisions on one row."""

    e2e_models = ALL_MODELS

    def seed_data(self):
        """
        Override: 11.13 doesn't need the full seed. We want one
        counterparty, one period, one invoice — and then hammer
        history.
        """
        from .models import StressCounterparty, StressPeriod
        from datetime import date

        self.counterparty_ids = self.bulk_seed(
            StressCounterparty, 1,
            factory=lambda i: StressCounterparty(name="CP-single"),
        )
        self.period_ids = self.bulk_seed(
            StressPeriod, 1,
            factory=lambda i: StressPeriod(
                label="P-single",
                period_from=date(2025, 1, 1),
                period_to=date(2025, 1, 31),
            ),
        )
        self.invoice_ids = []

    def test_11_13_history_growth(self):
        """
        Scenario 11.13: hammer a single invoice with many updates.

        Intent: the history table supports unbounded revisions
        without degrading the main-row query. We:

        * Create one invoice.
        * Update it ``n`` times (tier-scaled).
        * Assert history count == n+1 (create + n updates).
        * Assert ``qs.history.all()[:50]`` returns quickly — the
          history index on ``history_date`` should make a top-N page
          bounded regardless of total revisions.

        This catches the regression where someone indexes history by
        something other than ``history_date`` and every history
        pagination becomes a full-table scan.
        """
        from datetime import date
        from decimal import Decimal

        # Scale revisions by tier — full 20k at LARGE, 500 at SMALL.
        n_by_tier = {"SMALL": 200, "MEDIUM": 2_000, "LARGE": 20_000}
        n = n_by_tier[self.volume]
        budgets = {"SMALL": 10.0, "MEDIUM": 60.0, "LARGE": 600.0}

        inv = StressInvoice.objects.create(
            invoice_number="INV-SINGLE",
            counterparty_id=self.counterparty_ids[0],
            period_id=self.period_ids[0],
            booked_on=date(2025, 1, 1),
            due_on=date(2025, 2, 1),
            amount_net=Decimal("100.00"),
            amount_tax=Decimal("20.00"),
            amount_gross=Decimal("120.00"),
        )

        with self.assert_runtime_under(
            budgets[self.volume], "11.13_history_growth",
        ), self.measure("11.13_history_growth"):
            for i in range(n):
                inv.amount_net = Decimal(f"{100 + i}.00")
                inv.save()

        self.assertEqual(
            inv.history.count(), n + 1,
            f"Single-row with {n} updates must have {n + 1} history "
            f"rows (create + n updates); got {inv.history.count()}.",
        )

        # Top-N history page must be fast regardless of total.
        with self.assert_runtime_under(
            1.0, "11.13_history_top50",
        ), self.measure("11.13_history_top50"):
            top = list(inv.history.all()[:50])
        self.assertEqual(len(top), 50)


class TestCluster11f_PermissionAtScale(StressTestCase):
    """11.14 — ``permission_read`` invocation count."""

    e2e_models = ALL_MODELS

    # @unittest.expectedFailure  # BUG-011: permission_read called per-row on list
    def test_11_14_permission_read_called_once_per_list(self):
        """
        Scenario 11.14: ``permission_read`` fires once per list
        request, not once per row.

        Intent: permission evaluation is a per-user-per-request
        check, not a per-row check. If the framework evaluates
        ``permission_read`` once per serialized row, a 20k-row page
        generates 20k evaluations — burning time proportional to
        ``n``.

        Patch ``StressInvoice.permission_read`` with a counter and
        assert the observed count is **at most** a small constant.
        We allow up to 4 to absorb permission probing done by the
        list view or filter backends for preflight / metadata.

        **Note**: this scenario may surface the same root cause as
        BUG-011 (scenario 11.3). Keep the assertion tight — the
        correct behaviour is a single call.
        """
        call_counter = {"n": 0}
        original = StressInvoice.permission_read

        def counting_permission_read(self, uc):
            call_counter["n"] += 1
            return PermissionResult.allow_all("cluster 11: counted")

        with patch.object(
            StressInvoice, "permission_read", counting_permission_read,
        ), self.measure("11.14_permission_calls"):
            resp = self.list_get(
                INVOICE,
                query_params={"page": 1, "page_size": 100},
            )

        self.assertEqual(resp.status_code, 200)
        self.assertLessEqual(
            call_counter["n"], 4,
            f"permission_read must be called at most a small constant "
            f"number of times per list request — one check per user "
            f"per request is the documented contract. Observed "
            f"{call_counter['n']} calls for a 100-row page. This is "
            "likely the root cause of BUG-011 (N+1 on list).",
        )


class TestCluster11f_AuditLogThroughput(StressTestCase):
    """11.15 — audit-log write throughput."""

    e2e_models = ALL_MODELS

    @unittest.skip(
        "Scenario 11.15: audit-log throughput. The current "
        "AuditLogMixin only wraps ``perform_{create,update,destroy}`` "
        "on the API viewset — see scenario 6.4. A middleware-level "
        "audit hook is the prerequisite; until that lands, throughput "
        "cannot be measured end-to-end without inventing a second "
        "audit path just for this test."
    )
    def test_11_15_audit_log_throughput(self):
        """Scenario 11.15: 20k API writes → 20k audit rows in budget."""

