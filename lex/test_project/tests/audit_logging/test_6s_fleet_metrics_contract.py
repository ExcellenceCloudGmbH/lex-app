"""The audit-table contract the lex-analytics collector reads.

Intent: fleet metrics (LEX-749) counts writes, writers (K1) and calculation runs (K3)
from each instance's audit tables. The SQL that does it lives in the lex-analytics
collector, not here. These tests pin the columns and the behaviour that SQL depends
on, so a lex-app change that would silently break the collector fails lex-app's own
suite first:

* the columns exist, and the timestamps are ``timestamptz``;
* only the existence of the author column was pinned here before; 6.126 now checks
  whether an API key's audit entries actually carry its name (they do not -- BUG-034
  -- so K1's name-match exclusion of API-key writers does not fire yet);
* a finished calculation run leaves audit rows that carry its terminal ``is_calculated``
  in the payload and share the request's calculation id: one run, however many rows;
* a plain edit of a calculation row is audited with ``is_calculated = NOT_CALCULATED``, so
  it is never mistaken for a run;
* a create that starts its own run (``calculate_on_create``) groups, under the
  collector's K3 rule, to exactly one finished run — the create's own entry never
  counts as a second one.

Cluster 6s — scenarios 6.121-6.126. Type: I and E.
Covers: lex/audit_logging/models/AuditLog.py, lex/audit_logging/models/AuditLogStatus.py,
lex/audit_logging/utils/calculation_audit.py, lex/api/views/model_entries/One.py.
Run: python -m lex pytest lex/test_project/tests/audit_logging/test_6s_fleet_metrics_contract.py -v
"""

from __future__ import annotations

import time

from django.db import connection
from django.test import TestCase

from lex.audit_logging.models.AuditLog import AuditLog
from lex.audit_logging.models.AuditLogStatus import AuditLogStatus
from lex.test_project.tests._e2e_test_case import E2ETestCase

from .models import ALL_MODELS, AUDIT_ATOMIC, AUDIT_SIMPLE, AuditAtomicCalc
from ..calculations.models import ON_CREATE, ON_CREATE_MODELS, OnCreateCalc

import pytest

pytestmark = pytest.mark.audit_logging

TERMINAL_STATES = ("SUCCESS", "ERROR", "ABORTED", "CANCELLED")


#: Calculation ids that name no run (the lex-analytics collector's list).
PLACEHOLDER_CALCULATION_IDS = (None, "", "test_id", "default", "undefined", "null")


def _group_key(audit_log: AuditLog) -> str:
    """The collector's run key for one audit row: ``resource:calculation_id``.

    A ``calculation_id`` that names no run means the row is its own run, keyed by its
    own id instead: NULL, empty, the model field's ``'test_id'`` default, and the
    placeholders a client sends when it has no run id (``'default'``, ``'undefined'``,
    ``'null'``). That is the rule the lex-analytics collector's instance-database
    reads apply.
    """
    calc_id = audit_log.calculation_id
    tail = calc_id if calc_id not in PLACEHOLDER_CALCULATION_IDS else f"row-{audit_log.id}"
    return f"{audit_log.resource}:{tail}"


def _collector_runs(pairs) -> dict:
    """Python mirror of the collector's K3 grouping rule.

    ``pairs`` is an iterable of ``(AuditLog, AuditLogStatus)`` rows — the SQL's
    ``audit_logging_auditlog a JOIN audit_logging_auditlogstatus s ON s.audit_log_id =
    a.id``. Groups by :func:`_group_key`; a group's ``outcome`` is the last terminal
    ``is_calculated`` by audit-log id (``None`` if the group never carries one, i.e. the
    run has not finished); ``started_at``/``finished_at`` bracket the group the same way
    the collector's SQL does (``min(created_at)`` / ``max(updated_at)``).

    Returns every group seen, finished or not, so a test can assert both that one run
    finished and that another specific group did not.
    """
    groups: dict[str, list[tuple[int, object, object, object]]] = {}
    for audit_log, status in pairs:
        key = _group_key(audit_log)
        state = (audit_log.payload or {}).get("is_calculated")
        groups.setdefault(key, []).append(
            (audit_log.id, state, status.created_at, status.updated_at)
        )

    runs = {}
    for key, entries in groups.items():
        terminal = sorted(
            ((audit_id, state) for audit_id, state, _, _ in entries if state in TERMINAL_STATES),
            key=lambda pair: pair[0],
            reverse=True,
        )
        runs[key] = {
            "outcome": terminal[0][1] if terminal else None,
            "started_at": min(created for _, _, created, _ in entries),
            "finished_at": max(updated for _, _, _, updated in entries),
        }
    return runs


