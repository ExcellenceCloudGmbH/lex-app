"""Secure defaults of lex-app 3.0 on the generic REST endpoints.

Intent
------
Permission methods are only worth what their enforcement is worth. Since 3.0,
on by default (``LEX_LEGACY_SECURITY=true`` / ``LEX_SEC_<FLAG>=false`` restore
the old behaviour):

* FAIL_CLOSED – a permission method that raises denies instead of allowing.
* NOT_FOUND – a record the caller may not read answers 404 on single and bulk
  endpoints, never 200 ``{}`` and never a new 403 (the bundled frontend signs
  the user out on 403).
* EDIT – ``permission_edit`` is enforced on PATCH/PUT, bulk PATCH and
  run_step (BUG-010); bulk DELETE asks ``permission_delete`` per row; run_step
  asks ``permission_create``. Denials answer 400 with ``EDIT_NOT_ALLOWED``,
  ``DELETE_NOT_ALLOWED`` or ``CREATE_NOT_ALLOWED``.
* READ_SURFACES – history, global search, export and file download apply read
  permission; HTML reports have ``permission_view``.

Rows a model admits by an SQL rule (``permission_read_queryset``) are decided
in one query, and ``permission_read`` runs at most once per row and request
(BUG-011's duplicate evaluations).

Cluster 4p — scenarios 4.86–4.103. Type: E.
Covers: lex/api/security.py, lex/api/views/model_entries/{One,Many,History,
filter_backends}.py, lex/api/views/process_flow/CreateOrUpdate.py,
lex/api/views/global_search_for_models/Search.py,
lex/api/views/file_operations/{ModelExport,FileDownload}.py,
lex/api/filters/GenericFilters.py, lex/process_admin/views/model_relation_views.py
(Overview), lex/core/models/{LexModel,HTMLReport}.py.
Run: python -m lex pytest lex/test_project/tests/permissions/test_4p_secure_defaults.py -v
"""

from __future__ import annotations

import os
from io import BytesIO
from unittest import mock

import pandas as pd
import pytest
from django.core.files.base import ContentFile
from django.urls import reverse
from rest_framework.test import APIClient

from lex.core.models.HTMLReport import HTMLReport
from lex.core.models.LexModel import PermissionResult
from lex.test_project.tests._e2e_test_case import E2ETestCase

from .models import (
    FILTER_BACKEND,
    KEYCLOAK,
    PROTECTED,
    QUERYSET_RULE,
    RAISING_READ,
    SECRET_DOCUMENT,
    SECURE_DEFAULT_MODELS,
    FilterBackendItem,
    KeycloakItem,
    ProtectedItem,
    QuerysetRuleItem,
    RaisingReadItem,
    SecretDocument,
)

pytestmark = pytest.mark.permissions


def _env(**extra):
    clean = {k: v for k, v in os.environ.items() if not k.startswith(("LEX_SEC_", "LEX_LEGACY_SECURITY"))}
    clean.update(extra)
    return mock.patch.dict(os.environ, clean, clear=True)


def _legacy():
    return _env(LEX_LEGACY_SECURITY="true")


class _SecureDefaultsCase(E2ETestCase):
    e2e_models = SECURE_DEFAULT_MODELS

    def setUp(self) -> None:
        super().setUp()
        self._defaults = _env()
        self._defaults.start()
        self.addCleanup(self._defaults.stop)

    def list_names(self, model_name, **params):
        response = self.client.get(self.url_list(model_name), params)
        self.assertEqual(response.status_code, 200, response.content[:300])
        body = response.json()
        rows = body["results"] if isinstance(body, dict) else body
        return sorted(row.get("name") for row in rows)


