"""
Diagnostic: what queries does ``LexModel.save()`` actually issue?

The April 20 perf trace showed **173 317 saves producing 656 278
SELECTs — ~3.8 SELECT per save**. The framework-overhead buckets
(CalculationLog, signals, hooks, etc.) only account for ~127 s of
the 1 379 s runtime. The other ~700 s of "overhead" is buried in
those per-save SELECTs. This test pins down **exactly** what SQL
a single ``.save()`` produces — INSERT path and UPDATE path —
so we can point at the extra SELECTs and count the fixable ones.

It does NOT assert budgets. It **prints the full query fingerprint**
to the test output so you can read it with your eyes.

Run with verbose output:

    lex test lex.test_project.tests.stress.test_11i_save_fingerprint \\
            --noinput --keepdb -v 2

What to look for:

* Every ``SELECT`` outside the INSERT path is a candidate for
  elimination / caching.
* ``simple_history`` and ``MetaHistory`` insert patterns — 3 writes
  per save is expected (main + history + meta_history); if we see
  *more* INSERTs that's a clue.
* ``django_content_type`` / ``auth_group`` / ``django_session`` —
  ContentType and permission machinery bleeding into hot save path.

Compare against the old-lex-app baseline mentally: a plain Django
``.save()`` on a minimal model = **1 INSERT, no SELECTs**.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from django.db import connection
from django.test.utils import CaptureQueriesContext

from ._stress_test_case import StressTestCase
from .models import (
    ALL_MODELS, StressCounterparty, StressInvoice, StressPeriod,
)

import pytest

pytestmark = pytest.mark.stress


class TestCluster11i_SaveSqlFingerprint(StressTestCase):
    """Pin down which SQL statements ``LexModel.save()`` actually runs."""

    e2e_models = ALL_MODELS

    def seed_data(self):
        """Minimal seed — 1 counterparty + 1 period. No invoices."""
        self.counterparty_ids = self.bulk_seed(
            StressCounterparty, 1,
            factory=lambda i: StressCounterparty(name="CP-fp"),
        )
        self.period_ids = self.bulk_seed(
            StressPeriod, 1,
            factory=lambda i: StressPeriod(
                label="P-fp",
                period_from=date(2025, 1, 1),
                period_to=date(2025, 1, 31),
            ),
        )
        self.invoice_ids = []

    @staticmethod
    def _classify(sql: str) -> str:
        s = sql.lstrip().upper()
        if s.startswith("SELECT"):
            return "SELECT"
        if s.startswith("INSERT"):
            return "INSERT"
        if s.startswith("UPDATE"):
            return "UPDATE"
        if s.startswith("DELETE"):
            return "DELETE"
        if s.startswith(("BEGIN", "COMMIT", "ROLLBACK", "SAVEPOINT", "RELEASE")):
            return "TX"
        return "OTHER"

    @staticmethod
    def _table_of(sql: str) -> str:
        # Extract first table-looking token after FROM/INTO/UPDATE.
        import re
        m = re.search(
            r"\b(?:FROM|INTO|UPDATE)\s+\"?([a-zA-Z0-9_]+)",
            sql, re.IGNORECASE,
        )
        return m.group(1) if m else "?"

    def _dump_queries(self, label: str, ctx: CaptureQueriesContext) -> None:
        print(f"\n{'='*80}")
        print(f"[fingerprint:{label}] {len(ctx.captured_queries)} queries total")
        print(f"{'='*80}")
        by_kind: dict = {}
        for i, q in enumerate(ctx.captured_queries, 1):
            sql = q["sql"]
            kind = self._classify(sql)
            table = self._table_of(sql)
            by_kind.setdefault(kind, []).append((table, sql))
            # Truncated print so output is readable
            short = sql[:140].replace("\n", " ")
            print(f"  {i:2d}. [{kind:6}] {table:30} {short}")

        print(f"\n[fingerprint:{label}] BY KIND:")
        for kind in ("SELECT", "INSERT", "UPDATE", "DELETE", "TX", "OTHER"):
            if kind in by_kind:
                print(f"  {kind}: {len(by_kind[kind])}")
                # Table breakdown within kind
                tables: dict = {}
                for t, _ in by_kind[kind]:
                    tables[t] = tables.get(t, 0) + 1
                for t, n in sorted(tables.items(), key=lambda x: -x[1]):
                    print(f"    {n:3d}× {t}")

    def test_fingerprint_fresh_insert(self):
        """Fingerprint 1 — first save of a brand-new StressInvoice (INSERT path)."""
        inv = StressInvoice(
            invoice_number="INV-FP-001",
            counterparty_id=self.counterparty_ids[0],
            period_id=self.period_ids[0],
            booked_on=date(2025, 1, 15),
            due_on=date(2025, 2, 15),
            amount_net=Decimal("100.00"),
            amount_tax=Decimal("20.00"),
            amount_gross=Decimal("120.00"),
        )

        with CaptureQueriesContext(connection) as ctx:
            inv.save()

        self._dump_queries("fresh_insert", ctx)

    def test_fingerprint_update_existing(self):
        """Fingerprint 2 — second save of an existing row (UPDATE path)."""
        inv = StressInvoice.objects.create(
            invoice_number="INV-FP-002",
            counterparty_id=self.counterparty_ids[0],
            period_id=self.period_ids[0],
            booked_on=date(2025, 1, 15),
            due_on=date(2025, 2, 15),
            amount_net=Decimal("100.00"),
            amount_tax=Decimal("20.00"),
            amount_gross=Decimal("120.00"),
        )

        # Now mutate + save again — the UPDATE path.
        inv.amount_net = Decimal("101.00")
        with CaptureQueriesContext(connection) as ctx:
            inv.save()

        self._dump_queries("update_existing", ctx)

    def test_fingerprint_batch_1000_saves(self):
        """
        Fingerprint 3 — 1000 individual ``.save()`` calls.

        Counts TOTAL SELECTs and INSERTs across the batch so we can
        compute **queries-per-save** empirically. The production
        trace showed 3.8 SELECT/save; this number should match
        (or explain the gap).
        """
        # Freshen FK data
        cp = self.counterparty_ids[0]
        pd = self.period_ids[0]
        base_day = date(2025, 1, 1)

        with CaptureQueriesContext(connection) as ctx:
            for i in range(1000):
                inv = StressInvoice(
                    invoice_number=f"INV-FP-BATCH-{i:05d}",
                    counterparty_id=cp,
                    period_id=pd,
                    booked_on=base_day + timedelta(days=i % 365),
                    due_on=base_day + timedelta(days=(i + 30) % 365),
                    amount_net=Decimal("10.00"),
                    amount_tax=Decimal("2.00"),
                    amount_gross=Decimal("12.00"),
                )
                inv.save()

        kinds: dict = {}
        by_table: dict = {}
        for q in ctx.captured_queries:
            k = self._classify(q["sql"])
            kinds[k] = kinds.get(k, 0) + 1
            by_table[(k, self._table_of(q["sql"]))] = (
                by_table.get((k, self._table_of(q["sql"])), 0) + 1
            )

        n_saves = 1000
        print(f"\n{'='*80}")
        print(f"[fingerprint:batch_1000] per-save SQL amplification")
        print(f"{'='*80}")
        total = len(ctx.captured_queries)
        print(f"  total queries: {total}  ({total/n_saves:.2f} per save)")
        for kind in ("SELECT", "INSERT", "UPDATE", "DELETE", "TX", "OTHER"):
            n = kinds.get(kind, 0)
            print(f"  {kind:6}: {n:>6}  ({n/n_saves:.2f} per save)")
        print(f"\n  by (kind, table) — top 20:")
        for (k, t), n in sorted(by_table.items(), key=lambda x: -x[1])[:20]:
            print(f"    {n:>6}× {k:<7} {t}")
        print(f"\n  Production trace observed 3.8 SELECT/save (173 317 "
              f"saves / 656 278 SELECTs).")
        print(f"  If this run shows the same ratio, the overhead is in "
              f"the save path itself.")

    def test_fingerprint_batch_with_suppression(self):
        """
        Fingerprint 4 — the same 1000-save batch, run with the
        bitemporal chaining signals SUPPRESSED.

        This is the diagnostic that proves whether bitemporal
        chaining is the per-save hot spot. We wrap the batch in
        ``suppress_history_valid_to_chaining`` +
        ``suppress_meta_sys_to_chaining`` + ``suppress_main_table_sync``
        — the three context managers already defined in
        ``lex/core/services/bitemporal_signals.py`` and currently
        only used by ``backfill_bitemporal_history``.

        If the query count drops dramatically here, the fix is
        "apply the existing suppression during calculation hooks
        when the customer opts in". If it doesn't drop, the
        overhead is elsewhere.

        Expectation from the production trace (3.8 SELECT/save
        observed) and the fresh-insert fingerprint (7 SELECT/save
        including 2 FK + 5 bitemporal): suppressing chaining should
        knock SELECT-per-save from ~7 down to ~2 (FK loads only).
        """
        from lex.core.services.bitemporal_signals import (
            suppress_history_valid_to_chaining,
            suppress_main_table_sync,
            suppress_meta_sys_to_chaining,
        )

        cp = self.counterparty_ids[0]
        pd = self.period_ids[0]
        base_day = date(2025, 1, 1)

        with suppress_history_valid_to_chaining(), \
                suppress_meta_sys_to_chaining(), \
                suppress_main_table_sync(), \
                CaptureQueriesContext(connection) as ctx:
            for i in range(1000):
                inv = StressInvoice(
                    invoice_number=f"INV-FP-SUPPRESSED-{i:05d}",
                    counterparty_id=cp,
                    period_id=pd,
                    booked_on=base_day + timedelta(days=i % 365),
                    due_on=base_day + timedelta(days=(i + 30) % 365),
                    amount_net=Decimal("10.00"),
                    amount_tax=Decimal("2.00"),
                    amount_gross=Decimal("12.00"),
                )
                inv.save()

        kinds: dict = {}
        by_table: dict = {}
        for q in ctx.captured_queries:
            k = self._classify(q["sql"])
            kinds[k] = kinds.get(k, 0) + 1
            by_table[(k, self._table_of(q["sql"]))] = (
                by_table.get((k, self._table_of(q["sql"])), 0) + 1
            )

        n_saves = 1000
        total = len(ctx.captured_queries)
        print(f"\n{'='*80}")
        print(f"[fingerprint:batch_1000_SUPPRESSED] bitemporal chaining OFF")
        print(f"{'='*80}")
        print(f"  total queries: {total}  ({total/n_saves:.2f} per save)")
        for kind in ("SELECT", "INSERT", "UPDATE", "DELETE", "TX", "OTHER"):
            n = kinds.get(kind, 0)
            print(f"  {kind:6}: {n:>6}  ({n/n_saves:.2f} per save)")
        print(f"\n  by (kind, table) — top 20:")
        for (k, t), n in sorted(by_table.items(), key=lambda x: -x[1])[:20]:
            print(f"    {n:>6}× {k:<7} {t}")
        print(
            f"\n  If SELECT/save drops from ~7 to ~2 here, the production "
            f"regression is DEFINITIVELY caused by the bitemporal chaining "
            f"signals firing on every save. The fix is to expose these "
            f"suppression context managers to customers (or run them "
            f"automatically inside calculate_hook when bitemporal chaining "
            f"is not needed during the calc, and recompute the chain once "
            f"at the end). See docs/perf-bottleneck-2026-04-20.md."
        )


