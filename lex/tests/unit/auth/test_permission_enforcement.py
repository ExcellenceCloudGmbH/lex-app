"""
Tests for permission *enforcement* in ``ModelContainer`` and the
``LexModel`` convenience helpers.

Coverage targets:
    1. ``ModelContainer._permission_result_to_bool`` normalisation
    2. ``ModelContainer._evaluate_request_permission`` integration
       (new methods, legacy fallback, exception safety)
    3. ``LexModel`` convenience helpers (superuser, groups, owner,
       keycloak fallback, sensitive/public/basic field patterns)

All tests are pure-unit (no DB, no HTTP) and use ``SimpleTestCase``.
"""

import os
import sys
from pathlib import Path
from types import SimpleNamespace

import django
from django.apps import apps

sys.path.append(str(Path(__file__).resolve().parents[2]))
os.environ.setdefault(
    "DJANGO_SETTINGS_MODULE", "lex.process_admin.tests.django_test_settings"
)
if not apps.ready:
    django.setup()

from django.test import SimpleTestCase

from lex.core.models.LexModel import LexModel, PermissionResult, UserContext
from lex.process_admin.models.ModelContainer import ModelContainer


# ── Helpers ───────────────────────────────────────────────────────────────


def _user_context(*, is_superuser=False, groups=None, keycloak_scopes=None,
                  email="u@test.com", user=None, is_authenticated=True):
    return UserContext(
        user=user or SimpleNamespace(email=email),
        email=email,
        is_authenticated=is_authenticated,
        is_superuser=is_superuser,
        groups=groups or set(),
        keycloak_scopes=keycloak_scopes or set(),
    )


class _Restriction:
    """Controllable stub for ``modification_restriction``."""

    def __init__(self, *, read=True, modify=True, create=True, delete=True):
        self._read = read
        self._modify = modify
        self._create = create
        self._delete = delete

    def can_read_in_general(self, user, violations):
        return self._read

    def can_modify_in_general(self, user, violations):
        return self._modify

    def can_create_in_general(self, user, violations):
        return self._create

    def can_delete_in_general(self, user, violations):
        return self._delete


def _container(model_class=None, restriction=None):
    """Build a ``ModelContainer`` with controllable stubs."""
    container = ModelContainer.__new__(ModelContainer)
    container.model_class = model_class or type("FakeModel", (), {
        "modification_restriction": restriction or _Restriction(),
    })
    return container


# ═════════════════════════════════════════════════════════════════════════
# 1.  _permission_result_to_bool
# ═════════════════════════════════════════════════════════════════════════


class PermissionResultToBoolTests(SimpleTestCase):
    """``_permission_result_to_bool`` normalises every return shape."""

    def test_permission_result_allowed_true(self):
        self.assertTrue(
            ModelContainer._permission_result_to_bool(PermissionResult.allow_all())
        )

    def test_permission_result_denied(self):
        self.assertFalse(
            ModelContainer._permission_result_to_bool(PermissionResult.deny())
        )

    def test_non_empty_set_is_truthy(self):
        self.assertTrue(ModelContainer._permission_result_to_bool({"name"}))

    def test_empty_set_is_falsy(self):
        self.assertFalse(ModelContainer._permission_result_to_bool(set()))

    def test_non_empty_list_is_truthy(self):
        self.assertTrue(ModelContainer._permission_result_to_bool(["name"]))

    def test_empty_list_is_falsy(self):
        self.assertFalse(ModelContainer._permission_result_to_bool([]))

    def test_non_empty_dict_is_truthy(self):
        self.assertTrue(ModelContainer._permission_result_to_bool({"a": 1}))

    def test_empty_dict_is_falsy(self):
        self.assertFalse(ModelContainer._permission_result_to_bool({}))

    def test_true_bool(self):
        self.assertTrue(ModelContainer._permission_result_to_bool(True))

    def test_false_bool(self):
        self.assertFalse(ModelContainer._permission_result_to_bool(False))

    def test_none_is_falsy(self):
        self.assertFalse(ModelContainer._permission_result_to_bool(None))

    def test_object_with_allowed_attr_uses_it(self):
        obj = SimpleNamespace(allowed=True)
        self.assertTrue(ModelContainer._permission_result_to_bool(obj))

    def test_object_with_allowed_false(self):
        obj = SimpleNamespace(allowed=False)
        self.assertFalse(ModelContainer._permission_result_to_bool(obj))


# ═════════════════════════════════════════════════════════════════════════
# 2.  _evaluate_request_permission
# ═════════════════════════════════════════════════════════════════════════


