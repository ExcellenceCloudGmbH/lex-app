import time

from django.db.models import Max
from django.http import JsonResponse
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView
from rest_framework_api_key.permissions import HasAPIKey
from lex.lex_app.logging.CalculationLog import CalculationLog
from lex_app.LexLogger.LexLogger import LexLogLevel, LexLogger

class InitCalculationLogs(APIView):
    http_method_names = ['get']
    permission_classes = [HasAPIKey | IsAuthenticated]

    def get(self, request, *args, **kwargs):
        try:
            calculation_record = request.query_params['calculation_record']
            calculation_id = request.query_params['calculation_id']
            if ("get_size" in request.query_params and request.query_params["get_size"]):
                queryset_calc = CalculationLog.objects.filter(
                    calculationId=calculation_id,
                    calculation_record=calculation_record,
                ).order_by("-timestamp").all()
                return JsonResponse({"size": len(queryset_calc)})



            offset = int(request.query_params['offset'])

            queryset_calc = CalculationLog.objects.filter(
                                                          calculationId=calculation_id,
                                                          ).order_by("-timestamp").all()
            logs = [log.to_dict() for log in queryset_calc]

            if offset < 0:
                pass
            elif offset < len(logs):
                logs = logs[offset:min(offset + 7, len(logs))]
            else:
                logs = []


            return JsonResponse({"logs": list(reversed(logs))})
        except Exception as e:
            print(e)
            return JsonResponse({"logs": ""})
