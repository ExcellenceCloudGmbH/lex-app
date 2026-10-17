"""Every error of a generic endpoint reaches the exception handler.

Intent
------
A project that installs its own ``EXCEPTION_HANDLER`` (for example to keep
one error shape for its clients) must see every error of lex's generic
endpoints, not only the ones DRF happens to raise. Since 3.0
(``LEX_SEC_ERRORS``) the generic views raise their refusals — create and
delete denied, unknown or read-only fields, field errors, history and bulk
"not found" — instead of returning bodies past the handler; lex's default
handler renders them with every key of the old body plus ``code`` and
``detail``. A typed error raised in model code (``LexActionError``, a DRF
error, a database error the project maps) keeps its own status and code
instead of becoming a 500. ``LEX_SEC_ERRORS=false`` restores the old bodies.

Cluster 10v — scenarios 10.122–10.127. Type: E.
Covers: lex/api/errors.py (error_from_body, raise_or_return, surface),
lex/api/views/model_entries/One.py, Many.py, History.py,
lex/api/views/model_entries/mixins/DestroyOneWithPayloadMixin.py,
lex/api/views/process_flow/CreateOrUpdate.py, lex/api/views/file_operations/ModelExport.py (_no_data),
lex/process_admin/views/model_relation_views.py, lex/api/views/model_entries/mixins/ModelEntryProviderMixin.py
(lex_maps_db_errors), lex/core/security_flags.py (ERRORS).
Run: python -m lex pytest lex/test_project/tests/api_layer/test_10v_errors_reach_the_handler.py -v
"""

from __future__ import annotations

import os
from unittest import mock

import pytest
from django.db import DatabaseError
from django.test import override_settings
from django.urls import reverse
from rest_framework.response import Response

from lex.api.errors import LexActionError
from lex.test_project.tests._e2e_test_case import E2ETestCase, _MockContainer

from .models import API_PROJECT_ITEM, PROJECT_MODELS, ApiProjectItem

pytestmark = pytest.mark.api_layer

_SWITCHES = ("LEX_SEC_", "LEX_LEGACY_SECURITY", "LEX_API_TRACEBACKS", "LEX_ERROR_FORMAT")


def _env(**extra):
    clean = {k: v for k, v in os.environ.items() if not k.startswith(_SWITCHES)}
    clean.update(extra)
    return mock.patch.dict(os.environ, clean, clear=True)


def recording_handler(exc, context):
    """A project handler: answers 418 and names what it was given."""
    code = getattr(exc, "code", None) or type(exc).__name__
    return Response({"seen": type(exc).__name__, "code": str(code)}, status=418)


def map_trigger_errors(exc):
    if "trigger said no" in str(exc):
        return LexActionError("The database refused this.", code="TRIGGER_REFUSED", status_code=409)
    return None


def _many_url():
    return reverse("process_admin_rest_api:model-many-entries", kwargs={"model_container": _MockContainer(API_PROJECT_ITEM)})


def _history_url(pk):
    return reverse(
        "process_admin_rest_api:model-history-list",
        kwargs={"model_container": _MockContainer(API_PROJECT_ITEM), "calculationId": "c", "pk": pk},
    )


