from rest_framework import serializers
from lex.legacy_data.models import LegacyCalculationLog, LegacyUserChangeLog, LegacyCalculationId

class LegacyReadOnlySerializerMixin:
    """
    Mixin to enforce read-only scopes for legacy models.
    Overrides get_lex_reserved_scopes to return empty/disabled permissions.
    """
    lex_reserved_scopes = serializers.SerializerMethodField()
    
    def get_lex_reserved_scopes(self, instance):
        return {
            "edit": [],       # No fields are editable
            "delete": False,  # Deletion is disabled
            "export": True,   # Export is allowed
        }

class LegacyCalculationLogSerializer(LegacyReadOnlySerializerMixin, serializers.ModelSerializer):
    class Meta:
        model = LegacyCalculationLog
        fields = '__all__'
        read_only_fields = [f.name for f in LegacyCalculationLog._meta.fields]

class LegacyUserChangeLogSerializer(LegacyReadOnlySerializerMixin, serializers.ModelSerializer):
    class Meta:
        model = LegacyUserChangeLog
        fields = '__all__'
        read_only_fields = [f.name for f in LegacyUserChangeLog._meta.fields]

class LegacyCalculationIdSerializer(LegacyReadOnlySerializerMixin, serializers.ModelSerializer):
    class Meta:
        model = LegacyCalculationId
        fields = '__all__'
        read_only_fields = [f.name for f in LegacyCalculationId._meta.fields]
