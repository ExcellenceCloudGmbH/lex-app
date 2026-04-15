"""
Tests for ``AuditLogMixin.perform_update`` and audit data correctness.

Why this matters
----------------
``perform_update`` is the most-exercised audit path — every time a user edits
a record in the UI, this method:

1. Creates an AuditLog with the validated data as initial payload
2. Saves the serializer (which may trigger calculation hooks)
3. **Re-serializes** the saved instance to capture post-save changes
   (auto-fields, calculated fields, timestamps)
4. Updates the AuditLog payload with the re-serialized data
5. Sets AuditLogStatus to ``success`` or ``failure``

This re-serialization step is unique to ``perform_update`` — create and
destroy don't do it. Without testing this, the audit log could show stale
pre-save data instead of the actual persisted state.

What is verified
----------------
* Initial AuditLog is created with action="update" and initial payload
* The serializer is saved via ``_save_with_retry``
* After save, ``get_serializer(instance).data`` is called to re-serialize
* The re-serialized payload replaces the initial payload on the AuditLog
* content_type and object_id are set on the AuditLog
* AuditLogStatus transitions to ``success``
* On failure: status transitions to ``failure`` with traceback, exception re-raised

How to run
----------
.. code-block:: bash

    python -m django test lex.audit_logging.tests.test_audit_log_mixin_update \\
        --settings=lex.process_admin.tests.django_test_settings \\
        --verbosity=2 --noinput
"""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch, call

from django.test import SimpleTestCase

from lex.audit_logging.mixins.AuditLogMixin import AuditLogMixin


