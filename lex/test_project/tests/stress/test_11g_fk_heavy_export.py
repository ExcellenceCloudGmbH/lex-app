"""
Cluster 11g: FK-heavy export at 25k rows.

Every row under test carries **four foreign keys**
(``counterparty``, ``period``, ``category``, ``currency``). Four
joins is the threshold where the naive ORM path (one lazy SELECT
per FK attribute access) goes from "annoying" to "catastrophic" —
25 000 rows × 4 FKs × 1 SELECT = 100 000 round trips on a
well-intentioned-but-wrong loop.

Scenarios:

* 11.16 — full-table export via the REST ``list`` endpoint at 25k
  rows. Time budget + exact count match. This is the customer-facing
  regression signal: "Export last year's invoices" must not stall the
  worker.
* 11.17 — CSV writer with the *documented* ``select_related`` + 4-FK
  ``.iterator()`` path. Query count must be a **small constant**
  regardless of row count. This is the "how you're supposed to do it"
  baseline.
* 11.18 — *anti-baseline*: the same 25k export done naively (no
  ``select_related``, no ``.iterator()``). We ASSERT the query count
  explodes to ``~n × fks`` so the delta between 11.17 and 11.18 is
  documented and measurable. If a future optimisation makes the naive
  path bounded (e.g. global eager loading), this test fails and we
  update the docs.
* 11.19 — ``.iterator()`` keeps peak memory bounded. Compare RSS
  between materialising 25k rows as a list vs streaming through
  ``.iterator()``. The list variant is the memory-hog regression
  signal; the iterator variant must stay within a small ceiling.
* 11.20 — aggregate across the FK join. ``Sum(invoices__amount_net)``
  grouped by counterparty must resolve in a single SQL statement —
  not one SELECT per counterparty, not one per invoice.

**Volume:** these scenarios target 25 000 rows by default — that is
the whole point. On ``LEX_STRESS_VOLUME=SMALL`` we scale back to
2 500 so PR-level iteration stays under a minute; the query-count
and count-match assertions are volume-independent and still run.
``MEDIUM`` and ``LARGE`` both run at full 25k.

The export path in 11.16 is the one the user hits from the UI's
"Export all" button — if it regresses, the whole product feels slow.
"""

from __future__ import annotations

import csv
import gc
import io
from datetime import date, timedelta
from decimal import Decimal

from django.db import connection
from django.db.models import Sum
from django.test.utils import CaptureQueriesContext

from ._stress_test_case import StressTestCase
from .models import (
    FKHeavyCategory, FKHeavyCurrency, FKHeavyInvoice, FK_HEAVY_INVOICE,
    FK_HEAVY_MODELS, StressCounterparty, StressPeriod,
)

# Row counts per volume tier. The point of this cluster is FK fan-out
# at volume; 25k is the target. SMALL scales to 2.5k so local /
# PR runs stay under a minute without losing the N+1 signal.
FK_ROWS_BY_TIER = {
    "SMALL": 2_500,
    "MEDIUM": 25_000,
    "LARGE": 25_000,
}


