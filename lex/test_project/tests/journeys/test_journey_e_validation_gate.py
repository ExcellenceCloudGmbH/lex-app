"""
Journey E — "The validation gate"

Cluster-3 focus. A customer's API integration tries to push a mix of
valid and invalid records through the framework. The journey proves
the two validation gates in combination:

    * ``pre_validation`` cancels a save **before** it reaches the DB —
      no row is created, no history row is written, nothing is visible
      downstream.
    * ``post_validation`` rolls back a save **after** it has landed —
      any changes to an existing row are undone, and the prior state
      (including its history trail) is preserved intact.

Why this journey
----------------
Cluster 3 sub-cluster tests each pin one property of one hook. A real
integration failure looks like "I posted 10 records, 3 were bad, did
the other 7 land? Is my DB in a clean state?". This journey answers
that question by walking a realistic mix of good and bad payloads
through the API and asserting the DB + history afterwards.

Scenarios touched: 3.1 / 3.2 / 3.3 / 3.4 / 3.8 + the HTTP contract
from Cluster 2.
"""

from __future__ import annotations

import unittest

from lex.tests.e2e._authenticated_e2e_test_case import AuthenticatedE2ETestCase

from .models import ALL_MODELS, VALIDATED_INVOICE, ValidatedInvoice

import pytest

pytestmark = pytest.mark.journeys


class TestJourneyE_ValidationGate(AuthenticatedE2ETestCase):
    """Customer API pushes a mix of valid/invalid records; gates must hold."""

    e2e_models = ALL_MODELS
    as_superuser = True

    def test_pre_validation_cancels_save_cleanly(self) -> None:
        """
        ``pre_validation`` rejects before the DB is touched.

        Given: a customer POSTs a record the pre-hook will reject.
        When: the request completes.
        Then: the response is non-2xx, no row exists, no history row
        exists — the framework behaves as if the attempt never happened.
        """

        with self.subTest(act="1-post-forbidden"):
            resp = self.client.post(
                self.url_create(VALIDATED_INVOICE),
                data={"customer": "BLOCKED", "amount": 10},
                format="json",
            )
            self.assertNotIn(
                resp.status_code, (200, 201),
                f"pre_validation rejection must NOT succeed; got "
                f"{resp.status_code}: {getattr(resp, 'data', resp.content)!r}",
            )

        with self.subTest(act="2-no-row-exists"):
            self.assertFalse(
                ValidatedInvoice.objects.filter(customer="BLOCKED").exists(),
                "pre_validation cancel must leave zero rows behind — "
                "that is the distinguishing property from post_validation.",
            )

        with self.subTest(act="3-no-history-row-exists"):
            self.assertFalse(
                ValidatedInvoice.history.filter(customer="BLOCKED").exists(),
                "pre_validation cancel must leave NO history row — a "
                "history entry for a never-saved record would mislead "
                "compliance.",
            )

    def test_post_validation_rollback_preserves_prior_state(self) -> None:
        """
        ``post_validation`` rolls an update back to its pre-save snapshot.

        Given: a valid row already exists with ``amount=100``.
        When: the customer PATCHes ``amount=-5`` (post-hook will reject).
        Then: the row is restored to ``amount=100``, its pre-existing
        history trail is intact, and no dangling new history row was
        left behind by the rolled-back save.
        """
        # Seed a clean record with a single history row.
        invoice = ValidatedInvoice.objects.create(customer="Valid Co", amount=100)
        baseline_rows = list(
            ValidatedInvoice.history.filter(id=invoice.pk),
        )

        with self.subTest(act="1-patch-invalid-amount"):
            resp = self.client.patch(
                self.url_detail(VALIDATED_INVOICE, invoice.pk),
                data={"amount": -5}, format="json",
            )
            self.assertNotIn(
                resp.status_code, (200, 201),
                "A post_validation failure must surface as a non-2xx — "
                f"got {resp.status_code}: {getattr(resp, 'data', resp.content)!r}",
            )

        with self.subTest(act="2-row-rolled-back"):
            invoice.refresh_from_db()
            self.assertEqual(
                invoice.amount, 100,
                "post_validation rollback must restore the prior amount "
                "— the whole point of post-hook rollback is that the DB "
                "never carries rejected changes.",
            )
            self.assertEqual(
                invoice.customer, "Valid Co",
                "post_validation rollback must not disturb unrelated "
                "fields either.",
            )

        with self.subTest(act="3-history-trail-intact"):
            # There must be no *extra* history row from the failed PATCH
            # and the pre-existing row(s) must still be there.
            current_rows = list(
                ValidatedInvoice.history.filter(id=invoice.pk),
            )
            self.assertEqual(
                len(current_rows), len(baseline_rows),
                "A rolled-back PATCH must not leave a dangling history "
                f"row. Before: {len(baseline_rows)} rows; after: "
                f"{len(current_rows)} rows.",
            )

    def test_mixed_batch_good_rows_survive_bad_ones(self) -> None:
        """
        Sequential posts of good / bad / good records.

        Given: three POSTs in order — valid, pre-rejected, valid.
        When: all three requests complete.
        Then: exactly the two valid rows exist. The rejected POST
        leaves no trace. Each valid row has exactly one history entry
        — create.

        This is the journey every CSV-import integration walks.
        """
        # Row 1 — valid
        r1 = self.client.post(
            self.url_create(VALIDATED_INVOICE),
            data={"customer": "Good 1", "amount": 10}, format="json",
        )
        # Row 2 — blocked by pre_validation
        r2 = self.client.post(
            self.url_create(VALIDATED_INVOICE),
            data={"customer": "BLOCKED", "amount": 20}, format="json",
        )
        # Row 3 — valid
        r3 = self.client.post(
            self.url_create(VALIDATED_INVOICE),
            data={"customer": "Good 2", "amount": 30}, format="json",
        )

        with self.subTest(act="1-request-statuses"):
            self.assertIn(r1.status_code, (200, 201))
            self.assertNotIn(
                r2.status_code, (200, 201),
                "The blocked row must be visibly rejected — the "
                "customer needs a clear error for their retry logic.",
            )
            self.assertIn(r3.status_code, (200, 201))

        with self.subTest(act="2-only-valid-rows-exist"):
            customers = set(
                ValidatedInvoice.objects.values_list("customer", flat=True),
            )
            self.assertEqual(
                customers, {"Good 1", "Good 2"},
                "Only the two valid rows must exist — the pre-rejected "
                f"row must not have leaked in. Got {customers!r}",
            )

        with self.subTest(act="3-each-valid-row-has-one-history-row"):
            for c in ("Good 1", "Good 2"):
                count = ValidatedInvoice.history.filter(customer=c).count()
                self.assertEqual(
                    count, 1,
                    f"A valid row's history must consist of a single create "
                    f"entry — got {count} for customer={c!r}.",
                )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()

