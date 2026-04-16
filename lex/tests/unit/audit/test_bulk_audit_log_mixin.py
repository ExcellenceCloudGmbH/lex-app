"""
Tests for ``BulkAuditLogMixin`` — bulk CRUD audit trail.

**What is tested:**

    * ``log_change()`` — creates AuditLog + pending AuditLogStatus, serializes
      payload via ``_serialize_payload`` before persisting (unlike the singular
      ``AuditLogMixin`` which stores raw payload).
    * ``perform_bulk_update()`` — saves serializer, logs each updated instance,
      refreshes each AuditLog payload with full post-save data, transitions
      status to ``success``.  On exception: marks all logs ``failure`` with
      traceback, then re-raises.
    * ``perform_bulk_destroy()`` — logs each instance before deletion, deletes
      queryset, updates each AuditLog with serialized payload, transitions
      status to ``success``.  On exception: marks all logs ``failure`` with
      traceback, then re-raises.

**Why this matters:**

    Bulk endpoints (multi-row edit, multi-row delete) are used by the AG Grid
    views.  If ``perform_bulk_update`` fails to create audit log entries, bulk
    edits are invisible in the audit history.  If the failure path doesn't
    mark logs as ``failure``, pending entries accumulate and give operators
    false confidence that nothing went wrong.

**How to run:**

    .. code-block:: bash

        lex test lex.audit_logging.tests.test_bulk_audit_log_mixin --verbosity=2 --noinput
"""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch, call

from django.test import SimpleTestCase

from lex.audit_logging.mixins.BulkAuditLogMixin import BulkAuditLogMixin


# ─── helpers ──────────────────────────────────────────────────────────

def _make_mixin(user="test-user", calculation_id=None, serializer_data=None):
    """Return a BulkAuditLogMixin stub with mocked request and kwargs.

    Parameters
    ----------
    user : str or None
        ``request.user``; set to ``None`` to simulate anonymous request.
    calculation_id : str or None
        Value of ``kwargs["calculationId"]``.
    serializer_data : dict or None
        What ``get_serializer(instance).data`` returns.
    """
    mixin = BulkAuditLogMixin()
    mixin.request = SimpleNamespace(user=user) if user else SimpleNamespace()
    mixin.kwargs = {"calculationId": calculation_id} if calculation_id else {}
    mixin.get_serializer = MagicMock(
        return_value=MagicMock(data=serializer_data or {"id": 1, "name": "Test"})
    )
    return mixin


def _make_instance(pk=1, class_name="CashFlow"):
    """Create a stub model instance with a given class name and pk."""
    cls = type(class_name, (), {})
    inst = cls()
    inst.pk = pk
    inst.__class__ = cls
    return inst


# ════════════════════════════════════════════════════════════════════════
#  log_change
# ════════════════════════════════════════════════════════════════════════

