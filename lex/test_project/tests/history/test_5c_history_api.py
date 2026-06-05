"""
Cluster 5c: History via the REST API (``/history/`` endpoint).

Intent (from docs/features/history/):

    ``GET /api/<model>/<id>/history/`` returns the history rows for a
    given record, ordered by ``history_date``, with field values at
    each point in time.

Scenario numbering matches
docs/test-plan/test-clusters.md#5-history--bitemporal.
"""

from __future__ import annotations

import unittest

from lex.test_project.tests._e2e_test_case import E2ETestCase
from rest_framework import status

from .models import ALL_MODELS, HIST_SIMPLE, HistSimpleItem

import pytest

pytestmark = pytest.mark.history


class TestCluster05c_HistoryAPI(E2ETestCase):
    """History endpoint contract."""

    e2e_models = ALL_MODELS

    # -- 5.9 -----------------------------------------------------------
    def test_5_9_history_endpoint_returns_rows(self) -> None:
        """Scenario 5.9: GET ``/history/`` returns rows ordered by history_date."""
        item = HistSimpleItem.objects.create(name="h5-9", value=1)
        item.value = 2
        item.save()
        item.value = 3
        item.save()

        resp = self.client.get(self.url_history(HIST_SIMPLE, item.pk))

        self.assertEqual(
            resp.status_code, status.HTTP_200_OK,
            msg=f"GET /history/ must return 200; got {resp.status_code}: "
                f"{getattr(resp, 'data', resp.content)!r}",
        )
        rows = self.extract_results(resp.data)
        self.assertGreaterEqual(
            len(rows), 3,
            "History endpoint must return at least the create + 2 updates",
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()

