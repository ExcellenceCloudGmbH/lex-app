from rest_framework import serializers

from lex.audit_logging.models.audit_log import AuditLog


class AuditLogDefaultSerializer(serializers.ModelSerializer):
    calculation_record = serializers.SerializerMethodField()

    class Meta:
        model = AuditLog
        fields = [
            "id",
            'date',
            'author',
            'resource',
            'action',
            'payload',
            'calculation_id',
            'calculation_record',
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


AuditLog.api_serializers = {
    "default": AuditLogDefaultSerializer,
}