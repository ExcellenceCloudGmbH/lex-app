from rest_framework import serializers

from lex.audit_logging.models.CalculationLog import CalculationLog
from lex.audit_logging.utils.content_types import safe_get_generic_related_object


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
        if getattr(obj, "content_type_id", None) and obj.object_id:
            target = safe_get_generic_related_object(obj)
            if target is not None:
                return str(target)
            # return target.is_calculated
        return None


CalculationLog.api_serializers = {
    "default": CalculationLogDefaultSerializer,
}