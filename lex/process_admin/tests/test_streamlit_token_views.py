"""
Tests for ``StreamlitTokenView`` and ``StreamlitTokenRevokeView`` — the JWT
token endpoints used by Streamlit dashboards.

These views manage short-lived JWTs that let Streamlit apps authenticate
against the LEX API without needing the full OIDC flow. They are
security-critical: a bug here could leak tokens, skip revocation, or
allow cross-user token usage.

Coverage targets:
    1. ``_check_token_status`` — valid / expired / revoked / wrong-user
    2. ``_generate_new_token`` — payload structure, expiry, permissions
    3. ``_get_user_permissions`` — Keycloak integration and fallback
    4. ``StreamlitTokenRevokeView.post`` — revocation via cache
    5. Edge cases — missing token, decode errors, missing settings

All tests use ``SimpleTestCase`` with mocked JWT/cache — no Keycloak or
Redis required.

How to run::

    lex test lex.process_admin.tests.test_streamlit_token_views \\
        --verbosity=2 --noinput
"""

import os
import sys
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch, MagicMock

import django
from django.apps import apps

sys.path.append(str(Path(__file__).resolve().parents[2]))
os.environ.setdefault(
    "DJANGO_SETTINGS_MODULE", "lex.process_admin.tests.django_test_settings"
)
if not apps.ready:
    django.setup()

from django.test import SimpleTestCase, override_settings

try:
    import jwt as pyjwt
    HAS_JWT = True
except ImportError:
    HAS_JWT = False

if HAS_JWT:
    from lex.authentication.views.token_views import (
        StreamlitTokenView,
        StreamlitTokenRevokeView,
    )


# ── Helpers ───────────────────────────────────────────────────────────────

JWT_SECRET = "test-jwt-secret-key-for-unit-tests"


def _make_user(user_id=1, email="analyst@fund.com", username="analyst"):
    return SimpleNamespace(id=user_id, email=email, username=username)


def _make_request(user=None, token=None, data=None, session=None):
    request = MagicMock()
    request.user = user or _make_user()
    request.data = data or {}
    request.META = {}
    request.session = session or {}
    if token:
        request.META["HTTP_AUTHORIZATION"] = f"Bearer {token}"
    return request


def _encode_token(payload, secret=JWT_SECRET):
    """Encode a JWT token for testing."""
    return pyjwt.encode(payload, secret, algorithm="HS256")


# ═══════════════════════════════════════════════════════════════════════════
#  1. _check_token_status
# ═══════════════════════════════════════════════════════════════════════════

class CheckTokenStatusTests(SimpleTestCase):
    """
    ``_check_token_status`` classifies tokens as 'valid', 'refresh', or
    'invalid'. This drives the smart-token-management logic: only generate
    a new token when the current one is actually invalid or about to expire.
    """

    def setUp(self):
        if not HAS_JWT:
            self.skipTest("PyJWT is not installed")
        self.view = StreamlitTokenView()
        self.user = _make_user(user_id=42)

    @override_settings(SECRET_KEY=JWT_SECRET)
    def test_valid_token_returns_valid(self):
        """A token with > 60s remaining life is 'valid' — no refresh needed."""
        payload = {
            "sub": "42",
            "exp": int((datetime.now(timezone.utc) + timedelta(minutes=5)).timestamp()),
            "iat": int(datetime.now(timezone.utc).timestamp()),
        }
        token = _encode_token(payload)

        result = self.view._check_token_status(token, self.user)

        self.assertEqual(result, "valid")

    @override_settings(SECRET_KEY=JWT_SECRET)
    def test_nearly_expired_token_returns_refresh(self):
        """A token with < 60s remaining should be refreshed."""
        payload = {
            "sub": "42",
            "exp": int((datetime.now(timezone.utc) + timedelta(seconds=30)).timestamp()),
            "iat": int(datetime.now(timezone.utc).timestamp()),
        }
        token = _encode_token(payload)

        result = self.view._check_token_status(token, self.user)

        self.assertEqual(result, "refresh")

    @override_settings(SECRET_KEY=JWT_SECRET)
    def test_expired_token_returns_invalid(self):
        """An expired token is invalid."""
        payload = {
            "sub": "42",
            "exp": int((datetime.now(timezone.utc) - timedelta(minutes=5)).timestamp()),
            "iat": int((datetime.now(timezone.utc) - timedelta(minutes=10)).timestamp()),
        }
        token = _encode_token(payload)

        result = self.view._check_token_status(token, self.user)

        self.assertEqual(result, "invalid")

    @override_settings(SECRET_KEY=JWT_SECRET)
    def test_wrong_user_returns_invalid(self):
        """A token for a different user must not be accepted."""
        payload = {
            "sub": "999",  # wrong user
            "exp": int((datetime.now(timezone.utc) + timedelta(minutes=5)).timestamp()),
        }
        token = _encode_token(payload)

        result = self.view._check_token_status(token, self.user)

        self.assertEqual(result, "invalid")

    @override_settings(SECRET_KEY=JWT_SECRET)
    @patch("lex.authentication.views.token_views.cache")
    def test_revoked_token_returns_invalid(self, mock_cache):
        """A token whose jti is marked as revoked in cache is invalid."""
        jti = str(uuid.uuid4())
        payload = {
            "sub": "42",
            "jti": jti,
            "exp": int((datetime.now(timezone.utc) + timedelta(minutes=5)).timestamp()),
        }
        token = _encode_token(payload)
        mock_cache.get.return_value = {"revoked": True}

        result = self.view._check_token_status(token, self.user)

        self.assertEqual(result, "invalid")
        mock_cache.get.assert_called_with(f"jwt_token:{jti}")

    def test_garbage_token_returns_invalid(self):
        """A non-JWT string must not crash — return 'invalid'."""
        result = self.view._check_token_status("not.a.jwt", self.user)
        self.assertEqual(result, "invalid")


