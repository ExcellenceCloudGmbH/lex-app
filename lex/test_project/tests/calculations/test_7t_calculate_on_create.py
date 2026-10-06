"""A record created through the app calculates by itself when its model asks to.

Intent: the ability to trigger a record's calculation, in the background, right
after the record is created, acting the same as a calculation started from the
Calculate button. A model opts in with ``calculate_on_create = True``. A create
through the form or the API then starts the run the way a Calculate click does:
off the request thread, through Celery when it is on. Records created in code
(scripts, initial data, uploads, other calculations) are left alone, because a
parent calculation waits on its children synchronously and a background run
would break that. A regression here either blocks the create form for the whole
run again, or starts runs nobody asked for.
Cluster 7t — scenarios 7.222–7.227. Type: E.
Covers: lex/core/models/CalculationModel.py (``calculate_on_create``),
lex/api/views/model_entries/One.py (``OneModelEntry.create``).
Run: python -m lex pytest lex/test_project/tests/calculations/test_7t_calculate_on_create.py -v
"""

from __future__ import annotations

import threading
import time
from unittest.mock import Mock, patch

import pytest
from rest_framework import status

from lex.core.models.CalculationModel import CalculationModel
from lex.test_project.tests._e2e_test_case import E2ETestCase

from .models import (
    ON_CREATE,
    ON_CREATE_CONDITIONAL,
    ON_CREATE_MODELS,
    ON_CREATE_OFF,
    OnCreateCalc,
    OnCreateConditionalCalc,
    OnCreateOffCalc,
)

pytestmark = pytest.mark.calculations

_SETTLE_TIMEOUT_S = 10.0
_POLL_S = 0.05
# How long a test waits before concluding that nothing started. A run, when
# one starts, is submitted before the create answers, so half a second is
# far longer than it needs to begin.
_QUIET_WINDOW_S = 0.5