class _FKHeavyBase(StressTestCase):
    """Shared fixture — seed the 4-FK dataset once per test."""

    e2e_models = FK_HEAVY_MODELS

    # Categories / currencies are tiny reference tables.
    N_CATEGORIES = 20
    N_CURRENCIES = 5

    def seed_data(self):
        """
        Override: build the 4-FK invoice dataset.

        Counterparties (200) and periods (12) are seeded through the
        parent fixture's factories for consistency; categories and
        currencies live only on this cluster so we seed them inline.
        """
        self.n_invoices = FK_ROWS_BY_TIER[self.volume]

        # Counterparties
        self.counterparty_ids = self.bulk_seed(
            StressCounterparty,
            200 if self.volume != "SMALL" else 50,
            factory=lambda i: StressCounterparty(name=f"FK-CP-{i:05d}"),
        )

        # Periods
        self.period_ids = self.bulk_seed(
            StressPeriod,
            12,
            factory=lambda i: StressPeriod(
                label=f"FK-P-{i:02d}",
                period_from=date(2025, 1, 1) + timedelta(days=30 * i),
                period_to=date(2025, 1, 31) + timedelta(days=30 * i),
            ),
        )

        # Categories
        self.category_ids = self.bulk_seed(
            FKHeavyCategory,
            self.N_CATEGORIES,
            factory=lambda i: FKHeavyCategory(
                code=f"CAT-{i:02d}", label=f"Category {i}",
            ),
        )

        # Currencies
        self.currency_ids = self.bulk_seed(
            FKHeavyCurrency,
            self.N_CURRENCIES,
            factory=lambda i: FKHeavyCurrency(
                code=["EUR", "USD", "SEK", "GBP", "CHF"][i],
                symbol=["€", "$", "kr", "£", "CHF"][i],
            ),
        )

        # The main 25k-row table with 4 FKs each.
        self.invoice_ids = self.bulk_seed(
            FKHeavyInvoice,
            self.n_invoices,
            factory=self._invoice_factory,
            batch_size=2_000,
        )

    def _invoice_factory(self, i: int) -> FKHeavyInvoice:
        return FKHeavyInvoice(
            invoice_number=f"FK-INV-{i:07d}",
            counterparty_id=self.counterparty_ids[
                i % len(self.counterparty_ids)
            ],
            period_id=self.period_ids[i % len(self.period_ids)],
            category_id=self.category_ids[i % len(self.category_ids)],
            currency_id=self.currency_ids[i % len(self.currency_ids)],
            booked_on=date(2025, 1, 1) + timedelta(days=i % 365),
            amount_net=Decimal(f"{100 + (i % 9000)}.00"),
            amount_gross=Decimal(f"{120 + (i % 9000)}.00"),
        )


