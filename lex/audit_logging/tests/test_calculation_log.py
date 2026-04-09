"""
Regression tests for ``CalculationLog`` deduplication and delivery.

Verifies:
    • Duplicate log rows are prevented within the same audit-log context
    • Deferred persistence respects audit-log state
    • Cache + websocket fan-out reaches both root and current records
    • Model-context fallback routes messages when operation context
      is unavailable
"""

from unittest.mock import call, patch

from django.contrib.contenttypes.models import ContentType
from django.db import transaction
from django.test import TransactionTestCase

from lex.audit_logging.models.AuditLog import AuditLog
from lex.audit_logging.models.CalculationLog import CalculationLog
from lex.audit_logging.utils.DataModels import ContextInfo, ContextResolutionError
from lex.audit_logging.utils.ModelContext import ModelContext, _model_context


class CalculationLogRegressionTest(TransactionTestCase):
    """Prove deduplication, cache fan-out, and fallback routing of calculation logs."""
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
        self._original_model_context = _model_context.get()["model_context"]

    def tearDown(self):
        _model_context.get()["model_context"] = self._original_model_context
        super().tearDown()

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

    @patch("lex.audit_logging.utils.ContextResolver.ContextResolver.resolve")
    def test_log_fans_out_cache_and_websocket_updates_to_root_and_current_records(self, resolve_mock):
        resolve_mock.return_value = ContextInfo(
            calculation_id="calc-1",
            audit_log=self.audit_log,
            current_model=self.current_model,
            parent_model=self.parent_model,
            current_record="demandforecastparallel_7",
            parent_record="rundemandforecastparallel_1",
            content_type=self.content_type,
            parent_content_type=self.content_type,
            root_record="rundemandforecastparallel_1",
        )

        with patch.object(CalculationLog, "_persist_message") as persist_mock, patch(
            "lex.audit_logging.models.CalculationLog.CacheManager.store_message"
        ) as store_message_mock, patch(
            "lex.audit_logging.models.CalculationLog.logging.getLogger"
        ) as get_logger_mock:
            logger_mock = get_logger_mock.return_value

            CalculationLog.log("Start: parallel child")

        persist_mock.assert_called_once()
        store_message_mock.assert_has_calls(
            [
                call("demandforecastparallel_7_calc-1", "Start: parallel child"),
                call("rundemandforecastparallel_1_calc-1", "Start: parallel child"),
            ]
        )
        self.assertIn(
            (
                "Start: parallel child",
            ),
            [call.args for call in logger_mock.debug.call_args_list],
        )
        self.assertEqual(
            logger_mock.debug.call_args_list[-2].kwargs,
            {
                "extra": {
                    "calculation_record": "demandforecastparallel_7",
                    "calculationId": "calc-1",
                }
            },
        )
        self.assertEqual(
            logger_mock.debug.call_args_list[-1].kwargs,
            {
                "extra": {
                    "calculation_record": "rundemandforecastparallel_1",
                    "calculationId": "calc-1",
                }
            },
        )

    @patch(
        "lex.audit_logging.utils.ContextResolver.ContextResolver.resolve",
        side_effect=ContextResolutionError("missing calculation id"),
    )
    def test_log_uses_model_context_fallback_for_websocket_routing(self, _resolve_mock):
        _model_context.get()["model_context"] = ModelContext(
            [self.parent_model, self.current_model]
        )

        with patch(
            "lex.audit_logging.models.CalculationLog.logging.getLogger"
        ) as get_logger_mock:
            logger_mock = get_logger_mock.return_value

            CalculationLog.log("Warning: fallback routing")

        debug_calls = logger_mock.debug.call_args_list
        self.assertTrue(
            any(
                call.args
                and "Context resolution incomplete" in str(call.args[0])
                for call in debug_calls
            )
        )
        self.assertIn(
            {
                "extra": {
                    "calculation_record": f"{self.current_model._meta.model_name}_{self.current_model.pk}",
                    "calculationId": "",
                }
            },
            [call.kwargs for call in debug_calls],
        )
        self.assertIn(
            {
                "extra": {
                    "calculation_record": f"{self.parent_model._meta.model_name}_{self.parent_model.pk}",
                    "calculationId": "",
                }
            },
            [call.kwargs for call in debug_calls],
        )
