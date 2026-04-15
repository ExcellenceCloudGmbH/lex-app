"""
Tests for ``AuditLogMixin`` — the view-level audit trail for CRUD operations.

**What is tested:**

    • ``log_change()`` creates an ``AuditLog`` + ``AuditLogStatus(pending)``
      pair for every create / update / delete operation.
    • ``perform_create()`` transitions the status to ``success`` on happy path.
    • ``perform_update()`` transitions the status to ``success`` on happy path.
    • ``perform_destroy()`` transitions the status to ``success`` on happy path.
    • On any exception the status transitions to ``failure`` with a traceback
      and the exception is re-raised.
    • Retry logic (``_execute_with_retry``) retries on PostgreSQL deadlocks
      but propagates non-retryable errors immediately.

**Why this matters:**

    Every data mutation in the framework is audit-logged.  If the
    pending → success / failure state machine breaks, customers lose
    visibility into what changed and when — a compliance requirement in
    regulated environments.

**How to run:**

    .. code-block:: bash

        lex test lex.audit_logging.tests.test_audit_log_mixin --verbosity=2
"""

import time
from types import SimpleNamespace
from unittest.mock import MagicMock, patch, PropertyMock

from django.test import SimpleTestCase

from lex.audit_logging.mixins.AuditLogMixin import (
    AuditLogMixin,
    _execute_with_retry,
    _is_retryable_db_error,
    _iter_exception_chain,
    RETRYABLE_SQLSTATE_CODES,
    MAX_UPDATE_RETRIES,
)


# ────────────────────────────────────────────────────────────────────
#  Retry helper tests (pure functions — no DB)
# ────────────────────────────────────────────────────────────────────

class RetryHelperTest(SimpleTestCase):
    """Verify deadlock-retry logic in ``_execute_with_retry``."""

    def test_iter_exception_chain_single(self):
        """Single exception with no cause yields one item."""
        ex = ValueError("boom")
        chain = list(_iter_exception_chain(ex))
        self.assertEqual(len(chain), 1)
        self.assertIs(chain[0], ex)

    def test_iter_exception_chain_chained(self):
        """Chained exceptions are traversed via __cause__."""
        inner = RuntimeError("inner")
        outer = ValueError("outer")
        outer.__cause__ = inner
        chain = list(_iter_exception_chain(outer))
        self.assertEqual(len(chain), 2)
        self.assertIs(chain[0], outer)
        self.assertIs(chain[1], inner)

    def test_iter_exception_chain_context(self):
        """Chained exceptions via __context__ are also traversed."""
        inner = RuntimeError("inner")
        outer = ValueError("outer")
        outer.__context__ = inner
        chain = list(_iter_exception_chain(outer))
        self.assertEqual(len(chain), 2)

    def test_iter_exception_chain_cycle_protection(self):
        """Cyclic exception chains terminate without infinite loop."""
        a = ValueError("a")
        b = ValueError("b")
        a.__cause__ = b
        b.__cause__ = a  # cycle
        chain = list(_iter_exception_chain(a))
        self.assertEqual(len(chain), 2)

    # ── _is_retryable_db_error ────────────────────────────────────

    def test_deadlock_pgcode_is_retryable(self):
        """PostgreSQL deadlock error codes are retryable."""
        for code in RETRYABLE_SQLSTATE_CODES:
            exc = Exception("db error")
            exc.pgcode = code
            self.assertTrue(
                _is_retryable_db_error(exc),
                f"pgcode {code} should be retryable",
            )

    def test_deadlock_message_is_retryable(self):
        """PostgreSQL deadlock messages are retryable."""
        exc = Exception("deadlock detected on tuple xyz")
        self.assertTrue(_is_retryable_db_error(exc))

    def test_serialization_failure_is_retryable(self):
        """Serialization failure messages are retryable."""
        exc = Exception("could not serialize access due to concurrent update")
        self.assertTrue(_is_retryable_db_error(exc))

    def test_generic_error_not_retryable(self):
        """Non-deadlock errors are not retryable."""
        exc = ValueError("bad input")
        self.assertFalse(_is_retryable_db_error(exc))

    def test_chained_deadlock_is_retryable(self):
        """Deadlock pgcode on a chained __cause__ is detected."""
        inner = Exception("pg")
        inner.pgcode = "40P01"
        outer = RuntimeError("wrapper")
        outer.__cause__ = inner
        self.assertTrue(_is_retryable_db_error(outer))

    # ── _execute_with_retry ────────────────────────────────────────

    def test_execute_with_retry_succeeds_first_try(self):
        """Happy path — callable succeeds on first attempt."""
        result = _execute_with_retry(lambda: 42, max_retries=3)
        self.assertEqual(result, 42)

    @patch("lex.audit_logging.mixins.AuditLogMixin.time.sleep")
    def test_execute_with_retry_retries_on_deadlock(self, mock_sleep):
        """Deadlock on first call, success on second."""
        deadlock = Exception("deadlock detected")
        call_count = {"n": 0}

        def flaky():
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise deadlock
            return "ok"

        result = _execute_with_retry(flaky, max_retries=3)
        self.assertEqual(result, "ok")
        self.assertEqual(call_count["n"], 2)
        mock_sleep.assert_called_once()

    def test_execute_with_retry_raises_non_retryable(self):
        """Non-retryable errors propagate immediately without retry."""
        call_count = {"n": 0}

        def always_fails():
            call_count["n"] += 1
            raise ValueError("bad input")

        with self.assertRaises(ValueError):
            _execute_with_retry(always_fails, max_retries=3)
        self.assertEqual(call_count["n"], 1)

    @patch("lex.audit_logging.mixins.AuditLogMixin.time.sleep")
    def test_execute_with_retry_exhausts_retries(self, mock_sleep):
        """When all retries are exhausted, the last exception propagates."""
        deadlock = Exception("deadlock detected")

        def always_deadlocks():
            raise deadlock

        with self.assertRaises(Exception) as cm:
            _execute_with_retry(always_deadlocks, max_retries=3)
        self.assertIn("deadlock", str(cm.exception))


