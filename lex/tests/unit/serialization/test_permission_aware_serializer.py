"""
Tests for ``PermissionAwareSerializerMixin`` — field-level write permission checks.

**What is tested:**

    * ``_camel_to_snake()`` — frontend camelCase to backend snake_case conversion
    * ``_get_model_field_names()`` — model introspection for field names
    * ``_get_non_editable_fields()`` — system-managed and auto-generated fields
    * ``run_validation()`` — edit path: change detection, permission_edit/can_edit,
      camelCase translation, lexReserved fields, non-model fields, non-editable fields
    * ``run_validation()`` — create path: permission_create/can_create/default allow
    * ``add_permission_checks()`` — class decorator injection

**Why this matters:**

    This mixin enforces field-level authorization on every create/update request
    in the AG Grid forms.  If change detection is wrong, users can't save unchanged
    fields that they have no permission to edit.  If camelCase conversion is wrong,
    permission checks silently pass because the field name doesn't match.  If the
    create path doesn't check permission_create, unauthorized users can create records.

**How to run:**

    .. code-block:: bash

        lex test lex.tests.test_permission_aware_serializer --verbosity=2 --noinput
"""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase
from lex.api.views.model_entries.mixins.PermissionAwareSerializerMixin import (
    _camel_to_snake,
    PermissionAwareSerializerMixin,
    add_permission_checks,
)
from rest_framework.exceptions import PermissionDenied


# ─── test infrastructure ──────────────────────────────────────────────
# PermissionAwareSerializerMixin.__mro__[1] is `object`, which can't be
# patched.  We create a concrete parent class with `run_validation` so
# super().run_validation() resolves to it.

class _FakeParentSerializer:
    """Stand-in for DRF's Serializer.run_validation — returns data as-is."""

    def run_validation(self, data):
        return data


class _TestableSerializer(PermissionAwareSerializerMixin, _FakeParentSerializer):
    """Concrete class so super().run_validation() resolves to _FakeParentSerializer."""
    pass


def _build_edit_serializer(instance, request, data, editable_fields=None):
    """Wire up a _TestableSerializer for the edit (update) path.

    Parameters
    ----------
    instance : object
        Model instance being edited.
    request : object
        DRF request.
    data : dict
        Incoming payload (camelCase keys from frontend).
    editable_fields : set or None
        Fields the permission system says are editable.
    """
    ser = _TestableSerializer()
    ser.instance = instance
    ser.context = {"request": request}

    field_a = SimpleNamespace(name="amount")
    field_b = SimpleNamespace(name="report_date")
    field_c = SimpleNamespace(name="id", editable=False)

    model = MagicMock()
    model._meta.get_fields.return_value = [field_a, field_b, field_c]
    model._meta.pk = field_c
    model._meta.fields = [field_a, field_b, field_c]
    ser.Meta = SimpleNamespace(model=model)

    # Serializer fields for change detection
    ser.fields = {}
    for key in data:
        field_mock = MagicMock()
        field_mock.to_internal_value.return_value = data[key]
        ser.fields[key] = field_mock

    if editable_fields is not None:
        result_mock = MagicMock()
        result_mock.allowed = True
        result_mock.get_fields.return_value = editable_fields
        instance.permission_edit = MagicMock(return_value=result_mock)

    return ser


def _build_create_serializer(request, model_class):
    """Wire up a _TestableSerializer for the create path."""
    ser = _TestableSerializer()
    ser.instance = None
    ser.context = {"request": request}
    ser.Meta = SimpleNamespace(model=model_class)
    return ser


# ════════════════════════════════════════════════════════════════════════
#  _camel_to_snake — pure function
# ════════════════════════════════════════════════════════════════════════

class TestCamelToSnake(SimpleTestCase):
    """Verify camelCase → snake_case conversion for field name translation."""

    def test_simple_camel_case(self):
        self.assertEqual(_camel_to_snake("reportDate"), "report_date")

    def test_multiple_humps(self):
        self.assertEqual(_camel_to_snake("firstName"), "first_name")
        self.assertEqual(_camel_to_snake("isActiveUser"), "is_active_user")

    def test_consecutive_capitals(self):
        self.assertEqual(_camel_to_snake("XMLParser"), "xml_parser")
        self.assertEqual(_camel_to_snake("getHTTPResponse"), "get_http_response")

    def test_already_snake_case(self):
        self.assertEqual(_camel_to_snake("report_date"), "report_date")

    def test_single_word(self):
        self.assertEqual(_camel_to_snake("name"), "name")

    def test_single_capital(self):
        self.assertEqual(_camel_to_snake("Name"), "name")


