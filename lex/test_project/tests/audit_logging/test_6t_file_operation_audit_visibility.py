"""
Cluster 6t: who reads the export and download entries of the trail.

Intent (from docs/history-and-audit/audit logs.md, "Who sees an entry"):

    An export or download payload describes the request — file name,
    size, rows, filter — not field values of the record. So the entry is
    not judged by a record rebuilt from its payload (that record is
    blank, and a blank record is readable by nobody or by everybody,
    depending on the model). Instead:

    * a download is readable by whoever may read the record it names;
    * a model-wide export is readable by whoever may export the model;
    * a superuser reads every entry;
    * the payload comes back whole to whoever may read the entry;
    * an ``AuditLogStatus`` row is as visible as its entry, and its
      traceback is for superusers only.

All of it sits behind ``LEX_SEC_READ_SURFACES`` (on by default).

Scenarios 6.160 – 6.167.
"""

from __future__ import annotations

import os
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.files.base import ContentFile
from django.utils import timezone
from lex.audit_logging.models.AuditLog import AuditLog
from lex.audit_logging.models.AuditLogStatus import AuditLogStatus
from lex.test_project.tests._e2e_test_case import _MockContainer, E2ETestCase

from .models import AUDIT_GUARDED, GUARDED_FILE_MODELS, AuditGuardedFile

import pytest

pytestmark = pytest.mark.audit_logging


def _drain(resp):
    if getattr(resp, "streaming", False):
        b"".join(resp.streaming_content)
    return resp


