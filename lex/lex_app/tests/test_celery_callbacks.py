from contextlib import nullcontext
from unittest.mock import Mock, patch

from django.db import models
from django.test import SimpleTestCase

from lex.core.models.CalculationModel import CalculationModel
from lex.lex_app.celery_tasks import CallbackTask


class DummyCallbackCalculationModel(CalculationModel):
    task_id = models.CharField(max_length=255, null=True, blank=True)
    error_message = models.TextField(null=True, blank=True)
    name = models.CharField(max_length=255, null=True, blank=True)

    class Meta:
        app_label = "lex_app"
        managed = False

    def calculate(self):
        return None


class CallbackTaskStatusUpdateTests(SimpleTestCase):
    def setUp(self):
        self.callback_task = CallbackTask()

    def test_success_callback_updates_only_status_fields(self):
        instance = DummyCallbackCalculationModel(id=1, name="Sponsor Plan")
        instance.save = Mock()

        with patch(
            "lex.lex_app.celery_tasks.transaction.atomic",
            return_value=nullcontext(),
        ), patch(
            "lex.lex_app.celery_tasks.update_calculation_status"
        ) as update_status_mock:
            self.callback_task._update_model_status(
                instance,
                CalculationModel.SUCCESS,
                task_id="task-123",
            )

        self.assertEqual(instance.is_calculated, CalculationModel.SUCCESS)
        self.assertEqual(instance.task_id, "task-123")
        instance.save.assert_called_once_with(
            skip_hooks=True,
            update_fields=["is_calculated", "task_id"],
        )
        update_status_mock.assert_called_once_with(
            instance,
            exception_details=None,
            stack_trace=None,
        )

    def test_failure_callback_updates_only_error_related_fields(self):
        instance = DummyCallbackCalculationModel(id=2, name="Sponsor Plan")
        instance.save = Mock()

        with patch(
            "lex.lex_app.celery_tasks.transaction.atomic",
            return_value=nullcontext(),
        ), patch(
            "lex.lex_app.celery_tasks.update_calculation_status"
        ) as update_status_mock:
            self.callback_task._update_model_status(
                instance,
                CalculationModel.ERROR,
                error_message="foreign key missing",
                task_id="task-456",
            )

        self.assertEqual(instance.is_calculated, CalculationModel.ERROR)
        self.assertEqual(instance.error_message, "foreign key missing")
        self.assertEqual(instance.task_id, "task-456")
        instance.save.assert_called_once_with(
            skip_hooks=True,
            update_fields=["is_calculated", "error_message", "task_id"],
        )
        update_status_mock.assert_called_once_with(
            instance,
            exception_details="foreign key missing",
            stack_trace=None,
        )
