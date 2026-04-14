"""
Tests for ``KeycloakPermissionsMiddleware`` — the Django middleware that
attaches UMA permissions, userinfo, and client roles to every authenticated
request.

This middleware runs on **every** request. A bug here silently breaks
**all** permission enforcement downstream (model-level permissions,
export masking, UI-driven access control). That makes it one of the
highest-impact untested modules in the framework.

Coverage targets:
    1. Default attributes set on every request (even unauthenticated)
    2. Happy-path: token present, permissions and userinfo fetched
    3. UMA permission fetch failure — must not crash the request
    4. Userinfo fetch failure — must not crash the request
    5. ``_extract_client_roles`` — handles string, list, dict, nested, empty

All tests are pure-unit (no Keycloak, no HTTP) and use ``SimpleTestCase``.

How to run::

    lex test lex.process_admin.tests.test_keycloak_permissions_middleware \\
        --verbosity=2 --noinput
"""

import os
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch, MagicMock, PropertyMock

import django
from django.apps import apps

sys.path.append(str(Path(__file__).resolve().parents[2]))
os.environ.setdefault(
    "DJANGO_SETTINGS_MODULE", "lex.process_admin.tests.django_test_settings"
)
if not apps.ready:
    django.setup()

from django.test import SimpleTestCase

from lex.api.middleware.keycloak_permissions import KeycloakPermissionsMiddleware


# ── Helpers ───────────────────────────────────────────────────────────────

def _make_request(*, access_token=None):
    """Build a fake request with a controllable session."""
    request = MagicMock()
    session = {}
    if access_token is not None:
        session["oidc_access_token"] = access_token
    request.session = session
    return request


def _make_middleware(get_response_return=None):
    """Build the middleware with a controllable inner handler."""
    get_response = MagicMock(return_value=get_response_return or MagicMock())
    middleware = KeycloakPermissionsMiddleware(get_response)
    return middleware, get_response


# ═══════════════════════════════════════════════════════════════════════════
#  1. Default attributes on unauthenticated requests
# ═══════════════════════════════════════════════════════════════════════════

class DefaultAttributeTests(SimpleTestCase):
    """
    Every request — including unauthenticated ones — must have
    ``user_permissions``, ``userinfo``, and ``client_roles`` set to safe
    defaults so downstream code can always access them without KeyError.
    """

    def test_unauthenticated_request_gets_empty_permissions(self):
        """No token in session → empty permissions list."""
        middleware, get_response = _make_middleware()
        request = _make_request()

        middleware(request)

        self.assertEqual(request.user_permissions, [])

    def test_unauthenticated_request_gets_empty_userinfo(self):
        middleware, get_response = _make_middleware()
        request = _make_request()

        middleware(request)

        self.assertEqual(request.userinfo, {})

    def test_unauthenticated_request_gets_empty_client_roles(self):
        middleware, get_response = _make_middleware()
        request = _make_request()

        middleware(request)

        self.assertEqual(request.client_roles, [])

    def test_get_response_is_called(self):
        """The middleware must always call the next handler in the chain."""
        middleware, get_response = _make_middleware()
        request = _make_request()

        middleware(request)

        get_response.assert_called_once_with(request)


# ═══════════════════════════════════════════════════════════════════════════
#  2. Happy path — token present, permissions fetched
# ═══════════════════════════════════════════════════════════════════════════

class HappyPathTests(SimpleTestCase):
    """When a valid OIDC access token is in the session, permissions and
    userinfo are fetched from Keycloak and attached to the request."""

    @patch("lex.api.middleware.keycloak_permissions.KeycloakManager")
    def test_permissions_attached_to_request(self, MockKCManager):
        """UMA permissions from Keycloak are set on ``request.user_permissions``."""
        mock_instance = MockKCManager.return_value
        mock_instance.get_uma_permissions.return_value = [
            {"rsname": "Investor", "scopes": ["view", "edit"]}
        ]
        mock_instance.oidc = None  # skip userinfo

        middleware, _ = _make_middleware()
        request = _make_request(access_token="valid-token-123")

        middleware(request)

        self.assertEqual(len(request.user_permissions), 1)
        self.assertEqual(request.user_permissions[0]["rsname"], "Investor")
        mock_instance.get_uma_permissions.assert_called_once_with("valid-token-123")

    @patch("lex.api.middleware.keycloak_permissions.KeycloakManager")
    def test_userinfo_and_client_roles_attached(self, MockKCManager):
        """Userinfo dict and extracted client_roles are attached."""
        mock_instance = MockKCManager.return_value
        mock_instance.get_uma_permissions.return_value = []
        mock_oidc = MagicMock()
        mock_oidc.userinfo.return_value = {
            "sub": "user-uuid",
            "email": "analyst@fund.com",
            "client_roles": ["fund_admin", "viewer"],
        }
        mock_instance.oidc = mock_oidc

        middleware, _ = _make_middleware()
        request = _make_request(access_token="valid-token")

        middleware(request)

        self.assertEqual(request.userinfo["email"], "analyst@fund.com")
        self.assertEqual(request.client_roles, ["fund_admin", "viewer"])

    @patch("lex.api.middleware.keycloak_permissions.KeycloakManager")
    def test_none_permissions_becomes_empty_list(self, MockKCManager):
        """If Keycloak returns None for permissions, default to empty list."""
        mock_instance = MockKCManager.return_value
        mock_instance.get_uma_permissions.return_value = None
        mock_instance.oidc = None

        middleware, _ = _make_middleware()
        request = _make_request(access_token="token")

        middleware(request)

        self.assertEqual(request.user_permissions, [])


