"""FastMCP server factory for lex-app."""
from __future__ import annotations

import logging
import threading
from typing import List, Optional

from django.conf import settings as django_settings
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from lex.mcp_server import prompts, resources, tools
from lex.mcp_server.config import mcp_setting

logger = logging.getLogger(__name__)

_INSTANCE: Optional[FastMCP] = None
_LOCK = threading.Lock()


def _resolve_allowed_hosts() -> List[str]:
    """Build the host whitelist for FastMCP's DNS-rebinding protection.

    Sources, in order:
      1. ``MCP_SERVER['ALLOWED_HOSTS']`` (if set).
      2. Django's ``ALLOWED_HOSTS``.
    Always includes localhost variants. Bare hostnames get a ``:*`` suffix so
    any port matches; ``"*"`` from Django becomes a wildcard match.
    """
    override = mcp_setting("ALLOWED_HOSTS")
    if override is False:
        return ["*"]
    if override:
        sources = list(override)
    else:
        sources = list(getattr(django_settings, "ALLOWED_HOSTS", []) or [])

    base = {"127.0.0.1:*", "localhost:*", "[::1]:*", "127.0.0.1", "localhost"}
    out: set[str] = set(base)
    for host in sources:
        h = (host or "").strip()
        if not h:
            continue
        if h == "*":
            return ["*"]
        h = h.lstrip(".")  # Django allows leading-dot wildcards; strip for MCP
        out.add(h)
        if ":" not in h and not h.endswith("*"):
            out.add(f"{h}:*")
    return sorted(out)


def _resolve_allowed_origins() -> List[str]:
    override = mcp_setting("ALLOWED_ORIGINS")
    if override is False:
        return ["*"]
    if override:
        return list(override)
    # Default: derive http(s) origins from allowed hosts (excluding port wildcards).
    origins: set[str] = {"http://127.0.0.1", "http://localhost", "http://[::1]"}
    for host in _resolve_allowed_hosts():
        if host == "*":
            return ["*"]
        if host.endswith(":*"):
            host = host[:-2]
        origins.add(f"http://{host}")
        origins.add(f"https://{host}")
    return sorted(origins)


def _build_server() -> FastMCP:
    try:
        from lex._version import __version__ as lex_version
    except Exception:
        lex_version = "unknown"

    enable_protection = bool(mcp_setting("DNS_REBINDING_PROTECTION"))
    transport_security = TransportSecuritySettings(
        enable_dns_rebinding_protection=enable_protection,
        allowed_hosts=_resolve_allowed_hosts() if enable_protection else ["*"],
        allowed_origins=_resolve_allowed_origins() if enable_protection else ["*"],
    )
    logger.info(
        "MCP transport security: enabled=%s hosts=%s origins=%s",
        enable_protection,
        transport_security.allowed_hosts,
        transport_security.allowed_origins,
    )

    server = FastMCP(
        name="lex-app",
        instructions=(
            "Lex business application MCP server. Exposes CRUD over registered "
            "model containers. Authenticate with either an 'API-KEY' header or "
            "an 'Authorization: Bearer <jwt>' Keycloak token. Per-request RBAC "
            "is enforced; failures surface as JSON-RPC tool errors with HTTP "
            "status codes embedded in the response envelope."
        ),
        # Stateless = each Streamable-HTTP request is fully self-contained;
        # no server-side session state to manage across requests, which makes
        # MCP horizontally scalable on top of the existing Django deployment.
        # TODO Phase 10: switch to ``stateless_http=False`` to support
        # ``notifications/progress`` for long-running calculations
        # (requires wiring through ``WebSocketNotifier`` / Channels).
        stateless_http=True,
        json_response=True,
        # Mounted at "/" because the surrounding ASGI dispatcher already
        # routes /mcp* to this app.
        streamable_http_path="/",
        transport_security=transport_security,
    )
    server.dependencies = (f"lex-app=={lex_version}",)
    tools.register(server)
    if mcp_setting("ENABLE_RESOURCES"):
        resources.register(server)
    if mcp_setting("ENABLE_PROMPTS"):
        prompts.register(server)
    return server


def get_server() -> FastMCP:
    global _INSTANCE
    if _INSTANCE is not None:
        return _INSTANCE
    with _LOCK:
        if _INSTANCE is None:
            _INSTANCE = _build_server()
            logger.info("FastMCP server initialised for lex-app")
    return _INSTANCE
