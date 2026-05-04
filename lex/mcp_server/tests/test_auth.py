"""Header-level auth-resolution tests for the MCP server.

These tests are deliberately lightweight: they only exercise the
header parsing + plug-in points of :mod:`lex.mcp_server.auth` without
touching Keycloak. Full end-to-end coverage (API-key DB lookups,
Keycloak introspection) belongs in integration tests run against a
real ``rest_framework_api_key`` table and a stubbed
:class:`KeycloakManager`.
"""
from __future__ import annotations

from unittest import mock

import pytest

from lex.mcp_server.auth import (
    McpAuthError,
    _resolve_api_key_principal,
    resolve_principal_sync,
)
from lex.mcp_server.context import McpPrincipal


def _h(d: dict[str, str]) -> dict[bytes, bytes]:
    return {k.lower().encode("latin-1"): v.encode("latin-1") for k, v in d.items()}


def test_missing_credentials_raises():
    with pytest.raises(McpAuthError):
        resolve_principal_sync(_h({}))


def test_api_key_branch_invokes_lookup_and_short_circuits_bearer():
    fake = McpPrincipal(user=mock.Mock(is_authenticated=True), auth_kind="api_key")
    with mock.patch(
        "lex.mcp_server.auth._resolve_api_key_principal", return_value=fake
    ) as api_key_lookup, mock.patch(
        "lex.mcp_server.auth._resolve_bearer_principal"
    ) as bearer_lookup:
        principal = resolve_principal_sync(_h({"API-KEY": "raw-key", "Authorization": "Bearer x"}))
    assert principal is fake
    api_key_lookup.assert_called_once_with("raw-key")
    bearer_lookup.assert_not_called()


def test_invalid_api_key_raises_with_apikey_challenge():
    with mock.patch("lex.mcp_server.auth._resolve_api_key_principal", return_value=None):
        with pytest.raises(McpAuthError) as exc:
            resolve_principal_sync(_h({"API-KEY": "bogus"}))
    assert exc.value.www_authenticate == "ApiKey"


def test_bearer_branch_when_no_api_key():
    fake = McpPrincipal(user=mock.Mock(is_authenticated=True), auth_kind="oidc_bearer")
    with mock.patch(
        "lex.mcp_server.auth._resolve_bearer_principal", return_value=fake
    ) as bearer_lookup:
        principal = resolve_principal_sync(_h({"Authorization": "Bearer some.jwt"}))
    assert principal is fake
    bearer_lookup.assert_called_once_with("some.jwt")
