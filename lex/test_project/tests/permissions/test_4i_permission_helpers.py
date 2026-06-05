"""
Cluster 4i: ``LexModel`` permission helper convenience methods.

Intent (from docs/features/permissions/ and ``LexModel`` public API):

    Customers override ``permission_read`` / ``permission_edit`` /
    ``permission_export`` on their models. To make the most common
    patterns one-liners, ``LexModel`` ships a set of **shorthand
    helpers** customers compose inside those overrides:

        def permission_read(self, uc):
            return (
                self.allow_all_if_superuser(uc)
                or self.allow_all_if_in_groups(uc, "finance")
                or self.allow_fields_if_owner(uc, fields={"name", "secret"})
                or self.keycloak_fallback(uc, "read")
            )

    Each helper has a tightly-defined contract (return ``None`` when
    inapplicable so the caller can fall through; return a concrete
    ``PermissionResult`` when applicable). A drift here silently
    weakens every customer model's permission code — either by
    granting access that should be denied (helper returns
    ``allow_all`` when it shouldn't) or by denying access that should
    be granted (helper returns ``None`` instead of allowing).

    This sub-cluster also pins the **legacy** ``can_read`` /
    ``can_edit`` / ``can_export`` / ``can_create`` / ``can_delete`` /
    ``can_list`` adapters that wrap the new permission methods for
    customer code still calling the old API.

**Why a sub-cluster of 4 (Permissions):** these helpers compose with
the same ``UserContext`` / ``PermissionResult`` building blocks as
4a–4h. They are not their own feature — they are **convenience
shortcuts** customers reach for when overriding the same hooks
4a–4h test against.

**Scenario numbering** continues in the 4.27 – 4.39 free band
(after 4f's last 4.26 and before 4h's 4.40).

**How to run:**

    .. code-block:: bash

        lex test lex.test_project.tests.permissions.test_4i_permission_helpers \\
            --verbosity=2 --noinput --keepdb
"""

from __future__ import annotations

import unittest

from django.contrib.auth.models import User
from django.test import RequestFactory, SimpleTestCase
from lex.core.models.LexModel import PermissionResult, UserContext
from lex.test_project.tests._e2e_test_case import E2ETestCase

from .models import ALL_MODELS, FieldLevelItem, OwnedItem, ProtectedItem

import pytest