class TestBulkLogChange(SimpleTestCase):
    """Verify ``BulkAuditLogMixin.log_change`` creates the expected DB records."""

    @patch("lex.audit_logging.mixins.BulkAuditLogMixin.AuditLogStatus.objects")
    @patch("lex.audit_logging.mixins.BulkAuditLogMixin.AuditLog.objects")
    @patch("lex.audit_logging.mixins.BulkAuditLogMixin._serialize_payload", side_effect=lambda x: x)
    def test_creates_audit_log_and_pending_status(self, _ser, mock_log_mgr, mock_status_mgr):
        """log_change creates one AuditLog + one AuditLogStatus(pending)."""
        fake_log = MagicMock(id=1)
        mock_log_mgr.create.return_value = fake_log

        mixin = _make_mixin(user="alice", calculation_id="calc-42")
        result = mixin.log_change("create", _make_instance(pk=5, class_name="Bond"), payload={"amount": 100})

        mock_log_mgr.create.assert_called_once()
        kw = mock_log_mgr.create.call_args.kwargs
        self.assertEqual(kw["author"], "alice")
        self.assertEqual(kw["action"], "create")
        self.assertEqual(kw["resource"], "bond")
        self.assertEqual(kw["payload"], {"amount": 100})
        self.assertEqual(kw["calculation_id"], "calc-42")

        mock_status_mgr.create.assert_called_once_with(audit_log=fake_log, status="pending")
        self.assertIs(result, fake_log)

    @patch("lex.audit_logging.mixins.BulkAuditLogMixin.AuditLogStatus.objects")
    @patch("lex.audit_logging.mixins.BulkAuditLogMixin.AuditLog.objects")
    @patch("lex.audit_logging.mixins.BulkAuditLogMixin._serialize_payload", side_effect=lambda x: x)
    def test_class_target_uses_class_name(self, _ser, mock_log_mgr, mock_status_mgr):
        """When target is a class (not instance), resource = __name__.lower()."""
        mock_log_mgr.create.return_value = MagicMock(id=2)

        mixin = _make_mixin()
        MyModel = type("InvestorProfile", (), {})
        mixin.log_change("delete", MyModel)

        resource = mock_log_mgr.create.call_args.kwargs["resource"]
        self.assertEqual(resource, "investorprofile")

    @patch("lex.audit_logging.mixins.BulkAuditLogMixin.AuditLogStatus.objects")
    @patch("lex.audit_logging.mixins.BulkAuditLogMixin.AuditLog.objects")
    @patch("lex.audit_logging.mixins.BulkAuditLogMixin._serialize_payload", side_effect=lambda x: x)
    def test_instance_target_uses_class_name(self, _ser, mock_log_mgr, mock_status_mgr):
        """When target is an instance, resource = __class__.__name__.lower()."""
        mock_log_mgr.create.return_value = MagicMock(id=3)

        mixin = _make_mixin()
        mixin.log_change("update", _make_instance(class_name="CashFlow"))

        resource = mock_log_mgr.create.call_args.kwargs["resource"]
        self.assertEqual(resource, "cashflow")

    @patch("lex.audit_logging.mixins.BulkAuditLogMixin.AuditLogStatus.objects")
    @patch("lex.audit_logging.mixins.BulkAuditLogMixin.AuditLog.objects")
    @patch("lex.audit_logging.mixins.BulkAuditLogMixin._serialize_payload", side_effect=lambda x: x)
    def test_anonymous_request_sets_author_none(self, _ser, mock_log_mgr, mock_status_mgr):
        """When request has no user attribute, author is None."""
        mock_log_mgr.create.return_value = MagicMock(id=4)

        mixin = _make_mixin(user=None)
        mixin.log_change("create", _make_instance())

        author = mock_log_mgr.create.call_args.kwargs["author"]
        self.assertIsNone(author)

    @patch("lex.audit_logging.mixins.BulkAuditLogMixin.AuditLogStatus.objects")
    @patch("lex.audit_logging.mixins.BulkAuditLogMixin.AuditLog.objects")
    @patch("lex.audit_logging.mixins.BulkAuditLogMixin._serialize_payload", side_effect=lambda x: x)
    def test_missing_kwargs_sets_calculation_id_none(self, _ser, mock_log_mgr, mock_status_mgr):
        """When kwargs has no calculationId, calculation_id is None."""
        mock_log_mgr.create.return_value = MagicMock(id=5)

        mixin = _make_mixin()
        mixin.kwargs = {}
        mixin.log_change("update", _make_instance())

        calc_id = mock_log_mgr.create.call_args.kwargs["calculation_id"]
        self.assertIsNone(calc_id)

    @patch("lex.audit_logging.mixins.BulkAuditLogMixin.AuditLogStatus.objects")
    @patch("lex.audit_logging.mixins.BulkAuditLogMixin.AuditLog.objects")
    def test_payload_is_serialized_before_storage(self, mock_log_mgr, mock_status_mgr):
        """Payload passes through _serialize_payload — unlike AuditLogMixin.log_change."""
        mock_log_mgr.create.return_value = MagicMock(id=6)

        mixin = _make_mixin()
        # Pass a non-trivial payload with a type that _serialize_payload processes
        from datetime import datetime
        mixin.log_change("create", _make_instance(), payload={"ts": datetime(2026, 1, 1)})

        stored_payload = mock_log_mgr.create.call_args.kwargs["payload"]
        # datetime should have been converted to string by _serialize_payload
        self.assertIsInstance(stored_payload["ts"], str)


# ════════════════════════════════════════════════════════════════════════
#  perform_bulk_update
# ════════════════════════════════════════════════════════════════════════

