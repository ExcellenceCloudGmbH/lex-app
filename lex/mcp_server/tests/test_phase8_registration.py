"""Phase 8 permission tools/resources register when ENABLE_PERMISSIONS is on."""
from __future__ import annotations

import asyncio

import django
import pytest
from django.conf import settings


def _configure(enable_permissions: bool) -> None:
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
                "ENABLE_FILES": True,
                "ENABLE_PERMISSIONS": enable_permissions,
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
            "ENABLE_FILES": True,
            "ENABLE_PERMISSIONS": enable_permissions,
        }


def _fresh_server():
    from lex.mcp_server import server as server_module

    server_module._INSTANCE = None
    return server_module.get_server()


_PERMISSION_TOOLS = {"lex.permissions.user", "lex.permissions.model"}


def test_permission_tools_register_when_enabled():
    _configure(enable_permissions=True)
    server = _fresh_server()

    tools = asyncio.run(server.list_tools())
    names = {t.name for t in tools}
    assert _PERMISSION_TOOLS <= names

    resources = asyncio.run(server.list_resources())
    uris = {str(r.uri) for r in resources}
    assert "lex://permissions/user" in uris

    templates = asyncio.run(server.list_resource_templates())
    template_uris = {t.uriTemplate for t in templates}
    assert "lex://permissions/{container}" in template_uris

    prompts = asyncio.run(server.list_prompts())
    prompt_names = {p.name for p in prompts}
    assert "lex.check_permissions" in prompt_names


def test_permission_tools_omitted_when_disabled():
    _configure(enable_permissions=False)
    server = _fresh_server()

    tools = asyncio.run(server.list_tools())
    names = {t.name for t in tools}
    assert not (_PERMISSION_TOOLS & names)

    resources = asyncio.run(server.list_resources())
    uris = {str(r.uri) for r in resources}
    assert "lex://permissions/user" not in uris

    templates = asyncio.run(server.list_resource_templates())
    template_uris = {t.uriTemplate for t in templates}
    assert "lex://permissions/{container}" not in template_uris
