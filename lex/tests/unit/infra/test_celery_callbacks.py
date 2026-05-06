from contextlib import nullcontext
from unittest.mock import Mock, patch

from django.db import models
from django.test import SimpleTestCase, TestCase

from lex.audit_logging.models.AuditLog import AuditLog
from lex.audit_logging.models.AuditLogStatus import AuditLogStatus
from lex.audit_logging.utils.ModelContext import ModelContext
from lex.core.models.CalculationModel import CalculationModel
from lex.core.signals.ActiveCalculationStateStore import ActiveCalculationStateStore
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
        queryset = Mock()
        queryset.update.return_value = 1

        with patch(
            "lex.lex_app.celery_tasks.transaction.atomic",
            return_value=nullcontext(),
        ), patch.object(
            DummyCallbackCalculationModel._default_manager,
            "filter",
            return_value=queryset,
        ), patch(
            "lex.lex_app.celery_tasks.update_calculation_status"
        ) as update_status_mock, patch(
            "lex.lex_app.celery_tasks.ensure_terminal_calculation_audit"
        ):
            self.callback_task._update_model_status(
                instance,
                CalculationModel.SUCCESS,
                task_id="task-123",
            )

        self.assertEqual(instance.is_calculated, CalculationModel.SUCCESS)
        self.assertEqual(instance.task_id, "task-123")
        queryset.update.assert_called_once_with(
            is_calculated=CalculationModel.SUCCESS,
            task_id="task-123",
        )
        update_status_mock.assert_called_once_with(
            instance,
            exception_details=None,
            stack_trace=None,
        )


class CallbackTaskAuditRecoveryTests(TestCase):
    def setUp(self):
        self.callback_task = CallbackTask()
        ActiveCalculationStateStore.clear_all()

    def tearDown(self):
        ActiveCalculationStateStore.clear_all()

    def test_failure_callback_recreates_missing_terminal_audit_log(self):
        instance = DummyCallbackCalculationModel(id=5, name="Sponsor Plan")
        queryset = Mock()
        queryset.update.return_value = 1
        context_data = {
            "operation_id": "worker-op",
            "request_obj": {},
            "calculation_id": "calc-worker-1",
            "audit_log_temp": None,
        }

        with patch(
            "lex.lex_app.celery_tasks.transaction.atomic",
            return_value=nullcontext(),
        ), patch.object(
            DummyCallbackCalculationModel._default_manager,
            "filter",
            return_value=queryset,
        ), patch(
            "lex.lex_app.celery_tasks.update_calculation_status"
        ):
            self.callback_task._update_model_status(
                instance,
                CalculationModel.ERROR,
                error_message="worker exploded",
                stack_trace="Traceback: worker exploded",
                task_id="task-999",
                context_data=context_data,
                model_context=ModelContext([instance]),
            )

        audit_log = AuditLog.objects.get(calculation_id="calc-worker-1")
        audit_status = AuditLogStatus.objects.get(audit_log=audit_log)

        self.assertEqual(audit_log.resource, "dummycallbackcalculationmodel")
        self.assertEqual(audit_log.object_id, instance.pk)
        self.assertEqual(audit_status.status, "failure")
        self.assertEqual(audit_status.error_traceback, "Traceback: worker exploded")

    def test_failure_callback_updates_only_error_related_fields(self):
        instance = DummyCallbackCalculationModel(id=2, name="Sponsor Plan")
        queryset = Mock()
        queryset.update.return_value = 1

        with patch(
            "lex.lex_app.celery_tasks.transaction.atomic",
            return_value=nullcontext(),
        ), patch.object(
            DummyCallbackCalculationModel._default_manager,
            "filter",
            return_value=queryset,
        ), patch(
            "lex.lex_app.celery_tasks.update_calculation_status"
        ) as update_status_mock, patch(
            "lex.lex_app.celery_tasks.ensure_terminal_calculation_audit"
        ):
            self.callback_task._update_model_status(
                instance,
                CalculationModel.ERROR,
                error_message="foreign key missing",
                task_id="task-456",
            )

        self.assertEqual(instance.is_calculated, CalculationModel.ERROR)
        self.assertEqual(instance.error_message, "foreign key missing")
        self.assertEqual(instance.task_id, "task-456")
        queryset.update.assert_called_once_with(
            is_calculated=CalculationModel.ERROR,
            error_message="foreign key missing",
            task_id="task-456",
        )
        update_status_mock.assert_called_once_with(
            instance,
            exception_details="foreign key missing",
            stack_trace=None,
        )

    def test_missing_row_retries_after_bitemporal_resync(self):
        instance = DummyCallbackCalculationModel(id=3, name="Sponsor Plan")
        queryset = Mock()
        queryset.update.side_effect = [0, 1]

        with patch(
            "lex.lex_app.celery_tasks.transaction.atomic",
            return_value=nullcontext(),
        ), patch.object(
            DummyCallbackCalculationModel._default_manager,
            "filter",
            return_value=queryset,
        ) as filter_mock, patch.object(
            DummyCallbackCalculationModel,
            "history",
            object(),
            create=True,
        ), patch(
            "lex.process_admin.utils.bitemporal_sync.BitemporalSynchronizer.sync_record_for_model"
        ) as sync_mock, patch(
            "lex.lex_app.celery_tasks.update_calculation_status"
        ), patch(
            "lex.lex_app.celery_tasks.ensure_terminal_calculation_audit"
        ):
            self.callback_task._update_model_status(
                instance,
                CalculationModel.SUCCESS,
                task_id="task-789",
            )

        self.assertEqual(filter_mock.call_count, 2)
        self.assertEqual(queryset.update.call_count, 2)
        sync_mock.assert_called_once_with(DummyCallbackCalculationModel, 3)

    def test_missing_row_is_skipped_when_no_resynced_row_exists(self):
        instance = DummyCallbackCalculationModel(id=4, name="Sponsor Plan")
        queryset = Mock()
        queryset.update.side_effect = [0, 0]

        with patch(
            "lex.lex_app.celery_tasks.transaction.atomic",
            return_value=nullcontext(),
        ), patch.object(
            DummyCallbackCalculationModel._default_manager,
            "filter",
            return_value=queryset,
        ), patch.object(
            DummyCallbackCalculationModel,
            "history",
            object(),
            create=True,
        ), patch(
            "lex.process_admin.utils.bitemporal_sync.BitemporalSynchronizer.sync_record_for_model"
        ) as sync_mock, patch(
            "lex.lex_app.celery_tasks.update_calculation_status"
        ) as update_status_mock, patch(
            "lex.lex_app.celery_tasks.ensure_terminal_calculation_audit"
        ):
            self.callback_task._update_model_status(
                instance,
                CalculationModel.SUCCESS,
                task_id="task-000",
            )

        sync_mock.assert_called_once_with(DummyCallbackCalculationModel, 4)
        update_status_mock.assert_called_once_with(
            instance,
            exception_details=None,
            stack_trace=None,
        )
