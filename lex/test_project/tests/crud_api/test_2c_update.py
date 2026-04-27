"""
Cluster 2c: Update (PATCH / PUT) via REST API.

Asserts the customer-observable update contract:
    * PATCH only touches fields in the payload
    * Invalid PATCH → 400 AND DB unchanged
    * PATCH on non-existent id → 404
    * Anonymous PATCH must not mutate

Scenario numbering matches
docs/test-plan/test-clusters.md#2-crud-via-rest-api.
"""

from __future__ import annotations

import unittest

from rest_framework import status

from lex.tests.e2e._e2e_test_case import E2ETestCase

from .models import ALL_MODELS, SIMPLE, SimpleItem


class TestCluster02c_Update(E2ETestCase):
    """PATCH / PUT /api/<model>/<pk>/"""

    e2e_models = ALL_MODELS

    # -- 2.13 ----------------------------------------------------------
    def test_2_13_patch_updates_only_specified_fields(self) -> None:
        """Scenario 2.13: PATCH updates only listed fields."""
        item = SimpleItem.objects.create(name="hotel", value=10, description="orig")
        resp = self.client.patch(
            self.url_detail(SIMPLE, item.pk),
            data={"value": 99}, format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        item.refresh_from_db()
        self.assertEqual(item.value, 99, "PATCHed field must be updated")
        self.assertEqual(item.name, "hotel", "Unspecified field must NOT change")
        self.assertEqual(
            item.description, "orig",
            "PATCH must not touch fields missing from the payload",
        )

    # -- 2.15 ----------------------------------------------------------
    def test_2_15_patch_invalid_value_leaves_record_unchanged(self) -> None:
        """
        Scenario 2.15: PATCH with invalid value → 400 AND DB unchanged.

        A PATCH payload that fails field-level validation must return a
        client-correctable 400 and leave the persisted row unchanged.
        """
        item = SimpleItem.objects.create(name="juliet", value=5)
        resp = self.client.patch(
            self.url_detail(SIMPLE, item.pk),
            data={"value": "not-an-int"}, format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        item.refresh_from_db()
        self.assertEqual(
            item.value, 5,
            "Rejected PATCH must not mutate the DB",
        )

    # -- 2.17 ----------------------------------------------------------
    def test_2_17_patch_nonexistent_returns_404(self) -> None:
        """Scenario 2.17: PATCH non-existent id → 404."""
        resp = self.client.patch(
            self.url_detail(SIMPLE, 99_999),
            data={"value": 1}, format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)

    # -- 2.18 ----------------------------------------------------------
    def test_2_18_unauthenticated_patch_is_rejected(self) -> None:
        """Scenario 2.18: Anonymous PATCH must not mutate."""
        item = SimpleItem.objects.create(name="kilo", value=7)
        self.client.logout()
        resp = self.client.patch(
            self.url_detail(SIMPLE, item.pk),
            data={"value": 999}, format="json",
        )
        self.assertNotIn(resp.status_code, (200, 201))
        item.refresh_from_db()
        self.assertEqual(
            item.value, 7,
            "Anonymous PATCH must NOT mutate the DB",
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