# ═══════════════════════════════════════════════════════════════════════════
#  3. Error resilience
# ═══════════════════════════════════════════════════════════════════════════

class ErrorResilienceTests(SimpleTestCase):
    """The middleware must never crash a request — even if Keycloak is down."""

    @patch("lex.api.middleware.keycloak_permissions.KeycloakManager")
    def test_uma_permissions_failure_does_not_crash(self, MockKCManager):
        """If ``get_uma_permissions`` throws, permissions default to [] and
        the request continues normally."""
        mock_instance = MockKCManager.return_value
        mock_instance.get_uma_permissions.side_effect = ConnectionError("Keycloak unreachable")
        mock_instance.oidc = None

        middleware, get_response = _make_middleware()
        request = _make_request(access_token="token")

        # Must not raise
        middleware(request)

        self.assertEqual(request.user_permissions, [])
        get_response.assert_called_once()

    @patch("lex.api.middleware.keycloak_permissions.KeycloakManager")
    def test_userinfo_failure_does_not_crash(self, MockKCManager):
        """If userinfo fetch fails, the request proceeds with empty userinfo."""
        mock_instance = MockKCManager.return_value
        mock_instance.get_uma_permissions.return_value = [{"rsname": "X"}]
        mock_oidc = MagicMock()
        mock_oidc.userinfo.side_effect = Exception("Token expired")
        mock_instance.oidc = mock_oidc

        middleware, get_response = _make_middleware()
        request = _make_request(access_token="token")

        middleware(request)

        # Permissions were fetched before the userinfo error
        self.assertEqual(request.user_permissions, [{"rsname": "X"}])
        self.assertEqual(request.userinfo, {})
        self.assertEqual(request.client_roles, [])
        get_response.assert_called_once()

    @patch("lex.api.middleware.keycloak_permissions.KeycloakManager")
    def test_non_dict_userinfo_is_ignored(self, MockKCManager):
        """If OIDC returns a non-dict (e.g., error string), don't crash."""
        mock_instance = MockKCManager.return_value
        mock_instance.get_uma_permissions.return_value = []
        mock_oidc = MagicMock()
        mock_oidc.userinfo.return_value = "error: invalid_token"
        mock_instance.oidc = mock_oidc

        middleware, _ = _make_middleware()
        request = _make_request(access_token="token")

        middleware(request)

        # Non-dict userinfo should not be stored
        self.assertEqual(request.userinfo, {})
        self.assertEqual(request.client_roles, [])


# ═══════════════════════════════════════════════════════════════════════════
#  4. _extract_client_roles — handles all data shapes
# ═══════════════════════════════════════════════════════════════════════════

