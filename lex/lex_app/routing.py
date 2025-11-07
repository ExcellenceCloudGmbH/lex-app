from channels.auth import AuthMiddlewareStack
from channels.routing import ProtocolTypeRouter, URLRouter

# TODO: Websocket routing needs to be migrated to the new api app structure
# import lex.api.routing

# Temporary empty websocket patterns until routing is migrated
websocket_urlpatterns = []

application = ProtocolTypeRouter({
    # (http->django views is added by default)
    'websocket': AuthMiddlewareStack(
        URLRouter(
            websocket_urlpatterns
        )
    ),
})