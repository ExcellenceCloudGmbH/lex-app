"""
Cluster 10c: Many endpoint (bulk delete operation).

Intent (from docs/features/api-layer/):

    The public bulk write contract for /api/<model>/many/ is DELETE
    only. Selected ids are supplied through repeated ``ids`` query
    params; the endpoint returns the ids it actually deleted.

Scenario numbering matches
docs/test-plan/test-clusters.md#10-api-layer.
"""

from __future__ import annotations

import unittest
from urllib.parse import urlencode

from rest_framework import status

from lex.tests.e2e._e2e_test_case import E2ETestCase

from ..crud_api.models import ALL_MODELS, SIMPLE, SimpleItem


class TestCluster10c_ManyEndpoint(E2ETestCase):
    """Scenario 10.7 — ``many/`` DELETE selected rows."""

    e2e_models = ALL_MODELS

    def _many_url_for_ids(self, *ids: int) -> str:
        query = urlencode([("ids", pk) for pk in ids])
        return f"{self.url_many(SIMPLE)}?{query}"

    def test_10_7_many_endpoint_bulk_delete(self) -> None:
        """Scenario 10.7: DELETE to ``many/`` deletes selected records only."""
        a = SimpleItem.objects.create(name="api-many-a", value=1)
        b = SimpleItem.objects.create(name="api-many-b", value=2)
        survivor = SimpleItem.objects.create(name="api-many-survivor", value=3)

        resp = self.client.delete(self._many_url_for_ids(a.pk, b.pk))

        self.assertEqual(
            resp.status_code, status.HTTP_200_OK,
            f"Many DELETE must return 200; got {resp.status_code}: "
            f"{getattr(resp, 'data', resp.content)!r}",
        )
        self.assertEqual(set(resp.data), {a.pk, b.pk})
        self.assertFalse(SimpleItem.objects.filter(pk__in=[a.pk, b.pk]).exists())
        self.assertTrue(SimpleItem.objects.filter(pk=survivor.pk).exists())


if __name__ == "__main__":  # pragma: no cover
    unittest.main()

