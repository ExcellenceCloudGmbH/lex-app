"""
Unit tests for ``lex.audit_logging.utils.legacy_audit_payload``.

**What this tests (customer-visible behaviour)**

``parse_record_id`` and ``extract_resource_and_record_id`` parse
calculation record strings (e.g. ``"invoice_42"``) into a resource name
and a typed record ID.  ``merge_model_and_legacy_payload`` merges a
model snapshot with legacy metadata.

**Why it matters**

If record IDs aren't parsed correctly, audit log entries cannot be
associated with the correct model instance — leading to orphaned or
misattributed audit records.

**Methodology**

Pure functions — no DB, no models.

Run::

    python manage.py test lex.tests.test_legacy_audit_payload
"""

from django.test import SimpleTestCase

from lex.audit_logging.utils.legacy_audit_payload import (
    parse_record_id,
    extract_resource_and_record_id,
    merge_model_and_legacy_payload,
)


class TestParseRecordId(SimpleTestCase):
    """Prove ``parse_record_id`` handles numeric, UUID, and invalid IDs."""

    def test_numeric_string(self):
        self.assertEqual(parse_record_id("42"), 42)

    def test_numeric_zero(self):
        self.assertEqual(parse_record_id("0"), 0)

    def test_large_number(self):
        self.assertEqual(parse_record_id("999999999"), 999999999)

    def test_uuid_string(self):
        uuid_str = "550e8400-e29b-41d4-a716-446655440000"
        self.assertEqual(parse_record_id(uuid_str), uuid_str)

    def test_none_returns_none(self):
        self.assertIsNone(parse_record_id(None))

    def test_empty_string_returns_none(self):
        self.assertIsNone(parse_record_id(""))

    def test_whitespace_only_returns_none(self):
        self.assertIsNone(parse_record_id("   "))

    def test_plain_string_returns_none(self):
        """Non-numeric, non-UUID strings are rejected."""
        self.assertIsNone(parse_record_id("legacy_user_change"))

    def test_partial_uuid_returns_none(self):
        self.assertIsNone(parse_record_id("550e8400-e29b"))

    def test_numeric_with_whitespace(self):
        """Whitespace is stripped before parsing."""
        self.assertEqual(parse_record_id("  42  "), 42)


class TestExtractResourceAndRecordId(SimpleTestCase):
    """Prove ``extract_resource_and_record_id`` splits canonical records."""

    def test_canonical_form(self):
        resource, record_id = extract_resource_and_record_id("invoice_42")
        self.assertEqual(resource, "invoice")
        self.assertEqual(record_id, 42)

    def test_multi_word_resource(self):
        resource, record_id = extract_resource_and_record_id("cash_flow_100")
        self.assertEqual(resource, "cash_flow")
        self.assertEqual(record_id, 100)

    def test_uuid_record_id(self):
        uuid_str = "550e8400-e29b-41d4-a716-446655440000"
        resource, record_id = extract_resource_and_record_id(f"fund_{uuid_str}")
        self.assertEqual(resource, "fund")
        self.assertEqual(record_id, uuid_str)

    def test_none_input(self):
        resource, record_id = extract_resource_and_record_id(None)
        self.assertIsNone(resource)
        self.assertIsNone(record_id)

    def test_empty_string(self):
        resource, record_id = extract_resource_and_record_id("")
        self.assertIsNone(resource)
        self.assertIsNone(record_id)

    def test_no_underscore(self):
        """A string without underscore falls through to bare resource name."""
        resource, record_id = extract_resource_and_record_id("invoice")
        self.assertEqual(resource, "invoice")
        self.assertIsNone(record_id)

    def test_unparsable_suffix(self):
        """Non-ID suffix falls through to model lookup or bare resource."""
        resource, record_id = extract_resource_and_record_id(
            "legacy_user_change", model_lookup=None
        )
        self.assertEqual(resource, "legacy_user_change")
        self.assertIsNone(record_id)

    def test_whitespace_input(self):
        resource, record_id = extract_resource_and_record_id("   ")
        self.assertIsNone(resource)
        self.assertIsNone(record_id)


class TestMergeModelAndLegacyPayload(SimpleTestCase):
    """Prove ``merge_model_and_legacy_payload`` merges dicts correctly."""

    def test_basic_merge(self):
        model = {"field_a": 1}
        legacy = {"reason": "calc", "message": "ok"}
        result = merge_model_and_legacy_payload(model, legacy)
        self.assertEqual(result["field_a"], 1)
        self.assertEqual(result["reason"], "calc")
        self.assertEqual(result["message"], "ok")

    def test_model_none(self):
        legacy = {"reason": "calc"}
        result = merge_model_and_legacy_payload(None, legacy)
        self.assertEqual(result["reason"], "calc")

    def test_conflicting_keys_legacy_prefixed(self):
        """When both have the same key, legacy is prefixed with 'legacy_'."""
        model = {"message": "model_msg"}
        legacy = {"message": "legacy_msg"}
        result = merge_model_and_legacy_payload(model, legacy)
        self.assertEqual(result["message"], "model_msg")
        self.assertEqual(result["legacy_message"], "legacy_msg")

    def test_id_from_legacy_when_model_missing(self):
        model = {"field": "val"}
        legacy = {"id": 42}
        result = merge_model_and_legacy_payload(model, legacy)
        self.assertEqual(result["id"], 42)

    def test_id_from_model_takes_precedence(self):
        model = {"id": 99}
        legacy = {"id": 42}
        result = merge_model_and_legacy_payload(model, legacy)
        self.assertEqual(result["id"], 99)

    def test_empty_legacy(self):
        model = {"a": 1}
        result = merge_model_and_legacy_payload(model, {})
        self.assertEqual(result, {"a": 1})

    def test_empty_model(self):
        result = merge_model_and_legacy_payload({}, {"b": 2})
        self.assertEqual(result, {"b": 2})

    def test_does_not_mutate_inputs(self):
        model = {"a": 1}
        legacy = {"b": 2}
        model_copy = dict(model)
        legacy_copy = dict(legacy)
        merge_model_and_legacy_payload(model, legacy)
        self.assertEqual(model, model_copy)
        self.assertEqual(legacy, legacy_copy)
