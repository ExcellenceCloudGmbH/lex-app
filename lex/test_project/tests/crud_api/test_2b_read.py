"""
Cluster 2b: Read (GET) via REST API.

Asserts the customer-observable read contract:
    * GET detail returns the record (200) or 404
    * GET list returns every record the user is allowed to see
    * Anonymous reads are rejected

Scenario numbering matches
docs/test-plan/test-clusters.md#2-crud-via-rest-api.
"""

from __future__ import annotations

import unittest

from lex.test_project.tests._e2e_test_case import E2ETestCase
from rest_framework import status

from .models import ALL_MODELS, SIMPLE, SimpleItem

import pytest

pytestmark = pytest.mark.crud_api


class TestCluster02b_Read(E2ETestCase):
    """GET detail + list endpoints."""

    e2e_models = ALL_MODELS

    # -- 2.8 -----------------------------------------------------------
    def test_2_8_get_detail_returns_record(self) -> None:
        """Scenario 2.8: GET detail returns the record."""
        item = SimpleItem.objects.create(name="foxtrot", value=42)
        resp = self.client.get(self.url_detail(SIMPLE, item.pk))
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data["name"], "foxtrot")
        self.assertEqual(resp.data["value"], 42)

    # -- 2.9 -----------------------------------------------------------
    def test_2_9_get_detail_nonexistent_returns_404(self) -> None:
        """Scenario 2.9: GET detail for non-existent id → 404."""
        resp = self.client.get(self.url_detail(SIMPLE, 99_999))
        self.assertEqual(
            resp.status_code, status.HTTP_404_NOT_FOUND,
            msg="Unknown id must yield 404, not 500/200/empty",
        )

    # -- 2.10 ----------------------------------------------------------
    def test_2_10_get_list_returns_all_records(self) -> None:
        """Scenario 2.10: GET list returns every record."""
        for name in ("a", "b", "c"):
            SimpleItem.objects.create(name=name)

        resp = self.list_get(SIMPLE)
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        results = self.extract_results(resp.data)
        self.assertEqual(
            len(results), 3,
            f"Expected 3 rows, got {len(results)}: {results!r}",
        )

    # -- 2.11 ----------------------------------------------------------
    def test_2_11_list_respects_pagination(self) -> None:
        """
        Scenario 2.11: GET list with pagination params returns a
        paginated envelope.

        Intent: the list endpoint supports pagination via ``?page=`` /
        ``?perPage=`` (``CustomPageNumberPagination`` in List.py).
        When pagination params are present, the response body is a
        dict with ``results`` / ``count`` / ``next`` / ``previous``.
        Clients depend on this envelope for paging through large sets.
        """
        for i in range(5):
            SimpleItem.objects.create(name=f"p-{i}", value=i)

        resp = self.client.get(
            self.url_list(SIMPLE), data={"page": 1, "perPage": 2},
        )

        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertIsInstance(
            resp.data, dict,
            f"Paginated list must return a dict, not {type(resp.data).__name__}",
        )
        self.assertIn("results", resp.data)
        self.assertIn("count", resp.data)
        self.assertEqual(
            resp.data["count"], 5,
            f"count must reflect DB total (5); got {resp.data['count']}",
        )

    # -- 2.11b ---------------------------------------------------------
    def test_2_11b_per_page_controls_page_size(self) -> None:
        """Scenario 2.11b: ``?perPage=N`` caps the page to N rows."""
        for i in range(5):
            SimpleItem.objects.create(name=f"pp-{i}", value=i)

        resp = self.client.get(self.url_list(SIMPLE), data={"perPage": 2})

        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        results = self.extract_results(resp.data)
        self.assertEqual(
            len(results), 2,
            f"perPage=2 must limit the page to 2 rows; got {len(results)}",
        )

    # -- 2.11c ---------------------------------------------------------
    def test_2_11c_per_page_negative_one_returns_all(self) -> None:
        """
        Scenario 2.11c: ``?perPage=-1`` returns every row in a single
        paginated response. Customers use this for exports and selects.
        """
        for i in range(7):
            SimpleItem.objects.create(name=f"all-{i}", value=i)

        resp = self.client.get(self.url_list(SIMPLE), data={"perPage": -1})

        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        results = self.extract_results(resp.data)
        self.assertEqual(
            len(results), 7,
            f"perPage=-1 must return every row; got {len(results)}",
        )

    # -- 2.12 ----------------------------------------------------------
    def test_2_12_unauthenticated_get_is_rejected(self) -> None:
        """Scenario 2.12: Anonymous GET must not succeed."""
        SimpleItem.objects.create(name="golf")
        self.client.logout()
        resp = self.client.get(self.url_list(SIMPLE))
        self.assertNotIn(
            resp.status_code, (200, 201),
            "Anonymous GET must NOT succeed; got %s" % resp.status_code,
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
