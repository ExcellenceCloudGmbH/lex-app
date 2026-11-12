"""Who reads the traceback of a failed calculation in the audit log (LEX-1555).

Intent (docs/history-and-audit/audit logs.md, docs/using-the-app/record-detail/
audit log tab.md): a failed operation's audit entry turns ``failure`` and
"the error traceback is stored for investigation". After an API-triggered run
fails, the audit log is the operator's only lasting diagnostic. The HTTP
answer was 202 before the run even started, so the run's failure cannot travel
back in it.

So the traceback follows the entry. Whoever may read the audit entry, which
under ``LEX_SEC_READ_SURFACES`` means whoever may read its record, reads the
traceback too, on ``auditlog`` and on ``auditlogstatus``, in the lists and by
id. Whoever may not read the record sees neither the entry nor the traceback.
The same text already reaches the record's readers through the record's own
``calculation_error_message`` and the ``calculation_error`` broadcast.

The regression this pins: 2.4.0 made the traceback superuser-only. Keycloak
users are never Django superusers, so in a deployed app nobody saw it, and the
audit log's traceback button stayed disabled after every failed run.

Cluster 6u — scenarios 6.168–6.172. Type: E.
Covers: lex/audit_logging/serializers/AuditLogSerializer.py
(``get_error_traceback``), lex/audit_logging/models/AuditLogStatus.py
(``permission_read``), lex/core/security_flags.py (what ``READ_SURFACES``
says it does).
Run: python -m lex pytest lex/test_project/tests/audit_logging/test_6u_calculation_failure_traceback_visibility.py -v
"""

from __future__ import annotations

import time
from datetime import timedelta

import pytest
from django.contrib.auth.models import User
from django.utils import timezone

from lex.audit_logging.models.AuditLog import AuditLog
from lex.audit_logging.models.AuditLogStatus import AuditLogStatus
from lex.audit_logging.serializers.AuditLogSerializer import AuditLogDefaultSerializer
from lex.core.models.CalculationModel import CalculationModel
from lex.test_project.tests._e2e_test_case import E2ETestCase

from .models import (
    AUDIT_OWNED_CALC,
    AUDIT_OWNED_ON_CREATE,
    OWNED_CALC_MODELS,
    AuditOwnedCalc,
    AuditOwnedOnCreateCalc,
)

pytestmark = pytest.mark.audit_logging

_SETTLE_TIMEOUT_S = 10.0
_POLL_S = 0.05
_FAILURE_TEXT = "failing on purpose"


