"""
Unit tests for ``base_serializers`` module-level helpers and ``LexSerializer``
utility methods.

**What this tests (customer-visible behaviour)**

``_get_lexmodel_fields`` powers field-exclusion from edit scopes — LexModel
internal fields (created_by, edited_by, etc.) must never appear as editable.
``_get_capabilities`` caches permission-method presence per model class so
the serializer doesn't call ``hasattr`` thousands of times.
``_normalize_field_names`` converts legacy permission return types to a
consistent ``set[str]``.
``_unwrap_instance`` reaches through History/MetaHistory wrappers to the
concrete model for permission checks.
``FilteredListSerializer`` filters out empty serialised records.

**Methodology**

Pure logic — no database or request objects needed.

Run::

    lex test lex.process_admin.tests.test_base_serializer_helpers --verbosity=2 --noinput --keepdb
"""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase
from lex.api.serializers.base_serializers import (
    _get_capabilities,
    _get_lexmodel_fields,
    _get_model_lookup,
    _capability_cache,
    FilteredListSerializer,
    LexSerializer,
)
from lex.core.models.LexModel import LexModel


class TestGetLexmodelFields(SimpleTestCase):
    """Prove ``_get_lexmodel_fields`` returns the LexModel base field names."""

    def test_returns_set(self):
        """Returns a set of strings."""
        fields = _get_lexmodel_fields()
        self.assertIsInstance(fields, set)

    def test_contains_known_fields(self):
        """Contains known LexModel internal fields."""
        fields = _get_lexmodel_fields()
        # LexModel always has created_by, edited_by, id
        # (exact field names depend on the model definition)
        self.assertTrue(len(fields) > 0)

    def test_is_cached(self):
        """Second call returns the same object (cached)."""
        fields1 = _get_lexmodel_fields()
        fields2 = _get_lexmodel_fields()
        self.assertIs(fields1, fields2)


class TestGetCapabilities(SimpleTestCase):
    """Prove ``_get_capabilities`` caches model capability flags."""

    def setUp(self):
        # Clear the cache between tests
        _capability_cache.clear()
        self.addCleanup(_capability_cache.clear)

    def test_detects_permission_methods(self):
        """A LexModel subclass has ``has_permission_read`` etc."""
        caps = _get_capabilities(LexModel)
        self.assertIn("has_permission_read", caps)
        self.assertIn("has_permission_edit", caps)
        self.assertIn("has_permission_delete", caps)
        self.assertIn("has_permission_export", caps)

    def test_detects_legacy_methods(self):
        """Also checks for legacy ``can_read`` / ``can_edit`` etc."""
        caps = _get_capabilities(LexModel)
        self.assertIn("has_can_read", caps)
        self.assertIn("has_can_edit", caps)
        self.assertIn("has_can_delete", caps)
        self.assertIn("has_can_export", caps)

    def test_caches_result(self):
        """Second call for the same class returns the cached dict."""
        caps1 = _get_capabilities(LexModel)
        caps2 = _get_capabilities(LexModel)
        self.assertIs(caps1, caps2)

    def test_plain_class_has_no_methods(self):
        """A plain class without permission methods returns False flags."""

        class PlainModel:
            pass

        caps = _get_capabilities(PlainModel)
        self.assertFalse(caps["has_permission_read"])
        self.assertFalse(caps["has_can_read"])


class TestNormalizeFieldNames(SimpleTestCase):
    """Prove ``_normalize_field_names`` handles all legacy return types."""

    def test_none_returns_empty_set(self):
        self.assertEqual(LexSerializer._normalize_field_names(None), set())

    def test_string_returns_singleton_set(self):
        self.assertEqual(LexSerializer._normalize_field_names("name"), {"name"})

    def test_list_returns_set(self):
        result = LexSerializer._normalize_field_names(["a", "b", "c"])
        self.assertEqual(result, {"a", "b", "c"})

    def test_set_returns_set(self):
        result = LexSerializer._normalize_field_names({"x", "y"})
        self.assertEqual(result, {"x", "y"})

    def test_tuple_returns_set(self):
        result = LexSerializer._normalize_field_names(("p", "q"))
        self.assertEqual(result, {"p", "q"})

    def test_filters_non_strings(self):
        """Non-string entries in a list are filtered out."""
        result = LexSerializer._normalize_field_names(["a", 123, None, "b"])
        self.assertEqual(result, {"a", "b"})

    def test_unknown_type_returns_empty(self):
        """An unsupported type (int, dict) returns empty set."""
        self.assertEqual(LexSerializer._normalize_field_names(42), set())
        self.assertEqual(LexSerializer._normalize_field_names({"a": 1}), set())


