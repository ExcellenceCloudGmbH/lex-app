"""
Cluster 12a: Read contract — field visibility & framework-managed fields.

Intent (from docs/api/ and docs/reference/):

    Every record the REST API serializes carries the framework-managed
    keys — ``id``, ``id_field``, ``short_description``, and
    ``lex_reserved_scopes`` — alongside the model fields the caller is
    permitted to read. ``short_description`` is the model's ``__str__``.
    ``lex_reserved_scopes`` is a dict of ``{"edit": [...], "delete":
    bool, "export": bool}`` where ``edit`` is a sorted list of the
    writable field names. When ``permission_read`` restricts field
    visibility, the JSON body MUST reflect that restriction exactly —
    and when ``permission_read`` denies the record entirely, the
    ``FilteredListSerializer`` drops it from list responses.

Scenario numbering matches
docs/test-plan/test-clusters.md#12-serializer-contract.
"""

from __future__ import annotations

import unittest

from lex.test_project.tests._e2e_test_case import E2ETestCase
from rest_framework import status

from .models import (
    ALL_MODELS,
    PROTECTED_WIDE,
    WIDE,
    ProtectedWideItem,
    WideItem,
)

import pytest

pytestmark = pytest.mark.serializers

FRAMEWORK_KEYS = {"id", "id_field", "short_description", "lex_reserved_scopes"}


class TestCluster12a_ReadContract(E2ETestCase):
    """GET /api/<model>/<pk>/ — JSON shape contract."""

    e2e_models = ALL_MODELS

    # -- 12.1 ----------------------------------------------------------
    def test_12_1_detail_contains_framework_managed_keys(self) -> None:
        """Scenario 12.1: framework-managed keys present in every detail response."""
        item = WideItem.objects.create(name="alpha", amount="10.0000")

        resp = self.client.get(self.url_detail(WIDE, item.pk))
        self.assertEqual(resp.status_code, status.HTTP_200_OK)

        missing = FRAMEWORK_KEYS - set(resp.data.keys())
        self.assertFalse(
            missing,
            f"Framework-managed keys missing from detail response: {missing}. "
            f"Got keys: {sorted(resp.data.keys())}",
        )
        # Model fields are also present — the serializer does not drop them.
        for field_name in ("name", "amount", "category", "payload"):
            self.assertIn(
                field_name, resp.data,
                f"Model field {field_name!r} must be in detail response",
            )

    # -- 12.2 ----------------------------------------------------------
    def test_12_2_short_description_uses_model_str(self) -> None:
        """Scenario 12.2: ``short_description`` == ``str(instance)``."""
        item = WideItem.objects.create(name="bravo")

        resp = self.client.get(self.url_detail(WIDE, item.pk))
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(
            resp.data["short_description"],
            str(item),
            "short_description must be the model's __str__ — not the "
            "auto-generated 'Model object (pk)' default",
        )

    # -- 12.3 ----------------------------------------------------------
    def test_12_3_lex_reserved_scopes_shape(self) -> None:
        """Scenario 12.3: scopes dict has exactly ``{edit, delete, export}``."""
        item = WideItem.objects.create(name="charlie")

        resp = self.client.get(self.url_detail(WIDE, item.pk))
        self.assertEqual(resp.status_code, status.HTTP_200_OK)

        scopes = resp.data["lex_reserved_scopes"]
        self.assertEqual(
            set(scopes.keys()), {"edit", "delete", "export"},
            f"lex_reserved_scopes must have exactly these keys — got {sorted(scopes.keys())}",
        )
        self.assertIsInstance(
            scopes["edit"], list,
            "scopes.edit must be a list of field names",
        )
        self.assertEqual(
            scopes["edit"], sorted(scopes["edit"]),
            "scopes.edit must be sorted for stable UI rendering",
        )
        self.assertIsInstance(scopes["delete"], bool, "scopes.delete must be bool")
        self.assertIsInstance(scopes["export"], bool, "scopes.export must be bool")

    # -- 12.4 ----------------------------------------------------------
    def test_12_4_permission_read_restricts_visible_fields(self) -> None:
        """Scenario 12.4: ``allow_fields({"name","amount"})`` → only those
        (plus framework-managed keys) appear in the JSON.
        """
        item = ProtectedWideItem.objects.create(
            name="delta", amount="42.0000",
            secret_note="top-secret", secret_category="beta",
        )

        resp = self.client.get(self.url_detail(PROTECTED_WIDE, item.pk))
        self.assertEqual(resp.status_code, status.HTTP_200_OK)

        keys = set(resp.data.keys())
        allowed_data_keys = {"name", "amount"}

        # Framework-managed keys always survive.
        for k in FRAMEWORK_KEYS:
            self.assertIn(
                k, keys,
                f"Framework-managed key {k!r} must survive permission filtering",
            )

        # Explicitly-allowed fields survive.
        for k in allowed_data_keys:
            self.assertIn(
                k, keys, f"Allowed field {k!r} missing from response",
            )

        # Restricted fields MUST be stripped.
        for k in ("secret_note", "secret_category"):
            self.assertNotIn(
                k, keys,
                f"Restricted field {k!r} leaked through to JSON — "
                f"permission_read is not enforced on serialization",
            )

    # -- 12.5 ----------------------------------------------------------
    def test_12_5_permission_read_deny_all_omits_from_list(self) -> None:
        """Scenario 12.5: when ``permission_read`` denies entirely, the
        record must NOT appear in the list response (FilteredListSerializer
        drops rows that serialize to ``{}``)."""

        # Two rows the caller can read.
        visible_a = WideItem.objects.create(name="echo-visible-1")
        visible_b = WideItem.objects.create(name="echo-visible-2")

        # One row on ProtectedWideItem — the fixture's ``allow_fields``
        # for non-admins includes ``id/name/amount``, so it IS visible.
        # To exercise a hard deny we monkey-patch ``permission_read`` on
        # a single instance-class method for this test.
        from lex.core.models.LexModel import PermissionResult

        original = ProtectedWideItem.permission_read

        def _deny(self, uc):
            if self.name == "deny-me":
                return PermissionResult.deny("hard deny for 12.5")
            return original(self, uc)

        ProtectedWideItem.permission_read = _deny
        try:
            denied = ProtectedWideItem.objects.create(name="deny-me", amount="1")
            also_visible = ProtectedWideItem.objects.create(name="also-visible")

            # List the ProtectedWideItem endpoint.
            resp = self.list_get(PROTECTED_WIDE)
            self.assertEqual(resp.status_code, status.HTTP_200_OK)
            rows = self.extract_results(resp.data)

            ids = {row.get("id") for row in rows if isinstance(row, dict)}
            self.assertIn(also_visible.pk, ids, "allowed row missing from list")
            self.assertNotIn(
                denied.pk, ids,
                f"Row denied by permission_read leaked into list response "
                f"— FilteredListSerializer did not drop it. Got ids={ids}",
            )
        finally:
            ProtectedWideItem.permission_read = original
            # Silence the unused-local warnings.
            _ = (visible_a, visible_b)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()

