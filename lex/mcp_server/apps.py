from django.apps import AppConfig


class McpServerConfig(AppConfig):
    """Django app config for the embedded MCP server.

    The MCP ``FastMCP`` instance and its tool registry are constructed
    lazily on first request rather than in ``ready()`` so that this app
    has no import-time side effects on the rest of the project. See
    ``lex.mcp_server.server.get_server`` for the lazy entry point.
    """

    name = "lex.mcp_server"
    label = "lex_mcp_server"
    verbose_name = "Lex MCP Server"
    default_auto_field = "django.db.models.BigAutoField"
