"""
Cluster 10p: the file-download endpoints write an audit entry.

Intent (from docs/history-and-audit/audit logs.md):

    A download takes a stored file out of the system, so it belongs in the
    same trail as create / update / delete: one ``AuditLog`` row with
    action ``download``, naming the actor, the record and field it served,
    and how the file reached the caller — streamed bytes, a signed URL, a
    SharePoint share link or a preview link. The entry points at the
    record, so the download shows up on that record's audit tab.

Four endpoints serve a file, each through its own machine, and the
``file-download`` endpoint branches again per ``STORAGE_TYPE``:

* ``api/<model>/file-download`` — local filesystem and GCS
* ``api/<model>/sharepoint-file-download``
* ``api/<model>/sharepoint-file-share-link``
* ``api/<model>/sharepoint-file-preview-link``

SharePoint's SDK is mocked at the project boundary; everything between
the HTTP request and the audit row is the real code.

Scenarios 10.91 – 10.96.
"""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

from django.contrib.contenttypes.models import ContentType
from django.core.files.base import ContentFile
from lex.audit_logging.models.AuditLog import AuditLog
from lex.audit_logging.models.AuditLogStatus import AuditLogStatus
from lex.test_project.tests._e2e_test_case import _MockContainer, E2ETestCase

from .models import DOWNLOAD_MODELS, DOWNLOADABLE, DownloadableDocument

import pytest

pytestmark = pytest.mark.api_layer

FILE_BYTES = b"audited bytes"


class _DownloadAuditCase(E2ETestCase):
    """A record with a real attachment, and helpers to read the audit row."""

    e2e_models = DOWNLOAD_MODELS
    e2e_framework_models = [AuditLog, AuditLogStatus]

    def setUp(self):
        super().setUp()
        AuditLog.objects.all().delete()
        self.document = DownloadableDocument.objects.create(name="invoice")
        self.document.attachment.save("invoice.txt", ContentFile(FILE_BYTES))
        self.addCleanup(self.document.attachment.delete, save=False)

    def download_url(self, view_name="file-download"):
        return self._url(view_name, model_container=_MockContainer(DOWNLOADABLE))

    def assert_single_download_audit(self, *, status):
        rows = AuditLog.objects.filter(resource="downloadabledocument", action="download")
        self.assertEqual(
            rows.count(), 1,
            f"One download request must produce exactly one AuditLog row with "
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
            f"Download audit status must be {status!r}; "
            f"got {status_rows.first().status!r}.",
        )
        return row


