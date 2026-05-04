"""Embedded MCP (Model Context Protocol) server for lex-app.

Mounts a Streamable-HTTP MCP endpoint into the existing Django ASGI
application. Authentication accepts both ``API-KEY`` headers (via
``rest_framework_api_key``) and Keycloak OIDC bearer tokens. Tool
implementations delegate to the existing DRF view classes so RBAC,
audit logging and ``simple_history`` behave identically to the regular
HTTP API.
"""

default_app_config = "lex.mcp_server.apps.McpServerConfig"
