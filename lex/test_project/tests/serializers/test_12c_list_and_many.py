"""
Cluster 12c: List & Many contract.

Intent: the shape of a list response is a **superset-stable** mirror
of the detail response. Every row has the same framework-managed
keys, rows denied by ``permission_read`` are dropped entirely (not
serialized as empty dicts), and the ``/many/`` bulk endpoint enforces
the same field-level validation contract as a single-row PATCH.

Scenario numbering matches
docs/test-plan/test-clusters.md#12-serializer-contract.
"""

from __future__ import annotations

import unittest

from rest_framework import status

from lex.core.models.LexModel import PermissionResult
from lex.tests.e2e._e2e_test_case import E2ETestCase

from .models import (
    ALL_MODELS,
    PROTECTED_WIDE,
    WIDE,
    ProtectedWideItem,
    WideItem,
)

# Framework-managed keys every serialized row carries (mirrors 12a).
FRAMEWORK_KEYS = {"id", "id_field", "short_description", "lex_reserved_scopes"}


class TestCluster12c_ListContract(E2ETestCase):
    """GET /api/<model>/ — list response shape contract."""

    e2e_models = ALL_MODELS

    # -- 12.20 ---------------------------------------------------------
    def test_12_20_filtered_list_drops_denied_rows(self) -> None:
        """Scenario 12.20: 3 rows, 1 denied → list returns 2 rows.

        The denied row must be **absent** from the list — not
        present as an empty dict. This is what
        :class:`FilteredListSerializer` exists for; without it the UI
        would render a blank row for every denied record.
        """
        a = ProtectedWideItem.objects.create(name="row-A", amount="1")
        denied = ProtectedWideItem.objects.create(name="deny-me", amount="2")
        b = ProtectedWideItem.objects.create(name="row-B", amount="3")

        original = ProtectedWideItem.permission_read

        def _deny(self, uc):
            if self.name == "deny-me":
                return PermissionResult.deny("12.20 hard deny")
            return original(self, uc)

        ProtectedWideItem.permission_read = _deny
        try:
            resp = self.list_get(PROTECTED_WIDE)
            self.assertEqual(resp.status_code, status.HTTP_200_OK)
            rows = self.extract_results(resp.data)

            ids = [row.get("id") for row in rows if isinstance(row, dict)]
            self.assertIn(a.pk, ids, "allowed row A missing from list")
            self.assertIn(b.pk, ids, "allowed row B missing from list")
            self.assertNotIn(
                denied.pk, ids,
                f"Denied row leaked into list response; got ids={ids}",
            )
            # And no row is an empty dict — they must be dropped, not kept as {}.
            for row in rows:
                self.assertTrue(
                    row, f"Empty-dict row survived filtering: {row!r}",
                )
        finally:
            ProtectedWideItem.permission_read = original

    # -- 12.21 ---------------------------------------------------------
    def test_12_21_list_row_shape_matches_detail(self) -> None:
        """Scenario 12.21: each list row carries the same framework-managed
        keys as the detail endpoint.

        Frontends build tables off the list endpoint and reuse the same
        row schema in detail views. If a key silently disappears from
        one or the other, UI code breaks.
        """
        item_a = WideItem.objects.create(name="L-A", amount="1")
        item_b = WideItem.objects.create(name="L-B", amount="2")

        # Detail shape for item_a (reference).
        detail_resp = self.client.get(self.url_detail(WIDE, item_a.pk))
        self.assertEqual(detail_resp.status_code, status.HTTP_200_OK)
        detail_keys = set(detail_resp.data.keys())

        # List shape.
        list_resp = self.list_get(WIDE)
        self.assertEqual(list_resp.status_code, status.HTTP_200_OK)
        rows = self.extract_results(list_resp.data)
        self.assertGreaterEqual(
            len(rows), 2, "Expected both seeded rows in list response",
        )

        for row in rows:
            missing = FRAMEWORK_KEYS - set(row.keys())
            self.assertFalse(
                missing,
                f"Framework-managed keys missing from a list row: {missing}. "
                f"Row keys: {sorted(row.keys())}",
            )
            # Every list-row key must also exist in the detail shape —
            # no list-only fields that break detail-view assumptions.
            extra = set(row.keys()) - detail_keys
            self.assertFalse(
                extra,
                f"List row has keys absent from detail response: {extra}. "
                f"This is a contract drift between list and detail.",
            )

        # Sanity: the rows we expected are there.
        ids = {row["id"] for row in rows if "id" in row}
        self.assertIn(item_a.pk, ids)
        self.assertIn(item_b.pk, ids)

    # -- 12.22 ---------------------------------------------------------
    @unittest.expectedFailure  # BUG-006: /many/ POST endpoint returns 405
    def test_12_22_many_post_rejects_invalid_choice(self) -> None:
        """Scenario 12.22: POST to ``/many/`` with an invalid ``choices``
        value must reject the batch with 400 (not 500, not 405).

        Mirrors 12.16 at the bulk layer. Currently xfail against
        BUG-006 (``many`` POST returns 405 — no bulk-insert via API).
        Once BUG-006 lands, this test additionally gates BUG-005 at
        the bulk path.
        """
        resp = self.client.post(
            self.url_many(WIDE),
            data=[
                {"name": "m-ok", "category": "alpha"},
                {"name": "m-bad", "category": "not-a-choice"},
            ],
            format="json",
        )
        self.assertEqual(
            resp.status_code, status.HTTP_400_BAD_REQUEST,
            f"/many/ POST with an invalid row must 400 — got {resp.status_code}",
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()

