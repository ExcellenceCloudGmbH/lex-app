"""Global PostgreSQL full-text search exposed via MCP."""
from __future__ import annotations

import logging
from typing import Any, Dict

from mcp.server.fastmcp import FastMCP

from lex.mcp_server.dispatch import call_view
from lex.mcp_server.tools._common import current_principal, envelope as _envelope

logger = logging.getLogger(__name__)


def register(server: FastMCP) -> None:
    server.add_tool(
        _global_search,
        name="lex_search_global",
        description=(
            "Run the cross-model full-text search. Returns a list of hit "
            "objects with `model`, `id`, `url` and a short `content` summary. "
            "Per-model and per-row read permissions are enforced."
        ),
    )


async def _global_search(query: str) -> Dict[str, Any]:
    principal = current_principal()

    from lex.api.views.global_search_for_models.Search import Search
    from lex.process_admin.settings import processAdminSite

    # Force lazy ModelCollection initialisation if it has not happened yet.
    if not processAdminSite.initialized:
        _ = processAdminSite.urls

    status_code, payload = await call_view(
        Search,
        principal=principal,
        method="GET",
        view_kwargs={"query": query},
        view_init_kwargs={"model_collection": processAdminSite.model_collection},
    )
    return _envelope(status_code, payload)