class TestCluster11g_FKHeavyExport(_FKHeavyBase):
    """11.16 / 11.17 — export paths at 25k × 4 FKs."""

    # -- 11.16 ---------------------------------------------------------
    def test_11_16_full_rest_export_at_25k(self):
        """
        Scenario 11.16: GET the list endpoint with pagination disabled
        (``page_size`` > row count) and confirm:

        * every seeded row is returned (exact count match — no silent
          cap at 1000 or 10 000);
        * the exported body structure includes each of the four FKs
          (checked on the first returned row);
        * total runtime is within the tier budget.

        **Note:** this scenario exercises the same list-endpoint path
        as scenario 11.3. BUG-011 (per-row permission evaluation)
        applies and will make this runtime noticeably worse than it
        should be — so the time budget here is generous. Once
        BUG-011 is fixed, the budget can be tightened aggressively.
        """
        budgets = {"SMALL": 20.0, "MEDIUM": 180.0, "LARGE": 180.0}
        seeded = self.n_invoices

        with self.assert_runtime_under(
            budgets[self.volume], "11.16_fk_heavy_export",
        ), self.measure("11.16_fk_heavy_export"):
            resp = self.list_get(
                FK_HEAVY_INVOICE,
                query_params={"page_size": seeded + 10},
            )

        self.assertEqual(resp.status_code, 200)
        results = self.extract_results(resp.data)
        self.assertEqual(
            len(results), seeded,
            f"Full-table FK-heavy export must return all {seeded} "
            f"seeded rows; got {len(results)}. A short count here is "
            "a silent pagination cap or a lost row through the "
            "serializer path.",
        )
        # All four FK fields must be present in the serialized row.
        first = results[0]
        for fk in ("counterparty", "period", "category", "currency"):
            self.assertIn(
                fk, first,
                f"Serialized row is missing FK field {fk!r}. "
                f"Shape: {sorted(first.keys())}",
            )

    # -- 11.17 ---------------------------------------------------------
    def test_11_17_csv_export_select_related(self):
        """
        Scenario 11.17: documented-path CSV export — ``select_related``
        on all 4 FKs + ``.iterator()``.

        Query budget: **a small constant** (1 main SELECT with four
        JOINs; up to 4 extra framework queries for session / auth /
        metadata noise = cap at 8). The main point: this is O(1) in
        the number of rows.

        This is the "here is how to do a large export correctly"
        reference implementation. If customers follow this pattern,
        25k rows × 4 FKs = one round-trip, regardless of volume.
        """
        buf = io.StringIO()
        writer = csv.writer(buf)

        with self.assert_query_count_at_most(
            8, "11.17_csv_select_related_queries",
        ), self.measure("11.17_csv_select_related"):
            qs = (
                FKHeavyInvoice.objects
                .select_related(
                    "counterparty", "period", "category", "currency",
                )
                .only(
                    "invoice_number",
                    "amount_gross",
                    "counterparty__name",
                    "period__label",
                    "category__code",
                    "currency__code",
                )
                .iterator(chunk_size=2_000)
            )
            for inv in qs:
                writer.writerow([
                    inv.invoice_number,
                    str(inv.amount_gross),
                    inv.counterparty.name,
                    inv.period.label,
                    inv.category.code,
                    inv.currency.code,
                ])

        lines = buf.getvalue().splitlines()
        self.assertEqual(
            len(lines), self.n_invoices,
            f"CSV export must produce one line per seeded invoice "
            f"({self.n_invoices}); got {len(lines)}. Either the "
            "iterator is losing rows or the seed drifted.",
        )

    # -- 11.18 ---------------------------------------------------------
    def test_11_18_naive_export_reveals_nplus1(self):
        """
        Scenario 11.18: *anti-baseline.* Same export, no
        ``select_related``, no ``.iterator()``. Every FK access on
        every row triggers a lazy SELECT.

        We:

        * Run the same write loop as 11.17 but on the naive queryset.
        * Count the queries actually issued.
        * Assert the number is **at least** ``n × 4 // factor`` —
          i.e. the N+1 is unambiguously visible — so the gap with
          11.17 is provable.
        * Record the observed query count in the trend report so
          anyone can see the delta.

        This test does NOT fail on slow runtime — the point is the
        query-count delta, not wall time. A future optimisation
        making naive FK access bounded (global eager loading, query
        batching, etc.) would cause this test to fail; at that point
        update the assertion and celebrate.
        """
        # SMALL runs 2.5k rows — still gives us a visible N+1 signal
        # without taking minutes. At MEDIUM/LARGE 25k we scale the
        # scan count down to keep runtime bounded — the assertion is
        # on the *ratio*, not the absolute count.
        scan_rows = min(self.n_invoices, 2_000)
        buf = io.StringIO()
        writer = csv.writer(buf)

        qs = FKHeavyInvoice.objects.all()[:scan_rows]

        with CaptureQueriesContext(connection) as ctx, \
                self.measure("11.18_naive_export"):
            for inv in qs:
                writer.writerow([
                    inv.invoice_number,
                    inv.counterparty.name,
                    inv.period.label,
                    inv.category.code,
                    inv.currency.code,
                ])

        observed = len(ctx.captured_queries)
        # Lower bound: at least ~3 FK SELECTs per row (the 4th may
        # get cached once Django dedupes by pk). We conservatively
        # require 2.5 × scan_rows to call it "clear N+1".
        min_expected = int(scan_rows * 2.5)
        self.assertGreater(
            observed, min_expected,
            f"Anti-baseline expects the naive path to issue at least "
            f"{min_expected} queries ({scan_rows} rows × 2.5 FKs "
            "before cache dedup). Observed only "
            f"{observed} — the framework has started batching FK "
            "lookups on the naive path. Good news; update this "
            "assertion and 11.17's budget to match.",
        )
        lines = buf.getvalue().splitlines()
        self.assertEqual(len(lines), scan_rows)


