"""
Tests for ``InitialDataAuditLogger`` — audit trail for bulk data uploads.

**What is tested:**

    • ``generate_calculation_id()`` produces unique timestamped IDs.
    • ``log_object_creation()`` creates AuditLog + pending AuditLogStatus.
    • ``log_object_update()`` creates AuditLog + pending AuditLogStatus.
    • ``log_object_deletion()`` creates AuditLog + pending AuditLogStatus.
    • ``mark_operation_success()`` transitions pending → success.
    • ``mark_operation_failure()`` transitions pending → failure with traceback.
    • ``finalize_batch()`` resolves all remaining pending statuses (safety net).
    • All audit methods swallow exceptions to avoid breaking the data upload.

**Why this matters:**

    Initial data upload is the first thing customers do when setting up a new
    project.  Every row created/updated/deleted during upload must be audited.
    If the audit logger crashes, the entire upload fails.  If pending statuses
    are not resolved, the dashboard shows phantom "in-progress" operations.

**How to run:**

    .. code-block:: bash

        lex test lex.audit_logging.tests.test_initial_data_audit_logger --verbosity=2
"""

from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase
from lex.audit_logging.utils.InitialDataAuditLogger import InitialDataAuditLogger


class CalculationIdTest(SimpleTestCase):
    """Verify unique calculation ID generation."""

    def test_format_matches_pattern(self):
        """ID format: ``initial_data_upload_YYYYMMDD_HHMMSS_<uuid8>``."""
        logger = InitialDataAuditLogger()
        cid = logger.generate_calculation_id()
        pattern = r"^initial_data_upload_\d{8}_\d{6}_[a-f0-9]{8}$"
        self.assertRegex(cid, pattern)

    def test_ids_are_unique(self):
        """Two consecutive calls produce different IDs."""
        logger = InitialDataAuditLogger()
        id1 = logger.generate_calculation_id()
        id2 = logger.generate_calculation_id()
        self.assertNotEqual(id1, id2)


class LogObjectCreationTest(SimpleTestCase):
    """Verify ``log_object_creation`` audit logging."""
    databases = {"default"}

    @patch("lex.audit_logging.utils.InitialDataAuditLogger.AuditLogStatus.objects")
    @patch("lex.audit_logging.utils.InitialDataAuditLogger.AuditLog.objects")
    @patch("lex.audit_logging.utils.InitialDataAuditLogger._serialize_payload", side_effect=lambda x: x)
    def test_creates_audit_log_and_pending_status(self, _ser, mock_log_mgr, mock_status_mgr):
        """Logging a creation produces AuditLog(create) + AuditLogStatus(pending)."""
        fake_log = MagicMock(id=1)
        mock_log_mgr.create.return_value = fake_log

        logger = InitialDataAuditLogger()
        model_cls = type("Investor", (), {"__name__": "Investor"})
        result = logger.log_object_creation(model_cls, {"name": "Alice"}, calculation_id="cid-1")

        self.assertIs(result, fake_log)
        create_kwargs = mock_log_mgr.create.call_args.kwargs
        self.assertEqual(create_kwargs["action"], "create")
        self.assertEqual(create_kwargs["resource"], "investor")
        self.assertEqual(create_kwargs["author"], "system (initial_data_upload)")
        mock_status_mgr.create.assert_called_once_with(audit_log=fake_log, status="pending")
        self.assertIn(1, logger._logged_ids)

    @patch("lex.audit_logging.utils.InitialDataAuditLogger.AuditLogStatus.objects")
    @patch("lex.audit_logging.utils.InitialDataAuditLogger.AuditLog.objects")
    @patch("lex.audit_logging.utils.InitialDataAuditLogger._serialize_payload", side_effect=lambda x: x)
    def test_tag_is_included_in_payload(self, _ser, mock_log_mgr, mock_status_mgr):
        """When a tag is provided, it appears in the payload."""
        mock_log_mgr.create.return_value = MagicMock(id=2)
        logger = InitialDataAuditLogger()
        model_cls = type("Vehicle", (), {"__name__": "Vehicle"})

        logger.log_object_creation(model_cls, {"name": "V1"}, tag="vehicle_alpha")

        payload = mock_log_mgr.create.call_args.kwargs["payload"]
        self.assertEqual(payload.get("_audit_tag"), "vehicle_alpha")

    @patch("lex.audit_logging.utils.InitialDataAuditLogger.AuditLog.objects")
    def test_creation_swallows_exceptions(self, mock_log_mgr):
        """If the DB is down, log_object_creation returns None (no crash)."""
        mock_log_mgr.create.side_effect = RuntimeError("DB down")
        logger = InitialDataAuditLogger()
        result = logger.log_object_creation(type("X", (), {"__name__": "X"}), {})
        self.assertIsNone(result)


