"""Every action run leaves an audit-log entry.

Intent
------
An action (``@lex_action``) changes data outside the CRUD endpoints, so it
must be just as visible in the audit log: one entry per run, naming the
caller, the model and the record, with the action's name in the payload and
status "success". A failed run leaves no entry, because the audit entry is
written in the action's transaction.

Cluster 6s — scenarios 6.121–6.122. Type: E.
Covers: lex/api/actions.py (ModelActionView._audit).
Run: python -m lex pytest lex/test_project/tests/audit_logging/test_6s_action_audit.py -v
"""

from __future__ import annotations

import pytest
from django.urls import reverse

from lex.audit_logging.models.AuditLog import AuditLog
from lex.audit_logging.models.AuditLogStatus import AuditLogStatus
from lex.test_project.tests._e2e_test_case import E2ETestCase, _MockContainer

from .models import ACTION_MODELS, AUDIT_ACTION, AuditActionItem

pytestmark = pytest.mark.audit_logging


class TestCluster06s_ActionAudit(E2ETestCase):
    """Cluster 6s: audit entries for actions."""

    e2e_models = ACTION_MODELS
    e2e_framework_models = [AuditLog, AuditLogStatus]

    def _close(self, item, action="close"):
        url = reverse(
            "process_admin_rest_api:model-record-action",
            kwargs={"model_container": _MockContainer(AUDIT_ACTION), "calculationId": "audit-6s", "pk": item.pk, "action": action},
        )
        return self.client.post(url, {}, format="json")

    # -- 6.121 ---------------------------------------------------------
    def test_6_121_action_run_writes_one_entry(self) -> None:
        """
        Scenario 6.121: an action run is audited.
        Given: an AuditActionItem
        When: its close action runs
        Then: exactly one audit entry for the model and record, by the caller,
              with {"lex_action": "close", "id": <pk>} and status success.
        """
        item = AuditActionItem.objects.create(name="file")
        self.assertEqual(self._close(item).status_code, 200)
        entries = AuditLog.objects.filter(resource="auditactionitem")
        self.assertEqual(entries.count(), 1)
        entry = entries.get()
        self.assertEqual(entry.payload["lex_action"], "close")
        self.assertEqual(entry.payload["id"], item.pk)
        self.assertEqual(entry.calculation_id, "audit-6s")
        self.assertIn("e2e_user", entry.author)
        self.assertEqual(AuditLogStatus.objects.get(audit_log=entry).status, "success")

    # -- 6.122 ---------------------------------------------------------
    def test_6_122_unknown_action_leaves_no_entry(self) -> None:
        """
        Scenario 6.122: a refused call is not audited as a run.
        Given: an AuditActionItem
        When: an action that does not exist is called
        Then: 404 and no audit entry.
        """
        item = AuditActionItem.objects.create(name="file")
        self.assertEqual(self._close(item, action="nope").status_code, 404)
        self.assertFalse(AuditLog.objects.filter(resource="auditactionitem").exists())
