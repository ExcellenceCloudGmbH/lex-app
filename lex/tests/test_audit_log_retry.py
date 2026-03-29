from unittest import TestCase
from unittest.mock import MagicMock, patch

from django.db.utils import OperationalError

from lex.audit_logging.mixins.AuditLogMixin import (
    _resolve_audit_failure_traceback,
    _delete_with_retry,
    _execute_with_retry,
    _is_retryable_db_error,
    _save_with_retry,
)
from lex.core.models.CalculationModel import CalculationModelException


def build_deadlock_error():
    deadlock = OperationalError("deadlock detected")
    deadlock.pgcode = "40P01"
    return deadlock


class RetryableDbErrorTests(TestCase):
    def test_detects_retryable_error_by_sqlstate(self):
        self.assertTrue(_is_retryable_db_error(build_deadlock_error()))

    def test_detects_retryable_error_by_message(self):
        error = OperationalError("could not serialize access due to concurrent update")
        self.assertTrue(_is_retryable_db_error(error))

    def test_non_retryable_error_returns_false(self):
        self.assertFalse(_is_retryable_db_error(RuntimeError("not retryable")))


class SaveWithRetryTests(TestCase):
    def test_retries_then_succeeds_for_deadlock(self):
        serializer = MagicMock()
        saved_instance = object()
        serializer.save.side_effect = [build_deadlock_error(), saved_instance]

        with patch("lex.audit_logging.mixins.AuditLogMixin.time.sleep") as sleep_mock:
            result = _save_with_retry(serializer, max_retries=3)

        self.assertIs(result, saved_instance)
        self.assertEqual(serializer.save.call_count, 2)
        sleep_mock.assert_called_once_with(0.05)

    def test_raises_after_retry_budget_is_exhausted(self):
        serializer = MagicMock()
        serializer.save.side_effect = [
            build_deadlock_error(),
            build_deadlock_error(),
            build_deadlock_error(),
        ]

        with patch("lex.audit_logging.mixins.AuditLogMixin.time.sleep") as sleep_mock:
            with self.assertRaises(OperationalError):
                _save_with_retry(serializer, max_retries=3)

        self.assertEqual(serializer.save.call_count, 3)
        self.assertEqual(sleep_mock.call_count, 2)

    def test_non_retryable_error_is_raised_immediately(self):
        serializer = MagicMock()
        serializer.save.side_effect = RuntimeError("hard failure")

        with patch("lex.audit_logging.mixins.AuditLogMixin.time.sleep") as sleep_mock:
            with self.assertRaises(RuntimeError):
                _save_with_retry(serializer, max_retries=3)

        self.assertEqual(serializer.save.call_count, 1)
        sleep_mock.assert_not_called()

    def test_delete_uses_retry_logic(self):
        instance = MagicMock()
        instance.delete.side_effect = [build_deadlock_error(), None]

        with patch("lex.audit_logging.mixins.AuditLogMixin.time.sleep") as sleep_mock:
            _delete_with_retry(instance, max_retries=3)

        self.assertEqual(instance.delete.call_count, 2)
        sleep_mock.assert_called_once_with(0.05)

    def test_execute_with_retry_uses_operation_name(self):
        operation = MagicMock(side_effect=[build_deadlock_error(), "done"])

        with patch("lex.audit_logging.mixins.AuditLogMixin.time.sleep"), \
             patch("lex.audit_logging.mixins.AuditLogMixin.logger.warning") as warning_mock:
            result = _execute_with_retry(operation, max_retries=2, operation_name="custom op")

        self.assertEqual(result, "done")
        self.assertEqual(operation.call_count, 2)
        warning_args = warning_mock.call_args[0]
        self.assertIn("Retrying %s after transient DB error", warning_args[0])
        self.assertEqual(warning_args[1], "custom op")


class AuditFailureTracebackTests(TestCase):
    def test_prefers_nested_calculation_stack_trace(self):
        nested_exception = CalculationModelException(
            exception_details=["SharePoint Server cannot handle requests at the moment."],
            stack_trace=["REAL INNER TRACEBACK"],
        )

        try:
            try:
                raise nested_exception
            except CalculationModelException:
                raise RuntimeError("wrapper failure")
        except RuntimeError as exc:
            error_traceback = _resolve_audit_failure_traceback(exc)

        self.assertEqual(error_traceback, "REAL INNER TRACEBACK")
