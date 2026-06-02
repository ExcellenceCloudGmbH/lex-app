"""
Cluster 6e: Bulk audit logging — ``BulkAuditLogMixin``.

Intent (from docs/features/tracking/audit logs.md):

    "Each individual record in a bulk operation gets its own audit
    log entry — so a bulk update of 100 records creates 100 audit log
    entries." (BulkAuditLogMixin docs)

Cluster 2e's bulk DELETE scenarios never assert audit row count.
6e closes:
  6.51 DELETE /many/?ids=… → one audit row per deleted record
  6.52 partial-success contract (some rows deletable, others not) ⏸
  6.53 per-row GFK populated so the per-record Audit Log Tab works

Scenario numbering matches docs/test-plan/test-clusters.md § 6e.
"""

from __future__ import annotations

import unittest

from django.contrib.contenttypes.models import ContentType
from lex.audit_logging.models.AuditLog import AuditLog
from lex.audit_logging.models.AuditLogStatus import AuditLogStatus
from lex.tests.e2e._e2e_test_case import E2ETestCase

from .models import ALL_MODELS, AUDIT_SIMPLE, AuditSimpleItem

import pytest

pytestmark = pytest.mark.audit_logging


class TestCluster06e_BulkAuditLog(E2ETestCase):
    """``BulkAuditLogMixin`` writes one audit row per affected record."""

    e2e_models = ALL_MODELS
    e2e_framework_models = [AuditLog, AuditLogStatus]
    e2e_unpatch = {"store_message", "build_cache_key"}

    # -- 6.51 ----------------------------------------------------------
    def test_6_51_bulk_delete_writes_one_audit_row_per_record(self) -> None:
        """
        Scenario 6.51: ``DELETE /many/?ids=1,2,3`` produces exactly N
        audit rows (one per deleted record), all ``action='delete'``,
        all ``status='success'``.
        """
        AuditLog.objects.all().delete()
        items = [
            AuditSimpleItem.objects.create(name=f"a6-51-{i}", value=i)
            for i in range(3)
        ]
        ids = [str(i.pk) for i in items]

        url = self.url_many(AUDIT_SIMPLE) + "?" + "&".join(f"ids={pk}" for pk in ids)
        resp = self.client.delete(url)
        self.assertIn(
            resp.status_code, (200, 204),
            "Bulk DELETE must succeed; got %d: %r"
            % (resp.status_code, getattr(resp, "data", resp.content)),
        )

        rows = AuditLog.objects.filter(
            resource="auditsimpleitem", action="delete",
        )
        self.assertEqual(
            rows.count(), 3,
            "Bulk delete of 3 records must produce 3 audit rows; got %d. "
            "BulkAuditLogMixin contract: one audit row per affected record."
            % rows.count(),
        )
        for row in rows:
            status_row = AuditLogStatus.objects.filter(audit_log=row).first()
            self.assertEqual(
                getattr(status_row, "status", None), "success",
                "Every successful bulk-delete row must have status=success; "
                "got %r" % (getattr(status_row, "status", None),),
            )

    # -- 6.52 ----------------------------------------------------------
    def test_6_52_bulk_delete_failure_marks_all_rows_failure(self) -> None:
        """
        Scenario 6.52: When a bulk delete fails mid-flight, every
        audit row written for the batch transitions to ``failure``.

        Reading ``BulkAuditLogMixin.perform_bulk_destroy`` in
        ``lex/audit_logging/mixins/BulkAuditLogMixin.py``: the bulk
        path is all-or-nothing by design — ``queryset.delete()`` is a
        single statement, so there is no partial-success surface to
        test. The customer-visible contract that *can* be tested is
        the failure path: when the underlying delete raises, every
        pending audit row in the batch is updated to ``failure`` with
        the same traceback so the operator can see exactly which
        records the failed bulk op was meant to cover.

        We force the failure by patching ``QuerySet.delete`` to raise.
        That bypasses any DB-level integrity checks and exercises the
        ``except`` branch in ``perform_bulk_destroy`` directly.
        """
        from unittest.mock import patch as _patch

        from django.db.models.query import QuerySet

        AuditLog.objects.all().delete()
        items = [
            AuditSimpleItem.objects.create(name=f"a6-52-{i}", value=i)
            for i in range(3)
        ]
        original_pks = sorted(int(i.pk) for i in items)

        url = self.url_many(AUDIT_SIMPLE) + "?" + "&".join(
            f"ids={i.pk}" for i in items
        )

        original_delete = QuerySet.delete

        def _raise_delete(self_qs, *a, **kw):
            # Only fail for the AuditSimpleItem queryset so we don't
            # accidentally break audit-row writes on the way out.
            if self_qs.model is AuditSimpleItem:
                raise RuntimeError("simulated bulk-delete failure")
            return original_delete(self_qs, *a, **kw)

        with _patch.object(QuerySet, "delete", _raise_delete):
            # Plain ``RuntimeError`` is not a DRF-recognized exception
            # so DRF re-raises it through the test client. The view's
            # ``except`` branch still ran (queueing audit rows) before
            # the re-raise — that is what we are here to assert.
            self.client.raise_request_exception = False
            try:
                resp = self.client.delete(url)
            finally:
                self.client.raise_request_exception = True

        self.assertGreaterEqual(
            resp.status_code, 400,
            "A bulk-delete that raised inside perform_bulk_destroy "
            "must surface as a 4xx/5xx response.",
        )
        # Source rows must still be present — the simulated failure
        # rolled the delete back.
        self.assertEqual(
            sorted(
                AuditSimpleItem.objects.filter(pk__in=original_pks)
                .values_list("pk", flat=True)
            ),
            original_pks,
            "Failed bulk-delete must leave source rows intact",
        )

        rows = AuditLog.objects.filter(
            resource="auditsimpleitem", action="delete",
        )
        self.assertEqual(
            rows.count(), 3,
            "Even on failure, BulkAuditLogMixin must write one audit "
            "row per record in the batch so the operator knows what "
            "the failed op was meant to touch; got %d." % rows.count(),
        )
        statuses = list(
            AuditLogStatus.objects.filter(audit_log__in=rows).values_list(
                "status", flat=True,
            )
        )
        self.assertEqual(
            sorted(statuses), ["failure"] * 3,
            "Every audit row in the failed batch must end on "
            "status='failure'; got %r." % statuses,
        )

    # -- 6.53 ----------------------------------------------------------
    def test_6_53_bulk_delete_preserves_per_row_gfk(self) -> None:
        """
        Scenario 6.53: Each bulk-delete audit row's
        ``content_type`` + ``object_id`` points back at its own
        pre-delete instance — so the Audit Log Tab on each individual
        record still finds its delete entry after the bulk op.
        """
        AuditLog.objects.all().delete()
        items = [
            AuditSimpleItem.objects.create(name=f"a6-53-{i}", value=i)
            for i in range(3)
        ]
        original_pks = sorted(int(i.pk) for i in items)

        url = self.url_many(AUDIT_SIMPLE) + "?" + "&".join(
            f"ids={i.pk}" for i in items
        )
        resp = self.client.delete(url)
        self.assertIn(resp.status_code, (200, 204))

        rows = AuditLog.objects.filter(
            resource="auditsimpleitem", action="delete",
        )
        self.assertEqual(rows.count(), 3, "Need 3 audit rows for 3 deletes")

        expected_ct = ContentType.objects.get_for_model(AuditSimpleItem)
        observed_pks = []
        for row in rows:
            self.assertEqual(
                row.content_type_id, expected_ct.id,
                "Every bulk-delete audit row's content_type must "
                "resolve to AuditSimpleItem",
            )
            self.assertIsNotNone(
                row.object_id,
                "Every bulk-delete audit row must carry object_id "
                "back to the deleted record",
            )
            observed_pks.append(int(row.object_id))

        self.assertEqual(
            sorted(observed_pks), original_pks,
            "Each audit row's object_id must match a distinct deleted "
            "row's pk — got %r vs original %r"
            % (sorted(observed_pks), original_pks),
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()