class EvaluateRequestPermissionTests(SimpleTestCase):
    """``_evaluate_request_permission`` merges legacy + new permission APIs."""

    def test_legacy_denied_short_circuits(self):
        """If legacy restriction already denied, new methods are never called."""
        c = _container()
        result = c._evaluate_request_permission(
            request=None,
            instance=SimpleNamespace(),
            user_context=_user_context(),
            legacy_allowed=False,
            new_method_names=("permission_edit",),
        )
        self.assertFalse(result)

    def test_none_instance_returns_legacy(self):
        c = _container()
        result = c._evaluate_request_permission(
            request=None,
            instance=None,
            user_context=_user_context(),
            legacy_allowed=True,
            new_method_names=("permission_edit",),
        )
        self.assertTrue(result)

    def test_new_method_returning_permission_result_allowed(self):
        instance = SimpleNamespace(
            permission_edit=lambda uc: PermissionResult.allow_all(),
        )
        c = _container()
        result = c._evaluate_request_permission(
            request=None,
            instance=instance,
            user_context=_user_context(),
            legacy_allowed=True,
            new_method_names=("permission_edit",),
        )
        self.assertTrue(result)

    def test_new_method_returning_permission_result_denied(self):
        instance = SimpleNamespace(
            permission_edit=lambda uc: PermissionResult.deny(),
        )
        c = _container()
        result = c._evaluate_request_permission(
            request=None,
            instance=instance,
            user_context=_user_context(),
            legacy_allowed=True,
            new_method_names=("permission_edit",),
        )
        self.assertFalse(result)

    def test_new_method_returning_bool_true(self):
        instance = SimpleNamespace(permission_list=lambda uc: True)
        c = _container()
        result = c._evaluate_request_permission(
            request=None,
            instance=instance,
            user_context=_user_context(),
            legacy_allowed=True,
            new_method_names=("permission_list",),
        )
        self.assertTrue(result)

    def test_new_method_returning_set(self):
        """Legacy permission methods may return a set of field names."""
        instance = SimpleNamespace(permission_edit=lambda uc: {"name", "email"})
        c = _container()
        result = c._evaluate_request_permission(
            request=None,
            instance=instance,
            user_context=_user_context(),
            legacy_allowed=True,
            new_method_names=("permission_edit",),
        )
        self.assertTrue(result)

    def test_falls_through_to_legacy_method(self):
        """When no *new* method exists, legacy method is invoked."""
        instance = SimpleNamespace(
            can_edit=lambda req: {"name"},
        )
        c = _container()
        result = c._evaluate_request_permission(
            request="fake_request",
            instance=instance,
            user_context=_user_context(),
            legacy_allowed=True,
            new_method_names=("permission_edit",),
            legacy_method_names=("can_edit",),
        )
        self.assertTrue(result)

    def test_exception_in_permission_method_returns_legacy_allowed(self):
        """If the permission method raises, we fall back safely."""
        def boom(uc):
            raise RuntimeError("broken")

        instance = SimpleNamespace(permission_edit=boom)
        c = _container()
        result = c._evaluate_request_permission(
            request=None,
            instance=instance,
            user_context=_user_context(),
            legacy_allowed=True,
            new_method_names=("permission_edit",),
        )
        self.assertTrue(result)

    def test_no_methods_found_returns_legacy_allowed(self):
        instance = SimpleNamespace()
        c = _container()
        result = c._evaluate_request_permission(
            request=None,
            instance=instance,
            user_context=_user_context(),
            legacy_allowed=True,
            new_method_names=("permission_edit",),
            legacy_method_names=("can_edit",),
        )
        self.assertTrue(result)

    def test_first_matching_new_method_wins(self):
        """If ``permission_list`` exists, ``permission_read`` is never called."""
        call_log = []
        instance = SimpleNamespace(
            permission_list=lambda uc: (call_log.append("list"), True)[-1],
            permission_read=lambda uc: (call_log.append("read"), True)[-1],
        )
        c = _container()
        c._evaluate_request_permission(
            request=None,
            instance=instance,
            user_context=_user_context(),
            legacy_allowed=True,
            new_method_names=("permission_list", "permission_read"),
        )
        self.assertEqual(call_log, ["list"])


# ═════════════════════════════════════════════════════════════════════════
# 3.  LexModel convenience helpers
# ═════════════════════════════════════════════════════════════════════════


class _StubLexModel:
    """Instantiate LexModel helpers without needing a real DB model."""

    # Re-use the actual method implementations from LexModel
    allow_all_if_superuser = LexModel.allow_all_if_superuser
    allow_all_if_in_groups = LexModel.allow_all_if_in_groups
    allow_fields_if_owner = LexModel.allow_fields_if_owner
    keycloak_fallback = LexModel.keycloak_fallback
    allow_all_except_sensitive = LexModel.allow_all_except_sensitive
    allow_public_fields = LexModel.allow_public_fields
    allow_basic_fields = LexModel.allow_basic_fields


