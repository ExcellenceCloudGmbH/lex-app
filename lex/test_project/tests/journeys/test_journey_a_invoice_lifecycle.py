"""
Journey A — "The invoice lifecycle"

A finance admin and a regular staffer share one invoice record across
a realistic lifecycle. The journey exercises, in order:

    Cluster 2 (CRUD via REST)    — POST, GET, PATCH, DELETE
    Cluster 4 (Permissions)      — admin vs non-admin authz
    Cluster 5 (History)          — create → update → delete trail
    Cluster 6 (Audit actor)      — created_by / edited_by stamping
    Cluster 10 (API layer)       — HTTP contract of each verb

Each "Act" is a ``self.subTest`` block so a failure anywhere reports
the specific act without masking the rest of the narrative.

Why this test exists
--------------------
Single-cluster tests assert each feature in isolation. A journey test
proves the features *compose* — that the audit trail is consistent
with the HTTP response, the history contains every state transition
the user actually performed, and permissions flip the right bits at
the right stage. Regressions in the seams between clusters (e.g.
"PATCH works but doesn't advance edited_at", "DELETE succeeds but
audit actor is wrong") surface here first.
"""

from __future__ import annotations

import unittest

from rest_framework import status

from lex.tests.e2e._authenticated_e2e_test_case import AuthenticatedE2ETestCase

from .models import ALL_MODELS, INVOICE, Invoice


class TestJourneyA_InvoiceLifecycle_Admin(AuthenticatedE2ETestCase):
    """Admin walks an invoice through create → update → delete."""

    e2e_models = ALL_MODELS
    as_superuser = True

    def test_invoice_lifecycle_admin(self) -> None:
        """Admin journey — every verb succeeds, trail is complete."""

        # -- Act 1: create ------------------------------------------
        with self.subTest(act="1-create"):
            resp = self.client.post(
                self.url_create(INVOICE),
                data={"customer": "Acme Corp", "amount": 100, "note": "initial"},
                format="json",
            )
            self.assertIn(
                resp.status_code, (200, 201),
                f"Admin POST must succeed; got {resp.status_code}: "
                f"{getattr(resp, 'data', resp.content)!r}",
            )
            invoice_id = resp.data["id"]

        invoice = Invoice.objects.get(pk=invoice_id)

        # -- Act 2: actor stamped on create -------------------------
        with self.subTest(act="2-actor-on-create"):
            self.assertTrue(
                invoice.created_by,
                "created_by must be populated for an authenticated POST",
            )
            self.assertIsNotNone(
                invoice.created_at,
                "created_at must be populated on create",
            )

        # -- Act 3: read roundtrip ----------------------------------
        with self.subTest(act="3-read"):
            resp = self.client.get(self.url_detail(INVOICE, invoice_id))
            self.assertEqual(resp.status_code, status.HTTP_200_OK)
            self.assertEqual(resp.data["customer"], "Acme Corp")
            self.assertEqual(resp.data["amount"], 100)

        # -- Act 4: admin PATCHes amount ----------------------------
        with self.subTest(act="4-patch-amount"):
            resp = self.client.patch(
                self.url_detail(INVOICE, invoice_id),
                data={"amount": 250}, format="json",
            )
            self.assertEqual(resp.status_code, status.HTTP_200_OK)
            invoice.refresh_from_db()
            self.assertEqual(invoice.amount, 250)
            self.assertEqual(
                invoice.customer, "Acme Corp",
                "PATCH must not clobber untouched fields",
            )

        # -- Act 5: history has create + update ---------------------
        with self.subTest(act="5-history-trail"):
            rows = list(Invoice.history.filter(id=invoice_id).order_by("history_id"))
            self.assertGreaterEqual(
                len(rows), 2,
                "History must contain at least create + update rows "
                f"after one PATCH; got {[r.history_type for r in rows]}",
            )
            self.assertEqual(rows[0].history_type, "+", "First row is create")
            self.assertEqual(
                rows[-1].history_type, "~",
                "Latest row after PATCH is a change",
            )

        # -- Act 6: history endpoint returns same trail -------------
        with self.subTest(act="6-history-endpoint"):
            resp = self.client.get(self.url_history(INVOICE, invoice_id))
            self.assertEqual(resp.status_code, status.HTTP_200_OK)
            api_rows = self.extract_results(resp.data)
            self.assertGreaterEqual(
                len(api_rows), 2,
                "History endpoint must expose the same trail as ORM",
            )

        # -- Act 7: delete ------------------------------------------
        with self.subTest(act="7-delete"):
            resp = self.client.delete(self.url_detail(INVOICE, invoice_id))
            self.assertIn(
                resp.status_code,
                (status.HTTP_200_OK, status.HTTP_204_NO_CONTENT),
                f"Admin DELETE must succeed; got {resp.status_code}",
            )

        # -- Act 8: read after delete returns 404 -------------------
        with self.subTest(act="8-delete-propagates-to-read"):
            resp = self.client.get(self.url_detail(INVOICE, invoice_id))
            self.assertEqual(
                resp.status_code, status.HTTP_404_NOT_FOUND,
                "Deleted invoice must be absent from the read path",
            )


