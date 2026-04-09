"""
Tests for ``UserContext`` – the clean authorization primitive.

Documented contract (docs/features/access-and-ui/permissions.md):
    • ``from_request`` resolves user, email, groups, keycloak scopes, client
      roles, and user_permissions from a Django request + model instance.
    • ``from_request_base`` + ``with_instance`` provides an efficient
      two-phase pattern for serializer loops.
    • ``anonymous()`` creates a fully-unauthenticated context.
    • Keycloak scopes are resolved per-instance via ``resource_set_id`` or
      global resource-name matching, including historical resource names.
    • ``_normalize_permissions`` and ``_normalize_roles`` handle every
      shape that middleware can produce.

These tests prove core construction paths, scope resolution (including the
historical-model unwrap case), normalization robustness, and the anonymous
factory – all without requiring a running Keycloak or database.
"""

from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase

from lex.core.models.LexModel import UserContext


def _groups(names=()):
    return SimpleNamespace(values_list=lambda *a, **kw: list(names))


def _user(*, email="u@test.com", is_superuser=False, groups_names=(), **kwargs):
    return SimpleNamespace(
        email=email,
        is_authenticated=True,
        is_superuser=is_superuser,
        groups=_groups(groups_names),
        **kwargs,
    )


def _request(*, user=None, user_permissions=(), userinfo=None, session=None, client_roles=None):
    return SimpleNamespace(
        user=user or _user(),
        user_permissions=list(user_permissions),
        userinfo=userinfo or {},
        client_roles=client_roles or [],
        session=session or {},
    )


def _instance(app_label="myapp", class_name="MyModel", pk=42):
    """Build a lightweight stub whose ``__class__.__name__`` resolves correctly."""
    cls = type(class_name, (), {
        "_meta": SimpleNamespace(app_label=app_label),
    })
    obj = cls.__new__(cls)
    obj._meta = SimpleNamespace(app_label=app_label)
    obj.pk = pk
    return obj


# ── anonymous() ──────────────────────────────────────────────────────────


class UserContextAnonymousTests(SimpleTestCase):
    """``anonymous()`` creates a safe unauthenticated context."""

    def test_is_not_authenticated(self):
        ctx = UserContext.anonymous()
        self.assertFalse(ctx.is_authenticated)

    def test_user_is_none(self):
        self.assertIsNone(UserContext.anonymous().user)

    def test_email_is_empty(self):
        self.assertEqual(UserContext.anonymous().email, "")

    def test_groups_are_empty(self):
        self.assertEqual(UserContext.anonymous().groups, set())

    def test_keycloak_scopes_are_empty(self):
        self.assertEqual(UserContext.anonymous().keycloak_scopes, set())

    def test_client_roles_are_empty(self):
        self.assertEqual(UserContext.anonymous().client_roles, frozenset())

    def test_user_permissions_are_empty(self):
        self.assertEqual(UserContext.anonymous().user_permissions, tuple())


# ── from_request – basic fields ──────────────────────────────────────────


class UserContextFromRequestTests(SimpleTestCase):
    """``from_request`` maps request attributes to context fields."""

    def test_email_comes_from_user(self):
        user = _user(email="alice@corp.com")
        ctx = UserContext.from_request(_request(user=user))
        self.assertEqual(ctx.email, "alice@corp.com")

    def test_is_superuser_propagates(self):
        user = _user(is_superuser=True)
        ctx = UserContext.from_request(_request(user=user))
        self.assertTrue(ctx.is_superuser)

    def test_groups_are_resolved(self):
        user = _user(groups_names=("admins", "editors"))
        ctx = UserContext.from_request(_request(user=user))
        self.assertEqual(ctx.groups, {"admins", "editors"})

    def test_missing_request_returns_anonymous(self):
        ctx = UserContext.from_request(None)
        self.assertFalse(ctx.is_authenticated)

    def test_request_without_user_returns_anonymous(self):
        ctx = UserContext.from_request(SimpleNamespace())
        self.assertFalse(ctx.is_authenticated)


# ── from_request_base + with_instance ────────────────────────────────────


class UserContextBaseAndInstanceTests(SimpleTestCase):
    """Two-phase pattern used in serializer loops."""

    def test_base_has_empty_keycloak_scopes(self):
        req = _request(user_permissions=[
            {"rsname": "myapp.MyModel", "scopes": ["read"], "resource_set_id": None},
        ])
        base = UserContext.from_request_base(req)
        self.assertEqual(base.keycloak_scopes, frozenset())

    def test_with_instance_resolves_keycloak_scopes(self):
        req = _request(user_permissions=[
            {"rsname": "myapp.MyModel", "scopes": ["read", "edit"], "resource_set_id": None},
        ])
        base = UserContext.from_request_base(req)
        instance = _instance()
        ctx = base.with_instance(req, instance)
        self.assertEqual(ctx.keycloak_scopes, {"read", "edit"})

    def test_with_instance_preserves_base_fields(self):
        user = _user(email="base@corp.com", is_superuser=True, groups_names=("admins",))
        req = _request(user=user)
        base = UserContext.from_request_base(req)
        ctx = base.with_instance(req, _instance())
        self.assertEqual(ctx.email, "base@corp.com")
        self.assertTrue(ctx.is_superuser)
        self.assertIn("admins", ctx.groups)


# ── Keycloak scope resolution ────────────────────────────────────────────


