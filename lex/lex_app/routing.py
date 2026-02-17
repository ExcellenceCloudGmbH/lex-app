# lex/lex_app/routing.py
from django.urls import path


def websocket_urlpatterns():
    # Lazy imports: Django setup tamamlandıktan sonra yüklenir
    from lex.api.consumers.BackendHealthConsumer import BackendHealthConsumer
    from lex.api.consumers.CalculationLogConsumer import CalculationLogConsumer
    from lex.api.consumers.CalculationsConsumer import CalculationsConsumer
    from lex.api.consumers.LogConsumer import LogConsumer
    from lex.api.consumers.UpdateCalculationStatusConsumer import UpdateCalculationStatusConsumer

    return [
        path("ws/logs", LogConsumer.as_asgi(), name="logs"),
        path("ws/health", BackendHealthConsumer.as_asgi(), name="backend-health"),
        path("ws/calculations", CalculationsConsumer.as_asgi(), name="calculations"),
        path("ws/calculation_logs/<str:calculationId>", CalculationLogConsumer.as_asgi(), name="calculation-logs"),
        path("ws/calculation_status_update", UpdateCalculationStatusConsumer.as_asgi(), name="calculation-status-update"),
    ]