class TestCluster04p_FailClosedAndNotFound(_SecureDefaultsCase):
    """FAIL_CLOSED and NOT_FOUND."""

    # -- 4.86 ----------------------------------------------------------
    def test_4_86_raising_read_check_hides_the_row(self) -> None:
        """
        Scenario 4.86: a permission_read that raises hides its row.
        Given: two rows, one whose permission_read raises
        When: the list is requested by default and with the legacy switch
        Then: by default only the healthy row is listed; in legacy mode the
              filter lets the row through and the request fails with 500 when
              the serializer asks again (the pre-3.0 behaviour).
        """
        RaisingReadItem.objects.create(name="fine")
        RaisingReadItem.objects.create(name="boom")
        self.assertEqual(self.list_names(RAISING_READ), ["fine"])
        with _legacy():
            client = APIClient(raise_request_exception=False)
            client.force_login(self.user)
            session = client.session
            session.update(self.client.session.items())
            session.save()
            response = client.get(self.url_list(RAISING_READ))
        self.assertEqual(response.status_code, 500)

    # -- 4.87 ----------------------------------------------------------
    def test_4_87_raising_create_check_refuses_the_create(self) -> None:
        """
        Scenario 4.87: a permission_create that raises refuses the create.
        Given: a model whose permission_create raises
        When: a row is created by default and with the legacy switch
        Then: by default HTTP 500 and nothing is stored; legacy stores the row.
        """
        response = self.client.post(self.url_create(RAISING_READ), {"name": "new"}, format="json")
        self.assertEqual(response.status_code, 500)
        self.assertFalse(RaisingReadItem.objects.exists())
        with _legacy():
            response = self.client.post(self.url_create(RAISING_READ), {"name": "new"}, format="json")
        self.assertIn(response.status_code, (200, 201), response.content[:300])
        self.assertTrue(RaisingReadItem.objects.filter(name="new").exists())

    # -- 4.88 ----------------------------------------------------------
    def test_4_88_unreadable_record_is_not_found(self) -> None:
        """
        Scenario 4.88: a record the caller may not read answers 404.
        Given: a secret FilterBackendItem a regular user may not read
        When: it is fetched by id by default and with the legacy switch
        Then: by default 404; legacy answers 200 with an empty body.
        """
        secret = FilterBackendItem.objects.create(name="hidden", is_secret=True)
        response = self.client.get(self.url_detail(FILTER_BACKEND, secret.pk))
        self.assertEqual(response.status_code, 404)
        with _legacy():
            response = self.client.get(self.url_detail(FILTER_BACKEND, secret.pk))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {})

    # -- 4.89 ----------------------------------------------------------
    def test_4_89_unreadable_record_cannot_be_changed_or_deleted(self) -> None:
        """
        Scenario 4.89: PATCH and DELETE on an unreadable record answer 404, never 403.
        Given: a secret row
        When: it is patched and deleted
        Then: both answer 404 and the row is unchanged.
        """
        secret = FilterBackendItem.objects.create(name="hidden", is_secret=True)
        self.assertEqual(
            self.client.patch(self.url_detail(FILTER_BACKEND, secret.pk), {"name": "x"}, format="json").status_code,
            404,
        )
        self.assertEqual(self.client.delete(self.url_detail(FILTER_BACKEND, secret.pk)).status_code, 404)
        secret.refresh_from_db()
        self.assertEqual(secret.name, "hidden")

    # -- 4.90 ----------------------------------------------------------
    def test_4_90_bulk_endpoints_respect_visibility(self) -> None:
        """
        Scenario 4.90: the bulk endpoint never shows or deletes unreadable rows.
        Given: a visible and a secret row
        When: both ids are fetched and deleted through /many
        Then: GET returns only the visible row; DELETE answers 404 and deletes nothing.
        """
        visible = FilterBackendItem.objects.create(name="open")
        secret = FilterBackendItem.objects.create(name="hidden", is_secret=True)
        url = f"{self.url_many(FILTER_BACKEND)}?ids={visible.pk}&ids={secret.pk}"
        rows = self.client.get(url).json()
        self.assertEqual([row.get("name") for row in rows], ["open"])
        self.assertEqual(self.client.delete(url).status_code, 404)
        self.assertEqual(FilterBackendItem.objects.count(), 2)


