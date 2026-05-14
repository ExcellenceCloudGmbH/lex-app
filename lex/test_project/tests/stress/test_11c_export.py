"""
Cluster 11c: Export throughput.

Scenarios:

* 11.5 — full-table export via the REST ``many/`` endpoint. Must
  complete within the time budget and exported row count must match
  seeded count exactly. A streaming response is preferred (bounded
  memory); we record peak RSS for trend analysis but don't hard-gate
  it — the test infra can't tell streaming from buffered in every
  framework version. The row-count and time gates ARE hard.
* 11.6 — CSV writer loop over a queryset. Catches the regression
  where someone iterates without ``.iterator()`` and pins 20k rows
  plus their FK joins in memory + fires one SELECT per FK touch.
"""

from __future__ import annotations

import csv
import io

from ._stress_test_case import StressTestCase
from .models import ALL_MODELS, INVOICE, StressInvoice


class TestCluster11c_Export(StressTestCase):
    """11.5 / 11.6 — bulk export."""

    e2e_models = ALL_MODELS

    # -- 11.5 ----------------------------------------------------------
    def test_11_5_full_table_export(self):
        """
        Scenario 11.5: full-table GET returns every seeded invoice
        inside the time budget.

        We disable pagination (``page_size`` large enough to cover all
        seeded rows) and assert:

        * response status 200
        * body length == seeded invoice count — exactly, no silent
          truncation
        * runtime inside the tier-indexed budget.
        """
        budgets = {"SMALL": 5.0, "MEDIUM": 15.0, "LARGE": 30.0}
        seeded = len(self.invoice_ids)
        self.assertGreater(seeded, 0, "Must have seeded invoices.")

        with self.assert_runtime_under(
            budgets[self.volume], "11.5_full_export",
        ), self.measure("11.5_full_export"):
            resp = self.list_get(
                INVOICE,
                query_params={"page_size": seeded + 10},
            )

        self.assertEqual(resp.status_code, 200)
        results = self.extract_results(resp.data)
        self.assertEqual(
            len(results), seeded,
            f"Full-table export must return exactly the seeded count. "
            f"Seeded={seeded}, got={len(results)}. A mismatch here is "
            "either a silent pagination cap or a lost row in the "
            "serializer path.",
        )

    # -- 11.6 ----------------------------------------------------------
    def test_11_6_csv_writer_no_nplus1(self):
        """
        Scenario 11.6: iterate a queryset and write each row to a CSV
        sink. Must NOT do a per-row FK lookup.

        Query budget: a small constant (select + prefetch FK tables) —
        ideally 3, we cap at 5 to absorb session/metadata noise. If a
        future refactor drops the ``select_related`` or iterates
        without ``.iterator()`` + eager join, this gate catches it.
        """
        buf = io.StringIO()
        writer = csv.writer(buf)

        with self.assert_query_count_at_most(
            5, "11.6_csv_export_queries",
        ), self.measure("11.6_csv_export"):
            qs = (
                StressInvoice.objects
                .select_related("counterparty", "period")
                .only(
                    "invoice_number",
                    "amount_gross",
                    "counterparty__name",
                    "period__label",
                )
                .iterator(chunk_size=1000)
            )
            for inv in qs:
                writer.writerow([
                    inv.invoice_number,
                    str(inv.amount_gross),
                    inv.counterparty.name,
                    inv.period.label,
                ])

        lines = buf.getvalue().splitlines()
        self.assertEqual(
            len(lines), len(self.invoice_ids),
            f"CSV export must produce one line per seeded invoice "
            f"({len(self.invoice_ids)}); got {len(lines)}. Either the "
            "iterator is losing rows or the test fixture drifted.",
        )