# ════════════════════════════════════════════════════════════════════════
#  Field introspection helpers
# ════════════════════════════════════════════════════════════════════════

class TestFieldIntrospection(SimpleTestCase):
    """Verify model field introspection helpers."""

    def _make_serializer(self, model_class=None):
        ser = PermissionAwareSerializerMixin()
        ser.Meta = SimpleNamespace(model=model_class) if model_class else SimpleNamespace()
        return ser

    def test_get_model_field_names_returns_field_set(self):
        model = MagicMock()
        model._meta.get_fields.return_value = [
            SimpleNamespace(name="amount"), SimpleNamespace(name="currency"),
        ]
        ser = self._make_serializer(model)
        self.assertEqual(ser._get_model_field_names(), {"amount", "currency"})

    def test_get_model_field_names_no_model_returns_empty(self):
        ser = self._make_serializer()
        self.assertEqual(ser._get_model_field_names(), set())

    def test_get_non_editable_fields_includes_auto_fields(self):
        pk_field = SimpleNamespace(name="id")
        auto_field = SimpleNamespace(name="created_at", editable=False)
        normal_field = SimpleNamespace(name="amount", editable=True)

        model = MagicMock()
        model._meta.get_fields.return_value = [pk_field, auto_field, normal_field]
        model._meta.pk = pk_field

        ser = self._make_serializer(model)
        result = ser._get_non_editable_fields()

        self.assertIn("created_at", result)
        self.assertIn("id", result)
        self.assertNotIn("amount", result)

    def test_get_non_editable_fields_no_model_returns_empty(self):
        ser = self._make_serializer()
        self.assertEqual(ser._get_non_editable_fields(), set())


# ════════════════════════════════════════════════════════════════════════
#  run_validation — edit path
# ════════════════════════════════════════════════════════════════════════

class TestRunValidationEdit(SimpleTestCase):
    """Verify field-level permission checks during edit (update) operations."""

    def test_changed_field_in_allowed_set_passes(self):
        """Changing a field in the editable set → no PermissionDenied."""
        instance = MagicMock()
        instance.amount = 100
        data = {"amount": 200}

        ser = _build_edit_serializer(instance, MagicMock(), data,
                                     editable_fields={"amount", "report_date"})

        with patch("lex.core.models.LexModel.UserContext.from_request", return_value=MagicMock()):
            result = ser.run_validation(data)

        self.assertEqual(result, data)

    def test_changed_field_not_in_allowed_set_raises(self):
        """Changing a field NOT in the editable set → PermissionDenied."""
        instance = MagicMock()
        instance.amount = 100
        data = {"amount": 200}

        ser = _build_edit_serializer(instance, MagicMock(), data,
                                     editable_fields={"report_date"})

        with patch("lex.core.models.LexModel.UserContext.from_request", return_value=MagicMock()):
            with self.assertRaises(PermissionDenied) as cm:
                ser.run_validation(data)

        self.assertIn("amount", str(cm.exception.detail))

    def test_unchanged_field_skipped_by_change_detection(self):
        """Same value as instance → skipped (no permission check needed)."""
        instance = MagicMock()
        instance.amount = 100
        data = {"amount": 100}

        # amount NOT in editable_fields — but change detection should skip it
        ser = _build_edit_serializer(instance, MagicMock(), data,
                                     editable_fields={"report_date"})

        with patch("lex.core.models.LexModel.UserContext.from_request", return_value=MagicMock()):
            result = ser.run_validation(data)

        self.assertEqual(result, data)

    def test_camel_case_field_translated_to_snake_case(self):
        """reportDate (camelCase) → checked as report_date (snake_case)."""
        instance = MagicMock()
        instance.report_date = "2025-01-01"
        data = {"reportDate": "2025-06-15"}

        ser = _build_edit_serializer(instance, MagicMock(), data,
                                     editable_fields={"report_date"})

        with patch("lex.core.models.LexModel.UserContext.from_request", return_value=MagicMock()):
            result = ser.run_validation(data)

        self.assertEqual(result, data)

    def test_lex_reserved_field_always_skipped(self):
        """Fields starting with 'lexReserved' bypass permission checks."""
        instance = MagicMock()
        data = {"lexReservedAction": "delete"}

        # Need at least one editable field to pass the empty-set guard;
        # the lexReserved check is inside the field loop that follows.
        ser = _build_edit_serializer(instance, MagicMock(), data,
                                     editable_fields={"amount"})

        with patch("lex.core.models.LexModel.UserContext.from_request", return_value=MagicMock()):
            result = ser.run_validation(data)

        self.assertEqual(result, data)

    def test_empty_editable_fields_denies_whole_record(self):
        """permission_edit returns empty set → PermissionDenied before field loop."""
        instance = MagicMock()
        data = {"amount": 100}

        ser = _build_edit_serializer(instance, MagicMock(), data,
                                     editable_fields=set())

        with patch("lex.core.models.LexModel.UserContext.from_request", return_value=MagicMock()):
            with self.assertRaises(PermissionDenied) as cm:
                ser.run_validation(data)

        self.assertIn("do not have permission to edit", str(cm.exception.detail))

    def test_legacy_can_edit_returns_set(self):
        """Legacy can_edit returning a set of field names → used as editable fields."""
        instance = MagicMock(spec=[])
        instance.amount = 100
        instance.can_edit = MagicMock(return_value={"amount", "report_date"})
        instance._meta = MagicMock()
        instance._meta.fields = [SimpleNamespace(name="amount"), SimpleNamespace(name="report_date")]

        data = {"amount": 200}
        ser = _build_edit_serializer(instance, MagicMock(), data)

        result = ser.run_validation(data)
        self.assertEqual(result, data)

    def test_permission_edit_exception_allows_all_fields(self):
        """Exception during permission_edit → allows all fields (error-tolerant)."""
        instance = MagicMock()
        instance.amount = 100
        instance.permission_edit.side_effect = RuntimeError("keycloak down")
        instance._meta.fields = [SimpleNamespace(name="amount")]

        data = {"amount": 200}
        ser = _build_edit_serializer(instance, MagicMock(), data)

        with patch("lex.core.models.LexModel.UserContext.from_request", return_value=MagicMock()):
            result = ser.run_validation(data)

        self.assertEqual(result, data)


