"""Per-request principal context for MCP tool invocations.

The principal (resolved by :mod:`lex.mcp_server.auth`) is stashed in a
:class:`contextvars.ContextVar` for the duration of an HTTP request so
that any tool callback can recover it via :func:`current_principal`
without having to thread it through every call site.
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, List, Optional


@dataclass(frozen=True)
class McpPrincipal:
    """Identity + RBAC bundle for one MCP request.

    ``user`` is a Django ``User`` instance for OIDC requests, or a
    ``TechnicalAPIKeyUser`` for API-key requests. Either way it is
    truthy and ``is_authenticated`` returns ``True`` so the existing
    DRF permission classes treat it uniformly.
    """

    user: Any
    auth_kind: str  # "api_key" | "oidc_bearer"
    user_permissions: List[Any] = field(default_factory=list)
    userinfo: dict = field(default_factory=dict)
    client_roles: List[str] = field(default_factory=list)
    api_key_name: Optional[str] = None
    access_token: Optional[str] = None


_current: ContextVar[Optional[McpPrincipal]] = ContextVar(
    "lex_mcp_current_principal", default=None
)


def current_principal() -> McpPrincipal:
    """Return the principal bound to the current MCP request.

    Raises ``RuntimeError`` if called outside an authenticated request
    scope; tool implementations should never see that because the auth
    middleware short-circuits unauthenticated requests with HTTP 401.
    """
    principal = _current.get()
    if principal is None:
        raise RuntimeError(
            "No MCP principal bound; tool invoked outside an authenticated MCP request."
        )
    return principal


@contextmanager
def bind_principal(principal: McpPrincipal):
    token = _current.set(principal)
    try:
        yield
    finally:
        _current.reset(token)
