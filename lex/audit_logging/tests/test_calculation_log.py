from unittest.mock import patch

from django.contrib.contenttypes.models import ContentType
from django.db import transaction
from django.test import TransactionTestCase

from lex.audit_logging.models.AuditLog import AuditLog
from lex.audit_logging.models.CalculationLog import CalculationLog
from lex.audit_logging.utils.DataModels import ContextInfo


class CalculationLogRegressionTest(TransactionTestCase):
    def setUp(self):
        self.audit_log = AuditLog.objects.create(
            author="tester",
            resource="calculation",
            action="update",
            calculation_id="calc-1",
        )
        self.content_type = ContentType.objects.get_for_model(CalculationLog)
        self.parent_model = CalculationLog.objects.create(calculationId="parent-model")
        self.current_model = CalculationLog.objects.create(calculationId="current-model")

    def _context_info(self):
        return ContextInfo(
            calculation_id="calc-1",
            audit_log=self.audit_log,
            current_model=self.current_model,
            parent_model=self.parent_model,
            current_record=None,
            parent_record=None,
            content_type=self.content_type,
            parent_content_type=self.content_type,
            root_record=None,
        )

    @patch("lex.audit_logging.utils.ContextResolver.ContextResolver.resolve")
    def test_log_reuses_existing_parent_log_when_duplicates_exist(self, resolve_mock):
        resolve_mock.return_value = self._context_info()

        first_parent = CalculationLog.objects.create(
            calculationId="calc-1",
            audit_log=self.audit_log,
            content_type=self.content_type,
            object_id=self.parent_model.pk,
        )
        CalculationLog.objects.create(
            calculationId="calc-1",
            audit_log=self.audit_log,
            content_type=self.content_type,
            object_id=self.parent_model.pk,
        )

        with self.assertLogs("lex.calclog", level="WARNING") as captured_logs:
            CalculationLog.log("Warning: child message")

        child_log = CalculationLog.objects.get(
            calculationId="calc-1",
            audit_log=self.audit_log,
            content_type=self.content_type,
            object_id=self.current_model.pk,
        )

        self.assertEqual(child_log.parent_log_id, first_parent.id)
        self.assertEqual(child_log.calculation_log, "\nWarning: child message")
        self.assertEqual(
            CalculationLog.objects.filter(
                calculationId="calc-1",
                audit_log=self.audit_log,
                content_type=self.content_type,
                object_id=self.parent_model.pk,
                parent_log__isnull=True,
            ).count(),
            2,
        )
        self.assertTrue(
            any("Detected duplicate CalculationLog rows" in entry for entry in captured_logs.output)
        )

    @patch("lex.audit_logging.utils.ContextResolver.ContextResolver.resolve")
    def test_log_appends_to_oldest_current_log_when_duplicates_exist(self, resolve_mock):
        resolve_mock.return_value = self._context_info()

        parent_log = CalculationLog.objects.create(
            calculationId="calc-1",
            audit_log=self.audit_log,
            content_type=self.content_type,
            object_id=self.parent_model.pk,
        )
        oldest_current_log = CalculationLog.objects.create(
            calculationId="calc-1",
            audit_log=self.audit_log,
            content_type=self.content_type,
            object_id=self.current_model.pk,
            parent_log=parent_log,
            calculation_log="existing message",
        )
        duplicate_current_log = CalculationLog.objects.create(
            calculationId="calc-1",
            audit_log=self.audit_log,
            content_type=self.content_type,
            object_id=self.current_model.pk,
            parent_log=parent_log,
            calculation_log="duplicate message",
        )

        with self.assertLogs("lex.calclog", level="WARNING") as captured_logs:
            CalculationLog.log("Finish: child completed")

        oldest_current_log.refresh_from_db()
        duplicate_current_log.refresh_from_db()

        self.assertEqual(
            oldest_current_log.calculation_log,
            "existing message\nFinish: child completed",
        )
        self.assertEqual(duplicate_current_log.calculation_log, "duplicate message")
        self.assertEqual(
            CalculationLog.objects.filter(
                calculationId="calc-1",
                audit_log=self.audit_log,
                content_type=self.content_type,
                object_id=self.current_model.pk,
                parent_log=parent_log,
            ).count(),
            2,
        )
        self.assertTrue(
            any("Detected duplicate CalculationLog rows" in entry for entry in captured_logs.output)
        )

    @patch("lex.audit_logging.utils.ContextResolver.ContextResolver.resolve")
    def test_log_defers_persistence_until_commit_inside_atomic_block(self, resolve_mock):
        resolve_mock.return_value = self._context_info()

        with patch.object(CalculationLog, "_persist_message") as persist_mock:
            with transaction.atomic():
                CalculationLog.log("Start: deferred")
                persist_mock.assert_not_called()

            persist_mock.assert_called_once()