@override_settings(DEBUG=False)
class TestCluster10v_ErrorsReachTheHandler(E2ETestCase):
    """Cluster 10v: generic endpoints raise their errors."""

    e2e_models = PROJECT_MODELS

    # -- 10.122 --------------------------------------------------------
    def test_10_122_project_handler_sees_every_refusal(self) -> None:
        """
        Scenario 10.122: the project's handler renders every refusal of the generic API.
        Given: EXCEPTION_HANDLER answering 418; a strict-input model; a hidden row
        When: a create without the required field, with an unknown field, a refused create,
              a refused edit, a refused delete, history and bulk delete of a hidden row
        Then: every answer is the handler's 418, carrying the refusal's code.
        """
        self.use_lex_extensions(EXCEPTION_HANDLER=f"{__name__}.recording_handler")
        row = ApiProjectItem.objects.create(name="visible")
        hidden = ApiProjectItem.objects.create(name="hidden")
        cases = {}
        with _env():
            cases["missing field"] = self.client.post(self.url_create(API_PROJECT_ITEM), {}, format="json")
            with mock.patch.object(ApiProjectItem, "lex_strict_input", True, create=True):
                cases["unknown field"] = self.client.post(
                    self.url_create(API_PROJECT_ITEM), {"name": "x", "nope": 1}, format="json"
                )
            with mock.patch.object(ApiProjectItem, "permission_create", lambda self, uc: False):
                cases["create refused"] = self.client.post(self.url_create(API_PROJECT_ITEM), {"name": "y"}, format="json")
            from lex.core.models.LexModel import PermissionResult

            with mock.patch.object(ApiProjectItem, "permission_edit", lambda self, uc: PermissionResult.deny("no")):
                cases["edit refused"] = self.client.patch(self.url_detail(API_PROJECT_ITEM, row.pk), {"name": "z"}, format="json")
            with mock.patch.object(ApiProjectItem, "permission_delete", lambda self, uc: False):
                cases["delete refused"] = self.client.delete(self.url_detail(API_PROJECT_ITEM, row.pk))
            cases["history hidden"] = self.client.get(_history_url(hidden.pk))
            cases["bulk hidden"] = self.client.delete(_many_url() + f"?ids={hidden.pk}")
        expected = {
            "missing field": "ValidationError",
            "unknown field": "LexActionError",
            "create refused": "LexActionError",
            "edit refused": "LexActionError",
            "delete refused": "LexActionError",
            "history hidden": "LexActionError",
            "bulk hidden": "LexActionError",
        }
        for name, response in cases.items():
            self.assertEqual(response.status_code, 418, (name, response.content[:300]))
            self.assertEqual(response.json()["seen"], expected[name], name)
        self.assertEqual(cases["unknown field"].json()["code"], "UNKNOWN_FIELDS")
        self.assertEqual(cases["edit refused"].json()["code"], "EDIT_NOT_ALLOWED")
        self.assertEqual(cases["create refused"].json()["code"], "PERMISSION_DENIED")
        self.assertTrue(ApiProjectItem.objects.filter(pk=row.pk, name="visible").exists())

    # -- 10.123 --------------------------------------------------------
    def test_10_123_default_bodies_keep_their_old_keys(self) -> None:
        """
        Scenario 10.123: without a project handler the bodies stay readable by old clients.
        Given: lex's own handler
        When: a refused create and a refused bulk delete
        Then: 403 with the old "message" plus code PERMISSION_DENIED and detail; 400 with
              the refused "ids" plus code DELETE_NOT_ALLOWED.
        """
        row = ApiProjectItem.objects.create(name="kept")
        with _env():
            with mock.patch.object(ApiProjectItem, "permission_create", lambda self, uc: False):
                created = self.client.post(self.url_create(API_PROJECT_ITEM), {"name": "y"}, format="json")
            with mock.patch.object(ApiProjectItem, "permission_delete", lambda self, uc: False):
                bulk = self.client.delete(_many_url() + f"?ids={row.pk}")
        self.assertEqual(created.status_code, 403)
        body = created.json()
        self.assertEqual(body["code"], "PERMISSION_DENIED")
        self.assertIn("not authorized to create", body["message"])
        self.assertEqual(body["detail"], body["message"])
        self.assertEqual(bulk.status_code, 400)
        self.assertEqual((bulk.json()["code"], bulk.json()["ids"]), ("DELETE_NOT_ALLOWED", [row.pk]))

    # -- 10.124 --------------------------------------------------------
    def test_10_124_typed_errors_from_model_code_keep_their_status(self) -> None:
        """
        Scenario 10.124: a business error raised while saving is not a 500.
        Given: save() raising LexActionError(409, code NOT_NOW)
        When: the row is created through the generic endpoint and through run_step, and updated
        Then: each answers 409 {code: NOT_NOW}, and nothing is stored.
        """
        row = ApiProjectItem.objects.create(name="existing")
        error = LexActionError("Not now.", code="NOT_NOW", status_code=409)
        with _env(), mock.patch.object(ApiProjectItem, "save", side_effect=error):
            responses = [
                self.client.post(self.url_create(API_PROJECT_ITEM), {"name": "new"}, format="json"),
                self.client.patch(self.url_detail(API_PROJECT_ITEM, row.pk), {"name": "changed"}, format="json"),
                self.client.patch(
                    reverse("process_admin_rest_api:run_step",
                            kwargs={"model_container": _MockContainer(API_PROJECT_ITEM), "pk": 999999}),
                    {"name": "new"}, format="json",
                ),
            ]
        for response in responses:
            self.assertEqual(response.status_code, 409, response.content[:300])
            self.assertEqual(response.json()["code"], "NOT_NOW")
        self.assertEqual(list(ApiProjectItem.objects.values_list("name", flat=True)), ["existing"])

    # -- 10.125 --------------------------------------------------------
    def test_10_125_database_errors_reach_the_mapper_and_the_handler(self) -> None:
        """
        Scenario 10.125: a database error is the project's to answer.
        Given: save() raising DatabaseError("trigger said no")
        When: it is created with DB_ERROR_MAPPER, then with a project EXCEPTION_HANDLER
        Then: 409 TRIGGER_REFUSED from the mapper; the handler receives the DatabaseError itself.
        """
        from lex.core.extensions import override_extensions

        with _env(), mock.patch.object(ApiProjectItem, "save", side_effect=DatabaseError("trigger said no")):
            with override_extensions(DB_ERROR_MAPPER=f"{__name__}.map_trigger_errors"):
                mapped = self.client.post(self.url_create(API_PROJECT_ITEM), {"name": "new"}, format="json")
            with override_extensions(EXCEPTION_HANDLER=f"{__name__}.recording_handler"):
                handled = self.client.post(self.url_create(API_PROJECT_ITEM), {"name": "new"}, format="json")
        self.assertEqual((mapped.status_code, mapped.json()["code"]), (409, "TRIGGER_REFUSED"))
        self.assertEqual((handled.status_code, handled.json()["seen"]), (418, "DatabaseError"))

    # -- 10.126 --------------------------------------------------------
    def test_10_126_opt_out_keeps_the_old_answers(self) -> None:
        """
        Scenario 10.126: LEX_SEC_ERRORS=false restores the pre-3.0 behaviour.
        Given: a project handler, a refused create, a save raising LexActionError
        When: LEX_SEC_ERRORS=false
        Then: the refused create returns its old body past the handler (403, no code);
              the business error is wrapped into a 500 again.
        """
        error = LexActionError("Not now.", code="NOT_NOW", status_code=409)
        with _env(LEX_SEC_ERRORS="false"), mock.patch.object(ApiProjectItem, "save", side_effect=error):
            wrapped = self.client.post(self.url_create(API_PROJECT_ITEM), {"name": "new"}, format="json")
        self.assertEqual(wrapped.status_code, 500)
        self.use_lex_extensions(EXCEPTION_HANDLER=f"{__name__}.recording_handler")
        with _env(LEX_SEC_ERRORS="false"):
            with mock.patch.object(ApiProjectItem, "permission_create", lambda self, uc: False):
                refused = self.client.post(self.url_create(API_PROJECT_ITEM), {"name": "y"}, format="json")
        self.assertEqual(refused.status_code, 403)
        self.assertNotIn("code", refused.json())


