from django.http import JsonResponse
from lex.audit_logging.utils.CacheManager import CacheManager
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView
from rest_framework_api_key.permissions import HasAPIKey


class InitCalculationLogs(APIView):
    http_method_names = ['get']
    permission_classes = [HasAPIKey | IsAuthenticated]

    def get(self, request, *args, **kwargs):
        try:

            calculation_id = request.query_params['calculation_id']
            calculation_record = request.query_params['calculation_record']
            cache_key = CacheManager.build_cache_key(calculation_record, calculation_id)

            cache_value = CacheManager.get_message(cache_key)

            return JsonResponse({"logs": cache_value})
        except Exception as e:
            print(e)
            return JsonResponse({"logs": ""})
