import traceback
from lex.audit_logging.models.audit_log import AuditLog
from lex.audit_logging.models.audit_log_status import AuditLogStatus
from lex.audit_logging.serializers.audit_log_mixin_serializer import _serialize_payload
from django.contrib.contenttypes.models import ContentType

class BulkAuditLogMixin:

    def log_change(self, action, target, payload=None):
        """
        Create an audit log record along with a pending status.

        The `target` parameter is used to determine the resource name (using the model class name for a class
        or the instance's class name for an instance). The payload is serialized for JSON compatibility.
        """
        payload = _serialize_payload(payload or {})
        user = self.request.user if hasattr(self.request, 'user') else None
        resource = target.__name__.lower() if isinstance(target, type) else target.__class__.__name__.lower()
        audit_log = AuditLog.objects.create(
            author=f"{str(user)}" if user else None,
            resource=resource,
            action=action,
            payload=payload,
            calculation_id=self.kwargs.get('calculationId') if hasattr(self, 'kwargs') else None,
        )
        AuditLogStatus.objects.create(audit_log=audit_log, status='pending')
        return audit_log

    def perform_bulk_update(self, serializer):
        """
        Perform a bulk update while logging the changes.

        The initial payload from `serializer.data` is logged for each updated instance. After the update,
        each audit log record is updated with the full, fresh payload and its status set to 'success'.
        If an exception occurs, the log status is updated with 'failure' and error details.
        """
        # Save the bulk update; this returns the updated instance(s)
        updated_instances = serializer.save()
        audit_logs = []
        # Log the initial update for each instance.
        for instance, data in zip(updated_instances, serializer.data):
            audit_log = self.log_change("update", instance, payload=data)
            audit_logs.append((audit_log, instance))
        try:
            # After saving, refresh the payload of each audit log entry with the full updated data.
            for audit_log, instance in audit_logs:
                updated_payload = _serialize_payload(self.get_serializer(instance).data)
                audit_log.content_type = ContentType.objects.get_for_model(instance)
                audit_log.object_id = instance.pk
                audit_log.payload = updated_payload
                audit_log.save()
                AuditLogStatus.objects.filter(audit_log=audit_log).update(status='success')
            return updated_instances
        except Exception as e:
            error_msg = traceback.format_exc()
            for audit_log, _ in audit_logs:
                AuditLogStatus.objects.filter(audit_log=audit_log) \
                    .update(status='failure', error_traceback=error_msg)
            raise e

    def perform_bulk_destroy(self, queryset):
        """
        Perform a bulk deletion while logging the deletion of each instance.

        Each instance's current serialized state is logged before deletion. After deletion,
        each audit log record has its status updated to 'success'. In case of an error, the log status is updated
        to 'failure' with the error traceback attached.
        """
        audit_logs = []
        # Log deletion for each instance before actually deleting it.
        for instance in queryset:
            serializer = self.get_serializer(instance)
            audit_log = self.log_change("delete", instance, payload=serializer.data)
            audit_logs.append(audit_log)
        try:
            deleted_ids = [instance.pk for instance in queryset]
            instances = [instance for instance in queryset]
            queryset.delete()
            # TODO: Test
            for audit_log, instance in zip(audit_logs, instances):
                updated_payload = _serialize_payload(self.get_serializer(instance).data)
                audit_log.content_type = ContentType.objects.get_for_model(instance.__class__)
                audit_log.object_id = instance.pk
                audit_log.payload = updated_payload
                audit_log.save()
                AuditLogStatus.objects.filter(audit_log=audit_log).update(status='success')
            return deleted_ids
        except Exception as e:
            error_msg = traceback.format_exc()
            for audit_log in audit_logs:
                AuditLogStatus.objects.filter(audit_log=audit_log) \
                    .update(status='failure', error_traceback=error_msg)
            raise e