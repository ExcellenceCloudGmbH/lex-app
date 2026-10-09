"""A closed record is never calculated again, and keeps its status and results.

Intent: once a calculation's result has been used (posted to SAP, a period
closed), running it again would overwrite what was already relied on. A model
says a record is closed through ``calculation_closed_reason()``; from then on
no path starts its calculation. A record saved IN_PROGRESS in code keeps its
previous status, a calculation that starts other calculations skips the closed
ones and says so in its own log, and generated batch rows that are closed keep
their values. A regression here silently rewrites closed figures, or flips a
closed record's status as if it had been recalculated.
Cluster 7u — scenarios 7.228–7.237. Type: E.
Covers: lex/core/calculation_closing.py, lex/core/models/CalculationModel.py
(``save``), lex/core/mixins/CalculatedModelMixin.py (``calc_and_save_sync``,
``calc_and_save_streaming``), lex/lex_app/celery_tasks.py (``calc_and_save``),
lex/api/views/model_entries/One.py (``_calculate_after_create``).
Run: python -m lex pytest lex/test_project/tests/calculations/test_7u_closed_calculations.py -v
"""

from __future__ import annotations

import os
import time
from unittest.mock import patch

import pytest
from rest_framework import status

from lex.audit_logging.handlers.LexLogger import LexLogger
from lex.audit_logging.models.AuditLog import AuditLog
from lex.audit_logging.models.AuditLogStatus import AuditLogStatus
from lex.audit_logging.models.CalculationLog import CalculationLog
from lex.core.models.CalculationModel import CalculationModel
from lex.test_project.tests._e2e_test_case import E2ETestCase

from .models import (
    CLOSED_FROM_THE_START,
    CLOSED_MODELS,
    CLOSED_ON_CREATE,
    CLOSING_PARENT,
    SAP_POSTED,
    ClosableBatchRow,
    CalculatesOnceCalc,
    ClosableCalc,
    ClosedByTrueCalc,
    ClosedOnCreateCalc,
    ClosingParentCalc,
)

pytestmark = pytest.mark.calculations

_SETTLE_TIMEOUT_S = 10.0
_POLL_S = 0.05
# How long a test waits before concluding nothing started (see 7t).
_QUIET_WINDOW_S = 0.5


def _closed_record(**fields):
    """A ClosableCalc that already calculated once: SUCCESS, total 5.

    Written with ``update()`` so no calculation runs while setting it up.
    """
    record = ClosableCalc.objects.create(name=fields.pop("name", "posted"), **fields)
    ClosableCalc.objects.filter(pk=record.pk).update(is_calculated=CalculationModel.SUCCESS, total=5)
    record.refresh_from_db()
    return record


def _wait_for(test, record, wanted):
    """Poll ``record`` until its status is ``wanted``, or fail ``test``."""
    deadline = time.monotonic() + _SETTLE_TIMEOUT_S
    while time.monotonic() < deadline:
        record.refresh_from_db()
        if record.is_calculated == wanted:
            return
        time.sleep(_POLL_S)
    test.fail(
        f"{type(record).__name__} pk={record.pk} never reached {wanted!r}; "
        f"last is_calculated={record.is_calculated!r}."
    )


