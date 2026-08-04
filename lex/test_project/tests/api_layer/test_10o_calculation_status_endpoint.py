"""Read-only calculation-status endpoint for the Streamlit widget.

Intent: a Streamlit dashboard polls this every couple of seconds to render a
calculation's live state. Polling the full record serialization to learn one
enum is wasteful on wide models and cannot carry the log tail, so the widget
gets a purpose-built endpoint. It must expose exactly the state the widget
renders and nothing the caller is not allowed to see -- a response that
confirms a record exists and errored, to someone who cannot read that record,
is a leak.

Cluster 10o — scenarios 10.72–10.79. Type: E.
Covers: lex/api/views/calculations/CalculationStatus.py.
Run: python -m lex pytest lex/test_project/tests/api_layer/test_10o_calculation_status_endpoint.py -v
"""

from __future__ import annotations

import pytest

from lex.core.models.CalculationModel import CalculationModel
from lex.test_project.tests._e2e_test_case import E2ETestCase

from .models import ALL_MODELS, ApiLayerCalc

pytestmark = pytest.mark.api_layer

CALC = "apilayercalc"


class TestCluster10o_CalculationStatusEndpoint(E2ETestCase):
    """Cluster 10o: the status contract the Streamlit widget polls."""

    e2e_models = ALL_MODELS

    def url_status(self, model_name: str, pk: int) -> str:
        return f"/api/model_entries/{model_name}/{pk}/calculation-status"

    def test_10_72_returns_status_for_a_never_calculated_record(self):
        """
        Scenario 10.72: a fresh record reports NOT_CALCULATED with no run data.
        Given: a calculation record that has never been run
        When: the widget polls its status
        Then: status is NOT_CALCULATED and the timing fields are null, so the
              widget can render "Never run" without guessing
        """
        item = ApiLayerCalc.objects.create(name="fresh")

        resp = self.client.get(self.url_status(CALC, item.pk))

        self.assertEqual(resp.status_code, 200, resp.content)
        body = resp.json()
        self.assertEqual(body["status"], CalculationModel.NOT_CALCULATED)
        self.assertIsNone(body["started_at"], "A record never run has no start time.")
        self.assertIsNone(body["finished_at"])
        self.assertIsNone(body["error"])
