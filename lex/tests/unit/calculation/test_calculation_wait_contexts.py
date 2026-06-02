"""
Tests for ``WaitForTasks`` / ``FireAndForget`` calculation wait contexts.

Verifies:
    • A calculation without an outer ``WaitForTasks`` waits locally
    • Nested ``WaitForTasks`` properly registers sub-tasks
    • ``register_task_with_context`` attaches to the active context
"""

from contextlib import nullcontext
from unittest.mock import Mock, patch

from django.test import SimpleTestCase
from lex.core.models.CalculationModel import CalculationModel
from lex.lex_app.celery_tasks import WaitForTasks, register_task_with_context


class DummyWaitContextCalculationModel(CalculationModel):
    class Meta:
        app_label = "lex_app"
        managed = False

    def calculate(self):
        return None


class CalculationWaitContextTests(SimpleTestCase):
    @staticmethod
    def _build_instance(pk):
        instance = DummyWaitContextCalculationModel(id=pk)
        instance.is_calculated = CalculationModel.IN_PROGRESS
        return instance

    def test_calculate_hook_waits_locally_without_outer_wait_context(self):
        instance = self._build_instance(1)
        task_result = Mock()
        task_result.id = "task-1"

        with patch.object(
            DummyWaitContextCalculationModel, "should_use_celery", return_value=True
        ), patch.object(
            DummyWaitContextCalculationModel,
            "dispatch_calculation_task",
            side_effect=lambda: register_task_with_context(task_result),
        ), patch(
            "lex.core.signals.CalculationSignals.update_calculation_status"
        ), patch(
            "lex.core.signals.ActiveCalculationStateStore.ActiveCalculationStateStore.get_calculation_id",
            return_value="calc-existing",
        ), patch(
            "lex.lex_app.celery_tasks._celery_is_active", return_value=True
        ), patch(
            "lex.lex_app.celery_tasks.allow_join_result",
            return_value=nullcontext(),
        ), patch(
            "lex.lex_app.celery_tasks.is_celery_worker_process",
            return_value=False,
        ):
            DummyWaitContextCalculationModel.calculate_hook(instance)

        task_result.get.assert_called_once_with()

    def test_legacy_and_canonical_celery_task_modules_share_wait_context(self):
        import lex.lex_app.celery_tasks as canonical_celery_tasks
        import lex_app.celery_tasks as legacy_celery_tasks

        self.assertIs(canonical_celery_tasks, legacy_celery_tasks)
        self.assertIs(
            canonical_celery_tasks.WaitForTasks,
            legacy_celery_tasks.WaitForTasks,
        )
        self.assertIs(
            canonical_celery_tasks.tasks_context,
            legacy_celery_tasks.tasks_context,
        )

    def test_calculate_hook_reuses_outer_wait_context(self):
        instance = self._build_instance(2)
        task_result = Mock()
        task_result.id = "task-2"

        with patch.object(
            DummyWaitContextCalculationModel, "should_use_celery", return_value=True
        ), patch.object(
            DummyWaitContextCalculationModel,
            "dispatch_calculation_task",
            side_effect=lambda: register_task_with_context(task_result),
        ), patch(
            "lex.core.signals.CalculationSignals.update_calculation_status"
        ), patch(
            "lex.core.signals.ActiveCalculationStateStore.ActiveCalculationStateStore.get_calculation_id",
            return_value="calc-existing",
        ), patch(
            "lex.lex_app.celery_tasks._celery_is_active", return_value=True
        ), patch(
            "lex.lex_app.celery_tasks.allow_join_result",
            return_value=nullcontext(),
        ), patch(
            "lex.lex_app.celery_tasks.is_celery_worker_process",
            return_value=True,
        ):
            with WaitForTasks():
                DummyWaitContextCalculationModel.calculate_hook(instance)
                task_result.get.assert_not_called()

        task_result.get.assert_called_once_with()

    def test_calculate_hook_executes_sync_inside_worker_without_outer_wait_context(self):
        instance = self._build_instance(3)

        with patch.object(
            DummyWaitContextCalculationModel, "should_use_celery", return_value=True
        ), patch.object(
            DummyWaitContextCalculationModel, "dispatch_calculation_task"
        ) as dispatch_task, patch.object(
            DummyWaitContextCalculationModel, "execute_calculation_sync"
        ) as execute_sync, patch(
            "lex.core.signals.CalculationSignals.update_calculation_status"
        ), patch(
            "lex.core.signals.ActiveCalculationStateStore.ActiveCalculationStateStore.get_calculation_id",
            return_value="calc-existing",
        ), patch(
            "lex.lex_app.celery_tasks.is_celery_worker_process",
            return_value=True,
        ):
            DummyWaitContextCalculationModel.calculate_hook(instance)

        execute_sync.assert_called_once_with()
        dispatch_task.assert_not_called()
