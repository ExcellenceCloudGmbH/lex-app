from collections import defaultdict

from lex.api.views.model_entries.serializers.CalculationLogTreeSerializer import CalculationLogTreeSerializer
# import CalculationLog
from lex.audit_logging.models.CalculationLog import CalculationLog
from rest_framework.response import Response
from rest_framework.views import APIView


class CalculationLogTreeView(APIView):
    # Bound how many log rows a single request can materialize and serialize.
    # Without this the view loaded the entire CalculationLog table (or every row
    # for a calculation) into memory at once, which contributed to backend OOMs.
    DEFAULT_LIMIT = 1000
    MAX_LIMIT = 5000

    def get(self, request, *args, **kwargs):
        # Optionally filter by a calculation_id query parameter.
        calculation_id = request.query_params.get('calculation_id')
        if calculation_id:
            logs = CalculationLog.objects.filter(calculationId=calculation_id)
        else:
            logs = CalculationLog.objects.all()
        logs = logs.order_by('id')

        total = logs.count()
        limit, offset = self._parse_pagination(request)
        page = list(logs[offset:offset + limit])

        # Resolve each row's child IDs in a single query for the whole page,
        # instead of one query per row inside the serializer (N+1).
        children_map = self._build_children_map(page, calculation_id)

        # Serialize the page into a flat list.
        serializer = CalculationLogTreeSerializer(
            page, many=True, context={'children_map': children_map}
        )
        return Response({
            'data': serializer.data,
            'pagination': {
                'limit': limit,
                'offset': offset,
                'total': total,
                'has_more': offset + len(page) < total,
            },
        })

    def _parse_pagination(self, request):
        try:
            limit = int(request.query_params.get('limit', self.DEFAULT_LIMIT))
        except (TypeError, ValueError):
            limit = self.DEFAULT_LIMIT
        try:
            offset = int(request.query_params.get('offset', 0))
        except (TypeError, ValueError):
            offset = 0
        limit = max(1, min(limit, self.MAX_LIMIT))
        offset = max(0, offset)
        return limit, offset

    @staticmethod
    def _build_children_map(page, calculation_id):
        children_map = defaultdict(list)
        parent_ids = [log.id for log in page]
        if not parent_ids:
            return children_map
        children = CalculationLog.objects.filter(parent_log_id__in=parent_ids)
        if calculation_id:
            children = children.filter(calculationId=calculation_id)
        for parent_id, child_id in (
            children.order_by('id').values_list('parent_log_id', 'id')
        ):
            children_map[parent_id].append(child_id)
        return children_map