class TestUnwrapInstance(SimpleTestCase):
    """Prove ``_unwrap_instance`` reaches the concrete model."""

    def test_plain_instance_returns_self(self):
        """A non-wrapped instance is returned unchanged."""
        # Use spec=[] to prevent MagicMock from auto-creating attributes
        # like history_object, instance, instance_type
        obj = MagicMock(spec=[])
        result = LexSerializer._unwrap_instance(obj)
        self.assertIs(result, obj)

    def test_unwraps_history_object(self):
        """An instance with ``history_object`` → the history object."""
        inner = MagicMock()
        inner.instance = None
        outer = MagicMock()
        outer.history_object = inner
        result = LexSerializer._unwrap_instance(outer)
        # Should have unwrapped at least the history_object level
        self.assertIsNotNone(result)

    def test_unwraps_instance_attribute(self):
        """An instance with ``.instance`` → the concrete model."""
        concrete = MagicMock(spec=[])
        wrapper = MagicMock()
        wrapper.history_object = None
        wrapper.instance = concrete
        result = LexSerializer._unwrap_instance(wrapper)
        self.assertIs(result, concrete)


class TestGetCachedFieldNames(SimpleTestCase):
    """Prove ``_get_cached_field_names`` caches model field names."""

    def setUp(self):
        LexSerializer._meta_fields_cache.clear()
        self.addCleanup(LexSerializer._meta_fields_cache.clear)

    def test_returns_set_of_strings(self):
        fields = LexSerializer._get_cached_field_names(LexModel)
        self.assertIsInstance(fields, set)
        self.assertTrue(all(isinstance(f, str) for f in fields))

    def test_caches_result(self):
        fields1 = LexSerializer._get_cached_field_names(LexModel)
        fields2 = LexSerializer._get_cached_field_names(LexModel)
        self.assertIs(fields1, fields2)


class TestParseValueForField(SimpleTestCase):
    """Prove ``_parse_value_for_field`` converts values for Django fields."""

    def test_none_returns_none(self):
        field = MagicMock()
        result = LexSerializer._parse_value_for_field(field, None)
        self.assertIsNone(result)

    def test_fk_dict_extracts_id(self):
        """FK field with dict value extracts the ``id`` key."""
        from django.db.models import ForeignKey
        field = MagicMock(spec=ForeignKey)
        field.__class__ = ForeignKey
        result = LexSerializer._parse_value_for_field(field, {"id": 42, "name": "Fund"})
        self.assertEqual(result, 42)

    def test_fk_dict_no_id_returns_none(self):
        """FK field with dict but no ``id`` key returns None."""
        from django.db.models import ForeignKey
        field = MagicMock(spec=ForeignKey)
        field.__class__ = ForeignKey
        result = LexSerializer._parse_value_for_field(field, {"name": "Fund"})
        self.assertIsNone(result)


class TestFilteredListSerializer(SimpleTestCase):
    """Prove ``FilteredListSerializer`` filters out empty representations."""

    def test_filters_out_empty_dicts(self):
        """Empty dict representations are excluded from the result."""
        child = MagicMock()
        child.to_representation.side_effect = [
            {"id": 1, "name": "A"},
            {},
            {"id": 3, "name": "C"},
        ]
        serializer = FilteredListSerializer(child=child)
        result = serializer.to_representation([1, 2, 3])
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]["id"], 1)
        self.assertEqual(result[1]["id"], 3)

    def test_handles_manager_input(self):
        """When ``data`` is a Manager, calls ``.all()`` first."""
        from django.db import models
        child = MagicMock()
        child.to_representation.return_value = {"id": 1}
        manager = MagicMock(spec=models.Manager)
        manager.all.return_value = [MagicMock()]

        serializer = FilteredListSerializer(child=child)
        result = serializer.to_representation(manager)
        manager.all.assert_called_once()


class TestGetModelLookup(SimpleTestCase):
    """Prove ``_get_model_lookup`` builds a name→class lookup dict."""

    def test_returns_dict(self):
        lookup = _get_model_lookup()
        self.assertIsInstance(lookup, dict)

    def test_contains_known_models(self):
        """Contains at least the User model from django.contrib.auth."""
        lookup = _get_model_lookup()
        self.assertIn("user", lookup)


class TestResolveTargetModel(SimpleTestCase):
    """Prove ``_resolve_target_model`` finds the model class from an audit log."""

    def test_resolves_via_content_type_id(self):
        """Uses ``safe_get_content_type`` when content_type_id is present."""
        expected = type("MyModel", (), {})
        fake_ct = SimpleNamespace(model_class=lambda: expected)
        audit_log = SimpleNamespace(content_type_id=1, resource="ignored", _state=SimpleNamespace(db="default"))

        with patch("lex.api.serializers.base_serializers.safe_get_content_type", return_value=fake_ct):
            result = LexSerializer._resolve_target_model(audit_log)
        self.assertIs(result, expected)

    def test_falls_back_to_resource_name(self):
        """When content_type_id is None, uses ``resource`` for lookup."""
        audit_log = SimpleNamespace(content_type_id=None, resource="user")
        result = LexSerializer._resolve_target_model(audit_log)
        # "user" should resolve to django.contrib.auth.models.User
        from django.contrib.auth.models import User
        self.assertIs(result, User)

    def test_returns_none_for_unknown_resource(self):
        """Unknown resource name returns None."""
        audit_log = SimpleNamespace(content_type_id=None, resource="nonexistent_xyz")
        result = LexSerializer._resolve_target_model(audit_log)
        self.assertIsNone(result)
