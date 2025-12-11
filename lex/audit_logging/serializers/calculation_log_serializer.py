from rest_framework import serializers

from lex.audit_logging.models.calculation_log import CalculationLog


class CalculationLogDefaultSerializer(serializers.ModelSerializer):
    calculation_record = serializers.SerializerMethodField()

    class Meta:
        model = CalculationLog
        fields = [
            "id",
            "calculationId",
            "calculation_log",
            "timestamp",
            "calculation_record",  # renamed field now appears in the output
            "audit_log",
            "parent_log",
        ]

    def get_calculation_record(self, obj):
        """
        Return a JSON-serializable representation (for example, a flag) derived from the generically related object.
        In this case, we're using a property named 'is_calculated' from the linked object.
        """
        if obj.content_type and obj.object_id:
            return str(obj.calculatable_object)
            # return obj.calculatable_object.is_calculated
        return None


CalculationLog.api_serializers = {
    "default": CalculationLogDefaultSerializer,
}