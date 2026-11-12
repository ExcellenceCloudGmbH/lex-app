"""Cluster 12q — files larger than one request body, uploaded in pieces.

Intent: a user must be able to attach a file larger than the ~100 MB every
request body is capped at before it reaches Django (Cloudflare, the nginx
Ingress, ModSecurity). A 606 MB claims CSV failed in the browser as "Failed to
fetch" (claim_validation, 2026-11-11) without any trace on the backend. The
client now uploads such a file in pieces to ``api/chunked-uploads/`` and sends
``lex-chunked-upload:<id>`` as the file field's value; ``LexClearableFileField``
joins the pieces and the record is saved as if the file had come in directly.
Cluster 12q — scenarios 12.73–12.80. Type: E.
Covers: lex/api/views/file_operations/ChunkedUpload.py,
        lex/api/serializers/base_serializers.py (LexClearableFileField),
        lex/process_admin/sites/process_admin_site.py (the three api/chunked-uploads/ routes,
        reached here by name through self._url).
Run: python -m lex pytest lex/test_project/tests/serializers/test_12q_chunked_upload.py -v
"""

from __future__ import annotations

import shutil
import tempfile
import time
from datetime import timedelta
from unittest.mock import patch

import pytest
from django.contrib.auth.models import User
from django.core.files.storage import default_storage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from lex.api.views.file_operations import ChunkedUpload
from lex.test_project.tests._e2e_test_case import E2ETestCase

from .models import ALL_MODELS, ATTACHMENT, AttachmentItem

pytestmark = pytest.mark.serializers

PIECE = 4  # bytes per piece in these tests