class TestCluster10p_FileDownloadAudit(_DownloadAuditCase):
    """``api/<model>/file-download`` — one entry per storage branch."""

    # -- 10.91 ---------------------------------------------------------
    def test_10_91_local_download_is_audited_against_the_record(self) -> None:
        """
        Scenario 10.91: a served file is recorded against its own record.

        Given: a record with an attachment on local storage.
        When: the caller downloads that field.
        Then: one ``AuditLog`` row with action ``download`` names the field,
              the file and its size, says the bytes were streamed, and
              points its generic relation at the record — which is what
              puts the download on that record's audit tab.
        """
        response = self.client.get(
            self.download_url(), {"pk": self.document.pk, "field": "attachment"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(b"".join(response.streaming_content), FILE_BYTES)

        row = self.assert_single_download_audit(status="success")
        self.assertEqual(
            row.content_type, ContentType.objects.get_for_model(DownloadableDocument),
        )
        self.assertEqual(
            row.object_id, self.document.pk,
            "A download must be attributable to the record whose file was "
            f"served; got object_id={row.object_id!r}.",
        )
        self.assertEqual(row.payload.get("field"), "attachment")
        self.assertEqual(row.payload.get("storage"), "LOCAL")
        self.assertEqual(row.payload.get("delivery"), "stream")
        self.assertEqual(
            row.payload.get("size_bytes"), len(FILE_BYTES),
            "The audit must record how many bytes left the system.",
        )
        self.assertIn("invoice", row.payload.get("filename") or "")

    # -- 10.92 ---------------------------------------------------------
    def test_10_92_refused_download_is_audited_as_a_failed_attempt(self) -> None:
        """
        Scenario 10.92: a refused download is still recorded.

        Given: a request naming a record that does not exist.
        When: the endpoint answers 404 instead of a file.
        Then: an audit row exists with status ``failure`` and the refusing
              status code — an attempt to take data out is worth recording
              whether or not it succeeded — and no record is attached,
              because none was served.
        """
        response = self.client.get(
            self.download_url(), {"pk": self.document.pk + 10_000, "field": "attachment"},
        )
        self.assertEqual(response.status_code, 404)

        row = self.assert_single_download_audit(status="failure")
        self.assertEqual(row.payload.get("status_code"), 404)
        self.assertIsNone(
            row.object_id,
            "No file was served, so no record may be attached to the entry.",
        )

    # -- 10.93 ---------------------------------------------------------
    def test_10_93_gcs_download_records_that_a_url_was_handed_out(self) -> None:
        """
        Scenario 10.93: on GCS the caller receives a URL, not the bytes.

        Given: ``STORAGE_TYPE=GCS``.
        When: the same endpoint answers with a download URL.
        Then: the audit says the file was delivered as a URL, so the trail
              distinguishes "bytes were streamed" from "access was handed
              out" instead of recording both as the same event.
        """
        with patch.dict(os.environ, {"STORAGE_TYPE": "GCS"}, clear=False):
            response = self.client.get(
                self.download_url(), {"pk": self.document.pk, "field": "attachment"},
            )
        self.assertEqual(response.status_code, 200)
        self.assertIn("download_url", response.json())

        row = self.assert_single_download_audit(status="success")
        self.assertEqual(row.payload.get("storage"), "GCS")
        self.assertEqual(row.payload.get("delivery"), "url")
        self.assertEqual(row.object_id, self.document.pk)


class TestCluster10p_SharePointDownloadAudit(_DownloadAuditCase):
    """The three SharePoint endpoints — stream, share link, preview link."""

    # -- 10.94 ---------------------------------------------------------
    def test_10_94_sharepoint_download_is_audited(self) -> None:
        """
        Scenario 10.94: the SharePoint download endpoint is audited too.

        Given: a record with an attachment.
        When: the SharePoint download endpoint streams it.
        Then: an audit row records a streamed SharePoint download against
              that record — this endpoint shares no code with
              ``file-download``, so its own coverage is the only proof.
        """
        response = self.client.get(
            self.download_url("sharepoint-file-download"),
            {"pk": self.document.pk, "field": "attachment"},
        )
        self.assertEqual(response.status_code, 200)

        row = self.assert_single_download_audit(status="success")
        self.assertEqual(row.object_id, self.document.pk)
        self.assertEqual(row.payload.get("storage"), "SHAREPOINT")
        self.assertEqual(row.payload.get("delivery"), "stream")
        self.assertIn("invoice", row.payload.get("filename") or "")

    def _patch_sharepoint(self, module):
        """Stub SharePoint's SDK at the project boundary."""
        sp_file = MagicMock()
        sp_file.unique_id = "sp-unique-id"
        context = MagicMock()
        context.ctx.web.get_file_by_server_relative_path.return_value.get.return_value \
            .execute_query.return_value = sp_file

        for target, new in (
            (f"{module}.SharePointContext", MagicMock(return_value=context)),
            (f"{module}.get_server_relative_path", MagicMock(return_value="/sites/x/invoice.txt")),
        ):
            patcher = patch(target, new)
            patcher.start()
            self.addCleanup(patcher.stop)

    # -- 10.95 ---------------------------------------------------------
    def test_10_95_share_link_is_audited_as_a_download(self) -> None:
        """
        Scenario 10.95: handing out a share link is a data-egress event.

        Given: a record with an attachment.
        When: the caller asks for a SharePoint share link.
        Then: an audit row records it as a ``download`` delivered as a
              share link — no bytes crossed the wire, but the file left the
              system's control just the same.
        """
        self._patch_sharepoint("lex.api.views.sharepoint.SharePointShareLink")

        response = self.client.get(
            self.download_url("sharepoint-file-share-link"),
            {"pk": self.document.pk, "field": "attachment"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("share_link", response.json())

        row = self.assert_single_download_audit(status="success")
        self.assertEqual(row.object_id, self.document.pk)
        self.assertEqual(row.payload.get("delivery"), "share_link")
        self.assertEqual(row.payload.get("sharepoint_unique_id"), "sp-unique-id")

    # -- 10.96 ---------------------------------------------------------
    def test_10_96_preview_link_is_audited_as_a_download(self) -> None:
        """
        Scenario 10.96: a preview link exposes the file's contents.

        Given: a record with an attachment.
        When: the caller asks for a SharePoint preview link.
        Then: an audit row records it as a ``download`` delivered as a
              preview link, distinguishable from the share link so the
              trail says which form of access was granted.
        """
        self._patch_sharepoint("lex.api.views.sharepoint.SharePointPreview")

        response = self.client.get(
            self.download_url("sharepoint-file-preview-link"),
            {"pk": self.document.pk, "field": "attachment"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("preview_link", response.json())

        row = self.assert_single_download_audit(status="success")
        self.assertEqual(row.object_id, self.document.pk)
        self.assertEqual(row.payload.get("delivery"), "preview_link")
