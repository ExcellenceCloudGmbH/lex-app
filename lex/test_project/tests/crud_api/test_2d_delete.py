"""
Cluster 2d: Delete (DELETE) via REST API.

Asserts the customer-observable delete contract:
    * DELETE removes the record (200/204)
    * DELETE non-existent → 404
    * Anonymous DELETE must not remove
    * After DELETE, subsequent GET returns 404 (roundtrip)

Scenario numbering matches
docs/test-plan/test-clusters.md#2-crud-via-rest-api.
"""

from __future__ import annotations

import unittest

from lex.tests.e2e._e2e_test_case import E2ETestCase
from rest_framework import status

from .models import ALL_MODELS, SIMPLE, SimpleItem

import pytest

pytestmark = pytest.mark.crud_api


class TestCluster02d_Delete(E2ETestCase):
    """DELETE /api/<model>/<pk>/"""

    e2e_models = ALL_MODELS

    # -- 2.19 ----------------------------------------------------------
    def test_2_19_delete_removes_record(self) -> None:
        """Scenario 2.19: DELETE removes the record."""
        item = SimpleItem.objects.create(name="lima")
        resp = self.client.delete(self.url_detail(SIMPLE, item.pk))
        self.assertIn(
            resp.status_code,
            (status.HTTP_200_OK, status.HTTP_204_NO_CONTENT),
            msg=f"DELETE must succeed (200/204); got {resp.status_code}",
        )
        self.assertFalse(
            SimpleItem.objects.filter(pk=item.pk).exists(),
            "Record must be gone from the DB after DELETE",
        )

    # -- 2.20 ----------------------------------------------------------
    def test_2_20_delete_nonexistent_returns_404(self) -> None:
        """Scenario 2.20: DELETE non-existent id → 404."""
        resp = self.client.delete(self.url_detail(SIMPLE, 99_999))
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)

    # -- 2.21 ----------------------------------------------------------
    def test_2_21_unauthenticated_delete_is_rejected(self) -> None:
        """Scenario 2.21: Anonymous DELETE must not remove the record."""
        item = SimpleItem.objects.create(name="mike")
        self.client.logout()
        resp = self.client.delete(self.url_detail(SIMPLE, item.pk))
        self.assertNotIn(resp.status_code, (200, 204))
        self.assertTrue(
            SimpleItem.objects.filter(pk=item.pk).exists(),
            "Anonymous DELETE must leave the record in the DB",
        )

    # -- 2.22 ----------------------------------------------------------
    def test_2_22_delete_then_get_returns_404(self) -> None:
        """Scenario 2.22: After DELETE, GET returns 404 (end-to-end roundtrip)."""
        item = SimpleItem.objects.create(name="november")
        self.client.delete(self.url_detail(SIMPLE, item.pk))

        resp = self.client.get(self.url_detail(SIMPLE, item.pk))
        self.assertEqual(
            resp.status_code, status.HTTP_404_NOT_FOUND,
            msg="Deleted record must be absent from the read path too",
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
