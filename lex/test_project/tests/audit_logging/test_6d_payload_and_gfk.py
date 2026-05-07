"""
Cluster 6d: Audit-log payload + GenericForeignKey contract.

Intent (from docs/features/tracking/audit logs.md +
docs/interface/record-detail/audit log tab.md):

    Every successful API write produces an ``AuditLog`` row carrying:
      - ``content_type`` + ``object_id`` (GFK back to the row)
      - ``payload``: the *full* serialized data of the operation,
        refreshed to the post-save state on update, capturing the
        deleted row's pre-delete state on delete.
      - ``calculation_id`` when the operation is part of a calculation.

    ``AuditLogStatus`` walks ``pending → success`` (or ``pending →
    failure`` carrying the traceback). The audit row is written
    BEFORE the operation, so failures are recorded with full context.

Cluster 6.1/6.2/6.3 only assert count + status. 6d closes:
  6.41 GFK populated on create
  6.42 create payload carries full body + post-save id
  6.43 update payload refreshed to post-save full state
  6.44 delete payload preserves pre-delete state
  6.45 ``pre_validation`` raise → failure audit row (replaces 6.4)
  6.46 failure traceback round-trips
  6.47 atomic-block failure queues replacement audit row
  6.48 pending state observable mid-flight

Scenario numbering matches docs/test-plan/test-clusters.md § 6d.
"""

from __future__ import annotations

import unittest

from django.contrib.contenttypes.models import ContentType

from lex.audit_logging.models.AuditLog import AuditLog
from lex.audit_logging.models.AuditLogStatus import AuditLogStatus
from lex.tests.e2e._e2e_test_case import E2ETestCase

from .extra_models import AUDIT_PREVAL, AuditPreValItem
from .models import ALL_MODELS, AUDIT_SIMPLE, AuditSimpleItem