class TestCluster07u_ClosedCalculations(E2ETestCase):
    """Cluster 7u: ``calculation_closed_reason()`` stops every run, without touching status."""

    e2e_models = CLOSED_MODELS
    e2e_framework_models = [CalculationLog, AuditLog, AuditLogStatus]

    def setUp(self):
        super().setUp()
        ClosableCalc.calls = 0
        ClosedByTrueCalc.calls = 0
        ClosedOnCreateCalc.calls = 0
        CalculatesOnceCalc.calls = 0
        CalculationLog.objects.all().delete()

    def tearDown(self):
        LexLogger().content = []
        super().tearDown()

    # -- 7.228 ---------------------------------------------------------
    def test_7_228_a_closed_record_saved_in_progress_is_not_calculated(self):
        """
        Scenario 7.228: code cannot restart a closed record
        Given: a closed record that already calculated (SUCCESS, total 5)
        When: code sets it IN_PROGRESS and saves it, the way a script or
              another calculation starts a run
        Then: calculate() never runs, and the record keeps SUCCESS and total 5
        """
        record = _closed_record(closed=True)

        record.is_calculated = CalculationModel.IN_PROGRESS
        record.save()

        record.refresh_from_db()
        self.assertEqual(record.is_calculated, CalculationModel.SUCCESS, "A closed record's status must not change.")
        self.assertEqual(record.total, 5, "A closed record's results must not change.")
        self.assertEqual(ClosableCalc.calls, 0, "calculate() must not run for a closed record.")

    # -- 7.229 ---------------------------------------------------------
    def test_7_229_the_rest_of_the_save_still_happens(self):
        """
        Scenario 7.229: closing stops the calculation, not the save
        Given: a closed record
        When: code changes another field and sets IN_PROGRESS in the same save
        Then: the field change is saved, while the status stays SUCCESS
        """
        record = _closed_record(closed=True)

        record.note = "edited after posting"
        record.is_calculated = CalculationModel.IN_PROGRESS
        record.save()

        record.refresh_from_db()
        self.assertEqual(record.note, "edited after posting", "The rest of the save must still be written.")
        self.assertEqual(record.is_calculated, CalculationModel.SUCCESS, "The status must still be SUCCESS.")
        self.assertEqual(record.total, 5, "The results must not change: nothing was calculated.")
        self.assertEqual(ClosableCalc.calls, 0, "calculate() must not run.")

    # -- 7.230 ---------------------------------------------------------
    def test_7_230_an_open_record_calculates_as_before(self):
        """
        Scenario 7.230: nothing changes while the method answers None
        Given: a record whose ``calculation_closed_reason()`` answers None
        When: code sets it IN_PROGRESS and saves it
        Then: it calculates and settles in SUCCESS, as always
        """
        record = _closed_record(closed=False)

        record.is_calculated = CalculationModel.IN_PROGRESS
        record.save()

        record.refresh_from_db()
        self.assertEqual(record.is_calculated, CalculationModel.SUCCESS)
        self.assertEqual(record.total, 6, "The run's result must be saved.")
        self.assertEqual(ClosableCalc.calls, 1, "calculate() must run once.")

    # -- 7.231 ---------------------------------------------------------
    def test_7_231_a_calculation_skips_its_closed_child_and_says_so(self):
        """
        Scenario 7.231: calculations that start calculations
        Given: a parent whose calculate() starts a closed child's calculation
        When: the parent is calculated through the Calculate endpoint
        Then: the child is skipped and keeps SUCCESS and total 5, the parent
              still finishes in SUCCESS, and the parent's calculation log
              carries the child's reason
        """
        child = _closed_record(closed=True, name="posted child")
        parent = ClosingParentCalc.objects.create(name="parent", child_pk=child.pk)

        resp = self.client.patch(self.url_detail(CLOSING_PARENT, parent.pk), data={"calculate": "true"}, format="json")
        self.assertEqual(resp.status_code, status.HTTP_202_ACCEPTED, getattr(resp, "data", None))
        _wait_for(self, parent, CalculationModel.SUCCESS)

        child.refresh_from_db()
        self.assertEqual(child.is_calculated, CalculationModel.SUCCESS, "The closed child's status must not change.")
        self.assertEqual(child.total, 5, "The closed child's results must not change.")
        self.assertEqual(ClosableCalc.calls, 0, "The closed child must not be calculated.")
        logged = " ".join(CalculationLog.objects.values_list("calculation_log", flat=True))
        self.assertIn(SAP_POSTED, logged, "The parent's log must say why the child was skipped.")
        self.assertIn("posted child", logged, "The log must name the skipped record.")

    # -- 7.232 ---------------------------------------------------------
    def test_7_232_generated_rows_that_are_closed_keep_their_values(self):
        """
        Scenario 7.232: batch rows, through the default (streaming) path
        Given: two generated rows, one of them since posted (closed)
        When: the batch is generated again
        Then: the posted row keeps its values, the open one is recalculated
        """
        ClosableBatchRow.create()
        ClosableBatchRow.objects.filter(region="US").update(posted=True)

        ClosableBatchRow.create()

        runs = dict(ClosableBatchRow.objects.values_list("region", "runs"))
        self.assertEqual(runs["US"], 1, "The posted row must not be recalculated.")
        self.assertEqual(runs["EU"], 2, "The open row must be recalculated.")

    # -- 7.233 ---------------------------------------------------------
    def test_7_233_the_materialized_batch_path_skips_closed_rows_too(self):
        """
        Scenario 7.233: batch rows, through the legacy materialized path
        Given: the same two rows, one posted, with streaming switched off
        When: the batch is generated again
        Then: the posted row keeps its values, the open one is recalculated
        """
        with patch.dict(os.environ, {"LEX_SYNC_STREAMING_EXPANSION": "false"}):
            ClosableBatchRow.create()
            ClosableBatchRow.objects.filter(region="US").update(posted=True)
            ClosableBatchRow.create()

        runs = dict(ClosableBatchRow.objects.values_list("region", "runs"))
        self.assertEqual(runs["US"], 1, "The posted row must not be recalculated.")
        self.assertEqual(runs["EU"], 2, "The open row must be recalculated.")

    # -- 7.234 ---------------------------------------------------------
    def test_7_234_the_celery_worker_skips_closed_rows(self):
        """
        Scenario 7.234: batch rows, as a Celery worker receives them
        Given: one posted and one open generated row
        When: the worker's calc_and_save task runs on both
        Then: the posted row keeps its values, the open one is recalculated
        """
        from lex.lex_app.celery_tasks import calc_and_save

        ClosableBatchRow.create()
        ClosableBatchRow.objects.filter(region="US").update(posted=True)
        rows = list(ClosableBatchRow.objects.order_by("region"))

        calc_and_save(rows)

        runs = dict(ClosableBatchRow.objects.values_list("region", "runs"))
        self.assertEqual(runs["US"], 1, "The worker must not recalculate the posted row.")
        self.assertEqual(runs["EU"], 2, "The worker must recalculate the open row.")

    # -- 7.235 ---------------------------------------------------------
    def test_7_235_answering_true_closes_the_record(self):
        """
        Scenario 7.235: a yes without a reason still closes the record
        Given: a model whose ``calculation_closed_reason()`` answers True
        When: code sets a record IN_PROGRESS and saves it
        Then: nothing runs and the record stays NOT_CALCULATED
        """
        record = ClosedByTrueCalc.objects.create(name="closed")

        record.is_calculated = CalculationModel.IN_PROGRESS
        record.save()

        record.refresh_from_db()
        self.assertEqual(record.is_calculated, CalculationModel.NOT_CALCULATED)
        self.assertEqual(ClosedByTrueCalc.calls, 0, "calculate() must not run.")

    # -- 7.237 ---------------------------------------------------------
    def test_7_237_a_record_that_closes_once_calculated_runs_only_once(self):
        """
        Scenario 7.237: "already calculated" closes the record on every path
        Given: a model whose ``calculation_closed_reason()`` answers a reason
               once ``is_calculated`` is SUCCESS
        When: code sets a new record IN_PROGRESS and saves it, twice, the way
              a calculation that starts other calculations would
        Then: the first save calculates; the second is skipped, because the
              method sees the status the record had before the save (SUCCESS),
              as the Calculate button does, not the IN_PROGRESS being asked for
        """
        record = CalculatesOnceCalc.objects.create(name="once")

        record.is_calculated = CalculationModel.IN_PROGRESS
        record.save()
        record.refresh_from_db()
        self.assertEqual(record.is_calculated, CalculationModel.SUCCESS, "The first run must calculate.")

        record.is_calculated = CalculationModel.IN_PROGRESS
        record.save()

        record.refresh_from_db()
        self.assertEqual(record.is_calculated, CalculationModel.SUCCESS, "The status must not change.")
        self.assertEqual(record.total, 1, "The second run must not recalculate.")
        self.assertEqual(CalculatesOnceCalc.calls, 1, "calculate() must run exactly once.")


