"""MCP tools for permission introspection.

Wrap the existing DRF permission views so an LLM client can check what
the current principal is allowed to do **before** attempting CRUD or
calculation tools, and explain why a write was refused.

* ``lex_permissions_user``  → :class:`lex.api.views.authentication.UserPermissionView.UserPermissionsView`
* ``lex_permissions_model`` → :class:`lex.api.views.permissions.ModelPermissions.ModelPermissions`
"""
from __future__ import annotations

import logging
from typing import Any, Dict

from mcp.server.fastmcp import FastMCP

from lex.mcp_server.dispatch import call_view
from lex.mcp_server.tools._common import (
    current_principal,
    envelope as _envelope,
    require_container as _require_container,
)

logger = logging.getLogger(__name__)


def register(server: FastMCP) -> None:
    server.add_tool(
        _user_permissions,
        name="lex_permissions_user",
        description=(
            "Return the current principal's permissions in the ra-rbac shape "
            "[{action, resource, record?}]. Backed by Keycloak UMA when "
            "authenticated via Bearer token; for API-KEY auth it reflects the "
            "scopes attached to the key's user."
        ),
    )
    server.add_tool(
        _model_permissions,
        name="lex_permissions_model",
        description=(
            "Return the per-container modification restrictions that apply to "
            "the current principal for `model_container`. Useful to check "
            "before issuing `lex_entries_create/update/delete`."
        ),
    )


async def _user_permissions() -> Dict[str, Any]:
    principal = current_principal()
    from lex.api.views.authentication.UserPermissionView import UserPermissionsView

    status_code, payload = await call_view(
        UserPermissionsView,
        principal=principal,
        method="GET",
        view_kwargs={},
    )
    return _envelope(status_code, payload)


async def _model_permissions(model_container: str) -> Dict[str, Any]:
    container = _require_container(model_container)
    principal = current_principal()
    from lex.api.views.permissions.ModelPermissions import ModelPermissions

    status_code, payload = await call_view(
        ModelPermissions,
        principal=principal,
        method="GET",
        view_kwargs={"model_container": container},
    )
    return _envelope(status_code, payload)
