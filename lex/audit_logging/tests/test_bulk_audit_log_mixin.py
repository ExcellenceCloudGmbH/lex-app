from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase

from lex.audit_logging.mixins.BulkAuditLogMixin import BulkAuditLogMixin


class _FakeQuerySet(list):
    def delete(self):
        raise RuntimeError("bulk delete failed")


class BulkAuditLogMixinTest(SimpleTestCase):
    def _make_mixin(self):
        mixin = BulkAuditLogMixin()
        mixin.request = SimpleNamespace(user="test-user")
        mixin.kwargs = {}
        mixin.get_serializer = MagicMock(
            side_effect=lambda instance: MagicMock(data={"id": instance.pk, "name": f"item-{instance.pk}"})
        )
        return mixin

    @patch("lex.audit_logging.mixins.BulkAuditLogMixin.transaction.get_connection")
    @patch("lex.audit_logging.mixins.BulkAuditLogMixin._safe_get_content_type", return_value=None)
    @patch("lex.audit_logging.mixins.BulkAuditLogMixin._serialize_payload", side_effect=lambda x: x)
    @patch("lex.audit_logging.mixins.BulkAuditLogMixin.AuditLogStatus.objects")
    @patch("lex.audit_logging.mixins.BulkAuditLogMixin.AuditLog.objects")
    def test_perform_bulk_update_failure_queues_all_fallbacks_when_atomic(
        self,
        mock_log_mgr,
        mock_status_mgr,
        _serialize,
        _content_type,
        mock_get_connection,
    ):
        mixin = self._make_mixin()
        instance_one = MagicMock(pk=1, __class__=type("Vehicle", (), {}))
        instance_two = MagicMock(pk=2, __class__=type("Vehicle", (), {}))
        serializer = MagicMock()
        serializer.instance = [instance_one, instance_two]
        serializer.validated_data = [{"name": "A"}, {"name": "B"}]
        serializer.save.side_effect = RuntimeError("bulk update failed")
        mock_log_mgr.create.side_effect = [MagicMock(id=1), MagicMock(id=2)]
        mock_get_connection.return_value = SimpleNamespace(in_atomic_block=True)

        with self.assertRaises(RuntimeError):
            mixin.perform_bulk_update(serializer)

        pending_failed_audit_logs = getattr(mixin, "_pending_failed_audit_logs")
        self.assertEqual(len(pending_failed_audit_logs), 2)
        self.assertEqual(pending_failed_audit_logs[0]["action"], "update")
        self.assertEqual(pending_failed_audit_logs[1]["action"], "update")
        self.assertEqual(pending_failed_audit_logs[0]["payload"]["id"], 1)
        self.assertEqual(pending_failed_audit_logs[1]["payload"]["id"], 2)
        self.assertFalse(mixin.has_logged_failure_audit())
        self.assertEqual(mock_status_mgr.filter.return_value.update.call_count, 2)

    @patch("lex.audit_logging.mixins.BulkAuditLogMixin.transaction.get_connection")
    @patch("lex.audit_logging.mixins.BulkAuditLogMixin._safe_get_content_type", return_value=None)
    @patch("lex.audit_logging.mixins.BulkAuditLogMixin._serialize_payload", side_effect=lambda x: x)
    @patch("lex.audit_logging.mixins.BulkAuditLogMixin.AuditLogStatus.objects")
    @patch("lex.audit_logging.mixins.BulkAuditLogMixin.AuditLog.objects")
    def test_perform_bulk_destroy_failure_queues_all_fallbacks_when_atomic(
        self,
        mock_log_mgr,
        mock_status_mgr,
        _serialize,
        _content_type,
        mock_get_connection,
    ):
        mixin = self._make_mixin()
        instance_one = MagicMock(pk=11, __class__=type("Vehicle", (), {}))
        instance_two = MagicMock(pk=12, __class__=type("Vehicle", (), {}))
        queryset = _FakeQuerySet([instance_one, instance_two])
        mock_log_mgr.create.side_effect = [MagicMock(id=11), MagicMock(id=12)]
        mock_get_connection.return_value = SimpleNamespace(in_atomic_block=True)

        with self.assertRaises(RuntimeError):
            mixin.perform_bulk_destroy(queryset)

        pending_failed_audit_logs = getattr(mixin, "_pending_failed_audit_logs")
        self.assertEqual(len(pending_failed_audit_logs), 2)
        self.assertEqual(pending_failed_audit_logs[0]["action"], "delete")
        self.assertEqual(pending_failed_audit_logs[1]["action"], "delete")
        self.assertFalse(mixin.has_logged_failure_audit())
        self.assertEqual(mock_status_mgr.filter.return_value.update.call_count, 2)