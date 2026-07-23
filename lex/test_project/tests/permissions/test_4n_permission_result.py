"""Cluster 4n — ``PermissionResult`` value-object contract.

Intent
------

``LexModel.PermissionResult`` is the building-block returned by every
``permission_read`` / ``permission_edit`` / ``permission_export`` override on
customer models.  It encodes *whether* access is allowed and *which* fields
are in scope.  Four factory methods cover the common patterns customers need:
``allow_all()``, ``allow_fields()``, ``allow_all_except()``, ``deny()``.

The ``get_fields(all_field_names)`` resolver turns the abstract result into a
concrete set of visible field names — it is called by serialisers and
viewsets whenever they need the effective field list for a particular user.

A regression in this value object silently breaks every customer model's
permission logic: ``allow_all`` returning the wrong field set grants access
that should be denied; ``deny`` returning a non-empty set exposes fields that
should be hidden.  The constructor and string representation must also reflect
the permission state so that logging and debugging surface actionable output.

Cluster 4n — scenarios 4.75–4.82. Type: U.
Covers: lex/core/models/LexModel.py (PermissionResult).
Run: python -m lex pytest lex/test_project/tests/permissions/test_4n_permission_result.py -v
"""

from __future__ import annotations

from django.test import SimpleTestCase

import pytest

from lex.core.models.LexModel import PermissionResult

pytestmark = pytest.mark.permissions

ALL_FIELDS = {"id", "name", "amount", "created_at"}


class TestCluster04n_PermissionResult(SimpleTestCase):
    """Cluster 4n: PermissionResult factory methods and field resolution."""

    # ------------------------------------------------------------------
    # Factory: allow_all
    # ------------------------------------------------------------------

    def test_4_75_allow_all_sets_allowed_and_null_fields(self) -> None:
        """
        Scenario 4.75: allow_all() marks access allowed with fields=None.

        Given: PermissionResult.allow_all() with an optional reason
        When: the result is inspected
        Then: allowed=True, fields=None (no restriction), excluded_fields=None
        """
        result = PermissionResult.allow_all(reason="superuser")
        self.assertTrue(result.allowed, "allow_all must set allowed=True")
        self.assertIsNone(result.fields, "allow_all must leave fields=None (all fields)")
        self.assertIsNone(result.excluded_fields, "allow_all must leave excluded_fields=None")
        self.assertEqual(result.reason, "superuser", "reason must be preserved")

    def test_4_76_allow_all_get_fields_returns_all(self) -> None:
        """
        Scenario 4.76: get_fields on allow_all returns the complete field set.

        Given: allow_all result, a known set of all field names
        When: get_fields(ALL_FIELDS) is called
        Then: returns ALL_FIELDS (all fields visible)
        """
        result = PermissionResult.allow_all()
        self.assertEqual(
            result.get_fields(ALL_FIELDS),
            ALL_FIELDS,
            "allow_all.get_fields must return all field names",
        )

    # ------------------------------------------------------------------
    # Factory: allow_fields
    # ------------------------------------------------------------------

    def test_4_77_allow_fields_limits_to_specified_set(self) -> None:
        """
        Scenario 4.77: allow_fields restricts access to a specified subset.

        Given: PermissionResult.allow_fields({"id", "name"})
        When: get_fields(ALL_FIELDS) is called
        Then: returns only {"id", "name"} — the intersection
        """
        result = PermissionResult.allow_fields({"id", "name"})
        self.assertTrue(result.allowed, "allow_fields must set allowed=True")
        resolved = result.get_fields(ALL_FIELDS)
        self.assertEqual(resolved, {"id", "name"}, "only the allowed fields must be returned")

    def test_4_78_allow_fields_accepts_list_and_converts_to_set(self) -> None:
        """
        Scenario 4.78: allow_fields accepts a list and converts it to a set.

        Given: PermissionResult.allow_fields(["id", "amount"])
        When: the result is inspected
        Then: result.fields is a set
        """
        result = PermissionResult.allow_fields(["id", "amount"])
        self.assertIsInstance(result.fields, set, "allow_fields must convert list to set")
        self.assertEqual(result.fields, {"id", "amount"})

    # ------------------------------------------------------------------
    # Factory: allow_all_except
    # ------------------------------------------------------------------

    def test_4_79_allow_all_except_excludes_listed_fields(self) -> None:
        """
        Scenario 4.79: allow_all_except returns all fields minus the excluded set.

        Given: PermissionResult.allow_all_except({"amount"})
        When: get_fields(ALL_FIELDS) is called
        Then: returns ALL_FIELDS - {"amount"}
        """
        result = PermissionResult.allow_all_except({"amount"})
        self.assertTrue(result.allowed, "allow_all_except must set allowed=True")
        resolved = result.get_fields(ALL_FIELDS)
        self.assertEqual(
            resolved,
            ALL_FIELDS - {"amount"},
            "excluded fields must be removed from the result",
        )

    # ------------------------------------------------------------------
    # Factory: deny / deny_all
    # ------------------------------------------------------------------

    def test_4_80_deny_sets_allowed_false_and_empty_fields(self) -> None:
        """
        Scenario 4.80: deny() sets allowed=False; get_fields returns empty set.

        Given: PermissionResult.deny(reason="unauthorized")
        When: get_fields(ALL_FIELDS) is called
        Then: allowed=False, get_fields returns an empty set
        """
        result = PermissionResult.deny(reason="unauthorized")
        self.assertFalse(result.allowed, "deny must set allowed=False")
        self.assertEqual(
            result.get_fields(ALL_FIELDS),
            set(),
            "denied access must yield an empty field set",
        )
        self.assertEqual(result.reason, "unauthorized", "reason must be preserved on deny")

    def test_4_81_deny_all_is_identical_to_deny(self) -> None:
        """
        Scenario 4.81: deny_all() is a named alias for deny() — same semantics.

        Given: PermissionResult.deny_all()
        When: get_fields is called
        Then: result is equivalent to deny() — allowed=False, empty field set
        """
        result = PermissionResult.deny_all()
        self.assertFalse(result.allowed, "deny_all must set allowed=False")
        self.assertEqual(
            result.get_fields(ALL_FIELDS),
            set(),
            "deny_all must yield an empty field set identical to deny",
        )

    # ------------------------------------------------------------------
    # String representation
    # ------------------------------------------------------------------

    def test_4_82_str_reflects_permission_state(self) -> None:
        """
        Scenario 4.82: __str__ returns a human-readable summary.

        Given: allow_all, allow_fields, deny results
        When: str() is called
        Then: each string starts with ALLOWED or DENIED
        """
        allow_str = str(PermissionResult.allow_all(reason="admin"))
        deny_str = str(PermissionResult.deny(reason="blocked"))
        fields_str = str(PermissionResult.allow_fields({"id", "name"}))

        self.assertIn("ALLOWED", allow_str, "allow_all str must contain ALLOWED")
        self.assertIn("DENIED", deny_str, "deny str must contain DENIED")
        self.assertIn("ALLOWED", fields_str, "allow_fields str must contain ALLOWED")