class TestCluster06t_FileOperationAuditVisibility(E2ETestCase):
    """The read side of the export / download entries."""

    e2e_models = GUARDED_FILE_MODELS
    e2e_framework_models = [AuditLog, AuditLogStatus]

    def setUp(self):
        super().setUp()
        AuditLog.objects.all().delete()
        for name in ("owner", "stranger", "exporter", "root"):
            User.objects.filter(username=name).delete()
        self.owner = User.objects.create_user(username="owner", password="pw")
        self.stranger = User.objects.create_user(username="stranger", password="pw")
        self.exporter = User.objects.create_user(username="exporter", password="pw")
        self.root = User.objects.create_superuser(username="root", password="pw", email="root@test.local")

        self.document = AuditGuardedFile.objects.create(name="statement", owner="owner")
        self.document.attachment.save("statement.txt", ContentFile(b"bytes"))
        self.addCleanup(self.document.attachment.delete, save=False)
        AuditGuardedFile.objects.create(name="exported row", owner="exporter")

        self.download_url = self._url("file-download", model_container=_MockContainer(AUDIT_GUARDED))

    # -- helpers ---------------------------------------------------------

    def login(self, user):
        self.client.force_login(user)
        session = self.client.session
        session["oidc_expires_at"] = (timezone.now() + timedelta(hours=1)).timestamp()
        session.save()

    def download_as_owner(self):
        self.login(self.owner)
        resp = _drain(self.client.get(self.download_url, {"pk": self.document.pk, "field": "attachment"}))
        self.assertEqual(resp.status_code, 200, getattr(resp, "content", b"")[:300])
        return AuditLog.objects.get(action="download")

    def export_as_exporter(self):
        self.login(self.exporter)
        _drain(self.client.post(self.url_export(AUDIT_GUARDED), data={}, format="json"))
        return AuditLog.objects.get(action="export")

    def rows(self, user, model_name="auditlog", **params):
        self.login(user)
        resp = self.client.get(self.url_list(model_name), {"page": 1, "perPage": 100, **params})
        self.assertEqual(resp.status_code, 200, resp.content[:300])
        body = resp.json()
        return body["results"] if isinstance(body, dict) else body

    def entry_ids(self, user, **params):
        return {row["id"] for row in self.rows(user, **params)}

    # -- 6.160 ---------------------------------------------------------
    def test_6_160_download_entry_follows_the_record(self) -> None:
        """
        Scenario 6.160: a download entry is as visible as its record.

        Given: a download of a file only its owner may read.
        When: the audit log is listed as the owner, as a stranger and as
              a superuser.
        Then: the owner and the superuser see the entry; the stranger
              does not. Before, the entry was judged by a blank record
              rebuilt from the payload and hidden from everyone.
        """
        entry = self.download_as_owner()
        self.assertEqual(entry.object_id, self.document.pk)

        self.assertIn(entry.pk, self.entry_ids(self.owner), "A reader of the record must see its download entry.")
        self.assertNotIn(
            entry.pk, self.entry_ids(self.stranger),
            "A caller who may not read the record must not see who downloaded it.",
        )
        self.assertIn(entry.pk, self.entry_ids(self.root), "A superuser reads every entry.")

    # -- 6.161 ---------------------------------------------------------
    def test_6_161_export_entry_follows_the_model_export_right(self) -> None:
        """
        Scenario 6.161: a model-wide export entry is visible to exporters.

        Given: an export of the model (no single record, ``object_id`` unset).
        When: the audit log is listed as another exporter-capable caller,
              as the record owner (who may read rows but not export) and
              as a superuser.
        Then: the exporter and the superuser see it; the owner does not.
        """
        entry = self.export_as_exporter()
        self.assertIsNone(entry.object_id)

        self.assertIn(entry.pk, self.entry_ids(self.exporter), "Who may export the model sees its exports.")
        self.assertNotIn(
            entry.pk, self.entry_ids(self.owner),
            "Reading one record is not the right to see the model's export trail.",
        )
        self.assertIn(entry.pk, self.entry_ids(self.root))

    # -- 6.162 ---------------------------------------------------------
    def test_6_162_record_filter_finds_the_download(self) -> None:
        """
        Scenario 6.162: a record's download history is one query.

        Given: a download of the document.
        When: the owner lists ``resource=<model>&object_id=<pk>&action=download``.
        Then: exactly that entry comes back — the request a record's
              audit tab makes.
        """
        entry = self.download_as_owner()
        rows = self.rows(self.owner, resource=AUDIT_GUARDED, object_id=self.document.pk, action="download")
        self.assertEqual([row["id"] for row in rows], [entry.pk])

    # -- 6.163 ---------------------------------------------------------
    def test_6_163_file_operation_payload_is_returned_whole(self) -> None:
        """
        Scenario 6.163: the reader of an entry reads its payload.

        Given: a download and an export entry.
        When: they are listed by a caller who may read them.
        Then: the payload carries the file name, size, rows and columns.
              Before, it was pruned to the target model's readable fields —
              none of which occur in a file-operation payload — and came
              back ``{}`` to everyone.
        """
        download = self.download_as_owner()
        export = self.export_as_exporter()

        row = next(r for r in self.rows(self.owner) if r["id"] == download.pk)
        self.assertEqual(row["payload"].get("filename"), download.payload.get("filename"))
        self.assertEqual(row["payload"].get("size_bytes"), 5)
        self.assertEqual(str(row["payload"].get("pk")), str(self.document.pk))

        row = next(r for r in self.rows(self.exporter) if r["id"] == export.pk)
        self.assertEqual(row["payload"].get("mode"), "legacy")
        self.assertEqual(row["payload"].get("rows"), export.payload.get("rows"))
        self.assertIn("columns", row["payload"])

    # -- 6.164 ---------------------------------------------------------
    def test_6_164_status_rows_follow_their_entry(self) -> None:
        """
        Scenario 6.164: ``auditlogstatus/list`` is as narrow as ``auditlog/list``.

        Given: a download entry and its status row.
        When: the status list is read as the owner and as a stranger.
        Then: the owner sees the status row, the stranger sees none. Before,
              every authenticated caller saw every status row on the
              platform, with ``"<action> on <resource> by <name>"``.
        """
        entry = self.download_as_owner()
        status_pk = AuditLogStatus.objects.get(audit_log=entry).pk

        self.assertIn(status_pk, {r["id"] for r in self.rows(self.owner, "auditlogstatus")})
        self.assertNotIn(
            status_pk, {r["id"] for r in self.rows(self.stranger, "auditlogstatus")},
            "A status row must not outlive the visibility of its entry.",
        )
        self.assertIn(status_pk, {r["id"] for r in self.rows(self.root, "auditlogstatus")})

    # -- 6.165 ---------------------------------------------------------
    def test_6_165_traceback_is_for_superusers(self) -> None:
        """
        Scenario 6.165: a failure's traceback is server internals.

        Given: a download that crashed, leaving a ``failure`` entry with a
               traceback.
        When: the owner (who may read the entry) and a superuser list both
              ``auditlog`` and ``auditlogstatus``.
        Then: the owner sees the entry and its ``failure`` status but no
              traceback; the superuser sees the traceback.
        """
        self.login(self.owner)
        with patch(
            "lex.api.views.file_operations.FileDownload.FileDownloadView._may_read_file",
            side_effect=RuntimeError("storage exploded"),
        ):
            with self.assertRaises(RuntimeError):
                self.client.get(self.download_url, {"pk": self.document.pk, "field": "attachment"})
        entry = AuditLog.objects.get(action="download")

        owner_row = next(r for r in self.rows(self.owner) if r["id"] == entry.pk)
        self.assertEqual(owner_row["status"], "failure")
        self.assertFalse(owner_row.get("error_traceback"), "Only a superuser reads a traceback.")
        owner_status = next(r for r in self.rows(self.owner, "auditlogstatus") if r["audit_log"] == entry.pk)
        self.assertFalse(owner_status.get("error_traceback"), "Only a superuser reads a traceback.")

        root_row = next(r for r in self.rows(self.root) if r["id"] == entry.pk)
        self.assertIn("storage exploded", root_row.get("error_traceback") or "")
        root_status = next(r for r in self.rows(self.root, "auditlogstatus") if r["audit_log"] == entry.pk)
        self.assertIn("storage exploded", root_status.get("error_traceback") or "")

    # -- 6.166 ---------------------------------------------------------
    def test_6_166_legacy_switch_keeps_the_payload_check(self) -> None:
        """
        Scenario 6.166: ``LEX_SEC_READ_SURFACES=false`` restores the old rule.

        Given: a download entry and the hardening switched off.
        When: the owner lists the audit log and the status rows.
        Then: the entry is judged by the record rebuilt from its payload
              again (blank here, so hidden), and status rows are not
              filtered — the opt-out changes nothing else.
        """
        entry = self.download_as_owner()
        with patch.dict(os.environ, {"LEX_SEC_READ_SURFACES": "false"}):
            self.assertNotIn(entry.pk, self.entry_ids(self.owner))
            self.assertIn(
                AuditLogStatus.objects.get(audit_log=entry).pk,
                {r["id"] for r in self.rows(self.stranger, "auditlogstatus")},
            )

    # -- 6.167 ---------------------------------------------------------
    def test_6_167_detail_endpoint_applies_the_same_rule(self) -> None:
        """
        Scenario 6.167: an entry hidden from the list is hidden by id too.

        Given: a download entry and its status row.
        When: a stranger and the owner fetch each by id.
        Then: the stranger gets 404, the owner gets the row. Before, the
              detail endpoint asked ``AuditLog.permission_read``, which
              allows everyone, so any entry was one id away.
        """
        entry = self.download_as_owner()
        status = AuditLogStatus.objects.get(audit_log=entry)

        self.login(self.stranger)
        self.assertEqual(self.client.get(self.url_detail("auditlog", entry.pk)).status_code, 404)
        self.assertEqual(self.client.get(self.url_detail("auditlogstatus", status.pk)).status_code, 404)

        self.login(self.owner)
        resp = self.client.get(self.url_detail("auditlog", entry.pk))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json().get("id"), entry.pk)
        self.assertEqual(self.client.get(self.url_detail("auditlogstatus", status.pk)).status_code, 200)
