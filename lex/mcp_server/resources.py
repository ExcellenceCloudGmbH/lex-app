"""Read-only MCP resources backed by existing DRF views.

Resources let MCP clients pre-load reference material into the chat
context with stable URIs (RFC 3986). The server advertises a small,
curated set covering project-level metadata and per-model schemas; for
record-level data we provide a templated resource so any single entry
can be attached by URI.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.resources import FunctionResource

from lex.mcp_server.config import mcp_setting
from lex.mcp_server.dispatch import call_view
from lex.mcp_server.tools._common import current_principal, require_container

logger = logging.getLogger(__name__)


def register(server: FastMCP) -> None:
    server.add_resource(
        FunctionResource(
            uri="lex://project/info",
            name="Project info",
            description="Static metadata about the deployed lex-app instance.",
            mime_type="application/json",
            fn=_project_info,
        )
    )
    server.add_resource(
        FunctionResource(
            uri="lex://models/structure",
            name="Model structure",
            description="Hierarchical structure of all registered model containers (with readable names).",
            mime_type="application/json",
            fn=_model_structure,
        )
    )
    server.add_resource(
        FunctionResource(
            uri="lex://widgets/structure",
            name="Widget structure",
            description="Configured widget structure used by the React admin UI.",
            mime_type="application/json",
            fn=_widget_structure,
        )
    )

    # Templated resources: URIs with placeholders are registered via the
    # @server.resource(...) decorator so FastMCP can build a ResourceTemplate
    # for them.
    @server.resource(
        "lex://models/{container}/fields",
        name="Model fields",
        description="DRF/serializer field metadata for a single model container.",
        mime_type="application/json",
    )
    async def _container_fields(container: str) -> str:
        return await _read_container_fields(container)

    @server.resource(
        "lex://entries/{container}/{pk}",
        name="Single entry",
        description="A single record from a model container, serialised as JSON.",
        mime_type="application/json",
    )
    async def _entry_resource(container: str, pk: str) -> str:
        return await _read_entry(container, pk)

    if mcp_setting("ENABLE_PERMISSIONS"):
        server.add_resource(
            FunctionResource(
                uri="lex://permissions/user",
                name="Current principal permissions",
                description="ra-rbac formatted permissions for the current MCP principal.",
                mime_type="application/json",
                fn=_user_permissions,
            )
        )

        @server.resource(
            "lex://permissions/{container}",
            name="Model container permissions",
            description="Modification restrictions for a single model container as resolved for the current principal.",
            mime_type="application/json",
        )
        async def _container_permissions(container: str) -> str:
            return await _read_container_permissions(container)


# --------------------------------------------------------------------------- #
# Implementation                                                              #
# --------------------------------------------------------------------------- #


def _to_json(value: Any) -> str:
    return json.dumps(value, default=str, ensure_ascii=False)


async def _project_info() -> str:
    principal = current_principal()
    from lex.api.views.project_info.ProjectInfo import ProjectInfo

    _, payload = await call_view(
        ProjectInfo,
        principal=principal,
        method="GET",
        view_kwargs={},
    )
    return _to_json(payload)


async def _model_structure() -> str:
    principal = current_principal()
    from lex.process_admin.settings import processAdminSite
    from lex.process_admin.views.model_relation_views import ModelStructureObtainView

    if not processAdminSite.initialized:
        _ = processAdminSite.urls

    _, payload = await call_view(
        ModelStructureObtainView,
        principal=principal,
        method="GET",
        view_kwargs={},
        view_init_kwargs={
            "get_container_func": processAdminSite.get_container_func,
            "get_hidden_historical_models_func": processAdminSite.get_hidden_historical_models_func,
            "get_model_structure_func": processAdminSite.get_model_structure_func,
        },
    )
    return _to_json(payload)


async def _widget_structure() -> str:
    principal = current_principal()
    from lex.api.views.model_info.Widgets import Widgets

    _, payload = await call_view(
        Widgets,
        principal=principal,
        method="GET",
        view_kwargs={},
    )
    return _to_json(payload)


async def _read_container_fields(container_id: str) -> str:
    principal = current_principal()
    container = require_container(container_id)
    from lex.api.views.model_info.Fields import Fields

    _, payload = await call_view(
        Fields,
        principal=principal,
        method="GET",
        view_kwargs={"model_container": container},
    )
    return _to_json(payload)


async def _read_entry(container_id: str, pk: str) -> str:
    principal = current_principal()
    container = require_container(container_id)
    from lex.api.views.model_entries.One import OneModelEntry

    _, payload = await call_view(
        OneModelEntry,
        principal=principal,
        method="GET",
        view_kwargs={"model_container": container, "calculationId": "manual"},
        pk=pk,
    )
    return _to_json(payload)


async def _user_permissions() -> str:
    principal = current_principal()
    from lex.api.views.authentication.UserPermissionView import UserPermissionsView

    _, payload = await call_view(
        UserPermissionsView,
        principal=principal,
        method="GET",
        view_kwargs={},
    )
    return _to_json(payload)


async def _read_container_permissions(container_id: str) -> str:
    principal = current_principal()
    container = require_container(container_id)
    from lex.api.views.permissions.ModelPermissions import ModelPermissions

    _, payload = await call_view(
        ModelPermissions,
        principal=principal,
        method="GET",
        view_kwargs={"model_container": container},
    )
    return _to_json(payload)
