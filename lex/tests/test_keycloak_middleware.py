"""
Unit tests for ``KeycloakPermissionsMiddleware._extract_client_roles``.

**What this tests (customer-visible behaviour)**

Keycloak returns user roles in various formats depending on the OIDC
configuration (single string, list, nested dict under a client name).
``_extract_client_roles`` normalizes all variants into a flat list of
role strings that downstream permission checks consume.

**Why it matters**

If roles aren't extracted correctly, users lose permissions — they can't
see models, can't create/edit/delete records, or lose admin panel access.

**Methodology**

Pure static method — no DB, no Keycloak connection.

Run::

    python manage.py test lex.tests.test_keycloak_middleware
"""

from django.test import SimpleTestCase

from lex.api.middleware.keycloak_permissions import KeycloakPermissionsMiddleware


class TestExtractClientRoles(SimpleTestCase):
    """Prove ``_extract_client_roles`` handles all Keycloak role formats."""

    def test_list_of_strings(self):
        userinfo = {"client_roles": ["admin", "viewer"]}
        self.assertEqual(
            KeycloakPermissionsMiddleware._extract_client_roles(userinfo),
            ["admin", "viewer"],
        )

    def test_single_string(self):
        userinfo = {"client_roles": "admin"}
        self.assertEqual(
            KeycloakPermissionsMiddleware._extract_client_roles(userinfo),
            ["admin"],
        )

    def test_none_returns_empty(self):
        userinfo = {"client_roles": None}
        self.assertEqual(
            KeycloakPermissionsMiddleware._extract_client_roles(userinfo),
            [],
        )

    def test_missing_key_returns_empty(self):
        userinfo = {}
        self.assertEqual(
            KeycloakPermissionsMiddleware._extract_client_roles(userinfo),
            [],
        )

    def test_empty_list_returns_empty(self):
        userinfo = {"client_roles": []}
        self.assertEqual(
            KeycloakPermissionsMiddleware._extract_client_roles(userinfo),
            [],
        )

    def test_dict_with_nested_list(self):
        """Keycloak sometimes nests roles under the client name as a dict."""
        userinfo = {
            "client_roles": {
                "my-app": ["admin", "editor"],
                "another-app": ["viewer"],
            }
        }
        result = KeycloakPermissionsMiddleware._extract_client_roles(userinfo)
        self.assertIn("admin", result)
        self.assertIn("editor", result)
        self.assertIn("viewer", result)

    def test_dict_with_string_values(self):
        userinfo = {"client_roles": {"app": "admin"}}
        result = KeycloakPermissionsMiddleware._extract_client_roles(userinfo)
        self.assertEqual(result, ["admin"])

    def test_filters_non_string_from_list(self):
        userinfo = {"client_roles": ["admin", 42, None, "viewer"]}
        self.assertEqual(
            KeycloakPermissionsMiddleware._extract_client_roles(userinfo),
            ["admin", "viewer"],
        )

    def test_tuple_input(self):
        userinfo = {"client_roles": ("admin", "editor")}
        self.assertEqual(
            KeycloakPermissionsMiddleware._extract_client_roles(userinfo),
            ["admin", "editor"],
        )

    def test_set_input(self):
        userinfo = {"client_roles": {"admin"}}
        result = KeycloakPermissionsMiddleware._extract_client_roles(userinfo)
        self.assertEqual(result, ["admin"])

    def test_unexpected_type_returns_empty(self):
        """Integer, boolean, etc. should return empty list."""
        userinfo = {"client_roles": 42}
        self.assertEqual(
            KeycloakPermissionsMiddleware._extract_client_roles(userinfo),
            [],
        )
