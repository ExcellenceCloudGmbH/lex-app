from django.db import transaction

from lex.audit_logging.mixins.AuditLogMixin import (
    AuditLogMixin,
    _resolve_audit_failure_traceback,
    _safe_get_content_type,
)
from lex.audit_logging.models.AuditLog import AuditLog
from lex.audit_logging.models.AuditLogStatus import AuditLogStatus
from lex.audit_logging.serializers.AuditLogMixinSerializer import _serialize_payload

class BulkAuditLogMixin(AuditLogMixin):

    @staticmethod
    def _normalize_bulk_payloads(targets, payloads=None):
        target_list = list(targets)
        if isinstance(payloads, list):
            serialized_payloads = _serialize_payload(payloads)
            if len(serialized_payloads) == len(target_list):
                return [
                    AuditLogMixin._attach_related_instance_id(payload, target)
                    for target, payload in zip(target_list, serialized_payloads)
                ]
            if len(serialized_payloads) == 1 and len(target_list) > 1:
                serialized_payload = serialized_payloads[0]
                return [
                    AuditLogMixin._attach_related_instance_id(serialized_payload, target)
                    for target in target_list
                ]

        serialized_payload = _serialize_payload(payloads) or {}
        return [
            AuditLogMixin._attach_related_instance_id(serialized_payload, target)
            for target in target_list
        ]

    def log_failed_bulk_changes(self, action, targets, payloads=None, error_traceback=None):
        target_list = list(targets)
        normalized_payloads = self._normalize_bulk_payloads(target_list, payloads)
        return [
            self.log_failed_change(
                action,
                target,
                payload=payload,
                error_traceback=error_traceback,
                related_instance=target,
            )
            for target, payload in zip(target_list, normalized_payloads)
        ]

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
        instances = list(serializer.instance)
        initial_payloads = self._normalize_bulk_payloads(
            instances,
            getattr(serializer, "validated_data", None),
        )
        audit_logs = []
        for instance, payload in zip(instances, initial_payloads):
            audit_log = self.log_change("update", instance, payload=payload)
            audit_logs.append((audit_log, instance, payload))
        try:
            updated_instances = list(serializer.save())
            # After saving, refresh the payload of each audit log entry with the full updated data.
            for (audit_log, _instance, _payload), updated_instance in zip(audit_logs, updated_instances):
                updated_payload = _serialize_payload(self.get_serializer(updated_instance).data)
                audit_log.content_type = _safe_get_content_type(updated_instance.__class__)
                audit_log.object_id = updated_instance.pk
                audit_log.payload = updated_payload
                audit_log.save()
                AuditLogStatus.objects.filter(audit_log=audit_log).update(status='success')
            return updated_instances
        except Exception as e:
            error_msg = _resolve_audit_failure_traceback(e)
            is_atomic_block = transaction.get_connection().in_atomic_block
            for audit_log, instance, payload in audit_logs:
                AuditLogStatus.objects.filter(audit_log=audit_log) \
                    .update(status='failure', error_traceback=error_msg)
                if is_atomic_block:
                    self.queue_failed_audit_log(
                        "update",
                        instance,
                        payload=payload,
                        error_traceback=error_msg,
                        related_instance=instance,
                    )

            if audit_logs and not is_atomic_block:
                self._failed_audit_logged = True
            raise

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
                audit_log.content_type = _safe_get_content_type(instance.__class__)
                audit_log.object_id = instance.pk
                audit_log.payload = updated_payload
                audit_log.save()
                AuditLogStatus.objects.filter(audit_log=audit_log).update(status='success')
            return deleted_ids
        except Exception as e:
            error_msg = _resolve_audit_failure_traceback(e)
            is_atomic_block = transaction.get_connection().in_atomic_block
            for audit_log, instance in zip(audit_logs, queryset):
                AuditLogStatus.objects.filter(audit_log=audit_log) \
                    .update(status='failure', error_traceback=error_msg)
                if is_atomic_block:
                    self.queue_failed_audit_log(
                        "delete",
                        instance,
                        payload=self.get_serializer(instance).data,
                        error_traceback=error_msg,
                        related_instance=instance,
                    )

            if audit_logs and not is_atomic_block:
                self._failed_audit_logged = True
            raise
