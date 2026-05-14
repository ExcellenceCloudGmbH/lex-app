"""
Cluster 4b: Action-level permissions.

Intent (from docs/features/permissions/):

    ``permission_create`` / ``permission_delete`` / ``permission_list``
    return a ``bool``. When False, the corresponding HTTP verb must be
    rejected with a 403 (or 401 if the caller is anonymous).
    ``permission_edit`` may further restrict which *fields* are
    mutable — PATCH of a field outside the permitted set must be
    rejected or ignored.

Scenario numbering matches
docs/test-plan/test-clusters.md#4-permissions.
"""

from __future__ import annotations

import unittest

from lex.tests.e2e._authenticated_e2e_test_case import AuthenticatedE2ETestCase
from lex.tests.e2e._e2e_test_case import E2ETestCase
from rest_framework import status

from .models import ALL_MODELS, PROTECTED, ProtectedItem


class TestCluster04b_ActionLevel(E2ETestCase):
    """Action-level permission contract — non-admin caller."""

    e2e_models = ALL_MODELS

    # -- 4.4 -----------------------------------------------------------
    # @unittest.expectedFailure  # BUG-010: permission_edit field-restriction not enforced on PATCH
    # def test_4_4_permission_edit_restricts_editable_fields(self) -> None:
    #     """
    #     Scenario 4.4: PATCH to a field outside the permitted set must
    #     leave that field unchanged.
    #
    #     ``ProtectedItem.permission_edit`` allows only ``name`` for
    #     non-admins. A non-admin PATCH of ``secret`` must either be
    #     rejected (4xx) or silently ignored (200 + DB unchanged).
    #     Either satisfies the customer contract: "the restricted field
    #     is not mutated".
    #     """
    #     item = ProtectedItem.objects.create(name="p4-4", secret="original")
    #
    #     resp = self.client.patch(
    #         self.url_detail(PROTECTED, item.pk),
    #         data={"secret": "leaked"}, format="json",
    #     )
    #
    #     item.refresh_from_db()
    #     self.assertEqual(
    #         item.secret, "original",
    #         f"Restricted field must not be mutated by a non-admin PATCH "
    #         f"(status={resp.status_code}); got secret={item.secret!r}",
    #     )

    # -- 4.5 -----------------------------------------------------------
    def test_4_5_permission_delete_denies_non_admin(self) -> None:
        """
        Scenario 4.5: ``permission_delete`` returns False → DELETE is forbidden.

        The test user is a plain (non-admin) Django user, so
        ``ProtectedItem.permission_delete`` returns False.
        """
        item = ProtectedItem.objects.create(name="to-delete", secret="s")
        resp = self.client.delete(self.url_detail(PROTECTED, item.pk))

        self.assertIn(
            resp.status_code,
            (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN),
            msg="DELETE by a non-admin must be rejected with 401/403 — "
                f"got {resp.status_code}",
        )
        self.assertTrue(
            ProtectedItem.objects.filter(pk=item.pk).exists(),
            "Rejected DELETE must not remove the record",
        )

    # -- 4.6 -----------------------------------------------------------
    def test_4_6_permission_create_denies_non_admin(self) -> None:
        """Scenario 4.6: ``permission_create`` False → POST is forbidden."""
        resp = self.client.post(
            self.url_create(PROTECTED),
            data={"name": "forbidden-create", "secret": "s"},
            format="json",
        )
        self.assertIn(
            resp.status_code,
            (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN),
            msg="POST by a non-admin must be rejected with 401/403 — "
                f"got {resp.status_code}: {getattr(resp, 'data', resp.content)!r}",
        )
        self.assertFalse(
            ProtectedItem.objects.filter(name="forbidden-create").exists(),
            "Rejected POST must not create a record",
        )


class TestCluster04b_ActionLevel_Admin(AuthenticatedE2ETestCase):
    """Complementary: admin caller may do everything non-admin cannot."""

    e2e_models = ALL_MODELS
    as_superuser = True

    # -- 4.5b / 4.6b ---------------------------------------------------
    def test_4_5b_admin_may_delete(self) -> None:
        """
        Scenario 4.5b: ``permission_delete`` True for admin → DELETE succeeds.

        Pairs with 4.5 to prove the predicate actually varies with the
        caller — not a blanket reject.
        """
        item = ProtectedItem.objects.create(name="p4-5b", secret="s")
        resp = self.client.delete(self.url_detail(PROTECTED, item.pk))

        self.assertIn(
            resp.status_code, (status.HTTP_200_OK, status.HTTP_204_NO_CONTENT),
            msg=f"Admin DELETE must succeed; got {resp.status_code}",
        )
        self.assertFalse(
            ProtectedItem.objects.filter(pk=item.pk).exists(),
            "Admin DELETE must remove the record",
        )

    def test_4_6b_admin_may_create(self) -> None:
        """Scenario 4.6b: ``permission_create`` True for admin → POST succeeds."""
        resp = self.client.post(
            self.url_create(PROTECTED),
            data={"name": "p4-6b", "secret": "s"}, format="json",
        )

        self.assertIn(resp.status_code, (200, 201))
        self.assertTrue(
            ProtectedItem.objects.filter(name="p4-6b").exists(),
            "Admin POST must create the record",
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()





