"""Default-dispatch contract for nested calculations running inside a Celery worker.

Intent: a calculation that is itself executing inside a Celery worker must, by
default, still dispatch its nested work to Celery rather than collapsing to an
inline synchronous run. The previous ``is_celery_worker_process()`` guard made
nested fan-out *opt-in* (it ran inline unless the caller opened an explicit
async context); that guard was removed so the default is "always dispatch".
When no async context is active the dispatch path opens its own ``WaitForTasks``
and blocks on the children — so correctness/ordering is preserved at the cost of
holding the worker slot. A regression here would silently serialise large
combinatorial calcs (e.g. 65k-row InvestmentPosting) onto a single worker.

Cluster 7q — scenarios 7.196–7.201. Type: E.
Covers: lex/core/mixins/CalculatedModelMixin.py (_dispatch_model_processing),
        lex/core/models/CalculationModel.py (calculate_hook dispatch branch).
Run: python -m lex pytest lex/test_project/tests/calculations/test_7q_worker_default_dispatch.py -v
"""

from __future__ import annotations

import os
import unittest
from contextlib import contextmanager, nullcontext
from unittest.mock import Mock, patch

import pytest

from lex.core.models.CalculationModel import CalculationModel
from lex.core.tasks.CeleryTaskDispatcher import CeleryTaskDispatcher
from lex.lex_app.celery_tasks import (
    FireAndForget,
    WaitForTasks,
    register_task_with_context,
)
from lex.test_project.tests._e2e_test_case import E2ETestCase

from .models import ALL_MODELS, AtomicCalc, CombinatorialCalc

pytestmark = pytest.mark.calculations


class TestCluster07q_MixinWorkerDefaultDispatch(E2ETestCase):
    """Cluster 7q: ``CalculatedModelMixin`` fan-out dispatches from inside a
    worker by default — the worker-detection inline branch is gone."""

    e2e_models = ALL_MODELS

    def setUp(self) -> None:
        super().setUp()
        CombinatorialCalc._region_keys = ["US", "EU"]
        CombinatorialCalc._category_keys = ["A", "B"]
        CombinatorialCalc.fail_for_region = None

    def _run_dispatch(self, context_manager):
        """Drive ``_dispatch_model_processing`` while pretending to be inside a
        worker, under *context_manager*, and return the dispatcher mock."""
        model = CombinatorialCalc(region="US", category="A")
        clusters = {"US": [model]}

        # ``hasattr(cls.calculate, 'delay')`` must be True for celery_active.
        original_calculate = CombinatorialCalc.calculate
        original_calculate.delay = lambda *a, **k: None  # pragma: no cover

        with patch.dict(os.environ, {"CELERY_ACTIVE": "true"}, clear=False), patch(
            "lex.lex_app.celery_tasks._celery_is_active", return_value=True
        ), patch(
            "lex.lex_app.celery_tasks.is_celery_worker_process", return_value=True
        ), patch.object(
            CeleryTaskDispatcher, "dispatch_calculation_groups"
        ) as mock_dispatch, patch(
            "lex.core.mixins.CalculatedModelMixin.calc_and_save_sync"
        ) as mock_sync:
            try:
                with context_manager:
                    CombinatorialCalc._dispatch_model_processing(clusters)
            finally:
                try:
                    del original_calculate.delay
                except AttributeError:
                    pass

        return mock_dispatch, mock_sync, model

    # -- 7.196 ---------------------------------------------------------
    def test_7_196_inside_worker_no_context_dispatches(self) -> None:
        """Scenario 7.196: inside a worker, NO async context open.
        Given: CELERY_ACTIVE=true, is_celery_worker_process()=True, no scope.
        When:  _dispatch_model_processing runs.
        Then:  it fans out to CeleryTaskDispatcher (NOT calc_and_save_sync) —
               the old inline-inside-worker behaviour is gone.
        """
        mock_dispatch, mock_sync, model = self._run_dispatch(nullcontext())

        self.assertTrue(
            mock_dispatch.called,
            "Inside a worker with no async context the mixin must now dispatch "
            "to Celery by default; it fell back to the inline sync path instead.",
        )
        self.assertFalse(
            mock_sync.called,
            "calc_and_save_sync must NOT be called — nested fan-out no longer "
            "collapses to inline inside a worker.",
        )
        self.assertEqual(
            mock_dispatch.call_args.args[0], [[model]],
            "Dispatcher must receive the flattened cluster groups.",
        )

    # -- 7.197 ---------------------------------------------------------
    def test_7_197_inside_worker_waitfortasks_dispatches(self) -> None:
        """Scenario 7.197: inside a worker WITH an explicit WaitForTasks scope →
        still dispatches (context is reused, not a behaviour change)."""
        mock_dispatch, mock_sync, _ = self._run_dispatch(WaitForTasks())

        self.assertTrue(mock_dispatch.called, "WaitForTasks scope must dispatch.")
        self.assertFalse(mock_sync.called, "No inline fallback under WaitForTasks.")

    # -- 7.198 ---------------------------------------------------------
    def test_7_198_inside_worker_fireandforget_dispatches(self) -> None:
        """Scenario 7.198: inside a worker WITH a FireAndForget scope →
        dispatches (non-blocking opt-in path still fans out)."""
        mock_dispatch, mock_sync, _ = self._run_dispatch(FireAndForget())

        self.assertTrue(mock_dispatch.called, "FireAndForget scope must dispatch.")
        self.assertFalse(mock_sync.called, "No inline fallback under FireAndForget.")