class TestPerformUpdate(SimpleTestCase):
    """
    Verify ``perform_update`` creates correct audit entries with
    re-serialized post-save data.
    """

    def _make_mixin(self, user="test-user", calculation_id=None):
        """Build a minimal AuditLogMixin stub."""
        mixin = AuditLogMixin()
        mixin.request = SimpleNamespace(user=user)
        mixin.kwargs = {"calculationId": calculation_id}
        return mixin

    def _mock_serializer(
        self,
        *,
        pk=10,
        validated_data=None,
        model_name="Vehicle",
        pre_save_data=None,
        post_save_data=None,
    ):
        """
        Build a serializer mock that returns different data before and after save.
        
        pre_save_data: The initial validated_data (what the user submitted)
        post_save_data: What get_serializer(instance).data returns after save
                        (may include auto-fields, timestamps, calculated values)
        """
        ser = MagicMock()
        ser.validated_data = validated_data or pre_save_data or {"name": "Updated Vehicle"}
        ser.Meta = SimpleNamespace(model=type(model_name, (), {}))

        instance = MagicMock(pk=pk)
        instance.__class__ = type(model_name, (), {})
        ser.save.return_value = instance

        # pre_save_data is the initial form data
        ser.data = pre_save_data or {"id": pk, "name": "Updated Vehicle"}

        return ser, instance, post_save_data or {
            "id": pk,
            "name": "Updated Vehicle",
            "updated_at": "2026-04-14T12:00:00Z",
            "is_calculated": "SUCCESS",
        }

    @patch("lex.audit_logging.mixins.AuditLogMixin._safe_get_content_type")
    @patch("lex.audit_logging.mixins.AuditLogMixin._serialize_payload", side_effect=lambda x: x)
    @patch("lex.audit_logging.mixins.AuditLogMixin.AuditLogStatus.objects")
    @patch("lex.audit_logging.mixins.AuditLogMixin.AuditLog.objects")
    def test_creates_audit_log_with_update_action(
        self, mock_log_mgr, mock_status_mgr, _ser, _ct
    ):
        """perform_update creates an AuditLog with action='update'."""
        _ct.return_value = None
        fake_log = MagicMock(id=1, payload={})
        mock_log_mgr.create.return_value = fake_log

        mixin = self._make_mixin(user="alice@example.com")
        ser, instance, post_data = self._mock_serializer()

        # Mock get_serializer to return post-save data
        post_serializer = MagicMock()
        post_serializer.data = post_data
        mixin.get_serializer = MagicMock(return_value=post_serializer)

        mixin.perform_update(ser)

        # AuditLog must be created with action='update'
        create_kwargs = mock_log_mgr.create.call_args.kwargs
        self.assertEqual(create_kwargs["action"], "update")
        self.assertEqual(create_kwargs["author"], "alice@example.com")

    @patch("lex.audit_logging.mixins.AuditLogMixin._safe_get_content_type")
    @patch("lex.audit_logging.mixins.AuditLogMixin._serialize_payload", side_effect=lambda x: x)
    @patch("lex.audit_logging.mixins.AuditLogMixin.AuditLogStatus.objects")
    @patch("lex.audit_logging.mixins.AuditLogMixin.AuditLog.objects")
    def test_re_serializes_payload_after_save(
        self, mock_log_mgr, mock_status_mgr, _ser, _ct
    ):
        """
        After saving, perform_update calls get_serializer(instance) to get
        the post-save data (which may include auto-updated timestamps,
        calculated fields, etc.) and updates the AuditLog payload with it.
        """
        _ct.return_value = None
        fake_log = MagicMock(id=1, payload={})
        mock_log_mgr.create.return_value = fake_log

        mixin = self._make_mixin()
        ser, instance, post_data = self._mock_serializer(
            pre_save_data={"name": "Before Save"},
            post_save_data={
                "name": "After Save",
                "updated_at": "2026-04-14T12:00:00Z",
            },
        )

        post_serializer = MagicMock()
        post_serializer.data = post_data
        mixin.get_serializer = MagicMock(return_value=post_serializer)

        mixin.perform_update(ser)

        # get_serializer must be called with the saved instance
        mixin.get_serializer.assert_called_once_with(instance)

        # AuditLog payload must be the RE-SERIALIZED post-save data
        # _serialize_payload is called with the post-save data
        self.assertEqual(fake_log.payload, post_data)

    @patch("lex.audit_logging.mixins.AuditLogMixin._safe_get_content_type")
    @patch("lex.audit_logging.mixins.AuditLogMixin._serialize_payload", side_effect=lambda x: x)
    @patch("lex.audit_logging.mixins.AuditLogMixin.AuditLogStatus.objects")
    @patch("lex.audit_logging.mixins.AuditLogMixin.AuditLog.objects")
    def test_sets_content_type_and_object_id(
        self, mock_log_mgr, mock_status_mgr, _ser, mock_ct
    ):
        """perform_update sets content_type and object_id on the AuditLog."""
        fake_ct = MagicMock()
        mock_ct.return_value = fake_ct
        fake_log = MagicMock(id=1, payload={})
        mock_log_mgr.create.return_value = fake_log

        mixin = self._make_mixin()
        ser, instance, post_data = self._mock_serializer(pk=42)
        post_serializer = MagicMock()
        post_serializer.data = post_data
        mixin.get_serializer = MagicMock(return_value=post_serializer)

        mixin.perform_update(ser)

        self.assertEqual(fake_log.content_type, fake_ct)
        self.assertEqual(fake_log.object_id, 42)
        fake_log.save.assert_called_once()

    @patch("lex.audit_logging.mixins.AuditLogMixin._safe_get_content_type", return_value=None)
    @patch("lex.audit_logging.mixins.AuditLogMixin._serialize_payload", side_effect=lambda x: x)
    @patch("lex.audit_logging.mixins.AuditLogMixin.AuditLogStatus.objects")
    @patch("lex.audit_logging.mixins.AuditLogMixin.AuditLog.objects")
    def test_success_marks_status_success(
        self, mock_log_mgr, mock_status_mgr, _ser, _ct
    ):
        """On success, AuditLogStatus transitions to 'success'."""
        fake_log = MagicMock(id=1, payload={})
        mock_log_mgr.create.return_value = fake_log

        mixin = self._make_mixin()
        ser, instance, post_data = self._mock_serializer()
        post_serializer = MagicMock()
        post_serializer.data = post_data
        mixin.get_serializer = MagicMock(return_value=post_serializer)

        mixin.perform_update(ser)

        mock_status_mgr.filter.return_value.update.assert_called_with(status="success")

    @patch("lex.audit_logging.mixins.AuditLogMixin._safe_get_content_type", return_value=None)
    @patch("lex.audit_logging.mixins.AuditLogMixin._serialize_payload", side_effect=lambda x: x)
    @patch("lex.audit_logging.mixins.AuditLogMixin.AuditLogStatus.objects")
    @patch("lex.audit_logging.mixins.AuditLogMixin.AuditLog.objects")
    def test_failure_marks_status_failure(
        self, mock_log_mgr, mock_status_mgr, _ser, _ct
    ):
        """On failure, AuditLogStatus transitions to 'failure' with traceback."""
        fake_log = MagicMock(id=1, payload={})
        mock_log_mgr.create.return_value = fake_log

        mixin = self._make_mixin()
        ser, instance, _ = self._mock_serializer()
        ser.save.side_effect = RuntimeError("constraint violation")

        with self.assertRaises(RuntimeError):
            mixin.perform_update(ser)

        update_call = mock_status_mgr.filter.return_value.update
        update_call.assert_called_once()
        call_kwargs = update_call.call_args.kwargs
        self.assertEqual(call_kwargs["status"], "failure")
        self.assertIn("error_traceback", call_kwargs)
        self.assertIsNotNone(call_kwargs["error_traceback"])

    @patch("lex.audit_logging.mixins.AuditLogMixin._safe_get_content_type", return_value=None)
    @patch("lex.audit_logging.mixins.AuditLogMixin._serialize_payload", side_effect=lambda x: x)
    @patch("lex.audit_logging.mixins.AuditLogMixin.AuditLogStatus.objects")
    @patch("lex.audit_logging.mixins.AuditLogMixin.AuditLog.objects")
    def test_failure_re_raises_original_exception(
        self, mock_log_mgr, mock_status_mgr, _ser, _ct
    ):
        """On failure, the original exception is re-raised unchanged."""
        fake_log = MagicMock(id=1, payload={})
        mock_log_mgr.create.return_value = fake_log

        mixin = self._make_mixin()
        ser, _, _ = self._mock_serializer()
        ser.save.side_effect = ValueError("bad data")

        with self.assertRaises(ValueError) as ctx:
            mixin.perform_update(ser)
        self.assertIn("bad data", str(ctx.exception))

    @patch("lex.audit_logging.mixins.AuditLogMixin._safe_get_content_type", return_value=None)
    @patch("lex.audit_logging.mixins.AuditLogMixin._serialize_payload", side_effect=lambda x: x)
    @patch("lex.audit_logging.mixins.AuditLogMixin.AuditLogStatus.objects")
    @patch("lex.audit_logging.mixins.AuditLogMixin.AuditLog.objects")
    def test_returns_saved_instance(
        self, mock_log_mgr, mock_status_mgr, _ser, _ct
    ):
        """perform_update returns the instance returned by serializer.save()."""
        fake_log = MagicMock(id=1, payload={})
        mock_log_mgr.create.return_value = fake_log

        mixin = self._make_mixin()
        ser, expected_instance, post_data = self._mock_serializer(pk=77)
        post_serializer = MagicMock()
        post_serializer.data = post_data
        mixin.get_serializer = MagicMock(return_value=post_serializer)

        result = mixin.perform_update(ser)
        self.assertIs(result, expected_instance)


