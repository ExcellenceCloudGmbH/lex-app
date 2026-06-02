"""
Journey B — "Portfolio valuation with child orchestration"

A quant triggers a portfolio calculation that spawns a child
position calculation. The journey exercises, in order:

    Cluster 5 (History)          — transitions produce history rows
    Cluster 7 (Calc state machine) — NOT_CALCULATED → IN_PROGRESS → SUCCESS/ERROR
    Cluster 7c (Hierarchy)       — parent reflects child's outcome
    Cluster 9 (Signals, indirectly) — state store side effects stay
                                     contained via E2ETestCase patches

Each act is a ``subTest`` block so each check labels its own failure.

Why this test exists
--------------------
Calculations are the biggest moving part of the framework. The
per-cluster tests prove individual slices; this one proves a full
parent→child roundtrip (happy path and failure path and recovery)
survives every seam.
"""

from __future__ import annotations

import unittest

from lex.core.models.CalculationModel import CalculationModel
from lex.tests.e2e._e2e_test_case import E2ETestCase

from .models import ALL_MODELS, JourneyPortfolio, JourneyPosition

import pytest

pytestmark = pytest.mark.journeys


class TestJourneyB_PortfolioCalculation(E2ETestCase):
    """Parent/child calculation, happy path + failure + recovery."""

    e2e_models = ALL_MODELS

    def test_portfolio_calculation_journey(self) -> None:
        """
        Portfolio run: happy path, failing child, recovery.

        Happy path:
            quantity=10, price=5 → position market_value=50 → portfolio
            total_market_value=50. Both end SUCCESS.

        Failure path:
            child raises → both end ERROR, parent error_message populated.

        Recovery:
            clear the failure flag, re-trigger, both end SUCCESS again.
        """

        # -- Act 1: happy path, parent + child succeed --------------
        portfolio = JourneyPortfolio(
            name="portfolio-ok", symbol="ACME", quantity=10, price=5,
            child_should_fail=False,
        )
        portfolio.is_calculated = CalculationModel.IN_PROGRESS
        portfolio.save()
        portfolio.refresh_from_db()

        with self.subTest(act="1a-parent-success"):
            self.assertEqual(
                portfolio.is_calculated, CalculationModel.SUCCESS,
                f"Parent must end SUCCESS; got {portfolio.is_calculated!r}",
            )
            self.assertEqual(
                portfolio.total_market_value, 50,
                "Parent must have summed the child's market_value",
            )

        with self.subTest(act="1b-child-success"):
            position = JourneyPosition.objects.get(symbol="ACME")
            self.assertEqual(
                position.is_calculated, CalculationModel.SUCCESS,
                "Child position must end SUCCESS",
            )
            self.assertEqual(
                position.market_value, 50,
                "Child must have computed quantity * price = 50",
            )

        with self.subTest(act="1c-history-trail"):
            parent_states = [
                h.is_calculated
                for h in portfolio.history.order_by("history_id")
            ]
            self.assertIn(CalculationModel.IN_PROGRESS, parent_states)
            self.assertIn(CalculationModel.SUCCESS, parent_states)
            self.assertLess(
                parent_states.index(CalculationModel.IN_PROGRESS),
                parent_states.index(CalculationModel.SUCCESS),
                "IN_PROGRESS must precede SUCCESS in the parent's trail",
            )

        # -- Act 2: failure path, child raises ----------------------
        failing = JourneyPortfolio(
            name="portfolio-fail", symbol="BAD", quantity=1, price=1,
            child_should_fail=True,
        )
        failing.is_calculated = CalculationModel.IN_PROGRESS
        try:
            failing.save()
        except Exception:
            pass
        failing.refresh_from_db()

        with self.subTest(act="2a-parent-propagates-failure"):
            self.assertEqual(
                failing.is_calculated, CalculationModel.ERROR,
                f"Parent must end ERROR when child fails; "
                f"got {failing.is_calculated!r}",
            )

        with self.subTest(act="2b-child-ended-in-error"):
            bad_position = JourneyPosition.objects.get(symbol="BAD")
            self.assertEqual(
                bad_position.is_calculated, CalculationModel.ERROR,
                "Failing child must end ERROR",
            )
            self.assertTrue(
                bad_position.calculation_error_message,
                "Failing child must populate calculation_error_message",
            )

        # -- Act 3: recovery, re-trigger same parent ----------------
        # Flip the flag and retry — a real user would investigate and
        # fix the underlying cause before re-running.
        failing.child_should_fail = False
        failing.is_calculated = CalculationModel.IN_PROGRESS
        failing.save()
        failing.refresh_from_db()

        with self.subTest(act="3-recovery-succeeds"):
            self.assertEqual(
                failing.is_calculated, CalculationModel.SUCCESS,
                "After clearing the failure flag, re-trigger must "
                f"land at SUCCESS; got {failing.is_calculated!r}",
            )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()

