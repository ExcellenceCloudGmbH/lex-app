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
        Returns a structured object for AG Grid.
        This allows the frontend to:
        1. Render a clickable link (using id/model).
        2. Populate a 'Master/Detail' expandable row with the 'details' dict.
        """
        target = obj.calculatable_object

        if target and obj.content_type:
            return {
                # Metadata for Navigation/Routing
                "id": obj.object_id,
                "app_label": obj.content_type.app_label,
                "model": obj.content_type.model,

                # Display text for the Cell Renderer
                "display_name": str(target),

                # Data for the "Collapsed" / Detail view in AG Grid
                # You can customize what goes here based on the target model
                "details": {
                    "is_calculated": getattr(target, 'is_calculated', None),
                    # You could add other fields dynamically here:
                    # "status": getattr(target, 'status', 'N/A'),
                }
            }
        return None


AuditLog.api_serializers = {
    "default": AuditLogDefaultSerializer,
}