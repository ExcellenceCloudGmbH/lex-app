"""Phase 7 file tools register on the FastMCP server when ENABLE_FILES is on."""
from __future__ import annotations

import asyncio

import django
import pytest
from django.conf import settings


def _configure(enable_files: bool) -> None:
    if not settings.configured:
        settings.configure(
            DEBUG=True,
            INSTALLED_APPS=[],
            DATABASES={"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}},
            USE_TZ=True,
            MCP_SERVER={
                "ENABLE_CALCULATIONS": True,
                "ENABLE_HISTORY": True,
                "ENABLE_SEARCH": True,
                "ENABLE_RESOURCES": True,
                "ENABLE_PROMPTS": True,
                "ENABLE_FILES": enable_files,
            },
        )
        django.setup()
    else:
        settings.MCP_SERVER = {
            "ENABLE_CALCULATIONS": True,
            "ENABLE_HISTORY": True,
            "ENABLE_SEARCH": True,
            "ENABLE_RESOURCES": True,
            "ENABLE_PROMPTS": True,
            "ENABLE_FILES": enable_files,
        }


def _fresh_server():
    from lex.mcp_server import server as server_module

    server_module._INSTANCE = None
    return server_module.get_server()


_FILE_TOOLS = {
    "lex_files_download",
    "lex_files_export",
    "lex_sharepoint_download",
    "lex_sharepoint_preview_link",
    "lex_sharepoint_share_link",
    "lex_calculations_download_pdf",
}


def test_file_tools_register_when_enabled():
    _configure(enable_files=True)
    server = _fresh_server()
    tools = asyncio.run(server.list_tools())
    names = {t.name for t in tools}
    missing = _FILE_TOOLS - names
    assert not missing, f"missing file tools: {missing}"


def test_file_tools_omitted_when_disabled():
    _configure(enable_files=False)
    server = _fresh_server()
    tools = asyncio.run(server.list_tools())
    names = {t.name for t in tools}
    overlap = _FILE_TOOLS & names
    assert not overlap, f"unexpected file tools registered: {overlap}"
