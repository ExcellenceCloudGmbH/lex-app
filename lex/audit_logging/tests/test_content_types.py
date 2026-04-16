from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.contrib.contenttypes.models import ContentType
from django.test import SimpleTestCase

from lex.api.serializers.base_serializers import LexSerializer
from lex.api.utils.helpers import resolve_target_model
from lex.audit_logging.serializers.AuditLogSerializer import AuditLogDefaultSerializer
from lex.audit_logging.serializers.CalculationLogSerializer import CalculationLogDefaultSerializer
from lex.audit_logging.utils.content_types import safe_get_content_type


class SafeContentTypeTest(SimpleTestCase):
    def test_safe_get_content_type_retries_stale_content_type_id_cache(self):
        manager = MagicMock()
        db_manager = MagicMock()
        manager.db_manager.return_value = db_manager

        stale_ct = SimpleNamespace(pk=9)
        fresh_ct = SimpleNamespace(pk=9)

        db_manager.get_for_id.side_effect = [stale_ct, fresh_ct]
        db_manager.get.side_effect = [ContentType.DoesNotExist(), fresh_ct]

        with patch("lex.audit_logging.utils.content_types.ContentType.objects", manager):
            result = safe_get_content_type(content_type_id=9, using="default")

        self.assertIs(result, fresh_ct)
        manager.clear_cache.assert_called_once()
        manager.db_manager.assert_called_with("default")


class AuditLogSerializerRegressionTest(SimpleTestCase):
    def test_audit_log_serializer_builds_calculation_record_from_cached_content_type_and_payload(self):
        serializer = AuditLogDefaultSerializer()
        content_type = SimpleNamespace(app_label="core", model="dummy")

        class StubAuditLog:
            object_id = 13
            content_type_id = 7
            payload = {"name": "Target Name", "is_calculated": True}
            _state = SimpleNamespace(db="default", fields_cache={"content_type": content_type})

        result = serializer.get_calculation_record(StubAuditLog())

        self.assertEqual(
            result,
            {
                "id": 13,
                "app_label": "core",
                "model": "dummy",
                "display_name": "Target Name",
                "details": {"is_calculated": True},
            },
        )

    def test_audit_log_serializer_falls_back_to_safe_content_type_lookup(self):
        serializer = AuditLogDefaultSerializer()
        content_type = SimpleNamespace(app_label="core", model="dummy")

        class StubAuditLog:
            object_id = 21
            content_type_id = 8
            payload = {}
            _state = SimpleNamespace(db="default", fields_cache={})

        with patch(
            "lex.audit_logging.serializers.AuditLogSerializer.safe_get_content_type",
            return_value=content_type,
        ):
            result = serializer.get_calculation_record(StubAuditLog())

        self.assertEqual(
            result,
            {
                "id": 21,
                "app_label": "core",
                "model": "dummy",
                "display_name": "dummy #21",
                "details": {},
            },
        )

    def test_calculation_log_serializer_uses_safe_generic_lookup(self):
        serializer = CalculationLogDefaultSerializer()
        target = SimpleNamespace(label="safe-target")

        class StubCalculationLog:
            object_id = 5
            content_type_id = 3

            @property
            def content_type(self):
                raise AssertionError("Serializer should not access obj.content_type directly")

            @property
            def calculatable_object(self):
                raise AssertionError("Serializer should not access obj.calculatable_object directly")

        with patch(
            "lex.audit_logging.serializers.CalculationLogSerializer.safe_get_generic_related_object",
            return_value=target,
        ):
            result = serializer.get_calculation_record(StubCalculationLog())

        self.assertEqual(result, str(target))


class ResolveTargetModelRegressionTest(SimpleTestCase):
    def test_helper_resolve_target_model_uses_content_type_id(self):
        expected_model = type("ExpectedModel", (), {})
        fake_ct = SimpleNamespace(model_class=lambda: expected_model)

        class StubAuditLog:
            resource = "ignored"
            content_type_id = 11
            _state = SimpleNamespace(db="default")

            @property
            def content_type(self):
                raise AssertionError("resolve_target_model should not access audit_log.content_type directly")

        with patch("lex.api.utils.helpers.safe_get_content_type", return_value=fake_ct):
            result = resolve_target_model(StubAuditLog())

        self.assertIs(result, expected_model)

    def test_serializer_resolve_target_model_uses_content_type_id(self):
        expected_model = type("ExpectedModel", (), {})
        fake_ct = SimpleNamespace(model_class=lambda: expected_model)

        class StubAuditLog:
            resource = "ignored"
            content_type_id = 12
            _state = SimpleNamespace(db="default")

            @property
            def content_type(self):
                raise AssertionError("LexSerializer should not access audit_log.content_type directly")

        with patch(
            "lex.api.serializers.base_serializers.safe_get_content_type",
            return_value=fake_ct,
        ):
            result = LexSerializer._resolve_target_model(StubAuditLog())

        self.assertIs(result, expected_model)
