import traceback
from unittest.mock import patch

from django.db import connection, models
from django.test import TransactionTestCase

from lex.api.utils import OperationContext
from lex.audit_logging.models.AuditLog import AuditLog
from lex.audit_logging.models.AuditLogStatus import AuditLogStatus
from lex.audit_logging.utils.ModelContext import model_logging_context
from lex.core.models.CalculationModel import CalculationModel, CalculationModelException
from lex.core.models.LexModel import PermissionResult
from lex.lex_app.celery_tasks import lex_shared_task


class AuditRecoveryCalculationModel(CalculationModel):
    name = models.CharField(max_length=100)
    error_message = models.TextField(null=True, blank=True)

    class Meta:
        app_label = "lex_app"

    @lex_shared_task
    def calculate(self):
        raise ValueError("sync calculation exploded")

    def permission_read(self, user_context):
        return PermissionResult.allow_all("test")

    def permission_edit(self, user_context):
        return PermissionResult.allow_all("test")

    def permission_create(self, user_context):
        return True

    def permission_delete(self, user_context):
        return True


class CalculationAuditRecoveryTests(TransactionTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        with connection.schema_editor() as schema_editor:
            schema_editor.create_model(AuditRecoveryCalculationModel)

    @classmethod
    def tearDownClass(cls):
        try:
            with connection.schema_editor() as schema_editor:
                schema_editor.delete_model(AuditRecoveryCalculationModel)
        finally:
            super().tearDownClass()

    def test_sync_atomic_failure_creates_terminal_audit_records(self):
        instance = AuditRecoveryCalculationModel.objects.create(name="initial")

        with self.assertRaises(CalculationModelException) as raised:
            with OperationContext({}, "calc-sync-failure"):
                with model_logging_context(instance):
                    instance.is_calculated = CalculationModel.IN_PROGRESS
                    instance.save()

        CalculationModel.persist_error_state(raised.exception.calc_obj)
        instance.refresh_from_db()
        audit_log = AuditLog.objects.get(calculation_id="calc-sync-failure")
        audit_status = AuditLogStatus.objects.get(audit_log=audit_log)

        self.assertEqual(instance.is_calculated, CalculationModel.ERROR)
        self.assertEqual(audit_log.resource, "auditrecoverycalculationmodel")
        self.assertEqual(audit_log.object_id, instance.pk)
        self.assertEqual(audit_status.status, "failure")
        self.assertIn("sync calculation exploded", audit_status.error_traceback)

    def test_nested_calculation_failure_keeps_inner_traceback_in_audit_status(self):
        instance = AuditRecoveryCalculationModel.objects.create(name="nested")

        def wrapped_calculate(model_instance):
            try:
                raise ValueError("sharepoint 401 unauthorized")
            except ValueError:
                inner_traceback = traceback.format_exc()

            try:
                raise CalculationModelException(
                    calc_obj=[model_instance],
                    exception_details=[
                        "SharePoint Server cannot handle requests at the moment."
                    ],
                    stack_trace=[inner_traceback],
                )
            except CalculationModelException:
                raise Exception("wrapped at period level")

        with patch.object(
            AuditRecoveryCalculationModel,
            "calculate",
            new=wrapped_calculate,
        ):
            with self.assertRaises(CalculationModelException) as raised:
                with OperationContext({}, "calc-nested-failure"):
                    with model_logging_context(instance):
                        instance.is_calculated = CalculationModel.IN_PROGRESS
                        instance.save()

        CalculationModel.persist_error_state(raised.exception.calc_obj)
        audit_log = AuditLog.objects.get(calculation_id="calc-nested-failure")
        audit_status = AuditLogStatus.objects.get(audit_log=audit_log)

        self.assertEqual(
            raised.exception.exception_details[0],
            "SharePoint Server cannot handle requests at the moment.",
        )
        self.assertIn("sharepoint 401 unauthorized", raised.exception.stack_trace[0])
        self.assertIn("sharepoint 401 unauthorized", audit_status.error_traceback)
        self.assertNotIn("wrapped at period level", audit_status.error_traceback)
