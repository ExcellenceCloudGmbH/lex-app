"""What the audit log stores: ``AUDIT_PAYLOAD`` and ``AUDIT_ACTOR``.

Intent
------
lex copies every write into its audit log: the payload and who wrote it. A
project whose erasure rules do not reach lex's tables needs to keep personal
data out of them. ``AUDIT_PAYLOAD = "fields"`` stores only which fields a write
touched (values become ``None``, nested objects keep their keys); ``"none"``
stores nothing; the default ``"full"`` is unchanged. ``AUDIT_ACTOR`` names
the author of every entry, and the ``created_by``/``edited_by`` of every
record written in a request, through a project function — e.g. ``"user:42"``
instead of a name or an e-mail address. The same rules hold for actions. An
unknown ``AUDIT_PAYLOAD`` stops startup.

Cluster 6t — scenarios 6.123–6.128. Type: U + E.
Covers: lex/audit_logging/utils/policy.py, lex/audit_logging/mixins/AuditLogMixin.py (log_change),
lex/audit_logging/mixins/BulkAuditLogMixin.py, lex/api/actions.py (_audit),
lex/audit_logging/utils/InitialDataAuditLogger.py, lex/audit_logging/utils/calculation_audit.py,
lex/core/models/LexModel.py (_resolve_request_audit_actor), lex/core/extensions.py (AUDIT_PAYLOAD, AUDIT_ACTOR).
Run: python -m lex pytest lex/test_project/tests/audit_logging/test_6t_audit_policy.py -v
"""

from __future__ import annotations

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.test import SimpleTestCase
from django.urls import reverse

from lex.audit_logging.models.AuditLog import AuditLog
from lex.audit_logging.models.AuditLogStatus import AuditLogStatus
from lex.audit_logging.utils.policy import audit_payload
from lex.core import extensions
from lex.test_project.tests._e2e_test_case import E2ETestCase, _MockContainer

from .models import ACTION_MODELS, ALL_MODELS, AUDIT_ACTION, AUDIT_SIMPLE, AuditActionItem, AuditSimpleItem

pytestmark = pytest.mark.audit_logging


def pseudonym(user):
    return f"user:{user.pk}"


class TestCluster06t_AuditPayloadUnit(SimpleTestCase):
    """Cluster 6t (unit): the payload rule."""

    # -- 6.123 ---------------------------------------------------------
    def test_6_123_payload_modes(self) -> None:
        """
        Scenario 6.123: full, fields and none.
        Given: a payload with a scalar, a list and a nested object
        When: audit_payload runs under each AUDIT_PAYLOAD, and validation with a typo
        Then: full keeps it; fields keeps every key with None values (nested keys too);
              none stores {}; the typo stops startup.
        """
        payload = {"iban": "DE89 3704", "tags": ["a"], "address": {"city": "Berlin"}}
        with extensions.override_extensions(AUDIT_PAYLOAD="full"):
            self.assertEqual(audit_payload(payload), payload)
        with extensions.override_extensions(AUDIT_PAYLOAD="fields"):
            self.assertEqual(audit_payload(payload), {"iban": None, "tags": None, "address": {"city": None}})
        with extensions.override_extensions(AUDIT_PAYLOAD="none"):
            self.assertEqual(audit_payload(payload), {})
        with extensions.override_extensions(AUDIT_PAYLOAD="values"):
            with self.assertRaisesRegex(ImproperlyConfigured, "AUDIT_PAYLOAD"):
                extensions.validate_configuration()


