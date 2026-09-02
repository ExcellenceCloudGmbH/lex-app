"""Stub install/uninstall helpers for ``lex.mcp_server``.

``lex/mcp_server/tools/embed.py`` imports three sibling modules —
``lex.mcp_server.config``, ``lex.mcp_server.registry`` and
``lex.mcp_server.context`` — that do not exist in this checkout: per
AGENTS.md, most of ``lex/mcp_server`` lives on an unmerged branch and only
``tools/embed.py`` has landed here so far. Importing the real, unmodified
``embed.py`` module therefore requires those three modules to exist in
``sys.modules`` *before* the import happens.

This module installs minimal, behaviourally-honest fakes for exactly those
three call surfaces (a settings lookup, a container registry lookup, and a
principal/auth accessor) so the real ``embed.py`` source runs unmodified.
Nothing about ``embed.py``'s own logic is mocked — only its as-yet-unmerged
neighbours.
"""
from __future__ import annotations

import os
import sys
import types
from contextlib import contextmanager
from typing import Any, Callable, Dict, Optional

_MCP_SERVER_DIR = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "mcp_server"
)
_MCP_SERVER_DIR = os.path.normpath(_MCP_SERVER_DIR)


class FakePrincipal:
    def __init__(self, access_token: Optional[str] = None):
        self.access_token = access_token


@contextmanager
def install_mcp_server_stubs(
    *,
    settings: Optional[Dict[str, Any]] = None,
    containers: Optional[Dict[str, Any]] = None,
    writable: Optional[Dict[str, bool]] = None,
    container_lookup_error: Optional[BaseException] = None,
    current_principal: Optional[Callable[[], Any]] = None,
):
    """Install fake ``lex.mcp_server.{config,registry,context}`` modules.

    Any module under ``lex.mcp_server`` (including ``tools.embed`` itself)
    that was already imported is evicted from ``sys.modules`` first, so the
    caller gets a fresh import that binds to the fakes. Everything is
    restored on exit.
    """
    settings = dict(settings or {})
    containers = dict(containers or {})
    writable = dict(writable or {})

    def _mcp_setting(name, default=None):
        return settings.get(name, default)

    def _get_container(resource):
        if container_lookup_error is not None:
            raise container_lookup_error
        return containers.get(resource)

    def _container_is_writable(resource):
        return writable.get(resource, False)

    def _current_principal():
        if current_principal is not None:
            return current_principal()
        raise RuntimeError("no principal bound")

    saved = {
        key: sys.modules.get(key)
        for key in (
            "lex.mcp_server",
            "lex.mcp_server.config",
            "lex.mcp_server.registry",
            "lex.mcp_server.context",
            "lex.mcp_server.tools",
            "lex.mcp_server.tools.embed",
        )
    }

    pkg = types.ModuleType("lex.mcp_server")
    pkg.__path__ = [_MCP_SERVER_DIR]
    sys.modules["lex.mcp_server"] = pkg

    tools_pkg = types.ModuleType("lex.mcp_server.tools")
    tools_pkg.__path__ = [os.path.join(_MCP_SERVER_DIR, "tools")]
    sys.modules["lex.mcp_server.tools"] = tools_pkg

    config_mod = types.ModuleType("lex.mcp_server.config")
    config_mod.mcp_setting = _mcp_setting
    sys.modules["lex.mcp_server.config"] = config_mod

    registry_mod = types.ModuleType("lex.mcp_server.registry")
    registry_mod.get_container = _get_container
    registry_mod.container_is_writable = _container_is_writable
    sys.modules["lex.mcp_server.registry"] = registry_mod

    context_mod = types.ModuleType("lex.mcp_server.context")
    context_mod.current_principal = _current_principal
    sys.modules["lex.mcp_server.context"] = context_mod

    # Ensure a fresh embed.py import binds against the fakes above rather
    # than a module cached from a previous test.
    sys.modules.pop("lex.mcp_server.tools.embed", None)

    try:
        yield
    finally:
        sys.modules.pop("lex.mcp_server.tools.embed", None)
        for key, value in saved.items():
            if value is None:
                sys.modules.pop(key, None)
            else:
                sys.modules[key] = value