class TestCluster04p_EditEnforcement(_SecureDefaultsCase):
    """EDIT: permission_edit / permission_delete / permission_create on every write path."""

    # -- 4.91 ----------------------------------------------------------
    def test_4_91_disallowed_field_answers_400(self) -> None:
        """
        Scenario 4.91: PATCH of a field outside permission_edit is refused (BUG-010).
        Given: ProtectedItem, whose permission_edit allows only "name" for non-admins
        When: a regular user patches "secret", then "name"
        Then: "secret" answers 400 EDIT_NOT_ALLOWED naming the field and the value
              stays; "name" answers 200 and changes.
        """
        item = ProtectedItem.objects.create(name="p", secret="original")
        response = self.client.patch(self.url_detail(PROTECTED, item.pk), {"secret": "leaked"}, format="json")
        self.assertEqual(response.status_code, 400)
        body = response.json()
        self.assertEqual(body["code"], "EDIT_NOT_ALLOWED")
        self.assertEqual(body["fields"], {"secret": "Not editable."})
        self.assertIn("secret", body["error"])
        item.refresh_from_db()
        self.assertEqual(item.secret, "original")
        response = self.client.patch(self.url_detail(PROTECTED, item.pk), {"name": "renamed"}, format="json")
        self.assertEqual(response.status_code, 200, response.content[:300])
        item.refresh_from_db()
        self.assertEqual(item.name, "renamed")

    # -- 4.92 ----------------------------------------------------------
    def test_4_92_unchanged_values_do_not_count(self) -> None:
        """
        Scenario 4.92: sending a disallowed field with its current value is not an edit.
        Given: the same ProtectedItem
        When: "name" changes and "secret" is sent unchanged
        Then: HTTP 200 — only changed fields are checked, as @add_permission_checks does.
        """
        item = ProtectedItem.objects.create(name="p", secret="original")
        response = self.client.patch(
            self.url_detail(PROTECTED, item.pk), {"name": "next", "secret": "original"}, format="json"
        )
        self.assertEqual(response.status_code, 200, response.content[:300])

    # -- 4.93 ----------------------------------------------------------
    def test_4_93_record_without_edit_right_refuses_every_write(self) -> None:
        """
        Scenario 4.93: a record permission_edit denies refuses any PATCH.
        Given: a readable KeycloakItem and a user without the Keycloak "edit" scope
        When: it is patched by default and with the legacy switch
        Then: by default 400 EDIT_NOT_ALLOWED; legacy applies the change.
        """
        item = KeycloakItem.objects.create(label="k")
        # Readable (so the record is found), but no edit scope.
        readable = mock.patch.object(
            KeycloakItem, "permission_read", lambda self, uc: PermissionResult.allow_all("4.93: readable")
        )
        readable.start()
        self.addCleanup(readable.stop)
        response = self.client.patch(self.url_detail(KEYCLOAK, item.pk), {"label": "changed"}, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["code"], "EDIT_NOT_ALLOWED")
        with _legacy():
            self.client.patch(self.url_detail(KEYCLOAK, item.pk), {"label": "changed"}, format="json")
        item.refresh_from_db()
        self.assertEqual(item.label, "changed")

    # -- 4.94 ----------------------------------------------------------
    def test_4_94_bulk_patch_checks_every_row(self) -> None:
        """
        Scenario 4.94: a bulk PATCH touching a disallowed field changes nothing.
        Given: two ProtectedItems
        When: both are patched through /many with a new "secret"
        Then: HTTP 400 EDIT_NOT_ALLOWED and both secrets are unchanged.
        """
        first = ProtectedItem.objects.create(name="a", secret="s1")
        second = ProtectedItem.objects.create(name="b", secret="s2")
        url = f"{self.url_many(PROTECTED)}?ids={first.pk}&ids={second.pk}"
        response = self.client.patch(url, {"secret": "leaked"}, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(sorted(ProtectedItem.objects.values_list("secret", flat=True)), ["s1", "s2"])

    # -- 4.95 ----------------------------------------------------------
    def test_4_95_bulk_delete_asks_permission_delete(self) -> None:
        """
        Scenario 4.95: bulk DELETE refuses rows permission_delete refuses.
        Given: ProtectedItems a non-admin may not delete
        When: they are deleted through /many by default and with the legacy switch
        Then: by default 400 DELETE_NOT_ALLOWED listing the ids, nothing deleted;
              legacy deletes them (the pre-3.0 hole).
        """
        first = ProtectedItem.objects.create(name="a")
        second = ProtectedItem.objects.create(name="b")
        url = f"{self.url_many(PROTECTED)}?ids={first.pk}&ids={second.pk}"
        response = self.client.delete(url)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["code"], "DELETE_NOT_ALLOWED")
        self.assertEqual(sorted(response.json()["ids"]), sorted([first.pk, second.pk]))
        self.assertEqual(ProtectedItem.objects.count(), 2)
        with _legacy():
            self.client.delete(url)
        self.assertEqual(ProtectedItem.objects.count(), 0)

    # -- 4.96 ----------------------------------------------------------
    def test_4_96_run_step_applies_the_same_checks(self) -> None:
        """
        Scenario 4.96: run_step cannot bypass edit and create permissions.
        Given: a ProtectedItem and a non-admin (may edit only "name", may not create)
        When: run_step PATCHes "secret" on it, and POSTs a new row for an unknown pk
        Then: the PATCH answers 400 EDIT_NOT_ALLOWED; the create answers 400
              CREATE_NOT_ALLOWED; nothing changes.
        """
        item = ProtectedItem.objects.create(name="p", secret="original")
        url = reverse("process_admin_rest_api:run_step", kwargs={"model_container": _Container(PROTECTED), "pk": item.pk})
        response = self.client.patch(url, {"secret": "leaked"}, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["code"], "EDIT_NOT_ALLOWED")
        url = reverse("process_admin_rest_api:run_step", kwargs={"model_container": _Container(PROTECTED), "pk": 999999})
        response = self.client.patch(url, {"name": "new"}, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["code"], "CREATE_NOT_ALLOWED")
        self.assertEqual(ProtectedItem.objects.count(), 1)


