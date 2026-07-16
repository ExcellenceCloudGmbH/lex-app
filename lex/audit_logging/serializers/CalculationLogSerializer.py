from lex.audit_logging.models.CalculationLog import CalculationLog
from lex.audit_logging.utils.content_types import safe_get_generic_related_object
from rest_framework import serializers


class CalculationLogDefaultSerializer(serializers.ModelSerializer):
    calculation_record = serializers.SerializerMethodField()

    # Same timezone-aware datetime rendering as LexSerializer, so log
    # timestamps stay truthful when an instance runs a non-UTC naive
    # convention (LEX_TIME_ZONE).
    from django.db import models as _models

    from lex.api.serializers.base_serializers import LexAwareDateTimeField as _LexAwareDT

    serializer_field_mapping = {
        **serializers.ModelSerializer.serializer_field_mapping,
        _models.DateTimeField: _LexAwareDT,
    }

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
CalculationLog._lex_skip_serializer_alias = True