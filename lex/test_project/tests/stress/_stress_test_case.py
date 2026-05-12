"""
``StressTestCase`` — base class for Cluster 11 stress / performance tests.

Extends :class:`E2ETestCase` with:

* ``bulk_seed(model, n, factory, batch_size=1000)`` — chunked
  ``bulk_create``; returns the full id list so tests can reference
  seeded rows without re-querying.
* ``VOLUME`` — class attribute controlling the seed count.
  Overridable per-test via the ``LEX_STRESS_VOLUME`` env var
  (``SMALL`` / ``MEDIUM`` / ``LARGE``). Default is ``SMALL`` so local
  runs stay fast; CI nightly sets ``MEDIUM``, release-gate sets
  ``LARGE``.
* ``assert_runtime_under(seconds, label)`` — hard time gate with a
  human-readable failure message.
* ``assert_query_count_at_most(n, label)`` — context manager wrapping
  ``CaptureQueriesContext`` so N+1 regressions fail loudly.
* ``measure(label)`` — records ``(duration, query_count,
  peak_memory_mb)`` into a JSON trend report at
  ``test_report/stress/<run-id>.json`` for 90-day drift analysis.
  Budgets are still asserted inline; the JSON is extra.

Seed data is deterministic (``random.seed(42)``) so CI runtime trends
reflect code changes, not input variance.
"""

from __future__ import annotations

import json
import os
import random
import time
from contextlib import contextmanager
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable

from django.db import connection
from django.test.utils import CaptureQueriesContext
from lex.tests.e2e._e2e_test_case import E2ETestCase

# ── Volume tiers ────────────────────────────────────────────────────

VOLUMES = {
    "SMALL": {
        "counterparties": 50,
        "periods": 6,
        "invoices": 500,
    },
    "MEDIUM": {
        "counterparties": 100,
        "periods": 12,
        "invoices": 5_000,
    },
    "LARGE": {
        "counterparties": 200,
        "periods": 12,
        "invoices": 20_000,
    },
}

DEFAULT_VOLUME = "SMALL"


def _current_volume() -> str:
    """Resolve active volume tier from env, defaulting to SMALL."""
    v = os.environ.get("LEX_STRESS_VOLUME", DEFAULT_VOLUME).upper()
    if v not in VOLUMES:
        raise ValueError(
            f"LEX_STRESS_VOLUME={v!r} not in {list(VOLUMES)}",
        )
    return v


def _report_dir() -> Path:
    """Path to the stress-trend report dir — created on demand."""
    root = Path(os.environ.get(
        "LEX_STRESS_REPORT_DIR", "test_reports/stress",
    ))
    root.mkdir(parents=True, exist_ok=True)
    return root


# ── memory helper ───────────────────────────────────────────────────

def _peak_memory_mb() -> float:
    """Best-effort RSS peak (MB). Linux-only — returns 0.0 elsewhere."""
    try:
        import resource  # POSIX only

        # ru_maxrss is KB on Linux, bytes on macOS. We target Linux CI.
        kb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return kb / 1024.0
    except Exception:  # pragma: no cover
        return 0.0


# ── base class ──────────────────────────────────────────────────────

