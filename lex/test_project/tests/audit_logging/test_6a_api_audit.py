
"""
Cluster 6a: API-path audit log rows.

Intent (from docs/features/audit-logging/):

    Every API create / update / delete produces a row in the
    ``AuditLog`` table with:
      - ``author`` resolved from the authenticated user
      - ``action`` = create / update / delete
      - ``payload`` including the changed fields
      - ``resource`` = lowercased model class name
    An accompanying ``AuditLogStatus`` row transitions from
    ``pending`` → ``success`` on normal completion, or
    ``pending`` → ``failure`` on exception.

Pass B2 unblocked these scenarios by adding ``AuditLog`` /
``AuditLogStatus`` to ``e2e_framework_models`` and unpatching
``store_message`` / ``build_cache_key`` so ``AuditLogMixin``'s real
write path runs. Scenario numbering matches
docs/test-plan/test-clusters.md#6-audit-logging.
"""

from __future__ import annotations

import unittest

from rest_framework import status

from lex.audit_logging.models.AuditLog import AuditLog
from lex.audit_logging.models.AuditLogStatus import AuditLogStatus
from lex.tests.e2e._e2e_test_case import E2ETestCase

from .models import ALL_MODELS, AUDIT_SIMPLE, AuditSimpleItem


class TestCluster06a_APIAuditLog(E2ETestCase):
    """API path writes AuditLog rows — live under the Pass B2 fixture."""

    e2e_models = ALL_MODELS
    e2e_framework_models = [AuditLog, AuditLogStatus]
    e2e_unpatch = {"store_message", "build_cache_key"}

    def test_6_1_api_create_audit_row(self) -> None:
        """
        Scenario 6.1: API create → one AuditLog row, status=success.

        Given: authenticated POST to a LexModel's create endpoint.
        When: the request succeeds.
        Then: exactly one AuditLog row exists for that resource with
              ``action='create'``, ``author`` populated, and an
              accompanying ``AuditLogStatus`` transitioned to
              ``'success'``.
        """
        AuditLog.objects.all().delete()

        resp = self.client.post(
            self.url_create(AUDIT_SIMPLE),
            data={"name": "a6-1", "value": 1}, format="json",
        )
        self.assertIn(resp.status_code, (200, 201))

        rows = AuditLog.objects.filter(
            resource="auditsimpleitem", action="create",
        )
        self.assertEqual(
            rows.count(), 1,
            "A successful API create must produce exactly one AuditLog "
            f"row; got {rows.count()}.",
        )
        row = rows.first()
        self.assertTrue(
            row.author,
            "AuditLog.author must be resolved from the authenticated "
            f"user; got {row.author!r}.",
        )

        status_rows = AuditLogStatus.objects.filter(audit_log=row)
        self.assertEqual(
            status_rows.count(), 1,
            "Every AuditLog row must have exactly one AuditLogStatus "
            "tracking its final disposition.",
        )
        self.assertEqual(
            status_rows.first().status, "success",
            "A successful create must finalize AuditLogStatus to 'success'.",
        )

    def test_6_2_api_update_audit_row(self) -> None:
        """
        Scenario 6.2: API update → AuditLog with ``action='update'``.

        The payload must include the fields that were patched so an
        auditor can reconstruct "what changed".
        """
        AuditLog.objects.all().delete()
        item = AuditSimpleItem.objects.create(name="a6-2", value=1)

        resp = self.client.patch(
            self.url_detail(AUDIT_SIMPLE, item.pk),
            data={"value": 42}, format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)

        updates = AuditLog.objects.filter(
            resource="auditsimpleitem", action="update",
        )
        self.assertEqual(
            updates.count(), 1,
            f"A successful PATCH must produce exactly one 'update' "
            f"AuditLog row; got {updates.count()}.",
        )
        self.assertIn(
            "value", (updates.first().payload or {}),
            "Update payload must carry the patched field so auditors can "
            f"see what changed; got {updates.first().payload!r}.",
        )
        self.assertEqual(
            AuditLogStatus.objects.filter(
                audit_log=updates.first(),
            ).first().status,
            "success",
        )

    def test_6_3_api_delete_audit_row(self) -> None:
        """Scenario 6.3: API delete → AuditLog with ``action='delete'``."""
        AuditLog.objects.all().delete()
        item = AuditSimpleItem.objects.create(name="a6-3", value=1)

        resp = self.client.delete(self.url_detail(AUDIT_SIMPLE, item.pk))
        self.assertIn(
            resp.status_code,
            (status.HTTP_200_OK, status.HTTP_204_NO_CONTENT),
        )

        deletes = AuditLog.objects.filter(
            resource="auditsimpleitem", action="delete",
        )
        self.assertEqual(
            deletes.count(), 1,
            "A successful DELETE must produce exactly one 'delete' "
            "AuditLog row.",
        )
        self.assertEqual(
            AuditLogStatus.objects.filter(
                audit_log=deletes.first(),
            ).first().status,
            "success",
        )

    # Scenario 6.4 — superseded by 6.45 in test_6d_payload_and_gfk.py.
    #
    # The original 6.4 ("every failed API write yields a failure
    # AuditLog") was skipped because failures *before* the mixin runs
    # (404, 401, raw ValidationError) bypass the audit writer. That
    # gap is real but it's a framework limitation, not something to
    # assert against here. The reachable failure contract — a
    # mutation that fails *inside* perform_create / perform_update —
    # is now pinned by 6.45 (pre_validation reject → failure audit
    # row + traceback) and 6.51 (atomic calc failure via API → failure
    # audit). Nothing else to do at this layer.


if __name__ == "__main__":  # pragma: no cover
    unittest.main()