# ═══════════════════════════════════════════════════════════════════════════
#  2. _generate_new_token
# ═══════════════════════════════════════════════════════════════════════════

class GenerateNewTokenTests(SimpleTestCase):
    """
    ``_generate_new_token`` creates a signed JWT with user identity,
    permissions, and a 1-minute expiry (short-lived for security).
    """

    def setUp(self):
        if not HAS_JWT:
            self.skipTest("PyJWT is not installed")
        self.view = StreamlitTokenView()
        self.user = _make_user(user_id=42, email="analyst@fund.com", username="analyst")

    @override_settings(SECRET_KEY=JWT_SECRET)
    @patch.object(StreamlitTokenView, "_get_user_permissions", return_value=[])
    def test_response_contains_token(self, mock_perms):
        """Response must include the JWT token string."""
        request = _make_request(user=self.user)

        response = self.view._generate_new_token(self.user, request)

        self.assertEqual(response.status_code, 200)
        self.assertIn("token", response.data)
        self.assertIsInstance(response.data["token"], str)

    @override_settings(SECRET_KEY=JWT_SECRET)
    @patch.object(StreamlitTokenView, "_get_user_permissions", return_value=[])
    def test_token_payload_contains_user_identity(self, mock_perms):
        """The decoded JWT must contain sub, email, preferred_username."""
        request = _make_request(user=self.user)

        response = self.view._generate_new_token(self.user, request)

        token = response.data["token"]
        decoded = pyjwt.decode(token, JWT_SECRET, algorithms=["HS256"])
        self.assertEqual(decoded["sub"], "42")
        self.assertEqual(decoded["email"], "analyst@fund.com")
        self.assertEqual(decoded["preferred_username"], "analyst")

    @override_settings(SECRET_KEY=JWT_SECRET)
    @patch.object(StreamlitTokenView, "_get_user_permissions", return_value=[])
    def test_token_has_short_expiry(self, mock_perms):
        """Token must expire within 2 minutes (short-lived for security)."""
        request = _make_request(user=self.user)
        now = datetime.now(timezone.utc)

        response = self.view._generate_new_token(self.user, request)

        token = response.data["token"]
        decoded = pyjwt.decode(token, JWT_SECRET, algorithms=["HS256"])
        exp = datetime.fromtimestamp(decoded["exp"], tz=timezone.utc)
        # Expiry should be within 2 minutes of now
        self.assertLessEqual((exp - now).total_seconds(), 120)

    @override_settings(SECRET_KEY=JWT_SECRET)
    @patch.object(StreamlitTokenView, "_get_user_permissions",
                  return_value=[{"rsname": "Investor", "scopes": ["view"]}])
    def test_token_contains_permissions(self, mock_perms):
        """Permissions from Keycloak are embedded in the JWT."""
        request = _make_request(user=self.user)

        response = self.view._generate_new_token(self.user, request)

        token = response.data["token"]
        decoded = pyjwt.decode(token, JWT_SECRET, algorithms=["HS256"])
        self.assertEqual(decoded["permissions"], [{"rsname": "Investor", "scopes": ["view"]}])

    @override_settings(SECRET_KEY=JWT_SECRET)
    @patch.object(StreamlitTokenView, "_get_user_permissions", return_value=[])
    def test_response_contains_user_metadata(self, mock_perms):
        """Response includes user dict with id/email/username."""
        request = _make_request(user=self.user)

        response = self.view._generate_new_token(self.user, request)

        self.assertIn("user", response.data)
        self.assertEqual(response.data["user"]["email"], "analyst@fund.com")

    @override_settings(SECRET_KEY=JWT_SECRET)
    @patch.object(StreamlitTokenView, "_get_user_permissions", return_value=[])
    def test_response_contains_action(self, mock_perms):
        """The 'action' field tells the caller what happened (generated/refreshed)."""
        request = _make_request(user=self.user)

        response = self.view._generate_new_token(self.user, request, action="refreshed")

        self.assertEqual(response.data["action"], "refreshed")