class LexModelConvenienceHelperTests(SimpleTestCase):
    """Test every documented convenience helper on ``LexModel``."""

    def setUp(self):
        self.model = _StubLexModel()

    # ── allow_all_if_superuser ─────────────────────────────────────────

    def test_superuser_gets_allow_all(self):
        result = self.model.allow_all_if_superuser(_user_context(is_superuser=True))
        self.assertIsNotNone(result)
        self.assertTrue(result.allowed)
        self.assertIsNone(result.fields)

    def test_non_superuser_gets_none(self):
        result = self.model.allow_all_if_superuser(_user_context(is_superuser=False))
        self.assertIsNone(result)

    # ── allow_all_if_in_groups ─────────────────────────────────────────

    def test_user_in_matching_group_gets_allow_all(self):
        ctx = _user_context(groups={"admins", "editors"})
        result = self.model.allow_all_if_in_groups(ctx, {"admins"})
        self.assertIsNotNone(result)
        self.assertTrue(result.allowed)

    def test_user_not_in_group_gets_none(self):
        ctx = _user_context(groups={"viewers"})
        result = self.model.allow_all_if_in_groups(ctx, {"admins"})
        self.assertIsNone(result)

    def test_string_group_is_accepted(self):
        ctx = _user_context(groups={"admins"})
        result = self.model.allow_all_if_in_groups(ctx, "admins")
        self.assertIsNotNone(result)

    # ── allow_fields_if_owner ──────────────────────────────────────────

    def test_owner_gets_allow_all_when_no_field_spec(self):
        owner_user = SimpleNamespace(email="owner@test.com")
        self.model.owner = owner_user
        ctx = _user_context(user=owner_user)
        result = self.model.allow_fields_if_owner(ctx)
        self.assertIsNotNone(result)
        self.assertTrue(result.allowed)
        self.assertIsNone(result.fields)

    def test_owner_gets_specific_fields(self):
        owner_user = SimpleNamespace(email="owner@test.com")
        self.model.owner = owner_user
        ctx = _user_context(user=owner_user)
        result = self.model.allow_fields_if_owner(ctx, fields={"name", "email"})
        self.assertEqual(result.fields, {"name", "email"})

    def test_owner_gets_excluded_fields(self):
        owner_user = SimpleNamespace(email="owner@test.com")
        self.model.owner = owner_user
        ctx = _user_context(user=owner_user)
        result = self.model.allow_fields_if_owner(ctx, excluded_fields={"password"})
        self.assertEqual(result.excluded_fields, {"password"})

    def test_non_owner_gets_none(self):
        self.model.owner = SimpleNamespace(email="other@test.com")
        ctx = _user_context(user=SimpleNamespace(email="me@test.com"))
        result = self.model.allow_fields_if_owner(ctx)
        self.assertIsNone(result)

    def test_unauthenticated_user_gets_none(self):
        self.model.owner = SimpleNamespace()
        ctx = _user_context(is_authenticated=False)
        result = self.model.allow_fields_if_owner(ctx)
        self.assertIsNone(result)

    # ── keycloak_fallback ──────────────────────────────────────────────

    def test_scope_present_returns_allow_all(self):
        ctx = _user_context(keycloak_scopes={"read"})
        result = self.model.keycloak_fallback(ctx, "read")
        self.assertTrue(result.allowed)

    def test_scope_absent_returns_deny(self):
        ctx = _user_context(keycloak_scopes=set())
        result = self.model.keycloak_fallback(ctx, "read")
        self.assertFalse(result.allowed)

    # ── allow_all_except_sensitive ─────────────────────────────────────

    def test_default_sensitive_fields_are_excluded(self):
        result = self.model.allow_all_except_sensitive(_user_context())
        self.assertTrue(result.allowed)
        self.assertIn("password", result.excluded_fields)
        self.assertIn("ssn", result.excluded_fields)

    def test_custom_sensitive_fields(self):
        result = self.model.allow_all_except_sensitive(
            _user_context(), sensitive_fields={"secret_key"},
        )
        self.assertEqual(result.excluded_fields, {"secret_key"})

    # ── allow_public_fields ────────────────────────────────────────────

    def test_returns_known_public_subset(self):
        result = self.model.allow_public_fields(_user_context())
        self.assertTrue(result.allowed)
        self.assertIn("id", result.fields)
        self.assertIn("name", result.fields)
        self.assertNotIn("password", result.fields)

    # ── allow_basic_fields ─────────────────────────────────────────────

    def test_returns_known_basic_subset(self):
        result = self.model.allow_basic_fields(_user_context())
        self.assertTrue(result.allowed)
        self.assertIn("id", result.fields)
        self.assertIn("email", result.fields)
        self.assertNotIn("password", result.fields)
