"""Error bodies: no server tracebacks by default, a stable shape on request.

Intent
------
Until 3.0 an unexpected error in the generic CRUD endpoints answered 500 with
the full server traceback. Since 3.0 the traceback appears only when
``DEBUG`` is on, ``LEX_API_TRACEBACKS=true`` or the legacy opt-out is set;
the rest of the body (``error``/``message``) is unchanged, because the
bundled frontend reads it. DRF's own bodies (field errors) stay exactly as
they were unless a project asks for the structured format
(``LEX_ERROR_FORMAT=structured``): ``{code, detail, error, fields}``.
Project endpoints always use that format, can map database errors to
business codes (``lex_config.DB_ERROR_MAPPER``) and can replace the handler
entirely (``lex_config.EXCEPTION_HANDLER``).

Cluster 10p — scenarios 10.92–10.97. Type: E.
Covers: lex/api/errors.py, lex/api/views/model_entries/One.py (error bodies),
REST_FRAMEWORK["EXCEPTION_HANDLER"] in lex/lex_app/settings.py.
Run: python -m lex pytest lex/test_project/tests/api_layer/test_10p_error_contract.py -v
"""

from __future__ import annotations

import os
from unittest import mock

import pytest
from django.test import override_settings
from rest_framework.response import Response

from lex.api.errors import LexActionError
from lex.test_project.tests._e2e_test_case import E2ETestCase

from .models import API_PROJECT_ITEM, PROJECT_MODELS

pytestmark = pytest.mark.api_layer

PREFIX = "/api/cluster10/"
PROJECT_API = {"API_URLCONF": "lex.test_project.tests.api_layer._project_api", "API_PREFIX": "api/cluster10/"}

_SWITCHES = ("LEX_SEC_", "LEX_LEGACY_SECURITY", "LEX_API_TRACEBACKS", "LEX_ERROR_FORMAT")


def _env(**extra):
    clean = {k: v for k, v in os.environ.items() if not k.startswith(_SWITCHES)}
    clean.update(extra)
    return mock.patch.dict(os.environ, clean, clear=True)


def map_trigger_errors(exc):
    if "trigger said no" in str(exc):
        return LexActionError("The database refused this.", code="TRIGGER_REFUSED", status_code=409)
    return None


def replacing_handler(exc, context):
    return Response({"replaced": True, "type": type(exc).__name__}, status=418)