class TestCluster12q_ChunkedUpload(E2ETestCase):
    """Cluster 12q: pieces in, one file out; only for the user who sent them."""

    e2e_models = ALL_MODELS

    def setUp(self) -> None:
        super().setUp()
        self._media = tempfile.mkdtemp(prefix="lex-chunked-")
        self._media_override = override_settings(MEDIA_ROOT=self._media)
        self._media_override.enable()
        self._chunk_patch = patch.object(ChunkedUpload, "CHUNK_SIZE", PIECE)
        self._chunk_patch.start()

    def tearDown(self) -> None:
        self._chunk_patch.stop()
        self._media_override.disable()
        shutil.rmtree(self._media, ignore_errors=True)
        super().tearDown()

    # -- helpers ---------------------------------------------------------

    def _start(self, content: bytes, filename: str = "claims.csv", client=None) -> dict:
        resp = (client or self.client).post(
            self._url("chunked-upload-start"),
            data={"filename": filename, "size": len(content)},
            format="json",
        )
        self.assertEqual(resp.status_code, 201, resp.content)
        return resp.json()

    def _send(self, upload_id: str, index: int, piece: bytes, client=None):
        return (client or self.client).post(
            self._url("chunked-upload-chunk", upload_id=upload_id, index=index),
            data={"chunk": SimpleUploadedFile("blob", piece)},
            format="multipart",
        )

    def _upload(self, content: bytes, order=None) -> dict:
        started = self._start(content)
        pieces = [content[i:i + PIECE] for i in range(0, len(content), PIECE)]
        self.assertEqual(started["chunk_count"], len(pieces))
        for index in order or range(len(pieces)):
            resp = self._send(started["upload_id"], index, pieces[index])
            self.assertEqual(resp.status_code, 200, resp.content)
        return started

    def _other_client(self) -> APIClient:
        User.objects.filter(username="e2e_other").delete()
        other = User.objects.create_user(username="e2e_other", password="pw")
        client = APIClient()
        client.force_login(other)
        session = client.session
        session["oidc_expires_at"] = (timezone.now() + timedelta(hours=1)).timestamp()
        session.save()
        return client

    def _stored(self, item: AttachmentItem) -> bytes:
        with item.document.open("rb") as fh:
            return fh.read()

    # -- scenarios -------------------------------------------------------

    def test_12_73_create_with_a_chunked_upload_stores_the_whole_file(self) -> None:
        """
        Scenario 12.73: pieces sent out of order become the record's file.
        Given: a file uploaded as 4 pieces, the last one short, in reverse order
        When: POST create with the upload token as the file field
        Then: the record stores the original bytes under the original name
        """
        content = b"id,amount\n1,10\n"  # 15 bytes -> 4 pieces
        started = self._upload(content, order=[3, 2, 1, 0])

        resp = self.client.post(
            self.url_create(ATTACHMENT),
            data={"name": "big", "document": started["token"]},
            format="multipart",
        )
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED, getattr(resp, "data", None))
        item = AttachmentItem.objects.get(name="big")
        self.assertEqual(self._stored(item), content)
        self.assertIn("claims", item.document.name)

    def test_12_74_update_with_a_chunked_upload_replaces_the_file(self) -> None:
        """
        Scenario 12.74: the update path, which validates its payload more than
        once (edit permission check, no-op check, save), gets the file intact.
        """
        item = AttachmentItem(name="upd")
        item.document.save("old.txt", SimpleUploadedFile("old.txt", b"old"), save=False)
        AttachmentItem.objects.bulk_create([item])
        item = AttachmentItem.objects.get(name="upd")

        content = b"newer and longer content"
        started = self._upload(content)
        resp = self.client.put(
            self.url_detail(ATTACHMENT, item.pk),
            data={"name": "upd", "document": started["token"]},
            format="multipart",
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK, getattr(resp, "data", None))
        item.refresh_from_db()
        self.assertEqual(self._stored(item), content)

    def test_12_75_an_incomplete_upload_is_refused(self) -> None:
        """
        Scenario 12.75: a token whose pieces are not all there fails validation
        with a message naming the gap, and no record is created.
        """
        content = b"0123456789"
        started = self._start(content)
        self._send(started["upload_id"], 0, content[:PIECE])

        resp = self.client.post(
            self.url_create(ATTACHMENT),
            data={"name": "partial", "document": started["token"]},
            format="multipart",
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("incomplete", str(resp.content))
        self.assertFalse(AttachmentItem.objects.filter(name="partial").exists())

    def test_12_76_another_user_cannot_use_or_extend_the_upload(self) -> None:
        """
        Scenario 12.76: an upload belongs to the user who started it.
        Given: a finished upload of user A
        When: user B sends a piece to it, reads it, deletes it, or uses its token
        Then: every one of those is refused as if the upload did not exist
        """
        content = b"secret!!"
        started = self._upload(content)
        other = self._other_client()
        upload_id = started["upload_id"]

        self.assertEqual(self._send(upload_id, 0, b"evil", client=other).status_code, 404)
        detail = self._url("chunked-upload-detail", upload_id=upload_id)
        self.assertEqual(other.get(detail).status_code, 404)
        self.assertEqual(other.delete(detail).status_code, 404)
        resp = other.post(
            self.url_create(ATTACHMENT),
            data={"name": "stolen", "document": started["token"]},
            format="multipart",
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(AttachmentItem.objects.filter(name="stolen").exists())
        # The owner's upload is untouched.
        self.assertTrue(self.client.get(detail).json()["complete"])

    def test_12_77_pieces_of_the_wrong_size_or_index_are_refused(self) -> None:
        """
        Scenario 12.77: a piece must have exactly its expected length and an
        index inside the upload, so a joined file always has the declared size.
        """
        started = self._start(b"0123456789")  # pieces of 4, 4, 2
        upload_id = started["upload_id"]
        self.assertEqual(self._send(upload_id, 0, b"012").status_code, 400)
        self.assertEqual(self._send(upload_id, 2, b"8").status_code, 400)
        self.assertEqual(self._send(upload_id, 3, b"89").status_code, 400)
        self.assertEqual(self._send(upload_id, 2, b"89").status_code, 200)

    def test_12_78_the_size_cap_and_bad_ids_are_enforced(self) -> None:
        """
        Scenario 12.78: a file above LEX_CHUNKED_UPLOAD_MAX_SIZE is refused up
        front with 413, and an upload id that is not ours (e.g. a path) is 404.
        """
        with patch.object(ChunkedUpload, "MAX_UPLOAD_SIZE", 10):
            resp = self.client.post(
                self._url("chunked-upload-start"),
                data={"filename": "x.csv", "size": 11},
                format="json",
            )
        self.assertEqual(resp.status_code, 413)
        resp = self.client.get(self._url("chunked-upload-detail", upload_id="..%2F..%2Fetc"))
        self.assertEqual(resp.status_code, 404)

    def test_12_79_delete_removes_the_pieces(self) -> None:
        """
        Scenario 12.79: DELETE by the owner removes the upload and its token
        stops working.
        """
        started = self._upload(b"abcdefgh")
        detail = self._url("chunked-upload-detail", upload_id=started["upload_id"])
        self.assertEqual(self.client.delete(detail).status_code, 204)
        self.assertEqual(self.client.get(detail).status_code, 404)
        self.assertFalse(
            default_storage.exists(ChunkedUpload._manifest_path(started["upload_id"]))
        )

    def test_12_80_expired_uploads_are_swept_when_a_new_one_starts(self) -> None:
        """
        Scenario 12.80: an upload older than the TTL is deleted the next time
        anyone starts one; a fresh upload is left alone.
        """
        old = self._upload(b"abcdefgh")
        with patch.object(ChunkedUpload.time, "time", return_value=time.time() + ChunkedUpload.UPLOAD_TTL + 60):
            fresh = self._start(b"xy")
        self.assertFalse(default_storage.exists(ChunkedUpload._manifest_path(old["upload_id"])))
        self.assertTrue(default_storage.exists(ChunkedUpload._manifest_path(fresh["upload_id"])))
