"""Internal tools, Django's users, permission_list and history tables (v2 security hardening).

Intent
------
Some of lex's endpoints are operator tools, not record surfaces, and were open
to every signed-in user. With ``LEX_SEC_INTERNAL`` (on by default):

* ``api/delete-unused-files`` is for superusers and the Keycloak client role
  ``admin``; for everyone else it does not exist (404).
* ``POST api/logs`` (writing to the server log) is for superusers only (404).
* The calculation-log tree and its markdown PDF show only logs of records the
  caller may read; logs that belong to no record are operator data.
  Superusers see every log.
* Django's users, groups and permissions have no permission hooks; only a
  superuser may change them through the generic API (400 ``EDIT_NOT_ALLOWED``).

A model's own ``permission_list`` is honoured by the list endpoint: refused →
404 (``LEX_SEC_NOT_FOUND``).

History tables (``Historical*``, ``MetaHistorical*``) are the record of what
was true and when. With ``LEX_SEC_HISTORY_WRITE`` only a superuser may write
them: PATCH, PUT, POST, DELETE and bulk PATCH/DELETE answer 400
``EDIT_NOT_ALLOWED`` for everyone else (never 403 — the bundled frontend signs
out on 403). ``LEX_LEGACY_SECURITY=true`` / ``LEX_SEC_<FLAG>=false`` restore
the old behaviour.

Cluster 4p — scenarios 4.101–4.110. Type: E.
Covers: lex/core/security_flags.py (superuser_only, is_superuser),
lex/api/security.py (write_denial, list_denied, is_history_model),
lex/api/views/sharepoint/DeleteUnusedFiles.py,
lex/api/views/LexLoggerView/LexLoggerView.py,
lex/api/views/model_entries/CalculationLogTreeView.py,
lex/api/views/calculations/DownloadMarkdownPdf.py,
lex/api/views/model_entries/{One,Many,List}.py,
lex/api/views/model_entries/mixins/DestroyOneWithPayloadMixin.py.
Run: python -m lex pytest lex/test_project/tests/permissions/test_4p_internal_tools_and_history.py -v
"""

from __future__ import annotations

import os
from unittest import mock

import pytest
from django.contrib.auth.models import Group, User
from django.contrib.contenttypes.models import ContentType
from django.urls import reverse

from lex.audit_logging.models.CalculationLog import CalculationLog
from lex.test_project.tests._e2e_test_case import E2ETestCase

from .models import (
    PROTECTED,
    SECURE_DEFAULT_MODELS,
    FilterBackendItem,
    ProtectedItem,
)

pytestmark = pytest.mark.permissions


def _env(**extra):
    clean = {k: v for k, v in os.environ.items() if not k.startswith(("LEX_SEC_", "LEX_LEGACY_SECURITY"))}
    clean.update(extra)
    return mock.patch.dict(os.environ, clean, clear=True)


def _legacy():
    return _env(LEX_LEGACY_SECURITY="true")


class _HardeningCase(E2ETestCase):
    e2e_models = SECURE_DEFAULT_MODELS
    e2e_framework_models = [CalculationLog]

    def setUp(self) -> None:
        super().setUp()
        self._defaults = _env()
        self._defaults.start()
        self.addCleanup(self._defaults.stop)
        CalculationLog.objects.all().delete()

    def make_superuser(self) -> None:
        self.user.is_superuser = True
        self.user.save(update_fields=["is_superuser"])


