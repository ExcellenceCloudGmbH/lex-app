"""
ASGI config for lex_app project.
"""

import logging
import os

# MUST be set before importing anything that may touch Django settings/apps
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "lex_app.settings")

import atexit

from django.core.asgi import get_asgi_application

# This initializes Django (calls django.setup() internally)
django_asgi_app = get_asgi_application()

# Only import Channels + your routing AFTER Django is ready
from asgiref.sync import async_to_sync
from channels.auth import AuthMiddlewareStack
from channels.routing import ProtocolTypeRouter, URLRouter
from channels.security.websocket import AllowedHostsOriginValidator

from lex.lex_app import routing
from lex.lex_app.fast_health import health_asgi_app, is_fast_health_path
from lex.mcp_server.asgi import (
    is_mcp_path,
    is_well_known_mcp_oauth_path,
    mcp_asgi_app,
)

logger = logging.getLogger(__name__)


async def http_application(scope, receive, send):
    path = scope.get("path", "")
    if scope.get("type") == "http" and is_fast_health_path(path):
        await health_asgi_app(scope, receive, send)
        return
    if scope.get("type") == "http" and is_mcp_path(path):
        await mcp_asgi_app()(scope, receive, send)
        return
    if scope.get("type") == "http" and is_well_known_mcp_oauth_path(path):
        from lex.mcp_server.asgi import _well_known_oauth

        await _well_known_oauth(scope, receive, send)
        return
    await django_asgi_app(scope, receive, send)


_ws_router = AllowedHostsOriginValidator(
    AuthMiddlewareStack(
        URLRouter(routing.websocket_urlpatterns())
    )
)


async def websocket_application(scope, receive, send):
    """Wrap the WS router so unmatched paths close cleanly instead of raising."""
    try:
        await _ws_router(scope, receive, send)
    except ValueError:
        # No route matched — reject the WebSocket handshake.
        path = scope.get("path", "")
        logger.debug("WebSocket rejected: no route for path %r", path)
        await send({"type": "websocket.close", "code": 4004})


application = ProtocolTypeRouter(
    {
        "http": http_application,
        "websocket": websocket_application,
    }
)


def on_server_shutdown(*args, **kwargs):
    # Import consumers lazily (and only after Django is initialized)
    from lex.api.consumers.BackendHealthConsumer import BackendHealthConsumer
    from lex.api.consumers.CalculationLogConsumer import CalculationLogConsumer
    from lex.api.consumers.CalculationsConsumer import CalculationsConsumer
    from lex.api.consumers.UpdateCalculationStatusConsumer import UpdateCalculationStatusConsumer

    # async_to_sync avoids "no current event loop" issues in Python 3.12 at exit
    async_to_sync(BackendHealthConsumer.disconnect_all)()
    async_to_sync(CalculationLogConsumer.disconnect_all)()
    async_to_sync(CalculationsConsumer.disconnect_all)()
    async_to_sync(UpdateCalculationStatusConsumer.disconnect_all)()


atexit.register(on_server_shutdown)