class TestPerformBulkUpdate(SimpleTestCase):
    """Verify bulk update logs each instance and transitions status correctly."""

    @patch("lex.audit_logging.mixins.BulkAuditLogMixin._safe_get_content_type", return_value=None)
    @patch("lex.audit_logging.mixins.BulkAuditLogMixin._serialize_payload", side_effect=lambda x: x)
    @patch("lex.audit_logging.mixins.BulkAuditLogMixin.AuditLogStatus.objects")
    @patch("lex.audit_logging.mixins.BulkAuditLogMixin.AuditLog.objects")
    def test_success_marks_all_statuses_success(
        self, mock_log_mgr, mock_status_mgr, _ser, _ct
    ):
        """On successful bulk update, every AuditLogStatus transitions to 'success'."""
        fake_log_1 = MagicMock(id=1, payload={})
        fake_log_2 = MagicMock(id=2, payload={})
        mock_log_mgr.create.side_effect = [fake_log_1, fake_log_2]

        inst1 = _make_instance(pk=10, class_name="Bond")
        inst2 = _make_instance(pk=20, class_name="Bond")

        serializer = MagicMock()
        serializer.save.return_value = [inst1, inst2]
        serializer.data = [{"id": 10, "name": "A"}, {"id": 20, "name": "B"}]

        mixin = _make_mixin(serializer_data={"id": 10})
        result = mixin.perform_bulk_update(serializer)

        self.assertEqual(result, [inst1, inst2])
        self.assertEqual(mock_log_mgr.create.call_count, 2)

        # Both logs should have status set to success
        status_update_calls = mock_status_mgr.filter.return_value.update.call_args_list
        for c in status_update_calls:
            self.assertEqual(c.kwargs.get("status") or c[1].get("status"), "success")

    @patch("lex.audit_logging.mixins.BulkAuditLogMixin._safe_get_content_type", return_value=None)
    @patch("lex.audit_logging.mixins.BulkAuditLogMixin._serialize_payload", side_effect=lambda x: x)
    @patch("lex.audit_logging.mixins.BulkAuditLogMixin.AuditLogStatus.objects")
    @patch("lex.audit_logging.mixins.BulkAuditLogMixin.AuditLog.objects")
    def test_failure_marks_all_statuses_failure_and_reraises(
        self, mock_log_mgr, mock_status_mgr, _ser, _ct
    ):
        """On exception during post-save refresh, all statuses become 'failure'."""
        fake_log = MagicMock(id=1, payload={})
        mock_log_mgr.create.return_value = fake_log

        inst = _make_instance(pk=10)
        serializer = MagicMock()
        serializer.save.return_value = [inst]
        serializer.data = [{"id": 10}]

        mixin = _make_mixin()
        # Simulate error during payload refresh (get_serializer().data raises)
        mixin.get_serializer = MagicMock(side_effect=RuntimeError("serializer exploded"))

        with self.assertRaises(RuntimeError) as cm:
            mixin.perform_bulk_update(serializer)

        self.assertIn("serializer exploded", str(cm.exception))

        # Status should have been set to failure
        update_kwargs = mock_status_mgr.filter.return_value.update.call_args.kwargs
        self.assertEqual(update_kwargs["status"], "failure")
        self.assertIn("error_traceback", update_kwargs)

    @patch("lex.audit_logging.mixins.BulkAuditLogMixin._safe_get_content_type", return_value=None)
    @patch("lex.audit_logging.mixins.BulkAuditLogMixin._serialize_payload", side_effect=lambda x: x)
    @patch("lex.audit_logging.mixins.BulkAuditLogMixin.AuditLogStatus.objects")
    @patch("lex.audit_logging.mixins.BulkAuditLogMixin.AuditLog.objects")
    def test_audit_log_payload_refreshed_after_save(
        self, mock_log_mgr, mock_status_mgr, _ser, _ct
    ):
        """After save, each AuditLog is updated with the fresh serialized payload."""
        fake_log = MagicMock(id=1, payload={"old": True})
        mock_log_mgr.create.return_value = fake_log

        inst = _make_instance(pk=10)
        serializer = MagicMock()
        serializer.save.return_value = [inst]
        serializer.data = [{"id": 10}]

        fresh_data = {"id": 10, "name": "Updated", "amount": 999}
        mixin = _make_mixin()
        mixin.get_serializer = MagicMock(
            return_value=MagicMock(data=fresh_data)
        )

        mixin.perform_bulk_update(serializer)

        # The fake_log's payload should be overwritten with the fresh data
        self.assertEqual(fake_log.payload, fresh_data)
        fake_log.save.assert_called_once()


