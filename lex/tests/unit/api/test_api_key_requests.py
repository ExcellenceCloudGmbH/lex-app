"""
Unit tests for ``lex.api.utils.api_key_requests`` — API key identity utilities.

**What this tests (customer-visible behaviour)**

API key authentication allows external systems (CI pipelines, Jira, etc.)
to interact with the LEX API.  ``TechnicalAPIKeyUser`` and
``APIKeyRequestIdentity`` provide the identity objects used downstream
by audit logging, permission checks, and user name extraction.

**Why it matters**

If ``TechnicalAPIKeyUser`` doesn't expose the right attributes
(``is_authenticated=True``, ``username``, etc.), permission checks fail
and audit logs attribute actions to the wrong user.

**Methodology**

Pure dataclass and class construction — no DB access.

Run::

    python manage.py test lex.tests.test_api_key_requests
"""

from django.test import SimpleTestCase

from lex.api.utils.api_key_requests import (
    TechnicalAPIKeyUser,
    APIKeyRequestIdentity,
    DEFAULT_API_KEY_SCOPES,
    _EmptyGroups,
)


class TestTechnicalAPIKeyUser(SimpleTestCase):
    """Prove ``TechnicalAPIKeyUser`` exposes the expected interface."""

    def test_is_authenticated(self):
        user = TechnicalAPIKeyUser("CI Key")
        self.assertTrue(user.is_authenticated)

    def test_not_superuser(self):
        user = TechnicalAPIKeyUser("CI Key")
        self.assertFalse(user.is_superuser)

    def test_not_anonymous(self):
        user = TechnicalAPIKeyUser("CI Key")
        self.assertFalse(user.is_anonymous)

    def test_str_returns_username(self):
        user = TechnicalAPIKeyUser("My API Key")
        self.assertEqual(str(user), "My API Key")

    def test_groups_values_list_returns_empty(self):
        user = TechnicalAPIKeyUser("Key")
        self.assertEqual(user.groups.values_list("name", flat=True), [])


class TestAPIKeyRequestIdentity(SimpleTestCase):
    """Prove ``APIKeyRequestIdentity`` dataclass stores identity correctly."""

    def test_basic_construction(self):
        user = TechnicalAPIKeyUser("Key")
        identity = APIKeyRequestIdentity(api_key_name="Key", user=user)
        self.assertEqual(identity.api_key_name, "Key")
        self.assertIs(identity.user, user)

    def test_default_scopes(self):
        user = TechnicalAPIKeyUser("Key")
        identity = APIKeyRequestIdentity(api_key_name="Key", user=user)
        self.assertEqual(identity.scopes, DEFAULT_API_KEY_SCOPES)
        self.assertIn("read", identity.scopes)
        self.assertIn("create", identity.scopes)
        self.assertIn("delete", identity.scopes)

    def test_custom_scopes(self):
        user = TechnicalAPIKeyUser("Key")
        identity = APIKeyRequestIdentity(
            api_key_name="Key",
            user=user,
            scopes=frozenset({"read"}),
        )
        self.assertEqual(identity.scopes, frozenset({"read"}))

    def test_default_email(self):
        user = TechnicalAPIKeyUser("Key")
        identity = APIKeyRequestIdentity(api_key_name="Key", user=user)
        self.assertEqual(identity.email, "")

    def test_is_frozen(self):
        user = TechnicalAPIKeyUser("Key")
        identity = APIKeyRequestIdentity(api_key_name="Key", user=user)
        with self.assertRaises(AttributeError):
            identity.api_key_name = "New Name"


class TestDefaultAPIKeyScopes(SimpleTestCase):
    """Prove the default scopes cover all six permission actions."""

    def test_all_scopes_present(self):
        expected = {"create", "read", "edit", "delete", "list", "export"}
        self.assertEqual(DEFAULT_API_KEY_SCOPES, expected)


class TestEmptyGroups(SimpleTestCase):
    """Prove ``_EmptyGroups`` mimics Django's group manager interface."""

    def test_values_list_returns_empty(self):
        groups = _EmptyGroups()
        self.assertEqual(groups.values_list("name", flat=True), [])
