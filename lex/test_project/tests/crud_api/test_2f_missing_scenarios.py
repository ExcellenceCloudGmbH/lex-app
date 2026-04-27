"""
Cluster 2f: CRUD — remaining scenarios to close Cluster 2 to 🟢.

Covers the two non-bulk scenarios that were not implemented in 2a–2e:

    * 2.7  — POST via API key → ``created_by = "Technical User"``
    * 2.16 — PUT replaces the record (all fields match request body)

Bulk ``many/`` write coverage lives in ``test_2e_bulk.py`` and is
DELETE-only. Bulk create and bulk patch are not supported contracts.

Scenario numbering matches
docs/test-plan/test-clusters.md#2-crud-via-rest-api.
"""

from __future__ import annotations

import unittest

from rest_framework import status

from lex.tests.e2e._e2e_test_case import E2ETestCase

from .models import ALL_MODELS, SIMPLE, SimpleItem


class TestCluster02f_MissingScenarios(E2ETestCase):
    """The final non-bulk scenarios required for Cluster 2 to reach 🟢."""

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


if __name__ == "__main__":  # pragma: no cover
    unittest.main()

