from datetime import datetime
from types import SimpleNamespace
from unittest import TestCase

from lex.audit_logging.utils.legacy_audit_payload import (
    build_legacy_calculation_payload,
    build_legacy_user_change_payload,
    extract_resource_and_record_id,
    merge_model_and_legacy_payload,
)


class BackfillAuditLogPayloadTest(TestCase):
    def test_extract_resource_and_record_id_numeric(self):
        resource, record_id = extract_resource_and_record_id("cashflow_42")
        self.assertEqual(resource, "cashflow")
        self.assertEqual(record_id, 42)

    def test_extract_resource_and_record_id_uuid(self):
        resource, record_id = extract_resource_and_record_id(
            "invoice_550e8400-e29b-41d4-a716-446655440000"
        )
        self.assertEqual(resource, "invoice")
        self.assertEqual(record_id, "550e8400-e29b-41d4-a716-446655440000")

    def test_extract_resource_and_record_id_rejects_non_identifier_suffix(self):
        resource, record_id = extract_resource_and_record_id("legacy_user_change")
        self.assertEqual(resource, "legacy_user_change")
        self.assertIsNone(record_id)

    def test_build_legacy_calculation_payload_includes_id(self):
        row = SimpleNamespace(
            _meta=SimpleNamespace(db_table="generic_app_calculationlog"),
            timestamp=datetime(2026, 2, 20, 12, 0, 0),
            message_type="INFO",
            message="done",
            method="run",
            is_notification=True,
            calculation_record="position_7",
        )
        payload, resource, record_id = build_legacy_calculation_payload(
            row, "V1 audit log migration snapshot"
        )

        self.assertEqual(resource, "position")
        self.assertEqual(record_id, 7)
        self.assertEqual(payload["id"], 7)
        self.assertEqual(payload["calculation_record"], "position_7")

    def test_build_legacy_user_payload_omits_id_when_not_parseable(self):
        row = SimpleNamespace(
            _meta=SimpleNamespace(db_table="generic_app_userchangelog"),
            timestamp=datetime(2026, 2, 20, 12, 0, 0),
            message="changed",
            traceback="",
            calculation_record="legacy_user_change",
        )
        payload, resource, record_id = build_legacy_user_change_payload(
            row, "V1 audit log migration snapshot"
        )

        self.assertEqual(resource, "legacy_user_change")
        self.assertIsNone(record_id)
        self.assertNotIn("id", payload)

    def test_merge_model_and_legacy_payload_keeps_model_fields(self):
        model_payload = {
            "id": 7,
            "name": "Invoice A",
            "timestamp": "2026-02-20T10:00:00",
        }
        legacy_payload = {
            "id": 7,
            "timestamp": "2026-02-20T12:00:00",
            "message": "legacy update",
        }

        merged = merge_model_and_legacy_payload(model_payload, legacy_payload)
        self.assertEqual(merged["id"], 7)
        self.assertEqual(merged["name"], "Invoice A")
        self.assertEqual(merged["timestamp"], "2026-02-20T10:00:00")
        self.assertEqual(merged["legacy_timestamp"], "2026-02-20T12:00:00")
        self.assertEqual(merged["message"], "legacy update")
