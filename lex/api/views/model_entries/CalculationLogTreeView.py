from collections import defaultdict

from lex.api.views.model_entries.serializers.CalculationLogTreeSerializer import CalculationLogTreeSerializer
# import CalculationLog
from lex.audit_logging.models.CalculationLog import CalculationLog
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from lex.api.utils.api_key_requests import HasAPIKey

from lex.core.security_flags import superuser_only

#: How many logs of one calculation are inspected to find a record the caller may read.
_VISIBILITY_PROBE = 50


def log_target_readable(request, log) -> bool:
    """Whether the record a calculation log belongs to is one the caller may read."""
    from lex.api.security import is_readable

    if log.content_type_id is None or log.object_id is None:
        return False
    model = log.content_type.model_class()
    if model is None:
        return False
    instance = model._default_manager.filter(pk=log.object_id).first()
    return instance is not None and is_readable(request, instance)


def calculation_visible(request, calculation_id) -> bool:
    """A calculation's logs are visible when one of them belongs to a record the caller may read."""
    if not calculation_id:
        return False
    logs = (
        CalculationLog.objects.filter(calculationId=calculation_id)
        .exclude(content_type=None)
        .select_related("content_type")
    )
    return any(log_target_readable(request, log) for log in logs[:_VISIBILITY_PROBE])


class CalculationLogTreeView(APIView):
    permission_classes = [HasAPIKey | IsAuthenticated]

    # Bound how many log rows a single request can materialize and serialize.
    # Without this the view loaded the entire CalculationLog table (or every row
    # for a calculation) into memory at once, which contributed to backend OOMs.
    DEFAULT_LIMIT = 1000
    MAX_LIMIT = 5000

    def get(self, request, *args, **kwargs):
        # Optionally filter by a calculation_id query parameter.
        calculation_id = request.query_params.get('calculation_id')
        # Only a superuser sees every log; anyone else the logs of a calculation
        # on a record they may read (LEX_SEC_INTERNAL). Logs that belong to no
        # record are operator data.
        if superuser_only(request) and not calculation_visible(request, calculation_id):
            logs = CalculationLog.objects.none()
        elif calculation_id:
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