class UserContextKeycloakScopeResolutionTests(SimpleTestCase):
    """Keycloak scopes resolve by resource_name + optional resource_set_id."""

    def test_global_permission_matches_any_pk(self):
        perms = [{"rsname": "myapp.MyModel", "scopes": ["read"], "resource_set_id": None}]
        scopes = UserContext._resolve_keycloak_scopes(tuple(perms), _instance(pk=99))
        self.assertIn("read", scopes)

    def test_id_scoped_permission_matches_only_that_pk(self):
        perms = [{"rsname": "myapp.MyModel", "scopes": ["edit"], "resource_set_id": "42"}]
        self.assertIn("edit", UserContext._resolve_keycloak_scopes(tuple(perms), _instance(pk=42)))

    def test_id_scoped_permission_does_not_match_different_pk(self):
        perms = [{"rsname": "myapp.MyModel", "scopes": ["edit"], "resource_set_id": "42"}]
        scopes = UserContext._resolve_keycloak_scopes(tuple(perms), _instance(pk=99))
        self.assertNotIn("edit", scopes)

    def test_different_resource_name_does_not_match(self):
        perms = [{"rsname": "other.OtherModel", "scopes": ["read"], "resource_set_id": None}]
        scopes = UserContext._resolve_keycloak_scopes(tuple(perms), _instance())
        self.assertEqual(scopes, set())

    def test_no_instance_returns_empty(self):
        perms = [{"rsname": "myapp.MyModel", "scopes": ["read"], "resource_set_id": None}]
        self.assertEqual(UserContext._resolve_keycloak_scopes(tuple(perms), None), set())

    def test_original_instance_resource_name_also_matches(self):
        """Keycloak scopes registered under a historical resource name
        should still resolve when the serializer unwraps to the main model."""
        perms = [{"rsname": "core.HistoricalQuarter", "scopes": ["read"], "resource_set_id": None}]
        main_instance = _instance(app_label="core", class_name="Quarter", pk=10)
        historical_instance = _instance(app_label="core", class_name="HistoricalQuarter", pk=10)
        scopes = UserContext._resolve_keycloak_scopes(
            tuple(perms), main_instance, original_instance=historical_instance,
        )
        self.assertIn("read", scopes)


# ── _normalize_permissions ────────────────────────────────────────────────


class UserContextNormalizePermissionsTests(SimpleTestCase):
    """``_normalize_permissions`` handles every shape middleware can produce."""

    def test_none_returns_empty_tuple(self):
        self.assertEqual(UserContext._normalize_permissions(None), tuple())

    def test_empty_list_returns_empty_tuple(self):
        self.assertEqual(UserContext._normalize_permissions([]), tuple())

    def test_non_iterable_returns_empty_tuple(self):
        self.assertEqual(UserContext._normalize_permissions(42), tuple())

    def test_dicts_are_preserved(self):
        raw = [{"rsname": "a", "scopes": ["r"]}]
        result = UserContext._normalize_permissions(raw)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["rsname"], "a")

    def test_non_mapping_items_are_skipped(self):
        raw = [{"rsname": "a"}, "garbage", 42]
        result = UserContext._normalize_permissions(raw)
        self.assertEqual(len(result), 1)


# ── _normalize_roles ──────────────────────────────────────────────────────


class UserContextNormalizeRolesTests(SimpleTestCase):
    """``_normalize_roles`` handles strings, lists, dicts, nested combos."""

    def test_none_returns_empty_frozenset(self):
        self.assertEqual(UserContext._normalize_roles(None), frozenset())

    def test_string_returns_singleton(self):
        self.assertEqual(UserContext._normalize_roles("admin"), frozenset({"admin"}))

    def test_empty_string_returns_empty(self):
        self.assertEqual(UserContext._normalize_roles(""), frozenset())

    def test_list_of_strings(self):
        self.assertEqual(UserContext._normalize_roles(["a", "b"]), frozenset({"a", "b"}))

    def test_dict_flattens_values(self):
        self.assertEqual(
            UserContext._normalize_roles({"realm": ["admin", "user"]}),
            frozenset({"admin", "user"}),
        )

    def test_nested_dict_flattens_recursively(self):
        self.assertEqual(
            UserContext._normalize_roles({"realm": {"client": ["editor"]}}),
            frozenset({"editor"}),
        )


# ── _extract_client_roles ────────────────────────────────────────────────


class UserContextExtractClientRolesTests(SimpleTestCase):
    """Client roles are merged from multiple sources on the request."""

    def test_roles_from_request_userinfo(self):
        req = SimpleNamespace(
            user=_user(),
            userinfo={"client_roles": ["viewer"]},
            session={},
            client_roles=[],
        )
        roles = UserContext._extract_client_roles(req, req.user)
        self.assertIn("viewer", roles)

    def test_roles_from_session_oidc_userinfo(self):
        req = SimpleNamespace(
            user=_user(),
            userinfo={},
            session={"oidc_userinfo": {"client_roles": ["editor"]}},
            client_roles=[],
        )
        roles = UserContext._extract_client_roles(req, req.user)
        self.assertIn("editor", roles)

    def test_roles_from_request_attribute(self):
        req = SimpleNamespace(
            user=_user(),
            userinfo={},
            session={},
            client_roles=["direct_role"],
        )
        roles = UserContext._extract_client_roles(req, req.user)
        self.assertIn("direct_role", roles)

    def test_roles_from_user_attribute(self):
        user = _user(client_roles=["user_role"])
        req = SimpleNamespace(
            user=user,
            userinfo={},
            session={},
            client_roles=[],
        )
        roles = UserContext._extract_client_roles(req, user)
        self.assertIn("user_role", roles)

    def test_multiple_sources_are_merged(self):
        user = _user(roles=["role_a"])
        req = SimpleNamespace(
            user=user,
            userinfo={"client_roles": ["role_b"]},
            session={},
            client_roles=["role_c"],
        )
        roles = UserContext._extract_client_roles(req, user)
        self.assertEqual(roles, frozenset({"role_a", "role_b", "role_c"}))
