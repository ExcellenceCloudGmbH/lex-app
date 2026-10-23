"""A raising permission hook denies on the surfaces 4o did not reach (LEX_SEC_FAIL_CLOSED).

Intent
------
4o made a raising permission hook deny on the record endpoints. Four places
still caught the exception and *allowed*:

* the audit-log list judged an entry by ``permission_read`` of a record
  rebuilt from its payload -- a raising hook showed the entry
  (``lex/api/utils/helpers.py``, ``can_read_from_payload``);
* the audit-log serializer kept a foreign-key reference in the payload when
  ``permission_read`` of the referenced record raised, and returned the payload
  unfiltered when its filtering raised (``base_serializers.py``);
* the export put every field of a row in the spreadsheet when the row's
  ``permission_export`` raised (``ModelExport.get_exportable_fields_for_object``);
* ``PermissionAwareSerializerMixin`` allowed every field / the create when
  ``permission_edit`` / ``permission_create`` raised.

With ``LEX_SEC_FAIL_CLOSED`` (on by default) each of them denies: the entry is
hidden, the reference dropped, the payload withheld (``{}``), the row left out
of the export, the write refused. ``LEX_SEC_FAIL_CLOSED=false`` or
``LEX_LEGACY_SECURITY=true`` restore the old allow.

Cluster 4r -- scenarios 4.116-4.120. Type: E (4.118, 4.120: I).
Covers: lex/api/utils/helpers.py, lex/api/serializers/base_serializers.py,
lex/api/views/file_operations/ModelExport.py,
lex/api/views/model_entries/mixins/PermissionAwareSerializerMixin.py.
Run: python -m lex pytest lex/test_project/tests/permissions/test_4r_fail_closed_remaining_surfaces.py -v
"""

from __future__ import annotations

import os
from io import BytesIO
from unittest import mock

import pandas as pd
import pytest
from rest_framework import serializers
from rest_framework.exceptions import PermissionDenied
from rest_framework.test import APIRequestFactory

from lex.api.views.model_entries.mixins.PermissionAwareSerializerMixin import (
    PermissionAwareSerializerMixin,
)
from lex.audit_logging.models.AuditLog import AuditLog
from lex.audit_logging.models.AuditLogStatus import AuditLogStatus
from lex.test_project.tests._e2e_test_case import E2ETestCase

from .models import (
    RAISING_CHILD,
    RAISING_EXPORT,
    RAISING_PARENT,
    RAISING_SURFACE_MODELS,
    RaisingChild,
    RaisingExportItem,
    RaisingParent,
    RaisingReadItem,
)

pytestmark = pytest.mark.permissions


def _env(**extra):
    clean = {k: v for k, v in os.environ.items() if not k.startswith(("LEX_SEC_", "LEX_LEGACY_SECURITY"))}
    clean.update(extra)
    return mock.patch.dict(os.environ, clean, clear=True)


def _legacy():
    return _env(LEX_SEC_FAIL_CLOSED="false")


class _RaisingSurfaceCase(E2ETestCase):
    e2e_models = RAISING_SURFACE_MODELS
    e2e_framework_models = [AuditLog, AuditLogStatus]

    def setUp(self) -> None:
        super().setUp()
        self._defaults = _env()
        self._defaults.start()
        self.addCleanup(self._defaults.stop)
        AuditLog.objects.all().delete()

    def audit_rows(self):
        response = self.client.get(self.url_list("auditlog"), {"page": 1, "perPage": 100})
        self.assertEqual(response.status_code, 200, response.content[:300])
        body = response.json()
        return {row["id"]: row for row in (body["results"] if isinstance(body, dict) else body)}

    def entry(self, resource, payload):
        return AuditLog.objects.create(author="someone", resource=resource, action="create", payload=payload)