pytestmark = pytest.mark.permissions


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  Helpers — UserContext factories
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def _uc(*, is_superuser=False, is_authenticated=True,
        groups=frozenset(), keycloak_scopes=frozenset(),
        user=None, email="user@example.com") -> UserContext:
    """Build a ``UserContext`` matching what the Keycloak middleware would produce."""
    return UserContext(
        user=user,
        email=email,
        is_authenticated=is_authenticated,
        is_superuser=is_superuser,
        groups=set(groups),
        keycloak_scopes=set(keycloak_scopes),
    )


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 4.27 — allow_all_if_superuser
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestCluster04i_AllowAllIfSuperuser(SimpleTestCase):
    """Scenario 4.27 — ``allow_all_if_superuser`` short-circuit."""

    def test_4_27_returns_allow_all_for_superuser(self) -> None:
        """Superuser → returns ``PermissionResult.allow_all(reason)`` so the caller short-circuits."""
        item = ProtectedItem(name="x")
        result = item.allow_all_if_superuser(_uc(is_superuser=True))

        self.assertIsInstance(result, PermissionResult)
        self.assertTrue(result.allowed)
        # ``allow_all`` is the no-restriction sentinel: ``fields`` is None.
        self.assertIsNone(result.fields)
        self.assertEqual(result.reason, "Superuser access")

    def test_4_27_returns_none_for_non_superuser(self) -> None:
        """Non-superuser → returns ``None`` so the caller falls through to the next helper."""
        item = ProtectedItem(name="x")
        self.assertIsNone(item.allow_all_if_superuser(_uc(is_superuser=False)))

    def test_4_27_custom_reason_propagated(self) -> None:
        """Custom ``reason`` argument flows through to the result."""
        item = ProtectedItem(name="x")
        result = item.allow_all_if_superuser(_uc(is_superuser=True), reason="custom")
        self.assertEqual(result.reason, "custom")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 4.28 — allow_all_if_in_groups
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestCluster04i_AllowAllIfInGroups(SimpleTestCase):
    """Scenario 4.28 — group membership → ``allow_all``."""

    def test_4_28_str_argument_treated_as_single_group(self) -> None:
        """Passing a bare string is normalised to a one-element set (the documented shorthand)."""
        item = ProtectedItem(name="x")
        result = item.allow_all_if_in_groups(_uc(groups={"finance"}), "finance")
        self.assertIsInstance(result, PermissionResult)
        self.assertTrue(result.allowed)

    def test_4_28_set_membership_intersection(self) -> None:
        """Any overlap between the user's groups and the required set allows."""
        item = ProtectedItem(name="x")
        result = item.allow_all_if_in_groups(
            _uc(groups={"finance", "ops"}), {"audit", "finance"},
        )
        self.assertTrue(result.allowed)

    def test_4_28_no_overlap_returns_none(self) -> None:
        """No group overlap → ``None`` so the caller falls through."""
        item = ProtectedItem(name="x")
        self.assertIsNone(
            item.allow_all_if_in_groups(_uc(groups={"sales"}), {"finance"}),
        )

    def test_4_28_default_reason_lists_required_groups(self) -> None:
        """Default ``reason`` mentions the matching groups so audit logs can attribute the decision."""
        item = ProtectedItem(name="x")
        result = item.allow_all_if_in_groups(_uc(groups={"finance"}), "finance")
        self.assertIn("finance", (result.reason or ""))


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 4.29 — allow_fields_if_owner (DB-backed: needs a real instance + auth.User)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestCluster04i_AllowFieldsIfOwner(E2ETestCase):
    """Scenario 4.29 — ``allow_fields_if_owner`` ownership check."""

    e2e_models = ALL_MODELS

    def setUp(self) -> None:
        super().setUp()
        self.alice = User.objects.create(username="alice")
        self.bob = User.objects.create(username="bob")
        self.item = OwnedItem.objects.create(
            owner=self.alice, name="x", secret="confidential",
        )

    def test_4_29_owner_with_explicit_fields_returns_allow_fields(self) -> None:
        """Owner + explicit ``fields=`` → ``allow_fields(...)``."""
        result = self.item.allow_fields_if_owner(
            _uc(user=self.alice), fields={"name", "secret"},
        )
        self.assertIsInstance(result, PermissionResult)
        self.assertTrue(result.allowed)
        self.assertEqual(result.fields, {"name", "secret"})

    def test_4_29_owner_with_excluded_fields_returns_allow_all_except(self) -> None:
        """Owner + ``excluded_fields=`` → ``allow_all_except(...)``."""
        result = self.item.allow_fields_if_owner(
            _uc(user=self.alice), excluded_fields={"secret"},
        )
        self.assertTrue(result.allowed)
        self.assertEqual(result.excluded_fields, {"secret"})
        self.assertIsNone(result.fields)

    def test_4_29_owner_with_no_fields_returns_allow_all(self) -> None:
        """Owner + neither ``fields`` nor ``excluded_fields`` → ``allow_all`` (full access)."""
        result = self.item.allow_fields_if_owner(_uc(user=self.alice))
        self.assertTrue(result.allowed)
        self.assertIsNone(result.fields)
        self.assertIsNone(result.excluded_fields)

    def test_4_29_non_owner_returns_none(self) -> None:
        """Different user → ``None`` so the caller falls through."""
        self.assertIsNone(
            self.item.allow_fields_if_owner(_uc(user=self.bob)),
        )

    def test_4_29_unauthenticated_returns_none(self) -> None:
        """Anonymous user is never an owner — short-circuit before the FK lookup runs."""
        self.assertIsNone(
            self.item.allow_fields_if_owner(
                _uc(user=None, is_authenticated=False),
            ),
        )

    def test_4_29_alternate_owner_field_name_supported(self) -> None:
        """``owner_field`` argument lets customers point at a non-default FK name."""
        # OwnedItem only has ``owner`` — when we point the helper at a
        # missing field name it must fall through (``getattr(..., None)``
        # returns None which won't equal user.user) — and never raise.
        result = self.item.allow_fields_if_owner(
            _uc(user=self.alice), owner_field="not_a_real_field",
        )
        self.assertIsNone(result)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 4.30 — keycloak_fallback
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestCluster04i_KeycloakFallback(SimpleTestCase):
    """Scenario 4.30 — ``keycloak_fallback`` returns a *concrete* result, not None.

    Unlike the other helpers, ``keycloak_fallback`` is the **terminal**
    helper — it always returns a concrete allow / deny because there is
    nothing else to fall through to. This is what makes it safe at the
    end of an ``or``-chain.
    """

    def test_4_30_scope_present_returns_allow_all(self) -> None:
        item = ProtectedItem(name="x")
        result = item.keycloak_fallback(
            _uc(keycloak_scopes={"read"}), "read",
        )
        self.assertTrue(result.allowed)
        self.assertIn("read", (result.reason or ""))

    def test_4_30_scope_missing_returns_deny(self) -> None:
        """No matching scope → returns ``deny`` (not ``None``) — terminal helper."""
        item = ProtectedItem(name="x")
        result = item.keycloak_fallback(_uc(keycloak_scopes=set()), "read")
        self.assertFalse(result.allowed)
        # ``deny`` carries an empty fields set — nothing visible.
        self.assertEqual(result.fields, set())

    def test_4_30_unrelated_scope_does_not_satisfy(self) -> None:
        """Having ``write`` does not satisfy a ``read`` check — exact match required."""
        item = ProtectedItem(name="x")
        result = item.keycloak_fallback(
            _uc(keycloak_scopes={"write"}), "read",
        )
        self.assertFalse(result.allowed)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 4.31 — allow_all_except_sensitive (default sensitive set)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestCluster04i_AllowAllExceptSensitive(SimpleTestCase):
    """Scenario 4.31 — sensitive-field exclusion shortcut."""

    def test_4_31_default_sensitive_set_excludes_pii(self) -> None:
        """No argument → uses the documented PII default set."""
        item = ProtectedItem(name="x")
        result = item.allow_all_except_sensitive(_uc())
        self.assertTrue(result.allowed)
        self.assertIsNotNone(result.excluded_fields)
        # Documented defaults include the obvious PII names.
        for sensitive in ("password", "ssn", "credit_card", "bank_account"):
            self.assertIn(sensitive, result.excluded_fields)

    def test_4_31_custom_sensitive_list_overrides_default(self) -> None:
        """Explicit ``sensitive_fields`` replaces the default set entirely."""
        item = ProtectedItem(name="x")
        result = item.allow_all_except_sensitive(_uc(), {"my_secret"})
        self.assertEqual(result.excluded_fields, {"my_secret"})


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 4.32 — allow_public_fields / allow_basic_fields
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestCluster04i_AllowPublicAndBasicFields(SimpleTestCase):
    """Scenario 4.32 — ``allow_public_fields`` / ``allow_basic_fields`` return a documented allowlist."""

    def test_4_32_public_fields_returns_documented_allowlist(self) -> None:
        item = ProtectedItem(name="x")
        result = item.allow_public_fields(_uc())
        self.assertTrue(result.allowed)
        # Documented public set: id, name, title, description, created_at, edited_at, updated_at
        self.assertEqual(
            result.fields,
            {"id", "name", "title", "description",
             "created_at", "edited_at", "updated_at"},
        )

    def test_4_32_basic_fields_returns_documented_allowlist(self) -> None:
        item = ProtectedItem(name="x")
        result = item.allow_basic_fields(_uc())
        self.assertTrue(result.allowed)
        self.assertEqual(
            result.fields, {"id", "name", "email", "created_at"},
        )


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 4.33 — Helper composition: chaining produces the documented short-circuit
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestCluster04i_HelperCompositionContract(SimpleTestCase):
    """Scenario 4.33 — helper return-shape contract that makes ``or``-chaining safe.

    The whole point of the convenience helpers is that they compose
    cleanly in the documented one-liner:

        return (
            self.allow_all_if_superuser(uc)
            or self.allow_all_if_in_groups(uc, "finance")
            or self.keycloak_fallback(uc, "read")
        )

    For this to be safe, every "intermediate" helper (anything but the
    terminal ``keycloak_fallback`` / ``allow_*_fields``) must return
    ``None`` — not ``False``, not a denied ``PermissionResult`` —
    when the caller should fall through to the next clause. This
    scenario locks that contract down in one place.
    """

    def test_4_33_intermediate_helpers_return_none_or_permission_result(self) -> None:
        item = ProtectedItem(name="x")
        # Each intermediate helper called with a context that does NOT
        # satisfy it must return ``None``, never a denied result.
        self.assertIsNone(item.allow_all_if_superuser(_uc()))
        self.assertIsNone(item.allow_all_if_in_groups(_uc(), "finance"))
        # And when satisfied must return a PermissionResult, not ``True``.
        ok = item.allow_all_if_superuser(_uc(is_superuser=True))
        self.assertIsInstance(ok, PermissionResult)

    def test_4_33_documented_chain_short_circuits_at_first_match(self) -> None:
        """The documented chain stops at the first helper that returns truthy.

        We assert behaviour of the actual ``or``-chain because the
        return-type contract is what enables it. If a helper started
        returning ``False`` instead of ``None``, the chain would
        silently return False (truthy-equivalent) and skip the
        remaining helpers — a real customer-facing regression.
        """
        item = ProtectedItem(name="x")
        result = (
            item.allow_all_if_superuser(_uc(is_superuser=True))
            or item.keycloak_fallback(_uc(), "read")
        )
        self.assertIsInstance(result, PermissionResult)
        # Superuser short-circuited before the fallback could deny.
        self.assertTrue(result.allowed)
        self.assertEqual(result.reason, "Superuser access")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 4.34 — Legacy can_*(request) compatibility adapters
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestCluster04i_LegacyCanMethods(E2ETestCase):
    """Scenario 4.34 — legacy ``can_read`` / ``can_edit`` / ``can_export`` /
    ``can_create`` / ``can_delete`` / ``can_list`` adapters call the
    matching new permission method and reduce its result to the
    legacy return shape.

    Field-returning adapters (``can_read`` / ``can_edit`` /
    ``can_export``) collapse a ``PermissionResult`` to the
    ``Set[str]`` of allowed field names by calling
    ``result.get_fields(self._get_all_field_names())``.

    Boolean adapters (``can_create`` / ``can_delete`` / ``can_list``)
    return the underlying predicate's bool directly.
    """

    e2e_models = ALL_MODELS

    def setUp(self) -> None:
        super().setUp()
        self.factory = RequestFactory()
        self.user = User.objects.create_user(username="alice", is_superuser=False)
        self.admin = User.objects.create_user(username="root", is_superuser=True)

    def _request(self, user) -> object:
        request = self.factory.get("/dummy/")
        request.user = user
        return request

    def test_4_34_can_read_returns_set_of_allowed_fields(self) -> None:
        """``can_read`` returns ``Set[str]`` matching ``permission_read``'s ``allow_fields``."""
        # FieldLevelItem.permission_read returns
        # ``allow_fields({"id", "public_name"})`` for non-superusers.
        item = FieldLevelItem.objects.create(public_name="x")
        fields = item.can_read(self._request(self.user))
        self.assertIsInstance(fields, set)
        self.assertEqual(fields, {"id", "public_name"})

        # Superuser sees everything (``allow_all`` → all model fields).
        admin_fields = item.can_read(self._request(self.admin))
        self.assertIn("pii_ssn", admin_fields)
        self.assertIn("sensitive_salary", admin_fields)

    def test_4_34_can_create_returns_bool_from_permission_create(self) -> None:
        """``can_create`` returns the predicate's ``bool`` directly."""
        item = ProtectedItem(name="x")
        # ProtectedItem.permission_create denies non-superusers.
        self.assertFalse(item.can_create(self._request(self.user)))
        self.assertTrue(item.can_create(self._request(self.admin)))

    def test_4_34_can_delete_returns_bool_from_permission_delete(self) -> None:
        item = ProtectedItem(name="x")
        self.assertFalse(item.can_delete(self._request(self.user)))
        self.assertTrue(item.can_delete(self._request(self.admin)))

    def test_4_34_can_list_returns_bool_from_permission_list(self) -> None:
        item = ProtectedItem(name="x")
        # FieldLevelItem.permission_list returns True for everyone.
        self.assertTrue(item.can_list(self._request(self.user)))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()

