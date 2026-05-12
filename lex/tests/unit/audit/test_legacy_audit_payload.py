"""
Tests for ``lex.audit_logging.utils.legacy_audit_payload`` — parsing
calculation_record strings into (resource, record_id) pairs and building
legacy audit payloads.

Covers:
- parse_record_id: int, UUID, empty, non-numeric strings
- _coerce_model_pk: success, failure, UUID coercion
- _pk_is_textual: CharField vs IntegerField
- _resolve_with_model_lookup: longest-prefix, textual PK, DB lookup
- extract_resource_and_record_id: canonical, no-underscore, empty
- build_legacy_calculation_payload / build_legacy_user_change_payload
- merge_model_and_legacy_payload
"""

from unittest.mock import MagicMock
from uuid import UUID

from django.test import SimpleTestCase
from lex.audit_logging.utils.legacy_audit_payload import (
    parse_record_id,
    _coerce_model_pk,
    _pk_is_textual,
    _resolve_with_model_lookup,
    extract_resource_and_record_id,
    build_legacy_calculation_payload,
    build_legacy_user_change_payload,
    merge_model_and_legacy_payload,
)


class ParseRecordIdTest(SimpleTestCase):

    def test_none_returns_none(self):
        self.assertIsNone(parse_record_id(None))

    def test_empty_string_returns_none(self):
        self.assertIsNone(parse_record_id(""))
        self.assertIsNone(parse_record_id("   "))

    def test_integer_string(self):
        self.assertEqual(parse_record_id("42"), 42)

    def test_uuid_string(self):
        uid = "550e8400-e29b-41d4-a716-446655440000"
        self.assertEqual(parse_record_id(uid), uid)

    def test_non_numeric_non_uuid_returns_none(self):
        self.assertIsNone(parse_record_id("legacy_user_change"))

    def test_whitespace_stripped(self):
        self.assertEqual(parse_record_id("  99  "), 99)


class CoerceModelPkTest(SimpleTestCase):

    def test_success_int(self):
        model = MagicMock()
        model._meta.pk.to_python.return_value = 42
        self.assertEqual(_coerce_model_pk(model, "42"), 42)

    def test_success_uuid_coerced_to_str(self):
        uid = UUID("550e8400-e29b-41d4-a716-446655440000")
        model = MagicMock()
        model._meta.pk.to_python.return_value = uid
        self.assertEqual(_coerce_model_pk(model, str(uid)), str(uid))

    def test_conversion_failure_returns_none(self):
        model = MagicMock()
        model._meta.pk.to_python.side_effect = ValueError("bad")
        self.assertIsNone(_coerce_model_pk(model, "not_a_pk"))


class PkIsTextualTest(SimpleTestCase):

    def test_char_field(self):
        model = MagicMock()
        model._meta.pk.get_internal_type.return_value = "CharField"
        self.assertTrue(_pk_is_textual(model))

    def test_text_field(self):
        model = MagicMock()
        model._meta.pk.get_internal_type.return_value = "TextField"
        self.assertTrue(_pk_is_textual(model))

    def test_slug_field(self):
        model = MagicMock()
        model._meta.pk.get_internal_type.return_value = "SlugField"
        self.assertTrue(_pk_is_textual(model))

    def test_int_field(self):
        model = MagicMock()
        model._meta.pk.get_internal_type.return_value = "IntegerField"
        self.assertFalse(_pk_is_textual(model))

    def test_exception_returns_false(self):
        model = MagicMock()
        model._meta.pk.get_internal_type.side_effect = AttributeError
        self.assertFalse(_pk_is_textual(model))