class TestCluster06s_AuditColumns(TestCase):
    """Cluster 6s: the columns the collector's SQL names."""

    CONTRACT = {
        "audit_logging_auditlog": {
            "id", "date", "author", "resource", "action", "payload", "calculation_id", "object_id",
        },
        "audit_logging_auditlogstatus": {"id", "audit_log_id", "status", "created_at", "updated_at"},
        # K1 leaves API-key authors out of the writers by matching their name.
        "rest_framework_api_key_apikey": {"name"},
    }
    TIMESTAMPTZ = {
        ("audit_logging_auditlog", "date"),
        ("audit_logging_auditlogstatus", "created_at"),
        ("audit_logging_auditlogstatus", "updated_at"),
    }

    # -- 6.121 ---------------------------------------------------------
    def test_6_121_the_columns_the_collector_reads_exist(self) -> None:
        """
        Scenario 6.121: the audit tables keep the shape fleet metrics reads.
        Given: a migrated database.
        When: the catalog is read.
        Then: every column the collector names exists, and the three timestamps are
              ``timestamp with time zone`` (the collector compares them to UTC bounds).
        """
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT table_name, column_name, data_type FROM information_schema.columns "
                "WHERE table_name = ANY(%s)",
                [list(self.CONTRACT)],
            )
            rows = cursor.fetchall()
        present = {(table, column) for table, column, _ in rows}
        types = {(table, column): data_type for table, column, data_type in rows}
        for table, columns in self.CONTRACT.items():
            missing = {c for c in columns if (table, c) not in present}
            self.assertEqual(
                missing, set(),
                f"{table} lost columns the lex-analytics collector's fleet-metrics SQL reads: {sorted(missing)}",
            )
        for key in self.TIMESTAMPTZ:
            self.assertEqual(types.get(key), "timestamp with time zone", f"{key} must stay timestamptz")


