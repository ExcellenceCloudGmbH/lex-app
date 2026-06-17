"""
Cluster 10n: active-calculation snapshot endpoint for release safety checks.

Intent
------
The Instance Controller needs a small, authenticated API it can call right
before a release to ask an instance "what calculations are still running?".
The endpoint must expose the same active-calculation snapshot the websocket
reconciliation path uses, so the release UI can warn operators before a pod
restart interrupts in-flight work.

Customer-visible contract:

* API-key callers can GET ``/api/active-calculations`` and receive the current
  running-calculation list.
* Stale store entries whose DB row is no longer ``IN_PROGRESS`` are pruned
  rather than shown as false positives.
* Anonymous callers are rejected by the same ``HasAPIKey | IsAuthenticated``
  gate used by the rest of the control-plane helper endpoints.

Cluster 10n — scenarios 10.67–10.69. Type: E.
Covers: ``lex/api/views/calculations/ActiveCalculations.py`` and the
``/api/active-calculations`` URL wiring in ``lex/lex_app/urls.py``.
Run: python -m lex pytest lex/test_project/tests/api_layer/test_10n_active_calculations_endpoint.py -v
"""

from __future__ import annotations

from rest_framework import status
from rest_framework.test import APIClient

from lex.core.models.CalculationModel import CalculationModel
from lex.core.signals.ActiveCalculationStateStore import ActiveCalculationStateStore
from lex.test_project.tests._e2e_test_case import E2ETestCase

from .models import ALL_MODELS, ApiLayerCalc

import pytest

pytestmark = pytest.mark.api_layer


def _record_id(instance: ApiLayerCalc) -> str:
    return f"{instance._meta.model_name}_{instance.pk}"


class TestCluster10n_ActiveCalculationsEndpoint(E2ETestCase):
    """Cluster 10n: release-safety snapshot over REST."""

    e2e_models = ALL_MODELS
    e2e_unpatch = {"mark_in_progress"}

    def setUp(self) -> None:
        super().setUp()
        ActiveCalculationStateStore.clear_all()

    def tearDown(self) -> None:
        ActiveCalculationStateStore.clear_all()
        super().tearDown()

    # -- 10.67 ---------------------------------------------------------
    def test_10_67_api_key_get_returns_running_calculation_snapshot(self) -> None:
        """
        Scenario 10.67: API-key GET returns the active calculation snapshot.
        Given: a calculation row at IN_PROGRESS that is registered in the store.
        When: the IC-style API-key caller GETs /api/active-calculations.
        Then: HTTP 200 and the payload lists that running calculation by record
              label, record_id, and calculation_id.
        """
        calc = ApiLayerCalc.objects.create(name="Quarterly refresh")
        calc.is_calculated = CalculationModel.IN_PROGRESS
        calc.save(skip_hooks=True)
        ActiveCalculationStateStore.mark_in_progress(
            record_id=_record_id(calc),
            calculation_id="calc-10n-running",
            record=str(calc),
            model_label=calc._meta.label_lower,
            record_pk=calc.pk,
        )

        self.authenticate_as_api_key(name="IC")
        response = self.client.get("/api/active-calculations")

        self.assertEqual(
            response.status_code,
            status.HTTP_200_OK,
            f"Expected 200 for an API-key caller, got {response.status_code}: {response.content!r}",
        )
        self.assertEqual(
            response.json(),
            {
                "active_calculations": [
                    {
                        "record_id": _record_id(calc),
                        "record": "Quarterly refresh",
                        "calculation_id": "calc-10n-running",
                    }
                ]
            },
            "Release-safety payload drifted; the IC warning modal would no longer know which calculation is running.",
        )

    # -- 10.68 ---------------------------------------------------------
    def test_10_68_terminal_rows_are_pruned_from_snapshot(self) -> None:
        """
        Scenario 10.68: stale store entries are filtered out.
        Given: a store entry for a row that already reached SUCCESS.
        When: the endpoint snapshots active calculations.
        Then: the response is empty and the stale entry is pruned instead of
              warning the operator about a calculation that is no longer running.
        """
        calc = ApiLayerCalc.objects.create(name="Finished refresh")
        calc.is_calculated = CalculationModel.SUCCESS
        calc.save(skip_hooks=True)
        ActiveCalculationStateStore.mark_in_progress(
            record_id=_record_id(calc),
            calculation_id="calc-10n-stale",
            record=str(calc),
            model_label=calc._meta.label_lower,
            record_pk=calc.pk,
        )

        self.authenticate_as_api_key(name="IC")
        response = self.client.get("/api/active-calculations")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            response.json(),
            {"active_calculations": []},
            "Terminal rows must not stay in the release-warning snapshot once the DB says they finished.",
        )

    # -- 10.69 ---------------------------------------------------------
    def test_10_69_anonymous_get_is_rejected(self) -> None:
        """
        Scenario 10.69: anonymous callers cannot read the snapshot.
        Given: a running calculation exists.
        When: an unauthenticated caller GETs /api/active-calculations.
        Then: the request is rejected with 401/403 before any calculation
              inventory is exposed.
        """
        calc = ApiLayerCalc.objects.create(name="Hidden refresh")
        calc.is_calculated = CalculationModel.IN_PROGRESS
        calc.save(skip_hooks=True)
        ActiveCalculationStateStore.mark_in_progress(
            record_id=_record_id(calc),
            calculation_id="calc-10n-auth",
            record=str(calc),
            model_label=calc._meta.label_lower,
            record_pk=calc.pk,
        )

        anonymous_client = APIClient()
        response = anonymous_client.get("/api/active-calculations")

        self.assertIn(
            response.status_code,
            (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN),
            f"Anonymous access must be rejected with 401/403, got {response.status_code}; otherwise release-state leaks publicly.",
        )
