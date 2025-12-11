import time

from django.db.models import Max
from django.http import JsonResponse
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView
from rest_framework_api_key.permissions import HasAPIKey
from lex.audit_logging.models.calculation_log import CalculationLog
from lex.audit_logging.handlers.lex_logger import LexLogLevel, LexLogger
from django.core.cache import caches

from lex.audit_logging.utils.cache_manager import CacheManager


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