class TestCluster06s_CalculationRunContract(E2ETestCase):
    """Cluster 6s: what a calculation run and a plain edit leave in the audit."""

    e2e_models = ALL_MODELS
    e2e_framework_models = [AuditLog, AuditLogStatus]
    e2e_unpatch = {"store_message", "build_cache_key", "ensure_terminal_calculation_audit"}

    SETTLE_TIMEOUT_S = 10.0
    POLL_S = 0.05

    @staticmethod
    def _run_id(calc) -> str:
        return f"{AUDIT_ATOMIC}_{calc.pk}_update_6s"

    def _terminal_rows(self, calc) -> list:
        rows = AuditLog.objects.filter(resource=AUDIT_ATOMIC, object_id=calc.pk).order_by("id")
        return [row for row in rows if (row.payload or {}).get("is_calculated") in TERMINAL_STATES]

    def _run_to_the_end(self, calc) -> list:
        response = self.client.patch(
            self.url_detail(AUDIT_ATOMIC, calc.pk, calc_id=self._run_id(calc)),
            data={"calculate": "true"}, format="json",
        )
        self.assertLess(response.status_code, 300, f"Trigger failed: {response.status_code}")
        deadline = time.monotonic() + self.SETTLE_TIMEOUT_S
        while time.monotonic() < deadline:
            rows = self._terminal_rows(calc)
            finished = AuditLogStatus.objects.filter(
                audit_log__in=rows, status__in=("success", "failure"),
            ).exists()
            if rows and finished:
                return rows
            time.sleep(self.POLL_S)
        self.fail(f"No terminal audit row for {AUDIT_ATOMIC} pk={calc.pk} within {self.SETTLE_TIMEOUT_S}s")

    def _assert_one_run(self, calc, rows, expected_state: str) -> None:
        self.assertEqual(
            {row.calculation_id for row in rows}, {self._run_id(calc)},
            "Every terminal audit row of one run must carry the request's calculation id; "
            "the collector counts runs by it (collector plan, K3).",
        )
        self.assertEqual(rows[-1].payload["is_calculated"], expected_state)
        statuses = AuditLogStatus.objects.filter(audit_log__calculation_id=self._run_id(calc))
        self.assertTrue(statuses.exists(), "A run must leave status rows to time it by")
        for status_row in statuses:
            self.assertGreaterEqual(status_row.updated_at, status_row.created_at)

    # -- 6.122 ---------------------------------------------------------
    def test_6_122_a_successful_run_is_one_run_ending_in_success(self) -> None:
        """
        Scenario 6.122: a clean calculation is countable as one successful run.
        Given: an atomic calculation that succeeds.
        When: it is triggered through the API with a calculation id, as the frontend does.
        Then: its terminal audit rows all carry that id, the last one says SUCCESS, and
              its status rows bracket the run.
        """
        calc = AuditAtomicCalc.objects.create(name="ok", should_fail=False)
        self._assert_one_run(calc, self._run_to_the_end(calc), "SUCCESS")

    # -- 6.123 ---------------------------------------------------------
    def test_6_123_a_failed_run_is_one_run_ending_in_error(self) -> None:
        """
        Scenario 6.123: a failing calculation is countable as one failed run.
        Given: an atomic calculation whose calculate() raises.
        When: it is triggered through the API.
        Then: its terminal audit rows carry the id and the last one says ERROR.
        """
        calc = AuditAtomicCalc.objects.create(name="boom", should_fail=True)
        self._assert_one_run(calc, self._run_to_the_end(calc), "ERROR")

    # -- 6.124 ---------------------------------------------------------
    def test_6_124_a_plain_edit_of_a_calculation_row_is_not_a_run(self) -> None:
        """
        Scenario 6.124: editing a calculation row is an edit, not a calculation.
        Given: a calculation row.
        When: a person PATCHes its name (no ``calculate``).
        Then: its audit row says ``is_calculated = NOT_CALCULATED``, so the collector's
              run rule cannot count it.
        """
        calc = AuditAtomicCalc.objects.create(name="before")
        edit_id = f"{AUDIT_ATOMIC}_{calc.pk}_update_6s_edit"
        response = self.client.patch(
            self.url_detail(AUDIT_ATOMIC, calc.pk, calc_id=edit_id),
            data={"name": "after"}, format="json",
        )
        self.assertLess(response.status_code, 300, f"Edit failed: {response.status_code}")

        rows = list(AuditLog.objects.filter(calculation_id=edit_id))
        self.assertTrue(rows, "The edit must be audited")
        self.assertEqual(
            {(row.payload or {}).get("is_calculated") for row in rows}, {"NOT_CALCULATED"},
            "A plain edit must not look like a finished run to the collector",
        )

    # -- 6.126 ---------------------------------------------------------
    @pytest.mark.xfail(
        strict=True,
        reason="BUG-034: AuditLog.author reads request.user, which stays AnonymousUser "
        "for an API-key-only call; it never carries the key's name.",
    )
    def test_6_126_an_api_key_s_audit_author_is_its_own_name(self) -> None:
        """
        Scenario 6.126: an API key's writes should be its own, not a person's.
        Given: a caller authenticated with an API key named "Nightly import".
        When: it creates a record through the REST API.
        Then: the resulting AuditLog.author should equal the key's name -- how K1 would
              leave API-key writers out of the people it counts (spec §4.3). It does
              not today: see BUG-034.
        """
        self.authenticate_as_api_key(name="Nightly import")
        response = self.client.post(
            self.url_create(AUDIT_SIMPLE), data={"name": "created-by-key", "value": 1}, format="json",
        )
        self.assertEqual(response.status_code, 201, f"Create failed: {response.status_code}")

        entry = AuditLog.objects.get(resource=AUDIT_SIMPLE, action="create", object_id=response.data["id"])
        self.assertEqual(
            entry.author, "Nightly import",
            "K1 excludes API-key writers by matching this field against the key's name.",
        )


