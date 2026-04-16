"""
Unit tests for pure helper functions in ``lex.api.serializers.base_serializers``.

**What this tests (customer-visible behaviour)**

The serializer layer normalizes field names for permission checks,
detects model capabilities, and unwraps History/MetaHistory wrappers.
Bugs in these helpers cause permission fields to silently disappear
from the grid or the record detail page.

**Why it matters**

Field-level permissions drive what users see and edit.  If
``_normalize_field_names`` drops a field, that column vanishes from the
grid with no error.  If ``_get_capabilities`` mis-detects a permission
method, the scopes computation silently returns ``{}``.

**Methodology**

Pure functions and static methods — no DB, no serializer instantiation.

Run::

    python manage.py test lex.tests.test_serializer_helpers
"""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.db import models
from django.test import SimpleTestCase

from lex.api.serializers.base_serializers import (
    LexSerializer,
    _get_capabilities,
    _capability_cache,
)
from lex.core.models.LexModel import LexModel


class _SerializerHelperRelatedModel(LexModel):
    name = models.CharField(max_length=20, default="")

    class Meta:
        app_label = "serializer_tests"
        managed = False


class _SerializerHelperParentModel(LexModel):
    name = models.CharField(max_length=20, default="")
    related = models.ForeignKey(
        _SerializerHelperRelatedModel,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
    )

    class Meta:
        app_label = "serializer_tests"
        managed = False


class TestNormalizeFieldNames(SimpleTestCase):
    """Prove ``_normalize_field_names`` handles every legacy return type."""

    def test_none_returns_empty_set(self):
        self.assertEqual(LexSerializer._normalize_field_names(None), set())

    def test_string_returns_singleton_set(self):
        self.assertEqual(LexSerializer._normalize_field_names("name"), {"name"})

    def test_set_passes_through(self):
        self.assertEqual(
            LexSerializer._normalize_field_names({"name", "age"}),
            {"name", "age"},
        )

    def test_frozenset_converted(self):
        self.assertEqual(
            LexSerializer._normalize_field_names(frozenset(["name"])),
            {"name"},
        )

    def test_list_converted(self):
        self.assertEqual(
            LexSerializer._normalize_field_names(["name", "age"]),
            {"name", "age"},
        )

    def test_tuple_converted(self):
        self.assertEqual(
            LexSerializer._normalize_field_names(("name", "age")),
            {"name", "age"},
        )

    def test_non_string_elements_filtered(self):
        self.assertEqual(
            LexSerializer._normalize_field_names(["name", 42, None, "age"]),
            {"name", "age"},
        )

    def test_unknown_type_returns_empty(self):
        self.assertEqual(LexSerializer._normalize_field_names(42), set())

    def test_empty_list_returns_empty(self):
        self.assertEqual(LexSerializer._normalize_field_names([]), set())


class TestGetCapabilities(SimpleTestCase):
    """Prove ``_get_capabilities`` detects permission methods correctly."""

    def setUp(self):
        _capability_cache.clear()

    def test_detects_all_permission_methods(self):
        model_class = type("FullModel", (), {
            "permission_edit": lambda self, ctx: None,
            "permission_delete": lambda self, ctx: None,
            "permission_export": lambda self, ctx: None,
            "permission_read": lambda self, ctx: None,
            "can_read": lambda self, req: None,
            "can_edit": lambda self, req: None,
            "can_delete": lambda self, req: None,
            "can_export": lambda self, req: None,
        })
        caps = _get_capabilities(model_class)
        self.assertTrue(caps["has_permission_edit"])
        self.assertTrue(caps["has_permission_delete"])
        self.assertTrue(caps["has_permission_export"])
        self.assertTrue(caps["has_permission_read"])
        self.assertTrue(caps["has_can_read"])
        self.assertTrue(caps["has_can_edit"])
        self.assertTrue(caps["has_can_delete"])
        self.assertTrue(caps["has_can_export"])

    def test_detects_no_permission_methods(self):
        model_class = type("BareModel", (), {})
        caps = _get_capabilities(model_class)
        self.assertFalse(caps["has_permission_edit"])
        self.assertFalse(caps["has_permission_delete"])
        self.assertFalse(caps["has_can_read"])

    def test_result_is_cached(self):
        model_class = type("CachedModel", (), {})
        result1 = _get_capabilities(model_class)
        result2 = _get_capabilities(model_class)
        self.assertIs(result1, result2)


class TestUnwrapInstance(SimpleTestCase):
    """Prove ``_unwrap_instance`` peels History/MetaHistory wrappers."""

    def test_plain_instance_returned_as_is(self):
        instance = MagicMock(spec=[])  # no history_object, no instance
        result = LexSerializer._unwrap_instance(instance)
        self.assertIs(result, instance)

    def test_unwraps_meta_history_wrapper(self):
        inner = MagicMock()
        inner_instance = MagicMock(spec=[])  # The main model
        inner.instance = inner_instance
        meta_wrapper = MagicMock()
        meta_wrapper.history_object = inner
        result = LexSerializer._unwrap_instance(meta_wrapper)
        self.assertIs(result, inner_instance)

    def test_unwraps_history_wrapper_via_instance_property(self):
        main_model = MagicMock(spec=[])
        history = MagicMock(spec=["instance"])
        history.instance = main_model
        result = LexSerializer._unwrap_instance(history)
        self.assertIs(result, main_model)


class TestAuditLogPayloadPermissionFastPaths(SimpleTestCase):
    def test_default_permission_payload_visibility_skips_shadow_instance(self):
        request = SimpleNamespace(
            user_permissions=[
                {
                    "rsname": (
                        f"{_SerializerHelperParentModel._meta.app_label}."
                        f"{_SerializerHelperParentModel.__name__}"
                    ),
                    "resource_set_id": "10",
                    "scopes": ["read"],
                }
            ]
        )

        with patch.object(
            LexSerializer,
            "_build_shadow_instance",
            side_effect=AssertionError("shadow instance should not be built"),
        ):
            visible_fields = LexSerializer._get_audit_log_payload_visible_fields(
                request,
                _SerializerHelperParentModel,
                {"id": 10, "name": "allowed"},
            )

        self.assertIn("name", visible_fields)
        self.assertIn("related", visible_fields)

    def test_default_permission_related_payload_skips_database_lookup(self):
        request = SimpleNamespace(
            user_permissions=[
                {
                    "rsname": (
                        f"{_SerializerHelperRelatedModel._meta.app_label}."
                        f"{_SerializerHelperRelatedModel.__name__}"
                    ),
                    "resource_set_id": "77",
                    "scopes": ["read"],
                }
            ]
        )

        with patch.object(
            _SerializerHelperRelatedModel.objects,
            "get",
            side_effect=AssertionError("related object lookup should not run"),
        ):
            filtered = LexSerializer._filter_foreign_key_relations(
                request,
                _SerializerHelperParentModel,
                {"name": "parent", "related": {"id": 77, "name": "allowed"}},
            )

        self.assertIn("related", filtered)

    def test_unreadable_default_permission_related_payload_is_removed_without_database_lookup(self):
        request = SimpleNamespace(user_permissions=[])

        with patch.object(
            _SerializerHelperRelatedModel.objects,
            "get",
            side_effect=AssertionError("related object lookup should not run"),
        ):
            filtered = LexSerializer._filter_foreign_key_relations(
                request,
                _SerializerHelperParentModel,
                {"name": "parent", "related": {"id": 77, "name": "denied"}},
            )

        self.assertNotIn("related", filtered)
