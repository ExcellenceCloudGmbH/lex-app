"""
Cluster 12d: AuditLog payload filtering.

Intent (from ``LexSerializer.to_representation`` + the audit-log
features doc):

    When an ``AuditLog`` row is serialized, the ``payload`` field is
    passed through two permission filters against the **target**
    model:

      1. Any ForeignKey reference (``{"id": ...}`` dict) the caller
         cannot read is stripped from the payload.
      2. Any target-model field the caller cannot read is pruned from
         both the top-level payload and the ``payload.updates``
         sub-dict.

    If the caller cannot read the target row at all, ``payload``
    collapses to only the pinned identifier keys (``id``,
    ``id_field``, ``short_description``).

We exercise the serializer directly via
:func:`~lex.api.serializers.base_serializers.model2serializer`
against the ``AuditLog`` model — no HTTP route needed. This keeps the
test focused on the serializer contract rather than the viewset
wiring.

Scenario numbering matches
docs/test-plan/test-clusters.md#12-serializer-contract.
"""

from __future__ import annotations

import unittest

from django.contrib.contenttypes.models import ContentType
from lex.api.serializers.base_serializers import model2serializer
from lex.audit_logging.models.AuditLog import AuditLog
from lex.audit_logging.models.AuditLogStatus import AuditLogStatus
from lex.core.models.LexModel import PermissionResult
from lex.tests.e2e._e2e_test_case import E2ETestCase
from rest_framework.test import APIRequestFactory

from .models import ALL_MODELS, ProtectedWideItem, RelatedItem, WideItem

import pytest

pytestmark = pytest.mark.serializers