class _Container:
    def __init__(self, model_id):
        self.id = model_id


class _OnlyAdminsReport(HTMLReport):
    @classmethod
    def permission_view(cls, user_context):
        return user_context.is_superuser

    def get_html(self, user):
        return "<p>numbers</p>"


class TestCluster04p_ReadSurfaces(_SecureDefaultsCase):
    """READ_SURFACES and HTMLReport.permission_view."""

    # -- 4.97 ----------------------------------------------------------
    def test_4_97_history_of_unreadable_record_is_not_found(self) -> None:
        """
        Scenario 4.97: the history of a record the caller may not read does not exist.
        Given: a visible and a secret row with history
        When: their history is requested
        Then: the visible row's history answers 200 with entries; the secret
              row's answers 404 (editor and timestamps are data too).
        """
        visible = FilterBackendItem.objects.create(name="open")
        secret = FilterBackendItem.objects.create(name="hidden", is_secret=True)
        response = self.client.get(self.url_history(FILTER_BACKEND, visible.pk))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json())
        self.assertEqual(self.client.get(self.url_history(FILTER_BACKEND, secret.pk)).status_code, 404)

    # -- 4.98 ----------------------------------------------------------
    def test_4_98_global_search_shows_only_readable_rows(self) -> None:
        """
        Scenario 4.98: global search returns no row the caller may not read.
        Given: a visible and a secret row both matching "needle"
        When: the global search runs for "needle", by default and with the legacy switch
        Then: by default only the visible row is a result; legacy returns both.
        """
        FilterBackendItem.objects.create(name="needle open")
        FilterBackendItem.objects.create(name="needle hidden", is_secret=True)
        from types import SimpleNamespace

        from lex.api.views.global_search_for_models.Search import Search
        from lex.process_admin.settings import processAdminSite

        container = processAdminSite.model_collection.get_container(FILTER_BACKEND)

        def descriptions():
            # Only this model's container: the test database has no tables for
            # unrelated registered models.
            view = Search()
            view.model_collection = SimpleNamespace(all_containers=[container])
            view.kwargs = {"query": "needle"}
            view.request = self.client.get("/").wsgi_request
            body = view.get(view.request).data
            return sorted(match["content"]["description"] for match in body.get("data", [])) if isinstance(body, dict) else []

        self.assertEqual(descriptions(), ["needle open"])
        with _legacy():
            self.assertEqual(descriptions(), ["needle hidden", "needle open"])

    # -- 4.99 ----------------------------------------------------------
    def test_4_99_export_leaves_out_unreadable_rows(self) -> None:
        """
        Scenario 4.99: an export contains only rows the caller may read.
        Given: two visible rows and one secret row
        When: the model is exported
        Then: the spreadsheet has two rows.
        """
        FilterBackendItem.objects.create(name="one")
        FilterBackendItem.objects.create(name="two")
        FilterBackendItem.objects.create(name="hidden", is_secret=True)
        response = self.client.post(self.url_export(FILTER_BACKEND), data={}, format="json")
        self.assertEqual(response.status_code, 200)
        frame = pd.read_excel(BytesIO(b"".join(response.streaming_content)), index_col=0)
        self.assertEqual(len(frame), 2)

    # -- 4.100 ---------------------------------------------------------
    def test_4_100_file_download_checks_parameters_field_and_record(self) -> None:
        """
        Scenario 4.100: file download only serves real files of readable records.
        Given: a visible and a secret document, each with an attachment
        When: downloads are requested without parameters, for a non-file field,
              for the secret document and for the visible one
        Then: 400, 404, 404 and 200 with the file's bytes.
        """
        visible = SecretDocument.objects.create(name="open")
        visible.attachment.save("open.txt", ContentFile(b"visible bytes"))
        self.addCleanup(visible.attachment.delete, save=False)
        secret = SecretDocument.objects.create(name="hidden", is_secret=True)
        secret.attachment.save("hidden.txt", ContentFile(b"secret bytes"))
        self.addCleanup(secret.attachment.delete, save=False)
        url = reverse("process_admin_rest_api:file-download", kwargs={"model_container": _Container(SECRET_DOCUMENT)})
        self.assertEqual(self.client.get(url).status_code, 400)
        self.assertEqual(self.client.get(url, {"pk": visible.pk, "field": "name"}).status_code, 404)
        self.assertEqual(self.client.get(url, {"pk": secret.pk, "field": "attachment"}).status_code, 404)
        response = self.client.get(url, {"pk": visible.pk, "field": "attachment"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(b"".join(response.streaming_content), b"visible bytes")

    # -- 4.101 ---------------------------------------------------------
    def test_4_101_html_report_permission_view(self) -> None:
        """
        Scenario 4.101: a report's permission_view decides who may open it.
        Given: a report only superusers may view
        When: a regular user, a superuser and an anonymous client open it, and
              an unknown report is requested
        Then: 404 for the regular user, 200 with the HTML for the superuser,
              no 200 for the anonymous client, 404 for the unknown report.
        """
        from lex.process_admin.settings import processAdminSite

        processAdminSite.html_reports["cluster4preport"] = _OnlyAdminsReport
        self.addCleanup(processAdminSite.html_reports.pop, "cluster4preport", None)
        url = reverse("process_admin_rest_api:htmlreports", kwargs={"report_name": "cluster4preport"})
        self.assertEqual(self.client.get(url).status_code, 404)
        self.assertNotEqual(APIClient().get(url).status_code, 200)
        self.user.is_superuser = True
        self.user.save()
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), "<p>numbers</p>")
        unknown = reverse("process_admin_rest_api:htmlreports", kwargs={"report_name": "nope"})
        self.assertEqual(self.client.get(unknown).status_code, 404)


