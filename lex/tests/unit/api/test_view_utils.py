"""
Unit tests for ``lex.api.views.utils`` — user identity extraction.

**What this tests (customer-visible behaviour)**

``get_user_name`` and ``get_user_email`` extract the current user's
identity from the Django request.  They support multiple auth strategies:
Jira webhooks, API keys, Keycloak JWT tokens, and session-based Django
users.  The extracted values appear in audit logs, history records, and
the ``created_by`` / ``edited_by`` fields.

**Why it matters**

If identity extraction fails or picks the wrong attribute, audit logs
attribute actions to "Technical User" instead of the real person — a
compliance issue for regulated customers.

**Methodology**

Pure logic (no DB).  We build mock request objects with the attribute
combinations each auth strategy produces.

Run::

    python manage.py test lex.tests.test_view_utils
"""

from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase
from lex.api.views.utils import get_user_name, get_user_email


def _make_request(**kwargs):
    """Build a lightweight mock request."""
    request = MagicMock()
    request.headers = kwargs.get("headers", {})
    request.auth = kwargs.get("auth", None)
    request.user = kwargs.get("user", MagicMock(is_authenticated=False))
    return request


class TestGetUserName(SimpleTestCase):
    """Prove ``get_user_name`` resolves identity in the correct priority order."""

    @patch("lex.api.views.utils.get_api_key_request_identity", return_value=None)
    def test_jira_header_returns_jira_name(self, _):
        request = _make_request(headers={"JIRA": "webhook"})
        self.assertEqual(get_user_name(request), "Created by Jira")

    @patch("lex.api.views.utils.get_api_key_request_identity")
    def test_api_key_returns_key_name(self, mock_identity):
        identity = MagicMock()
        identity.api_key_name = "CI Pipeline Key"
        mock_identity.return_value = identity
        request = _make_request()
        self.assertEqual(get_user_name(request), "CI Pipeline Key")

    @patch("lex.api.views.utils.get_api_key_request_identity", return_value=None)
    def test_jwt_name_and_sub(self, _):
        request = _make_request(auth={"name": "John", "sub": "user123"})
        self.assertEqual(get_user_name(request), "John (user123)")

    @patch("lex.api.views.utils.get_api_key_request_identity", return_value=None)
    def test_jwt_name_only(self, _):
        request = _make_request(auth={"name": "John"})
        self.assertEqual(get_user_name(request), "John")

    @patch("lex.api.views.utils.get_api_key_request_identity", return_value=None)
    def test_jwt_sub_only(self, _):
        request = _make_request(auth={"sub": "user123"})
        self.assertEqual(get_user_name(request), "user123")

    @patch("lex.api.views.utils.get_api_key_request_identity", return_value=None)
    def test_authenticated_user_fallback(self, _):
        user = MagicMock(is_authenticated=True)
        user.__str__ = lambda self: "admin"
        request = _make_request(user=user, auth=None)
        self.assertEqual(get_user_name(request), "admin")

    @patch("lex.api.views.utils.get_api_key_request_identity", return_value=None)
    def test_anonymous_returns_technical_user(self, _):
        request = _make_request(auth=None)
        self.assertEqual(get_user_name(request), "Technical User")


class TestGetUserEmail(SimpleTestCase):
    """Prove ``get_user_email`` resolves email in the correct priority order."""

    @patch("lex.api.views.utils.get_api_key_request_identity", return_value=None)
    def test_jira_header_returns_jira_email(self, _):
        request = _make_request(headers={"JIRA": "webhook"})
        self.assertEqual(get_user_email(request), "jira@mail.com")

    @patch("lex.api.views.utils.get_api_key_request_identity")
    def test_api_key_returns_identity_email(self, mock_identity):
        identity = MagicMock()
        identity.email = "ci@company.com"
        mock_identity.return_value = identity
        request = _make_request()
        self.assertEqual(get_user_email(request), "ci@company.com")

    @patch("lex.api.views.utils.get_api_key_request_identity")
    def test_api_key_no_email_returns_default(self, mock_identity):
        identity = MagicMock()
        identity.email = ""
        mock_identity.return_value = identity
        request = _make_request()
        self.assertEqual(get_user_email(request), "technical-user@local")

    @patch("lex.api.views.utils.get_api_key_request_identity", return_value=None)
    def test_jwt_email(self, _):
        request = _make_request(auth={"email": "john@example.com"})
        self.assertEqual(get_user_email(request), "john@example.com")

    @patch("lex.api.views.utils.get_api_key_request_identity", return_value=None)
    def test_authenticated_user_email(self, _):
        user = MagicMock(is_authenticated=True, email="admin@example.com")
        request = _make_request(user=user, auth=None)
        self.assertEqual(get_user_email(request), "admin@example.com")

    @patch("lex.api.views.utils.get_api_key_request_identity", return_value=None)
    def test_authenticated_user_no_email_returns_default(self, _):
        user = MagicMock(is_authenticated=True, email="")
        request = _make_request(user=user, auth=None)
        self.assertEqual(get_user_email(request), "technical-user@local")

    @patch("lex.api.views.utils.get_api_key_request_identity", return_value=None)
    def test_anonymous_returns_default(self, _):
        request = _make_request(auth=None)
        self.assertEqual(get_user_email(request), "technical-user@local")
