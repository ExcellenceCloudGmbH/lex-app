"""
Cluster 10b: History endpoint (``/history/``).

Intent (from docs/features/api-layer/ and docs/features/history/):

    GET /api/<model>/<id>/history/ returns the record's history rows
    ordered by ``history_date``.

Scenario numbering matches
docs/test-plan/test-clusters.md#10-api-layer.
"""

from __future__ import annotations

import unittest

from lex.test_project.tests._e2e_test_case import E2ETestCase
from rest_framework import status

from .models import ALL_MODELS, API_SIMPLE, ApiSimpleItem

import pytest

pytestmark = pytest.mark.api_layer


class TestCluster10b_HistoryEndpoint(E2ETestCase):
    """History endpoint smoke."""

    e2e_models = ALL_MODELS

    # -- 10.6 ----------------------------------------------------------
    def test_10_6_history_endpoint_returns_rows(self) -> None:
        """Scenario 10.6: History endpoint returns rows in history_date order."""
        item = ApiSimpleItem.objects.create(name="h10-6", value=1)
        item.value = 2
        item.save()

        resp = self.client.get(self.url_history(API_SIMPLE, item.pk))

        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        rows = self.extract_results(resp.data)
        self.assertGreaterEqual(
            len(rows), 2,
            "At least create + update history rows must be returned",
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()