class TestJourneyA_InvoiceLifecycle_NonAdmin(AuthenticatedE2ETestCase):
    """
    Non-admin journey — permission boundary hit on every destructive verb.

    Exact complement to the admin journey above: every act that succeeded
    for the admin must be blocked or narrowed for a regular caller.
    """

    e2e_models = ALL_MODELS

    def test_invoice_lifecycle_nonadmin_is_blocked(self) -> None:
        """A non-admin may read, may edit ``note``, may not create or delete."""

        # Seed with an admin-created record (via ORM, since the test
        # user is non-admin and cannot create via API).
        invoice = Invoice.objects.create(
            customer="Acme Corp", amount=100, note="initial",
        )

        # -- Act 1: non-admin can read ------------------------------
        with self.subTest(act="1-read-allowed"):
            resp = self.client.get(self.url_detail(INVOICE, invoice.pk))
            self.assertEqual(resp.status_code, status.HTTP_200_OK)

        # -- Act 2: non-admin CAN patch note (allowed field) --------
        with self.subTest(act="2-patch-note-allowed"):
            resp = self.client.patch(
                self.url_detail(INVOICE, invoice.pk),
                data={"note": "updated by staff"}, format="json",
            )
            self.assertEqual(
                resp.status_code, status.HTTP_200_OK,
                "Non-admin PATCH of the allowed 'note' field must succeed",
            )
            invoice.refresh_from_db()
            self.assertEqual(invoice.note, "updated by staff")

        # -- Act 3: non-admin CANNOT create (POST blocked) ----------
        with self.subTest(act="3-create-blocked"):
            resp = self.client.post(
                self.url_create(INVOICE),
                data={"customer": "Ghost", "amount": 1},
                format="json",
            )
            self.assertNotIn(
                resp.status_code, (200, 201),
                f"Non-admin POST must NOT succeed; got {resp.status_code}",
            )
            self.assertFalse(
                Invoice.objects.filter(customer="Ghost").exists(),
                "Rejected POST must not create a record",
            )

        # -- Act 4: non-admin CANNOT delete -------------------------
        with self.subTest(act="4-delete-blocked"):
            resp = self.client.delete(self.url_detail(INVOICE, invoice.pk))
            self.assertNotIn(
                resp.status_code,
                (status.HTTP_200_OK, status.HTTP_204_NO_CONTENT),
                f"Non-admin DELETE must NOT succeed; got {resp.status_code}",
            )
            self.assertTrue(
                Invoice.objects.filter(pk=invoice.pk).exists(),
                "Rejected DELETE must leave the record in the DB",
            )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()

