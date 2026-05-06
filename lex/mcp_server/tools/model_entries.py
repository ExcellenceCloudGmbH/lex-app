"""MCP tools that expose CRUD over registered model containers.

Tools are registered on the FastMCP instance dynamically — one bundle
per container — so every Lex business model is reachable without
per-model boilerplate while every call still goes through the existing
DRF view (and therefore the existing permission, audit and history
machinery).
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from mcp.server.fastmcp import FastMCP

from lex.mcp_server.config import mcp_setting
from lex.mcp_server.registry import container_is_writable, exposed_containers
from lex.mcp_server.dispatch import call_view
from lex.mcp_server.tools._common import (
    current_principal,
    ensure_writable as _ensure_writable,
    envelope as _envelope,
    require_container as _require_container,
)

logger = logging.getLogger(__name__)


def register(server: FastMCP) -> None:
    """Register one CRUD bundle per exposed container."""
    enable_write = mcp_setting("ENABLE_WRITE")

    server.add_tool(
        _list_models,
        name="lex_models_list",
        description="List all model containers exposed over MCP, with read/write capability flags.",
    )
    server.add_tool(
        _list_entries,
        name="lex_entries_list",
        description=(
            "Page through entries of a model container. "
            "Filters use the same query-parameter syntax as the HTTP API "
            "(e.g. {'name__icontains': 'foo'}). RBAC and read-restrictions "
            "are enforced server-side."
        ),
    )
    server.add_tool(
        _get_entry,
        name="lex_entries_get",
        description="Retrieve a single entry by primary key from a model container.",
    )

    if enable_write:
        server.add_tool(
            _create_entry,
            name="lex_entries_create",
            description=(
                "Create a new entry in a model container. The 'data' object is "
                "validated by the model's DRF serializer."
            ),
        )
        server.add_tool(
            _update_entry,
            name="lex_entries_update",
            description=(
                "Partially update an existing entry. Pass `calculate=true` to "
                "trigger a recalculation for CalculationModel-backed records."
            ),
        )
        server.add_tool(
            _delete_entry,
            name="lex_entries_delete",
            description="Delete an entry by primary key.",
        )


# --------------------------------------------------------------------------- #
# Tool implementations                                                        #
# --------------------------------------------------------------------------- #


async def _list_models() -> List[Dict[str, Any]]:
    """Return metadata for every exposed model container."""
    # Touch the principal so unauthenticated traffic still cannot
    # enumerate the model surface.
    _ = current_principal()

    out: List[Dict[str, Any]] = []
    for container in exposed_containers():
        model_class = container.model_class
        meta = getattr(model_class, "_meta", None)
        out.append(
            {
                "id": container.id,
                "model": getattr(meta, "model_name", container.id),
                "app_label": getattr(meta, "app_label", ""),
                "verbose_name": str(getattr(meta, "verbose_name", container.id)),
                "writable": container_is_writable(container.id),
                "pk_field": getattr(getattr(meta, "pk", None), "name", "id"),
            }
        )
    return out


async def _list_entries(
    model_container: str,
    filters: Optional[Dict[str, Any]] = None,
    ordering: Optional[str] = None,
    page: int = 1,
    page_size: Optional[int] = None,
) -> Dict[str, Any]:
    """Page through entries of a model container."""
    container = _require_container(model_container)
    principal = current_principal()

    max_page = mcp_setting("MAX_PAGE_SIZE")
    default_page = mcp_setting("DEFAULT_PAGE_SIZE")
    effective = min(max(int(page_size or default_page), 1), int(max_page))

    query: Dict[str, Any] = {"page": int(page), "perPage": effective}
    if ordering:
        query["ordering"] = ordering
    for key, value in (filters or {}).items():
        query[key] = value

    from lex.api.views.model_entries.List import ListModelEntries

    status_code, payload = await call_view(
        ListModelEntries,
        principal=principal,
        method="GET",
        view_kwargs={"model_container": container},
        query=query,
    )
    return _envelope(status_code, payload)


async def _get_entry(model_container: str, pk: Any) -> Dict[str, Any]:
    container = _require_container(model_container)
    principal = current_principal()

    from lex.api.views.model_entries.One import OneModelEntry

    status_code, payload = await call_view(
        OneModelEntry,
        principal=principal,
        method="GET",
        view_kwargs={"model_container": container, "calculationId": "manual"},
        pk=pk,
    )
    return _envelope(status_code, payload)


async def _create_entry(
    model_container: str,
    data: Dict[str, Any],
    calculate: bool = False,
) -> Dict[str, Any]:
    container = _require_container(model_container)
    _ensure_writable(model_container)
    principal = current_principal()

    from lex.api.views.model_entries.One import OneModelEntry

    body = dict(data)
    if calculate:
        body["calculate"] = True

    status_code, payload = await call_view(
        OneModelEntry,
        principal=principal,
        method="POST",
        view_kwargs={"model_container": container, "calculationId": "manual"},
        body=body,
    )
    return _envelope(status_code, payload)


async def _update_entry(
    model_container: str,
    pk: Any,
    data: Dict[str, Any],
    calculate: bool = False,
    partial: bool = True,
) -> Dict[str, Any]:
    container = _require_container(model_container)
    _ensure_writable(model_container)
    principal = current_principal()

    from lex.api.views.model_entries.One import OneModelEntry

    body = dict(data)
    if calculate:
        body["calculate"] = True

    status_code, payload = await call_view(
        OneModelEntry,
        principal=principal,
        method="PATCH" if partial else "PUT",
        view_kwargs={"model_container": container, "calculationId": "manual"},
        pk=pk,
        body=body,
    )
    return _envelope(status_code, payload)


async def _delete_entry(
    model_container: str,
    pk: Any,
    payload: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    container = _require_container(model_container)
    _ensure_writable(model_container)
    principal = current_principal()

    from lex.api.views.model_entries.One import OneModelEntry

    status_code, result = await call_view(
        OneModelEntry,
        principal=principal,
        method="DELETE",
        view_kwargs={"model_container": container, "calculationId": "manual"},
        pk=pk,
        body=payload,
    )
    return _envelope(status_code, result)
