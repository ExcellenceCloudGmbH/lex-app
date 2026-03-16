import os
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest import TestCase

import django
from django.apps import apps

sys.path.append(str(Path(__file__).resolve().parents[2]))
os.environ.setdefault(
    "DJANGO_SETTINGS_MODULE", "lex.process_admin.tests.django_test_settings"
)
if not apps.ready:
    django.setup()

from rest_framework.test import APIRequestFactory, force_authenticate

from lex.api.views.permissions.ModelPermissions import ModelPermissions
from lex.authentication.views.permissions import UserPermissionsView
from lex.process_admin.models.ModelContainer import ModelContainer


class _Restriction:
    def __init__(self, *, read=True, modify=True, create=True, delete=True):
        self.read = read
        self.modify = modify
        self.create = create
        self.delete = delete

    def can_read_in_general(self, user, violations):
        return self.read

    def can_modify_in_general(self, user, violations):
        return self.modify

    def can_create_in_general(self, user, violations):
        return self.create

    def can_delete_in_general(self, user, violations):
        return self.delete


class ScopeAwareModel:
    _meta = SimpleNamespace(app_label="test_app", model_name="scope_model")
    modification_restriction = _Restriction()

    def permission_list(self, user_context):
        return "list" in user_context.keycloak_scopes

    def permission_edit(self, user_context):
        return SimpleNamespace(allowed="edit" in user_context.keycloak_scopes)

    def permission_create(self, user_context):
        return "create" in user_context.keycloak_scopes

    def permission_delete(self, user_context):
        return "delete" in user_context.keycloak_scopes


class CreateBlockedScopeAwareModel(ScopeAwareModel):
    modification_restriction = _Restriction(create=False)


class _Groups:
    def values_list(self, *args, **kwargs):
        return []


class _User:
    def __init__(self, username):
        self.username = username
        self.email = f"{username}@example.com"
        self.is_authenticated = True
        self.is_superuser = False
        self.groups = _Groups()


class ModelPermissionsViewTests(TestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        self.user = _User("scope-user")

    def _build_container(self, model_class):
        container = ModelContainer.__new__(ModelContainer)
        container.model_class = model_class
        return container

    def _build_request(self, model_class, scopes):
        request = self.factory.get("/api/scope_model/model-permissions")
        force_authenticate(request, user=self.user)
        request.user_permissions = [
            {
                "rsname": f"test_app.{model_class.__name__}",
                "scopes": scopes,
            }
        ]
        request.userinfo = {}
        request.client_roles = []
        request.session = {}
        return request

    def test_model_permissions_endpoint_uses_request_scoped_create_permission(self):
        request = self._build_request(ScopeAwareModel, ["list", "create"])
        response = ModelPermissions.as_view()(
            request,
            model_container=self._build_container(ScopeAwareModel),
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.data,
            {
                "scope_model": {
                    "can_read_in_general": True,
                    "can_modify_in_general": False,
                    "can_create_in_general": True,
                    "can_delete_in_general": False,
                }
            },
        )

    def test_model_permissions_endpoint_keeps_legacy_create_block(self):
        request = self._build_request(CreateBlockedScopeAwareModel, ["list", "create"])
        response = ModelPermissions.as_view()(
            request,
            model_container=self._build_container(CreateBlockedScopeAwareModel),
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.data["scope_model"]["can_create_in_general"])


class UserPermissionsViewTests(TestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        self.user = _User("rbac-user")

    def test_user_permissions_view_returns_live_request_scopes(self):
        request = self.factory.get("/api/user_permissions/")
        force_authenticate(request, user=self.user)
        request.user_permissions = [
            {
                "rsname": "test_app.ScopeAwareModel",
                "scopes": ["create", "read"],
            }
        ]

        response = UserPermissionsView.as_view()(request)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.data,
            [
                {"action": "create", "resource": "scopeawaremodel"},
                {"action": "read", "resource": "scopeawaremodel"},
            ],
        )
