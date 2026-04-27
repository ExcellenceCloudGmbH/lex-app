"""
Cluster 10a: One/List CRUD endpoints through the REST API.

Intent (from docs/features/api-layer/):

    The REST layer wraps the model's CRUD contract as HTTP. For an
    authenticated user:
      - POST  /api/<model>/create/   → 201
      - GET   /api/<model>/<id>/     → 200 with the record
      - PATCH /api/<model>/<id>/     → 200, partial update
      - DELETE/api/<model>/<id>/     → 204/200
      - GET   /api/<model>/          → 200 with list
    Invalid data → 400. Anonymous → 401/redirect.

Scenario numbering matches
docs/test-plan/test-clusters.md#10-api-layer.
"""

from __future__ import annotations

import unittest

from rest_framework import status

from lex.tests.e2e._e2e_test_case import E2ETestCase

from .models import ALL_MODELS, API_SIMPLE, ApiSimpleItem


class TestCluster10a_CRUDEndpoints(E2ETestCase):
    """One/List endpoint smoke tests at the HTTP layer."""

    e2e_models = ALL_MODELS

    # -- 10.1 ----------------------------------------------------------
    def test_10_1_post_creates_record(self) -> None:
        """Scenario 10.1: POST → 201, record in DB with returned ``id``."""
        resp = self.client.post(
            self.url_create(API_SIMPLE),
            data={"name": "r10-1", "value": 1}, format="json",
        )
        self.assertIn(resp.status_code, (200, 201))
        self.assertIn("id", resp.data, "Response body must include id")
        self.assertTrue(ApiSimpleItem.objects.filter(name="r10-1").exists())

    # -- 10.2 ----------------------------------------------------------
    def test_10_2_get_retrieves_record(self) -> None:
        """Scenario 10.2: GET detail returns the record with correct fields."""
        item = ApiSimpleItem.objects.create(name="r10-2", value=7)
        resp = self.client.get(self.url_detail(API_SIMPLE, item.pk))

        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data.get("name"), "r10-2")
        self.assertEqual(resp.data.get("value"), 7)

    # -- 10.2b ---------------------------------------------------------
    def test_10_2b_get_exposes_framework_managed_fields(self) -> None:
        """
        Scenario 10.2b: GET detail must expose the LexModel audit
        fields (``created_at``, ``created_by``, ``edited_at``,
        ``edited_by``). Customers rely on those for display and
        downstream auditing.
        """
        item = ApiSimpleItem.objects.create(name="r10-2b", value=1)
        resp = self.client.get(self.url_detail(API_SIMPLE, item.pk))

        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        for key in ("created_at", "created_by", "edited_at", "edited_by"):
            self.assertIn(
                key, resp.data,
                f"GET detail must include framework-managed field {key!r}; "
                f"keys present: {sorted(resp.data.keys())!r}",
            )

    # -- 10.3 ----------------------------------------------------------
    def test_10_3_patch_updates_fields(self) -> None:
        """Scenario 10.3: PATCH updates only specified fields."""
        item = ApiSimpleItem.objects.create(name="r10-3", value=1)
        resp = self.client.patch(
            self.url_detail(API_SIMPLE, item.pk),
            data={"value": 99}, format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)

        fresh = ApiSimpleItem.objects.get(pk=item.pk)
        self.assertEqual(fresh.value, 99)
        self.assertEqual(fresh.name, "r10-3", "PATCH must not touch other fields")

    # -- 10.3b ---------------------------------------------------------
    def test_10_3b_put_replaces_record(self) -> None:
        """
        Scenario 10.3b: PUT replaces the record (all fields).

        Scenario 2.16 analogue. PUT is full replacement per REST
        convention — all editable fields must match the request body.
        """
        item = ApiSimpleItem.objects.create(
            name="r10-3b", value=1,
        )
        resp = self.client.put(
            self.url_detail(API_SIMPLE, item.pk),
            data={"name": "r10-3b-put", "value": 42}, format="json",
        )
        self.assertEqual(
            resp.status_code, status.HTTP_200_OK,
            f"PUT must succeed with 200; got {resp.status_code}: "
            f"{getattr(resp, 'data', resp.content)!r}",
        )

        fresh = ApiSimpleItem.objects.get(pk=item.pk)
        self.assertEqual(fresh.name, "r10-3b-put")
        self.assertEqual(fresh.value, 42)

    # -- 10.4 ----------------------------------------------------------
    def test_10_4_delete_removes_record(self) -> None:
        """Scenario 10.4: DELETE removes the record."""
        item = ApiSimpleItem.objects.create(name="r10-4", value=1)
        resp = self.client.delete(self.url_detail(API_SIMPLE, item.pk))

        self.assertIn(
            resp.status_code,
            (status.HTTP_200_OK, status.HTTP_204_NO_CONTENT),
        )
        self.assertFalse(
            ApiSimpleItem.objects.filter(pk=item.pk).exists(),
            "Record must be gone from DB after successful DELETE",
        )

    # -- 10.4b ---------------------------------------------------------
    def test_10_4b_delete_then_get_returns_404(self) -> None:
        """
        Scenario 10.4b: After DELETE, GET on the same id returns 404.

        End-to-end roundtrip — confirms the delete propagated through
        to the read path (not just the ORM table).
        """
        item = ApiSimpleItem.objects.create(name="r10-4b", value=1)
        self.client.delete(self.url_detail(API_SIMPLE, item.pk))

        resp = self.client.get(self.url_detail(API_SIMPLE, item.pk))
        self.assertEqual(
            resp.status_code, status.HTTP_404_NOT_FOUND,
            "Deleted record must yield 404 on subsequent GET",
        )

    # -- 10.5 ----------------------------------------------------------
    def test_10_5_list_returns_all_records(self) -> None:
        """Scenario 10.5: GET list returns all records."""
        ApiSimpleItem.objects.create(name="r10-5a", value=1)
        ApiSimpleItem.objects.create(name="r10-5b", value=2)

        resp = self.client.get(self.url_list(API_SIMPLE))

        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        rows = self.extract_results(resp.data)
        self.assertGreaterEqual(len(rows), 2)

    # -- 10.5b ---------------------------------------------------------
    def test_10_5b_list_empty_returns_200_empty_results(self) -> None:
        """
        Scenario 10.5b: Empty DB → 200 with empty ``results``, not 404.

        A list endpoint with no matches is a valid state — frontends
        rely on it for "no data yet" views.
        """
        resp = self.client.get(self.url_list(API_SIMPLE))

        self.assertEqual(
            resp.status_code, status.HTTP_200_OK,
            "Empty list must yield 200, not 404",
        )
        rows = self.extract_results(resp.data)
        self.assertEqual(len(rows), 0, f"Expected empty list; got {rows!r}")

    # -- 10.9 ----------------------------------------------------------
    def test_10_9_invalid_data_returns_400(self) -> None:
        """Scenario 10.9: Invalid data → 400.

        Skipped for now as duplicate coverage: Cluster 2 owns the
        framework-level ValidationError → 400 regression gate.
        """
        resp = self.client.post(
            self.url_create(API_SIMPLE),
            data={"value": "not-an-int"}, format="json",
        )
        self.assertEqual(
            resp.status_code, status.HTTP_400_BAD_REQUEST,
            msg="Invalid data must yield 400",
        )

    # -- 10.10 ---------------------------------------------------------
    def test_10_10_unauthenticated_request_is_rejected(self) -> None:
        """Scenario 10.10: Anonymous request → 401/redirect."""
        self.client.logout()
        resp = self.client.get(self.url_list(API_SIMPLE))
        self.assertNotEqual(
            resp.status_code, status.HTTP_200_OK,
            "Anonymous list request must not return 200",
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()