class TestCluster07t_CalculateOnCreate(E2ETestCase):
    """Cluster 7t: ``calculate_on_create`` starts a run when the app creates a record."""

    e2e_models = ON_CREATE_MODELS

    def setUp(self):
        super().setUp()
        OnCreateCalc.gate = None
        OnCreateCalc.calls = 0
        OnCreateOffCalc.calls = 0

    def tearDown(self):
        # Never leave a run parked on the gate past its test.
        if OnCreateCalc.gate is not None:
            OnCreateCalc.gate.set()
        OnCreateCalc.gate = None
        super().tearDown()

    def _create(self, model_name, **data):
        resp = self.client.post(self.url_create(model_name), data=data, format="json")
        self.assertEqual(
            resp.status_code,
            status.HTTP_201_CREATED,
            f"Creating a {model_name} through the API must answer 201; "
            f"got {resp.status_code} body={getattr(resp, 'data', None)!r}",
        )
        return resp

    def _wait_until_settled(self, record) -> str:
        """Return the state the record lands in once its run leaves IN_PROGRESS."""
        deadline = time.monotonic() + _SETTLE_TIMEOUT_S
        while time.monotonic() < deadline:
            record.refresh_from_db()
            if record.is_calculated not in (
                CalculationModel.IN_PROGRESS,
                CalculationModel.NOT_CALCULATED,
            ):
                return record.is_calculated
            time.sleep(_POLL_S)
        self.fail(
            f"{type(record).__name__} pk={record.pk} never finished a run within "
            f"{_SETTLE_TIMEOUT_S:.0f}s; last is_calculated={record.is_calculated!r}."
        )

    # -- 7.222 ---------------------------------------------------------
    def test_7_222_a_record_created_through_the_api_calculates_by_itself(self):
        """
        Scenario 7.222: the run starts without anyone pressing Calculate
        Given: a model with ``calculate_on_create = True``
        When: a record is created through the REST API
        Then: its calculation runs once and the record settles in SUCCESS, with
              what calculate() computed saved on it
        """
        resp = self._create(ON_CREATE, name="first")
        record = OnCreateCalc.objects.get(pk=resp.data["id"])

        self.assertEqual(
            self._wait_until_settled(record),
            CalculationModel.SUCCESS,
            "A clean run started by the create must settle in SUCCESS.",
        )
        self.assertEqual(record.total, 42, "The run's result must be saved on the record.")
        self.assertEqual(OnCreateCalc.calls, 1, "Creating the record must run its calculation exactly once.")

    # -- 7.223 ---------------------------------------------------------
    def test_7_223_a_failing_run_leaves_the_record_created_and_in_error(self):
        """
        Scenario 7.223: a run that fails does not undo the create
        Given: a model with ``calculate_on_create = True`` whose calculate() raises
        When: a record is created through the REST API
        Then: the record exists and its run settles in ERROR, as a failed
              Calculate click would leave it
        """
        resp = self._create(ON_CREATE, name="broken", should_fail=True)
        record = OnCreateCalc.objects.get(pk=resp.data["id"])

        self.assertEqual(
            self._wait_until_settled(record),
            CalculationModel.ERROR,
            "A run that raises must settle in ERROR.",
        )
        self.assertTrue(
            OnCreateCalc.objects.filter(pk=record.pk).exists(),
            "The record must survive its failed run: the create already succeeded.",
        )

    # -- 7.224 ---------------------------------------------------------
    def test_7_224_without_the_flag_creating_starts_nothing(self):
        """
        Scenario 7.224: the behaviour is opt-in
        Given: a calculation model that does not set ``calculate_on_create``
        When: a record is created through the REST API
        Then: no run starts; the record stays NOT_CALCULATED until someone asks
        """
        resp = self._create(ON_CREATE_OFF, name="quiet")
        time.sleep(_QUIET_WINDOW_S)

        record = OnCreateOffCalc.objects.get(pk=resp.data["id"])
        self.assertEqual(
            record.is_calculated,
            CalculationModel.NOT_CALCULATED,
            "Without the flag, a create must not start a calculation.",
        )
        self.assertEqual(OnCreateOffCalc.calls, 0, "calculate() must not have run.")

    # -- 7.225 ---------------------------------------------------------
    def test_7_225_a_record_created_in_code_does_not_calculate(self):
        """
        Scenario 7.225: only creates made through the app start a run
        Given: a model with ``calculate_on_create = True``
        When: a record is created in code, the way scripts, initial data and
              other calculations create records
        Then: no run starts, so code that creates records keeps deciding for
              itself when they calculate
        """
        record = OnCreateCalc.objects.create(name="from code")
        time.sleep(_QUIET_WINDOW_S)

        record.refresh_from_db()
        self.assertEqual(
            record.is_calculated,
            CalculationModel.NOT_CALCULATED,
            "A record created in code must not start a calculation.",
        )
        self.assertEqual(OnCreateCalc.calls, 0, "calculate() must not have run.")

    # -- 7.226 ---------------------------------------------------------
    def test_7_226_a_property_lets_each_record_decide(self):
        """
        Scenario 7.226: ``calculate_on_create`` can be a property
        Given: a model whose ``calculate_on_create`` is a property reading a field
        When: one record is created with the field on and one with it off
        Then: only the first calculates
        """
        on = self._create(ON_CREATE_CONDITIONAL, name="on", auto=True)
        off = self._create(ON_CREATE_CONDITIONAL, name="off", auto=False)

        on_record = OnCreateConditionalCalc.objects.get(pk=on.data["id"])
        self.assertEqual(
            self._wait_until_settled(on_record),
            CalculationModel.SUCCESS,
            "The record whose property says yes must calculate.",
        )
        self.assertEqual(on_record.total, 7, "Its run's result must be saved.")

        time.sleep(_QUIET_WINDOW_S)
        off_record = OnCreateConditionalCalc.objects.get(pk=off.data["id"])
        self.assertEqual(
            off_record.is_calculated,
            CalculationModel.NOT_CALCULATED,
            "The record whose property says no must not calculate.",
        )

    # -- 7.227 ---------------------------------------------------------
    def test_7_227_with_celery_on_the_run_is_dispatched_from_the_background(self):
        """
        Scenario 7.227: the Celery route, as a Calculate click takes it
        Given: a model with ``calculate_on_create = True`` and Celery in use
        When: a record is created through the REST API
        Then: the create answers with IN_PROGRESS, and the run is handed to a
              Celery worker from the calculation thread pool, never run inline
              on the request thread
        """
        dispatched = threading.Event()
        dispatch_threads = []

        def _dispatch():
            dispatch_threads.append(threading.current_thread().name)
            dispatched.set()
            return Mock(id="task-7-227")

        with patch.object(OnCreateCalc, "should_use_celery", return_value=True), patch.object(
            OnCreateCalc, "dispatch_calculation_task", side_effect=_dispatch
        ) as dispatch_mock, patch.object(
            OnCreateCalc, "_run_in_calculation_executor"
        ) as inline_mock:
            resp = self._create(ON_CREATE, name="via celery")
            self.assertTrue(
                dispatched.wait(_SETTLE_TIMEOUT_S),
                "The run started by the create must be dispatched to Celery.",
            )

        self.assertEqual(resp.data["is_calculated"], CalculationModel.IN_PROGRESS)
        dispatch_mock.assert_called_once_with()
        inline_mock.assert_not_called()
        self.assertTrue(
            dispatch_threads[0].startswith("lex-calc"),
            f"The dispatch must happen on the calculation thread pool, like a "
            f"Calculate click's; it ran on {dispatch_threads[0]!r}.",
        )
