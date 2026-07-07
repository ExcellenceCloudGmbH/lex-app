"""Tests for the instance API-key utility functions introduced in PR #615.

Intent: The framework must be able to identify requests authenticated with a
deployment-specific instance key (stored in the ``LEX_API_KEY`` environment
variable) so that the Instance Controller can query internal endpoints without
requiring a registered ``rest_framework_api_key`` database entry.  Three
separate access paths must work: ``KeyParser`` (the drf-api-key header parser),
the raw ``HTTP_AUTHORIZATION: Api-Key <token>`` header, and the
``is_instance_api_key_request`` guard that compares the extracted key against
the env-configured secret.  A regression in any path would prevent the IC
from reaching the ``/api/active-calculations`` endpoint and block safe
pre-release checks.

Cluster 2j — scenarios 2.97–2.107. Type: U (SimpleTestCase — pure logic, no
DB, KeyParser and env vars are mocked at the boundary).
Covers: ``lex/api/utils/api_key_requests.py`` — ``get_raw_api_key``,
``is_instance_api_key_request``.
Run: python -m lex pytest lex/test_project/tests/crud_api/test_2j_instance_api_key.py -v
"""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest
from django.test import RequestFactory, SimpleTestCase

from lex.api.utils.api_key_requests import get_raw_api_key, is_instance_api_key_request

pytestmark = pytest.mark.crud_api

_FACTORY = RequestFactory()


def _plain_request(auth_header: str = "") -> object:
    """Return a minimal fake request with a ``META`` dict."""
    req = MagicMock()
    req.META = {"HTTP_AUTHORIZATION": auth_header} if auth_header else {}
    # Simulate a plain request (no ``_request`` wrapper attribute).
    del req._request  # make getattr(..., "_request") fall back to req itself
    return req