class TestCluster04p_InternalTools(_HardeningCase):
    """LEX_SEC_INTERNAL: operator endpoints."""

    # -- 4.101 ---------------------------------------------------------
    def test_4_101_delete_unused_files_is_for_superusers_and_admins(self) -> None:
        """
        Scenario 4.101: deleting unused storage files is an operator tool.
        Given: the delete-unused-files endpoint (storage access stubbed)
        When: a plain user, a member of the Keycloak client role "admin" and a
              superuser call it, and the plain user again with the legacy switch
        Then: 404 for the plain user (the endpoint does not exist for them) and
              the cleanup never runs; the admin and the superuser reach it (200);
              legacy lets the plain user through as before.
        """
        url = reverse("lex_rest_api:delete-unused-files")
        with mock.patch(
            "lex.api.views.sharepoint.DeleteUnusedFiles.DeleteUnusedFiles.cleanup_unused_files",
            return_value={"status": "success", "dry_run": True},
        ) as cleanup:
            response = self.client.post(url, {"dry_run": True}, format="json")
            self.assertEqual(response.status_code, 404, response.content[:300])
            cleanup.assert_not_called()
            with _legacy():
                self.assertEqual(self.client.post(url, {"dry_run": True}, format="json").status_code, 200)
            self.user.groups.add(Group.objects.get_or_create(name="admin")[0])
            self.assertEqual(self.client.post(url, {"dry_run": True}, format="json").status_code, 200)
            self.user.groups.clear()
            self.make_superuser()
            self.assertEqual(self.client.post(url, {"dry_run": True}, format="json").status_code, 200)
        self.assertEqual(cleanup.call_count, 3, "the cleanup runs only for the three allowed calls")

    # -- 4.102 ---------------------------------------------------------
    def test_4_102_posting_to_the_server_log_is_for_superusers(self) -> None:
        """
        Scenario 4.102: writing to the server log needs a superuser.
        Given: POST api/logs
        When: a plain user posts a message, with and without the legacy switch,
              and a superuser posts one
        Then: 404 with a readable error and nothing logged for the plain user;
              legacy and the superuser get 201 and the message reaches LexLogger.
        """
        url = reverse("lex_rest_api:log")
        with mock.patch("lex.api.views.LexLoggerView.LexLoggerView.LexLogger") as logger_class:
            response = self.client.post(url, {"message": "forged", "level": "ERROR"})
            self.assertEqual(response.status_code, 404)
            self.assertTrue(response.json()["error"])
            logger_class.assert_not_called()
            with _legacy():
                self.assertEqual(self.client.post(url, {"message": "legacy"}).status_code, 201)
            self.make_superuser()
            self.assertEqual(self.client.post(url, {"message": "operator"}).status_code, 201)
        self.assertEqual(logger_class.call_count, 2)

    def _calculation_logs(self):
        kind = ContentType.objects.get_for_model(FilterBackendItem)
        readable = FilterBackendItem.objects.create(name="seen")
        hidden = FilterBackendItem.objects.create(name="hidden", is_secret=True)
        seen = CalculationLog.objects.create(
            calculationId="c4p-seen", content_type=kind, object_id=readable.pk, calculation_log="# seen"
        )
        secret = CalculationLog.objects.create(
            calculationId="c4p-hidden", content_type=kind, object_id=hidden.pk, calculation_log="# hidden"
        )
        orphan = CalculationLog.objects.create(calculationId="c4p-none", calculation_log="# no record")
        return seen, secret, orphan

    # -- 4.103 ---------------------------------------------------------
    def test_4_103_log_tree_shows_logs_of_readable_records_only(self) -> None:
        """
        Scenario 4.103: the calculation-log tree shows only what the caller may read.
        Given: a calculation on a readable row, one on a row hidden from the user,
               and one that belongs to no record
        When: the user asks for each tree, and for the tree without a filter;
              then a superuser asks; then the user with the legacy switch
        Then: the readable calculation's log only (others and the unfiltered tree
              are empty); the superuser sees all three; legacy shows all three.
        """
        self._calculation_logs()
        url = reverse("lex_rest_api:calculationlog-tree")

        def total(**params):
            response = self.client.get(url, params)
            self.assertEqual(response.status_code, 200, response.content[:300])
            return response.json()["pagination"]["total"]

        self.assertEqual(total(calculation_id="c4p-seen"), 1)
        self.assertEqual(total(calculation_id="c4p-hidden"), 0)
        self.assertEqual(total(calculation_id="c4p-none"), 0)
        self.assertEqual(total(), 0)
        with _legacy():
            self.assertEqual(total(), 3)
        self.make_superuser()
        self.assertEqual(total(), 3)
        self.assertEqual(total(calculation_id="c4p-none"), 1)

    # -- 4.104 ---------------------------------------------------------
    def test_4_104_log_pdf_follows_the_tree(self) -> None:
        """
        Scenario 4.104: the markdown-PDF download applies the tree's rule.
        Given: the same three logs (PDF rendering stubbed)
        When: the user downloads each, then a superuser downloads the record-less one
        Then: 200 for the readable record's log; 404 for the hidden record's and the
              record-less log, and for an id that does not exist; the superuser gets 200.
        """
        seen, secret, orphan = self._calculation_logs()

        def pdf(pk):
            url = reverse("lex_rest_api:download-markdown-pdf", kwargs={"pk": pk})
            with mock.patch("lex.api.views.calculations.DownloadMarkdownPdf.render_pdf_bytes", return_value=b"%PDF-"):
                return self.client.get(url).status_code

        self.assertEqual([pdf(seen.pk), pdf(secret.pk), pdf(orphan.pk), pdf(999999)], [200, 404, 404, 404])
        self.make_superuser()
        self.assertEqual(pdf(orphan.pk), 200)

    # -- 4.105 ---------------------------------------------------------
    def test_4_105_django_users_are_changed_by_superusers_only(self) -> None:
        """
        Scenario 4.105: editing auth.User through the generic API needs a superuser.
        Given: another user and a signed-in non-superuser
        When: the user patches the other user's e-mail and superuser flag, creates
              a user through the create endpoint and deletes the other user
              through /many
        Then: each answers 400 EDIT_NOT_ALLOWED with a readable error and nothing
              changes; a superuser may change the e-mail (200).
        """
        other = User.objects.create_user(username="c4p-victim", email="victim@test.local")
        url = self.url_detail("user", other.pk)
        response = self.client.patch(url, {"email": "attacker@test.local", "is_superuser": True}, format="json")
        self.assertEqual(response.status_code, 400, response.content[:300])
        self.assertEqual(response.json()["code"], "EDIT_NOT_ALLOWED")
        self.assertTrue(response.json()["error"])
        created = self.client.post(self.url_create("user"), {"username": "c4p-new"}, format="json")
        self.assertEqual(created.status_code, 400, created.content[:300])
        self.assertEqual(created.json()["code"], "EDIT_NOT_ALLOWED")
        deleted = self.client.delete(f"{self.url_many('user')}?ids={other.pk}")
        self.assertEqual(deleted.status_code, 400, deleted.content[:300])
        other.refresh_from_db()
        self.assertEqual((other.email, other.is_superuser), ("victim@test.local", False))
        self.assertFalse(User.objects.filter(username="c4p-new").exists())
        self.make_superuser()
        response = self.client.patch(url, {"email": "ops@test.local"}, format="json")
        self.assertEqual(response.status_code, 200, response.content[:300])
        other.refresh_from_db()
        self.assertEqual(other.email, "ops@test.local")


