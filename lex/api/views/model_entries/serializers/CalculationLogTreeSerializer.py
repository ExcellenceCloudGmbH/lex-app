from lex.audit_logging.models.CalculationLog import CalculationLog
from rest_framework import serializers


class CalculationLogTreeSerializer(serializers.ModelSerializer):
    title = serializers.SerializerMethodField()
    isRoot = serializers.SerializerMethodField()  # Declare the custom field
    children = serializers.SerializerMethodField()

    class Meta:
        model = CalculationLog
        fields = ['id', 'title', 'isRoot', 'children']

    def get_title(self, obj):
        # Use trigger_name; fallback to message or default if needed
        return str(obj)

    def get_isRoot(self, obj):
        # A node is considered root if it has no parent. Check the FK id rather
        # than the related object so we don't lazy-load the parent row per node.
        return obj.parent_log_id is None

    def get_children(self, obj):
        # Prefer a prefetched parent->children map supplied by the view (one
        # query for the whole page) to avoid an N+1 query per node.
        children_map = self.context.get('children_map')
        if children_map is not None:
            return list(children_map.get(obj.id, []))
        # Fallback for callers that don't supply a prefetched map: retrieve the
        # immediate children for the same calculation (returning just their IDs).
        children_qs = CalculationLog.objects.filter(
            parent_log=obj,
            calculationId=obj.calculationId
        )
        return list(children_qs.values_list('id', flat=True))

    def to_representation(self, instance):
        rep = super().to_representation(instance)
        # If the node is not a root, you might want to remove "isRoot" from its output
        if not self.get_isRoot(instance):
            rep.pop('isRoot', None)
        return rep