class TestCluster07u_CreatedClosed(E2ETestCase):
    """Cluster 7u: ``calculate_on_create`` asks ``calculation_closed_reason()`` first."""

    e2e_models = CLOSED_MODELS
    e2e_framework_models = [CalculationLog, AuditLog, AuditLogStatus]
    # Spied below: the three ways a started run makes itself known.
    e2e_unpatch = {"mark_in_progress", "send_calculation_update", "build_cache_key"}

    def setUp(self):
        super().setUp()
        ClosedOnCreateCalc.calls = 0

    # -- 7.236 ---------------------------------------------------------
    def test_7_236_a_record_created_closed_starts_nothing(self):
        """
        Scenario 7.236: closed wins over ``calculate_on_create``, with no side effects
        Given: a ``calculate_on_create`` model whose ``calculation_closed_reason()``
               answers a reason for one new record and None for another
        When: both are created through the REST API
        Then: the closed one answers 201 NOT_CALCULATED, with its reason and
              without Calculate in its edit scopes, and stays that way: no run
              is registered, announced or cached for it, its trail holds only
              its create entry, and its history only its creation. The open
              one calculates, as before.
        """
        registered = self.spy_on("mark_in_progress")
        announced = self.spy_on("send_calculation_update")
        cached = self.spy_on("build_cache_key")

        closed = self.client.post(
            self.url_create(CLOSED_ON_CREATE), data={"name": "c", "closed": True}, format="json",
        )
        opened = self.client.post(
            self.url_create(CLOSED_ON_CREATE), data={"name": "o", "closed": False}, format="json",
        )
        self.assertEqual(closed.status_code, status.HTTP_201_CREATED, getattr(closed, "data", None))
        self.assertEqual(opened.status_code, status.HTTP_201_CREATED, getattr(opened, "data", None))
        self.assertEqual(
            closed.data.get("is_calculated"), CalculationModel.NOT_CALCULATED,
            "A record created closed must not start its calculation.",
        )
        self.assertEqual(
            closed.data.get("lex_reserved_calculation_closed_reason"), CLOSED_FROM_THE_START,
            "The answer must carry the reason.",
        )
        self.assertNotIn(
            "is_calculated", closed.data["lex_reserved_scopes"]["edit"],
            "A record created closed must not offer Calculate.",
        )

        # The open record's run is the yardstick: once it has finished, a run
        # for the closed one has had every chance to start.
        _wait_for(self, ClosedOnCreateCalc.objects.get(pk=opened.data["id"]), CalculationModel.SUCCESS)
        time.sleep(_QUIET_WINDOW_S)

        pk = closed.data["id"]
        closed_run = f"'{CLOSED_ON_CREATE}_{pk}'"
        open_run = f"'{CLOSED_ON_CREATE}_{opened.data['id']}'"
        self.assertEqual(ClosedOnCreateCalc.objects.get(pk=pk).is_calculated, CalculationModel.NOT_CALCULATED)
        self.assertEqual(ClosedOnCreateCalc.calls, 1, "Only the open record may have calculated.")
        self.assertTrue(
            [c for c in registered.call_args_list if open_run in repr(c)],
            "The open record's run must be registered, or the checks below prove nothing.",
        )
        for spy, what in ((registered, "registered"), (announced, "announced"), (cached, "cached")):
            self.assertFalse(
                [c for c in spy.call_args_list if closed_run in repr(c)],
                f"No run may be {what} for the closed record; got {spy.call_args_list!r}.",
            )
        self.assertEqual(
            list(AuditLog.objects.filter(resource=CLOSED_ON_CREATE, object_id=pk).values_list("action", flat=True)),
            ["create"],
            "The closed record's trail must hold its create entry only.",
        )
        self.assertEqual(
            list(ClosedOnCreateCalc.history.filter(id=pk).values_list("is_calculated", flat=True)),
            [CalculationModel.NOT_CALCULATED],
            "The closed record's history must hold its creation only.",
        )