class StressTestCase(E2ETestCase):
    """
    Base class for Cluster 11.

    Subclasses must set ``e2e_models`` (the stress models) and may
    override :meth:`seed_data` to control what gets bulk-loaded. Seed
    data is created in ``setUp`` (per-test) so each test starts from a
    known baseline — cheap for SMALL, noticeable for LARGE. If a
    subclass needs the seed reused across multiple tests, it can opt
    into class-level seeding by setting ``seed_once = True`` and
    overriding :meth:`setUpClass` to invoke :meth:`seed_data` there.

    Tests assert time / query budgets with the helpers below and
    optionally wrap a block in :meth:`measure` to drop structured
    results into the per-run JSON report.
    """

    # Subclasses override to pull in stress models.
    e2e_models: list = []

    # Per-test seeding by default.
    seed_once: bool = False
    _seeded_at_class: bool = False

    # Populated during :meth:`seed_data` — subclasses can read.
    counterparty_ids: list
    period_ids: list
    invoice_ids: list

    # Results captured by :meth:`measure`. Flushed to disk in tearDown.
    _measurements: list

    # ── lifecycle ──────────────────────────────────────────────────

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._seeded_at_class = False

    def setUp(self):
        super().setUp()
        self.volume = _current_volume()
        self.volume_spec = VOLUMES[self.volume]
        self._measurements = []
        self.counterparty_ids = []
        self.period_ids = []
        self.invoice_ids = []
        random.seed(42)

        if self.seed_once:
            # Once-per-class seeding: do it lazily on first test so
            # setUpClass doesn't have to. Guarded by a class flag.
            if not type(self)._seeded_at_class:
                self.seed_data()
                type(self)._seeded_at_class = True
            # And rehydrate the id lists for this instance.
            self._rehydrate_ids()
        else:
            self.seed_data()

    def tearDown(self):
        self._flush_measurements()
        super().tearDown()

    # ── seeders ────────────────────────────────────────────────────

    def seed_data(self):
        """
        Default seeder: counterparties, 12 monthly periods, invoices.

        Subclasses that want a different shape (e.g. one row with 20k
        history revisions for 11.13) override this and don't call
        ``super().seed_data()``.
        """
        from .models import StressCounterparty, StressInvoice, StressPeriod

        self.counterparty_ids = self.bulk_seed(
            StressCounterparty,
            self.volume_spec["counterparties"],
            factory=lambda i: StressCounterparty(
                name=f"CP-{i:05d}",
                country=random.choice(["SE", "DE", "FR", "IT", "ES"]),
            ),
        )

        self.period_ids = self.bulk_seed(
            StressPeriod,
            self.volume_spec["periods"],
            factory=self._period_factory,
        )

        # Invoices spread round-robin across periods and counterparties.
        self.invoice_ids = self.bulk_seed(
            StressInvoice,
            self.volume_spec["invoices"],
            factory=self._invoice_factory,
        )

    def _period_factory(self, i: int):
        from .models import StressPeriod

        # Monthly periods rolling forward from 2025-01-01, so
        # ``period_from`` is ordered and filters / sorts are
        # non-trivial.
        start = date(2025, 1, 1) + timedelta(days=30 * i)
        end = start + timedelta(days=29)
        return StressPeriod(
            label=f"P-{start.isoformat()}",
            period_from=start,
            period_to=end,
        )

    def _invoice_factory(self, i: int):
        from .models import StressInvoice

        cp_id = self.counterparty_ids[i % len(self.counterparty_ids)]
        p_id = self.period_ids[i % len(self.period_ids)]
        net = Decimal(random.randint(10_00, 100_000_00)) / 100
        tax = (net * Decimal("0.20")).quantize(Decimal("0.01"))
        return StressInvoice(
            invoice_number=f"INV-{i:06d}",
            counterparty_id=cp_id,
            period_id=p_id,
            booked_on=date(2025, 1, 1) + timedelta(days=i % 365),
            due_on=date(2025, 1, 1) + timedelta(days=(i % 365) + 30),
            amount_net=net,
            amount_tax=tax,
            amount_gross=(net + tax).quantize(Decimal("0.01")),
            currency=random.choice(["EUR", "USD", "SEK"]),
            category=random.choice(["standard", "reversed", "credit"]),
            is_posted=(i % 3 != 0),
        )

    def _rehydrate_ids(self):
        """After class-level seed, repopulate id lists for this test."""
        from .models import StressCounterparty, StressInvoice, StressPeriod

        self.counterparty_ids = list(
            StressCounterparty.objects.values_list("id", flat=True),
        )
        self.period_ids = list(
            StressPeriod.objects.values_list("id", flat=True),
        )
        self.invoice_ids = list(
            StressInvoice.objects.values_list("id", flat=True),
        )

    def bulk_seed(
        self,
        model,
        n: int,
        factory: Callable[[int], Any],
        batch_size: int = 1000,
        skip_history: bool = True,
    ) -> list:
        """
        Bulk-insert ``n`` rows of ``model`` produced by ``factory(i)``.

        Returns the list of primary keys in insertion order. Uses
        ``bulk_create`` with chunked batches so memory stays bounded
        even at 20k rows.

        ``skip_history`` defaults to ``True`` because this is a test
        fixture helper — we want fast seed, and cluster 5 already
        covers history correctness. Scenario 11.1 flips it back on to
        measure the documented history-preserving fast-path.

        ``LexModel.bulk_create`` accepts ``skip_history``; plain
        ``models.Manager.bulk_create`` does not. We only forward the
        kwarg if the manager supports it.
        """
        import inspect

        bulk_kwargs = {}
        sig = inspect.signature(model.objects.bulk_create)
        if "skip_history" in sig.parameters:
            bulk_kwargs["skip_history"] = skip_history

        batch = []
        created_ids: list = []
        for i in range(n):
            batch.append(factory(i))
            if len(batch) >= batch_size:
                created = model.objects.bulk_create(batch, **bulk_kwargs)
                created_ids.extend(obj.pk for obj in created)
                batch.clear()
        if batch:
            created = model.objects.bulk_create(batch, **bulk_kwargs)
            created_ids.extend(obj.pk for obj in created)
        return created_ids

    # ── assertion helpers ──────────────────────────────────────────

    @contextmanager
    def assert_runtime_under(self, seconds: float, label: str):
        """Fail with a clear message if the block exceeds ``seconds``."""
        t0 = time.perf_counter()
        try:
            yield
        finally:
            dt = time.perf_counter() - t0
            self._record(label, duration=dt)
            self.assertLess(
                dt, seconds,
                f"[stress:{label}] took {dt:.3f}s, budget {seconds:.3f}s "
                f"(volume={self.volume}). Budgets tighten, never loosen "
                "— if this is a legitimate new cost, update the budget "
                "AND the baseline in the same commit.",
            )

    @contextmanager
    def assert_query_count_at_most(self, n: int, label: str):
        """
        Fail if the block runs more than ``n`` SQL queries. Catches
        the N+1 regression class directly.
        """
        with CaptureQueriesContext(connection) as ctx:
            yield ctx
        observed = len(ctx.captured_queries)
        self._record(label, query_count=observed)
        self.assertLessEqual(
            observed, n,
            f"[stress:{label}] ran {observed} queries, budget {n} "
            f"(volume={self.volume}). Likely an N+1: inspect "
            "connection.queries in a debugger. First 5 queries:\n"
            + "\n".join(
                q["sql"][:200] for q in ctx.captured_queries[:5]
            ),
        )

    @contextmanager
    def measure(self, label: str):
        """
        Wrap a block to capture ``(duration, query_count, peak_mb)``
        into the run's JSON trend report. Does NOT assert — combine
        with :meth:`assert_runtime_under` / :meth:`assert_query_count_at_most`
        if you also want budget gates.
        """
        t0 = time.perf_counter()
        mem0 = _peak_memory_mb()
        with CaptureQueriesContext(connection) as ctx:
            yield
        dt = time.perf_counter() - t0
        mem1 = _peak_memory_mb()
        self._record(
            label,
            duration=dt,
            query_count=len(ctx.captured_queries),
            peak_memory_mb=max(mem0, mem1),
        )

    def _record(self, label: str, **fields):
        """Merge a single-label measurement into the current test's list."""
        # Find an existing entry for this label in this test; merge.
        for entry in self._measurements:
            if entry["label"] == label:
                entry.update({k: v for k, v in fields.items() if v is not None})
                return
        self._measurements.append({
            "label": label,
            "volume": self.volume,
            "test": self.id(),
            **{k: v for k, v in fields.items() if v is not None},
        })

    def _flush_measurements(self):
        if not self._measurements:
            return
        path = _report_dir() / f"{os.getpid()}.jsonl"
        with path.open("a") as fh:
            for entry in self._measurements:
                fh.write(json.dumps(entry) + "\n")




