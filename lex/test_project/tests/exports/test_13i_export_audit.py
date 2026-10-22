"""
Cluster 13i: ``POST /api/<model>/export`` writes an audit entry.

Intent (from docs/history-and-audit/audit logs.md):

    An export hands model data out of the system, so it belongs in the
    same trail as create / update / delete: one ``AuditLog`` row naming
    the actor, the model, what shaped the export and how much data left,
    with an ``AuditLogStatus`` that ends ``success`` or ``failure``.

``post`` reaches the file through four different machines — the universal
streaming writer, the flat DB-only streaming writer, the pandas AG-Grid
fallback and the legacy non-AG path. They share no code between the
request and the response, so each is audited and asserted separately; a
green universal-stream test says nothing about the other three.

Scenarios 13.46 – 13.51.
"""

from __future__ import annotations

import base64
from decimal import Decimal
from urllib.parse import urlencode
from unittest.mock import patch

from django.contrib.contenttypes.models import ContentType
from lex.audit_logging.models.AuditLog import AuditLog
from lex.audit_logging.models.AuditLogStatus import AuditLogStatus
from lex.test_project.tests._e2e_test_case import E2ETestCase

from .models import ALL_MODELS, FAST, ITEM, ExportCategory, ExportItem, FastExportItem
from .test_13a_legacy_export import _assert_msg
from .test_13e_streaming_fast_export import _ag_flat_payload

import pytest

pytestmark = pytest.mark.exports


def _drain(resp):
    """Consume a streaming export so the response is fully served."""
    if getattr(resp, "streaming", False):
        b"".join(resp.streaming_content)
    return resp


class _ExportAuditTestBase(E2ETestCase):
    """Export tests that read back the audit row the request produced."""

    e2e_models = ALL_MODELS
    e2e_framework_models = [AuditLog, AuditLogStatus]

    def setUp(self):
        super().setUp()
        AuditLog.objects.all().delete()

    def assert_single_export_audit(self, resource, *, status):
        """The one export entry for ``resource``, with its status checked."""
        rows = AuditLog.objects.filter(resource=resource, action="export")
        self.assertEqual(
            rows.count(), 1,
            f"One export request must produce exactly one AuditLog row with "
            f"action='export'; got {rows.count()}.",
        )
        row = rows.first()
        self.assertTrue(
            row.author,
            f"AuditLog.author must name the caller; got {row.author!r}.",
        )
        status_rows = AuditLogStatus.objects.filter(audit_log=row)
        self.assertEqual(
            status_rows.count(), 1,
            "An export entry must carry exactly one AuditLogStatus.",
        )
        self.assertEqual(
            status_rows.first().status, status,
            f"Export audit status must be {status!r}; "
            f"got {status_rows.first().status!r}.",
        )
        return row


