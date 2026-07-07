from django.http import JsonResponse
from rest_framework.permissions import BasePermission, IsAuthenticated
from rest_framework.views import APIView
from rest_framework_api_key.permissions import HasAPIKey

from lex.api.utils.api_key_requests import is_instance_api_key_request
from lex.core.signals.ActiveCalculationStateStore import ActiveCalculationStateStore


class HasInstanceAPIKey(BasePermission):
    def has_permission(self, request, view):
        return is_instance_api_key_request(request)


class ActiveCalculations(APIView):
    http_method_names = ["get"]
    permission_classes = [HasInstanceAPIKey | HasAPIKey | IsAuthenticated]

    def get(self, request, *args, **kwargs):
        return JsonResponse(
            {"active_calculations": ActiveCalculationStateStore.snapshot()}
        )