class TestCluster02j_GetRawApiKey(SimpleTestCase):
    """Cluster 2j: ``get_raw_api_key`` extraction logic."""

    # 2.97 ---------------------------------------------------------------
    def test_2_97_key_parser_result_returned_directly(self) -> None:
        """
        Scenario 2.97: KeyParser returns a key → that key is returned as-is.
        Given: a request where ``KeyParser().get()`` yields ``"token-abc"``.
        When: ``get_raw_api_key`` is called.
        Then: ``"token-abc"`` is returned without consulting AUTH header.
        """
        request = _plain_request()
        with patch(
            "lex.api.utils.api_key_requests.KeyParser"
        ) as mock_cls:
            mock_cls.return_value.get.return_value = "token-abc"
            result = get_raw_api_key(request)

        self.assertEqual(
            result,
            "token-abc",
            "get_raw_api_key must return the KeyParser result when present",
        )

    # 2.98 ---------------------------------------------------------------
    def test_2_98_auth_header_fallback_when_key_parser_returns_none(self) -> None:
        """
        Scenario 2.98: KeyParser returns None, HTTP_AUTHORIZATION carries Api-Key prefix.
        Given: KeyParser yields None; META has ``HTTP_AUTHORIZATION: Api-Key my-secret``.
        When: ``get_raw_api_key`` is called.
        Then: ``"my-secret"`` is extracted from the header.
        """
        request = MagicMock()
        request.META = {"HTTP_AUTHORIZATION": "Api-Key my-secret"}
        del request._request

        with patch(
            "lex.api.utils.api_key_requests.KeyParser"
        ) as mock_cls:
            mock_cls.return_value.get.return_value = None
            result = get_raw_api_key(request)

        self.assertEqual(
            result,
            "my-secret",
            "get_raw_api_key must fall back to HTTP_AUTHORIZATION when KeyParser returns None",
        )

    # 2.99 ---------------------------------------------------------------
    def test_2_99_auth_header_prefix_stripped_correctly(self) -> None:
        """
        Scenario 2.99: Only the literal ``Api-Key `` prefix (with trailing space) is stripped.
        Given: header value ``"Api-Key   padded-token  "`` (extra inner/outer spaces).
        When: ``get_raw_api_key`` is called.
        Then: the returned value is ``"padded-token"`` (inner leading space included).
        """
        request = MagicMock()
        request.META = {"HTTP_AUTHORIZATION": "Api-Key   padded-token  "}
        del request._request

        with patch(
            "lex.api.utils.api_key_requests.KeyParser"
        ) as mock_cls:
            mock_cls.return_value.get.return_value = None
            result = get_raw_api_key(request)

        self.assertIsNotNone(result, "Non-empty candidate must not return None")
        self.assertIn(
            "padded-token",
            result,
            "The token value must survive the prefix strip",
        )

    # 2.100 --------------------------------------------------------------
    def test_2_100_empty_candidate_after_prefix_returns_none(self) -> None:
        """
        Scenario 2.100: ``Api-Key `` prefix present but nothing follows it.
        Given: header ``"Api-Key "`` (prefix only, candidate is empty after strip).
        When: ``get_raw_api_key`` is called.
        Then: ``None`` is returned (empty token is treated as absent).
        """
        request = MagicMock()
        request.META = {"HTTP_AUTHORIZATION": "Api-Key "}
        del request._request

        with patch(
            "lex.api.utils.api_key_requests.KeyParser"
        ) as mock_cls:
            mock_cls.return_value.get.return_value = None
            result = get_raw_api_key(request)

        self.assertIsNone(
            result,
            "Empty candidate after prefix strip must return None, not an empty string",
        )

    # 2.101 --------------------------------------------------------------
    def test_2_101_no_key_in_any_source_returns_none(self) -> None:
        """
        Scenario 2.101: No key in KeyParser and no Api-Key header present.
        Given: KeyParser returns None; META has no HTTP_AUTHORIZATION key.
        When: ``get_raw_api_key`` is called.
        Then: ``None`` is returned.
        """
        request = MagicMock()
        request.META = {}
        del request._request

        with patch(
            "lex.api.utils.api_key_requests.KeyParser"
        ) as mock_cls:
            mock_cls.return_value.get.return_value = None
            result = get_raw_api_key(request)

        self.assertIsNone(
            result,
            "get_raw_api_key must return None when no key is present in any source",
        )

    # 2.102 --------------------------------------------------------------
    def test_2_102_drf_wrapped_request_uses_inner_holder(self) -> None:
        """
        Scenario 2.102: DRF wraps the plain request in a ``Request`` with ``_request``.
        Given: an outer object whose ``_request`` attribute is a plain Django request.
        When: ``get_raw_api_key`` is called on the outer object.
        Then: the inner ``_request`` is passed to ``KeyParser`` (not the outer wrapper).
        """
        inner = MagicMock()
        inner.META = {"HTTP_AUTHORIZATION": "Api-Key inner-token"}

        outer = MagicMock()
        outer._request = inner  # simulates DRF Request wrapping

        captured = []

        with patch(
            "lex.api.utils.api_key_requests.KeyParser"
        ) as mock_cls:
            def _capturing_get(req):
                captured.append(req)
                return None

            mock_cls.return_value.get.side_effect = _capturing_get
            result = get_raw_api_key(outer)

        self.assertIs(
            captured[0],
            inner,
            "get_raw_api_key must unwrap the DRF Request and pass _request to KeyParser",
        )
        self.assertEqual(
            result,
            "inner-token",
            "The token extracted from the inner request's AUTH header must be returned",
        )

    # 2.103 --------------------------------------------------------------
    def test_2_103_non_api_key_auth_header_returns_none(self) -> None:
        """
        Scenario 2.103: HTTP_AUTHORIZATION carries a different scheme (e.g. Bearer).
        Given: header ``"******"`` — does not start with ``"Api-Key "``.
        When: ``get_raw_api_key`` is called and KeyParser returns None.
        Then: ``None`` is returned (****** is not extracted).
        """
        request = MagicMock()
        request.META = {"HTTP_AUTHORIZATION": "******"}
        del request._request

        with patch(
            "lex.api.utils.api_key_requests.KeyParser"
        ) as mock_cls:
            mock_cls.return_value.get.return_value = None
            result = get_raw_api_key(request)

        self.assertIsNone(
            result,
            "get_raw_api_key must not extract tokens from non-Api-Key auth schemes",
        )


