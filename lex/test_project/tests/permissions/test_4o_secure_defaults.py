"""Secure defaults on the generic REST endpoints (v2 security hardening).

Intent
------
Permission methods are only worth what their enforcement is worth. On by
default (``LEX_LEGACY_SECURITY=true`` / ``LEX_SEC_<FLAG>=false`` restore the
old behaviour; a per-flag setting wins over the umbrella switch):

* FAIL_CLOSED – a permission method that raises denies instead of allowing.
* NOT_FOUND – a record the caller may not read answers 404 on single and bulk
  endpoints, never 200 ``{}`` and never a new 403 (the bundled frontend signs
  the user out on 403).
* EDIT – ``permission_edit`` is enforced on PATCH/PUT and bulk PATCH
  (BUG-010); bulk DELETE asks ``permission_delete`` per row. Denials answer
  400 with ``EDIT_NOT_ALLOWED``, ``DELETE_NOT_ALLOWED`` or
  ``CREATE_NOT_ALLOWED`` and a ``{code, detail, error}`` body.
* READ_SURFACES – history, global search, export and file download apply read
  permission. The HTML report endpoint requires sign-in; an unknown report is
  404.

Signed-out callers keep answering 401/403: authentication is decided before
visibility. Every refusal carries a readable ``error``.

``permission_read`` runs at most once per row and request (BUG-011's
duplicate evaluations), and a classic API key keeps its own context where the
serializer decides a row.

Cluster 4o — scenarios 4.80–4.100. Type: E (4.98–4.99: U).
Scenario 4.90 is retired: it pinned run_step, and the ProcessFlow endpoint it
drove was removed with the Process feature (cluster 10q pins its absence).
Covers: lex/core/security_flags.py, lex/api/security.py,
lex/api/views/model_entries/{One,Many,History,filter_backends}.py,
lex/api/views/global_search_for_models/Search.py,
lex/api/views/file_operations/{ModelExport,FileDownload}.py,
lex/api/filters/GenericFilters.py, lex/api/serializers/base_serializers.py,
lex/model_admin/views/model_relation_views.py (Overview).
Run: python -m lex pytest lex/test_project/tests/permissions/test_4o_secure_defaults.py -v
"""

from __future__ import annotations

import os
from io import BytesIO
from types import SimpleNamespace
from unittest import mock

import pandas as pd
import pytest
from django.contrib.auth.models import AnonymousUser, Group, User
from django.core.files.base import ContentFile
from django.test import RequestFactory, SimpleTestCase, TransactionTestCase
from django.urls import reverse
from rest_framework.test import APIClient

from lex.core.models.HTMLReport import HTMLReport
from lex.core.models.LexModel import PermissionResult
from lex.core.security_flags import FLAGS, is_superuser_or_admin, strict
from lex.test_project.tests._e2e_test_case import E2ETestCase

