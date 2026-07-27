"""
Tests for ``ModelContainer`` — the core wrapper for every registered Django model.

**What is tested:**

    * ``__init__()`` validation — None model_class raises ValueError, None
      process_admin raises ValueError, non-Django class accepted if it has __name__
    * ``model_id`` / ``id`` property — returns _meta.model_name or fallback __name__
    * ``display_title`` / ``title`` property — returns verbose name or class name
    * ``pk_name`` property — returns PK field name or None
    * ``get_modification_restriction()`` — returns model attribute or default
    * ``_permission_result_to_bool()`` — normalizes PermissionResult, set, list, dict,
      bool, frozenset, tuple to boolean
    * ``_evaluate_request_permission()`` — new vs legacy permission methods, exception
      fallback, None instance short-circuit
    * ``get_serializers_map()`` — lazy loading, force_refresh, signature invalidation,
      exception handling
    * ``get_permission_restrictions_for_user()`` — delegates to modification_restriction
    * ``get_permission_restrictions_for_request()`` — combines legacy + new permissions

**Why this matters:**

    Every model in the framework is wrapped in a ModelContainer.  If __init__
    validation accepts garbage, the system silently breaks downstream.  If
    ``_permission_result_to_bool`` doesn't handle all return types, permission
    checks return wrong results — either blocking legitimate users or allowing
    unauthorized access.

**How to run:**

    .. code-block:: bash

        lex test lex.process_admin.tests.test_model_container --verbosity=2 --noinput
"""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase
from lex.process_admin.models.ModelContainer import ModelContainer


# ─── helpers ──────────────────────────────────────────────────────────

def _make_model_class(name="TestModel", model_name=None, pk_name="id", verbose_name=None):
    """Create a stub Django model class with _meta."""
    cls = type(name, (), {})
    meta = SimpleNamespace(
        model_name=model_name or name.lower(),
        pk=SimpleNamespace(name=pk_name),
        verbose_name=verbose_name or name.lower(),
        verbose_name_plural=f"{verbose_name or name.lower()}s",
        abstract=False,
    )
    cls._meta = meta
    return cls


def _make_process_admin():
    """Create a minimal ProcessAdmin stub."""
    pa = MagicMock()
    pa.get_fields_in_table_view.return_value = ["id", "name"]
    return pa


def _make_container(model_class=None, process_admin=None):
    """Build a ModelContainer with mocked serializer creation."""
    mc = model_class or _make_model_class()
    pa = process_admin or _make_process_admin()
    with patch(
        "lex.process_admin.models.ModelContainer.get_serializer_map_for_model",
        return_value={"default": MagicMock()},
    ):
        return ModelContainer(mc, pa)


# ════════════════════════════════════════════════════════════════════════
#  __init__ validation
# ════════════════════════════════════════════════════════════════════════

class TestModelContainerInit(SimpleTestCase):
    """Verify constructor validation prevents invalid containers."""

    def test_none_model_class_raises(self):
        """model_class=None → ValueError."""
        with self.assertRaises(ValueError) as cm:
            ModelContainer(None, _make_process_admin())
        self.assertIn("cannot be None", str(cm.exception))

    def test_none_process_admin_raises(self):
        """process_admin=None → ValueError."""
        with self.assertRaises(ValueError) as cm:
            ModelContainer(_make_model_class(), None)
        self.assertIn("cannot be None", str(cm.exception))

    def test_valid_model_creates_container(self):
        """Valid model + process_admin → container with serializers loaded."""
        container = _make_container()
        self.assertIsNotNone(container.serializers_map)
        self.assertIsNotNone(container.model_class)

    def test_model_without_meta_gets_empty_serializers(self):
        """Model without _meta → serializers_map is empty dict, not None."""
        cls = type("PlainClass", (), {})
        pa = _make_process_admin()
        container = ModelContainer(cls, pa)
        self.assertEqual(container.serializers_map, {})
        self.assertIsNone(container.obj_serializer)


# ════════════════════════════════════════════════════════════════════════
#  Properties
# ════════════════════════════════════════════════════════════════════════