# ════════════════════════════════════════════════════════════════════════
#  run_validation — create path
# ════════════════════════════════════════════════════════════════════════

class TestRunValidationCreate(SimpleTestCase):
    """Verify permission checks during create operations."""

    def test_permission_create_denied_raises(self):
        """permission_create returns False → PermissionDenied."""
        ModelClass = type("Budget", (), {
            "permission_create": lambda self, ctx: False,
        })

        ser = _build_create_serializer(MagicMock(), ModelClass)

        with patch("lex.core.models.LexModel.UserContext.from_request", return_value=MagicMock()):
            with self.assertRaises(PermissionDenied) as cm:
                ser.run_validation({"name": "New Budget"})

        self.assertIn("do not have permission to create", str(cm.exception.detail))

    def test_legacy_can_create_denied_raises(self):
        """Legacy can_create returns False → PermissionDenied."""
        ModelClass = type("LegacyModel", (), {
            "can_create": lambda self, req: False,
        })

        ser = _build_create_serializer(MagicMock(), ModelClass)

        with self.assertRaises(PermissionDenied):
            ser.run_validation({"name": "test"})

    def test_no_permission_method_allows_create(self):
        """No permission_create or can_create → allowed by default."""
        ModelClass = type("PlainModel", (), {})

        ser = _build_create_serializer(MagicMock(), ModelClass)
        result = ser.run_validation({"name": "test"})

        self.assertEqual(result, {"name": "test"})

    def test_permission_create_exception_allows_by_default(self):
        """Exception during permission_create check → allowed (error-tolerant)."""
        ModelClass = type("ErrorModel", (), {
            "permission_create": property(
                lambda self: (_ for _ in ()).throw(RuntimeError("broken"))
            ),
        })

        ser = _build_create_serializer(MagicMock(), ModelClass)
        result = ser.run_validation({"name": "test"})

        self.assertEqual(result, {"name": "test"})


# ════════════════════════════════════════════════════════════════════════
#  add_permission_checks decorator
# ════════════════════════════════════════════════════════════════════════

class TestAddPermissionChecksDecorator(SimpleTestCase):
    """Verify the class decorator injects PermissionAwareSerializerMixin."""

    def test_decorator_injects_mixin(self):
        from rest_framework import serializers

        class MySerializer(serializers.Serializer):
            name = serializers.CharField()

        Decorated = add_permission_checks(MySerializer)
        self.assertTrue(issubclass(Decorated, PermissionAwareSerializerMixin))
        self.assertTrue(issubclass(Decorated, MySerializer))

    def test_decorator_preserves_name_and_module(self):
        from rest_framework import serializers

        class InvestorSerializer(serializers.Serializer):
            pass

        Decorated = add_permission_checks(InvestorSerializer)
        self.assertEqual(Decorated.__name__, "InvestorSerializer")
        self.assertEqual(Decorated.__module__, InvestorSerializer.__module__)
