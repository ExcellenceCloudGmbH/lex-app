"""
Cluster 10c: Many endpoint (bulk operations).

Intent (from docs/features/api-layer/):

    POST /api/<model>/many/ creates many records in one call. PATCH
    likewise. Documented semantics: each record validated separately;
    batch commits successfully when all pass (or fails with per-item
    errors if one fails — see the feature docs for the exact
    all-or-nothing contract).

Scenario numbering matches
docs/test-plan/test-clusters.md#10-api-layer.
"""

from __future__ import annotations

import unittest


class TestCluster10c_ManyEndpoint(unittest.TestCase):
    """Many endpoint — skipped pending bulk fixture."""

    @unittest.expectedFailure  # BUG-006: many endpoint rejects POST with 405
    @unittest.skip(
        "Scenario 10.7: Many endpoint bulk create — BUG-006 (POST returns "
        "405). Once the framework adds POST support to ``model-many-entries``, "
        "remove the skip and the expectedFailure will start passing."
    )
    def test_10_7_many_endpoint_bulk_create(self) -> None:
        """Scenario 10.7: POST to ``many/`` creates multiple records."""


if __name__ == "__main__":  # pragma: no cover
    unittest.main()