class LogObjectUpdateTest(SimpleTestCase):
    """Verify ``log_object_update`` audit logging."""
    databases = {"default"}

    @patch("lex.audit_logging.utils.InitialDataAuditLogger.AuditLogStatus.objects")
    @patch("lex.audit_logging.utils.InitialDataAuditLogger.AuditLog.objects")
    @patch("lex.audit_logging.utils.InitialDataAuditLogger._serialize_payload", side_effect=lambda x: x)
    def test_creates_update_audit_log(self, _ser, mock_log_mgr, mock_status_mgr):
        """Logging an update produces AuditLog(update) + pending status."""
        fake_log = MagicMock(id=3)
        mock_log_mgr.create.return_value = fake_log

        logger = InitialDataAuditLogger()
        model_cls = type("Target", (), {"__name__": "Target"})
        instance = MagicMock(pk=42)

        result = logger.log_object_update(model_cls, instance, {"nav": 100.0})
        self.assertIs(result, fake_log)
        create_kwargs = mock_log_mgr.create.call_args.kwargs
        self.assertEqual(create_kwargs["action"], "update")
        # Instance PK should be injected into payload
        self.assertEqual(create_kwargs["payload"]["id"], 42)


class LogObjectDeletionTest(SimpleTestCase):
    """Verify ``log_object_deletion`` audit logging."""
    databases = {"default"}

    @patch("lex.audit_logging.utils.InitialDataAuditLogger.AuditLogStatus.objects")
    @patch("lex.audit_logging.utils.InitialDataAuditLogger.AuditLog.objects")
    @patch("lex.audit_logging.utils.InitialDataAuditLogger.generic_instance_payload", return_value={"id": 7})
    def test_creates_delete_audit_log(self, _payload, mock_log_mgr, mock_status_mgr):
        """Logging a deletion produces AuditLog(delete) + pending status."""
        fake_log = MagicMock(id=4)
        mock_log_mgr.create.return_value = fake_log

        logger = InitialDataAuditLogger()
        instance = MagicMock(__class__=type("Cashflow", (), {}), pk=7)
        result = logger.log_object_deletion(instance, {"pk": 7})

        self.assertIs(result, fake_log)
        create_kwargs = mock_log_mgr.create.call_args.kwargs
        self.assertEqual(create_kwargs["action"], "delete")
        self.assertEqual(create_kwargs["resource"], "cashflow")


class MarkOperationTest(SimpleTestCase):
    """Verify ``mark_operation_success`` and ``mark_operation_failure``."""

    @patch("lex.audit_logging.utils.InitialDataAuditLogger.AuditLogStatus.objects")
    def test_mark_success(self, mock_status_mgr):
        """Success marking transitions status to 'success'."""
        mock_status_mgr.filter.return_value.update.return_value = 1
        logger = InitialDataAuditLogger()
        audit_log = MagicMock(id=10, calculation_id="cid")

        logger.mark_operation_success(audit_log)
        mock_status_mgr.filter.assert_called_once_with(audit_log=audit_log)
        mock_status_mgr.filter.return_value.update.assert_called_once_with(
            status="success", error_traceback=None
        )

    @patch("lex.audit_logging.utils.InitialDataAuditLogger.AuditLogStatus.objects")
    def test_mark_failure(self, mock_status_mgr):
        """Failure marking transitions status to 'failure' with traceback."""
        mock_status_mgr.filter.return_value.update.return_value = 1
        logger = InitialDataAuditLogger()
        audit_log = MagicMock(id=11, calculation_id="cid")

        logger.mark_operation_failure(audit_log, "stack trace here")
        mock_status_mgr.filter.return_value.update.assert_called_once_with(
            status="failure", error_traceback="stack trace here"
        )

    def test_mark_success_noop_for_none(self):
        """Marking None as success does not crash."""
        logger = InitialDataAuditLogger()
        logger.mark_operation_success(None)  # should not raise

    def test_mark_failure_noop_for_none(self):
        """Marking None as failure does not crash."""
        logger = InitialDataAuditLogger()
        logger.mark_operation_failure(None, "err")  # should not raise


class FinalizeBatchTest(SimpleTestCase):
    """Verify ``finalize_batch`` safety-net logic."""

    @patch("lex.audit_logging.utils.InitialDataAuditLogger.AuditLogStatus.objects")
    def test_finalize_batch_resolves_pending_to_success(self, mock_status_mgr):
        """When no failure_error, pending statuses are resolved as success."""
        mock_status_mgr.filter.return_value.update.return_value = 2
        mock_status_mgr.filter.return_value.values.return_value.annotate.return_value = [
            {"status": "success", "count": 5},
        ]

        logger = InitialDataAuditLogger()
        logger._logged_ids = [1, 2, 3, 4, 5]

        summary = logger.finalize_batch()
        self.assertEqual(summary["total_audit_logs"], 5)
        self.assertEqual(summary["pending_resolved"], 2)

    @patch("lex.audit_logging.utils.InitialDataAuditLogger.AuditLogStatus.objects")
    def test_finalize_batch_resolves_pending_to_failure(self, mock_status_mgr):
        """When failure_error is set, pending statuses are marked failure."""
        mock_status_mgr.filter.return_value.update.return_value = 3
        mock_status_mgr.filter.return_value.values.return_value.annotate.return_value = [
            {"status": "failure", "count": 3},
        ]

        logger = InitialDataAuditLogger()
        logger._logged_ids = [1, 2, 3]

        summary = logger.finalize_batch(failure_error="upload aborted")
        self.assertEqual(summary["pending_resolved"], 3)

    def test_finalize_batch_empty_session(self):
        """Finalizing with no operations returns zeroed summary."""
        logger = InitialDataAuditLogger()
        summary = logger.finalize_batch()
        self.assertEqual(summary["total_audit_logs"], 0)