class TestCluster12d_AuditLogPayloadFiltering(E2ETestCase):
    """Serialize an AuditLog row as a non-admin — assert payload is filtered.

    Uses ``model2serializer(AuditLog)`` and a DRF request factory so we
    exercise :meth:`LexSerializer.to_representation` end-to-end,
    including :meth:`_filter_foreign_key_relations` and
    :meth:`_get_audit_log_payload_visible_fields`, without depending
    on URL routing.

    Inherits :class:`E2ETestCase` for its history-table and
    framework-model fixture — that's the only reason we pay the e2e
    setup cost here; we don't use the ``APIClient``.
    """

    e2e_models = ALL_MODELS
    e2e_framework_models = [AuditLog, AuditLogStatus]

    def setUp(self):
        super().setUp()
        self.factory = APIRequestFactory()
        self.AuditLogSerializer = model2serializer(AuditLog)

    # -- helpers -------------------------------------------------------

    def _serialize(self, audit_log):
        """Render an AuditLog row as the non-admin caller."""
        request = self.factory.get("/")
        request.user = self.user
        serializer = self.AuditLogSerializer(
            instance=audit_log, context={"request": request},
        )
        return serializer.data

    def _build_audit_log(self, target, payload):
        ct = ContentType.objects.get_for_model(type(target))
        return AuditLog.objects.create(
            author="test@example.com",
            resource=type(target)._meta.model_name,
            action="update",
            payload=payload,
            content_type=ct,
            object_id=target.pk,
        )

    # -- 12.23 ---------------------------------------------------------
    def test_12_23_fk_reference_caller_cannot_read_is_stripped(self) -> None:
        """Scenario 12.23: a FK dict whose target is unreadable is
        dropped from the payload; the rest of the payload survives."""
        related = RelatedItem.objects.create(name="fk-target", code="T1")
        target = WideItem.objects.create(name="t12-23", related=related)

        # Deny reading the related row for this test.
        original = RelatedItem.permission_read
        RelatedItem.permission_read = lambda self, uc: PermissionResult.deny(
            "12.23 hard deny on FK target",
        )
        try:
            audit = self._build_audit_log(
                target,
                payload={
                    "name": "t12-23",
                    "related": {"id": related.pk, "short_description": "fk-target"},
                },
            )

            data = self._serialize(audit)
            payload = data.get("payload") or {}

            self.assertIn(
                "name", payload,
                "Non-FK fields must survive FK filtering",
            )
            self.assertNotIn(
                "related", payload,
                f"FK reference to an unreadable row must be stripped; "
                f"got payload={payload!r}",
            )
        finally:
            RelatedItem.permission_read = original

    # -- 12.24 ---------------------------------------------------------
    def test_12_24_unreadable_fields_pruned_from_updates(self) -> None:
        """Scenario 12.24: fields the caller cannot read on the target
        model are removed from ``payload.updates``.

        ``ProtectedWideItem`` allows non-admins to read only ``id``,
        ``name``, ``amount``. The AuditLog payload contains
        ``secret_note`` and ``secret_category`` in the updates dict —
        they must be pruned.
        """
        target = ProtectedWideItem.objects.create(
            name="t12-24", amount="10.0000",
            secret_note="confidential", secret_category="beta",
        )

        audit = self._build_audit_log(
            target,
            payload={
                "id": target.pk,
                "name": "t12-24",
                "amount": "10.0000",
                "secret_note": "confidential",
                "updates": {
                    "name": "t12-24",
                    "amount": "10.0000",
                    "secret_note": "confidential",
                    "secret_category": "beta",
                },
            },
        )

        data = self._serialize(audit)
        payload = data.get("payload") or {}

        # Top-level payload: restricted keys gone.
        self.assertIn("name", payload, "Allowed top-level field missing")
        self.assertIn("amount", payload, "Allowed top-level field missing")
        self.assertNotIn(
            "secret_note", payload,
            f"Restricted top-level field leaked: {payload!r}",
        )

        # Updates sub-dict: same filter applied.
        self.assertIn(
            "updates", payload,
            "Payload lost its 'updates' sub-dict during filtering",
        )
        updates = payload["updates"]
        self.assertIn("name", updates)
        self.assertIn("amount", updates)
        self.assertNotIn(
            "secret_note", updates,
            f"Restricted field leaked into updates: {updates!r}",
        )
        self.assertNotIn(
            "secret_category", updates,
            f"Restricted field leaked into updates: {updates!r}",
        )

    # -- 12.25 ---------------------------------------------------------
    def test_12_25_target_denied_collapses_payload(self) -> None:
        """Scenario 12.25: when ``permission_read`` denies the target
        entirely, ``payload`` collapses — only pinned identifier keys
        (``id`` / ``id_field`` / ``short_description``) may survive.
        """
        target = ProtectedWideItem.objects.create(
            name="t12-25", amount="1.0000",
            secret_note="confidential",
        )

        original = ProtectedWideItem.permission_read
        ProtectedWideItem.permission_read = lambda self, uc: PermissionResult.deny(
            "12.25 hard deny on target",
        )
        try:
            audit = self._build_audit_log(
                target,
                payload={
                    "id": target.pk,
                    "name": "t12-25",
                    "amount": "1.0000",
                    "secret_note": "confidential",
                    "updates": {"name": "t12-25", "amount": "1.0000"},
                },
            )

            data = self._serialize(audit)
            payload = data.get("payload") or {}

            # Non-pinned fields must be gone.
            forbidden = {"name", "amount", "secret_note"}
            leaked = forbidden & set(payload.keys())
            self.assertFalse(
                leaked,
                f"Target-denied AuditLog payload leaked fields {leaked}; "
                f"payload={payload!r}",
            )
            # Updates, if present, must also have been pruned to empty.
            if "updates" in payload:
                self.assertFalse(
                    set(payload["updates"].keys()) & forbidden,
                    f"updates sub-dict leaked forbidden fields: "
                    f"{payload['updates']!r}",
                )
        finally:
            ProtectedWideItem.permission_read = original


if __name__ == "__main__":  # pragma: no cover
    unittest.main()