class TestCluster06u_CalculationFailureTracebackVisibility(E2ETestCase):
    """Cluster 6u: a failed run's traceback is read by whoever reads its entry."""

    e2e_models = OWNED_CALC_MODELS
    e2e_framework_models = [AuditLog, AuditLogStatus]
    # Let the real audit and cache writers run so the entries can be observed.
    e2e_unpatch = {
        "store_message",
        "build_cache_key",
        "ensure_terminal_calculation_audit",
    }

    def setUp(self):
        super().setUp()
        AuditLog.objects.all().delete()
        for name in ("owner", "stranger"):
            User.objects.filter(username=name).delete()
        # Plain users, as every Keycloak/OIDC user is: never Django superusers.
        self.owner = User.objects.create_user(username="owner", password="pw")
        self.stranger = User.objects.create_user(username="stranger", password="pw")

    # -- helpers ---------------------------------------------------------

    def login(self, user):
        self.client.force_login(user)
        session = self.client.session
        session["oidc_expires_at"] = (timezone.now() + timedelta(hours=1)).timestamp()
        session.save()

    def rows(self, user, model_name="auditlog", **params):
        self.login(user)
        resp = self.client.get(self.url_list(model_name), {"page": 1, "perPage": 100, **params})
        self.assertEqual(resp.status_code, 200, resp.content[:300])
        body = resp.json()
        return body["results"] if isinstance(body, dict) else body

    def _wait_for_failure_entry(self, resource, pk):
        """The failure entry lands after ``is_calculated`` turns ERROR; poll for it."""
        deadline = time.monotonic() + _SETTLE_TIMEOUT_S
        while time.monotonic() < deadline:
            status_row = (
                AuditLogStatus.objects.filter(
                    audit_log__resource=resource,
                    audit_log__object_id=pk,
                    status="failure",
                )
                .select_related("audit_log")
                .first()
            )
            if status_row is not None:
                return status_row.audit_log, status_row
            time.sleep(_POLL_S)
        self.fail(f"No 'failure' audit entry for {resource} pk={pk} within {_SETTLE_TIMEOUT_S:.0f}s.")

    def fail_a_run_from_the_calculate_button(self):
        """The owner clicks Calculate on a record whose calculate() raises."""
        calc = AuditOwnedCalc.objects.create(name="boom", owner="owner", should_fail=True)
        self.login(self.owner)
        resp = self.client.patch(
            self.url_detail(AUDIT_OWNED_CALC, calc.pk), data={"calculate": "true"}, format="json"
        )
        self.assertIn(resp.status_code, (200, 202), getattr(resp, "content", b"")[:300])
        entry, status_row = self._wait_for_failure_entry(AUDIT_OWNED_CALC, calc.pk)
        calc.refresh_from_db()
        self.assertEqual(calc.is_calculated, CalculationModel.ERROR)
        self.assertIn(
            _FAILURE_TEXT, status_row.error_traceback or "",
            "Precondition: the failed run's traceback is stored on its status row.",
        )
        return calc, entry, status_row

    # -- 6.168 ---------------------------------------------------------
    def test_6_168_record_reader_sees_the_traceback_in_the_audit_log_tab(self) -> None:
        """
        Scenario 6.168: the record's Audit Log tab shows why the run failed.

        Given: a non-superuser who owns a calculation clicks Calculate and
               ``calculate()`` raises.
        When: they open the record's audit log tab
              (``auditlog`` list filtered by ``resource`` and ``object_id``).
        Then: the run's entry reads ``failure`` and carries the traceback.
              Before the fix it came back ``null`` for every non-superuser.
        """
        calc, entry, _ = self.fail_a_run_from_the_calculate_button()

        rows = self.rows(self.owner, resource=AUDIT_OWNED_CALC, object_id=calc.pk)
        row = next((r for r in rows if r["id"] == entry.pk), None)
        self.assertIsNotNone(row, f"The owner must see the run's audit entry; got ids {[r['id'] for r in rows]}.")
        self.assertEqual(row["status"], "failure")
        self.assertIn(
            _FAILURE_TEXT, row.get("error_traceback") or "",
            "Whoever may read a failed run's audit entry must read its traceback (LEX-1555).",
        )

    # -- 6.169 ---------------------------------------------------------
    def test_6_169_status_list_carries_the_traceback(self) -> None:
        """
        Scenario 6.169: the status row of the failed run shows the traceback.

        Given: the owner's failed run from 6.168.
        When: the owner lists ``auditlogstatus``.
        Then: the run's ``failure`` status row includes ``error_traceback``.
              Before the fix the field was stripped by ``permission_read``.
        """
        _, entry, status_row = self.fail_a_run_from_the_calculate_button()

        row = next((r for r in self.rows(self.owner, "auditlogstatus") if r["id"] == status_row.pk), None)
        self.assertIsNotNone(row, "The owner must see the status row of their own run's entry.")
        self.assertEqual(row.get("audit_log"), entry.pk)
        self.assertIn(
            _FAILURE_TEXT, row.get("error_traceback") or "",
            "The status row's traceback follows the row, not superuser status (LEX-1555).",
        )

    # -- 6.170 ---------------------------------------------------------
    def test_6_170_detail_endpoints_carry_the_traceback(self) -> None:
        """
        Scenario 6.170: fetched by id, both rows carry the traceback.

        Given: the owner's failed run.
        When: the owner fetches the entry and its status row by id.
        Then: both answer 200 with the traceback in ``error_traceback``.
        """
        _, entry, status_row = self.fail_a_run_from_the_calculate_button()
        self.login(self.owner)

        resp = self.client.get(self.url_detail("auditlog", entry.pk))
        self.assertEqual(resp.status_code, 200, resp.content[:300])
        self.assertIn(_FAILURE_TEXT, resp.json().get("error_traceback") or "")

        resp = self.client.get(self.url_detail("auditlogstatus", status_row.pk))
        self.assertEqual(resp.status_code, 200, resp.content[:300])
        self.assertIn(_FAILURE_TEXT, resp.json().get("error_traceback") or "")

    # -- 6.171 ---------------------------------------------------------
    def test_6_171_a_caller_who_may_not_read_the_record_sees_no_traceback(self) -> None:
        """
        Scenario 6.171: the traceback never travels beyond the entry's readers.

        Given: the owner's failed run.
        When: a stranger, who may not read the record, lists and fetches
              both the entry and its status row.
        Then: neither row is listed and both ids answer 404, so no
              traceback reaches the stranger by any of the four reads.
        """
        _, entry, status_row = self.fail_a_run_from_the_calculate_button()

        self.assertNotIn(entry.pk, {r["id"] for r in self.rows(self.stranger)})
        stranger_status = self.rows(self.stranger, "auditlogstatus")
        self.assertNotIn(status_row.pk, {r["id"] for r in stranger_status})
        self.assertFalse(
            any(_FAILURE_TEXT in (r.get("error_traceback") or "") for r in stranger_status),
            "No status row the stranger can list may carry the owner's traceback.",
        )

        self.login(self.stranger)
        self.assertEqual(self.client.get(self.url_detail("auditlog", entry.pk)).status_code, 404)
        self.assertEqual(self.client.get(self.url_detail("auditlogstatus", status_row.pk)).status_code, 404)

    # -- 6.172 ---------------------------------------------------------
    def test_6_172_a_run_started_by_its_create_shows_the_traceback_too(self) -> None:
        """
        Scenario 6.172: the other trigger route, the same reading rule.

        Given: a model with ``calculate_on_create``; the owner creates a
               record through the API and its run fails in the background.
        When: the owner lists the audit log for the record.
        Then: the run's ``failure`` entry carries the traceback, as for a
              run started from the Calculate button. The serializer asked
              directly as the owner agrees.
        """
        self.login(self.owner)
        resp = self.client.post(
            self.url_create(AUDIT_OWNED_ON_CREATE),
            data={"name": "boom", "owner": "owner", "should_fail": True},
            format="json",
        )
        self.assertEqual(resp.status_code, 201, getattr(resp, "content", b"")[:300])
        record = AuditOwnedOnCreateCalc.objects.get(name="boom")
        entry, _ = self._wait_for_failure_entry(AUDIT_OWNED_ON_CREATE, record.pk)

        row = next((r for r in self.rows(self.owner, resource=AUDIT_OWNED_ON_CREATE, object_id=record.pk)
                    if r["id"] == entry.pk), None)
        self.assertIsNotNone(row, "The owner must see the run's entry.")
        self.assertIn(_FAILURE_TEXT, row.get("error_traceback") or "")

        request = type("Req", (), {"user": self.owner})()
        serialized = AuditLogDefaultSerializer(entry, context={"request": request})
        self.assertIn(_FAILURE_TEXT, serialized.get_error_traceback(entry) or "")