from .models import (
    ALL_MODELS,
    FILTER_BACKEND,
    KEYCLOAK,
    PROTECTED,
    RAISING_READ,
    SECRET_DOCUMENT,
    SECURE_DEFAULT_MODELS,
    FilterBackendItem,
    KeycloakItem,
    ProtectedItem,
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


class _Container:
    def __init__(self, model_id):
        self.id = model_id


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


class TestCluster04o_FailClosedAndNotFound(_SecureDefaultsCase):
    """FAIL_CLOSED and NOT_FOUND."""

    # -- 4.80 ----------------------------------------------------------
    def test_4_80_raising_read_check_hides_the_row(self) -> None:
        """
        Scenario 4.80: a permission_read that raises hides its row.
        Given: two rows, one whose permission_read raises
        When: the list is requested by default and with the legacy switch
        Then: by default only the healthy row is listed; in legacy mode the
              filter lets the row through and the request fails with 500 when
              the serializer asks again (the old behaviour).
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

    # -- 4.81 ----------------------------------------------------------
    def test_4_81_raising_create_check_refuses_the_create(self) -> None:
        """
        Scenario 4.81: a permission_create that raises refuses the create.
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

    # -- 4.82 ----------------------------------------------------------
    def test_4_82_unreadable_record_is_not_found(self) -> None:
        """
        Scenario 4.82: a record the caller may not read answers 404.
        Given: a secret FilterBackendItem a regular user may not read
        When: it is fetched by id by default, with the legacy switch, and with
              LEX_LEGACY_SECURITY=true plus LEX_SEC_NOT_FOUND=true
        Then: by default 404; legacy answers 200 with an empty body; the
              per-flag setting wins over the umbrella switch (404).
        """
        secret = FilterBackendItem.objects.create(name="hidden", is_secret=True)
        response = self.client.get(self.url_detail(FILTER_BACKEND, secret.pk))
        self.assertEqual(response.status_code, 404)
        with _legacy():
            response = self.client.get(self.url_detail(FILTER_BACKEND, secret.pk))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {})
        with _env(LEX_LEGACY_SECURITY="true", LEX_SEC_NOT_FOUND="true"):
            response = self.client.get(self.url_detail(FILTER_BACKEND, secret.pk))
        self.assertEqual(response.status_code, 404)

    # -- 4.83 ----------------------------------------------------------
    def test_4_83_unreadable_record_cannot_be_changed_or_deleted(self) -> None:
        """
        Scenario 4.83: PATCH and DELETE on an unreadable record answer 404, never 403.
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

    # -- 4.84 ----------------------------------------------------------
    def test_4_84_bulk_endpoints_respect_visibility(self) -> None:
        """
        Scenario 4.84: the bulk endpoint never shows or deletes unreadable rows.
        Given: a visible and a secret row
        When: both ids are fetched and deleted through /many
        Then: GET answers 200 with only the visible row — the unreadable id is
              dropped, the batch is not refused (the frontend resolves every
              foreign-key cell this way); DELETE answers 404 and deletes nothing.
        """
        visible = FilterBackendItem.objects.create(name="open")
        secret = FilterBackendItem.objects.create(name="hidden", is_secret=True)
        url = f"{self.url_many(FILTER_BACKEND)}?ids={visible.pk}&ids={secret.pk}"
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual([row.get("name") for row in response.json()], ["open"])
        self.assertEqual(self.client.delete(url).status_code, 404)
        self.assertEqual(FilterBackendItem.objects.count(), 2)


class TestCluster04o_EditEnforcement(_SecureDefaultsCase):
    """EDIT: permission_edit / permission_delete / permission_create on every write path."""

    # -- 4.85 ----------------------------------------------------------
    def test_4_85_disallowed_field_answers_400(self) -> None:
        """
        Scenario 4.85: PATCH of a field outside permission_edit is refused (BUG-010).
        Given: ProtectedItem, whose permission_edit allows only "name" for non-admins
        When: a regular user patches "secret", then "name"
        Then: "secret" answers 400 with {code: EDIT_NOT_ALLOWED, detail, error,
              fields} naming the field and the value stays; "name" answers 200
              and changes.
        """
        item = ProtectedItem.objects.create(name="p", secret="original")
        response = self.client.patch(self.url_detail(PROTECTED, item.pk), {"secret": "leaked"}, format="json")
        self.assertEqual(response.status_code, 400)
        body = response.json()
        self.assertEqual(body["code"], "EDIT_NOT_ALLOWED")
        self.assertEqual(body["fields"], {"secret": "Not editable."})
        self.assertIn("secret", body["error"])
        self.assertEqual(body["detail"], body["error"])
        item.refresh_from_db()
        self.assertEqual(item.secret, "original")
        response = self.client.patch(self.url_detail(PROTECTED, item.pk), {"name": "renamed"}, format="json")
        self.assertEqual(response.status_code, 200, response.content[:300])
        item.refresh_from_db()
        self.assertEqual(item.name, "renamed")

    # -- 4.86 ----------------------------------------------------------
    def test_4_86_unchanged_values_do_not_count(self) -> None:
        """
        Scenario 4.86: sending a disallowed field with its current value is not an edit.
        Given: the same ProtectedItem
        When: "name" changes and "secret" is sent unchanged
        Then: HTTP 200 — only changed fields are checked, as @add_permission_checks does.
        """
        item = ProtectedItem.objects.create(name="p", secret="original")
        response = self.client.patch(
            self.url_detail(PROTECTED, item.pk), {"name": "next", "secret": "original"}, format="json"
        )
        self.assertEqual(response.status_code, 200, response.content[:300])

    # -- 4.87 ----------------------------------------------------------
    def test_4_87_record_without_edit_right_refuses_every_write(self) -> None:
        """
        Scenario 4.87: a record permission_edit denies refuses any PATCH.
        Given: a readable KeycloakItem and a user without the Keycloak "edit" scope
        When: it is patched by default and with the legacy switch
        Then: by default 400 EDIT_NOT_ALLOWED; legacy applies the change.
        """
        item = KeycloakItem.objects.create(label="k")
        # Readable (so the record is found), but no edit scope.
        readable = mock.patch.object(
            KeycloakItem, "permission_read", lambda self, uc: PermissionResult.allow_all("4.87: readable")
        )
        readable.start()
        self.addCleanup(readable.stop)
        response = self.client.patch(self.url_detail(KEYCLOAK, item.pk), {"label": "changed"}, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["code"], "EDIT_NOT_ALLOWED")
        item.refresh_from_db()
        self.assertEqual(item.label, "k")
        with _legacy():
            self.client.patch(self.url_detail(KEYCLOAK, item.pk), {"label": "changed"}, format="json")
        item.refresh_from_db()
        self.assertEqual(item.label, "changed")

    # -- 4.88 ----------------------------------------------------------
    def test_4_88_bulk_patch_checks_every_row(self) -> None:
        """
        Scenario 4.88: a bulk PATCH touching a disallowed field changes nothing.
        Given: two ProtectedItems
        When: both are patched through /many with a new "secret"
        Then: HTTP 400 EDIT_NOT_ALLOWED and both secrets are unchanged.
        """
        first = ProtectedItem.objects.create(name="a", secret="s1")
        second = ProtectedItem.objects.create(name="b", secret="s2")
        url = f"{self.url_many(PROTECTED)}?ids={first.pk}&ids={second.pk}"
        response = self.client.patch(url, {"secret": "leaked"}, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["code"], "EDIT_NOT_ALLOWED")
        self.assertEqual(sorted(ProtectedItem.objects.values_list("secret", flat=True)), ["s1", "s2"])

    # -- 4.89 ----------------------------------------------------------
    def test_4_89_bulk_delete_asks_permission_delete(self) -> None:
        """
        Scenario 4.89: bulk DELETE refuses rows permission_delete refuses.
        Given: ProtectedItems a non-admin may not delete
        When: they are deleted through /many by default and with the legacy switch
        Then: by default 400 DELETE_NOT_ALLOWED listing the ids, nothing deleted;
              legacy deletes them (the old hole).
        """
        first = ProtectedItem.objects.create(name="a")
        second = ProtectedItem.objects.create(name="b")
        url = f"{self.url_many(PROTECTED)}?ids={first.pk}&ids={second.pk}"
        response = self.client.delete(url)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["code"], "DELETE_NOT_ALLOWED")
        self.assertTrue(response.json()["error"])
        self.assertEqual(sorted(response.json()["ids"]), sorted([first.pk, second.pk]))
        self.assertEqual(ProtectedItem.objects.count(), 2)
        with _legacy():
            self.client.delete(url)
        self.assertEqual(ProtectedItem.objects.count(), 0)


class _PlainReport(HTMLReport):
    def get_html(self, user):
        return "<p>numbers</p>"


class TestCluster04o_ReadSurfaces(_SecureDefaultsCase):
    """READ_SURFACES and the HTML report endpoint."""

    # -- 4.91 ----------------------------------------------------------
    def test_4_91_history_of_unreadable_record_is_not_found(self) -> None:
        """
        Scenario 4.91: the history of a record the caller may not read does not exist.
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

    # -- 4.92 ----------------------------------------------------------
    def test_4_92_global_search_shows_only_readable_rows(self) -> None:
        """
        Scenario 4.92: global search returns no row the caller may not read.
        Given: a visible and a secret row both matching "needle"
        When: the global search runs for "needle", by default and with the legacy switch
        Then: by default only the visible row is a result; legacy returns both.
        """
        FilterBackendItem.objects.create(name="needle open")
        FilterBackendItem.objects.create(name="needle hidden", is_secret=True)
        from lex.api.views.global_search_for_models.Search import Search
        from lex.model_admin.settings import lexAdminSite

        container = lexAdminSite.model_collection.get_container(FILTER_BACKEND)

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

    # -- 4.93 ----------------------------------------------------------
    def test_4_93_export_leaves_out_unreadable_rows(self) -> None:
        """
        Scenario 4.93: an export contains only rows the caller may read.
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

    # -- 4.94 ----------------------------------------------------------
    def test_4_94_file_download_checks_parameters_field_and_record(self) -> None:
        """
        Scenario 4.94: file download only serves real files of readable records.
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
        url = reverse("lex_rest_api:file-download", kwargs={"model_container": _Container(SECRET_DOCUMENT)})
        missing = self.client.get(url)
        self.assertEqual(missing.status_code, 400)
        self.assertTrue(missing.json()["error"])
        self.assertEqual(self.client.get(url, {"pk": visible.pk, "field": "name"}).status_code, 404)
        self.assertEqual(self.client.get(url, {"pk": secret.pk, "field": "attachment"}).status_code, 404)
        response = self.client.get(url, {"pk": visible.pk, "field": "attachment"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(b"".join(response.streaming_content), b"visible bytes")

    # -- 4.95 ----------------------------------------------------------
    def test_4_95_html_report_requires_sign_in(self) -> None:
        """
        Scenario 4.95: the HTML report endpoint is for signed-in callers only.
        Given: a registered report
        When: a signed-in user and an anonymous client open it, and an
              unknown report is requested
        Then: 200 with the HTML for the signed-in user, no 200 for the
              anonymous client, 404 (not 500) for the unknown report.
        """
        from lex.model_admin.settings import lexAdminSite

        lexAdminSite.html_reports["cluster4oreport"] = _PlainReport
        self.addCleanup(lexAdminSite.html_reports.pop, "cluster4oreport", None)
        url = reverse("lex_rest_api:htmlreports", kwargs={"report_name": "cluster4oreport"})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), "<p>numbers</p>")
        self.assertNotEqual(APIClient().get(url).status_code, 200)
        unknown = reverse("lex_rest_api:htmlreports", kwargs={"report_name": "nope"})
        self.assertEqual(self.client.get(unknown).status_code, 404)


