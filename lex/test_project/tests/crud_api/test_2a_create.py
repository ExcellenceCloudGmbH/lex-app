"""
Cluster 2a: Create (POST) via REST API.

The customer hits ``POST /api/<model>/create/`` from their frontend or
an integration. We assert the customer-observable contract:

    * 2xx on success, with the new record id in the body
    * 400 on validation failure, with no record created
    * 401/403 on anonymous POST, with no record created
    * Framework-managed fields (timestamps, actor) auto-populated

Scenario numbering matches
docs/test-plan/test-clusters.md#2-crud-via-rest-api.
"""

from __future__ import annotations

import unittest

from rest_framework import status

from lex.tests.e2e._e2e_test_case import E2ETestCase

from .models import ALL_MODELS, SIMPLE, TRACKED, SimpleItem, TrackedItem


class TestCluster02a_Create(E2ETestCase):
    """POST /api/<model>/create/"""

    e2e_models = ALL_MODELS

    # -- 2.1 -----------------------------------------------------------
    def test_2_1_post_creates_record(self) -> None:
        """Scenario 2.1: POST creates a record."""
        resp = self.client.post(
            self.url_create(SIMPLE),
            data={"name": "alpha", "value": 1},
            format="json",
        )
        self.assertIn(
            resp.status_code, (status.HTTP_200_OK, status.HTTP_201_CREATED),
            msg=f"POST must succeed (200/201); got {resp.status_code}: "
                f"{getattr(resp, 'data', resp.content)!r}",
        )
        self.assertIn(
            "id", resp.data,
            "Response body must include the new record's id",
        )
        self.assertTrue(
            SimpleItem.objects.filter(name="alpha").exists(),
            "Record must be persisted in the DB after a successful POST",
        )

    # -- 2.2 -----------------------------------------------------------
    # @unittest.expectedFailure  # BUG-004: edited_at not set on create via POST
    def test_2_2_post_sets_framework_managed_fields(self) -> None:
        """
        Scenario 2.2: POST sets ``created_at`` / ``edited_at`` / ``created_by``.

        Expected failure (BUG-004 in progress.md): after creating a
        record via the REST API, ``edited_at`` is ``None``. Intent per
        docs/reference/LexModel Internals.md is that both timestamps
        are auto-set on create.
        """
        self.client.post(
            self.url_create(TRACKED),
            data={"label": "bravo"}, format="json",
        )
        item = TrackedItem.objects.get(label="bravo")
        self.assertIsNotNone(item.created_at, "created_at must be auto-set")
        self.assertIsNotNone(item.edited_at, "edited_at must be auto-set")
        self.assertTrue(
            item.created_by,
            "created_by must be resolved from the authenticated user",
        )

    # -- 2.3 -----------------------------------------------------------
    # @unittest.expectedFailure  # BUG-005: validation errors return 500, not 400
    def test_2_3_post_missing_required_field_returns_400(self) -> None:
        """
        Scenario 2.3: Missing required field → 400, no record created.

        Expected failure (BUG-005): API returns **500** instead of
        **400** when a required field is missing. The customer-facing
        contract (DRF convention + framework docs) is 400 Bad Request
        with a per-field error body.
        """
        resp = self.client.post(
            self.url_create(SIMPLE),
            data={"value": 5},  # missing required ``name``
            format="json",
        )
        self.assertEqual(
            resp.status_code, status.HTTP_400_BAD_REQUEST,
            msg=f"Missing required field must yield 400; got {resp.status_code}",
        )
        self.assertFalse(
            SimpleItem.objects.exists(),
            "No record may be created when validation rejects the payload",
        )

    # -- 2.4 -----------------------------------------------------------
    # @unittest.expectedFailure  # BUG-005: validation errors return 500, not 400
    def test_2_4_post_invalid_field_type_returns_400(self) -> None:
        """
        Scenario 2.4: Invalid field type → 400, no record created.

        Expected failure (BUG-005).
        """
        resp = self.client.post(
            self.url_create(SIMPLE),
            data={"name": "charlie", "value": "not-an-int"},
            format="json",
        )
        self.assertEqual(
            resp.status_code, status.HTTP_400_BAD_REQUEST,
            msg="Invalid type must yield 400 so the customer sees a clear error",
        )
        self.assertFalse(SimpleItem.objects.exists())

    # -- 2.5 -----------------------------------------------------------
    def test_2_5_post_extra_unknown_fields_are_ignored(self) -> None:
        """Scenario 2.5: Unknown fields are ignored — record created from known."""
        resp = self.client.post(
            self.url_create(SIMPLE),
            data={"name": "delta", "value": 2, "unknown_field": "ignored"},
            format="json",
        )
        self.assertIn(resp.status_code, (200, 201))
        self.assertTrue(SimpleItem.objects.filter(name="delta").exists())

    # -- 2.6 -----------------------------------------------------------
    def test_2_6_unauthenticated_post_is_rejected(self) -> None:
        """Scenario 2.6: Anonymous POST must not create a record."""
        self.client.logout()
        resp = self.client.post(
            self.url_create(SIMPLE),
            data={"name": "echo", "value": 0}, format="json",
        )
        self.assertNotIn(
            resp.status_code, (200, 201),
            "Anonymous POST must NOT succeed — got %s" % resp.status_code,
        )
        self.assertFalse(
            SimpleItem.objects.filter(name="echo").exists(),
            "No record may be created for an unauthenticated caller",
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