class TestCluster06d_PayloadAndGFK(E2ETestCase):
    """Full payload shape + GenericForeignKey populated end-to-end."""

    e2e_models = ALL_MODELS + [AuditPreValItem]
    e2e_framework_models = [AuditLog, AuditLogStatus]
    e2e_unpatch = {"store_message", "build_cache_key"}

    # -- 6.41 ----------------------------------------------------------
    def test_6_41_create_audit_populates_content_type_and_object_id(self) -> None:
        """
        Scenario 6.41: After API create, the audit row's
        ``content_type`` + ``object_id`` resolve back to the created
        record. Without these the Audit Log Tab UI cannot show
        operations affecting a specific record.
        """
        AuditLog.objects.all().delete()

        resp = self.client.post(
            self.url_create(AUDIT_SIMPLE),
            data={"name": "a6-41", "value": 1}, format="json",
        )
        self.assertIn(resp.status_code, (200, 201))

        created = AuditSimpleItem.objects.get(name="a6-41")
        row = AuditLog.objects.get(
            resource="auditsimpleitem", action="create",
        )

        expected_ct = ContentType.objects.get_for_model(AuditSimpleItem)
        self.assertEqual(
            row.content_type_id, expected_ct.id,
            "AuditLog.content_type must resolve to the model under "
            "audit; got %r vs expected %r" % (row.content_type_id, expected_ct.id),
        )
        self.assertEqual(
            int(row.object_id), created.pk,
            "AuditLog.object_id must equal the created row's pk; "
            "got %r vs %r" % (row.object_id, created.pk),
        )

    # -- 6.42 ----------------------------------------------------------
    def test_6_42_create_payload_carries_full_body_and_id(self) -> None:
        """
        Scenario 6.42: Create audit payload carries the full request
        body AND the post-save ``id``.
        """
        AuditLog.objects.all().delete()
        resp = self.client.post(
            self.url_create(AUDIT_SIMPLE),
            data={"name": "a6-42", "value": 7}, format="json",
        )
        self.assertIn(resp.status_code, (200, 201))

        created = AuditSimpleItem.objects.get(name="a6-42")
        row = AuditLog.objects.get(
            resource="auditsimpleitem", action="create",
        )
        payload = row.payload or {}
        self.assertEqual(
            payload.get("name"), "a6-42",
            "Create payload must carry every request field; got %r" % (payload,),
        )
        # value may be stringified through _serialize_payload — accept either
        self.assertIn(
            str(payload.get("value")), ("7",),
            "Create payload must carry every request field (value)",
        )
        self.assertEqual(
            str(payload.get("id")), str(created.pk),
            "Create payload must carry the post-save id so the audit "
            "row can be reconciled against the persisted record",
        )

    # -- 6.43 ----------------------------------------------------------
    def test_6_43_update_payload_refreshed_to_full_post_save_state(self) -> None:
        """
        Scenario 6.43: After PATCH, the update audit's payload carries
        the *full* post-save serialized state — not just the patched
        field. This is what makes audit logs reconstructable.
        """
        AuditLog.objects.all().delete()
        item = AuditSimpleItem.objects.create(name="a6-43", value=1)

        resp = self.client.patch(
            self.url_detail(AUDIT_SIMPLE, item.pk),
            data={"value": 42}, format="json",
        )
        self.assertEqual(resp.status_code, 200)

        row = AuditLog.objects.get(
            resource="auditsimpleitem", action="update",
        )
        payload = row.payload or {}
        # Both fields must appear, even though only ``value`` was patched
        self.assertEqual(
            payload.get("name"), "a6-43",
            "Update payload must carry the full post-save state — "
            "name should be present even though it wasn't patched; "
            "got %r" % (payload,),
        )
        self.assertIn(
            str(payload.get("value")), ("42",),
            "Update payload must reflect post-save value (the new "
            "value, not the prior one); got %r" % (payload,),
        )

    # -- 6.44 ----------------------------------------------------------
    def test_6_44_delete_payload_preserves_pre_delete_state(self) -> None:
        """
        Scenario 6.44: Delete audit's payload preserves every field of
        the deleted record. Docs: "you can always inspect what was
        removed."
        """
        AuditLog.objects.all().delete()
        item = AuditSimpleItem.objects.create(name="a6-44", value=999)
        pk = item.pk

        resp = self.client.delete(self.url_detail(AUDIT_SIMPLE, pk))
        self.assertIn(resp.status_code, (200, 204))

        row = AuditLog.objects.get(
            resource="auditsimpleitem", action="delete",
        )
        payload = row.payload or {}
        self.assertEqual(
            payload.get("name"), "a6-44",
            "Delete payload must preserve the pre-delete name; got %r" % (payload,),
        )
        self.assertIn(
            str(payload.get("value")), ("999",),
            "Delete payload must preserve the pre-delete value; got %r" % (payload,),
        )

    # -- 6.45 ----------------------------------------------------------
    def test_6_45_pre_validation_failure_writes_failure_audit_row(self) -> None:
        """
        Scenario 6.45: ``pre_validation`` raises on POST → response is
        400/500, AuditLogStatus.status == 'failure', traceback non-
        empty, no DB row created. Replaces the previously-skipped 6.4
        — this path is reachable today via validation hooks; no
        middleware-level audit hook needed.
        """
        AuditLog.objects.all().delete()
        AuditPreValItem._should_fail_prevalidation = True
        try:
            resp = self.client.post(
                self.url_create(AUDIT_PREVAL),
                data={"name": "a6-45", "value": 1}, format="json",
            )
        finally:
            AuditPreValItem._should_fail_prevalidation = False

        # No DB row created
        self.assertFalse(
            AuditPreValItem.objects.filter(name="a6-45").exists(),
            "pre_validation reject must leave zero DB rows",
        )
        # Response is a failure (4xx or 5xx — exact code is BUG-005 territory)
        self.assertGreaterEqual(resp.status_code, 400)

        rows = AuditLog.objects.filter(
            resource="auditprevalitem", action="create",
        )
        self.assertEqual(
            rows.count(), 1,
            "Failed API operation must produce exactly one audit row; "
            "got %d" % rows.count(),
        )
        status_row = AuditLogStatus.objects.filter(audit_log=rows.first()).first()
        self.assertEqual(
            status_row.status, "failure",
            "Status must transition pending → failure on pre_validation reject",
        )
        self.assertTrue(
            status_row.error_traceback,
            "Failure status row must carry the traceback for diagnosis",
        )

    # -- 6.46 ----------------------------------------------------------
    def test_6_46_failure_traceback_preserved_multiline(self) -> None:
        """
        Scenario 6.46: Failure traceback round-trips through
        ``resolve_exception_traceback`` — multi-line content
        preserved so operators have full diagnostic info.
        """
        AuditLog.objects.all().delete()
        AuditPreValItem._should_fail_prevalidation = True
        try:
            self.client.post(
                self.url_create(AUDIT_PREVAL),
                data={"name": "a6-46", "value": 1}, format="json",
            )
        finally:
            AuditPreValItem._should_fail_prevalidation = False

        row = AuditLog.objects.filter(
            resource="auditprevalitem", action="create",
        ).first()
        self.assertIsNotNone(row, "Failure must produce an audit row")
        status_row = AuditLogStatus.objects.filter(audit_log=row).first()
        tb = status_row.error_traceback or ""
        self.assertIn(
            "ValidationError", tb,
            "Failure traceback must name the raised exception class",
        )

    # -- 6.47 ----------------------------------------------------------
    @unittest.skip(
        "Scenario 6.47: atomic-block failure queues a replacement "
        "audit row through `_pending_failed_audit_logs`. Requires a "
        "controlled `perform_create` raise inside an atomic block — "
        "the current pre_validation path runs before the atomic save "
        "so this branch is not reachable through that fixture. "
        "Documented for later — a `post_save`-raising fixture or a "
        "patched serializer.save would unlock it."
    )
    def test_6_47_atomic_block_failure_queues_replacement(self) -> None:
        """Scenario 6.47: see skip reason."""

    # -- 6.48 ----------------------------------------------------------
    @unittest.skip(
        "Scenario 6.48: pending intermediate state observable mid-"
        "flight. Requires a `pre_save` signal handler that captures "
        "AuditLogStatus.status during the request. Deferred — needs a "
        "shared signal-spy fixture."
    )
    def test_6_48_pending_state_observable_mid_flight(self) -> None:
        """Scenario 6.48: see skip reason."""


if __name__ == "__main__":  # pragma: no cover
    unittest.main()



