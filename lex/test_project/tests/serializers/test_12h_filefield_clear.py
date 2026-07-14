"""Cluster 12h — clearing a FileField through the REST update path.

Intent: removing a file from a record must be possible through the API. DRF's
multipart semantics give a client only two signals — send a file (replace) or
omit the key (keep) — so file removal was silently impossible: the admin
frontend dropped the cleared (null) value from the FormData, the backend never
saw the field, and the "removed" file reappeared after save (customer report
2026-07-13). The framework now maps model file fields to
``LexClearableFileField``, which treats an explicit empty value as "clear the
stored file" while keeping omit-means-keep and upload-means-replace intact.
Cluster 12h — scenarios 12.39–12.41. Type: E.
Covers: lex/api/serializers/base_serializers.py (LexClearableFileField,
        LexClearableImageField, LexSerializer.serializer_field_mapping).
Run: python -m lex pytest lex/test_project/tests/serializers/test_12h_filefield_clear.py -v
"""

from __future__ import annotations

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from rest_framework import status

from lex.test_project.tests._e2e_test_case import E2ETestCase

from .models import ALL_MODELS, ATTACHMENT, AttachmentItem

pytestmark = pytest.mark.serializers


class TestCluster12h_FileFieldClear(E2ETestCase):
    """Cluster 12h: empty value clears, omit keeps, upload replaces."""

    e2e_models = ALL_MODELS

    def _item_with_file(self, name: str = "doc-item") -> AttachmentItem:
        item = AttachmentItem(name=name)
        item.document.save(
            "original.txt", SimpleUploadedFile("original.txt", b"original content"),
            save=False,
        )
        # bulk_create skips save() so no calculation/audit hooks fire — these
        # scenarios only exercise the serializer write path.
        AttachmentItem.objects.bulk_create([item])
        return AttachmentItem.objects.get(name=name)

    def test_12_39_empty_value_clears_the_stored_file(self) -> None:
        """
        Scenario 12.39: an explicit empty value on a file field clears it.
        Given: a record whose FileField holds a stored file
        When: PUT the record with the file field sent as an empty string
              (multipart — the only way a form client can express removal)
        Then: the update succeeds and the stored file reference is gone
        """
        item = self._item_with_file()
        self.assertTrue(bool(item.document), "Precondition: the file must exist.")

        resp = self.client.put(
            self.url_detail(ATTACHMENT, item.pk),
            data={"name": item.name, "document": ""},
            format="multipart",
        )
        self.assertEqual(
            resp.status_code, status.HTTP_200_OK,
            f"Clearing the file must be a valid update, got {resp.status_code}: "
            f"{getattr(resp, 'data', None)!r}",
        )
        item.refresh_from_db()
        self.assertFalse(
            bool(item.document),
            f"The cleared file must not survive the save — still stores "
            f"{item.document.name!r}.",
        )

    def test_12_40_omitting_the_key_keeps_the_stored_file(self) -> None:
        """
        Scenario 12.40: omitting the file key preserves the stored file.
        Given: a record whose FileField holds a stored file
        When: PUT the record without the file field in the payload
        Then: the stored file is untouched (DRF omit-means-keep semantics)
        """
        item = self._item_with_file(name="keep-item")
        original_name = item.document.name

        resp = self.client.put(
            self.url_detail(ATTACHMENT, item.pk),
            data={"name": "keep-item-renamed"},
            format="multipart",
        )
        self.assertEqual(
            resp.status_code, status.HTTP_200_OK,
            f"Update without the file key failed: {resp.status_code}",
        )
        item.refresh_from_db()
        self.assertEqual(
            item.document.name, original_name,
            "A payload that omits the file field must not touch the stored file.",
        )
        self.assertEqual(
            item.name, "keep-item-renamed",
            "The non-file change must still apply.",
        )

    def test_12_41_uploading_a_new_file_still_replaces(self) -> None:
        """
        Scenario 12.41: uploading a file still replaces the stored one.
        Given: a record whose FileField holds a stored file
        When: PUT the record with a new file in the payload
        Then: the field references the new file's content
        """
        item = self._item_with_file(name="replace-item")

        resp = self.client.put(
            self.url_detail(ATTACHMENT, item.pk),
            data={
                "name": item.name,
                "document": SimpleUploadedFile("newer.txt", b"newer content"),
            },
            format="multipart",
        )
        self.assertEqual(
            resp.status_code, status.HTTP_200_OK,
            f"Replacing the file failed: {resp.status_code}",
        )
        item.refresh_from_db()
        self.assertTrue(
            bool(item.document), "The replacement file must be stored."
        )
        with item.document.open("rb") as fh:
            self.assertEqual(
                fh.read(), b"newer content",
                "The stored file must be the newly uploaded one.",
            )
