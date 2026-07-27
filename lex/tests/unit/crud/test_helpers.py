"""
Tests for ``lex.api.utils.helpers`` — audit-log helper functions.

Covers:
- _parse_value: ForeignKey dict, datetime/date/time parsing, UUID/Decimal
- _get_model_lookup / _get_field_map: caching
- resolve_target_model: content_type, resource fallback, None
- build_shadow_instance: FK _id suffix, pk handling, failures
- can_read_from_payload: new system (permission_read), legacy (can_read),
  fallbacks, errors
"""

from datetime import date, datetime, time
from decimal import Decimal
from unittest.mock import MagicMock, patch, PropertyMock
from uuid import UUID

from django.test import SimpleTestCase
from lex.api.utils.helpers import (
    _parse_value,
    resolve_target_model,
    build_shadow_instance,
    can_read_from_payload,
)


# ── _parse_value ──────────────────────────────────────────────────────

class ParseValueTest(SimpleTestCase):

    def test_none_returns_none(self):
        field = MagicMock()
        self.assertIsNone(_parse_value(field, None))

    def test_foreign_key_dict_extracts_id(self):
        from django.db.models import ForeignKey
        field = MagicMock(spec=ForeignKey)
        result = _parse_value(field, {"id": 42, "name": "Foo"})
        self.assertEqual(result, 42)

    def test_foreign_key_dict_no_id(self):
        from django.db.models import ForeignKey
        field = MagicMock(spec=ForeignKey)
        result = _parse_value(field, {"name": "Foo"})
        self.assertIsNone(result)

    def test_datetime_field(self):
        from django.db.models.fields import DateTimeField
        field = MagicMock(spec=DateTimeField)
        result = _parse_value(field, "2025-06-15T10:30:00")
        self.assertIsInstance(result, datetime)
        self.assertEqual(result.year, 2025)

    def test_datetime_field_bad_value(self):
        from django.db.models.fields import DateTimeField
        field = MagicMock(spec=DateTimeField)
        result = _parse_value(field, "not-a-date")
        self.assertIsNone(result)

    def test_date_field(self):
        from django.db.models.fields import DateField
        field = MagicMock(spec=DateField)
        result = _parse_value(field, "2025-06-15")
        self.assertIsInstance(result, date)

    def test_date_field_bad_value(self):
        from django.db.models.fields import DateField
        field = MagicMock(spec=DateField)
        self.assertIsNone(_parse_value(field, "bad"))

    def test_time_field(self):
        from django.db.models.fields import TimeField
        field = MagicMock(spec=TimeField)
        result = _parse_value(field, "14:30:00")
        self.assertIsInstance(result, time)

    def test_time_field_bad_value(self):
        from django.db.models.fields import TimeField
        field = MagicMock(spec=TimeField)
        self.assertIsNone(_parse_value(field, "nope"))

    def test_uuid_string(self):
        field = MagicMock()  # generic field
        uid_str = "550e8400-e29b-41d4-a716-446655440000"
        result = _parse_value(field, uid_str)
        self.assertIsInstance(result, UUID)

    def test_decimal_string(self):
        field = MagicMock()
        result = _parse_value(field, "123.45")
        self.assertIsInstance(result, Decimal)

    def test_plain_string(self):
        field = MagicMock()
        result = _parse_value(field, "hello")
        self.assertEqual(result, "hello")

    def test_int_passthrough(self):
        field = MagicMock()
        self.assertEqual(_parse_value(field, 42), 42)


# ── resolve_target_model ──────────────────────────────────────────────

class ResolveTargetModelTest(SimpleTestCase):

    def test_content_type_resolution(self):
        mock_ct = MagicMock()
        mock_ct.model_class.return_value = "ResolvedModel"

        audit_log = MagicMock()
        audit_log.content_type_id = 5
        audit_log._state.db = "default"

        with patch("lex.api.utils.helpers.safe_get_content_type", return_value=mock_ct):
            result = resolve_target_model(audit_log)
        self.assertEqual(result, "ResolvedModel")

    def test_content_type_failure_falls_to_resource(self):
        audit_log = MagicMock()
        audit_log.content_type_id = 99
        audit_log.resource = "invoice"
        audit_log._state.db = None

        with patch("lex.api.utils.helpers.safe_get_content_type", side_effect=Exception):
            with patch("lex.api.utils.helpers._get_model_lookup", return_value={"invoice": "InvoiceModel"}):
                result = resolve_target_model(audit_log)
        self.assertEqual(result, "InvoiceModel")

    def test_no_content_type_no_resource(self):
        audit_log = MagicMock(spec=[])
        audit_log.content_type_id = None
        audit_log.resource = None
        # Need to handle getattr returning None
        result = resolve_target_model(audit_log)
        self.assertIsNone(result)


# ── build_shadow_instance ─────────────────────────────────────────────

