"""
Tests for ``PermissionResult`` – the field-level permission primitive.

Documented contract (docs/features/access-and-ui/permissions.md):
    • ``allow_all``  → grants every field
    • ``allow_fields``  → grants an explicit whitelist
    • ``allow_all_except`` → grants everything minus a blacklist
    • ``deny`` / ``deny_all`` → returns an empty field set
    • ``get_fields(all)`` resolves any pattern against the concrete schema

These tests prove every factory method, ``get_fields`` edge-case, and the
human-readable ``__str__`` contract so customers can trust the permission
primitives the framework exposes.
"""

from django.test import SimpleTestCase

from lex.core.models.LexModel import PermissionResult


ALL_FIELDS = {"id", "name", "email", "password", "created_at"}


# ── Factory method semantics ──────────────────────────────────────────────


class PermissionResultAllowAllTests(SimpleTestCase):
    """``allow_all`` grants every concrete field with no exclusions."""

    def test_allowed_flag_is_true(self):
        result = PermissionResult.allow_all("full access")
        self.assertTrue(result.allowed)

    def test_fields_is_none_meaning_unrestricted(self):
        result = PermissionResult.allow_all()
        self.assertIsNone(result.fields)

    def test_excluded_fields_is_none(self):
        result = PermissionResult.allow_all()
        self.assertIsNone(result.excluded_fields)

    def test_get_fields_returns_full_set(self):
        result = PermissionResult.allow_all()
        self.assertEqual(result.get_fields(ALL_FIELDS), ALL_FIELDS)

    def test_reason_is_preserved(self):
        result = PermissionResult.allow_all("superuser bypass")
        self.assertEqual(result.reason, "superuser bypass")

    def test_reason_defaults_to_none(self):
        result = PermissionResult.allow_all()
        self.assertIsNone(result.reason)


class PermissionResultAllowFieldsTests(SimpleTestCase):
    """``allow_fields`` restricts to an explicit whitelist."""

    def test_allowed_flag_is_true(self):
        result = PermissionResult.allow_fields({"name"})
        self.assertTrue(result.allowed)

    def test_get_fields_intersects_with_all_fields(self):
        result = PermissionResult.allow_fields({"name", "nonexistent"})
        self.assertEqual(result.get_fields(ALL_FIELDS), {"name"})

    def test_accepts_list_input(self):
        result = PermissionResult.allow_fields(["name", "email"])
        self.assertEqual(result.get_fields(ALL_FIELDS), {"name", "email"})

    def test_empty_whitelist_returns_empty_set(self):
        result = PermissionResult.allow_fields(set())
        self.assertEqual(result.get_fields(ALL_FIELDS), set())

    def test_reason_is_preserved(self):
        result = PermissionResult.allow_fields({"name"}, "limited view")
        self.assertEqual(result.reason, "limited view")


class PermissionResultAllowAllExceptTests(SimpleTestCase):
    """``allow_all_except`` grants everything minus an exclusion set."""

    def test_allowed_flag_is_true(self):
        result = PermissionResult.allow_all_except({"password"})
        self.assertTrue(result.allowed)

    def test_get_fields_excludes_specified_fields(self):
        result = PermissionResult.allow_all_except({"password", "email"})
        self.assertEqual(result.get_fields(ALL_FIELDS), {"id", "name", "created_at"})

    def test_accepts_list_input(self):
        result = PermissionResult.allow_all_except(["password"])
        self.assertEqual(result.get_fields(ALL_FIELDS), ALL_FIELDS - {"password"})

    def test_excluding_nonexistent_field_is_harmless(self):
        result = PermissionResult.allow_all_except({"nonexistent"})
        self.assertEqual(result.get_fields(ALL_FIELDS), ALL_FIELDS)

    def test_reason_is_preserved(self):
        result = PermissionResult.allow_all_except({"password"}, "mask secrets")
        self.assertEqual(result.reason, "mask secrets")


class PermissionResultDenyTests(SimpleTestCase):
    """``deny`` and ``deny_all`` block all fields."""

    def test_deny_allowed_is_false(self):
        result = PermissionResult.deny("blocked")
        self.assertFalse(result.allowed)

    def test_deny_get_fields_returns_empty(self):
        result = PermissionResult.deny()
        self.assertEqual(result.get_fields(ALL_FIELDS), set())

    def test_deny_all_allowed_is_false(self):
        result = PermissionResult.deny_all("explicit block")
        self.assertFalse(result.allowed)

    def test_deny_all_get_fields_returns_empty(self):
        result = PermissionResult.deny_all()
        self.assertEqual(result.get_fields(ALL_FIELDS), set())

    def test_deny_reason_is_preserved(self):
        result = PermissionResult.deny("no scope")
        self.assertEqual(result.reason, "no scope")


# ── get_fields edge-cases ─────────────────────────────────────────────────


class PermissionResultGetFieldsEdgeCases(SimpleTestCase):
    """Edge-cases in ``get_fields`` resolution logic."""

    def test_empty_all_fields_with_allow_all(self):
        result = PermissionResult.allow_all()
        self.assertEqual(result.get_fields(set()), set())

    def test_empty_all_fields_with_deny(self):
        result = PermissionResult.deny()
        self.assertEqual(result.get_fields(set()), set())

    def test_excluded_fields_given_as_string_is_handled(self):
        """Safety path: excluded_fields stored as a bare string."""
        result = PermissionResult(
            allowed=True, fields=None, excluded_fields="password"
        )
        self.assertEqual(result.get_fields(ALL_FIELDS), ALL_FIELDS - {"password"})

    def test_excluded_fields_given_as_list_is_handled(self):
        """Safety path: excluded_fields stored as a list instead of a set."""
        result = PermissionResult(
            allowed=True, fields=None, excluded_fields=["password", "email"]
        )
        self.assertEqual(result.get_fields(ALL_FIELDS), {"id", "name", "created_at"})

    def test_excluded_fields_given_as_tuple_is_handled(self):
        """Safety path: excluded_fields stored as a tuple."""
        result = PermissionResult(
            allowed=True, fields=None, excluded_fields=("password",)
        )
        self.assertEqual(result.get_fields(ALL_FIELDS), ALL_FIELDS - {"password"})


# ── __str__ contract ──────────────────────────────────────────────────────


class PermissionResultStrTests(SimpleTestCase):
    """``__str__`` provides useful debugging output."""

    def test_deny_str_contains_denied(self):
        self.assertIn("DENIED", str(PermissionResult.deny("blocked")))

    def test_deny_str_contains_reason(self):
        self.assertIn("blocked", str(PermissionResult.deny("blocked")))

    def test_allow_all_str_contains_allowed_all(self):
        self.assertIn("ALLOWED all fields", str(PermissionResult.allow_all()))

    def test_allow_fields_str_lists_fields(self):
        s = str(PermissionResult.allow_fields({"name", "email"}))
        self.assertIn("ALLOWED fields", s)
        self.assertIn("email", s)
        self.assertIn("name", s)

    def test_allow_all_except_str_lists_excluded(self):
        s = str(PermissionResult.allow_all_except({"password"}))
        self.assertIn("ALLOWED all except", s)
        self.assertIn("password", s)

    def test_deny_without_reason(self):
        self.assertIn("No reason given", str(PermissionResult.deny()))