class TestCluster04o_SignedOut(_SecureDefaultsCase):
    """Authentication is decided before visibility."""

    # -- 4.100 ---------------------------------------------------------
    def test_4_100_signed_out_requests_keep_the_sign_in_answer(self) -> None:
        """
        Scenario 4.100: a signed-out caller is told to sign in, not "not found".
        Given: a visible and a secret row and a client without a session
        When: it reads, patches and deletes them, reads them through /many and
              asks for their history
        Then: every answer is the sign-in answer it was before the hardening —
              401/403 from the API, or the login middleware's 302 redirect for
              a browser GET — never 404 and never 200, and nothing changes
              (the frontend's sign-in handling relies on it).
        """
        visible = FilterBackendItem.objects.create(name="open")
        secret = FilterBackendItem.objects.create(name="hidden", is_secret=True)
        anonymous = APIClient()
        for item in (visible, secret):
            detail = self.url_detail(FILTER_BACKEND, item.pk)
            responses = {
                "get": anonymous.get(detail),
                "patch": anonymous.patch(detail, {"name": "x"}, format="json"),
                "delete": anonymous.delete(detail),
                "many": anonymous.get(f"{self.url_many(FILTER_BACKEND)}?ids={item.pk}"),
                "history": anonymous.get(self.url_history(FILTER_BACKEND, item.pk)),
            }
            for what, response in responses.items():
                self.assertIn(response.status_code, (302, 401, 403), f"{item.name} {what}: {response.status_code}")
        self.assertEqual(sorted(FilterBackendItem.objects.values_list("name", flat=True)), ["hidden", "open"])