class TestCluster04p_PermissionList(_HardeningCase):
    """A model's own permission_list decides whether its list exists."""

    # -- 4.106 ---------------------------------------------------------
    def test_4_106_refusing_permission_list_hides_the_list(self) -> None:
        """
        Scenario 4.106: permission_list false → the list endpoint answers 404.
        Given: ProtectedItem whose permission_list refuses this user
        When: the list is requested by default, with the legacy switch, and with
              the hook allowing again
        Then: 404; legacy 200 (the hook was ignored before); allowing 200.
        """
        ProtectedItem.objects.create(name="listed")
        with mock.patch.object(ProtectedItem, "permission_list", lambda self, uc: False):
            self.assertEqual(self.client.get(self.url_list(PROTECTED)).status_code, 404)
            with _legacy():
                self.assertEqual(self.client.get(self.url_list(PROTECTED)).status_code, 200)
        self.assertEqual(self.client.get(self.url_list(PROTECTED)).status_code, 200)


class TestCluster04p_HistoryWrites(_HardeningCase):
    """LEX_SEC_HISTORY_WRITE: history tables are written by superusers only."""

    def _history(self):
        item = ProtectedItem.objects.create(name="v1")
        item.name = "v2"
        item.save()
        history_model = ProtectedItem.history.model
        row = history_model.objects.filter(id=item.pk).order_by("history_id").first()
        return item, history_model, row

    def _names(self, history_model):
        return sorted(history_model.objects.values_list("name", flat=True))

    # -- 4.107 ---------------------------------------------------------
    def test_4_107_patch_and_put_on_history_rows_are_refused(self) -> None:
        """
        Scenario 4.107: a non-superuser cannot correct a history row.
        Given: a ProtectedItem with two history rows, readable by the user (ProtectedItem: name editable)
        When: the user PATCHes and PUTs the first history row through the
              single-record endpoint
        Then: 400 EDIT_NOT_ALLOWED with a readable error each time and the history
              is unchanged; a superuser's PATCH is accepted (200).
        """
        item, history_model, row = self._history()
        name = history_model._meta.model_name
        before = self._names(history_model)
        url = self.url_detail(name, row.history_id)
        for method in (self.client.patch, self.client.put):
            response = method(url, {"name": "rewritten"}, format="json")
            self.assertEqual(response.status_code, 400, response.content[:300])
            self.assertEqual(response.json()["code"], "EDIT_NOT_ALLOWED")
            self.assertIn("superuser", response.json()["error"])
        self.assertEqual(self._names(history_model), before)
        self.make_superuser()
        response = self.client.patch(url, {"name": "corrected"}, format="json")
        self.assertEqual(response.status_code, 200, response.content[:300])

    # -- 4.108 ---------------------------------------------------------
    def test_4_108_every_other_write_path_is_refused(self) -> None:
        """
        Scenario 4.108: delete, create and bulk refuse history writes too.
        Given: the same history rows and their meta-history
        When: the user DELETEs one, POSTs a new one, bulk-PATCHes and bulk-DELETEs
              them and PATCHes a meta-history row
        Then: every answer is 400 EDIT_NOT_ALLOWED (never 403) and no history row
              changed, appeared or disappeared.
        """
        item, history_model, row = self._history()
        name = history_model._meta.model_name
        before = self._names(history_model)
        many = f"{self.url_many(name)}?ids={row.history_id}"
        answers = {
            "delete": self.client.delete(self.url_detail(name, row.history_id)),
            "create": self.client.post(self.url_create(name), {"name": "forged", "id": item.pk}, format="json"),
            "bulk patch": self.client.patch(many, {"name": "rewritten"}, format="json"),
            "bulk delete": self.client.delete(many),
        }
        meta_model = history_model.meta_history.model
        meta_row = meta_model.objects.first()
        if meta_row is not None:
            answers["meta patch"] = self.client.patch(
                self.url_detail(meta_model._meta.model_name, meta_row.pk), {"name": "x"}, format="json"
            )
        for what, response in answers.items():
            self.assertEqual(response.status_code, 400, f"{what}: {response.status_code} {response.content[:200]}")
            self.assertEqual(response.json()["code"], "EDIT_NOT_ALLOWED", what)
        self.assertEqual(self._names(history_model), before)

    # -- 4.109 ---------------------------------------------------------
    def test_4_109_opt_out_restores_bitemporal_correction_for_everyone(self) -> None:
        """
        Scenario 4.109: LEX_SEC_HISTORY_WRITE=false keeps the old behaviour.
        Given: the same history rows
        When: the user PATCHes the first one with the flag off
        Then: the correction is applied (200) — the pre-hardening "bitemporal
              correction for anyone".
        """
        item, history_model, row = self._history()
        with _env(LEX_SEC_HISTORY_WRITE="false"):
            response = self.client.patch(
                self.url_detail(history_model._meta.model_name, row.history_id), {"name": "corrected"}, format="json"
            )
        self.assertEqual(response.status_code, 200, response.content[:300])
        self.assertIn("corrected", self._names(history_model))

    # -- 4.110 ---------------------------------------------------------
    def test_4_110_main_records_are_unaffected(self) -> None:
        """
        Scenario 4.110: the history guard only concerns history tables.
        Given: a ProtectedItem the user may edit
        When: the user PATCHes the record itself
        Then: 200 and the change lands (and is itself recorded in history).
        """
        item, history_model, _ = self._history()
        response = self.client.patch(self.url_detail(PROTECTED, item.pk), {"name": "v3"}, format="json")
        self.assertEqual(response.status_code, 200, response.content[:300])
        item.refresh_from_db()
        self.assertEqual(item.name, "v3")
