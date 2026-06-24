"""Tests for the instance-API-key bypass added to ``ApiKeyAwareLoginRequiredMiddleware``.

Intent: The middleware sits in front of every request that is not yet logged
in via OIDC.  PR #615 extended it so that requests authenticated with the
deployment-specific ``LEX_API_KEY`` are also waved through, in addition to
the pre-existing ``rest_framework_api_key`` database-entry path.  Without
this bypass the Instance Controller — which uses the raw env-var key — would
be stopped by the login redirect before reaching any DRF permission check.
A regression here would mean the IC could never call ``/api/active-calculations``
even when it presents a valid key.

Cluster 4m — scenarios 4.66–4.72. Type: U (SimpleTestCase — the OIDC
middleware ``LoginRequiredMiddleware`` is mocked at its boundary so no live
auth backend is needed; request objects are minimal fakes).
Covers: ``lex/authentication/middleware.py`` —
``ApiKeyAwareLoginRequiredMiddleware.check_login_required``.
Run: python -m lex pytest lex/test_project/tests/permissions/test_4m_api_key_middleware.py -v
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch, call

import pytest
from django.test import RequestFactory, SimpleTestCase

from lex.authentication.middleware import ApiKeyAwareLoginRequiredMiddleware

pytestmark = pytest.mark.permissions

_FACTORY = RequestFactory()

_LOGIN_REQUIRED_PATH = (
    "oauth2_authcodeflow.middleware.LoginRequiredMiddleware.check_login_required"
)


class TestCluster04m_ApiKeyAwareMiddleware(SimpleTestCase):
    """Cluster 4m: ``ApiKeyAwareLoginRequiredMiddleware`` bypass contract."""

    def _make_middleware(self):
        """Return an instance of the middleware under test."""
        return ApiKeyAwareLoginRequiredMiddleware(get_response=lambda r: None)

    # 4.66 ---------------------------------------------------------------
    def test_4_66_instance_api_key_bypasses_login_check(self) -> None:
        """
        Scenario 4.66: Instance API-key request is allowed through without login.
        Given: ``is_instance_api_key_request`` returns True for the request.
        When: ``check_login_required`` is called.
        Then: the method returns ``None`` (no redirect); the parent's
              ``check_login_required`` is NOT called.
        """
        middleware = self._make_middleware()
        request = MagicMock()

        with patch(
            "lex.authentication.middleware.is_instance_api_key_request",
            return_value=True,
        ), patch(
            "lex.authentication.middleware.is_api_key_request",
            return_value=False,
        ), patch(_LOGIN_REQUIRED_PATH) as parent_mock:
            result = middleware.check_login_required(request)

        self.assertIsNone(
            result,
            "check_login_required must return None (pass-through) for instance API key requests",
        )
        parent_mock.assert_not_called()

    # 4.67 ---------------------------------------------------------------
    def test_4_67_drf_api_key_still_bypasses_login_check(self) -> None:
        """
        Scenario 4.67: Existing drf-api-key database-entry path still works.
        Given: ``is_api_key_request`` returns True; instance check returns False.
        When: ``check_login_required`` is called.
        Then: returns ``None``; parent NOT called (pre-existing behaviour preserved).
        """
        middleware = self._make_middleware()
        request = MagicMock()

        with patch(
            "lex.authentication.middleware.is_api_key_request",
            return_value=True,
        ), patch(
            "lex.authentication.middleware.is_instance_api_key_request",
            return_value=False,
        ), patch(_LOGIN_REQUIRED_PATH) as parent_mock:
            result = middleware.check_login_required(request)

        self.assertIsNone(
            result,
            "DRF API-key path must still bypass login — pre-existing contract must not regress",
        )
        parent_mock.assert_not_called()

    # 4.68 ---------------------------------------------------------------
    def test_4_68_non_api_key_request_delegates_to_parent(self) -> None:
        """
        Scenario 4.68: Neither API-key check passes → parent middleware decides.
        Given: both ``is_api_key_request`` and ``is_instance_api_key_request`` return False.
        When: ``check_login_required`` is called.
        Then: the parent ``check_login_required`` is called exactly once with the request.
        """
        middleware = self._make_middleware()
        request = MagicMock()

        with patch(
            "lex.authentication.middleware.is_api_key_request",
            return_value=False,
        ), patch(
            "lex.authentication.middleware.is_instance_api_key_request",
            return_value=False,
        ), patch(_LOGIN_REQUIRED_PATH, return_value=None) as parent_mock:
            middleware.check_login_required(request)

        parent_mock.assert_called_once_with(request)

    # 4.69 ---------------------------------------------------------------
    def test_4_69_instance_key_check_evaluated_when_drf_check_false(self) -> None:
        """
        Scenario 4.69: ``is_instance_api_key_request`` is consulted even when
        ``is_api_key_request`` is False.
        Given: DRF API-key check → False; instance key check → True.
        When: ``check_login_required`` is called.
        Then: returns ``None`` — instance key path alone is sufficient.
        """
        middleware = self._make_middleware()
        request = MagicMock()

        with patch(
            "lex.authentication.middleware.is_api_key_request",
            return_value=False,
        ), patch(
            "lex.authentication.middleware.is_instance_api_key_request",
            return_value=True,
        ), patch(_LOGIN_REQUIRED_PATH) as parent_mock:
            result = middleware.check_login_required(request)

        self.assertIsNone(result)
        parent_mock.assert_not_called()

    # 4.70 ---------------------------------------------------------------
    def test_4_70_middleware_subclasses_login_required_middleware(self) -> None:
        """
        Scenario 4.70: The middleware class relationship is correct.
        Given: the class definition of ``ApiKeyAwareLoginRequiredMiddleware``.
        When: its MRO is inspected.
        Then: it inherits from ``oauth2_authcodeflow.middleware.LoginRequiredMiddleware``.
        """
        from oauth2_authcodeflow.middleware import LoginRequiredMiddleware

        self.assertTrue(
            issubclass(ApiKeyAwareLoginRequiredMiddleware, LoginRequiredMiddleware),
            "ApiKeyAwareLoginRequiredMiddleware must be a LoginRequiredMiddleware subclass "
            "so it inherits the full OIDC redirect logic",
        )
