"""Verify Phase 6 tools, resources and prompts register on the FastMCP server."""
from __future__ import annotations

import asyncio

import django
import pytest
from django.conf import settings


@pytest.fixture(scope="module", autouse=True)
def _configure_django():
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
            },
        )
        django.setup()
    # Force a clean server build so toggles take effect for this test module.
    from lex.mcp_server import server as server_module

    server_module._INSTANCE = None
    yield
    server_module._INSTANCE = None


def test_phase6_tools_register():
    from lex.mcp_server.server import get_server

    server = get_server()
    tools = asyncio.run(server.list_tools())
    names = {t.name for t in tools}

    expected = {
        "lex.models.list",
        "lex.entries.list",
        "lex.entries.get",
        "lex.entries.create",
        "lex.entries.update",
        "lex.entries.delete",
        "lex.calculations.run",
        "lex.calculations.tail_logs",
        "lex.calculations.list_logs",
        "lex.calculations.clean",
        "lex.entries.history",
        "lex.search.global",
    }
    missing = expected - names
    assert not missing, f"missing tools: {missing}"


def test_phase6_resources_register():
    from lex.mcp_server.server import get_server

    server = get_server()
    resources = asyncio.run(server.list_resources())
    uris = {str(r.uri) for r in resources}

    assert "lex://project/info" in uris
    assert "lex://models/structure" in uris
    assert "lex://widgets/structure" in uris

    templates = asyncio.run(server.list_resource_templates())
    template_uris = {t.uriTemplate for t in templates}
    assert "lex://models/{container}/fields" in template_uris
    assert "lex://entries/{container}/{pk}" in template_uris


def test_phase6_prompts_register():
    from lex.mcp_server.server import get_server

    server = get_server()
    prompts = asyncio.run(server.list_prompts())
    names = {p.name for p in prompts}

    assert {
        "lex.investigate_calculation",
        "lex.summarize_model",
        "lex.audit_record",
    } <= names
