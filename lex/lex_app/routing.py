from channels.auth import AuthMiddlewareStack
from channels.routing import ProtocolTypeRouter, URLRouter
from django.urls import path

from lex.api.consumers.BackendHealthConsumer import BackendHealthConsumer
from lex.api.consumers.CalculationLogConsumer import CalculationLogConsumer
from lex.api.consumers.CalculationsConsumer import CalculationsConsumer
from lex.api.consumers.LogConsumer import LogConsumer
from lex.api.consumers.UpdateCalculationStatusConsumer import UpdateCalculationStatusConsumer

websocket_urlpatterns = [
    path('ws/logs', LogConsumer.as_asgi(), name='logs'),
    path('ws/health', BackendHealthConsumer.as_asgi(), name='backend-health'),
    path('ws/calculations', CalculationsConsumer.as_asgi(), name='calculations'),
    path('ws/calculation_logs/<str:calculationId>', CalculationLogConsumer.as_asgi(), name='calculation-logs'),
    path('ws/calculation_status_update', UpdateCalculationStatusConsumer.as_asgi(), name='calculation-status-update'),
]

application = ProtocolTypeRouter({
    # (http->django views is added by default)
    'websocket': AuthMiddlewareStack(
        URLRouter(
            websocket_urlpatterns
        )
    ),
})