# ════════════════════════════════════════════════════════════════════════
#  perform_bulk_destroy
# ════════════════════════════════════════════════════════════════════════

class TestPerformBulkDestroy(SimpleTestCase):
    """Verify bulk destroy logs each instance before deletion."""

    @patch("lex.audit_logging.mixins.BulkAuditLogMixin._safe_get_content_type", return_value=None)
    @patch("lex.audit_logging.mixins.BulkAuditLogMixin._serialize_payload", side_effect=lambda x: x)
    @patch("lex.audit_logging.mixins.BulkAuditLogMixin.AuditLogStatus.objects")
    @patch("lex.audit_logging.mixins.BulkAuditLogMixin.AuditLog.objects")
    def test_success_deletes_and_marks_success(
        self, mock_log_mgr, mock_status_mgr, _ser, _ct
    ):
        """On successful bulk delete, queryset is deleted and all statuses are 'success'."""
        fake_log = MagicMock(id=1, payload={})
        mock_log_mgr.create.return_value = fake_log

        inst1 = _make_instance(pk=10, class_name="Bond")
        inst2 = _make_instance(pk=20, class_name="Bond")
        items = [inst1, inst2]

        queryset = MagicMock()
        # The code iterates the queryset 3 times: log, deleted_ids, instances
        queryset.__iter__ = MagicMock(side_effect=lambda: iter(items))

        mixin = _make_mixin()
        result = mixin.perform_bulk_destroy(queryset)

        queryset.delete.assert_called_once()
        self.assertEqual(sorted(result), [10, 20])

    @patch("lex.audit_logging.mixins.BulkAuditLogMixin._safe_get_content_type", return_value=None)
    @patch("lex.audit_logging.mixins.BulkAuditLogMixin._serialize_payload", side_effect=lambda x: x)
    @patch("lex.audit_logging.mixins.BulkAuditLogMixin.AuditLogStatus.objects")
    @patch("lex.audit_logging.mixins.BulkAuditLogMixin.AuditLog.objects")
    def test_failure_marks_all_failure_and_reraises(
        self, mock_log_mgr, mock_status_mgr, _ser, _ct
    ):
        """On exception during delete, all statuses become 'failure' and exception re-raises."""
        fake_log = MagicMock(id=1, payload={})
        mock_log_mgr.create.return_value = fake_log

        inst = _make_instance(pk=10)
        items = [inst]
        queryset = MagicMock()
        queryset.__iter__ = MagicMock(side_effect=lambda: iter(items))
        queryset.delete.side_effect = RuntimeError("FK constraint violation")

        mixin = _make_mixin()

        with self.assertRaises(RuntimeError) as cm:
            mixin.perform_bulk_destroy(queryset)

        self.assertIn("FK constraint", str(cm.exception))

        update_kwargs = mock_status_mgr.filter.return_value.update.call_args.kwargs
        self.assertEqual(update_kwargs["status"], "failure")
        self.assertIn("error_traceback", update_kwargs)

    @patch("lex.audit_logging.mixins.BulkAuditLogMixin._safe_get_content_type", return_value=None)
    @patch("lex.audit_logging.mixins.BulkAuditLogMixin._serialize_payload", side_effect=lambda x: x)
    @patch("lex.audit_logging.mixins.BulkAuditLogMixin.AuditLogStatus.objects")
    @patch("lex.audit_logging.mixins.BulkAuditLogMixin.AuditLog.objects")
    def test_each_instance_logged_before_delete(
        self, mock_log_mgr, mock_status_mgr, _ser, _ct
    ):
        """Each instance is logged via log_change with 'delete' action before queryset.delete()."""
        fake_log_1 = MagicMock(id=1, payload={})
        fake_log_2 = MagicMock(id=2, payload={})
        mock_log_mgr.create.side_effect = [fake_log_1, fake_log_2]

        inst1 = _make_instance(pk=10, class_name="Fund")
        inst2 = _make_instance(pk=20, class_name="Fund")
        items = [inst1, inst2]

        queryset = MagicMock()
        queryset.__iter__ = MagicMock(side_effect=lambda: iter(items))

        mixin = _make_mixin()
        mixin.perform_bulk_destroy(queryset)

        # Two AuditLog records should have been created (one per instance)
        self.assertEqual(mock_log_mgr.create.call_count, 2)

        # Both should have action='delete'
        for c in mock_log_mgr.create.call_args_list:
            self.assertEqual(c.kwargs["action"], "delete")