@override_settings(DEBUG=False)
class TestCluster10v_ExportAndReports(E2ETestCase):
    """Cluster 10v: export and report endpoints raise their "not found" too."""

    from lex.test_project.tests.exports.models import ALL_MODELS as e2e_models  # noqa: N815

    # -- 10.127 --------------------------------------------------------
    def test_10_127_empty_export_and_unknown_report_reach_the_handler(self) -> None:
        """
        Scenario 10.127: "nothing to export" and an unknown report are the handler's to answer.
        Given: an empty export model and EXCEPTION_HANDLER answering 418
        When: the model is exported and a report that does not exist is opened
        Then: both answer the handler's 418, with codes NO_DATA and NOT_FOUND;
              without the handler the export still answers 404 with its "error";
              the generic views map database errors (lex_maps_db_errors).
        """
        from lex.api.views.model_entries.mixins.ModelEntryProviderMixin import ModelEntryProviderMixin
        from lex.core.extensions import override_extensions
        from lex.test_project.tests.exports.models import ITEM

        self.assertTrue(ModelEntryProviderMixin.lex_maps_db_errors)
        report = reverse("process_admin_rest_api:htmlreports", kwargs={"report_name": "nope"})
        with _env():
            plain = self.client.post(self.url_export(ITEM), data={}, format="json")
            with override_extensions(EXCEPTION_HANDLER=f"{__name__}.recording_handler"):
                export = self.client.post(self.url_export(ITEM), data={}, format="json")
                missing = self.client.get(report)
        self.assertEqual((plain.status_code, plain.json()["error"]), (404, "No data available for export"))
        self.assertEqual((export.status_code, export.json()["code"]), (418, "NO_DATA"))
        self.assertEqual((missing.status_code, missing.json()["code"]), (418, "NOT_FOUND"))

