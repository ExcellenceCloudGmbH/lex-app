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
from asgiref.sync import async_to_sync
from channels.auth import AuthMiddlewareStack
from channels.routing import ProtocolTypeRouter, URLRouter
from channels.security.websocket import AllowedHostsOriginValidator

from lex.lex_app import routing


application = ProtocolTypeRouter(
    {
        "http": django_asgi_app,
        "websocket": AllowedHostsOriginValidator(
            AuthMiddlewareStack(
                URLRouter(routing.websocket_urlpatterns())
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
