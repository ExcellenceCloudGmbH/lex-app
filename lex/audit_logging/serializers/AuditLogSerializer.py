from rest_framework import serializers

from lex.audit_logging.models.AuditLog import AuditLog


class AuditLogReadOnlySerializerMixin:
    """
    Mixin to enforce read-only scopes for legacy models.
    Overrides get_lex_reserved_scopes to return empty/disabled permissions.
    """
    lex_reserved_scopes = serializers.SerializerMethodField()

    def get_lex_reserved_scopes(self, instance):
        return {
            "edit": [],  # No fields are editable
            "delete": False,  # Deletion is disabled
            "export": True,  # Export is allowed
        }


class AuditLogDefaultSerializer(AuditLogReadOnlySerializerMixin, serializers.ModelSerializer):
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
        read_only_fields = [f.name for f in AuditLog._meta.fields]

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