class ResolveWithModelLookupTest(SimpleTestCase):

    def test_none_lookup_returns_none(self):
        self.assertIsNone(_resolve_with_model_lookup("anything", None))

    def test_empty_lookup_returns_none(self):
        self.assertIsNone(_resolve_with_model_lookup("anything", {}))

    def test_exact_alias_match_no_pk(self):
        model = MagicMock()
        model._meta.model_name.lower.return_value = "invoice"
        result = _resolve_with_model_lookup("invoice", {"invoice": model})
        self.assertEqual(result, ("invoice", None))

    def test_alias_with_pk_suffix_non_textual(self):
        """invoice_42 → model.pk is IntegerField → returns (invoice, 42)."""
        model = MagicMock()
        model._meta.model_name.lower.return_value = "invoice"
        model._meta.pk.to_python.return_value = 42
        model._meta.pk.get_internal_type.return_value = "IntegerField"
        # DB lookup finds the instance
        instance = MagicMock(pk=42)
        model._default_manager.filter.return_value.first.return_value = instance
        result = _resolve_with_model_lookup("invoice_42", {"invoice": model})
        self.assertEqual(result, ("invoice", 42))

    def test_alias_with_textual_pk_needs_db_hit(self):
        """textual PK model → requires instance from DB to confirm."""
        model = MagicMock()
        model._meta.model_name.lower.return_value = "tag"
        model._meta.pk.to_python.return_value = "my-slug"
        model._meta.pk.get_internal_type.return_value = "SlugField"

        instance = MagicMock(pk="my-slug")
        model._default_manager.filter.return_value.first.return_value = instance

        result = _resolve_with_model_lookup("tag_my-slug", {"tag": model})
        self.assertEqual(result, ("tag", "my-slug"))

    def test_textual_pk_no_db_match_returns_none(self):
        model = MagicMock()
        model._meta.model_name.lower.return_value = "tag"
        model._meta.pk.to_python.return_value = "ghost"
        model._meta.pk.get_internal_type.return_value = "CharField"
        model._default_manager.filter.return_value.first.return_value = None

        result = _resolve_with_model_lookup("tag_ghost", {"tag": model})
        self.assertIsNone(result)

    def test_longest_prefix_wins(self):
        """'fund_report_7' should match 'fund_report' (longer) not 'fund'."""
        short_model = MagicMock()
        short_model._meta.model_name.lower.return_value = "fund"
        short_model._meta.pk.to_python.return_value = 7  # "report_7" → coerce might fail
        short_model._meta.pk.get_internal_type.return_value = "IntegerField"
        short_model._default_manager.filter.return_value.first.return_value = None

        long_model = MagicMock()
        long_model._meta.model_name.lower.return_value = "fund_report"
        long_model._meta.pk.to_python.return_value = 7
        long_model._meta.pk.get_internal_type.return_value = "IntegerField"
        instance = MagicMock(pk=7)
        long_model._default_manager.filter.return_value.first.return_value = instance

        lookup = {"fund": short_model, "fund_report": long_model}
        result = _resolve_with_model_lookup("fund_report_7", lookup)
        self.assertEqual(result, ("fund_report", 7))


class ExtractResourceAndRecordIdTest(SimpleTestCase):

    def test_none_returns_none_none(self):
        self.assertEqual(extract_resource_and_record_id(None), (None, None))

    def test_empty_string(self):
        self.assertEqual(extract_resource_and_record_id(""), (None, None))

    def test_canonical_form(self):
        self.assertEqual(
            extract_resource_and_record_id("invoice_42"),
            ("invoice", 42),
        )

    def test_no_underscore_returns_resource_only(self):
        self.assertEqual(
            extract_resource_and_record_id("orphan"),
            ("orphan", None),
        )

    def test_non_numeric_suffix_falls_to_lookup(self):
        """'legacy_user_change' has no numeric suffix → tries model lookup."""
        self.assertEqual(
            extract_resource_and_record_id("legacy_user_change", model_lookup=None),
            ("legacy_user_change", None),
        )

    def test_with_model_lookup_resolves(self):
        model = MagicMock()
        model._meta.model_name.lower.return_value = "legacy_user_change"
        result = extract_resource_and_record_id(
            "legacy_user_change", model_lookup={"legacy_user_change": model},
        )
        self.assertEqual(result, ("legacy_user_change", None))


