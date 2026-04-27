"""
Cluster 2e: Bulk operations via the ``many/`` endpoint.

Intent: the only supported bulk write operation on ``many/`` is
DELETE. Customers can select multiple existing rows in the UI and
delete them in one request; bulk create and bulk patch are deliberately
outside the public contract.

Scenario numbering matches
docs/test-plan/test-clusters.md#2-crud-via-rest-api.
"""

from __future__ import annotations

import unittest
from urllib.parse import urlencode

from rest_framework import status

from lex.tests.e2e._e2e_test_case import E2ETestCase

from .models import ALL_MODELS, SIMPLE, SimpleItem


class TestCluster02e_BulkOperations(E2ETestCase):
    """DELETE-only bulk write contract for the ``many/`` endpoint."""

    e2e_models = ALL_MODELS

    def _many_url_for_ids(self, *ids: int) -> str:
        query = urlencode([("ids", pk) for pk in ids])
        return f"{self.url_many(SIMPLE)}?{query}"

    # -- 2.23 ----------------------------------------------------------
    def test_2_23_bulk_delete_removes_selected_records(self) -> None:
        """
        Scenario 2.23: DELETE to ``many/`` removes selected records.

        Selected rows are supplied through repeated ``ids`` query
        parameters, matching ``PrimaryKeyListFilterBackend``. The
        endpoint returns the deleted ids and the rows are gone from DB.
        """
        a = SimpleItem.objects.create(name="bulk-del-a", value=1)
        b = SimpleItem.objects.create(name="bulk-del-b", value=2)

        resp = self.client.delete(self._many_url_for_ids(a.pk, b.pk))

        self.assertEqual(
            resp.status_code, status.HTTP_200_OK,
            f"Bulk DELETE must return 200; got {resp.status_code}: "
            f"{getattr(resp, 'data', resp.content)!r}",
        )
        self.assertEqual(
            set(resp.data), {a.pk, b.pk},
            "Bulk DELETE response must list exactly the ids it deleted.",
        )
        self.assertFalse(SimpleItem.objects.filter(pk__in=[a.pk, b.pk]).exists())

    # -- 2.24 ----------------------------------------------------------
    def test_2_24_bulk_delete_leaves_unselected_records(self) -> None:
        """Scenario 2.24: bulk DELETE leaves unselected rows untouched."""
        selected = SimpleItem.objects.create(name="bulk-selected", value=1)
        survivor = SimpleItem.objects.create(name="bulk-survivor", value=2)

        resp = self.client.delete(self._many_url_for_ids(selected.pk))

        self.assertEqual(
            resp.status_code, status.HTTP_200_OK,
            f"Bulk DELETE must return 200; got {resp.status_code}: "
            f"{getattr(resp, 'data', resp.content)!r}",
        )
        self.assertFalse(SimpleItem.objects.filter(pk=selected.pk).exists())
        self.assertTrue(
            SimpleItem.objects.filter(pk=survivor.pk).exists(),
            "Rows not named in the ids query params must not be deleted.",
        )

    # -- 2.25 ----------------------------------------------------------
    def test_2_25_bulk_delete_unknown_ids_are_noop(self) -> None:
        """Scenario 2.25: unknown ids in a bulk DELETE are ignored safely."""
        item = SimpleItem.objects.create(name="bulk-known", value=1)
        missing_id = item.pk + 10_000

        resp = self.client.delete(self._many_url_for_ids(item.pk, missing_id))

        self.assertEqual(
            resp.status_code, status.HTTP_200_OK,
            f"Bulk DELETE with a stale id must still return 200; got "
            f"{resp.status_code}: {getattr(resp, 'data', resp.content)!r}",
        )
        self.assertEqual(
            resp.data, [item.pk],
            "Only existing selected rows should appear in the deleted-id list.",
        )
        self.assertFalse(SimpleItem.objects.filter(pk=item.pk).exists())


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
