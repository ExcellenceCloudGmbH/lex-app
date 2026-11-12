"""
Cluster 15l: ``GET api/download-pdf/<pk>/`` writes an audit entry.

Intent (from docs/history-and-audit/audit logs.md):

    Downloading a calculation log as PDF takes data out of the system, so
    it belongs in the same trail as create / update / delete: one
    ``AuditLog`` row with action ``download``, naming the actor and the
    log, saying whether the whole subtree was rendered, and ending
    ``success`` or ``failure``.

The endpoint answers two different questions — this log, or this log and
everything under it — and can refuse in two ways (the log is not the
caller's to see; the PDF cannot be rendered). All four are audited.

Scenarios 15.52 – 15.55.
"""

from __future__ import annotations

from unittest.mock import patch

from django.contrib.contenttypes.models import ContentType
from lex.audit_logging.models.AuditLog import AuditLog
from lex.audit_logging.models.AuditLogStatus import AuditLogStatus
from lex.audit_logging.models.CalculationLog import CalculationLog

from . import _CalcLogTestCase

import pytest

pytestmark = pytest.mark.calculation_logging


class TestCluster15l_PdfDownloadAudit(_CalcLogTestCase):
    """The calculation-log PDF endpoint records what it handed out."""

    def setUp(self):
        super().setUp()
        # These logs belong to no record, so only an operator may read
        # them (LEX_SEC_INTERNAL); scenario 15.55 drops the flag again.
        self.user.is_superuser = True
        self.user.save(update_fields=["is_superuser"])

    def pdf_url(self, pk):
        return self._url("download-markdown-pdf", pk=pk)

    def assert_single_pdf_audit(self, *, status):
        rows = AuditLog.objects.filter(resource="calculationlog", action="download")
        self.assertEqual(
            rows.count(), 1,
            f"One PDF download must produce exactly one AuditLog row with "
            f"action='download'; got {rows.count()}.",
        )
        row = rows.first()
        self.assertTrue(
            row.author,
            f"AuditLog.author must name the caller; got {row.author!r}.",
        )
        status_rows = AuditLogStatus.objects.filter(audit_log=row)
        self.assertEqual(
            status_rows.first().status, status,
            f"PDF download audit status must be {status!r}; "
            f"got {status_rows.first().status!r}.",
        )
        return row

    # -- 15.52 ---------------------------------------------------------
    def test_15_52_single_log_pdf_download_is_audited(self) -> None:
        """
        Scenario 15.52: downloading one log's PDF is recorded against it.

        Given: a calculation log the caller may read.
        When: its PDF is downloaded.
        Then: an audit row with action ``download`` names the log through
              its generic relation, records the PDF's name and size, and
              says the whole subtree was not included.
        """
        log = CalculationLog.objects.create(calculation_log="# Title\n\nBody.")

        response = self.client.get(self.pdf_url(log.pk))
        self.assertEqual(response.status_code, 200)

        row = self.assert_single_pdf_audit(status="success")
        self.assertEqual(
            row.content_type, ContentType.objects.get_for_model(CalculationLog),
        )
        self.assertEqual(
            row.object_id, log.pk,
            "The PDF download must be attributable to the log it rendered; "
            f"got object_id={row.object_id!r}.",
        )
        self.assertEqual(row.payload.get("format"), "pdf")
        self.assertIs(row.payload.get("include_descendants"), False)
        self.assertEqual(row.payload.get("filename"), f"document_{log.pk}.pdf")
        self.assertGreater(
            row.payload.get("size_bytes") or 0, 0,
            "The audit must record how large the generated PDF was.",
        )

    # -- 15.53 ---------------------------------------------------------
    def test_15_53_subtree_pdf_download_records_its_scope(self) -> None:
        """
        Scenario 15.53: a subtree download records how much it pulled in.

        Given: a log with two children.
        When: the PDF is downloaded with ``?include_descendants=true``.
        Then: the audit says the subtree was included and how many
              sections it rendered — "one log" and "a log and everything
              under it" are different amounts of data leaving the system,
              and the trail must tell them apart.
        """
        root = CalculationLog.objects.create(calculation_log="# Root")
        CalculationLog.objects.create(
            calculation_log="Child A body.", parent_log=root, heading="Child A",
        )
        CalculationLog.objects.create(
            calculation_log="Child B body.", parent_log=root, heading="Child B",
        )

        response = self.client.get(self.pdf_url(root.pk), {"include_descendants": "true"})
        self.assertEqual(response.status_code, 200)

        row = self.assert_single_pdf_audit(status="success")
        self.assertEqual(row.object_id, root.pk)
        self.assertIs(row.payload.get("include_descendants"), True)
        self.assertEqual(row.payload.get("filename"), f"document_{root.pk}_full.pdf")
        self.assertEqual(
            row.payload.get("section_count"), 3,
            "A subtree download must record how many sections were "
            f"rendered; got {row.payload.get('section_count')!r} for a root "
            "with two children.",
        )
        self.assertIs(row.payload.get("truncated"), False)

    # -- 15.54 ---------------------------------------------------------
    def test_15_54_failed_rendering_is_audited_as_a_failure(self) -> None:
        """
        Scenario 15.54: a download that produced no PDF is a failed attempt.

        Given: a log whose PDF rendering fails.
        When: the endpoint answers 500 instead of a document.
        Then: the audit row exists with status ``failure`` and the status
              code — the trail must not imply the caller received a file.
        """
        log = CalculationLog.objects.create(calculation_log="# Title")

        with patch(
            "lex.api.views.calculations.DownloadMarkdownPdf.render_pdf_bytes",
            side_effect=RuntimeError("renderer down"),
        ):
            response = self.client.get(self.pdf_url(log.pk))
        self.assertEqual(response.status_code, 500)

        row = self.assert_single_pdf_audit(status="failure")
        self.assertEqual(row.payload.get("status_code"), 500)
        self.assertNotIn(
            "size_bytes", row.payload,
            "Nothing was served, so no delivered size may be recorded.",
        )

    # -- 15.55 ---------------------------------------------------------
    def test_15_55_refused_download_is_audited_with_its_traceback(self) -> None:
        """
        Scenario 15.55: a log the caller may not see is a recorded refusal.

        Given: a non-operator caller and a log belonging to no record.
        When: the endpoint refuses with 404.
        Then: the attempt is audited as a failure carrying the refusal's
              traceback, and no log is attached — the entry records that
              someone asked, not that they received anything.
        """
        self.user.is_superuser = False
        self.user.save(update_fields=["is_superuser"])
        log = CalculationLog.objects.create(calculation_log="# Secret")

        response = self.client.get(self.pdf_url(log.pk))
        self.assertEqual(response.status_code, 404)

        row = self.assert_single_pdf_audit(status="failure")
        self.assertIsNone(
            row.object_id,
            "Nothing was served, so no log may be attached to the entry.",
        )
        self.assertTrue(
            AuditLogStatus.objects.filter(audit_log=row).first().error_traceback,
            "A refusal raised inside the view must keep its traceback on "
            "the audit entry, as failed creates and updates do.",
        )
