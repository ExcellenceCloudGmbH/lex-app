from django.http import JsonResponse
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView
from rest_framework_api_key.permissions import HasAPIKey

from lex.core.signals.ActiveCalculationStateStore import ActiveCalculationStateStore


class ActiveCalculations(APIView):
    http_method_names = ["get"]
    permission_classes = [HasAPIKey | IsAuthenticated]

    def get(self, request, *args, **kwargs):
        return JsonResponse(
            {"active_calculations": ActiveCalculationStateStore.snapshot()}
        )