class TestCluster04o_ReadOncePerRow(_SecureDefaultsCase):
    """BUG-011: one permission_read per row and request."""

    # -- 4.96 ----------------------------------------------------------
    def test_4_96_permission_read_runs_once_per_row(self) -> None:
        """
        Scenario 4.96: listing evaluates permission_read once per row, not twice.
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


class TestCluster04o_ApiKeyReadContext(E2ETestCase):
    """The serializer decides an API-key read with the key's own context."""

    e2e_models = ALL_MODELS

    # -- 4.97 ----------------------------------------------------------
    def test_4_97_classic_key_read_decided_by_serializer(self) -> None:
        """
        Scenario 4.97: a classic key reads a record the serializer decides.
        Given: a KeycloakItem (default permissions: the "read" scope), a classic
               API key (all scopes, technical identity) and
               LEX_SEC_NOT_FOUND=false, so no visibility answer is remembered
        When: the key reads the record by id
        Then: 200 with the record's label, not an empty body — re-resolving the
              record's Keycloak scopes would leave the key with none.
        """
        item = KeycloakItem.objects.create(label="for-the-key")
        self.authenticate_as_api_key(name="cluster 4o")
        with _env(LEX_SEC_NOT_FOUND="false"):
            response = self.client.get(self.url_detail(KEYCLOAK, item.pk))
        self.assertEqual(response.status_code, 200, response.content[:300])
        self.assertEqual(response.json().get("label"), "for-the-key")


