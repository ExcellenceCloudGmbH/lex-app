"""
Cluster 10g: One endpoint — full create/update/delete lifecycle paths.

Targets ``lex/api/views/model_entries/One.py`` — the detail-endpoint
view that handles every per-record HTTP verb. Cluster 10a covered the
happy path; 10g exercises the **lifecycle branches** the customer hits
in the wild:

* PATCH that doesn't actually change anything (no-op detection)
* PATCH partial vs PUT full-replace shape
* DELETE then GET → 404
* PATCH with unknown pk → 404 (not 500)
* Two consecutive PATCHes — last-write-wins

Scenarios numbered per the Coverage Roadmap **Tier 2.1** in
``docs/test-plan/test-clusters.md``.
"""

from __future__ import annotations

import unittest

from lex.test_project.tests._e2e_test_case import E2ETestCase
from rest_framework import status

from .models import ALL_MODELS, API_SIMPLE, ApiSimpleItem

import pytest

pytestmark = pytest.mark.api_layer


class TestCluster10g_OneEndpointLifecycle(E2ETestCase):
    """Detail-endpoint lifecycle paths."""

    e2e_models = ALL_MODELS

    # -- 10.15 ---------------------------------------------------------
    def test_10_15_get_then_patch_then_get_round_trip(self) -> None:
        """
        Scenario 10.15: GET → PATCH(name) → GET sees the change.

        Pins the customer's mental model: 'I read a row, I change one
        field, I read it back and the change is there'. The intermediate
        PATCH must echo the post-save state, not the input.
        """
        item = ApiSimpleItem.objects.create(name="r10-15", value=1)

        get1 = self.client.get(self.url_detail(API_SIMPLE, item.pk))
        self.assertEqual(get1.status_code, status.HTTP_200_OK)
        self.assertEqual(get1.data.get("name"), "r10-15")

        patch = self.client.patch(
            self.url_detail(API_SIMPLE, item.pk),
            data={"name": "r10-15-renamed"}, format="json",
        )
        self.assertEqual(patch.status_code, status.HTTP_200_OK)
        self.assertEqual(
            patch.data.get("name"), "r10-15-renamed",
            "PATCH response must echo the *post-save* state, not the request",
        )
        # Untouched field round-trips unchanged.
        self.assertEqual(
            patch.data.get("value"), 1,
            "PATCH must not silently null out fields the caller did not send",
        )

        get2 = self.client.get(self.url_detail(API_SIMPLE, item.pk))
        self.assertEqual(get2.data.get("name"), "r10-15-renamed")

    # -- 10.16 ---------------------------------------------------------
    def test_10_16_patch_with_same_value_is_safe(self) -> None:
        """
        Scenario 10.16: PATCH with the same value already in the DB
        must succeed (200) and leave the row unchanged. The frontend
        often re-PATCHes the whole edited form including unchanged
        fields; this must not be an error.

        Exercises the no-op detection branch in
        :meth:`OneModelEntry._serializer_update_is_noop`.
        """
        item = ApiSimpleItem.objects.create(name="r10-16", value=42)
        before_edited_at = item.edited_at

        resp = self.client.patch(
            self.url_detail(API_SIMPLE, item.pk),
            data={"name": "r10-16", "value": 42}, format="json",
        )

        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        item.refresh_from_db()
        self.assertEqual(item.name, "r10-16")
        self.assertEqual(item.value, 42)
        # No-op detection should NOT bump the edit timestamp; if a
        # regression flips this branch off, customers see fake edits
        # in the audit log every time they re-save an unchanged form.
        self.assertEqual(
            item.edited_at, before_edited_at,
            "A no-op PATCH must not bump edited_at — that is the "
            "whole point of the no-op detection branch",
        )

    # -- 10.17 ---------------------------------------------------------
    def test_10_17_delete_then_get_returns_404(self) -> None:
        """
        Scenario 10.17: DELETE removes the row; the next GET returns
        404, not 500 and not a stale snapshot.
        """
        item = ApiSimpleItem.objects.create(name="r10-17", value=1)

        delete = self.client.delete(self.url_detail(API_SIMPLE, item.pk))
        self.assertIn(delete.status_code, (200, 204))

        get_after = self.client.get(self.url_detail(API_SIMPLE, item.pk))
        self.assertEqual(
            get_after.status_code, status.HTTP_404_NOT_FOUND,
            "GET after DELETE must return 404 — never a 500 nor a "
            "cached body",
        )

    # -- 10.18 ---------------------------------------------------------
    def test_10_18_patch_unknown_pk_returns_404(self) -> None:
        """
        Scenario 10.18: PATCH for a pk that never existed must return
        404, not 500. Frontend stale-link handling depends on this.
        """
        # Use a pk safely past anything the test seeds.
        bogus_pk = 9_999_999_99
        resp = self.client.patch(
            self.url_detail(API_SIMPLE, bogus_pk),
            data={"name": "ghost"}, format="json",
        )
        self.assertEqual(
            resp.status_code, status.HTTP_404_NOT_FOUND,
            f"PATCH of unknown pk must be 404; got {resp.status_code} "
            f"with body {resp.data!r}",
        )

    # -- 10.19 ---------------------------------------------------------
    def test_10_19_two_consecutive_patches_last_write_wins(self) -> None:
        """
        Scenario 10.19: Back-to-back PATCHes — the second wins.

        This is the same shape as a user double-clicking 'Save' or two
        tabs editing the same record: the framework must not deadlock,
        return stale data, or merge values silently.
        """
        item = ApiSimpleItem.objects.create(name="r10-19", value=0)

        first = self.client.patch(
            self.url_detail(API_SIMPLE, item.pk),
            data={"value": 1}, format="json",
        )
        self.assertEqual(first.status_code, status.HTTP_200_OK)

        second = self.client.patch(
            self.url_detail(API_SIMPLE, item.pk),
            data={"value": 2}, format="json",
        )
        self.assertEqual(second.status_code, status.HTTP_200_OK)
        self.assertEqual(second.data.get("value"), 2)

        item.refresh_from_db()
        self.assertEqual(item.value, 2)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
