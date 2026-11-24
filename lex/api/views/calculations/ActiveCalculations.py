from django.http import JsonResponse
from rest_framework.permissions import BasePermission, IsAuthenticated
from rest_framework.views import APIView
from lex.api.security import calculation_record_readable, sees_every_calculation
from lex.api.utils.api_key_requests import HasAPIKey

from lex.api.utils.api_key_requests import is_instance_api_key_request
from lex.core.signals.ActiveCalculationStateStore import ActiveCalculationStateStore


class HasInstanceAPIKey(BasePermission):
    def has_permission(self, request, view):
        return is_instance_api_key_request(request)


class ActiveCalculations(APIView):
    """``api/active-calculations``: the calculations running now.

    The Instance Controller asks with the instance's own key before a release,
    to warn the operator about work a restart would interrupt; that key and
    superusers see every run. Anyone else sees the runs on records they may read
    (``LEX_SEC_INTERNAL``): an entry names its record, model and calculation id.
    """

    http_method_names = ["get"]
    permission_classes = [HasInstanceAPIKey | HasAPIKey | IsAuthenticated]

    def get(self, request, *args, **kwargs):
        active = ActiveCalculationStateStore.snapshot()
        if not sees_every_calculation(request):
            active = [
                entry
                for entry in active
                if calculation_record_readable(request, entry.get("record_id"))
            ]
        return JsonResponse({"active_calculations": active})
