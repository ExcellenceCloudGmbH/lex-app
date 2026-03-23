import os
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest import TestCase

import django
from django.apps import apps
from django.db import connection

sys.path.append(str(Path(__file__).resolve().parents[2]))
os.environ.setdefault(
    "DJANGO_SETTINGS_MODULE", "lex.process_admin.tests.django_test_settings"
)
if not apps.ready:
    django.setup()

from rest_framework.test import APIRequestFactory
from rest_framework_api_key.models import APIKey

from lex.core.models.LexModel import UserContext


class APIKeyUserContextTests(TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        existing_tables = connection.introspection.table_names()
        if APIKey._meta.db_table not in existing_tables:
            with connection.schema_editor() as schema_editor:
                schema_editor.create_model(APIKey)

    def setUp(self):
        self.factory = APIRequestFactory()
        _, self.api_key = APIKey.objects.create_key(name="Integration Bot")

    def test_api_key_request_builds_authenticated_technical_context(self):
        request = self.factory.get("/api/model_entries/example", HTTP_API_KEY=self.api_key)

        user_context = UserContext.from_request(request)

        self.assertTrue(user_context.is_authenticated)
        self.assertEqual(str(user_context.user), "Integration Bot")
        self.assertEqual(user_context.client_roles, frozenset({"api_key"}))
        self.assertSetEqual(
            user_context.keycloak_scopes,
            {"create", "read", "edit", "delete", "list", "export"},
        )

    def test_api_key_request_satisfies_default_create_and_edit_scope_checks(self):
        request = self.factory.post(
            "/api/model_entries/example",
            {},
            format="json",
            HTTP_API_KEY=self.api_key,
        )
        user_context = UserContext.from_request(request)

        class _ScopeAwareModel:
            def permission_create(self, context):
                return "create" in context.keycloak_scopes

            def permission_edit(self, context):
                return SimpleNamespace(allowed="edit" in context.keycloak_scopes)

        model = _ScopeAwareModel()

        self.assertTrue(model.permission_create(user_context))
        self.assertTrue(model.permission_edit(user_context).allowed)