class TestCluster13i_StreamingExportAudit(_ExportAuditTestBase):
    """The two streaming writers — the production hot paths."""

    def setUp(self):
        super().setUp()
        # FastExportItem relies on the framework's default
        # ``permission_export``, which needs the Keycloak ``export`` scope;
        # the session-authenticated test user carries none.
        scope_patch = patch(
            "lex.core.models.LexModel.UserContext._resolve_keycloak_scopes",
            return_value={"export", "read", "list", "edit"},
        )
        scope_patch.start()
        self.addCleanup(scope_patch.stop)

    # -- 13.46 ---------------------------------------------------------
    def test_13_46_universal_stream_export_is_audited(self) -> None:
        """
        Scenario 13.46: AG-Grid export via the universal streaming writer.

        Given: three rows a caller may export.
        When: the AG-Grid export endpoint streams them as xlsx.
        Then: one ``AuditLog`` row records action ``export``, the model as
              ``resource`` and content type, the request's shape, and how
              much data left — the row and column counts actually written,
              and the size of the generated file.
        """
        category = ExportCategory.objects.create(name="alpha")
        for name in ("row-a", "row-b", "row-c"):
            FastExportItem.objects.create(
                name=name, amount=Decimal("10.00"), count=1, category=category,
            )

        payload = _ag_flat_payload([
            {"dataKey": "name", "headerName": "Name"},
            {"dataKey": "amount", "headerName": "Amount"},
        ])
        resp = _drain(self.client.post(self.url_export(FAST), data=payload, format="json"))
        self.assertEqual(resp.status_code, 200, _assert_msg(resp))

        row = self.assert_single_export_audit("fastexportitem", status="success")
        self.assertEqual(
            row.content_type, ContentType.objects.get_for_model(FastExportItem),
            "An export entry must be attributable to the model it read.",
        )
        self.assertIsNone(
            row.object_id,
            "A model-wide export touches no single record, so object_id "
            f"must stay unset; got {row.object_id!r}.",
        )
        self.assertEqual(row.payload.get("export_path"), "universal-stream")
        self.assertEqual(row.payload.get("mode"), "ag_grid")
        self.assertEqual(row.payload.get("format"), "xlsx")
        self.assertEqual(
            row.payload.get("rows"), 3,
            "The audit must record how many rows left the system; "
            f"got {row.payload.get('rows')!r} for a 3-row export.",
        )
        self.assertEqual(row.payload.get("columns"), 2)
        self.assertGreater(
            row.payload.get("size_bytes") or 0, 0,
            "The audit must record the size of the generated file.",
        )

    # -- 13.47 ---------------------------------------------------------
    def test_13_47_flat_stream_export_is_audited(self) -> None:
        """
        Scenario 13.47: the flat DB-only streaming writer is audited too.

        Given: the universal writer declines this export.
        When: ``post`` falls through to the flat streaming writer.
        Then: the audit row names that path and still carries the row,
              column and size facts — covering a writer the universal
              path's test never reaches.
        """
        category = ExportCategory.objects.create(name="alpha")
        FastExportItem.objects.create(
            name="flat-a", amount=Decimal("10.00"), count=1, category=category,
        )
        FastExportItem.objects.create(
            name="flat-b", amount=Decimal("20.00"), count=2, category=category,
        )

        payload = _ag_flat_payload([
            {"dataKey": "name", "headerName": "Name"},
            {"dataKey": "amount", "headerName": "Amount"},
            {"dataKey": "category", "headerName": "Category"},
        ])
        with patch(
            "lex.api.views.file_operations.ModelExport."
            "ModelExportView._try_stream_universal_fast_export",
            return_value=None,
        ):
            resp = _drain(
                self.client.post(self.url_export(FAST), data=payload, format="json")
            )
        self.assertEqual(resp.status_code, 200, _assert_msg(resp))

        row = self.assert_single_export_audit("fastexportitem", status="success")
        self.assertEqual(row.payload.get("export_path"), "flat-stream")
        self.assertEqual(row.payload.get("rows"), 2)
        self.assertGreater(row.payload.get("size_bytes") or 0, 0)

    # -- 13.48 ---------------------------------------------------------
    def test_13_48_empty_streaming_export_is_audited_as_failure(self) -> None:
        """
        Scenario 13.48: an export that returns no file is a failed attempt.

        Given: no rows to export.
        When: the endpoint answers 404 instead of a file.
        Then: the audit row still exists — a refused export is as
              interesting to an audit as a served one — with its status
              ``failure`` and the refusing status code recorded.
        """
        payload = _ag_flat_payload([{"dataKey": "name", "headerName": "Name"}])
        resp = self.client.post(self.url_export(FAST), data=payload, format="json")
        self.assertEqual(resp.status_code, 404)

        row = self.assert_single_export_audit("fastexportitem", status="failure")
        self.assertEqual(row.payload.get("status_code"), 404)


