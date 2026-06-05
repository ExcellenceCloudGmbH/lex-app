"""
Cluster 4a: Field-level permissions.

Intent (from docs/features/permissions/):

    ``permission_read`` / ``permission_edit`` / ``permission_export`` return
    a :class:`PermissionResult` that names exactly which fields the caller
    may see or change. The REST API must honour that result — responses
    include only permitted fields; PATCHes to restricted fields must be
    ignored or rejected.

Scenario numbering matches
docs/test-plan/test-clusters.md#4-permissions.

Profile matrix
--------------
:class:`FieldLevelItem` enforces three tiers:

* superuser       → all fields
* ``hr`` group    → all fields except ``pii_ssn``
* regular user    → only ``id`` + ``public_name``

Each tier gets its own test class via
:class:`AuthenticatedE2ETestCase`, so the profile is obvious from the
class declaration and there is no per-test user-mutation boilerplate.
"""

from __future__ import annotations

import unittest

from lex.core.models.LexModel import PermissionResult, UserContext
from lex.test_project.tests._authenticated_e2e_test_case import AuthenticatedE2ETestCase
from lex.test_project.tests._e2e_test_case import E2ETestCase
from rest_framework import status

from .models import ALL_MODELS, FIELD_LEVEL, FieldLevelItem

import pytest

pytestmark = pytest.mark.permissions


class TestCluster04a_FieldLevel_Superuser(AuthenticatedE2ETestCase):
    """Superuser sees every field."""

    e2e_models = ALL_MODELS
    as_superuser = True

    # -- 4.1 -----------------------------------------------------------
    def test_4_1_superuser_reads_all_fields(self) -> None:
        """Scenario 4.1: Superuser — ``permission_read`` returns ``allow_all``."""
        FieldLevelItem.objects.create(
            public_name="alpha", sensitive_salary=100, pii_ssn="111-22-3333",
        )

        uc = UserContext(
            user=self.user, email=self.user.email,
            is_authenticated=True, is_superuser=True,
            groups=set(), keycloak_scopes=set(),
        )
        item = FieldLevelItem.objects.get(public_name="alpha")
        result = item.permission_read(uc)

        self.assertTrue(
            result.allowed,
            "Superuser must receive an allowed PermissionResult",
        )
        self.assertIsNone(
            result.fields,
            "Superuser allow_all must not restrict to a specific field set",
        )

    # -- 4.1b ----------------------------------------------------------
    def test_4_1b_superuser_api_response_contains_every_field(self) -> None:
        """
        Scenario 4.1b: Superuser's API response includes ``sensitive_salary``
        and ``pii_ssn``. End-to-end complement to 4.1.
        """
        FieldLevelItem.objects.create(
            public_name="alpha-api", sensitive_salary=100, pii_ssn="999-99-9999",
        )

        resp = self.client.get(self.url_list(FIELD_LEVEL))

        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        rows = self.extract_results(resp.data)
        self.assertTrue(rows, "Superuser must see at least one row")
        row = rows[0]
        for field in ("public_name", "sensitive_salary", "pii_ssn"):
            self.assertIn(
                field, row,
                f"Superuser must see {field!r} in the API response; "
                f"keys present: {sorted(row.keys())!r}",
            )


class TestCluster04a_FieldLevel_HR(AuthenticatedE2ETestCase):
    """HR group sees everything except ``pii_ssn``."""

    e2e_models = ALL_MODELS
    extra_groups = frozenset({"hr"})

    # -- 4.2 -----------------------------------------------------------
    def test_4_2_hr_user_sees_allowed_fields_only(self) -> None:
        """
        Scenario 4.2: HR user's API response excludes ``pii_ssn``.

        End-to-end proof the permission-aware serializer honours
        ``PermissionResult.allow_all_except``.
        """
        FieldLevelItem.objects.create(
            public_name="bravo", sensitive_salary=100, pii_ssn="000-00-0000",
        )

        resp = self.client.get(self.url_list(FIELD_LEVEL))

        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        rows = self.extract_results(resp.data)
        self.assertTrue(rows, "At least one row expected for HR user")
        row = rows[0]
        self.assertIn(
            "sensitive_salary", row,
            "HR must see sensitive_salary (not excluded)",
        )
        self.assertNotIn(
            "pii_ssn", row,
            "HR must not see pii_ssn — allow_all_except({'pii_ssn'}) "
            "must strip it from the response",
        )


class TestCluster04a_FieldLevel_Regular(AuthenticatedE2ETestCase):
    """Regular user sees only public fields."""

    e2e_models = ALL_MODELS

    # -- 4.2b ----------------------------------------------------------
    def test_4_2b_regular_user_sees_only_public_fields(self) -> None:
        """Scenario 4.2b: Regular user — only ``id`` + ``public_name`` in response."""
        FieldLevelItem.objects.create(
            public_name="charlie", sensitive_salary=100, pii_ssn="000-00-0000",
        )

        resp = self.client.get(self.url_list(FIELD_LEVEL))

        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        rows = self.extract_results(resp.data)
        self.assertTrue(rows, "Regular user must still see at least one row")
        row = rows[0]
        self.assertIn("public_name", row, "public_name must be allowed")
        self.assertNotIn(
            "sensitive_salary", row,
            "Regular users must not see sensitive_salary",
        )
        self.assertNotIn(
            "pii_ssn", row,
            "Regular users must not see pii_ssn",
        )

    # -- 4.9 -----------------------------------------------------------
    def test_4_9_legacy_can_read_matches_permission_read(self) -> None:
        """
        Scenario 4.9: ``can_read(request)`` returns the same allowed
        field set as ``permission_read``.
        """
        item = FieldLevelItem.objects.create(
            public_name="legacy", sensitive_salary=1, pii_ssn="000",
        )

        uc = UserContext(
            user=self.user, email=self.user.email,
            is_authenticated=True, is_superuser=False,
            groups=set(), keycloak_scopes=set(),
        )
        via_permission_read = item.permission_read(uc).get_fields(
            {f.name for f in item._meta.get_fields() if hasattr(f, "name")}
        )

        class _Req:
            pass
        req = _Req()
        req.user = self.user
        via_can_read = item.can_read(req)

        self.assertEqual(
            set(via_can_read), set(via_permission_read),
            "Legacy can_read must match permission_read. "
            f"permission_read={via_permission_read!r}, can_read={via_can_read!r}",
        )


class TestCluster04a_FieldLevel_PermissionResult(E2ETestCase):
    """Pure PermissionResult helpers — no user context needed."""

    e2e_models = ALL_MODELS

    # -- 4.3 -----------------------------------------------------------
    def test_4_3_allow_all_except_hides_excluded_fields(self) -> None:
        """Scenario 4.3: ``allow_all_except`` excludes sensitive fields."""
        result = PermissionResult.allow_all_except(
            {"pii_ssn"}, "hr sees everything except ssn",
        )
        all_fields = {"id", "public_name", "sensitive_salary", "pii_ssn"}
        resolved = result.get_fields(all_fields)
        self.assertIn("sensitive_salary", resolved)
        self.assertNotIn(
            "pii_ssn", resolved,
            "allow_all_except must strip the named field from the resolved set",
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()