class BuildLegacyCalculationPayloadTest(SimpleTestCase):

    def test_basic_payload(self):
        row = MagicMock()
        row._meta.db_table = "legacy_calculation_log"
        row.calculation_record = "invoice_99"
        row.timestamp.isoformat.return_value = "2025-01-01T00:00:00"
        row.message_type = "INFO"
        row.message = "Done"
        row.method = "calculate"
        row.is_notification = False

        payload, resource, record_id = build_legacy_calculation_payload(row, "migration")

        self.assertEqual(resource, "invoice")
        self.assertEqual(record_id, 99)
        self.assertEqual(payload["legacy_source"], "legacy_calculation_log")
        self.assertEqual(payload["reason"], "migration")
        self.assertEqual(payload["id"], 99)

    def test_no_record_id_omits_id_key(self):
        row = MagicMock()
        row._meta.db_table = "t"
        row.calculation_record = "orphan"
        row.timestamp = None
        row.message_type = "WARN"
        row.message = "m"
        row.method = "x"
        row.is_notification = True

        payload, resource, record_id = build_legacy_calculation_payload(row, "test")
        self.assertNotIn("id", payload)
        self.assertIsNone(record_id)


class BuildLegacyUserChangePayloadTest(SimpleTestCase):

    def test_basic_payload(self):
        row = MagicMock()
        row._meta.db_table = "legacy_user_change"
        row.calculation_record = "fund_5"
        row.timestamp.isoformat.return_value = "2025-06-01T12:00:00"
        row.message = "Updated budget"
        row.traceback = None

        payload, resource, record_id = build_legacy_user_change_payload(row, "import")

        self.assertEqual(resource, "fund")
        self.assertEqual(record_id, 5)
        self.assertEqual(payload["id"], 5)
        self.assertIsNone(payload["traceback"])


class MergeLegacyPayloadTest(SimpleTestCase):

    def test_merge_with_model_payload(self):
        model_p = {"name": "X", "amount": 100}
        legacy_p = {"legacy_source": "tbl", "reason": "migration", "message": "ok"}
        result = merge_model_and_legacy_payload(model_p, legacy_p)
        self.assertIn("legacy_source", result)
        self.assertIn("name", result)

    def test_merge_with_none_model_payload(self):
        legacy_p = {"legacy_source": "tbl", "reason": "migration"}
        result = merge_model_and_legacy_payload(None, legacy_p)
        self.assertEqual(result, legacy_p)


# ── Extended tests merged from lex/tests/test_legacy_audit_payload.py ───


class ParseRecordIdExtendedTest(SimpleTestCase):
    """Additional ``parse_record_id`` edge cases."""

    def test_numeric_zero(self):
        self.assertEqual(parse_record_id("0"), 0)

    def test_large_number(self):
        self.assertEqual(parse_record_id("999999999"), 999999999)

    def test_partial_uuid_returns_none(self):
        self.assertIsNone(parse_record_id("550e8400-e29b"))


class ExtractResourceExtendedTest(SimpleTestCase):
    """Additional ``extract_resource_and_record_id`` edge cases."""

    def test_multi_word_resource(self):
        resource, record_id = extract_resource_and_record_id("cash_flow_100")
        self.assertEqual(resource, "cash_flow")
        self.assertEqual(record_id, 100)

    def test_uuid_record_id(self):
        uuid_str = "550e8400-e29b-41d4-a716-446655440000"
        resource, record_id = extract_resource_and_record_id(f"fund_{uuid_str}")
        self.assertEqual(resource, "fund")
        self.assertEqual(record_id, uuid_str)

    def test_whitespace_input(self):
        resource, record_id = extract_resource_and_record_id("   ")
        self.assertIsNone(resource)
        self.assertIsNone(record_id)


class MergePayloadExtendedTest(SimpleTestCase):
    """Additional ``merge_model_and_legacy_payload`` edge cases."""

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