class TestCluster04o_SecurityFlags(SimpleTestCase):
    """lex.core.security_flags.strict: defaults, umbrella switch, per-flag override."""

    # -- 4.98 ----------------------------------------------------------
    def test_4_98_flag_resolution(self) -> None:
        """
        Scenario 4.98: every flag is on by default; the umbrella turns all off;
        a per-flag setting wins over the umbrella; unknown names are an error.
        Given: the nine flags
        When: strict() is asked with no setting, LEX_LEGACY_SECURITY=true,
              LEX_LEGACY_SECURITY=true + LEX_SEC_EDIT=true, LEX_SEC_FILTERS=false
        Then: all True; all False; only EDIT True; only FILTERS False;
              strict("NOPE") raises KeyError.
        """
        self.assertEqual(
            set(FLAGS),
            {"FAIL_CLOSED", "NOT_FOUND", "READ_SURFACES", "EDIT", "FILTERS", "TRACEBACKS",
             "OIDC_REDIRECT", "INTERNAL", "HISTORY_WRITE"},
        )
        with _env():
            self.assertTrue(all(strict(flag) for flag in FLAGS))
        with _legacy():
            self.assertFalse(any(strict(flag) for flag in FLAGS))
        with _env(LEX_LEGACY_SECURITY="true", LEX_SEC_EDIT="true"):
            self.assertEqual([flag for flag in FLAGS if strict(flag)], ["EDIT"])
        with _env(LEX_SEC_FILTERS="false"):
            self.assertEqual([flag for flag in FLAGS if not strict(flag)], ["FILTERS"])
        with self.assertRaises(KeyError):
            strict("NOPE")


class TestCluster04o_SuperuserOrAdmin(TransactionTestCase):
    """lex.core.security_flags.is_superuser_or_admin."""

    # -- 4.99 ----------------------------------------------------------
    def test_4_99_superuser_or_admin_group(self) -> None:
        """
        Scenario 4.99: the helper admits superusers and members of "admin" only.
        Given: a plain user, a member of "admin", a superuser and an anonymous caller
        When: is_superuser_or_admin is asked for each
        Then: False, True, True, False.
        """
        factory = RequestFactory()

        def request_for(user):
            request = factory.get("/")
            request.user = user
            return request

        plain = User.objects.create_user(username="c4o-plain", password="pw")
        member = User.objects.create_user(username="c4o-admin", password="pw")
        member.groups.add(Group.objects.get_or_create(name="admin")[0])
        root = User.objects.create_superuser(username="c4o-root", password="pw", email="r@test.local")
        self.assertFalse(is_superuser_or_admin(request_for(plain)))
        self.assertTrue(is_superuser_or_admin(request_for(member)))
        self.assertTrue(is_superuser_or_admin(request_for(root)))
        self.assertFalse(is_superuser_or_admin(request_for(AnonymousUser())))
