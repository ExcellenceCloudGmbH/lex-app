from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
# import CalculationLog
from lex.audit_logging.models.calculation_log import CalculationLog
from lex.api.views.model_entries.serializers.CalculationLogTreeSerializer import CalculationLogTreeSerializer

class CalculationLogTreeView(APIView):
    def get(self, request, *args, **kwargs):
        # Optionally filter by a calculation_id query parameter.
        calculation_id = request.query_params.get('calculation_id')
        if calculation_id:
            logs = CalculationLog.objects.filter(calculationId=calculation_id)
        else:
            logs = CalculationLog.objects.all()

        # Serialize all the logs into a flat list.
        serializer = CalculationLogTreeSerializer(logs, many=True)
        return Response({'data': serializer.data})
