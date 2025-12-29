from rest_framework import serializers
from lex.audit_logging.models.calculation_log import CalculationLog

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
        # A node is considered root if it has no parent_calculation_log
        return obj.parent_log is None

    def get_children(self, obj):
        # Retrieve the immediate children for the same calculation (returning just their IDs)
        children_qs = CalculationLog.objects.filter(
            calculation_log=obj,
            calculationId=obj.calculationId
        )
        return list(children_qs.values_list('id', flat=True))

    def to_representation(self, instance):
        rep = super().to_representation(instance)
        # If the node is not a root, you might want to remove "isRoot" from its output
        if not self.get_isRoot(instance):
            rep.pop('isRoot', None)
        return rep