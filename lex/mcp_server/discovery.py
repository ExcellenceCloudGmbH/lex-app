"""Discovery endpoints for MCP clients.

* ``/.well-known/mcp`` — lightweight server-info JSON for clients that
  want to detect lex-app speaks MCP without negotiating Streamable HTTP.
* ``/.well-known/oauth-protected-resource`` — RFC 9728 metadata so that
  MCP clients implementing the 2025-06-18 auth spec can discover the
  Keycloak realm to authenticate against.
"""
from __future__ import annotations

import os

from django.http import JsonResponse
from django.views import View

from lex.mcp_server.config import mcp_setting


class McpServerInfoView(View):
    def get(self, request, *args, **kwargs):
        try:
            from lex._version import __version__ as lex_version
        except Exception:
            lex_version = "unknown"

        return JsonResponse(
            {
                "name": "lex-app",
                "version": lex_version,
                "transport": "streamable-http",
                "endpoint": mcp_setting("MOUNT_PATH"),
                "auth": ["api-key", "oauth2-bearer"],
                "auth_metadata": "/.well-known/oauth-protected-resource",
            }
        )


class OAuthProtectedResourceView(View):
    """RFC 9728 protected-resource metadata pointing at the Keycloak issuer."""

    def get(self, request, *args, **kwargs):
        keycloak_url = (os.getenv("KEYCLOAK_URL") or "").rstrip("/")
        realm = os.getenv("KEYCLOAK_REALM") or os.getenv("KEYCLOAK_REALM_NAME")
        issuer = (
            f"{keycloak_url}/realms/{realm}"
            if keycloak_url and realm
            else None
        )

        resource_url = request.build_absolute_uri(mcp_setting("MOUNT_PATH"))
        body = {
            "resource": resource_url,
            "bearer_methods_supported": ["header"],
        }
        if issuer:
            body["authorization_servers"] = [issuer]
        return JsonResponse(body)
