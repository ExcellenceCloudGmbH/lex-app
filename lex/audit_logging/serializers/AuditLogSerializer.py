from rest_framework import serializers

from lex.audit_logging.models.AuditLog import AuditLog
from lex.audit_logging.utils.content_types import safe_get_content_type


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
    status = serializers.SerializerMethodField()
    error_traceback = serializers.SerializerMethodField()
    status_created_at = serializers.SerializerMethodField()
    status_updated_at = serializers.SerializerMethodField()
    duration = serializers.SerializerMethodField()
    lex_reserved_has_calculation_log = serializers.SerializerMethodField()

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
            'status',
            'error_traceback',
            'status_created_at',
            'status_updated_at',
            'duration',
            'lex_reserved_has_calculation_log',
        ]
        read_only_fields = [f.name for f in AuditLog._meta.fields]
        lex_field_type_overrides = {
            'status_created_at': 'date_time',
            'status_updated_at': 'date_time',
            'duration': 'duration',
        }

    def _get_latest_status_record(self, obj):
        """Return the latest AuditLogStatus record for this AuditLog, or None."""
        cache_attr = '_cached_latest_status'
        if hasattr(obj, cache_attr):
            return getattr(obj, cache_attr)

        if hasattr(obj, '_prefetched_objects_cache') and 'status_records' in obj._prefetched_objects_cache:
            records = list(obj._prefetched_objects_cache['status_records'])
            result = records[-1] if records else None
        else:
            result = obj.status_records.order_by('-created_at').first()

        obj._cached_latest_status = result
        return result

    def get_lex_reserved_has_calculation_log(self, obj):
        """True when at least one CalculationLog row exists for this audit entry."""
        if hasattr(obj, '_has_calculation_log'):
            return obj._has_calculation_log
        if not obj.calculation_id:
            return False
        from lex.audit_logging.models.CalculationLog import CalculationLog
        return CalculationLog.objects.filter(calculationId=obj.calculation_id).exists()

    def get_status(self, obj):
        record = self._get_latest_status_record(obj)
        return record.status if record else None

    def get_error_traceback(self, obj):
        record = self._get_latest_status_record(obj)
        return record.error_traceback if record else None

    def get_status_created_at(self, obj):
        record = self._get_latest_status_record(obj)
        return record.created_at.isoformat() if record and record.created_at else None

    def get_status_updated_at(self, obj):
        record = self._get_latest_status_record(obj)
        return record.updated_at.isoformat() if record and record.updated_at else None

    def get_duration(self, obj):
        record = self._get_latest_status_record(obj)
        return record.duration if record else None

    def get_calculation_record(self, obj):
        """
        Build the link/detail payload for AG Grid without dereferencing the
        generic relation for every row in a list response.
        """
        object_id = getattr(obj, "object_id", None)
        if object_id is None:
            return None

        using = getattr(getattr(obj, "_state", None), "db", None)
        state = getattr(obj, "_state", None)
        fields_cache = getattr(state, "fields_cache", {}) or {}
        content_type = fields_cache.get("content_type")

        if content_type is None and getattr(obj, "content_type_id", None):
            try:
                content_type = safe_get_content_type(content_type_id=obj.content_type_id, using=using)
            except Exception:
                content_type = None

        if content_type is None:
            return None

        payload = getattr(obj, "payload", None)
        if not isinstance(payload, dict):
            payload = {}

        display_name = (
            payload.get("short_description")
            or payload.get("name")
            or payload.get("display")
            or f"{content_type.model} #{object_id}"
        )

        details = {}
        if "is_calculated" in payload:
            details["is_calculated"] = payload.get("is_calculated")

        if "error_message" in payload:
            details["error_message"] = payload.get("error_message")

        return {
            "id": object_id,
            "app_label": content_type.app_label,
            "model": content_type.model,
            "display_name": display_name,
            "details": details,
        }


class AuditLogReferenceSerializer(AuditLogReadOnlySerializerMixin, serializers.ModelSerializer):
    class Meta:
        model = AuditLog
        fields = [
            "id",
            "date",
            "author",
            "resource",
            "action",
            "calculation_id",
        ]
        read_only_fields = [f.name for f in AuditLog._meta.fields]

AuditLog.api_serializers = {
    "default": AuditLogDefaultSerializer,
    "reference": AuditLogReferenceSerializer,
}
AuditLog._lex_skip_serializer_alias = True
AuditLog._lex_hidden_serializers = {"reference"}
