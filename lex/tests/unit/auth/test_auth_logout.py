"""
Unit tests for ``lex.api.views.authentication.auth.provider_logout``.

**What this tests (customer-visible behaviour)**

``provider_logout`` builds the Keycloak end-session URL that the
browser is redirected to when the user clicks "Log out".  It must
include the ``id_token_hint`` parameter so Keycloak doesn't ask the
user to confirm, and ``post_logout_redirect_uri`` so they land back
on the correct page.

**Why it matters**

If the URL is malformed or missing the token hint, users see a
Keycloak "are you sure?" page (confusing UX), or worse, the session
isn't invalidated and the user remains logged in despite clicking
logout.

**Methodology**

Mocks ``django.conf.settings`` and ``request.session``.

Run::

    python manage.py test lex.tests.test_auth_logout
"""

from unittest.mock import MagicMock
from urllib.parse import urlparse, parse_qs

from django.test import SimpleTestCase, override_settings


@override_settings(
    OIDC_OP_LOGOUT_ENDPOINT="https://keycloak.example.com/realms/lex/protocol/openid-connect/logout",
    LOGOUT_REDIRECT_URL="https://app.example.com/",
)
class TestProviderLogout(SimpleTestCase):
    """Prove ``provider_logout`` builds correct Keycloak logout URLs."""

    def _call(self, session_data=None):
        from lex.api.views.authentication.auth import provider_logout

        request = MagicMock()
        request.session = session_data or {}
        return provider_logout(request)

    def test_with_id_token(self):
        url = self._call({"oidc_id_token": "tok_abc123"})
        parsed = urlparse(url)
        params = parse_qs(parsed.query)
        self.assertEqual(params["id_token_hint"], ["tok_abc123"])
        self.assertEqual(
            params["post_logout_redirect_uri"], ["https://app.example.com/"]
        )
        self.assertIn("keycloak.example.com", parsed.netloc)

    def test_without_id_token_returns_base(self):
        url = self._call({})
        self.assertEqual(
            url,
            "https://keycloak.example.com/realms/lex/protocol/openid-connect/logout",
        )

    def test_empty_session_returns_base(self):
        url = self._call({"oidc_id_token": None})
        # None is falsy, so we expect just the base URL
        self.assertNotIn("?", url)

    def test_url_is_properly_encoded(self):
        url = self._call({"oidc_id_token": "tok with spaces"})
        self.assertIn("tok+with+spaces", url)