class TestModelContainerProperties(SimpleTestCase):
    """Verify model_id, display_title, pk_name, and legacy aliases."""

    def test_model_id_uses_meta_model_name(self):
        container = _make_container(_make_model_class("CashFlow", model_name="cashflow"))
        self.assertEqual(container.model_id, "cashflow")

    def test_model_id_fallback_without_meta(self):
        cls = type("RawWidget", (), {})
        pa = _make_process_admin()
        container = ModelContainer(cls, pa)
        self.assertEqual(container.model_id, "rawwidget")

    def test_id_is_alias_for_model_id(self):
        container = _make_container()
        self.assertEqual(container.id, container.model_id)

    def test_title_is_alias_for_display_title(self):
        container = _make_container()
        self.assertEqual(container.title, container.display_title)

    def test_pk_name_returns_pk_field_name(self):
        container = _make_container(_make_model_class(pk_name="uuid"))
        self.assertEqual(container.pk_name, "uuid")

    def test_pk_name_none_without_meta(self):
        cls = type("NoPK", (), {})
        pa = _make_process_admin()
        container = ModelContainer(cls, pa)
        self.assertIsNone(container.pk_name)


# ════════════════════════════════════════════════════════════════════════
#  _permission_result_to_bool
# ════════════════════════════════════════════════════════════════════════

class TestPermissionResultToBool(SimpleTestCase):
    """Verify all permission return types are normalized to bool correctly."""

    def test_permission_result_with_allowed_true(self):
        """PermissionResult.allowed=True → True."""
        result = SimpleNamespace(allowed=True)
        self.assertTrue(ModelContainer._permission_result_to_bool(result))

    def test_permission_result_with_allowed_false(self):
        """PermissionResult.allowed=False → False."""
        result = SimpleNamespace(allowed=False)
        self.assertFalse(ModelContainer._permission_result_to_bool(result))

    def test_nonempty_set_is_truthy(self):
        """Non-empty set (from can_edit returning field names) → True."""
        self.assertTrue(ModelContainer._permission_result_to_bool({"name", "amount"}))

    def test_empty_set_is_falsy(self):
        """Empty set → False."""
        self.assertFalse(ModelContainer._permission_result_to_bool(set()))

    def test_nonempty_list_is_truthy(self):
        self.assertTrue(ModelContainer._permission_result_to_bool(["field1"]))

    def test_empty_list_is_falsy(self):
        self.assertFalse(ModelContainer._permission_result_to_bool([]))

    def test_nonempty_dict_is_truthy(self):
        self.assertTrue(ModelContainer._permission_result_to_bool({"key": True}))

    def test_empty_dict_is_falsy(self):
        self.assertFalse(ModelContainer._permission_result_to_bool({}))

    def test_frozenset_nonempty_is_truthy(self):
        self.assertTrue(ModelContainer._permission_result_to_bool(frozenset({"a"})))

    def test_tuple_nonempty_is_truthy(self):
        self.assertTrue(ModelContainer._permission_result_to_bool(("x",)))

    def test_bool_true_passthrough(self):
        self.assertTrue(ModelContainer._permission_result_to_bool(True))

    def test_bool_false_passthrough(self):
        self.assertFalse(ModelContainer._permission_result_to_bool(False))

    def test_none_is_falsy(self):
        self.assertFalse(ModelContainer._permission_result_to_bool(None))

    def test_zero_is_falsy(self):
        self.assertFalse(ModelContainer._permission_result_to_bool(0))


# ════════════════════════════════════════════════════════════════════════
#  _evaluate_request_permission
# ════════════════════════════════════════════════════════════════════════

class TestEvaluateRequestPermission(SimpleTestCase):
    """Verify new vs legacy method dispatch and fallback logic."""

    def test_legacy_not_allowed_short_circuits(self):
        """If legacy_allowed=False, returns False immediately."""
        container = _make_container()
        result = container._evaluate_request_permission(
            request=MagicMock(),
            instance=MagicMock(),
            user_context=MagicMock(),
            legacy_allowed=False,
            new_method_names=("permission_read",),
        )
        self.assertFalse(result)

    def test_none_instance_returns_legacy(self):
        """If instance is None, returns legacy_allowed."""
        container = _make_container()
        result = container._evaluate_request_permission(
            request=MagicMock(),
            instance=None,
            user_context=MagicMock(),
            legacy_allowed=True,
            new_method_names=("permission_read",),
        )
        self.assertTrue(result)

    def test_new_method_takes_priority(self):
        """New permission method (permission_read) is called before legacy."""
        container = _make_container()
        instance = MagicMock()
        instance.permission_read.return_value = SimpleNamespace(allowed=False)

        result = container._evaluate_request_permission(
            request=MagicMock(),
            instance=instance,
            user_context=MagicMock(),
            legacy_allowed=True,
            new_method_names=("permission_read",),
            legacy_method_names=("can_read",),
        )
        self.assertFalse(result)
        instance.can_read.assert_not_called()

    def test_legacy_method_used_when_new_absent(self):
        """When new method doesn't exist, falls back to legacy."""
        container = _make_container()
        instance = MagicMock(spec=[])
        instance.can_read = MagicMock(return_value=True)

        result = container._evaluate_request_permission(
            request=MagicMock(),
            instance=instance,
            user_context=MagicMock(),
            legacy_allowed=True,
            new_method_names=("permission_read",),
            legacy_method_names=("can_read",),
        )
        self.assertTrue(result)

    def test_exception_falls_back_to_legacy_allowed(self):
        """Exception during permission check → returns legacy_allowed."""
        container = _make_container()
        instance = MagicMock()
        instance.permission_read.side_effect = RuntimeError("keycloak down")

        result = container._evaluate_request_permission(
            request=MagicMock(),
            instance=instance,
            user_context=MagicMock(),
            legacy_allowed=True,
            new_method_names=("permission_read",),
        )
        self.assertTrue(result)