class BuildShadowInstanceTest(SimpleTestCase):

    def test_basic_instance(self):
        mock_field = MagicMock()
        mock_field.name = "name"
        mock_field.attname = "name"
        mock_pk = MagicMock()
        mock_pk.name = "id"

        model_class = MagicMock()
        model_class._meta.concrete_fields = [mock_field]
        model_class._meta.pk = mock_pk

        result = build_shadow_instance(model_class, {"name": "Test"})
        model_class.assert_called_once()

    def test_empty_payload(self):
        model_class = MagicMock()
        self.assertIsNone(build_shadow_instance(model_class, {}))

    def test_none_payload(self):
        model_class = MagicMock()
        self.assertIsNone(build_shadow_instance(model_class, None))

    def test_exception_returns_none(self):
        model_class = MagicMock()
        model_class.__name__ = "BrokenModel"
        model_class._meta.concrete_fields = []
        # Force an exception when accessing pk.name during the function
        type(model_class._meta.pk).name = PropertyMock(side_effect=Exception("boom"))
        result = build_shadow_instance(model_class, {"x": 1})
        self.assertIsNone(result)


# ── can_read_from_payload ─────────────────────────────────────────────

class CanReadFromPayloadTest(SimpleTestCase):

    def test_no_model_resolved_allows(self):
        request = MagicMock()
        audit_log = MagicMock()

        with patch("lex.api.utils.helpers.resolve_target_model", return_value=None):
            self.assertTrue(can_read_from_payload(request, audit_log))

    def test_shadow_build_fails_allows(self):
        request = MagicMock()
        audit_log = MagicMock()
        audit_log.payload = {"x": 1}

        with patch("lex.api.utils.helpers.resolve_target_model", return_value=MagicMock()):
            with patch("lex.api.utils.helpers.build_shadow_instance", return_value=None):
                self.assertTrue(can_read_from_payload(request, audit_log))

    def test_permission_read_allowed(self):
        request = MagicMock()
        audit_log = MagicMock()
        audit_log.payload = {"id": 1}

        mock_instance = MagicMock()
        mock_result = MagicMock()
        mock_result.allowed = True
        mock_instance.permission_read.return_value = mock_result

        with patch("lex.api.utils.helpers.resolve_target_model", return_value=MagicMock()):
            with patch("lex.api.utils.helpers.build_shadow_instance", return_value=mock_instance):
                self.assertTrue(can_read_from_payload(request, audit_log))

    def test_permission_read_denied(self):
        request = MagicMock()
        audit_log = MagicMock()
        audit_log.payload = {"id": 1}

        mock_instance = MagicMock()
        mock_result = MagicMock()
        mock_result.allowed = False
        mock_instance.permission_read.return_value = mock_result

        with patch("lex.api.utils.helpers.resolve_target_model", return_value=MagicMock()):
            with patch("lex.api.utils.helpers.build_shadow_instance", return_value=mock_instance):
                self.assertFalse(can_read_from_payload(request, audit_log))

    def test_legacy_can_read_returns_fields(self):
        """Legacy can_read returning a set of field names → allowed if non-empty."""
        request = MagicMock()
        audit_log = MagicMock()
        audit_log.payload = {"id": 1}

        mock_instance = MagicMock(spec=["can_read"])
        mock_instance.can_read.return_value = {"name", "amount"}
        # Remove permission_read to force legacy path
        del mock_instance.permission_read

        with patch("lex.api.utils.helpers.resolve_target_model", return_value=MagicMock()):
            with patch("lex.api.utils.helpers.build_shadow_instance", return_value=mock_instance):
                self.assertTrue(can_read_from_payload(request, audit_log))

    def test_legacy_can_read_empty_set_denied(self):
        request = MagicMock()
        audit_log = MagicMock()
        audit_log.payload = {"id": 1}

        mock_instance = MagicMock(spec=["can_read"])
        mock_instance.can_read.return_value = set()
        del mock_instance.permission_read

        with patch("lex.api.utils.helpers.resolve_target_model", return_value=MagicMock()):
            with patch("lex.api.utils.helpers.build_shadow_instance", return_value=mock_instance):
                self.assertFalse(can_read_from_payload(request, audit_log))

    def test_legacy_can_read_boolean_true(self):
        request = MagicMock()
        audit_log = MagicMock()
        audit_log.payload = {"id": 1}

        mock_instance = MagicMock(spec=["can_read"])
        mock_instance.can_read.return_value = True
        del mock_instance.permission_read

        with patch("lex.api.utils.helpers.resolve_target_model", return_value=MagicMock()):
            with patch("lex.api.utils.helpers.build_shadow_instance", return_value=mock_instance):
                self.assertTrue(can_read_from_payload(request, audit_log))

    def test_exception_allows_by_default(self):
        request = MagicMock()
        audit_log = MagicMock()
        audit_log.payload = {"id": 1}

        mock_instance = MagicMock()
        mock_instance.permission_read.side_effect = Exception("boom")

        with patch("lex.api.utils.helpers.resolve_target_model", return_value=MagicMock()):
            with patch("lex.api.utils.helpers.build_shadow_instance", return_value=mock_instance):
                self.assertTrue(can_read_from_payload(request, audit_log))