class TestCluster04p_ReadRulesAtScale(_SecureDefaultsCase):
    """permission_read_queryset and one evaluation per row."""

    # -- 4.102 ---------------------------------------------------------
    def test_4_102_sql_read_rule_decides_rows(self) -> None:
        """
        Scenario 4.102: permission_read_queryset decides which rows are listed.
        Given: a model whose permission_read_queryset admits public rows only
        When: the list and a private row's detail are requested
        Then: only public rows are listed and the private row answers 404.
        """
        QuerysetRuleItem.objects.create(name="pub", is_public=True)
        private = QuerysetRuleItem.objects.create(name="priv", is_public=False)
        self.assertEqual(self.list_names(QUERYSET_RULE), ["pub"])
        self.assertEqual(self.client.get(self.url_detail(QUERYSET_RULE, private.pk)).status_code, 404)

    # -- 4.103 ---------------------------------------------------------
    def test_4_103_permission_read_runs_once_per_row(self) -> None:
        """
        Scenario 4.103: listing evaluates permission_read once per row, not twice.
        Given: three readable rows and a counting permission_read
        When: the list is requested
        Then: permission_read ran exactly three times (the filter decides, the
              serializer reuses the answer).
        """
        for name in ("a", "b", "c"):
            FilterBackendItem.objects.create(name=name)
        calls = []
        original = FilterBackendItem.permission_read

        def counting(item, uc):
            calls.append(item.pk)
            return original(item, uc)

        with mock.patch.object(FilterBackendItem, "permission_read", counting):
            self.assertEqual(self.list_names(FILTER_BACKEND), ["a", "b", "c"])
        self.assertEqual(len(calls), 3, calls)