class TestPerformCreateAuditDataCorrectness(SimpleTestCase):
    """
    Verify that perform_create stores the correct data on the AuditLog:
    * action is 'create'
    * payload includes the created instance's pk
    * resource name matches the model class
    * content_type and object_id link to the created instance
    """

    def _make_mixin(self, user="creator"):
        mixin = AuditLogMixin()
        mixin.request = SimpleNamespace(user=user)
        mixin.kwargs = {"calculationId": "calc-new"}
        return mixin

    @patch("lex.audit_logging.mixins.AuditLogMixin._safe_get_content_type")
    @patch("lex.audit_logging.mixins.AuditLogMixin._serialize_payload", side_effect=lambda x: x)
    @patch("lex.audit_logging.mixins.AuditLogMixin.AuditLogStatus.objects")
    @patch("lex.audit_logging.mixins.AuditLogMixin.AuditLog.objects")
    def test_create_payload_includes_pk_after_save(
        self, mock_log_mgr, mock_status_mgr, _ser, mock_ct
    ):
        """
        After serializer.save(), the created instance's pk must be injected
        into the AuditLog payload so the audit trail links to the new record.
        """
        mock_ct.return_value = MagicMock()
        fake_log = MagicMock(id=1, payload={})
        mock_log_mgr.create.return_value = fake_log

        mixin = self._make_mixin()
        ser = MagicMock()
        ser.validated_data = {"name": "New Vehicle", "type": "SUV"}
        ser.Meta = SimpleNamespace(model=type("Vehicle", (), {}))
        instance = MagicMock(pk=99)
        instance.__class__ = type("Vehicle", (), {})
        ser.save.return_value = instance

        mixin.perform_create(ser)

        # Payload must have id=99 injected
        self.assertEqual(fake_log.payload["id"], 99)

    @patch("lex.audit_logging.mixins.AuditLogMixin._safe_get_content_type")
    @patch("lex.audit_logging.mixins.AuditLogMixin._serialize_payload", side_effect=lambda x: x)
    @patch("lex.audit_logging.mixins.AuditLogMixin.AuditLogStatus.objects")
    @patch("lex.audit_logging.mixins.AuditLogMixin.AuditLog.objects")
    def test_create_sets_calculation_id_from_kwargs(
        self, mock_log_mgr, mock_status_mgr, _ser, _ct
    ):
        """
        The calculation_id on the AuditLog must come from the view's
        kwargs['calculationId'] so it links to the right calculation context.
        """
        _ct.return_value = None
        fake_log = MagicMock(id=1, payload={})
        mock_log_mgr.create.return_value = fake_log

        mixin = self._make_mixin()
        ser = MagicMock()
        ser.validated_data = {"name": "Test"}
        ser.Meta = SimpleNamespace(model=type("Model", (), {}))
        ser.save.return_value = MagicMock(pk=1, __class__=type("Model", (), {}))

        mixin.perform_create(ser)

        create_kwargs = mock_log_mgr.create.call_args.kwargs
        self.assertEqual(create_kwargs["calculation_id"], "calc-new")


