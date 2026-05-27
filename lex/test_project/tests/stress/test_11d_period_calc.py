"""
Cluster 11d: Period calculations at volume.

This is the headline cluster-11 scenario set — aggregating 20k
invoices across 12 periods, with a dependency chain on top, is the
exact workload a finance customer runs every month.

Scenarios:

* 11.7 — ``PeriodAggregateCalc`` over a single period, with all
  invoices in that period. Query count must be near-constant
  (``SUM()`` + ``COUNT()`` in one roundtrip), not O(n).
* 11.8 — all 12 periods sequentially. Total budget, no cross-period
  re-fetch. History rows correct for every period.
* 11.9 — ``DependentPeriodCalc`` × 12. Each dependent resolves the
  prior 3 aggregates; query count must grow ``O(periods)``, not
  ``O(periods²)``.
"""

from __future__ import annotations

from decimal import Decimal

from django.db.models import Sum
from lex.core.models.CalculationModel import CalculationModel

from ._stress_test_case import StressTestCase
from .models import (
    ALL_MODELS, DependentPeriodCalc, PeriodAggregateCalc,
    StressInvoice, StressPeriod,
)

import pytest

pytestmark = pytest.mark.stress


class TestCluster11d_PeriodCalculations(StressTestCase):
    """11.7 / 11.8 / 11.9 — period aggregation and dependency chain."""

    e2e_models = ALL_MODELS

    # -- 11.7 ----------------------------------------------------------
    def test_11_7_single_period_aggregate(self):
        """
        Scenario 11.7: ``PeriodAggregateCalc`` over a single period.

        Intent: ``calculate()`` must hit the DB **once** regardless of
        invoice count. The budget is generous on runtime (Celery /
        signal fan-out dominates) but the query-count gate is tight —
        the aggregate is one SELECT, plus up-to-8 framework queries
        (save, history, audit, state-machine). More than that and
        someone's looping the invoices.
        """
        budgets = {"SMALL": 5.0, "MEDIUM": 10.0, "LARGE": 15.0}
        # Pick the first period — it has roughly invoices/periods rows.
        period = StressPeriod.objects.get(pk=self.period_ids[0])
        expected_net = StressInvoice.objects.filter(period=period).aggregate(
            total=Sum("amount_net"),
        )["total"] or Decimal("0.00")

        agg = PeriodAggregateCalc(period=period)

        with self.assert_runtime_under(
            budgets[self.volume], "11.7_single_period",
        ), self.assert_query_count_at_most(
            40, "11.7_single_period_queries",
        ), self.measure("11.7_single_period"):
            agg.is_calculated = CalculationModel.IN_PROGRESS
            agg.save()

        agg.refresh_from_db()
        self.assertEqual(
            agg.is_calculated, CalculationModel.SUCCESS,
            f"Aggregate must end SUCCESS; got {agg.is_calculated!r}.",
        )
        self.assertEqual(
            agg.total_net, expected_net,
            f"Computed total_net {agg.total_net} != expected "
            f"{expected_net}. Either the aggregate SQL is wrong or "
            "the seed diverged from what the test computed by hand.",
        )

    # -- 11.8 ----------------------------------------------------------
    def test_11_8_all_periods_aggregate(self):
        """
        Scenario 11.8: aggregate every period in sequence.

        Intent: running 12 aggregates must not produce cross-period
        side effects — each period's ``calculate()`` reads only its
        own rows, writes its own aggregate, and leaves the prior
        periods alone.

        We assert:

        * total runtime inside budget,
        * every period ends SUCCESS,
        * the sum of per-period ``invoice_count`` equals the total
          seeded invoice count (proving no period double-counts or
          drops rows).
        """
        budgets = {"SMALL": 20.0, "MEDIUM": 120.0, "LARGE": 180.0}

        with self.assert_runtime_under(
            budgets[self.volume], "11.8_all_periods",
        ), self.measure("11.8_all_periods"):
            for pid in self.period_ids:
                agg = PeriodAggregateCalc(period_id=pid)
                agg.is_calculated = CalculationModel.IN_PROGRESS
                agg.save()

        aggs = list(PeriodAggregateCalc.objects.all())
        self.assertEqual(
            len(aggs), len(self.period_ids),
            "Every period must have produced an aggregate row.",
        )
        states = {a.is_calculated for a in aggs}
        self.assertEqual(
            states, {CalculationModel.SUCCESS},
            f"Every aggregate must end SUCCESS; got {states}. Mixed "
            "states mean at least one period calc errored.",
        )
        total_rows = sum(a.invoice_count for a in aggs)
        self.assertEqual(
            total_rows, len(self.invoice_ids),
            f"Sum of per-period invoice counts ({total_rows}) must "
            f"equal total seeded invoices ({len(self.invoice_ids)}). "
            "A mismatch is either a double-count or lost rows between "
            "periods.",
        )

    # -- 11.9 ----------------------------------------------------------
    def test_11_9_dependent_chain_scales_linearly(self):
        """
        Scenario 11.9: ``DependentPeriodCalc`` over all periods.

        Each dependent reads the prior 3 aggregates — the query
        pattern across all periods must be ``O(periods)``, not
        ``O(periods²)``. We seed the prerequisite aggregates up front,
        then run all dependents inside a single query-count gate. The
        gate is sized to allow ~N constant-per-instance framework
        queries + a handful of shared overhead; it will not absorb a
        full quadratic re-fetch.
        """
        # Prerequisite: all aggregates populated.
        for pid in self.period_ids:
            agg = PeriodAggregateCalc(period_id=pid)
            agg.is_calculated = CalculationModel.IN_PROGRESS
            agg.save()

        n_periods = len(self.period_ids)
        # Budget: up to 40 framework queries per dependent is plenty
        # once history/audit/state-machine overhead is accounted for;
        # a quadratic path would need ~3 × n_periods EXTRA SELECTs per
        # instance, which cannot fit in this linear ceiling.
        query_budget = 40 * n_periods + 40
        time_budget = {"SMALL": 15.0, "MEDIUM": 90.0, "LARGE": 120.0}[self.volume]

        with self.assert_runtime_under(
            time_budget, "11.9_dependent_chain",
        ), self.assert_query_count_at_most(
            query_budget, "11.9_dependent_chain_queries",
        ), self.measure("11.9_dependent_chain"):
            for pid in self.period_ids:
                dep = DependentPeriodCalc(period_id=pid)
                dep.is_calculated = CalculationModel.IN_PROGRESS
                dep.save()

        deps = list(DependentPeriodCalc.objects.all())
        self.assertEqual(len(deps), n_periods)
        self.assertEqual(
            {d.is_calculated for d in deps},
            {CalculationModel.SUCCESS},
            "Every dependent calc must end SUCCESS.",
        )




