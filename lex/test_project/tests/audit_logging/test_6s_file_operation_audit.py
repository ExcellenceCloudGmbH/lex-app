"""
Cluster 6s: the audit mechanism behind exports and downloads.

Intent (from docs/history-and-audit/audit logs.md):

    Export and download join create / update / delete as recorded
    actions. They share the trail's contract: the entry is written
    **before** the work, with status ``pending``, and only then moved to
    ``success`` or ``failure`` — which is what makes a crashed operation
    distinguishable from one that never started. The author names the
    real caller, including one authenticating with an API key.

Clusters 10p, 13i and 15l assert the individual endpoints. This file
pins the shared mechanism those endpoints rely on, through the
``file-download`` endpoint as its representative surface.

Scenarios 6.121 – 6.124.
"""

from __future__ import annotations

from unittest.mock import patch

from django.core.files.base import ContentFile
from lex.audit_logging.models.AuditLog import AuditLog
from lex.audit_logging.models.AuditLogStatus import AuditLogStatus
from lex.test_project.tests._e2e_test_case import _MockContainer, E2ETestCase

from .models import AUDIT_FILE, FILE_MODELS, AuditFileItem

import pytest

pytestmark = pytest.mark.audit_logging


class TestCluster06s_FileOperationAudit(E2ETestCase):
    """The export / download half of the audit trail's contract."""

    e2e_models = FILE_MODELS
    e2e_framework_models = [AuditLog, AuditLogStatus]

    def setUp(self):
        super().setUp()
        AuditLog.objects.all().delete()
        self.document = AuditFileItem.objects.create(name="statement")
        self.document.attachment.save("statement.txt", ContentFile(b"bytes"))
        self.addCleanup(self.document.attachment.delete, save=False)
        self.url = self._url("file-download", model_container=_MockContainer(AUDIT_FILE))

    def download(self):
        return self.client.get(
            self.url, {"pk": self.document.pk, "field": "attachment"},
        )

    # -- 6.121 ---------------------------------------------------------
    def test_6_121_export_and_download_are_recordable_actions(self) -> None:
        """
        Scenario 6.121: the trail admits ``export`` and ``download``.

        Given: the ``AuditLog`` action vocabulary.
        When: entries are written for an export and a download.
        Then: both actions are offered as choices and round-trip through
              the table and ``to_dict`` — without this the two operations
              could only be recorded as one of create / update / delete,
              which would misdescribe them.
        """
        offered = dict(AuditLog.ACTION_CHOICES)
        self.assertIn(
            "export", offered,
            f"'export' must be a recordable action; got {sorted(offered)}.",
        )
        self.assertIn(
            "download", offered,
            f"'download' must be a recordable action; got {sorted(offered)}.",
        )

        for action in ("export", "download"):
            entry = AuditLog.objects.create(
                author="e2e", resource="auditfileitem", action=action, payload={"k": "v"},
            )
            entry.refresh_from_db()
            self.assertEqual(entry.action, action)
            self.assertEqual(
                entry.to_dict()["action"], action,
                "The frontend-facing form of the entry must carry the "
                "action it was recorded with.",
            )

    # -- 6.122 ---------------------------------------------------------
    def test_6_122_api_key_download_names_the_technical_user(self) -> None:
        """
        Scenario 6.122: an API-key download names its caller.

        Given: a caller authenticating with an API key rather than a session.
        When: it downloads a file.
        Then: the entry's author is the technical user, matching how
              ``LexModel`` stamps the same caller — ``AnonymousUser``
              would name nobody and make the trail useless for exactly
              the integrations most worth auditing.
        """
        self.authenticate_as_api_key(name="Technical User")

        response = self.download()
        self.assertEqual(response.status_code, 200)
        if getattr(response, "streaming", False):
            b"".join(response.streaming_content)

        entry = AuditLog.objects.filter(action="download").first()
        self.assertIsNotNone(entry, "An API-key download must still be audited.")
        self.assertEqual(
            entry.author, "Technical User",
            f"An API-key caller must be named in the trail; got {entry.author!r}.",
        )

    # -- 6.123 ---------------------------------------------------------
    def test_6_123_entry_is_pending_while_the_operation_runs(self) -> None:
        """
        Scenario 6.123: the entry is opened before the work, not after it.

        Given: a download in progress.
        When: the status is read from inside the view, before the file is
              served.
        Then: the entry already exists and is ``pending`` — the property
              that makes an operation which crashed outright
              distinguishable from one that never started.
        """
        observed = {}

        def spy(view, request, instance, field_name):
            entry = AuditLog.objects.filter(action="download").first()
            observed["entry"] = entry
            observed["status"] = (
                AuditLogStatus.objects.filter(audit_log=entry).first().status
                if entry else None
            )
            return True

        # The spy only observes: it reads the trail mid-request and lets
        # the real download continue.
        with patch(
            "lex.api.views.file_operations.FileDownload.FileDownloadView._may_read_file",
            new=spy,
        ):
            response = self.download()
        self.assertEqual(response.status_code, 200)
        if getattr(response, "streaming", False):
            b"".join(response.streaming_content)

        self.assertIsNotNone(
            observed.get("entry"),
            "The audit entry must exist before the file is served, not "
            "only after the response is built.",
        )
        self.assertEqual(
            observed.get("status"), "pending",
            "A download in flight must read as 'pending'; got "
            f"{observed.get('status')!r}.",
        )

        entry = AuditLog.objects.filter(action="download").first()
        self.assertEqual(
            AuditLogStatus.objects.filter(audit_log=entry).first().status, "success",
            "The same entry must be finalised once the file is served.",
        )

    # -- 6.124 ---------------------------------------------------------
    def test_6_124_unhandled_failure_is_recorded_with_its_traceback(self) -> None:
        """
        Scenario 6.124: a download that crashes outright is still recorded.

        Given: a download whose handler raises an error the API layer has
               no answer for.
        When: the error escapes the view.
        Then: the entry is already ``failure`` and carries the traceback —
              the same guarantee failed creates and updates give, and the
              only record that the attempt happened at all.
        """
        with patch(
            "lex.api.views.file_operations.FileDownload.FileDownloadView._may_read_file",
            side_effect=RuntimeError("storage exploded"),
        ):
            with self.assertRaises(RuntimeError):
                self.download()

        entry = AuditLog.objects.filter(action="download").first()
        self.assertIsNotNone(
            entry, "A crashed download must leave an audit entry behind.",
        )
        status = AuditLogStatus.objects.filter(audit_log=entry).first()
        self.assertEqual(
            status.status, "failure",
            f"A crashed download must be recorded as a failure; got "
            f"{status.status!r}.",
        )
        self.assertIn(
            "storage exploded", status.error_traceback or "",
            "The failure's traceback must be preserved on the entry.",
        )
