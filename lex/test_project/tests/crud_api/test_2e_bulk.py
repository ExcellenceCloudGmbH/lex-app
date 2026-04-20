"""
Cluster 2e: Bulk operations via the ``many/`` endpoint.

Intent: customers must be able to create/update many records in a
single HTTP call (bulk inserts from CSV uploads, admin scripts, etc.).

Scenario numbering matches
docs/test-plan/test-clusters.md#2-crud-via-rest-api.
"""

from __future__ import annotations

import unittest

from lex.tests.e2e._e2e_test_case import E2ETestCase

from .models import ALL_MODELS, SIMPLE, SimpleItem


class TestCluster02e_BulkOperations(E2ETestCase):
    """POST/PATCH against the ``many/`` endpoint."""

    e2e_models = ALL_MODELS

    # -- 2.23 ----------------------------------------------------------
    @unittest.expectedFailure  # BUG-006: many/ endpoint rejects POST
    def test_2_23_bulk_post_creates_multiple_records(self) -> None:
        """
        Scenario 2.23: POST to many/ creates multiple records.

        Expected failure (BUG-006): the ``model-many-entries`` endpoint
        responds 405 Method Not Allowed to POST. The intent (per the
        cluster plan and customer UX for bulk inserts) is that bulk
        creation via a single HTTP call is supported.
        """
        payload = [
            {"name": "bulk-a", "value": 1},
            {"name": "bulk-b", "value": 2},
            {"name": "bulk-c", "value": 3},
        ]
        resp = self.client.post(
            self.url_many(SIMPLE), data=payload, format="json",
        )
        self.assertTrue(
            200 <= resp.status_code < 300,
            f"Bulk POST must succeed with a 2xx; got {resp.status_code}: "
            f"{getattr(resp, 'data', resp.content)!r}",
        )
        created_names = set(
            SimpleItem.objects.values_list("name", flat=True),
        )
        self.assertEqual(
            created_names, {"bulk-a", "bulk-b", "bulk-c"},
            "All three records must be persisted after a bulk POST",
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
