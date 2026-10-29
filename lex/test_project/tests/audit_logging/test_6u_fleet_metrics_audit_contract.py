"""Cluster 6u — the audit-column contract fleet-metrics backfill reads.

Intent: the fleet-metrics backfill (a one-off, out-of-repo job on the
Instance Controller) reads ``audit_logging_auditlog`` and
``audit_logging_auditlogstatus`` directly by column name and type to
reconstruct ``calculation_finished`` history for runs that completed before
an instance started sending the live event. That job has no way to notice a
framework refactor that quietly renames, retypes, or drops one of the
columns it reads -- the failure would surface only as silently wrong or
missing fleet data, much later, somewhere else. This batch pins the column
contract so a change here fails *this* suite instead.

Columns pinned:
  * ``audit_logging_auditlog``: id, date, author, resource, payload,
    calculation_id.
  * ``audit_logging_auditlogstatus``: audit_log_id, status, created_at,
    updated_at.

This is a model-metadata contract, not a behavioural one -- no DB access is
needed, so it runs as Type U against ``Model._meta`` directly.

Cluster 6u — scenario 6.168. Type: U.
Covers: lex/audit_logging/models/AuditLog.py, lex/audit_logging/models/AuditLogStatus.py.
Run: python -m lex pytest lex/test_project/tests/audit_logging/test_6u_fleet_metrics_audit_contract.py -v
"""

from __future__ import annotations

import pytest
from django.db import models
from django.test import SimpleTestCase

from lex.audit_logging.models.AuditLog import AuditLog
from lex.audit_logging.models.AuditLogStatus import AuditLogStatus

pytestmark = pytest.mark.audit_logging


class TestCluster06u_FleetMetricsAuditContract(SimpleTestCase):
    """Cluster 6u: the backfill's column contract on AuditLog / AuditLogStatus."""

    def _assert_column(self, model, field_name, db_column, field_types):
        """A field exists on ``model``, is one of ``field_types``, and is
        stored under the DB column name ``db_column`` (what the backfill's
        raw SQL actually selects by)."""
        field = model._meta.get_field(field_name)
        self.assertIsInstance(
            field, field_types,
            f"{model.__name__}.{field_name} must be one of "
            f"{[t.__name__ for t in field_types]}; got {type(field).__name__}. "
            f"The fleet-metrics backfill reads this column by name and type.",
        )
        self.assertEqual(
            field.column, db_column,
            f"{model.__name__}.{field_name} must be stored as column "
            f"{db_column!r}; got {field.column!r}. The backfill selects by "
            f"this exact column name.",
        )

    # -- 6.168 — AuditLog --------------------------------------------------
    def test_6_168_auditlog_backfill_columns_exist_with_their_types(self) -> None:
        """
        Scenario 6.168: AuditLog carries every column the backfill reads.
        Given: the AuditLog model as it ships
        When: each backfill-read field is inspected via Model._meta
        Then: id/date/author/resource/payload/calculation_id all exist, with
              the types and DB column names the backfill's SQL assumes
        """
        self._assert_column(AuditLog, "id", "id", (models.AutoField, models.BigAutoField))
        self._assert_column(AuditLog, "date", "date", (models.DateTimeField,))
        self._assert_column(AuditLog, "author", "author", (models.CharField,))
        self._assert_column(AuditLog, "resource", "resource", (models.CharField,))
        self._assert_column(AuditLog, "payload", "payload", (models.JSONField,))
        self._assert_column(
            AuditLog, "calculation_id", "calculation_id", (models.TextField,)
        )

        date_field = AuditLog._meta.get_field("date")
        self.assertTrue(
            date_field.auto_now_add,
            "AuditLog.date must be auto_now_add — it is the backfill's only "
            "signal for when a root run's audit entry was written.",
        )

    # -- 6.168 — AuditLogStatus ---------------------------------------------
    def test_6_168_auditlogstatus_backfill_columns_exist_with_their_types(self) -> None:
        """
        Scenario 6.168: AuditLogStatus carries every column the backfill reads.
        Given: the AuditLogStatus model as it ships
        When: each backfill-read field is inspected via Model._meta
        Then: audit_log_id/status/created_at/updated_at all exist, with the
              types and DB column names the backfill's SQL assumes — these
              are exactly the two timestamps emit_calculation_finished's
              duration_seconds is computed from
        """
        audit_log_field = AuditLogStatus._meta.get_field("audit_log")
        self.assertIsInstance(
            audit_log_field, models.ForeignKey,
            "AuditLogStatus.audit_log must be a ForeignKey (column "
            f"audit_log_id); got {type(audit_log_field).__name__}.",
        )
        self.assertEqual(
            audit_log_field.column, "audit_log_id",
            f"AuditLogStatus.audit_log must be stored as column "
            f"'audit_log_id'; got {audit_log_field.column!r}.",
        )

        self._assert_column(AuditLogStatus, "status", "status", (models.CharField,))
        self._assert_column(
            AuditLogStatus, "created_at", "created_at", (models.DateTimeField,)
        )
        self._assert_column(
            AuditLogStatus, "updated_at", "updated_at", (models.DateTimeField,)
        )

        created_field = AuditLogStatus._meta.get_field("created_at")
        updated_field = AuditLogStatus._meta.get_field("updated_at")
        self.assertTrue(
            created_field.auto_now_add,
            "AuditLogStatus.created_at must be auto_now_add — "
            "emit_calculation_finished's duration_seconds is "
            "updated_at - created_at.",
        )
        self.assertTrue(
            updated_field.auto_now,
            "AuditLogStatus.updated_at must be auto_now — it is the "
            "terminal-write timestamp duration_seconds is measured to.",
        )

    def test_6_168_auditlogstatus_db_table_name(self) -> None:
        """
        Scenario 6.168: both tables are named the way the backfill's raw SQL
        (and this batch's docstring) assumes.
        Given: the two models' Meta
        When: db_table is inspected
        Then: audit_logging_auditlog / audit_logging_auditlogstatus
        """
        self.assertEqual(AuditLog._meta.db_table, "audit_logging_auditlog")
        self.assertEqual(AuditLogStatus._meta.db_table, "audit_logging_auditlogstatus")