class TestCluster13i_DataFrameExportAudit(_ExportAuditTestBase):
    """The two pandas writers — ``ExportItem`` overrides ``permission_export``,
    which is what makes ``post`` decline both streaming paths."""

    # -- 13.49 ---------------------------------------------------------
    def test_13_49_legacy_export_is_audited(self) -> None:
        """
        Scenario 13.49: the legacy non-AG export path.

        Given: two rows and a request carrying no AG-Grid payload.
        When: the legacy path builds and returns the workbook.
        Then: the audit row names that path, reports ``mode='legacy'``, and
              records the rows and the worksheet the data was written to.
        """
        ExportItem.objects.create(name="n1", amount="10.00")
        ExportItem.objects.create(name="n2", amount="20.00")

        resp = _drain(self.client.post(self.url_export(ITEM), data={}, format="json"))
        self.assertEqual(resp.status_code, 200, _assert_msg(resp))

        row = self.assert_single_export_audit("exportitem", status="success")
        self.assertEqual(row.payload.get("export_path"), "legacy")
        self.assertEqual(row.payload.get("mode"), "legacy")
        self.assertEqual(row.payload.get("rows"), 2)
        self.assertEqual(row.payload.get("sheet_name"), "ExportItem")

    # -- 13.50 ---------------------------------------------------------
    def test_13_50_ag_pandas_fallback_export_is_audited(self) -> None:
        """
        Scenario 13.50: the AG-Grid pandas fallback.

        Given: a model whose per-row export permissions rule out both
               streaming writers.
        When: an AG-Grid export runs through the pandas fallback.
        Then: the audit row names that path and reports the AG-Grid
              request's shape, including the filter the caller applied.
        """
        ExportItem.objects.create(name="keep-1", amount="10.00")
        ExportItem.objects.create(name="keep-2", amount="20.00")

        payload = _ag_flat_payload([
            {"dataKey": "name", "headerName": "Name"},
            {"dataKey": "amount", "headerName": "Amount"},
        ])
        payload["ag_export"]["request"]["filterModel"] = {
            "name": {"filterType": "text", "type": "contains", "filter": "keep"}
        }

        resp = _drain(self.client.post(self.url_export(ITEM), data=payload, format="json"))
        self.assertEqual(resp.status_code, 200, _assert_msg(resp))

        row = self.assert_single_export_audit("exportitem", status="success")
        self.assertEqual(row.payload.get("export_path"), "pandas")
        self.assertEqual(row.payload.get("mode"), "ag_grid")
        self.assertEqual(row.payload.get("rows"), 2)
        self.assertEqual(
            row.payload.get("filter_model"),
            {"name": {"filterType": "text", "type": "contains", "filter": "keep"}},
            "The audit must record the filter that decided which rows left "
            "the system, not just that an export happened.",
        )

    # -- 13.51 ---------------------------------------------------------
    def test_13_51_selected_rows_are_audited_as_a_count(self) -> None:
        """
        Scenario 13.51: a hand-picked selection is recorded as a count.

        Given: an export of two explicitly selected rows.
        When: the export runs.
        Then: the audit records the selection's size and that the request
              was a selection, not the identifiers themselves — a
              ten-thousand-row selection must stay one bounded audit row.
        """
        first = ExportItem.objects.create(name="pick-1", amount="10.00")
        second = ExportItem.objects.create(name="pick-2", amount="20.00")
        ExportItem.objects.create(name="skip-3", amount="30.00")

        encoded = base64.b64encode(
            urlencode([("ids", first.pk), ("ids", second.pk)]).encode("utf-8"),
        ).decode("ascii")

        resp = _drain(
            self.client.post(
                self.url_export(ITEM),
                data={"filtered_export": encoded},
                format="json",
            )
        )
        self.assertEqual(resp.status_code, 200, _assert_msg(resp))

        row = self.assert_single_export_audit("exportitem", status="success")
        self.assertEqual(row.payload.get("mode"), "filtered")
        self.assertEqual(
            row.payload.get("selected_row_count"), 2,
            "A selection export must record the size of the selection.",
        )
        self.assertEqual(
            row.payload.get("rows"), 2,
            "Only the selected rows left the system, so only those may be "
            f"counted; got {row.payload.get('rows')!r}.",
        )
        self.assertNotIn(
            "filtered_export", row.payload,
            "The audit must not copy the selection itself into the payload.",
        )