@override_settings(DEBUG=False)
class TestCluster10p_ErrorContract(E2ETestCase):
    """Cluster 10p: what error responses contain."""

    e2e_models = PROJECT_MODELS

    def _create_exploding_row(self):
        return self.client.post(self.url_create(API_PROJECT_ITEM), {"name": "explode"}, format="json")

    # -- 10.92 ---------------------------------------------------------
    def test_10_92_server_errors_carry_no_traceback_by_default(self) -> None:
        """
        Scenario 10.92: a failing create answers 500 without a traceback.
        Given: DEBUG off, default security flags, a model whose save() raises
        When: the row is created through the generic CRUD endpoint
        Then: HTTP 500, the body still names the error under "error",
              and it contains no "traceback" — on the CRUD endpoint and on
              run_step (CreateOrUpdate) alike.
        """
        from django.urls import reverse

        from lex.test_project.tests._e2e_test_case import _MockContainer

        with _env():
            response = self._create_exploding_row()
            run_step = self.client.patch(
                reverse("process_admin_rest_api:run_step",
                        kwargs={"model_container": _MockContainer(API_PROJECT_ITEM), "pk": 999999}),
                {"name": "explode"}, format="json",
            )
        self.assertEqual(response.status_code, 500)
        body = response.json()
        self.assertIn("save failed on purpose", body["error"])
        self.assertNotIn("traceback", body)
        self.assertEqual(run_step.status_code, 500)
        self.assertIn("save failed on purpose", run_step.json()["error"])
        self.assertNotIn("traceback", run_step.json())

    # -- 10.93 ---------------------------------------------------------
    def test_10_93_tracebacks_return_when_allowed(self) -> None:
        """
        Scenario 10.93: the traceback comes back when explicitly allowed.
        Given: the same failing create
        When: LEX_API_TRACEBACKS=true, then LEX_LEGACY_SECURITY=true, then DEBUG on
        Then: each time the body contains a non-empty "traceback" naming the error.
        """
        for env in ({"LEX_API_TRACEBACKS": "true"}, {"LEX_LEGACY_SECURITY": "true"}):
            with _env(**env):
                body = self._create_exploding_row().json()
            self.assertIn("RuntimeError", body.get("traceback", ""), env)
        with _env(), override_settings(DEBUG=True):
            self.assertIn("traceback", self._create_exploding_row().json())

    # -- 10.94 ---------------------------------------------------------
    def test_10_94_drf_bodies_unchanged_by_default(self) -> None:
        """
        Scenario 10.94: field errors keep DRF's shape unless structured format is asked for.
        Given: a generic create without the required "name", and a project
               endpoint with invalid input
        When: they are sent with default settings, then the generic create with
              LEX_ERROR_FORMAT=structured
        Then: by default the generic body maps "name" to messages and has no
              "code"; the project endpoint always answers {code: VALIDATION_ERROR,
              detail, error, fields}; in structured mode the generic body does too.
        """
        self.use_lex_extensions(**PROJECT_API)
        with _env():
            response = self.client.post(self.url_create(API_PROJECT_ITEM), {}, format="json")
            self.assertEqual(response.status_code, 400)
            self.assertIn("name", response.json())
            self.assertNotIn("code", response.json())
            structured = self.client.post(PREFIX + "rename", {}, format="json")
        self.assertEqual(structured.status_code, 400)
        body = structured.json()
        self.assertEqual(body["code"], "VALIDATION_ERROR")
        self.assertIn("name", body["fields"])
        self.assertEqual(body["detail"], body["error"])
        with _env(LEX_ERROR_FORMAT="structured"):
            generic = self.client.post(self.url_create(API_PROJECT_ITEM), {}, format="json").json()
        self.assertEqual(generic["code"], "VALIDATION_ERROR")
        self.assertIn("name", generic["fields"])

    # -- 10.95 ---------------------------------------------------------
    def test_10_95_project_endpoint_errors_are_structured(self) -> None:
        """
        Scenario 10.95: unexpected errors in project endpoints are generic and stack-free.
        Given: a project view that raises RuntimeError, DEBUG off
        When: it is called
        Then: HTTP 500 {code: INTERNAL_ERROR, detail, error} without traceback
              and without the exception text.
        """
        self.use_lex_extensions(**PROJECT_API)
        with _env():
            response = self.client.get(PREFIX + "crash")
        self.assertEqual(response.status_code, 500)
        body = response.json()
        self.assertEqual(body["code"], "INTERNAL_ERROR")
        self.assertNotIn("traceback", body)
        self.assertNotIn("unexpected", body["detail"])

    # -- 10.96 ---------------------------------------------------------
    def test_10_96_database_errors_map_to_business_codes(self) -> None:
        """
        Scenario 10.96: DB_ERROR_MAPPER turns database errors into business answers.
        Given: a project view raising DatabaseError("... trigger said no")
        When: it is called without and with DB_ERROR_MAPPER configured
        Then: without a mapper it is a 500 INTERNAL_ERROR; with the mapper it is
              409 {code: TRIGGER_REFUSED}.
        """
        self.use_lex_extensions(**PROJECT_API)
        with _env():
            self.assertEqual(self.client.get(PREFIX + "db-failure").json()["code"], "INTERNAL_ERROR")
        self.use_lex_extensions(**PROJECT_API, DB_ERROR_MAPPER=f"{__name__}.map_trigger_errors")
        with _env():
            response = self.client.get(PREFIX + "db-failure")
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["code"], "TRIGGER_REFUSED")

    # -- 10.97 ---------------------------------------------------------
    def test_10_97_project_can_replace_the_handler(self) -> None:
        """
        Scenario 10.97: lex_config.EXCEPTION_HANDLER replaces lex's handler.
        Given: EXCEPTION_HANDLER pointing at a project function
        When: a project view raises
        Then: the project function's response is returned (418).
        """
        self.use_lex_extensions(**PROJECT_API, EXCEPTION_HANDLER=f"{__name__}.replacing_handler")
        response = self.client.get(PREFIX + "crash")
        self.assertEqual(response.status_code, 418)
        self.assertEqual(response.json(), {"replaced": True, "type": "RuntimeError"})
