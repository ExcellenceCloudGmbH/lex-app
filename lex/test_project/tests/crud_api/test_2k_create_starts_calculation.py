"""Creating a record whose model calculates on create: what the POST answers.

Intent: a create that also starts the record's calculation must answer as soon
as the record exists, not when the run ends. The form closes, the grid shows
the row with its status pill at IN_PROGRESS, and the run's outcome arrives the
way a Calculate click's does. A regression here puts the create form back to
waiting for the whole calculation, which is the problem the feature removes.
Cluster 2k — scenarios 2.108–2.109. Type: E.
Covers: lex/api/views/model_entries/One.py (``OneModelEntry.create``).
Run: python -m lex pytest lex/test_project/tests/crud_api/test_2k_create_starts_calculation.py -v
"""

from __future__ import annotations

import threading
import time

import pytest
from rest_framework import status

from lex.core.models.CalculationModel import CalculationModel
from lex.test_project.tests._e2e_test_case import E2ETestCase

from ..calculations.models import (
    ON_CREATE,
    ON_CREATE_MODELS,
    ON_CREATE_OFF,
    OnCreateCalc,
    OnCreateOffCalc,
)

pytestmark = pytest.mark.crud_api

_SETTLE_TIMEOUT_S = 10.0
_POLL_S = 0.05


class TestCluster02k_CreateStartsCalculation(E2ETestCase):
    """Cluster 2k: the POST contract when a create starts a calculation."""

    e2e_models = ON_CREATE_MODELS

    def setUp(self):
        super().setUp()
        OnCreateCalc.gate = None
        OnCreateCalc.calls = 0

    def tearDown(self):
        if OnCreateCalc.gate is not None:
            OnCreateCalc.gate.set()
        OnCreateCalc.gate = None
        super().tearDown()

    # -- 2.108 ---------------------------------------------------------
    def test_2_108_the_create_answers_before_the_run_ends(self):
        """
        Scenario 2.108: the POST does not wait for the calculation
        Given: a model with ``calculate_on_create = True`` whose calculate()
               is held open until the test lets it finish
        When: a record is created through POST
        Then: the POST answers 201 with ``is_calculated`` IN_PROGRESS while the
              run is still going, and the run finishes in SUCCESS afterwards
        """
        OnCreateCalc.gate = threading.Event()

        resp = self.client.post(self.url_create(ON_CREATE), data={"name": "held"}, format="json")

        self.assertEqual(
            resp.status_code,
            status.HTTP_201_CREATED,
            f"The create must answer 201; got {resp.status_code} body={getattr(resp, 'data', None)!r}",
        )
        self.assertEqual(
            resp.data.get("is_calculated"),
            CalculationModel.IN_PROGRESS,
            "The create's answer must already show the run as started.",
        )
        record = OnCreateCalc.objects.get(pk=resp.data["id"])
        self.assertEqual(
            record.is_calculated,
            CalculationModel.IN_PROGRESS,
            "The run is still held open, so the record must still be IN_PROGRESS: "
            "the create answered without waiting for it.",
        )

        OnCreateCalc.gate.set()
        deadline = time.monotonic() + _SETTLE_TIMEOUT_S
        while time.monotonic() < deadline:
            record.refresh_from_db()
            if record.is_calculated != CalculationModel.IN_PROGRESS:
                break
            time.sleep(_POLL_S)
        self.assertEqual(
            record.is_calculated,
            CalculationModel.SUCCESS,
            "Once released, the run must finish in SUCCESS.",
        )

    # -- 2.109 ---------------------------------------------------------
    def test_2_109_a_model_without_the_flag_answers_as_before(self):
        """
        Scenario 2.109: nothing changes for models that do not opt in
        Given: a calculation model without ``calculate_on_create``
        When: a record is created through POST
        Then: the POST answers 201 with ``is_calculated`` NOT_CALCULATED, as it
              always has
        """
        resp = self.client.post(self.url_create(ON_CREATE_OFF), data={"name": "plain"}, format="json")

        self.assertEqual(resp.status_code, status.HTTP_201_CREATED, getattr(resp, "data", None))
        self.assertEqual(
            resp.data.get("is_calculated"),
            CalculationModel.NOT_CALCULATED,
            "A model that does not opt in must be created NOT_CALCULATED.",
        )
        self.assertEqual(OnCreateOffCalc.calls, 0, "No calculation may run for it.")