class TestCluster07q_CalculationModelWorkerDefaultDispatch(E2ETestCase):
    """Cluster 7q: ``CalculationModel`` single-instance dispatch fans out from
    inside a worker by default — the ``execute_calculation_sync`` inline branch
    is gone."""

    e2e_models = ALL_MODELS

    def _build_instance(self):
        instance = AtomicCalc(id=1, name="worker-default")
        instance.is_calculated = CalculationModel.IN_PROGRESS
        return instance

    @contextmanager
    def _worker_patches(self, *, register_result=None):
        """Pretend to be inside a Celery worker with Celery active, yielding the
        instance + dispatch/sync mocks. Explicit async contexts must be entered
        by the caller *inside* this block so that ``_celery_is_active`` is True
        when ``WaitForTasks``/``FireAndForget`` push onto the contextvar stack —
        otherwise they construct inert (``_active=False``) scopes that never
        register and ``get_current_context()`` returns ``None``.
        """
        instance = self._build_instance()

        def _dispatch_side_effect():
            if register_result is not None:
                return register_task_with_context(register_result)
            return None

        with patch.object(
            AtomicCalc, "should_use_celery", return_value=True
        ), patch.object(
            AtomicCalc, "dispatch_calculation_task", side_effect=_dispatch_side_effect
        ) as dispatch_mock, patch.object(
            AtomicCalc, "execute_calculation_sync"
        ) as sync_mock, patch(
            "lex.core.signals.CalculationSignals.update_calculation_status"
        ), patch(
            "lex.core.signals.ActiveCalculationStateStore.ActiveCalculationStateStore.get_calculation_id",
            return_value="calc-existing",
        ), patch(
            "lex.lex_app.celery_tasks._celery_is_active", return_value=True
        ), patch(
            "lex.lex_app.celery_tasks.allow_join_result", return_value=nullcontext()
        ), patch(
            "lex.lex_app.celery_tasks.is_celery_worker_process", return_value=True
        ):
            yield instance, dispatch_mock, sync_mock

    # -- 7.199 ---------------------------------------------------------
    def test_7_199_inside_worker_no_context_dispatches_and_blocks(self) -> None:
        """Scenario 7.199: inside a worker, NO async context.
        Given: should_use_celery()=True, is_celery_worker_process()=True.
        When:  calculate_hook runs.
        Then:  it dispatches via its own WaitForTasks (and blocks on the child
               result) instead of calling execute_calculation_sync.
        """
        task_result = Mock()
        task_result.id = "task-7-199"

        with self._worker_patches(register_result=task_result) as (
            instance,
            dispatch_mock,
            sync_mock,
        ):
            AtomicCalc.calculate_hook(instance)

        dispatch_mock.assert_called_once_with()
        sync_mock.assert_not_called()
        task_result.get.assert_called_once_with()  # own WaitForTasks drained → blocked

    # -- 7.200 ---------------------------------------------------------
    def test_7_200_inside_worker_waitfortasks_dispatches(self) -> None:
        """Scenario 7.200: inside a worker WITH an outer WaitForTasks → dispatch
        is registered on the outer scope; no inline execution."""
        task_result = Mock()
        task_result.id = "task-7-200"

        with self._worker_patches(register_result=task_result) as (
            instance,
            dispatch_mock,
            sync_mock,
        ):
            with WaitForTasks():
                AtomicCalc.calculate_hook(instance)
                # Outer scope still open → child not yet drained.
                task_result.get.assert_not_called()
            # Outer scope exited → child drained.
            task_result.get.assert_called_once_with()

        dispatch_mock.assert_called_once_with()
        sync_mock.assert_not_called()

    # -- 7.201 ---------------------------------------------------------
    def test_7_201_inside_worker_fireandforget_dispatches_without_block(self) -> None:
        """Scenario 7.201: inside a worker WITH a FireAndForget scope → dispatch
        happens and the parent does NOT block on the child."""
        task_result = Mock()
        task_result.id = "task-7-201"

        with self._worker_patches(register_result=task_result) as (
            instance,
            dispatch_mock,
            sync_mock,
        ):
            with FireAndForget():
                AtomicCalc.calculate_hook(instance)

            dispatch_mock.assert_called_once_with()
            sync_mock.assert_not_called()
            task_result.get.assert_not_called()  # FireAndForget never blocks


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