class ExtractClientRolesTests(SimpleTestCase):
    """
    ``_extract_client_roles`` must handle every shape Keycloak produces:
    - A plain list of strings (most common)
    - A single string (legacy Keycloak configs)
    - A dict mapping client IDs to role lists
    - An empty/None value
    - Non-string values in lists are filtered out

    This is tested directly because downstream code (permission_create,
    permission_edit, etc.) relies on ``request.client_roles`` being a
    clean list of strings.
    """

    def test_list_of_strings(self):
        """Most common format: ['role1', 'role2']."""
        result = KeycloakPermissionsMiddleware._extract_client_roles(
            {"client_roles": ["fund_admin", "viewer"]}
        )
        self.assertEqual(result, ["fund_admin", "viewer"])

    def test_single_string(self):
        """Legacy format: 'fund_admin'."""
        result = KeycloakPermissionsMiddleware._extract_client_roles(
            {"client_roles": "fund_admin"}
        )
        self.assertEqual(result, ["fund_admin"])

    def test_dict_with_role_lists(self):
        """Keycloak can return a dict: {'my-client': ['admin', 'user']}."""
        result = KeycloakPermissionsMiddleware._extract_client_roles(
            {"client_roles": {"my-client": ["admin", "user"], "other": ["viewer"]}}
        )
        self.assertIn("admin", result)
        self.assertIn("user", result)
        self.assertIn("viewer", result)

    def test_dict_with_single_string_values(self):
        """Dict values can be plain strings too."""
        result = KeycloakPermissionsMiddleware._extract_client_roles(
            {"client_roles": {"client-a": "admin"}}
        )
        self.assertEqual(result, ["admin"])

    def test_none_roles_returns_empty(self):
        result = KeycloakPermissionsMiddleware._extract_client_roles(
            {"client_roles": None}
        )
        self.assertEqual(result, [])

    def test_empty_string_returns_empty(self):
        result = KeycloakPermissionsMiddleware._extract_client_roles(
            {"client_roles": ""}
        )
        self.assertEqual(result, [])

    def test_missing_key_returns_empty(self):
        result = KeycloakPermissionsMiddleware._extract_client_roles({})
        self.assertEqual(result, [])

    def test_non_string_values_in_list_are_filtered(self):
        """Only strings should appear in the final list."""
        result = KeycloakPermissionsMiddleware._extract_client_roles(
            {"client_roles": ["admin", 42, None, True, "viewer"]}
        )
        self.assertEqual(result, ["admin", "viewer"])

    def test_tuple_input_handled(self):
        """Tuples should be treated like lists."""
        result = KeycloakPermissionsMiddleware._extract_client_roles(
            {"client_roles": ("admin", "viewer")}
        )
        self.assertEqual(result, ["admin", "viewer"])

    def test_set_input_handled(self):
        """Sets should be handled but order may vary."""
        result = KeycloakPermissionsMiddleware._extract_client_roles(
            {"client_roles": {"admin"}}
        )
        # Sets are iterable but may be treated as dict (which has .values())
        # The implementation checks for dict first, then list/tuple/set
        # A frozenset/set will match the list/tuple/set/frozenset branch
        self.assertIn("admin", result)


# ═══════════════════════════════════════════════════════════════════════════
#  5. Token cleanup
# ═══════════════════════════════════════════════════════════════════════════

class _MockSession(dict):
    """Dict subclass that supports .save() for session-like behavior."""
    def save(self):
        pass


class TokenCleanupTests(SimpleTestCase):
    """
    ``cleanup_invalid_tokens`` removes malformed tokens from the session.
    This prevents infinite redirect loops when a session has a corrupted
    OIDC token (e.g., None or empty string stored by a failed refresh).
    """

    def test_removes_none_token(self):
        """None stored as the access token must be cleaned up."""
        middleware, _ = _make_middleware()
        request = _make_request()
        request.session = _MockSession({
            "oidc_access_token": None,
            "oidc_id_token": "valid",
            "oidc_refresh_token": "valid",
        })

        middleware.cleanup_invalid_tokens(request)

        self.assertNotIn("oidc_access_token", request.session)
        self.assertIn("oidc_id_token", request.session)  # valid tokens stay

    def test_removes_empty_string_token(self):
        middleware, _ = _make_middleware()
        request = _make_request()
        request.session = _MockSession({
            "oidc_access_token": "",
            "oidc_id_token": "valid",
        })

        middleware.cleanup_invalid_tokens(request)

        self.assertNotIn("oidc_access_token", request.session)

    def test_removes_non_string_token(self):
        """If something stored an integer as a token, clean it up."""
        middleware, _ = _make_middleware()
        request = _make_request()
        request.session = _MockSession({"oidc_access_token": 12345})

        middleware.cleanup_invalid_tokens(request)

        self.assertNotIn("oidc_access_token", request.session)

    def test_valid_tokens_are_not_removed(self):
        """Proper string tokens must not be cleaned up."""
        middleware, _ = _make_middleware()
        request = _make_request()
        request.session = _MockSession({
            "oidc_access_token": "eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9...",
            "oidc_id_token": "eyJhbGciOiJSUzI1NiJ9...",
        })

        middleware.cleanup_invalid_tokens(request)

        self.assertIn("oidc_access_token", request.session)
        self.assertIn("oidc_id_token", request.session)

    def test_cleanup_removes_related_keys(self):
        """When a token is removed, related session data is also cleaned."""
        middleware, _ = _make_middleware()
        request = _make_request()
        request.session = _MockSession({
            "oidc_access_token": None,
            "oidc_access_expires_at": 1234567890,
            "oidc_expires_at": 1234567890,
        })

        middleware.cleanup_invalid_tokens(request)

        self.assertNotIn("oidc_access_token", request.session)
        self.assertNotIn("oidc_access_expires_at", request.session)
        self.assertNotIn("oidc_expires_at", request.session)