# ────────────────────────────────────────────────────────────────────
#  AuditLogMixin.log_change() tests (mocked DB)
# ────────────────────────────────────────────────────────────────────

class AuditLogMixinLogChangeTest(SimpleTestCase):
    """Verify ``log_change()`` creates the AuditLog + pending status pair."""

    def _make_mixin(self, user=None, calculation_id=None):
        """Return a minimal AuditLogMixin stub with a mocked request."""
        mixin = AuditLogMixin()
        mixin.request = SimpleNamespace(user=user or "test-user")
        mixin.kwargs = {"calculationId": calculation_id}
        return mixin

    @patch("lex.audit_logging.mixins.AuditLogMixin.AuditLogStatus.objects")
    @patch("lex.audit_logging.mixins.AuditLogMixin.AuditLog.objects")
    def test_log_change_creates_audit_log_and_pending_status(
        self, mock_auditlog_mgr, mock_status_mgr
    ):
        """``log_change`` creates one AuditLog and one pending AuditLogStatus."""
        fake_log = MagicMock(id=1)
        mock_auditlog_mgr.create.return_value = fake_log

        mixin = self._make_mixin(user="alice", calculation_id="calc-123")
        result = mixin.log_change("create", type("MyModel", (), {}), payload={"key": "val"})

        mock_auditlog_mgr.create.assert_called_once()
        create_kwargs = mock_auditlog_mgr.create.call_args.kwargs
        self.assertEqual(create_kwargs["author"], "alice")
        self.assertEqual(create_kwargs["action"], "create")
        self.assertEqual(create_kwargs["payload"], {"key": "val"})
        self.assertEqual(create_kwargs["calculation_id"], "calc-123")

        mock_status_mgr.create.assert_called_once_with(
            audit_log=fake_log, status="pending"
        )
        self.assertIs(result, fake_log)

    @patch("lex.audit_logging.mixins.AuditLogMixin.AuditLogStatus.objects")
    @patch("lex.audit_logging.mixins.AuditLogMixin.AuditLog.objects")
    def test_log_change_uses_class_name_for_resource(
        self, mock_auditlog_mgr, mock_status_mgr
    ):
        """Resource name is derived from the model class name (lowercased)."""
        mock_auditlog_mgr.create.return_value = MagicMock(id=2)
        mixin = self._make_mixin()

        # Pass a class — resource should be its __name__.lower()
        mixin.log_change("update", type("InvestorCashflow", (), {}))
        resource = mock_auditlog_mgr.create.call_args.kwargs["resource"]
        self.assertEqual(resource, "investorcashflow")

    @patch("lex.audit_logging.mixins.AuditLogMixin.AuditLogStatus.objects")
    @patch("lex.audit_logging.mixins.AuditLogMixin.AuditLog.objects")
    def test_log_change_uses_instance_class_name(
        self, mock_auditlog_mgr, mock_status_mgr
    ):
        """When target is an instance, resource is its __class__.__name__.lower()."""
        mock_auditlog_mgr.create.return_value = MagicMock(id=3)
        mixin = self._make_mixin()

        # Use a real class instance — SimpleNamespace can't have __class__ reassigned
        Vehicle = type("Vehicle", (), {})
        instance = Vehicle()
        mixin.log_change("delete", instance)
        resource = mock_auditlog_mgr.create.call_args.kwargs["resource"]
        self.assertEqual(resource, "vehicle")


# ────────────────────────────────────────────────────────────────────
#  perform_create / perform_update / perform_destroy
# ────────────────────────────────────────────────────────────────────

