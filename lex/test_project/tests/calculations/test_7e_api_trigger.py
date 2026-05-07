"""
Cluster 7e: API-triggered calculation.

Intent (from docs/features/calculations/):

    The REST API lets a customer trigger a calculation by PATCHing
    ``is_calculated=IN_PROGRESS`` on a record. The API path commits
    IN_PROGRESS in its own transaction, then runs the calculation
    hooks, landing the record at SUCCESS / ERROR.

Scenario numbering matches
docs/test-plan/test-clusters.md#7-calculation-state-machine.
"""

from __future__ import annotations

import unittest

from rest_framework import status

from lex.core.models.CalculationModel import CalculationModel

from lex.tests.e2e._e2e_test_case import E2ETestCase

from .models import ALL_MODELS, ATOMIC, AtomicCalc


class TestCluster07e_APITrigger(E2ETestCase):
    """API-triggered calculation (PATCH ``is_calculated=IN_PROGRESS``)."""

    e2e_models = ALL_MODELS

    # -- 7.14 ----------------------------------------------------------
    # def test_7_14_api_trigger_reaches_terminal_state(self) -> None:
    #     """
    #     Scenario 7.14: PATCH is_calculated=IN_PROGRESS → final state
    #     SUCCESS for a non-failing calc.
    #     """
    #     calc = AtomicCalc.objects.create(
    #         name="api7-14", should_fail=False,
    #         is_calculated=CalculationModel.NOT_CALCULATED,
    #     )
    #     resp = self.client.patch(
    #         self.url_detail(ATOMIC, calc.pk),
    #         data={"is_calculated": CalculationModel.IN_PROGRESS},
    #         format="json",
    #     )
    #
    #     self.assertIn(
    #         resp.status_code, (status.HTTP_200_OK, status.HTTP_202_ACCEPTED),
    #         msg=f"API trigger must return 200/202; got {resp.status_code}: "
    #             f"{getattr(resp, 'data', resp.content)!r}",
    #     )
    #     fresh = AtomicCalc.objects.get(pk=calc.pk)
    #     self.assertIn(
    #         fresh.is_calculated,
    #         (CalculationModel.SUCCESS, CalculationModel.IN_PROGRESS),
    #         f"API-triggered calc must reach SUCCESS (or still be "
    #         f"IN_PROGRESS synchronously pending); got {fresh.is_calculated!r}",
    #     )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()