class TestCluster06t_AuditPolicy(E2ETestCase):
    """Cluster 6t (end to end): what lands in the audit log and the audit columns."""

    e2e_models = ALL_MODELS + ACTION_MODELS
    e2e_framework_models = [AuditLog, AuditLogStatus]

    # -- 6.124 ---------------------------------------------------------
    def test_6_124_fields_only_payload_on_writes(self) -> None:
        """
        Scenario 6.124: AUDIT_PAYLOAD="fields" keeps values out of the log.
        Given: AUDIT_PAYLOAD="fields"
        When: a record is created and updated through the generic API
        Then: both entries name the fields with None values; no value appears anywhere in them.
        """
        self.use_lex_extensions(AUDIT_PAYLOAD="fields")
        created = self.client.post(self.url_create(AUDIT_SIMPLE), {"name": "secret-name", "value": 7}, format="json")
        self.assertIn(created.status_code, (200, 201), created.content[:300])
        pk = AuditSimpleItem.objects.get(name="secret-name").pk
        self.assertEqual(
            self.client.patch(self.url_detail(AUDIT_SIMPLE, pk), {"name": "other-secret"}, format="json").status_code, 200
        )
        entries = AuditLog.objects.filter(resource="auditsimpleitem")
        self.assertEqual(entries.count(), 2)
        for entry in entries:
            self.assertNotIn("secret", repr(entry.payload))
            self.assertIn("name", entry.payload)
            self.assertIsNone(entry.payload["name"])

    # -- 6.125 ---------------------------------------------------------
    def test_6_125_project_names_the_author(self) -> None:
        """
        Scenario 6.125: AUDIT_ACTOR replaces names and e-mail addresses.
        Given: AUDIT_ACTOR returning "user:<pk>"
        When: a record is created and then updated by the signed-in user
        Then: the audit entries' author and the record's created_by and edited_by are "user:<pk>";
              the user's name or e-mail appears nowhere.
        """
        self.use_lex_extensions(AUDIT_ACTOR=f"{__name__}.pseudonym")
        self.client.post(self.url_create(AUDIT_SIMPLE), {"name": "a", "value": 1}, format="json")
        item = AuditSimpleItem.objects.get(name="a")
        self.client.patch(self.url_detail(AUDIT_SIMPLE, item.pk), {"value": 2}, format="json")
        item.refresh_from_db()
        expected = f"user:{self.user.pk}"
        self.assertEqual((item.created_by, item.edited_by), (expected, expected))
        authors = set(AuditLog.objects.filter(resource="auditsimpleitem").values_list("author", flat=True))
        self.assertEqual(authors, {expected})

    # -- 6.126 ---------------------------------------------------------
    def test_6_126_actions_follow_the_same_rules(self) -> None:
        """
        Scenario 6.126: an action's entry obeys AUDIT_PAYLOAD and AUDIT_ACTOR too.
        Given: both keys set, an AuditActionItem
        When: its close action runs with an input
        Then: the entry keeps the action name and record id, the input's keys without
              values, and the pseudonymous author.
        """
        self.use_lex_extensions(AUDIT_PAYLOAD="fields", AUDIT_ACTOR=f"{__name__}.pseudonym")
        item = AuditActionItem.objects.create(name="file")
        url = reverse(
            "process_admin_rest_api:model-record-action",
            kwargs={"model_container": _MockContainer(AUDIT_ACTION), "calculationId": "c", "pk": item.pk, "action": "close"},
        )
        self.assertEqual(self.client.post(url, {"reason": "private reason"}, format="json").status_code, 200)
        entry = AuditLog.objects.get(resource="auditactionitem")
        self.assertEqual((entry.payload["lex_action"], entry.payload["id"]), ("close", item.pk))
        self.assertEqual(entry.payload["input"], {"reason": None})
        self.assertEqual(entry.author, f"user:{self.user.pk}")

    # -- 6.127 ---------------------------------------------------------
    def test_6_127_initial_data_entries_follow_the_payload_rule(self) -> None:
        """
        Scenario 6.127: rows loaded as initial data are logged by field name only.
        Given: AUDIT_PAYLOAD="fields"
        When: the InitialDataAuditLogger logs a created row
        Then: its entry names the field without its value.
        """
        from lex.audit_logging.utils.InitialDataAuditLogger import InitialDataAuditLogger

        self.use_lex_extensions(AUDIT_PAYLOAD="fields")
        entry = InitialDataAuditLogger().log_object_creation(AuditSimpleItem, {"name": "secret-initial", "value": 3})
        self.assertIsNotNone(entry)
        entry.refresh_from_db()
        self.assertNotIn("secret", repr(entry.payload))
        self.assertIn("name", entry.payload)


class TestCluster06t_CalculationRunAudit(E2ETestCase):
    """Cluster 6t (end to end): a calculation run's entry obeys AUDIT_PAYLOAD."""

    from lex.test_project.tests.calculations.models import ON_CREATE_MODELS as e2e_models  # noqa: N815

    e2e_framework_models = [AuditLog, AuditLogStatus]
    e2e_unpatch = {"store_message", "build_cache_key", "ensure_terminal_calculation_audit"}

    # -- 6.128 ---------------------------------------------------------
    def test_6_128_run_entries_follow_the_payload_rule(self) -> None:
        """
        Scenario 6.128: the run a create starts is logged by field name only.
        Given: AUDIT_PAYLOAD="fields", a model that calculates on create
        When: a record named "secret-run" is created and its run ends
        Then: no audit entry of that record holds the value.
        """
        import time

        from lex.test_project.tests.calculations.models import ON_CREATE, OnCreateCalc

        OnCreateCalc.gate = None
        self.use_lex_extensions(AUDIT_PAYLOAD="fields")
        response = self.client.post(self.url_create(ON_CREATE), data={"name": "secret-run"}, format="json")
        self.assertEqual(response.status_code, 201, response.content[:300])
        prefix = f"{ON_CREATE}_{response.data['id']}_"
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and not AuditLog.objects.filter(calculation_id__startswith=prefix).exists():
            time.sleep(0.05)
        runs = AuditLog.objects.filter(calculation_id__startswith=prefix)
        self.assertTrue(runs.exists(), "the run left no entry")
        for entry in AuditLog.objects.filter(resource=ON_CREATE):
            self.assertNotIn("secret-run", repr(entry.payload))

