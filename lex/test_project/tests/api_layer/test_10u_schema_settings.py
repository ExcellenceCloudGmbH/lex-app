"""The schema, the docs page and ``lex schema`` use the same settings.

Intent
------
``lex_config.SCHEMA["settings"]`` carries drf-spectacular settings for the
project API. They used to reach the served schema and ``lex schema`` only: the
Redoc docs page kept the global ``REDOC_DIST`` and ``TITLE``, and the cached
``ENUM_NAME_OVERRIDES`` stayed whatever the first generation in the process had
loaded. All three now run under one patched context that also reloads the enum
overrides; the schema and docs routes stay out of the document. Lex's scoped
API keys are described in the schema (``apiKeyAuth``, header ``Api-Key``) at
low priority, so generation no longer warns about an unknown authenticator and
a project's own description of the same class wins.

Cluster 10u — scenarios 10.117–10.120. Type: E + U.
Covers: lex/api/schema.py (project_schema_settings, schema views),
lex/api/management/commands/schema.py, lex/api/schema_extensions.py
(LexAPIKeyAuthenticationScheme).
Run: python -m lex pytest lex/test_project/tests/api_layer/test_10u_schema_settings.py -v
"""

from __future__ import annotations

from unittest import mock

import pytest
from drf_spectacular.extensions import OpenApiAuthenticationExtension
from drf_spectacular.plumbing import load_enum_name_overrides
from rest_framework.test import APIClient

from lex.api.schema import generate_schema, project_schema_settings
from lex.authentication.authentication_backends.api_keys import LexAPIKeyAuthentication
from lex.core import extensions
from lex.test_project.tests._e2e_test_case import E2ETestCase

from .models import PROJECT_MODELS

pytestmark = pytest.mark.api_layer

PREFIX = "/api/cluster10u/"
PROJECT_API = {"API_URLCONF": "lex.test_project.tests.api_layer._project_api", "API_PREFIX": "api/cluster10u/"}
SCHEMA = {
    "title": "Cluster 10u API",
    "version": "1.0.0",
    "public": True,
    "settings": {
        "REDOC_DIST": "https://cdn.jsdelivr.net/npm/redoc@2.1.5",
        "ENUM_NAME_OVERRIDES": {"Cluster10uColourEnum": ["red", "green"]},
    },
}


class TestCluster10u_SchemaSettings(E2ETestCase):
    """Cluster 10u: one settings context for schema, docs and CLI."""

    e2e_models = PROJECT_MODELS

    # -- 10.117 --------------------------------------------------------
    def test_10_117_docs_page_uses_the_schema_settings(self) -> None:
        """
        Scenario 10.117: the docs page loads the configured Redoc bundle under the configured title.
        Given: SCHEMA with a title and settings.REDOC_DIST pinned to redoc@2.1.5
        When: an anonymous client GETs <prefix>docs
        Then: HTTP 200; the page references redoc@2.1.5 and shows the title.
        """
        self.use_lex_extensions(**PROJECT_API, SCHEMA=SCHEMA)
        page = APIClient().get(PREFIX + "docs")
        self.assertEqual(page.status_code, 200, page.content[:300])
        html = page.content.decode()
        self.assertIn("redoc@2.1.5", html)
        self.assertIn("Cluster 10u API", html)

    # -- 10.118 --------------------------------------------------------
    def test_10_118_enum_overrides_follow_the_patched_settings(self) -> None:
        """
        Scenario 10.118: enum names do not depend on which generation ran first.
        Given: SCHEMA.settings with an ENUM_NAME_OVERRIDES entry
        When: the overrides are loaded inside and then outside project_schema_settings()
        Then: the entry is there inside and gone outside (the cache is reloaded both ways).
        """
        load_enum_name_overrides()  # warm the cache with the global settings first
        with extensions.override_extensions(**PROJECT_API, SCHEMA=SCHEMA):
            with project_schema_settings():
                inside = load_enum_name_overrides()
            outside = load_enum_name_overrides()
        self.assertIn("Cluster10uColourEnum", inside.values())
        self.assertNotIn("Cluster10uColourEnum", outside.values())

    # -- 10.119 --------------------------------------------------------
    def test_10_119_scoped_keys_are_described_without_warnings(self) -> None:
        """
        Scenario 10.119: lex's API-key authentication has a security scheme.
        Given: the project API with lex's default authentication classes
        When: the schema is generated
        Then: securitySchemes.apiKeyAuth is an apiKey in header Api-Key; no
              "could not resolve authenticator" warning is emitted; the schema
              and docs routes are not documented.
        """
        self.use_lex_extensions(**PROJECT_API, SCHEMA=SCHEMA)
        warnings = []
        with mock.patch("drf_spectacular.openapi.warn", side_effect=lambda msg, *a, **k: warnings.append(msg)):
            document = generate_schema()
        self.assertEqual(document["components"]["securitySchemes"]["apiKeyAuth"],
                         {"type": "apiKey", "in": "header", "name": "Api-Key"})
        self.assertFalse([w for w in warnings if "authenticator" in w], warnings)
        self.assertFalse([p for p in document["paths"] if p.endswith(("/schema", "/docs"))], list(document["paths"]))

    # -- 10.120 --------------------------------------------------------
    def test_10_120_a_project_description_of_the_key_class_wins(self) -> None:
        """
        Scenario 10.120: projects describe their own key header.
        Given: a project extension for LexAPIKeyAuthentication at default priority
        When: drf-spectacular picks the extension for that class
        Then: the project's extension is chosen, not lex's.
        """
        registry = OpenApiAuthenticationExtension._registry
        before = list(registry)

        class ProjectKeyScheme(OpenApiAuthenticationExtension):
            target_class = LexAPIKeyAuthentication
            name = "projectKeyAuth"

            def get_security_definition(self, auto_schema):
                return {"type": "apiKey", "in": "header", "name": "X-Project-Key"}

        try:
            match = OpenApiAuthenticationExtension.get_match(LexAPIKeyAuthentication())
            self.assertEqual(match.name, "projectKeyAuth")
        finally:
            registry[:] = before
        self.assertEqual(OpenApiAuthenticationExtension.get_match(LexAPIKeyAuthentication()).name, "apiKeyAuth")