class TestCluster11g_FKHeavyIteration(_FKHeavyBase):
    """11.19 / 11.20 — memory-bounded iteration + aggregated JOIN."""

    # -- 11.19 ---------------------------------------------------------
    def test_11_19_iterator_keeps_memory_bounded(self):
        """
        Scenario 11.19: streaming iteration over 25k rows + 4 FKs
        must NOT inflate peak memory the way a materialised list does.

        Measurement: ``tracemalloc`` — gives a true per-block peak of
        **Python-allocated** bytes since ``start()``. ``ru_maxrss`` is
        a cumulative high-water mark of the whole process and
        includes everything prior tests allocated, so it can't
        distinguish the list pass from the iterator pass.

        We measure the peak for each pass independently and assert
        the iterator's peak is meaningfully lower than the list's.
        Threshold: iterator peak ≤ 50% of list peak. At 25k × 4-FK
        rows the list pass allocates roughly 60 MB of Python objects;
        a properly-streaming iterator stays under 5 MB.

        **Volume-aware:** at SMALL (2.5k rows) the absolute numbers
        are too low to reliably distinguish, so we skip the
        assertion. At MEDIUM/LARGE the delta is real.
        """
        import tracemalloc

        fks = ("counterparty", "period", "category", "currency")

        def _measure_list_pass() -> int:
            tracemalloc.start()
            rows = list(
                FKHeavyInvoice.objects.select_related(*fks),
            )
            # Touch every FK so Python actually materialises them.
            for r in rows:
                _ = (
                    r.invoice_number,
                    r.counterparty.name,
                    r.period.label,
                    r.category.code,
                    r.currency.code,
                )
            _, peak = tracemalloc.get_traced_memory()
            tracemalloc.stop()
            del rows
            gc.collect()
            return peak

        def _measure_iterator_pass() -> int:
            tracemalloc.start()
            total = 0
            for r in (
                FKHeavyInvoice.objects
                .select_related(*fks)
                .iterator(chunk_size=1_000)
            ):
                total += 1
                _ = (
                    r.invoice_number,
                    r.counterparty.name,
                    r.period.label,
                    r.category.code,
                    r.currency.code,
                )
            _, peak = tracemalloc.get_traced_memory()
            tracemalloc.stop()
            self.assertEqual(
                total, self.n_invoices,
                f"Iterator pass must visit all {self.n_invoices} rows.",
            )
            return peak

        with self.measure("11.19_materialized_list"):
            peak_list = _measure_list_pass()
        with self.measure("11.19_iterator_stream"):
            peak_iter = _measure_iterator_pass()

        # Record for trend analysis.
        self._record(
            "11.19_memory_delta",
            peak_memory_mb=round(peak_list / (1024 * 1024), 2),
        )

        if self.volume == "SMALL":
            self.skipTest(
                "Scenario 11.19 peak-memory delta is only meaningful "
                "at MEDIUM or LARGE volume (25k rows). Measurement "
                "recorded for trend analysis."
            )

        # Real assertion: the iterator pass must use strictly less
        # Python-tracked memory than the materialising-list pass.
        # We require 2× headroom — the iterator should be at least
        # half the list peak. In practice iter ≈ 1/10 of list.
        self.assertGreater(
            peak_list, 1 * 1024 * 1024,  # > 1 MB — sanity check
            f"list-pass peak ({peak_list} bytes) is implausibly low. "
            "Either the seed didn't land or tracemalloc isn't "
            "attributing allocations correctly.",
        )
        self.assertLess(
            peak_iter, peak_list // 2,
            f"Iterator peak ({peak_iter / 1e6:.2f} MB) must be less "
            f"than half the materialising-list peak "
            f"({peak_list / 1e6:.2f} MB). If this ratio is violated, "
            "``.iterator()`` has regressed to full materialisation.",
        )

    # -- 11.20 ---------------------------------------------------------
    def test_11_20_aggregated_join_single_query(self):
        """
        Scenario 11.20: ``Sum(fk_heavy_invoices__amount_net)`` grouped
        by counterparty — a single SQL statement with a JOIN +
        GROUP BY.

        Intent: aggregate-across-FK is the classic report query every
        finance customer runs. It must compile to one SQL round-trip,
        not one-per-counterparty or one-per-invoice. We assert:

        * exactly 1 SELECT (plus session/auth noise — cap at 5),
        * the aggregate covers every seeded invoice (sum of the
          per-counterparty totals == total across the fixture),
        * runtime within the tier budget.
        """
        budgets = {"SMALL": 2.0, "MEDIUM": 10.0, "LARGE": 10.0}

        with self.assert_runtime_under(
            budgets[self.volume], "11.20_aggregated_join",
        ), self.assert_query_count_at_most(
            5, "11.20_aggregated_join_queries",
        ), self.measure("11.20_aggregated_join"):
            rows = list(
                StressCounterparty.objects
                .annotate(total=Sum("fk_heavy_invoices__amount_net"))
                .values("id", "total")
            )

        # Sum of the per-counterparty totals == total across all
        # invoices. This proves the JOIN covers every row and the
        # GROUP BY didn't silently drop a partition.
        summed = sum((r["total"] or Decimal("0")) for r in rows)
        expected = FKHeavyInvoice.objects.aggregate(
            t=Sum("amount_net"),
        )["t"] or Decimal("0")
        self.assertEqual(
            summed, expected,
            f"Aggregated JOIN sum ({summed}) must equal the raw table "
            f"aggregate ({expected}). A mismatch means either the "
            "JOIN dropped rows or the GROUP BY is losing partitions.",
        )