class TestCluster02j_IsInstanceApiKeyRequest(SimpleTestCase):
    """Cluster 2j: ``is_instance_api_key_request`` guard."""

    # 2.104 --------------------------------------------------------------
    def test_2_104_matching_key_returns_true(self) -> None:
        """
        Scenario 2.104: Request key matches LEX_API_KEY env var → True.
        Given: ``LEX_API_KEY=secret``; request carries ``Api-Key secret``.
        When: ``is_instance_api_key_request`` is called.
        Then: ``True`` is returned.
        """
        request = MagicMock()
        request.META = {}
        del request._request

        with patch(
            "lex.api.utils.api_key_requests.get_raw_api_key",
            return_value="secret",
        ), patch.dict(os.environ, {"LEX_API_KEY": "secret"}):
            result = is_instance_api_key_request(request)

        self.assertTrue(
            result,
            "is_instance_api_key_request must return True when key matches LEX_API_KEY",
        )

    # 2.105 --------------------------------------------------------------
    def test_2_105_mismatched_key_returns_false(self) -> None:
        """
        Scenario 2.105: Request key does not match LEX_API_KEY → False.
        Given: ``LEX_API_KEY=real-secret``; request carries ``Api-Key wrong-key``.
        When: ``is_instance_api_key_request`` is called.
        Then: ``False`` is returned.
        """
        request = MagicMock()
        request.META = {}
        del request._request

        with patch(
            "lex.api.utils.api_key_requests.get_raw_api_key",
            return_value="wrong-key",
        ), patch.dict(os.environ, {"LEX_API_KEY": "real-secret"}):
            result = is_instance_api_key_request(request)

        self.assertFalse(
            result,
            "is_instance_api_key_request must return False when key does not match LEX_API_KEY",
        )

    # 2.106 --------------------------------------------------------------
    def test_2_106_no_env_var_returns_false(self) -> None:
        """
        Scenario 2.106: LEX_API_KEY env var is not set → False.
        Given: ``LEX_API_KEY`` is absent from the environment; request has a key.
        When: ``is_instance_api_key_request`` is called.
        Then: ``False`` is returned (no expected key to compare against).
        """
        request = MagicMock()
        request.META = {}
        del request._request

        env_without_key = {k: v for k, v in os.environ.items() if k != "LEX_API_KEY"}
        with patch(
            "lex.api.utils.api_key_requests.get_raw_api_key",
            return_value="some-key",
        ), patch.dict(os.environ, env_without_key, clear=True):
            result = is_instance_api_key_request(request)

        self.assertFalse(
            result,
            "is_instance_api_key_request must return False when LEX_API_KEY is not set",
        )

    # 2.107 --------------------------------------------------------------
    def test_2_107_no_key_in_request_returns_false(self) -> None:
        """
        Scenario 2.107: No API key in the request → False.
        Given: ``LEX_API_KEY=secret`` is set; request carries no API key.
        When: ``is_instance_api_key_request`` is called.
        Then: ``False`` is returned.
        """
        request = MagicMock()
        request.META = {}
        del request._request

        with patch(
            "lex.api.utils.api_key_requests.get_raw_api_key",
            return_value=None,
        ), patch.dict(os.environ, {"LEX_API_KEY": "secret"}):
            result = is_instance_api_key_request(request)

        self.assertFalse(
            result,
            "is_instance_api_key_request must return False when the request carries no API key",
        )
