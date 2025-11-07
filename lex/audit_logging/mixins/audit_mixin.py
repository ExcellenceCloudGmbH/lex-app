import traceback
from lex.audit_logging.models.audit_log import AuditLog
from lex.audit_logging.models.audit_log_status import AuditLogStatus

from django.contrib.contenttypes.models import ContentType
from lex.audit_logging.serializers.audit_log_mixin_serializer import _serialize_payload

class AuditLogMixin:
    def log_change(self, action, target, payload=None):
        payload = payload or {}
        user = self.request.user if hasattr(self.request, 'user') else None
        resource = target.__name__.lower() if isinstance(target, type) else target.__class__.__name__.lower()

        audit_log = AuditLog.objects.create(
            author=f"{str(user)}" if user else None,
            resource=resource,
            action=action,
            payload=payload,
            calculation_id=self.kwargs.get('calculationId'),
        )
        AuditLogStatus.objects.create(audit_log=audit_log, status='pending')
        return audit_log

    def perform_create(self, serializer):
        payload = _serialize_payload(serializer.validated_data) or {}
        audit_log = self.log_change("create", serializer.Meta.model, payload=payload)
        try:
            instance = serializer.save()
            payload['id'] = instance.pk
            audit_log.payload = payload
            audit_log.content_type = ContentType.objects.get_for_model(instance.__class__)
            audit_log.object_id = instance.pk
            audit_log.save()
            AuditLogStatus.objects.filter(audit_log=audit_log).update(status='success')
            return instance
        except Exception as e:
            error_msg = traceback.format_exc()
            AuditLogStatus.objects.filter(audit_log=audit_log) \
                .update(status='failure', error_traceback=error_msg)
            raise e

    def perform_update(self, serializer):
        initial_payload = _serialize_payload(serializer.validated_data)
        audit_log = self.log_change("update", serializer.Meta.model, payload=initial_payload)
        try:
            instance = serializer.save()
            updated_payload = _serialize_payload(self.get_serializer(instance).data)
            audit_log.content_type = ContentType.objects.get_for_model(instance.__class__)
            audit_log.object_id = instance.pk
            audit_log.payload = updated_payload
            audit_log.save()
            AuditLogStatus.objects.filter(audit_log=audit_log).update(status='success')
            return instance
        except Exception as e:
            error_msg = traceback.format_exc()
            AuditLogStatus.objects.filter(audit_log=audit_log) \
                .update(status='failure', error_traceback=error_msg)
            raise e

    def perform_destroy(self, instance):
        serializer = self.get_serializer(instance)
        payload = _serialize_payload(serializer.data)
        audit_log = self.log_change("delete", instance, payload=payload)
        try:
            instance.delete()
            audit_log.content_type = ContentType.objects.get_for_model(instance.__class__)
            audit_log.object_id = instance.pk
            audit_log.save()
            AuditLogStatus.objects.filter(audit_log=audit_log).update(status='success')
        except Exception as e:
            error_msg = traceback.format_exc()
            AuditLogStatus.objects.filter(audit_log=audit_log) \
                .update(status='failure', error_traceback=error_msg)
            raise e