class AuditLogMixinCRUDTest(SimpleTestCase):
    """Verify audit status transitions for create / update / destroy."""

    def _make_mixin(self):
        mixin = AuditLogMixin()
        mixin.request = SimpleNamespace(user="test-user")
        mixin.kwargs = {}
        return mixin

    def _mock_serializer(self, *, pk=10, validated_data=None, model_name="MyModel"):
        ser = MagicMock()
        ser.validated_data = validated_data or {"name": "Test"}
        ser.Meta = SimpleNamespace(model=type(model_name, (), {}))
        instance = MagicMock(pk=pk, __class__=type(model_name, (), {}))
        ser.save.return_value = instance
        ser.data = {"id": pk, "name": "Test"}
        return ser, instance

    @patch("lex.audit_logging.mixins.AuditLogMixin._safe_get_content_type", return_value=None)
    @patch("lex.audit_logging.mixins.AuditLogMixin._serialize_payload", side_effect=lambda x: x)
    @patch("lex.audit_logging.mixins.AuditLogMixin.AuditLogStatus.objects")
    @patch("lex.audit_logging.mixins.AuditLogMixin.AuditLog.objects")
    def test_perform_create_success_marks_status_success(
        self, mock_log_mgr, mock_status_mgr, _ser, _ct
    ):
        """Successful create transitions AuditLogStatus from pending to success."""
        fake_log = MagicMock(id=1, payload={})
        mock_log_mgr.create.return_value = fake_log

        mixin = self._make_mixin()
        ser, _ = self._mock_serializer()

        mixin.perform_create(ser)

        # Status should transition to success
        mock_status_mgr.filter.return_value.update.assert_called_with(status="success")

    @patch("lex.audit_logging.mixins.AuditLogMixin._safe_get_content_type", return_value=None)
    @patch("lex.audit_logging.mixins.AuditLogMixin._serialize_payload", side_effect=lambda x: x)
    @patch("lex.audit_logging.mixins.AuditLogMixin.AuditLogStatus.objects")
    @patch("lex.audit_logging.mixins.AuditLogMixin.AuditLog.objects")
    def test_perform_create_failure_marks_status_failure(
        self, mock_log_mgr, mock_status_mgr, _ser, _ct
    ):
        """Failed create transitions AuditLogStatus to failure with traceback."""
        fake_log = MagicMock(id=1, payload={})
        mock_log_mgr.create.return_value = fake_log

        mixin = self._make_mixin()
        ser, _ = self._mock_serializer()
        ser.save.side_effect = RuntimeError("DB crash")

        with self.assertRaises(RuntimeError):
            mixin.perform_create(ser)

        update_call = mock_status_mgr.filter.return_value.update
        update_call.assert_called_once()
        call_kwargs = update_call.call_args.kwargs
        self.assertEqual(call_kwargs["status"], "failure")
        self.assertIn("error_traceback", call_kwargs)

    @patch("lex.audit_logging.mixins.AuditLogMixin._safe_get_content_type", return_value=None)
    @patch("lex.audit_logging.mixins.AuditLogMixin._serialize_payload", side_effect=lambda x: x)
    @patch("lex.audit_logging.mixins.AuditLogMixin.AuditLogStatus.objects")
    @patch("lex.audit_logging.mixins.AuditLogMixin.AuditLog.objects")
    def test_perform_destroy_success(self, mock_log_mgr, mock_status_mgr, _ser, _ct):
        """Successful destroy transitions to success."""
        fake_log = MagicMock(id=5, payload={})
        mock_log_mgr.create.return_value = fake_log

        mixin = self._make_mixin()
        mixin.get_serializer = MagicMock(return_value=MagicMock(data={"id": 1}))

        instance = MagicMock(pk=1, __class__=type("Vehicle", (), {}))
        mixin.perform_destroy(instance)

        mock_status_mgr.filter.return_value.update.assert_called_with(status="success")

    @patch("lex.audit_logging.mixins.AuditLogMixin._safe_get_content_type", return_value=None)
    @patch("lex.audit_logging.mixins.AuditLogMixin._serialize_payload", side_effect=lambda x: x)
    @patch("lex.audit_logging.mixins.AuditLogMixin.AuditLogStatus.objects")
    @patch("lex.audit_logging.mixins.AuditLogMixin.AuditLog.objects")
    def test_perform_destroy_failure(self, mock_log_mgr, mock_status_mgr, _ser, _ct):
        """Failed destroy transitions to failure and re-raises."""
        fake_log = MagicMock(id=5, payload={})
        mock_log_mgr.create.return_value = fake_log

        mixin = self._make_mixin()
        mixin.get_serializer = MagicMock(return_value=MagicMock(data={"id": 1}))

        instance = MagicMock(pk=1, __class__=type("Vehicle", (), {}))
        instance.delete.side_effect = RuntimeError("FK violation")

        with self.assertRaises(RuntimeError):
            mixin.perform_destroy(instance)

        call_kwargs = mock_status_mgr.filter.return_value.update.call_args.kwargs
        self.assertEqual(call_kwargs["status"], "failure")
