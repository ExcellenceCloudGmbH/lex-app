"""OpenAPI schema of the project API.

Intent
------
A project API needs a machine-readable contract for client generation and
integrators. With ``lex_config.SCHEMA`` set, lex serves the OpenAPI document
at ``<API_PREFIX>schema`` and a Redoc page at ``<API_PREFIX>docs`` (public on
request), and ``lex schema --file`` writes it. Only the configured prefixes
are documented — lex's generic endpoints stay out — and the security schemes
lex accepts are listed. Without ``SCHEMA`` nothing is served.

Cluster 10q — scenarios 10.98–10.101. Type: E.
Covers: lex/api/schema.py, lex/api/schema_extensions.py,
lex/api/management/commands/schema.py, schema routes in lex/lex_app/urls.py.
Run: python -m lex pytest lex/test_project/tests/api_layer/test_10q_openapi_schema.py -v
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
import yaml
from django.core.management import call_command
from django.urls import resolve
from rest_framework.test import APIClient

from lex.react.views import serve_react
from lex.test_project.tests._e2e_test_case import E2ETestCase

from .models import PROJECT_MODELS

pytestmark = pytest.mark.api_layer

PREFIX = "/api/cluster10/"
PROJECT_API = {"API_URLCONF": "lex.test_project.tests.api_layer._project_api", "API_PREFIX": "api/cluster10/"}
SCHEMA = {"title": "Cluster 10 API", "version": "3.1.4", "public": True}


class TestCluster10q_OpenApiSchema(E2ETestCase):
    """Cluster 10q: schema endpoint, docs page and CLI."""

    e2e_models = PROJECT_MODELS

    def _schema(self, client=None):
        response = (client or APIClient()).get(PREFIX + "schema", HTTP_ACCEPT="application/vnd.oai.openapi")
        self.assertEqual(response.status_code, 200, response.content[:500])
        return yaml.safe_load(response.content)

    # -- 10.98 ---------------------------------------------------------
    def test_10_98_schema_documents_only_the_project_api(self) -> None:
        """
        Scenario 10.98: the served schema covers the project prefix, with configured metadata.
        Given: SCHEMA with title, version and public=True
        When: an anonymous client GETs <prefix>schema
        Then: HTTP 200; info carries the title and version; every path starts
              with /api/cluster10/ (no model_entries); whoami is documented.
        """
        self.use_lex_extensions(**PROJECT_API, SCHEMA=SCHEMA)
        document = self._schema()
        self.assertEqual(document["info"]["title"], "Cluster 10 API")
        self.assertEqual(document["info"]["version"], "3.1.4")
        self.assertTrue(document["paths"])
        self.assertTrue(all(p.startswith(PREFIX) for p in document["paths"]), list(document["paths"]))
        self.assertIn(PREFIX + "whoami", document["paths"])

    # -- 10.99 ---------------------------------------------------------
    def test_10_99_security_schemes_and_docs_page(self) -> None:
        """
        Scenario 10.99: the schema names lex's bearer scheme and the docs page is served.
        Given: the same configuration
        When: the schema components and <prefix>docs are fetched anonymously
        Then: components.securitySchemes contains bearerAuth (HTTP bearer, JWT);
              the docs page answers 200 with HTML.
        """
        self.use_lex_extensions(**PROJECT_API, SCHEMA=SCHEMA)
        schemes = self._schema()["components"]["securitySchemes"]
        self.assertEqual(schemes["bearerAuth"], {"type": "http", "scheme": "bearer", "bearerFormat": "JWT"})
        docs = APIClient().get(PREFIX + "docs")
        self.assertEqual(docs.status_code, 200)
        self.assertIn(b"<html", docs.content.lower())

    # -- 10.100 ---------------------------------------------------------
    def test_10_100_cli_writes_a_valid_schema(self) -> None:
        """
        Scenario 10.100: `lex schema --validate --file` writes the document.
        Given: SCHEMA configured
        When: the schema management command runs with --validate and --file
        Then: the file is valid YAML with the configured title and only project paths.
        """
        self.use_lex_extensions(**PROJECT_API, SCHEMA=SCHEMA)
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "openapi.yaml"
            call_command("schema", "--validate", "--file", str(target))
            document = yaml.safe_load(target.read_text())
        self.assertEqual(document["info"]["title"], "Cluster 10 API")
        self.assertTrue(all(p.startswith(PREFIX) for p in document["paths"]))

    # -- 10.101 ---------------------------------------------------------
    def test_10_101_no_schema_without_configuration(self) -> None:
        """
        Scenario 10.101: without SCHEMA nothing is served.
        Given: the project API mounted but SCHEMA unset
        When: <prefix>schema is resolved
        Then: no schema view answers; the path is not part of the project routes.
        """
        self.use_lex_extensions(**PROJECT_API)
        self.assertIs(resolve(PREFIX + "schema").func, serve_react)
        self.assertIs(resolve(PREFIX + "docs").func, serve_react)
