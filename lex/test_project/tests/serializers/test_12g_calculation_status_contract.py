"""
Cluster 12g: Calculation status JSON contract.

Intent (from docs/features/calculations/ + the issue report):

    The frontend consumes ``is_calculated`` through the REST API. The
    contract is the framework's canonical enum strings
    (``NOT_CALCULATED``, ``IN_PROGRESS``, ``SUCCESS``, ``ERROR``,
    ``ABORTED``) — never ad-hoc labels like ``"No"``. The serializer
    layer is the last line of defense before that value reaches the UI.
"""

from __future__ import annotations

import unittest

from lex.core.models.CalculationModel import CalculationModel, CalculationModelException
from lex.tests.e2e._e2e_test_case import E2ETestCase
from rest_framework import status

from .models import ALL_MODELS, CALC_STATUS, CalculationStatusItem

CANONICAL_STATUSES = {
    CalculationModel.NOT_CALCULATED,
    CalculationModel.IN_PROGRESS,
    CalculationModel.SUCCESS,
    CalculationModel.ERROR,
    CalculationModel.ABORTED,
}


class TestCluster12g_CalculationStatusContract(E2ETestCase):
    """Pin the API payload the frontend receives for ``is_calculated``."""

    e2e_models = ALL_MODELS

    @staticmethod
    def _assert_canonical_status(testcase, actual: str, *, expected: str, context: str) -> None:
        testcase.assertEqual(
            actual,
            expected,
            f"{context} must expose the canonical calculation status string "
            f"{expected!r}; got {actual!r}",
        )
        testcase.assertIn(
            actual,
            CANONICAL_STATUSES,
            f"{context} leaked a non-canonical calculation status {actual!r}. "
            f"Allowed values: {sorted(CANONICAL_STATUSES)}",
        )
        testcase.assertNotEqual(
            actual,
            "No",
            f'{context} must never send the display label "No" to the frontend',
        )

    # -- 12.36 ---------------------------------------------------------
    def test_12_36_list_exposes_only_canonical_calculation_status_strings(self) -> None:
        """Scenario 12.36: list rows keep the calculation enum strings."""
        untouched = CalculationStatusItem.objects.create(name="untouched")

        successful = CalculationStatusItem.objects.create(name="successful")
        successful.is_calculated = CalculationModel.IN_PROGRESS
        successful.save()

        failing = CalculationStatusItem.objects.create(name="failing", should_fail=True)
        failing.is_calculated = CalculationModel.IN_PROGRESS
        with self.assertRaises(CalculationModelException):
            failing.save()
        failing.refresh_from_db()

        response = self.list_get(CALC_STATUS)
        self.assertEqual(
            response.status_code,
            status.HTTP_200_OK,
            f"Calculation-status list must succeed; got {response.status_code}: "
            f"{getattr(response, 'data', response.content)!r}",
        )

        rows = self.extract_results(response.data)
        statuses_by_id = {row["id"]: row["is_calculated"] for row in rows}

        self._assert_canonical_status(
            self,
            statuses_by_id[untouched.pk],
            expected=CalculationModel.NOT_CALCULATED,
            context="List row for an untouched calculation",
        )
        self._assert_canonical_status(
            self,
            statuses_by_id[successful.pk],
            expected=CalculationModel.SUCCESS,
            context="List row for a successful calculation",
        )
        self._assert_canonical_status(
            self,
            statuses_by_id[failing.pk],
            expected=CalculationModel.ERROR,
            context="List row for a failed calculation",
        )

    # -- 12.37 ---------------------------------------------------------
    def test_12_37_patch_response_resets_to_not_calculated_string(self) -> None:
        """Scenario 12.37: editing a calculated row returns ``NOT_CALCULATED``."""
        item = CalculationStatusItem.objects.create(name="patch-me")
        item.is_calculated = CalculationModel.IN_PROGRESS
        item.save()
        item.refresh_from_db()

        self.assertEqual(
            item.is_calculated,
            CalculationModel.SUCCESS,
            "Fixture setup failed: the calculated row must start in SUCCESS before PATCH",
        )

        response = self.client.patch(
            self.url_detail(CALC_STATUS, item.pk),
            data={"name": "patched"},
            format="json",
        )
        self.assertEqual(
            response.status_code,
            status.HTTP_200_OK,
            f"PATCH must succeed; got {response.status_code}: "
            f"{getattr(response, 'data', response.content)!r}",
        )

        self._assert_canonical_status(
            self,
            response.data["is_calculated"],
            expected=CalculationModel.NOT_CALCULATED,
            context="PATCH response for a calculated row",
        )

        item.refresh_from_db()
        self._assert_canonical_status(
            self,
            item.is_calculated,
            expected=CalculationModel.NOT_CALCULATED,
            context="Persisted row after PATCH",
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