class TestCluster04r_AuditLogRead(_RaisingSurfaceCase):
    """The audit-log list and its payload."""

    # -- 4.116 ---------------------------------------------------------
    def test_4_116_raising_read_hides_the_audit_entry(self) -> None:
        """
        Scenario 4.116: an entry whose record's permission_read raises is hidden.
        Given: two create entries of RaisingParent, payload names "boom" and "fine"
        When: the audit log is listed by default and with LEX_SEC_FAIL_CLOSED=false
        Then: by default only "fine" is listed; with fail-closed off both are.
        """
        boom = self.entry(RAISING_PARENT, {"name": "boom"})
        fine = self.entry(RAISING_PARENT, {"name": "fine"})
        ids = set(self.audit_rows())
        self.assertIn(fine.pk, ids)
        self.assertNotIn(boom.pk, ids)
        with _legacy():
            ids = set(self.audit_rows())
        self.assertTrue({boom.pk, fine.pk} <= ids)

    # -- 4.117 ---------------------------------------------------------
    def test_4_117_raising_read_drops_the_payload_reference(self) -> None:
        """
        Scenario 4.117: a payload reference to a record whose permission_read raises is dropped.
        Given: a RaisingChild entry whose payload names the parent "boom" by id
        When: the audit log is listed by default and with LEX_SEC_FAIL_CLOSED=false
        Then: the entry is listed; by default its payload has no "parent";
              with fail-closed off the reference stays.
        """
        parent = RaisingParent.objects.create(name="boom")
        entry = self.entry(RAISING_CHILD, {"name": "child", "parent": {"id": parent.pk, "display": "boom"}})
        payload = self.audit_rows()[entry.pk]["payload"]
        self.assertEqual(payload.get("name"), "child")
        self.assertNotIn("parent", payload)
        with _legacy():
            payload = self.audit_rows()[entry.pk]["payload"]
        self.assertEqual(payload.get("parent", {}).get("id"), parent.pk)

    # -- 4.118 ---------------------------------------------------------
    def test_4_118_raising_payload_filter_withholds_the_payload(self) -> None:
        """
        Scenario 4.118: when filtering the payload raises, the payload is withheld.
        Given: a readable RaisingChild entry; the payload's field check made to raise
        When: the audit log is listed by default and with LEX_SEC_FAIL_CLOSED=false
        Then: the entry is listed with payload {}; with fail-closed off the
              payload comes back unfiltered.
        """
        entry = self.entry(RAISING_CHILD, {"name": "child"})
        target = (
            "lex.api.serializers.base_serializers.LexSerializer"
            "._get_audit_log_payload_visible_fields"
        )
        with mock.patch(target, side_effect=RuntimeError("cluster 4r: payload check failed on purpose")):
            self.assertEqual(self.audit_rows()[entry.pk]["payload"], {})
            with _legacy():
                self.assertEqual(self.audit_rows()[entry.pk]["payload"].get("name"), "child")


class TestCluster04r_Export(_RaisingSurfaceCase):
    """The export's per-row field selection."""

    # -- 4.119 ---------------------------------------------------------
    def test_4_119_raising_export_check_leaves_the_row_out(self) -> None:
        """
        Scenario 4.119: a row whose permission_export raises is not exported.
        Given: rows "fine" and "boom"; permission_export raises for "boom"
        When: the model is exported by default and with LEX_SEC_FAIL_CLOSED=false
        Then: by default the spreadsheet has "fine" only; with fail-closed off both.
        """
        RaisingExportItem.objects.create(name="fine")
        RaisingExportItem.objects.create(name="boom")

        def exported_names():
            response = self.client.post(self.url_export(RAISING_EXPORT), data={}, format="json")
            self.assertEqual(response.status_code, 200)
            frame = pd.read_excel(BytesIO(b"".join(response.streaming_content)), index_col=0)
            return sorted(frame["name"].dropna().tolist())

        self.assertEqual(exported_names(), ["fine"])
        with _legacy():
            self.assertEqual(exported_names(), ["boom", "fine"])


class _RaisingParentSerializer(PermissionAwareSerializerMixin, serializers.ModelSerializer):
    class Meta:
        model = RaisingParent
        fields = "__all__"


class _RaisingReadSerializer(PermissionAwareSerializerMixin, serializers.ModelSerializer):
    class Meta:
        model = RaisingReadItem
        fields = "__all__"


class TestCluster04r_SerializerMixin(_RaisingSurfaceCase):
    """PermissionAwareSerializerMixin.run_validation."""

    def _request(self):
        request = APIRequestFactory().patch("/")
        request.user = self.user
        request.user_permissions = ()
        return request

    # -- 4.120 ---------------------------------------------------------
    def test_4_120_raising_edit_or_create_check_refuses(self) -> None:
        """
        Scenario 4.120: the mixin refuses an edit or create whose check raises.
        Given: RaisingParent.permission_edit and RaisingReadItem.permission_create raise
        When: run_validation runs for a change and for a create, by default and
              with LEX_SEC_FAIL_CLOSED=false
        Then: by default both raise PermissionDenied; with fail-closed off both validate.
        """
        parent = RaisingParent.objects.create(name="before")

        def edit():
            ser = _RaisingParentSerializer(parent, data={"name": "after"}, context={"request": self._request()})
            return ser.run_validation(ser.initial_data)

        def create():
            ser = _RaisingReadSerializer(data={"name": "new"}, context={"request": self._request()})
            return ser.run_validation(ser.initial_data)

        with self.assertRaises(PermissionDenied):
            edit()
        with self.assertRaises(PermissionDenied):
            create()
        with _legacy():
            self.assertEqual(edit()["name"], "after")
            self.assertEqual(create()["name"], "new")
