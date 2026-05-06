"""MCP tool that exposes the bitemporal history timeline for a record."""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

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
        _entry_history,
        name="lex_entries_history",
        description=(
            "Return the simple_history timeline for one record. Pass an "
            "ISO-8601 `as_of` to time-travel to a system-time snapshot. "
            "`limit` caps the number of returned rows (newest-first)."
        ),
    )


async def _entry_history(
    model_container: str,
    pk: Any,
    as_of: Optional[str] = None,
    limit: Optional[int] = None,
) -> Dict[str, Any]:
    container = _require_container(model_container)
    principal = current_principal()

    from lex.api.views.model_entries.History import HistoryModelEntry

    query: Dict[str, Any] = {}
    if as_of:
        query["as_of"] = as_of

    status_code, payload = await call_view(
        HistoryModelEntry,
        principal=principal,
        method="GET",
        view_kwargs={"model_container": container},
        pk=pk,
        query=query,
    )

    if isinstance(payload, list) and limit and limit > 0 and len(payload) > limit:
        payload = payload[:limit]

    return _envelope(status_code, payload)
