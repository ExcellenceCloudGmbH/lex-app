"""The grid opens the log of a run that a create started.

Intent: a run started by creating a record must be as reachable as one started
from the Calculate button. The grid finds a row's newest run by the id prefix
``<model>_<pk>_``; a run filed under the create request's own id
(``<model>_create_<uuid>``, which has no pk) would leave the row's log button
dead. A regression here means the record calculated, but nobody can read what
the run logged from the row that started it.
Cluster 15k — scenario 15.51. Type: E.
Covers: lex/api/views/model_entries/One.py (``OneModelEntry.create``),
lex/audit_logging/utils/latest_calculation.py (as read by the list view).
Run: python -m lex pytest lex/test_project/tests/calculation_logging/test_15k_create_triggered_run_log.py -v
"""

from __future__ import annotations

import time

import pytest
from rest_framework import status

from lex.audit_logging.handlers.LexLogger import LexLogger
from lex.audit_logging.models.AuditLog import AuditLog
from lex.audit_logging.models.AuditLogStatus import AuditLogStatus
from lex.audit_logging.models.CalculationLog import CalculationLog
from lex.core.models.CalculationModel import CalculationModel
from lex.test_project.tests._e2e_test_case import E2ETestCase

from .models import LogOnCreateCalc

pytestmark = pytest.mark.calculation_logging

_MODEL = "logoncreatecalc"
_SETTLE_TIMEOUT_S = 10.0
_POLL_S = 0.05


def _ag_request():
    return {
        "startRow": 0,
        "endRow": 100,
        "rowGroupCols": [],
        "groupKeys": [],
        "pivotCols": [],
        "pivotMode": False,
        "valueCols": [],
        "sortModel": [],
        "filterModel": {},
    }


class TestCluster15k_CreateTriggeredRunLog(E2ETestCase):
    """Cluster 15k: the row of a newly created record names the run its create started."""

    e2e_models = [LogOnCreateCalc]
    e2e_framework_models = [CalculationLog, AuditLog, AuditLogStatus]
    e2e_unpatch = {"ensure_terminal_calculation_audit"}

    def setUp(self):
        super().setUp()
        CalculationLog.objects.all().delete()
        AuditLog.objects.all().delete()

    def tearDown(self):
        # LexLogger is a process-wide singleton; never leak a half-built entry.
        LexLogger().content = []
        super().tearDown()

    # -- 15.51 ---------------------------------------------------------
    def test_15_51_the_row_opens_the_run_its_create_started(self):
        """
        Scenario 15.51: the log button works on a record that calculated itself
        Given: a model with ``calculate_on_create = True`` whose run logs a line
        When: a record is created through the REST API and its run finishes
        Then: the grid's row for it names that run (``lex_reserved_calculation_id``
              starts with ``<model>_<pk>_``), says it has a log, and the run's
              log rows are stored under that id
        """
        resp = self.client.post(self.url_create(_MODEL), data={"name": "logged"}, format="json")
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED, getattr(resp, "data", None))
        record = LogOnCreateCalc.objects.get(pk=resp.data["id"])

        deadline = time.monotonic() + _SETTLE_TIMEOUT_S
        while time.monotonic() < deadline:
            record.refresh_from_db()
            if record.is_calculated == CalculationModel.SUCCESS:
                break
            time.sleep(_POLL_S)
        self.assertEqual(record.is_calculated, CalculationModel.SUCCESS, "The run must finish in SUCCESS.")

        listing = self.client.post(self.url_list(_MODEL), data={"request": _ag_request()}, format="json")
        self.assertEqual(listing.status_code, status.HTTP_200_OK, getattr(listing, "data", None))
        row = {r["id"]: r for r in listing.data["rowData"]}[record.pk]

        run_id = row["lex_reserved_calculation_id"]
        self.assertIsNotNone(run_id, "The row must name the run its create started.")
        self.assertTrue(
            run_id.startswith(f"{_MODEL}_{record.pk}_"),
            f"The run's id must start with '{_MODEL}_{record.pk}_' so the grid can find it; got {run_id!r}.",
        )
        self.assertIs(row["lex_reserved_has_calculation_log"], True, "The row must say a log exists.")
        self.assertTrue(
            CalculationLog.objects.filter(calculationId=run_id).exists(),
            "The run's log rows must be stored under the id the row names.",
        )
