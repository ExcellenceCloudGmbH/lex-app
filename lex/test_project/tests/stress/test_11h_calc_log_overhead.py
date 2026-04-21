"""
Cluster 11h: ``CalculationLog.log`` per-call overhead benchmark.

Direct response to the April 20 perf-bottleneck investigation.
The production trace showed 4 638 calls to ``CalculationLog.log()``
costing **~70 seconds** in aggregate (8.5 ms Python + ~5 ms DB per
call + WebSocket + cache). That is the single biggest framework
overhead in the 23-minute ``Period`` calculation and is the #1
"didn't exist in old lex-app" cost.

This benchmark runs the same workload twice — once with the live
``CalculationLog`` pipeline, once with it patched to a no-op — and
reports the **delta per call**. It is the regression gate for any
future change claiming "I made CalculationLog faster".

Scenario 11.22:
    Measure per-call overhead of ``CalculationLog.log`` through its
    full live pipeline (ContextResolver → AuditLog SELECT FOR UPDATE
    → CalculationLog UPDATE → CacheManager → channel_layer broadcast)
    vs a no-op patched baseline. Assert the per-call cost is within a
    declared budget — currently generous at 15 ms because the
    production trace observed ~15 ms — but tighten aggressively once
    the batched-flush optimisation lands.

How to re-run after an optimisation:

    lex test lex.test_project.tests.stress.test_11h_calc_log_overhead \\
            --noinput --keepdb -v 2

The test prints both absolute times and the observed ms/call, so a
PR that reduces the overhead can be validated at a glance.

Scenario numbering extends the Cluster 11 plan in
docs/test-plan/test-clusters.md.
"""

from __future__ import annotations

import time
from decimal import Decimal
from unittest.mock import patch

from lex.audit_logging.models.AuditLog import AuditLog
from lex.audit_logging.models.AuditLogStatus import AuditLogStatus
from lex.audit_logging.models.CalculationLog import CalculationLog

from ._stress_test_case import StressTestCase
from .models import ALL_MODELS, StressInvoice


class TestCluster11h_CalculationLogOverhead(StressTestCase):
    """11.22 — CalculationLog.log per-call cost, live pipeline vs no-op."""

    e2e_models = ALL_MODELS
    # CalculationLog uses AuditLog as its anchor; we need the tables.
    e2e_framework_models = [AuditLog, AuditLogStatus, CalculationLog]

    # Number of log calls to run in each pass. 500 is enough to smooth
    # out jitter while staying under a minute in CI.
    N_CALLS = 500

    # Budget: the production observation was ~15 ms per call. Allow 2×
    # headroom for CI variance; tighten once the batched-flush
    # optimisation lands. When the budget is consistently met at 5 ms
    # or less, we've moved the needle.
    BUDGET_MS_PER_CALL = 30.0

    def seed_data(self):
        """Override: no bulk seed — we only need one AuditLog row."""
        self.counterparty_ids = []
        self.period_ids = []
        self.invoice_ids = []
        # One AuditLog row that ContextResolver.resolve() can find.
        self.audit_log = AuditLog.objects.create(
            resource="stressinvoice",
            action="create",
            calculation_id="calc-11-22",
        )

    # -- 11.22 ---------------------------------------------------------
    def test_11_22_calc_log_overhead(self):
        """
        Scenario 11.22: measure the per-call cost of the live
        ``CalculationLog.log`` pipeline and record it for trend
        analysis.

        Baseline run patches ``CalculationLog.log`` to a no-op so we
        measure only the test-loop overhead. The live run exercises
        the full pipeline. The difference is the pure framework cost.

        We assert the per-call cost is under a generous budget and
        record the live observation in the trend JSONL so a PR can
        show "I moved it from 15 ms to 5 ms".
        """
        # Prime the operation_context — ContextResolver.resolve() needs
        # a calculation_id on the context var.
        with self.operation_context("calc-11-22"):
            # --- baseline: CalculationLog.log patched to no-op -------
            with patch.object(CalculationLog, "log", lambda *a, **k: None):
                t0 = time.perf_counter()
                for i in range(self.N_CALLS):
                    CalculationLog.log(f"baseline-{i}")
                baseline_duration = time.perf_counter() - t0

            # --- live run: the real pipeline -------------------------
            t0 = time.perf_counter()
            for i in range(self.N_CALLS):
                CalculationLog.log(
                    f"live-{i}: "
                    f"Some calc message {Decimal('123.45') * i}"
                )
            live_duration = time.perf_counter() - t0

        overhead_total_s = max(0.0, live_duration - baseline_duration)
        overhead_ms_per_call = overhead_total_s / self.N_CALLS * 1000.0

        # Record for the trend report.
        self._record(
            "11.22_calc_log_overhead",
            duration=live_duration,
            query_count=None,
        )
        self._record(
            "11.22_calc_log_per_call_ms",
            duration=overhead_ms_per_call / 1000.0,
        )

        # Human-readable diagnostic (shown in verbose test output).
        print(
            f"\n[stress:11.22] CalculationLog.log overhead @ "
            f"N={self.N_CALLS}: baseline={baseline_duration*1000:.1f}ms "
            f"live={live_duration*1000:.1f}ms "
            f"delta={overhead_total_s*1000:.1f}ms "
            f"({overhead_ms_per_call:.2f} ms/call)\n"
            f"[stress:11.22] Production trace observed ~15 ms/call over "
            f"4 638 calls = 70 s overhead. Target after batched-flush "
            f"optimisation: ≤ 2 ms/call."
        )

        self.assertLess(
            overhead_ms_per_call, self.BUDGET_MS_PER_CALL,
            f"CalculationLog.log per-call overhead is "
            f"{overhead_ms_per_call:.2f} ms; budget "
            f"{self.BUDGET_MS_PER_CALL} ms. A per-call cost above 5 ms "
            "means a production Period calc (~4 600 log calls) pays "
            "more than 23 seconds in pure framework-logging overhead. "
            "See docs/perf-bottleneck-2026-04-20.md for the full "
            "analysis and recommended fix (batched flush).",
        )


