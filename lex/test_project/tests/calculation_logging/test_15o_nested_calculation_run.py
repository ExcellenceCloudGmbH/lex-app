"""A CalculationModel saved inside another calculation runs in a frame of its own.

Intent: docs/calculations/logging.md (Nested Calculations) promises that a child
calculation's log nests under the calculation that started it; the docs show a
caller wrapping the child's save in ``model_logging_context(child)`` to get it.
``calculate_hook`` now gives a CalculationModel it runs synchronously that frame
itself, through ``CalculationModel.child_calculation_context``, and keeps it for
the whole run: calculate(), the terminal bookkeeping in
``execute_calculation_sync`` and the failure handling in ``calculate_hook``. The
last two decide from the model context stack whether the run is the root, and
only the root may end the run's live log (cluster 9c: only the root purges the
cache) or write it back when the run fails (cluster 15j). Without the frame, a
nested model was taken for the root.

15f pins the tree for nested saves. 15o pins what else the frame decides, and
the routes the run can take:

- 15.65: a nested model that finishes leaves the run's live log alone;
- 15.66: a run that fails in a nested model keeps its log;
- 15.67: a nested failure the caller handles leaves the tree with one root;
- 15.68, 15.69: a root saved with nothing on the stack gets a node of its own,
  and keeps its log when it fails;
- 15.70: the API route, where the run happens on the calculation thread pool,
  in a copy of the request's context, on a stack that already holds the root;
- 15.71: the Celery route, whose generic ``calc_and_save`` task already gave a
  nested model its own frame, gives the same tree.

Cluster 15o — scenarios 15.65–15.71. Type: I (15.70: E).
Covers: lex/core/models/CalculationModel.py (calculate_hook,
child_calculation_context, execute_calculation_sync),
lex/api/views/model_entries/One.py (_calculate_in_background),
lex/lex_app/celery_tasks.py (calc_and_save).
Run: python -m lex pytest lex/test_project/tests/calculation_logging/test_15o_nested_calculation_run.py -v
"""
from __future__ import annotations

import time
from contextlib import contextmanager
from unittest.mock import patch
from uuid import uuid4

import pytest
from rest_framework import status

from lex.audit_logging.models.CalculationLog import CalculationLog
from lex.audit_logging.utils.CacheManager import CacheManager
from lex.audit_logging.utils.ModelContext import _model_context
from lex.core.models.CalculationModel import CalculationModelException
from lex.test_project.tests.celery_async.test_8h_eager_end_to_end import _celery_eager

from . import _CalcLogTestCase
from .models import (
    ALL_MODELS,
    NESTED_RUN_MODELS,
    LogInnerSavedCalc,
    LogRootCalc,
)

pytestmark = pytest.mark.calculation_logging

_SETTLE_TIMEOUT_S = 10.0
_POLL_S = 0.05


@contextmanager
def _live_log_purged_like_redis():
    """Let ``cleanup_calculation`` purge a run's live log the way it does on Redis.

    In production the live log is on Redis, where ``cleanup_calculation`` finds
    every key of a run by pattern (``*_<calculation_id>``) and deletes it. The
    test cache cannot match patterns, so a purge there deletes nothing and a
    purge at the wrong moment goes unnoticed. This answers the lookup with the
    keys the run's log was written under.
    """
    built = []
    build = CacheManager.build_cache_key

    def build_and_note(record, calculation_id):
        key = build(record, calculation_id)
        built.append(key)
        return key

    def find(cache, calculation_id):
        return sorted({key for key in built if key.endswith(f"_{calculation_id}")})

    with patch.object(CacheManager, "build_cache_key", side_effect=build_and_note), patch.object(
        CacheManager, "_find_calculation_keys", side_effect=find
    ):
        yield


