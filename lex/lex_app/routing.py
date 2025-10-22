from channels.auth import AuthMiddlewareStack
from channels.routing import ProtocolTypeRouter, URLRouter

import lex.lex_app.rest_api.routing

application = ProtocolTypeRouter({
    # (http->django views is added by default)
    'websocket': AuthMiddlewareStack(
        URLRouter(
            lex.lex_app.rest_api.routing.websocket_urlpatterns
        )
    ),
})