class TestCluster06s_CreateTriggeredRunIsOneRun(E2ETestCase):
    """Cluster 6s: a create-triggered run groups to exactly one finished run (K3)."""

    e2e_models = ON_CREATE_MODELS
    e2e_framework_models = [AuditLog, AuditLogStatus]
    # Let the real audit and terminal-audit writers run so the two entries —
    # the create's and the run's — can be observed, as test_6r does.
    e2e_unpatch = {"store_message", "build_cache_key", "ensure_terminal_calculation_audit"}

    SETTLE_TIMEOUT_S = 10.0
    POLL_S = 0.05

    def setUp(self):
        super().setUp()
        OnCreateCalc.gate = None
        OnCreateCalc.calls = 0
        AuditLog.objects.all().delete()

    def _create(self, **data):
        resp = self.client.post(self.url_create(ON_CREATE), data=data, format="json")
        self.assertEqual(
            resp.status_code, 201,
            f"The create must answer 201; got {resp.status_code} body={getattr(resp, 'data', None)!r}",
        )
        return OnCreateCalc.objects.get(pk=resp.data["id"])

    def _settle(self, record):
        """Poll until the run's own terminal audit row has landed (test_6r's approach).

        The create's entry is action='create'; the run the create started reports on
        its own, separate 'update' entry (``_calculate_after_create`` /
        ``AuditLogMixin.log_calculation``). Wait for that entry's payload to carry a
        terminal ``is_calculated`` and its status to settle to success/failure, the way
        an operator would see it, then return the refreshed record.
        """
        deadline = time.monotonic() + self.SETTLE_TIMEOUT_S
        while time.monotonic() < deadline:
            run_rows = AuditLog.objects.filter(resource=ON_CREATE, object_id=record.pk, action="update")
            for row in run_rows:
                if (row.payload or {}).get("is_calculated") in TERMINAL_STATES:
                    if AuditLogStatus.objects.filter(audit_log=row, status__in=("success", "failure")).exists():
                        record.refresh_from_db()
                        return record
            time.sleep(self.POLL_S)
        self.fail(f"No terminal run audit for {ON_CREATE} pk={record.pk} within {self.SETTLE_TIMEOUT_S}s")

    # -- 6.125 ---------------------------------------------------------
    def test_6_125_a_create_triggered_run_is_exactly_one_finished_run(self) -> None:
        """
        Scenario 6.125: a create-triggered run is countable as one finished run.
        Given: a model with ``calculate_on_create = True``.
        When: a record is created through the REST API, starting its own run the way
              ``test_6r_create_triggered_run_audit`` documents (a second, separate
              'update' entry under its own calculation id).
        Then: the collector's K3 grouping rule — group audit rows by
              (resource, calculation id), a run is finished when one of its rows
              carries a terminal ``is_calculated``, its outcome is the last such
              value — finds exactly one finished run, whose outcome matches the
              record's own final state; the create's entry forms its own group
              and that group never finishes.
        """
        record = self._create(name="create-triggered-6s")
        record = self._settle(record)

        pairs = [
            (audit_log, status)
            for audit_log in AuditLog.objects.filter(resource=ON_CREATE, object_id=record.pk)
            for status in AuditLogStatus.objects.filter(audit_log=audit_log)
        ]
        runs = _collector_runs(pairs)
        finished = {key: run for key, run in runs.items() if run["outcome"] is not None}

        self.assertEqual(
            len(finished), 1,
            f"The collector's K3 rule must see exactly one finished run; saw {finished!r} "
            f"(all groups: {runs!r}).",
        )
        ((_, the_run),) = finished.items()
        self.assertEqual(
            the_run["outcome"], record.is_calculated,
            "The run's outcome, by the collector's grouping rule, must match the record's own final state.",
        )

        create_entry = AuditLog.objects.get(resource=ON_CREATE, action="create", object_id=record.pk)
        create_key = _group_key(create_entry)
        self.assertIn(
            create_key, runs,
            "Sanity check on the key function: the create's own row must form a group too.",
        )
        self.assertIsNone(
            runs[create_key]["outcome"],
            "The create's own entry must never look like a finished run to the collector — "
            "its payload carries no terminal is_calculated.",
        )
        self.assertNotIn(
            create_key, finished,
            "The create's own entry must not contribute a second finished run.",
        )