class TestCluster15o_NestedCalculationRun(_CalcLogTestCase):
    """Cluster 15o: a nested CalculationModel's synchronous run is not taken for the root."""

    e2e_models = ALL_MODELS + NESTED_RUN_MODELS
    # The harness stubs the live cache (every key is "test_key" and nothing is
    # stored); these scenarios are about what the real cache holds.
    e2e_unpatch = {"ensure_terminal_calculation_audit", "store_message", "build_cache_key"}

    # -- 15.65 ---------------------------------------------------------
    def test_15_65_a_nested_model_that_finishes_leaves_the_runs_live_log_alone(self):
        """
        Scenario 15.65: the run's live log outlasts a nested CalculationModel
        Given: a root whose calculate() logs a line, saves a nested
               CalculationModel that logs one of its own, and then reads the
               run's live log the way the log panel's backfill does; the live
               log is purged as on Redis
        When:  the root is saved
        Then:  the live log read after the nested model finished still holds
               both lines: only the root ends the run's live log, when the
               whole run ends (cluster 9c)
        """
        with _live_log_purged_like_redis():
            root = self._save_root(child_mode="calcmodel_then_read_live_log", units_csv="x")

        root.refresh_from_db()
        self.assertEqual(root.is_calculated, LogRootCalc.SUCCESS, "Sanity: the run must succeed.")
        live_log = root.live_log_after_inner or ""
        self.assertIn(
            "root root-calcmodel_then_read_live_log", live_log,
            "Someone opening the log panel after the nested model finished must "
            f"still see what the root logged before it; the live log was {live_log!r}.",
        )
        self.assertIn(
            "inner x", live_log,
            f"The nested model's line belongs to the run's live log too; it was {live_log!r}.",
        )

    # -- 15.66 ---------------------------------------------------------
    def test_15_66_a_run_that_fails_in_a_nested_model_keeps_its_log(self):
        """
        Scenario 15.66: a failure inside a nested CalculationModel costs the run nothing it logged
        Given: an atomic root that logs a line and saves a nested
               CalculationModel, which logs and then fails; the root does not
               handle the failure; the live log is purged as on Redis
        When:  the root is saved
        Then:  the run fails and its rows are rolled back, and it keeps its log
               as 15j promises: one row on the root, with no parent, holding
               the root's line and the nested model's
        """
        with _live_log_purged_like_redis():
            with self.assertRaises(CalculationModelException):
                self._save_root(child_mode="failing_calcmodel", units_csv="x")

        root = LogRootCalc.objects.get(name="root-failing_calcmodel")
        self.assertEqual(root.is_calculated, LogRootCalc.ERROR, "Sanity: the run must fail.")
        row = self.assert_log_row(root, parent=None, contains="root root-failing_calcmodel")
        self.assertIn(
            "failing inner x", row.calculation_log,
            "The kept log is what the live log showed, the nested model's line included.",
        )
        self.assert_total_rows(1)

    # -- 15.67 ---------------------------------------------------------
    def test_15_67_a_nested_failure_the_caller_handles_leaves_one_root(self):
        """
        Scenario 15.67: a handled failure in a nested CalculationModel does not split the tree
        Given: an atomic root that logs a line, saves a nested CalculationModel
               which logs and then fails, handles that failure and logs again
        When:  the root is saved
        Then:  the run succeeds, and the tree has one root node, the root's,
               holding both of its lines; no other node repeats them. The
               nested model is not taken for the root of a failed run, so it
               does not write the run's live log out as a node of its own.
        """
        root = self._save_root(child_mode="failing_calcmodel_caught", units_csv="x")

        root.refresh_from_db()
        self.assertEqual(root.is_calculated, LogRootCalc.SUCCESS, "Sanity: the root must recover.")
        root_row = self.assert_log_row(
            root, parent=None, contains="root root-failing_calcmodel_caught",
        )
        self.assertIn("root recovered", root_row.calculation_log)
        parentless = list(
            CalculationLog.objects.filter(
                calculationId=self.calc_id, parent_log__isnull=True,
            ).values_list("pk", flat=True)
        )
        self.assertEqual(
            parentless, [root_row.pk],
            "The run's tree must have exactly one root node, the root's.",
        )
        self.assertFalse(
            CalculationLog.objects.filter(calculationId=self.calc_id)
            .exclude(pk=root_row.pk)
            .filter(calculation_log__contains="root root-failing_calcmodel_caught")
            .exists(),
            "The root's lines must not be copied into another node.",
        )

    # -- 15.68 ---------------------------------------------------------
    def test_15_68_a_root_saved_with_nothing_on_the_stack_gets_its_own_node(self):
        """
        Scenario 15.68: a root nobody put on the stack is still the node its lines go to
        Given: a calculation id and its audit row, but nothing on the model
               context stack: the root is saved by code that does not wrap the
               save in model_logging_context, as the API does
        When:  the root is saved with is_calculated=IN_PROGRESS
        Then:  its line is filed in one row keyed to the record, with no
               parent, rather than in a node that belongs to no record
        """
        self.assertIsNone(
            _model_context.get()["model_context"].current,
            "Precondition: nothing is on the model context stack.",
        )
        root = LogRootCalc(
            name="root-unframed", child_mode="log_only", units_csv="",
            is_calculated=LogRootCalc.IN_PROGRESS,
        )
        root.save()

        self.assert_log_row(root, parent=None, contains="root root-unframed")
        self.assert_total_rows(1)

    # -- 15.69 ---------------------------------------------------------
    def test_15_69_a_failed_root_saved_with_nothing_on_the_stack_keeps_its_log(self):
        """
        Scenario 15.69: a root nobody put on the stack keeps its log when it fails
        Given: as in 15.68, nothing on the model context stack; the atomic root
               logs a step and then fails inside its transaction
        When:  the root is saved with is_calculated=IN_PROGRESS
        Then:  the run keeps its log as 15j promises: one row on the root,
               holding the step it logged before failing
        """
        self.assertIsNone(
            _model_context.get()["model_context"].current,
            "Precondition: nothing is on the model context stack.",
        )
        with self.assertRaises(CalculationModelException):
            LogRootCalc(
                name="root-unframed-fail", child_mode="fail_after_log", units_csv="",
                is_calculated=LogRootCalc.IN_PROGRESS,
            ).save()

        root = LogRootCalc.objects.get(name="root-unframed-fail")
        self.assertEqual(root.is_calculated, LogRootCalc.ERROR, "Sanity: the run must fail.")
        row = self.assert_log_row(root, parent=None, contains="root root-unframed-fail")
        self.assertIn("step one done", row.calculation_log)
        self.assert_total_rows(1)

    # -- 15.70 ---------------------------------------------------------
    def test_15_70_calculate_through_the_api_nests_a_saved_calculation_model(self):
        """
        Scenario 15.70: Calculate through the API nests a CalculationModel the run saves
        Given: a stored root whose calculate() saves a nested CalculationModel
        When:  the root's Calculate is requested through the REST API (PATCH
               with calculate=true), so the run happens on the calculation
               thread pool, in a copy of the request's context, on a stack that
               already holds the root
        Then:  the root has exactly one row, with no parent, rather than a copy
               of itself nested under it, and the nested model has its own row
               under the root's
        """
        root = LogRootCalc.objects.create(name="root-api", child_mode="calcmodel_chain", units_csv="x")
        run_id = f"logrootcalc_{root.pk}_update_{uuid4().hex}"

        resp = self.client.patch(
            self.url_detail("logrootcalc", root.pk, calc_id=run_id),
            data={"calculate": "true"}, format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_202_ACCEPTED, getattr(resp, "data", None))
        deadline = time.monotonic() + _SETTLE_TIMEOUT_S
        while time.monotonic() < deadline:
            root.refresh_from_db()
            if root.is_calculated != LogRootCalc.IN_PROGRESS:
                break
            time.sleep(_POLL_S)
        self.assertEqual(root.is_calculated, LogRootCalc.SUCCESS, "The run must finish in SUCCESS.")

        # The run's rows are filed under the id the request carried.
        self.calc_id = run_id
        root_row = self.assert_log_row(root, parent=None, contains="root root-api")
        inner = LogInnerSavedCalc.objects.get(name="x")
        self.assert_log_row(inner, parent=root_row, contains="inner x")
        self.assert_total_rows(2)

    # -- 15.71 ---------------------------------------------------------
    def test_15_71_the_celery_route_gives_the_same_tree(self):
        """
        Scenario 15.71: a nested CalculationModel nests the same way on the Celery route
        Given: CELERY_ACTIVE with eager tasks, so the root runs in the generic
               calc_and_save task, and the nested CalculationModel its
               calculate() saves is dispatched to a calc_and_save task of its own
        When:  the root is saved
        Then:  the tree is the one 15.19 pins for the synchronous route: one
               row for the root, with no parent, and one for the nested model
               under it
        """
        dispatch = LogInnerSavedCalc.dispatch_calculation_task
        with _celery_eager(), patch.object(
            LogInnerSavedCalc, "dispatch_calculation_task", autospec=True, side_effect=dispatch,
        ) as inner_dispatch:
            root = self._save_root(child_mode="calcmodel_chain", units_csv="x")
        self.assertTrue(
            inner_dispatch.called,
            "Sanity: the nested model must have been dispatched to a Celery task.",
        )

        root_row = self.assert_log_row(root, parent=None, contains="root root-calcmodel_chain")
        inner = LogInnerSavedCalc.objects.get(name="x")
        self.assert_log_row(inner, parent=root_row, contains="inner x")
        self.assert_total_rows(2)
