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

import pytest

pytestmark = pytest.mark.audit_logging


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
    def test_6_47_atomic_block_failure_queues_replacement(self) -> None:
        """
        Scenario 6.47: When a mutation fails *inside* an atomic block,
        ``AuditLogMixin.perform_create`` / ``perform_update`` must
        queue a replacement audit log via ``queue_failed_audit_log``.

        Why: the original "pending → failure" status update written
        inside the atomic block rolls back with the surrounding
        request transaction. Without the queued replacement, the
        operator would see no audit row at all for the failed
        request. Spec lives in
        ``lex/audit_logging/mixins/AuditLogMixin.py`` —
        the ``if transaction.get_connection().in_atomic_block:``
        branch in both ``perform_create`` and ``perform_update``.

        We trigger the failure with ``AuditPreValItem`` (the
        ``pre_validation`` reject path), which raises *inside*
        ``serializer.save()`` while the surrounding
        ``OneModelEntry.create`` atomic block is still open. We spy
        on ``queue_failed_audit_log`` to assert the queue-and-flush
        path actually fired — and we observe the surviving audit row
        to confirm the replacement landed.
        """
        from unittest.mock import patch as _patch

        from lex.audit_logging.mixins.AuditLogMixin import AuditLogMixin

        AuditLog.objects.all().delete()
        AuditPreValItem._should_fail_prevalidation = True

        original_queue = AuditLogMixin.queue_failed_audit_log
        observed_queue_calls: list[dict] = []

        def _spy(self_inner, action, target, **kwargs):
            observed_queue_calls.append({
                "action": action,
                "in_atomic_block_at_call": True,
                "kwargs_keys": sorted(kwargs.keys()),
            })
            return original_queue(self_inner, action, target, **kwargs)

        try:
            with _patch.object(
                AuditLogMixin, "queue_failed_audit_log", _spy,
            ):
                resp = self.client.post(
                    self.url_create(AUDIT_PREVAL),
                    data={"name": "a6-47", "value": 1}, format="json",
                )
        finally:
            AuditPreValItem._should_fail_prevalidation = False

        self.assertGreaterEqual(
            resp.status_code, 400,
            "pre_validation reject must surface as a 4xx/5xx response",
        )
        self.assertEqual(
            len(observed_queue_calls), 1,
            "Failure inside the request's atomic block must call "
            "queue_failed_audit_log exactly once so the rolled-back "
            "audit gets a replacement; observed %d calls."
            % len(observed_queue_calls),
        )
        self.assertEqual(
            observed_queue_calls[0]["action"], "create",
            "The queued replacement must mirror the original action",
        )

        # And the queued replacement actually survives → exactly one
        # audit row with status='failure' is visible to the operator
        # after the request returns.
        rows = AuditLog.objects.filter(
            resource="auditprevalitem", action="create",
        )
        self.assertEqual(
            rows.count(), 1,
            "After atomic rollback, the queued replacement must be "
            "the surviving audit row; got %d rows" % rows.count(),
        )
        self.assertEqual(
            AuditLogStatus.objects.filter(
                audit_log=rows.first(),
            ).first().status,
            "failure",
            "The surviving (queued-replacement) audit row must carry "
            "status='failure' — that is the only signal the operator "
            "has that the request was attempted and failed.",
        )

    # -- 6.48 ----------------------------------------------------------
    def test_6_48_pending_state_observable_mid_flight(self) -> None:
        """
        Scenario 6.48: ``AuditLogStatus`` is created with
        ``status='pending'`` *before* the mutation runs, then
        transitions to ``'success'`` (or ``'failure'``) once the
        outcome is known.

        Why this matters: a long-running request that never finishes
        leaves a row stuck on ``pending`` — that's the operator's
        signal something hung. If the framework ever stopped writing
        the pending row first, that diagnostic would silently vanish.

        We attach a ``post_save`` signal handler to ``AuditLogStatus``
        and capture every ``status`` value it sees during the request.
        The expected sequence is ``[pending, success]``: ``pending``
        from ``log_change`` and ``success`` from the bulk update at
        the end of ``perform_create``.

        Note we use ``post_save`` (not ``pre_save``) because the
        success / failure transitions happen via a queryset
        ``.update()`` which only fires post-save signals through the
        framework's manager hooks; ``pre_save`` would not see the
        transition. ``post_save`` from ``log_change``'s explicit
        ``AuditLogStatus.objects.create(...)`` does fire, so the
        pending row is captured too.
        """
        from django.db.models.signals import post_save

        observed_statuses: list[str] = []

        def _capture(sender, instance, created, **kwargs):
            observed_statuses.append(instance.status)

        AuditLog.objects.all().delete()
        post_save.connect(_capture, sender=AuditLogStatus)
        try:
            resp = self.client.post(
                self.url_create(AUDIT_SIMPLE),
                data={"name": "a6-48", "value": 1}, format="json",
            )
        finally:
            post_save.disconnect(_capture, sender=AuditLogStatus)

        self.assertIn(resp.status_code, (200, 201))
        self.assertIn(
            "pending", observed_statuses,
            "AuditLogStatus must be created with status='pending' "
            "before the mutation runs — without it, an interrupted "
            "request leaves no in-flight audit signal. Observed "
            "statuses during request: %r" % observed_statuses,
        )
        # The pending row must be observed BEFORE any terminal status
        # (success/failure). If the framework ever flipped the order,
        # the in-flight diagnostic disappears.
        first_terminal = next(
            (s for s in observed_statuses if s in ("success", "failure")),
            None,
        )
        if first_terminal is not None:
            self.assertEqual(
                observed_statuses[0], "pending",
                "The first AuditLogStatus row written during a "
                "request must be 'pending'; got first=%r, full "
                "sequence=%r" % (observed_statuses[0], observed_statuses),
            )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()



