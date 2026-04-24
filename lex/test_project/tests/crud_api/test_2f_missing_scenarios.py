"""
Cluster 2f: CRUD — remaining scenarios to close Cluster 2 to 🟢.

Covers the four scenarios that were not yet implemented in 2a–2e:

    * 2.7  — POST via API key → ``created_by = "Technical User"``
    * 2.16 — PUT replaces the record (all fields match request body)
    * 2.24 — PATCH to ``many/`` updates multiple records
    * 2.25 — Bulk create with one invalid record — documented contract

Scenario numbering matches
docs/test-plan/test-clusters.md#2-crud-via-rest-api.
"""

from __future__ import annotations

import unittest

from rest_framework import status

from lex.tests.e2e._e2e_test_case import E2ETestCase

from .models import ALL_MODELS, SIMPLE, SimpleItem


class TestCluster02f_MissingScenarios(E2ETestCase):
    """The final four scenarios required for Cluster 2 to reach 🟢."""

    e2e_models = ALL_MODELS

    # -- 2.7 -----------------------------------------------------------
    def test_2_7_api_key_post_stamps_technical_user(self) -> None:
        """
        Scenario 2.7: POST via API key → ``created_by = "Technical User"``.

        The caller is an external system, not a logged-in user. The
        framework should treat the API-key identity as the actor and
        stamp ``created_by`` with the API-key's configured name.

        Pass B3 wires this path end-to-end through the REST layer via
        :meth:`E2ETestCase.authenticate_as_api_key`.
        """
        self.authenticate_as_api_key(name="Technical User")

        resp = self.client.post(
            self.url_create(SIMPLE),
            data={"name": "api-key-2-7", "value": 7}, format="json",
        )
        self.assertIn(
            resp.status_code, (200, 201),
            f"API-key POST must succeed; got {resp.status_code}: "
            f"{getattr(resp, 'data', resp.content)!r}",
        )

        item = SimpleItem.objects.get(name="api-key-2-7")
        self.assertEqual(
            item.created_by, "Technical User",
            "An API-key caller must be recorded on created_by as the "
            "configured key name ('Technical User') — not as the "
            "session user, not as 'Initial Data Upload'. "
            f"Got {item.created_by!r}.",
        )

    # -- 2.16 ----------------------------------------------------------
    def test_2_16_put_replaces_the_record(self) -> None:
        """
        Scenario 2.16: PUT replaces the record.

        Given: an existing ``SimpleItem`` with name / value / description.
        When: the customer PUTs a full replacement payload.
        Then: the response body reflects the new state verbatim and every
        field on the DB record matches the PUT body — PUT is the
        full-replacement verb and must behave as such.
        """
        item = SimpleItem.objects.create(
            name="original", value=1, description="old description",
        )
        payload = {"name": "replaced", "value": 42, "description": "new description"}

        resp = self.client.put(
            self.url_detail(SIMPLE, item.pk),
            data=payload, format="json",
        )
        self.assertEqual(
            resp.status_code, status.HTTP_200_OK,
            f"PUT must succeed with 200 on full replacement; got "
            f"{resp.status_code}: {getattr(resp, 'data', resp.content)!r}",
        )

        item.refresh_from_db()
        for field, expected in payload.items():
            with self.subTest(field=field):
                self.assertEqual(
                    getattr(item, field), expected,
                    f"After PUT, ``{field}`` must match the request body — "
                    "PUT is full-replacement, not merge.",
                )

    # -- 2.24 ----------------------------------------------------------
    # @unittest.expectedFailure  # BUG-006: many/ endpoint rejects POST/PATCH
    def test_2_24_bulk_patch_updates_multiple_records(self) -> None:
        """
        Scenario 2.24: PATCH to ``many/`` updates multiple records.

        Expected failure (BUG-006 in progress.md): the
        ``model-many-entries`` endpoint currently rejects bulk write
        operations. Intent: each entry in the payload is an update keyed
        by ``id`` and every listed row reflects the patched values
        afterwards.
        """
        a = SimpleItem.objects.create(name="bulk-a", value=1)
        b = SimpleItem.objects.create(name="bulk-b", value=2)

        payload = [
            {"id": a.pk, "value": 100},
            {"id": b.pk, "value": 200},
        ]
        resp = self.client.patch(
            self.url_many(SIMPLE), data=payload, format="json",
        )
        self.assertTrue(
            200 <= resp.status_code < 300,
            f"Bulk PATCH must succeed with a 2xx; got {resp.status_code}: "
            f"{getattr(resp, 'data', resp.content)!r}",
        )

        a.refresh_from_db()
        b.refresh_from_db()
        self.assertEqual(a.value, 100, "Row A must reflect the bulk patch")
        self.assertEqual(b.value, 200, "Row B must reflect the bulk patch")

    # -- 2.25 ----------------------------------------------------------
    # @unittest.expectedFailure  # BUG-006: bulk endpoint rejects POST
    def test_2_25_bulk_create_with_invalid_record_is_all_or_nothing(self) -> None:
        """
        Scenario 2.25: Bulk create with one invalid record.

        Intent (per the cluster plan's "all-or-nothing vs partial"
        contract): if **any** row in a bulk POST fails validation, none
        of the rows are persisted — the endpoint must not leave a
        half-applied dataset behind.

        Expected failure (BUG-006): the endpoint currently rejects POST
        entirely. Once bulk is shipped, the all-or-nothing contract
        asserted here is the first thing that must be true of it.
        """
        payload = [
            {"name": "bulk-ok-1", "value": 1},
            {"name": "bulk-bad", "value": "not-an-int"},  # type error
            {"name": "bulk-ok-2", "value": 3},
        ]

        resp = self.client.post(
            self.url_many(SIMPLE), data=payload, format="json",
        )
        self.assertEqual(
            resp.status_code, status.HTTP_400_BAD_REQUEST,
            f"An invalid row in a bulk POST must fail the whole batch; "
            f"got {resp.status_code}: {getattr(resp, 'data', resp.content)!r}",
        )
        self.assertEqual(
            SimpleItem.objects.count(), 0,
            "Bulk POST with any invalid row must leave the DB untouched "
            "— the contract is all-or-nothing.",
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()

