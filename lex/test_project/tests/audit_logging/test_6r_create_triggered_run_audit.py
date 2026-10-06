"""The audit trail of a run started by a create.

Intent: when creating a record also starts its calculation, the audit trail
must tell the two apart. The create is one entry, and it succeeded the moment
the record existed. The run is a second entry with its own calculation id,
recorded exactly like a run started from the Calculate button, and it carries
the run's outcome. A regression that put the run's outcome on the create's
entry would tell an auditor a record was never created when it was — or hide
a failed run behind a successful create.
Cluster 6r — scenarios 6.119–6.120. Type: E.
Covers: lex/api/views/model_entries/One.py (``OneModelEntry.create``),
lex/audit_logging/mixins/AuditLogMixin.py (the run's audit entry).
Run: python -m lex pytest lex/test_project/tests/audit_logging/test_6r_create_triggered_run_audit.py -v
"""

from __future__ import annotations

import time

import pytest
from rest_framework import status

from lex.audit_logging.models.AuditLog import AuditLog
from lex.audit_logging.models.AuditLogStatus import AuditLogStatus
from lex.core.models.CalculationModel import CalculationModel
from lex.test_project.tests._e2e_test_case import E2ETestCase

from ..calculations.models import ON_CREATE, ON_CREATE_MODELS, OnCreateCalc

pytestmark = pytest.mark.audit_logging

_SETTLE_TIMEOUT_S = 10.0
_POLL_S = 0.05


class TestCluster06r_CreateTriggeredRunAudit(E2ETestCase):
    """Cluster 6r: a create that starts a run leaves two audit entries, not one."""

    e2e_models = ON_CREATE_MODELS
    e2e_framework_models = [AuditLog, AuditLogStatus]
    # Let the real audit and cache writers run so the entries can be observed.
    e2e_unpatch = {
        "store_message",
        "build_cache_key",
        "ensure_terminal_calculation_audit",
    }

    def setUp(self):
        super().setUp()
        OnCreateCalc.gate = None
        OnCreateCalc.calls = 0
        AuditLog.objects.all().delete()

    def _create(self, **data):
        resp = self.client.post(self.url_create(ON_CREATE), data=data, format="json")
        self.assertEqual(
            resp.status_code,
            status.HTTP_201_CREATED,
            f"The create must answer 201; got {resp.status_code} body={getattr(resp, 'data', None)!r}",
        )
        return OnCreateCalc.objects.get(pk=resp.data["id"])

    def _status_of(self, audit_log):
        return AuditLogStatus.objects.filter(audit_log=audit_log).values_list("status", flat=True).first()

    def _run_entry(self, record, expected_status):
        """Wait for the run's own entry to carry ``expected_status``, then return it.

        The run records its outcome after the record's state is saved, so the
        test waits for the audit row the way an operator would see it appear.
        """
        prefix = f"{ON_CREATE}_{record.pk}_"
        deadline = time.monotonic() + _SETTLE_TIMEOUT_S
        entry = None
        while time.monotonic() < deadline:
            entry = (
                AuditLog.objects.filter(calculation_id__startswith=prefix)
                .order_by("-id")
                .first()
            )
            if entry is not None and self._status_of(entry) == expected_status:
                return entry
            time.sleep(_POLL_S)
        self.fail(
            f"No audit entry with a calculation id starting {prefix!r} reached "
            f"{expected_status!r} within {_SETTLE_TIMEOUT_S:.0f}s; last entry: "
            f"{entry!r} with status {self._status_of(entry) if entry else None!r}."
        )

    def _create_entry(self, record):
        entries = list(AuditLog.objects.filter(resource=ON_CREATE, action="create", object_id=record.pk))
        self.assertEqual(
            len(entries), 1,
            f"The create must leave exactly one 'create' entry; got {entries!r}.",
        )
        return entries[0]

    # -- 6.119 ---------------------------------------------------------
    def test_6_119_the_run_gets_its_own_audit_entry(self):
        """
        Scenario 6.119: create and run are recorded separately
        Given: a model with ``calculate_on_create = True``
        When: a record is created through the REST API and its run succeeds
        Then: the trail holds the create's entry, finalised to success, and a
              separate 'update' entry for the run, under a calculation id of
              the form ``<model>_<pk>_…``, also finalised to success
        """
        record = self._create(name="audited")

        run_entry = self._run_entry(record, "success")
        create_entry = self._create_entry(record)

        self.assertEqual(self._status_of(create_entry), "success", "The create's entry must be 'success'.")
        self.assertNotEqual(
            run_entry.pk, create_entry.pk,
            "The run must have its own entry, not reuse the create's.",
        )
        self.assertEqual(run_entry.action, "update", "A run is recorded as an update, like a Calculate click.")
        self.assertEqual(run_entry.object_id, record.pk, "The run's entry must point at the record it calculated.")
        self.assertNotEqual(
            run_entry.calculation_id, create_entry.calculation_id,
            "The run must carry its own calculation id.",
        )

    # -- 6.120 ---------------------------------------------------------
    def test_6_120_a_failed_run_does_not_fail_the_create(self):
        """
        Scenario 6.120: the run's failure stays on the run
        Given: a model with ``calculate_on_create = True`` whose calculate() raises
        When: a record is created through the REST API
        Then: the run's entry is 'failure' with the traceback an operator needs,
              and the create's entry stays 'success', because the record was
              created
        """
        record = self._create(name="audited failure", should_fail=True)

        run_entry = self._run_entry(record, "failure")
        record.refresh_from_db()
        self.assertEqual(record.is_calculated, CalculationModel.ERROR, "The record must be left in ERROR.")

        traceback_text = (
            AuditLogStatus.objects.filter(audit_log=run_entry).values_list("error_traceback", flat=True).first()
        )
        self.assertTrue(traceback_text, "The failed run's entry must carry a traceback.")

        create_entry = self._create_entry(record)
        self.assertEqual(
            self._status_of(create_entry), "success",
            "A failed run must not turn the create's entry into a failure.",
        )
