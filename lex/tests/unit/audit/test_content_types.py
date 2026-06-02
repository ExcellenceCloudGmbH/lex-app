from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.contrib.contenttypes.models import ContentType
from django.test import SimpleTestCase
from lex.api.serializers.base_serializers import LexSerializer
from lex.api.utils.helpers import resolve_target_model
from lex.audit_logging.serializers.AuditLogSerializer import AuditLogDefaultSerializer
from lex.audit_logging.serializers.CalculationLogSerializer import CalculationLogDefaultSerializer
from lex.audit_logging.utils.content_types import (
    safe_get_content_type,
    safe_get_generic_related_object,
    _describe_model,
    _get_content_type_manager,
)


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
    def test_audit_log_serializer_uses_safe_helpers_instead_of_direct_relations(self):
        serializer = AuditLogDefaultSerializer()
        target = SimpleNamespace(is_calculated=True)
        content_type = SimpleNamespace(app_label="core", model="dummy")

        class StubAuditLog:
            object_id = 13
            content_type_id = 7
            _state = SimpleNamespace(db="default")

            @property
            def content_type(self):
                raise AssertionError("Serializer should not access obj.content_type directly")

            @property
            def calculatable_object(self):
                raise AssertionError("Serializer should not access obj.calculatable_object directly")

        with patch(
            "lex.audit_logging.serializers.AuditLogSerializer.safe_get_generic_related_object",
            return_value=target,
        ), patch(
            "lex.audit_logging.serializers.AuditLogSerializer.safe_get_content_type",
            return_value=content_type,
        ):
            result = serializer.get_calculation_record(StubAuditLog())

        self.assertEqual(
            result,
            {
                "id": 13,
                "app_label": "core",
                "model": "dummy",
                "display_name": str(target),
                "details": {"is_calculated": True},
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


# ── New tests for untested content_types helpers ─────────────────────


class TestDescribeModel(SimpleTestCase):
    """Prove ``_describe_model`` returns a meaningful label for any input."""

    def test_django_model_class_returns_label_lower(self):
        """A Django model class returns ``app_label.model_name``."""
        from django.contrib.auth.models import User
        result = _describe_model(User)
        self.assertEqual(result, "auth.user")

    def test_plain_class_returns_name(self):
        """A class without ``_meta`` falls back to ``__name__``."""
        class Foo:
            pass
        self.assertEqual(_describe_model(Foo), "Foo")

    def test_string_returns_str(self):
        """A string (no ``_meta``, no ``__name__``) returns ``str()``."""
        self.assertEqual(_describe_model("hello"), "hello")

    def test_none_returns_str_none(self):
        """None returns the string ``'None'``."""
        self.assertEqual(_describe_model(None), "None")


class TestGetContentTypeManager(SimpleTestCase):
    """Prove ``_get_content_type_manager`` picks the right manager."""

    def test_none_returns_default_manager(self):
        """Passing ``None`` returns ``ContentType.objects``."""
        manager = _get_content_type_manager(None)
        self.assertIs(manager, ContentType.objects)

    def test_using_returns_db_manager(self):
        """Passing a db alias returns a db_manager bound to that alias."""
        manager = _get_content_type_manager("default")
        self.assertIsNotNone(manager)


class TestSafeGetContentTypeValidation(SimpleTestCase):
    """Prove ``safe_get_content_type`` validates its arguments."""

    def test_raises_on_no_arguments(self):
        """Calling with neither model nor content_type_id raises ValueError."""
        with self.assertRaises(ValueError):
            safe_get_content_type()


class TestSafeGetGenericRelatedObject(SimpleTestCase):
    """Prove ``safe_get_generic_related_object`` resolves GFK targets safely."""

    def test_returns_none_when_no_content_type_id(self):
        """When content_type_id is None, returns None immediately."""
        instance = MagicMock()
        instance.content_type_id = None
        instance.object_id = 1
        result = safe_get_generic_related_object(instance)
        self.assertIsNone(result)

    def test_returns_none_when_no_object_id(self):
        """When object_id is None, returns None."""
        instance = MagicMock()
        instance.content_type_id = 1
        instance.object_id = None
        result = safe_get_generic_related_object(instance)
        self.assertIsNone(result)

    def test_custom_field_names(self):
        """Supports custom ``content_type_field`` and ``object_id_field``."""
        instance = SimpleNamespace(my_ct_id=None, my_oid=1, _state=SimpleNamespace(db=None))
        result = safe_get_generic_related_object(
            instance,
            content_type_field="my_ct",
            object_id_field="my_oid",
        )
        self.assertIsNone(result)

    def test_returns_none_on_resolution_error(self):
        """Returns None when ContentType lookup raises an exception."""
        instance = SimpleNamespace(
            content_type_id=99999,
            object_id=1,
            _state=SimpleNamespace(db="default"),
        )
        result = safe_get_generic_related_object(instance)
        self.assertIsNone(result)

    def test_returns_none_when_model_class_is_none(self):
        """Returns None when content_type.model_class() is None."""
        fake_ct = SimpleNamespace(model_class=lambda: None)
        instance = SimpleNamespace(
            content_type_id=1,
            object_id=1,
            _state=SimpleNamespace(db="default"),
        )
        with patch(
            "lex.audit_logging.utils.content_types.safe_get_content_type",
            return_value=fake_ct,
        ):
            result = safe_get_generic_related_object(instance)
        self.assertIsNone(result)

