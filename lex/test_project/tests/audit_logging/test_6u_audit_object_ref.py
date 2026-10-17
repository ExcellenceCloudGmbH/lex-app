"""Audit entries name a record by its public id too.

Intent
------
A record addressed by a public uuid (``lex_lookup_field``) is the record a
project's users and integrators know; its integer key may be hidden from
them. Its audit entries must name it the same way: lex stores the public id
in ``AuditLog.object_ref`` next to ``object_id`` for creates, updates and
deletes through the generic API.

Cluster 6u — scenario 6.129. Type: E.
Covers: lex/audit_logging/models/AuditLog.py (object_ref), lex/audit_logging/migrations/0008_auditlog_object_ref.py,
lex/audit_logging/mixins/AuditLogMixin.py (_object_ref).
Run: python -m lex pytest lex/test_project/tests/audit_logging/test_6u_audit_object_ref.py -v
"""

from __future__ import annotations

import os
from unittest import mock
import pytest
from django.urls import reverse

from lex.audit_logging.models.AuditLog import AuditLog
from lex.audit_logging.models.AuditLogStatus import AuditLogStatus
from lex.test_project.tests._e2e_test_case import E2ETestCase, _MockContainer
from lex.test_project.tests.crud_api.models import PUBLIC_ID, PUBLIC_ID_MODELS, PublicIdItem

# These scenarios are strict mode's behaviour: pinned, whatever LEX_LEGACY_SECURITY the run sets (a legacy run keeps
# the pre-3.0 behaviour, which other clusters cover).
_STRICT = mock.patch.dict(os.environ, {"LEX_LEGACY_SECURITY": "false"})


def setUpModule():
    _STRICT.start()


def tearDownModule():
    _STRICT.stop()

pytestmark = pytest.mark.audit_logging


class TestCluster06u_AuditObjectRef(E2ETestCase):
    """Cluster 6u: object_ref on audit entries."""

    e2e_models = PUBLIC_ID_MODELS
    e2e_framework_models = [AuditLog, AuditLogStatus]

    # -- 6.129 ---------------------------------------------------------
    def test_6_129_entries_carry_the_public_id(self) -> None:
        """
        Scenario 6.129: create, update and delete entries name the record's uuid.
        Given: a model addressed by uuid
        When: a record is created, renamed by uuid and deleted by uuid
        Then: all three entries carry object_ref = the record's uuid (and its object_id).
        """
        created = self.client.post(self.url_create(PUBLIC_ID), {"name": "first"}, format="json")
        self.assertEqual(created.status_code, 201, created.content[:300])
        item = PublicIdItem.objects.get(name="first")
        by_uuid = reverse(
            "process_admin_rest_api:model-one-entry-by-lookup",
            kwargs={"model_container": _MockContainer(PUBLIC_ID), "calculationId": "c", "lookup": item.uuid},
        )
        self.assertEqual(self.client.patch(by_uuid, {"name": "second"}, format="json").status_code, 200)
        self.assertEqual(self.client.delete(by_uuid).status_code, 200)
        entries = AuditLog.objects.filter(resource="publiciditem").order_by("id")
        self.assertEqual([entry.action for entry in entries], ["create", "update", "delete"])
        for entry in entries:
            self.assertEqual((entry.object_ref, entry.object_id), (str(item.uuid), item.pk), entry.action)
