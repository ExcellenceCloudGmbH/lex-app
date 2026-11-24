from django.http import JsonResponse
from lex.audit_logging.utils.CacheManager import CacheManager
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView
from lex.api.security import calculation_record_readable, sees_every_calculation
from lex.api.utils.api_key_requests import HasAPIKey


class InitCalculationLogs(APIView):
    """``api/init-calculation-logs``: the lines a calculation has logged so far.

    The log panel shows them before the WebSocket stream takes over. They come
    from the log cache, under ``calculation_record`` (``<model>_<pk>``) and
    ``calculation_id``. A caller who may not read that record gets what a record
    with no log gets, ``{"logs": null}`` (``LEX_SEC_INTERNAL``, as for the
    calculation-log tree); a superuser reads every log.
    """

    http_method_names = ['get']
    permission_classes = [HasAPIKey | IsAuthenticated]

    def get(self, request, *args, **kwargs):
        calculation_id = request.query_params.get('calculation_id')
        calculation_record = request.query_params.get('calculation_record')
        if not calculation_id or not calculation_record:
            return JsonResponse({"logs": ""})

        if not sees_every_calculation(request) and not calculation_record_readable(
            request, calculation_record
        ):
            return JsonResponse({"logs": None})

        cache_key = CacheManager.build_cache_key(calculation_record, calculation_id)
        return JsonResponse({"logs": CacheManager.get_message(cache_key)})