class TestPerformDestroyAuditDataCorrectness(SimpleTestCase):
    """
    Verify that perform_destroy stores the correct data on the AuditLog:
    * action is 'delete'
    * payload contains the serialized instance data (captured BEFORE deletion)
    * content_type and object_id are set
    """

    def _make_mixin(self, user="deleter"):
        mixin = AuditLogMixin()
        mixin.request = SimpleNamespace(user=user)
        mixin.kwargs = {}
        return mixin

    @patch("lex.audit_logging.mixins.AuditLogMixin._safe_get_content_type")
    @patch("lex.audit_logging.mixins.AuditLogMixin._serialize_payload", side_effect=lambda x: x)
    @patch("lex.audit_logging.mixins.AuditLogMixin.AuditLogStatus.objects")
    @patch("lex.audit_logging.mixins.AuditLogMixin.AuditLog.objects")
    def test_destroy_captures_data_before_deletion(
        self, mock_log_mgr, mock_status_mgr, _ser, _ct
    ):
        """
        The AuditLog payload must contain the serialized instance data
        captured BEFORE the instance is deleted from the database.
        """
        _ct.return_value = None
        fake_log = MagicMock(id=1, payload={})
        mock_log_mgr.create.return_value = fake_log

        mixin = self._make_mixin()

        # Simulate instance with data
        instance = MagicMock(pk=55, __class__=type("Investor", (), {}))
        pre_delete_data = {
            "id": 55,
            "name": "Investor A",
            "commitment": 1000000,
        }
        mixin.get_serializer = MagicMock(
            return_value=MagicMock(data=pre_delete_data)
        )

        mixin.perform_destroy(instance)

        # The initial payload should be the pre-delete serialized data
        create_kwargs = mock_log_mgr.create.call_args.kwargs
        self.assertEqual(create_kwargs["payload"], pre_delete_data)
        self.assertEqual(create_kwargs["action"], "delete")

    @patch("lex.audit_logging.mixins.AuditLogMixin._safe_get_content_type")
    @patch("lex.audit_logging.mixins.AuditLogMixin._serialize_payload", side_effect=lambda x: x)
    @patch("lex.audit_logging.mixins.AuditLogMixin.AuditLogStatus.objects")
    @patch("lex.audit_logging.mixins.AuditLogMixin.AuditLog.objects")
    def test_destroy_uses_instance_class_for_resource(
        self, mock_log_mgr, mock_status_mgr, _ser, _ct
    ):
        """
        Unlike create/update which receive a model class, destroy receives
        an instance. The resource name must still be derived correctly.
        """
        _ct.return_value = None
        fake_log = MagicMock(id=1, payload={})
        mock_log_mgr.create.return_value = fake_log

        mixin = self._make_mixin()
        InvestorClass = type("InvestorCashflow", (), {})
        instance = InvestorClass()
        instance.pk = 10
        instance.delete = MagicMock()
        mixin.get_serializer = MagicMock(return_value=MagicMock(data={"id": 10}))

        mixin.perform_destroy(instance)

        create_kwargs = mock_log_mgr.create.call_args.kwargs
        self.assertEqual(create_kwargs["resource"], "investorcashflow")
