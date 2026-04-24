"""
Cluster 10d: API-triggered calculation.

Intent (from docs/features/api-layer/ + docs/features/calculations/):

    PATCH ``is_calculated=IN_PROGRESS`` on a ``CalculationModel`` via
    the REST API kicks off the calculation. The API path commits
    IN_PROGRESS in its own transaction, then fires the hooks.

Scenario numbering matches
docs/test-plan/test-clusters.md#10-api-layer.
"""

from __future__ import annotations

import unittest

from rest_framework import status

from lex.core.models.CalculationModel import CalculationModel

from lex.tests.e2e._e2e_test_case import E2ETestCase

from .models import ALL_MODELS, API_ATOMIC, ApiAtomicCalc


class TestCluster10d_APICalcTrigger(E2ETestCase):
    """PATCH-triggered calculation over the REST API."""

    e2e_models = ALL_MODELS

    # -- 10.8 ----------------------------------------------------------
    # @unittest.expectedFailure  # BUG-009: is_calculated is editable=False so PATCH silently ignores it
    def test_10_8_patch_in_progress_triggers_calculation(self) -> None:
        """Scenario 10.8: PATCH is_calculated=IN_PROGRESS → calc runs."""
        calc = ApiAtomicCalc.objects.create(
            name="api10-8", should_fail=False,
            is_calculated=CalculationModel.NOT_CALCULATED,
        )
        resp = self.client.patch(
            self.url_detail(API_ATOMIC, calc.pk),
            data={"is_calculated": CalculationModel.IN_PROGRESS},
            format="json",
        )
        self.assertIn(
            resp.status_code, (status.HTTP_200_OK, status.HTTP_202_ACCEPTED),
            msg=f"PATCH trigger must return 200/202; got {resp.status_code}",
        )
        fresh = ApiAtomicCalc.objects.get(pk=calc.pk)
        self.assertIn(
            fresh.is_calculated,
            (CalculationModel.SUCCESS, CalculationModel.IN_PROGRESS),
            f"API-triggered calc must reach SUCCESS (or still be "
            f"IN_PROGRESS if async); got {fresh.is_calculated!r}",
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()


