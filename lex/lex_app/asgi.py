"""
ASGI config for lex_app project.
"""

import os

# MUST be set before importing anything that may touch Django settings/apps
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "lex_app.settings")

import atexit

from django.core.asgi import get_asgi_application

# This initializes Django (calls django.setup() internally)
django_asgi_app = get_asgi_application()

# Only import Channels + your routing AFTER Django is ready
from concurrent.futures import ThreadPoolExecutor

from asgiref.sync import async_to_sync, SyncToAsync
from channels.auth import AuthMiddlewareStack
from channels.routing import ProtocolTypeRouter, URLRouter
from channels.security.websocket import AllowedHostsOriginValidator

# Increase the thread-sensitive executor from 1 → 3 workers so that
# long-running sync operations (e.g. calculations) don't starve other
# thread_sensitive=True calls like database_sync_to_async (WS auth).
SyncToAsync.single_thread_executor = ThreadPoolExecutor(
    max_workers=int(os.environ.get("ASGI_THREADS", "3"))
)

from lex.lex_app import routing
from lex.lex_app.fast_health import health_asgi_app, is_fast_health_path
from django.urls import re_path


async def http_application(scope, receive, send):
    request_path = scope.get("path", "")
    if scope.get("type") == "http" and is_fast_health_path(request_path):
        await health_asgi_app(scope, receive, send)
        return
    await django_asgi_app(scope, receive, send)


application = ProtocolTypeRouter(
    {
        "http": http_application,
        "websocket": AllowedHostsOriginValidator(
            URLRouter(
                routing.public_websocket_urlpatterns()
                + [
                    # All other WS routes require authentication
                    re_path(r"", AuthMiddlewareStack(
                        URLRouter(routing.authenticated_websocket_urlpatterns())
                    )),
                ]
            )
        ),
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