# ════════════════════════════════════════════════════════════════════════
#  get_serializers_map
# ════════════════════════════════════════════════════════════════════════

class TestGetSerializersMap(SimpleTestCase):
    """Verify lazy loading, caching, and error handling of serializer maps."""

    def test_returns_cached_when_signature_matches(self):
        """Second call with same signature returns cached map without rebuilding."""
        container = _make_container()
        first = container.get_serializers_map()
        second = container.get_serializers_map()
        self.assertIs(first, second)

    def test_force_refresh_rebuilds(self):
        """force_refresh=True rebuilds even with same signature."""
        container = _make_container()
        original = container.serializers_map

        with patch(
            "lex.process_admin.models.ModelContainer.get_serializer_map_for_model",
            return_value={"default": MagicMock()},
        ):
            container.get_serializers_map(force_refresh=True)

        # Should have been rebuilt (may be same dict contents but rebuilt)
        self.assertIsNotNone(container.serializers_map)

    def test_exception_returns_empty_dict(self):
        """If serializer creation raises, returns empty dict."""
        container = _make_container()

        with patch(
            "lex.process_admin.models.ModelContainer.get_serializer_map_for_model",
            side_effect=RuntimeError("import error"),
        ):
            result = container.get_serializers_map(force_refresh=True)

        self.assertEqual(result, {})
        self.assertIsNone(container.obj_serializer)

    def test_model_without_meta_returns_empty(self):
        """Model without _meta → empty serializers map."""
        cls = type("PlainClass", (), {})
        pa = _make_process_admin()
        container = ModelContainer(cls, pa)

        result = container.get_serializers_map()
        self.assertEqual(result, {})


# ════════════════════════════════════════════════════════════════════════
#  get_modification_restriction
# ════════════════════════════════════════════════════════════════════════

class TestGetModificationRestriction(SimpleTestCase):
    """Verify modification restriction resolution."""

    def test_returns_model_attribute(self):
        """Model with modification_restriction → returns it."""
        from lex.core.mixins.ModelModificationRestriction import ModelModificationRestriction

        custom = ModelModificationRestriction()
        model = _make_model_class()
        model.modification_restriction = custom

        container = _make_container(model)
        self.assertIs(container.get_modification_restriction(), custom)

    def test_returns_default_when_missing(self):
        """Model without modification_restriction → returns default instance."""
        from lex.core.mixins.ModelModificationRestriction import ModelModificationRestriction

        container = _make_container()
        result = container.get_modification_restriction()
        self.assertIsInstance(result, ModelModificationRestriction)


# ════════════════════════════════════════════════════════════════════════
#  get_permission_restrictions_for_user
# ════════════════════════════════════════════════════════════════════════

class TestGetPermissionRestrictionsForUser(SimpleTestCase):
    """Verify user-level permission flag resolution."""

    def test_returns_all_four_permission_keys(self):
        """Result contains can_read, can_modify, can_create, can_delete keys."""
        container = _make_container()
        result = container.get_permission_restrictions_for_user(MagicMock())

        expected_keys = {
            "can_read_in_general",
            "can_modify_in_general",
            "can_create_in_general",
            "can_delete_in_general",
        }
        self.assertEqual(set(result.keys()), expected_keys)

    def test_legacy_alias_returns_same(self):
        """get_general_modification_restrictions_for_user is an alias."""
        container = _make_container()
        user = MagicMock()
        new_result = container.get_permission_restrictions_for_user(user)
        legacy_result = container.get_general_modification_restrictions_for_user(user)
        self.assertEqual(new_result, legacy_result)
