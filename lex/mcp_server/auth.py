"""Resolve incoming MCP HTTP requests to a :class:`McpPrincipal`.

Two authentication paths are supported on every request, evaluated in
order:

1. ``API-KEY: <raw key>`` header → looked up via
   ``rest_framework_api_key`` exactly the way
   :func:`lex.api.utils.api_key_requests.get_api_key_request_identity`
   does for normal HTTP requests.
2. ``Authorization: Bearer <jwt>`` → introspected via
   :class:`lex.api.views.authentication.KeycloakManager.KeycloakManager`
   to pull the Django user, UMA permissions, ``userinfo`` and client
   roles, mirroring
   :class:`lex.api.middleware.keycloak_permissions.KeycloakPermissionsMiddleware`.
"""
from __future__ import annotations

import logging
from typing import Mapping, Optional

from asgiref.sync import sync_to_async

from lex.mcp_server.context import McpPrincipal

logger = logging.getLogger(__name__)


class McpAuthError(Exception):
    """Raised when no valid credential is present on an MCP request."""

    def __init__(self, message: str, *, www_authenticate: str = "Bearer"):
        super().__init__(message)
        self.www_authenticate = www_authenticate


def _header(headers: Mapping[bytes, bytes], name: str) -> Optional[str]:
    raw = headers.get(name.lower().encode("latin-1"))
    if raw is None:
        return None
    try:
        return raw.decode("latin-1")
    except UnicodeDecodeError:
        return None


def _resolve_api_key_principal(raw_key: str) -> Optional[McpPrincipal]:
    from rest_framework_api_key.models import APIKey

    from lex.api.utils.api_key_requests import TechnicalAPIKeyUser

    try:
        api_key = APIKey.objects.get_from_key(raw_key)
    except Exception:
        return None

    name = (str(api_key).strip() or "Technical User")
    return McpPrincipal(
        user=TechnicalAPIKeyUser(name),
        auth_kind="api_key",
        api_key_name=name,
    )


def _extract_client_roles(userinfo: dict) -> list[str]:
    raw_roles = userinfo.get("client_roles")
    if not raw_roles:
        return []
    if isinstance(raw_roles, str):
        return [raw_roles]
    if isinstance(raw_roles, (list, tuple, set, frozenset)):
        return [r for r in raw_roles if isinstance(r, str)]
    if isinstance(raw_roles, dict):
        out: list[str] = []
        for value in raw_roles.values():
            if isinstance(value, str):
                out.append(value)
            elif isinstance(value, (list, tuple, set, frozenset)):
                out.extend([r for r in value if isinstance(r, str)])
        return out
    return []


def _resolve_bearer_principal(access_token: str) -> Optional[McpPrincipal]:
    from django.contrib.auth import get_user_model

    from lex.api.views.authentication.KeycloakManager import KeycloakManager

    kc = KeycloakManager()
    if not kc.oidc:
        logger.warning("MCP bearer auth attempted but Keycloak OIDC client is unconfigured")
        return None

    try:
        userinfo = kc.oidc.userinfo(access_token)
    except Exception as exc:
        logger.info("MCP bearer auth: userinfo lookup failed: %s", exc)
        return None
    if not isinstance(userinfo, dict):
        return None

    username = (
        userinfo.get("preferred_username")
        or userinfo.get("email")
        or userinfo.get("sub")
    )
    if not username:
        return None

    User = get_user_model()
    user, _created = User.objects.get_or_create(
        username=username,
        defaults={
            "email": userinfo.get("email", ""),
            "first_name": userinfo.get("given_name", ""),
            "last_name": userinfo.get("family_name", ""),
        },
    )

    permissions: list = []
    try:
        permissions = kc.get_uma_permissions(access_token) or []
    except Exception as exc:
        logger.warning("MCP bearer auth: UMA permission fetch failed: %s", exc)

    return McpPrincipal(
        user=user,
        auth_kind="oidc_bearer",
        user_permissions=permissions,
        userinfo=userinfo,
        client_roles=_extract_client_roles(userinfo),
        access_token=access_token,
    )


def resolve_principal_sync(headers: Mapping[bytes, bytes]) -> McpPrincipal:
    """Synchronous principal resolution. Raises :class:`McpAuthError` on failure."""
    from lex.mcp_server.observability import record_auth

    api_key = _header(headers, "API-KEY")
    if api_key:
        principal = _resolve_api_key_principal(api_key.strip())
        if principal:
            record_auth("ok", auth_kind="api_key", principal_id=principal.api_key_name)
            return principal
        record_auth("denied", auth_kind="api_key", reason="invalid_api_key")
        raise McpAuthError("Invalid API key", www_authenticate="ApiKey")

    auth_header = _header(headers, "Authorization") or ""
    if auth_header.lower().startswith("bearer "):
        token = auth_header[7:].strip()
        principal = _resolve_bearer_principal(token)
        if principal:
            sub = principal.userinfo.get("sub") if principal.userinfo else None
            record_auth("ok", auth_kind="oidc_bearer", principal_id=sub)
            return principal
        record_auth("denied", auth_kind="oidc_bearer", reason="invalid_or_expired")
        raise McpAuthError("Invalid or expired bearer token")

    record_auth("denied", auth_kind=None, reason="missing_credentials")
    raise McpAuthError(
        "Missing credentials: provide either 'API-KEY' header or 'Authorization: Bearer <token>'"
    )


resolve_principal = sync_to_async(resolve_principal_sync, thread_sensitive=True)