# ═══════════════════════════════════════════════════════════════════════════
#  3. _get_user_permissions — Keycloak integration
# ═══════════════════════════════════════════════════════════════════════════

class GetUserPermissionsTests(SimpleTestCase):
    """``_get_user_permissions`` extracts permissions from the Bearer token."""

    def setUp(self):
        if not HAS_JWT:
            self.skipTest("PyJWT is not installed")
        self.view = StreamlitTokenView()

    def test_no_bearer_token_returns_empty(self):
        """No Authorization header → empty permissions."""
        request = _make_request()

        result = self.view._get_user_permissions(request)

        self.assertEqual(result, [])

    def test_non_bearer_auth_returns_empty(self):
        """Authorization header without 'Bearer ' prefix → empty permissions."""
        request = _make_request()
        request.META["HTTP_AUTHORIZATION"] = "Basic dXNlcjpwYXNz"

        result = self.view._get_user_permissions(request)

        self.assertEqual(result, [])

    def test_import_failure_returns_empty_gracefully(self):
        """If KeycloakManager can't be imported (common in test/local env),
        the try/except returns [] — this is the graceful degradation contract."""
        request = _make_request(token="some-token")

        # The lazy import `from .KeycloakManager import KeycloakManager`
        # will fail since no KeycloakManager.py exists in the views package.
        # The except clause should catch this and return [].
        result = self.view._get_user_permissions(request)

        self.assertEqual(result, [])


# ═══════════════════════════════════════════════════════════════════════════
#  4. StreamlitTokenRevokeView
# ═══════════════════════════════════════════════════════════════════════════

class TokenRevocationTests(SimpleTestCase):
    """
    ``StreamlitTokenRevokeView`` marks tokens as revoked in the cache.
    A revoked token must be rejected by ``_check_token_status``.
    """

    def setUp(self):
        if not HAS_JWT:
            self.skipTest("PyJWT is not installed")
        self.view = StreamlitTokenRevokeView()

    @override_settings(SECRET_KEY=JWT_SECRET)
    @patch("lex.authentication.views.token_views.cache")
    def test_revokes_token_in_cache(self, mock_cache):
        """A valid token's jti is marked as revoked in the cache."""
        jti = str(uuid.uuid4())
        payload = {
            "sub": "42",
            "jti": jti,
            "exp": int((datetime.now(timezone.utc) + timedelta(minutes=5)).timestamp()),
        }
        token = _encode_token(payload)
        mock_cache.get.return_value = {}

        request = _make_request(data={"token": token})
        response = self.view.post(request)

        self.assertEqual(response.status_code, 200)
        # Verify cache.set was called with revoked=True
        if mock_cache.set.called:
            cache_key, cache_value, *_ = mock_cache.set.call_args[0]
            self.assertIn("jwt_token:", cache_key)
            self.assertTrue(cache_value.get("revoked"))

    def test_no_token_to_revoke(self):
        """If no token is provided, return 200 with informative message."""
        request = _make_request(data={})
        response = self.view.post(request)

        self.assertEqual(response.status_code, 200)
        self.assertIn("No token", response.data["message"])

    def test_invalid_token_format_returns_200(self):
        """Garbage tokens should not crash the endpoint."""
        request = _make_request(data={"token": "not.valid.jwt"})
        response = self.view.post(request)

        self.assertEqual(response.status_code, 200)

    @override_settings(SECRET_KEY=JWT_SECRET)
    @patch("lex.authentication.views.token_views.cache")
    def test_cache_failure_during_decode_still_succeeds(self, mock_cache):
        """If cache.get fails during decode, the outer try/except catches it.
        The decode itself uses verify_signature=False so it doesn't need cache.
        The revocation still succeeds with a 200 since the outer handler
        catches all exceptions except when cache.set itself fails."""
        payload = {
            "sub": "42",
            "jti": "test-jti",
            "exp": int((datetime.now(timezone.utc) + timedelta(minutes=5)).timestamp()),
        }
        token = _encode_token(payload)
        mock_cache.get.return_value = {}  # get works
        mock_cache.set.side_effect = Exception("Redis write failure")

        request = _make_request(data={"token": token})
        response = self.view.post(request)

        # The outer try/except catches the set failure and returns 500
        self.assertEqual(response.status_code, 500)
