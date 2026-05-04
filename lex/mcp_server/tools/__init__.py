"""MCP tools that expose CRUD over registered model containers.

Tool registration is split per concern so each domain bundle can be
toggled via ``MCP_SERVER`` settings:

* :mod:`lex.mcp_server.tools.model_entries` — CRUD (always on).
* :mod:`lex.mcp_server.tools.calculations`  — run/tail-logs/list-logs/clean.
* :mod:`lex.mcp_server.tools.history`       — ``simple_history`` timeline.
* :mod:`lex.mcp_server.tools.search`        — global PostgreSQL search.
* :mod:`lex.mcp_server.tools.files`         — file/SharePoint/PDF download/export.
"""
from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from lex.mcp_server.config import mcp_setting
from lex.mcp_server.tools import calculations, files, history, model_entries, permissions, search


def register(server: FastMCP) -> None:
    """Top-level entry point invoked by :func:`lex.mcp_server.server._build_server`."""
    model_entries.register(server)
    if mcp_setting("ENABLE_CALCULATIONS"):
        calculations.register(server)
    if mcp_setting("ENABLE_HISTORY"):
        history.register(server)
    if mcp_setting("ENABLE_SEARCH"):
        search.register(server)
    if mcp_setting("ENABLE_FILES"):
        files.register(server)
    if mcp_setting("ENABLE_PERMISSIONS"):
        permissions.register(server)
