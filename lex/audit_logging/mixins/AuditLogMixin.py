import logging
import time
import traceback

from django.db import transaction
from django.utils import timezone
from lex.audit_logging.models.AuditLog import AuditLog
from lex.audit_logging.models.AuditLogStatus import AuditLogStatus
from lex.audit_logging.serializers.AuditLogMixinSerializer import _serialize_payload
from lex.audit_logging.utils.content_types import safe_get_content_type as _safe_get_content_type
from lex.core.exceptions import resolve_exception_traceback

logger = logging.getLogger(__name__)

RETRYABLE_SQLSTATE_CODES = {"40P01", "40001"}
RETRYABLE_DB_ERROR_MESSAGES = (
    "deadlock detected",
    "could not serialize access due to concurrent update",
    "could not serialize access due to read/write dependencies among transactions",
)
MAX_UPDATE_RETRIES = 3
BASE_RETRY_DELAY_SECONDS = 0.05


def _iter_exception_chain(exception):
    current = exception
    seen = set()
    while current and id(current) not in seen:
        seen.add(id(current))
        yield current
        current = getattr(current, "__cause__", None) or getattr(current, "__context__", None)


def _is_retryable_db_error(exception):
    for chained_exception in _iter_exception_chain(exception):
        pg_code = getattr(chained_exception, "pgcode", None)
        if pg_code in RETRYABLE_SQLSTATE_CODES:
            return True

        message = str(chained_exception).lower()
        if any(token in message for token in RETRYABLE_DB_ERROR_MESSAGES):
            return True

    return False


def _execute_with_retry(callable_obj, *, max_retries=MAX_UPDATE_RETRIES, operation_name="database operation"):
    for attempt in range(1, max_retries + 1):
        try:
            return callable_obj()
        except Exception as exc:
            if attempt >= max_retries or not _is_retryable_db_error(exc):
                raise

            delay_seconds = BASE_RETRY_DELAY_SECONDS * (2 ** (attempt - 1))
            logger.warning(
                "Retrying %s after transient DB error "
                "(attempt %s/%s): %s",
                operation_name,
                attempt,
                max_retries,
                exc,
            )
            time.sleep(delay_seconds)


def _save_with_retry(serializer, *, max_retries=MAX_UPDATE_RETRIES):
    return _execute_with_retry(
        serializer.save,
        max_retries=max_retries,
        operation_name="serializer save",
    )


def _delete_with_retry(instance, *, max_retries=MAX_UPDATE_RETRIES):
    return _execute_with_retry(
        instance.delete,
        max_retries=max_retries,
        operation_name="instance delete",
    )


def _resolve_audit_failure_traceback(exception):
    return resolve_exception_traceback(
        exception,
        fallback=traceback.format_exc(),
    )


class AuditLogMixin:

    @staticmethod
    def _attach_related_instance_id(payload, related_instance):
        related_instance_pk = getattr(related_instance, "pk", None)
        if related_instance_pk is None or not isinstance(payload, dict):
            return payload

        enriched_payload = dict(payload)
        enriched_payload.setdefault("id", related_instance_pk)
        return enriched_payload

    def reset_failed_audit_log_state(self):
        self._failed_audit_logged = False
        if hasattr(self, "_pending_failed_audit_log"):
            delattr(self, "_pending_failed_audit_log")
        if hasattr(self, "_pending_failed_audit_logs"):
            delattr(self, "_pending_failed_audit_logs")

    def _get_pending_failed_audit_logs(self):
        pending_failed_audit_logs = getattr(self, "_pending_failed_audit_logs", None)
        if pending_failed_audit_logs is not None:
            return pending_failed_audit_logs

        legacy_pending_failed_audit_log = getattr(self, "_pending_failed_audit_log", None)
        if legacy_pending_failed_audit_log is None:
            return []

        pending_failed_audit_logs = [legacy_pending_failed_audit_log]
        self._pending_failed_audit_logs = pending_failed_audit_logs
        delattr(self, "_pending_failed_audit_log")
        return pending_failed_audit_logs

    def has_logged_failure_audit(self):
        return bool(getattr(self, "_failed_audit_logged", False))

    def log_failed_change(
        self,
        action,
        target,
        payload=None,
        error_traceback=None,
        related_instance=None,
    ):
        payload = self._attach_related_instance_id(payload, related_instance)
        audit_log = self.log_change(
            action,
            target,
            payload=_serialize_payload(payload) or {},
        )

        if related_instance is not None and getattr(related_instance, "pk", None) is not None:
            audit_log.content_type = _safe_get_content_type(related_instance.__class__)
            audit_log.object_id = related_instance.pk
            audit_log.save()

        AuditLogStatus.objects.filter(audit_log=audit_log).update(
            status='failure',
            error_traceback=error_traceback,
            updated_at=timezone.now(),
        )
        self._failed_audit_logged = True
        return audit_log

    def queue_failed_audit_log(
        self,
        action,
        target,
        payload=None,
        error_traceback=None,
        related_instance=None,
    ):
        pending_failed_audit_logs = list(self._get_pending_failed_audit_logs())
        pending_failed_audit_logs.append({
            "action": action,
            "target": target,
            "payload": payload,
            "error_traceback": error_traceback,
            "related_instance": related_instance,
        })
        self._pending_failed_audit_logs = pending_failed_audit_logs
        self._failed_audit_logged = False

    def flush_pending_failed_audit_logs(self):
        pending_failed_audit_logs = list(self._get_pending_failed_audit_logs())
        if not pending_failed_audit_logs:
            return []

        if hasattr(self, "_pending_failed_audit_logs"):
            delattr(self, "_pending_failed_audit_logs")

        return [
            self.log_failed_change(**pending_failed_audit_log)
            for pending_failed_audit_log in pending_failed_audit_logs
        ]

    def flush_pending_failed_audit_log(self):
        flushed_failed_audit_logs = self.flush_pending_failed_audit_logs()
        return flushed_failed_audit_logs[0] if flushed_failed_audit_logs else None
    
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

        # Make the audit_log immediately available to ContextResolver so
        # that CalculationLog.log() can find it without a DB query — which
        # would fail inside an atomic() block where the row hasn't been
        # committed yet.
        try:
            from lex.api.utils import operation_context
            ctx = operation_context.get()
            if ctx and isinstance(ctx, dict):
                ctx['audit_log_temp'] = audit_log
                operation_context.set(ctx)
        except Exception:
            pass  # operation_context may not be active (e.g. management commands)

        return audit_log

    def perform_create(self, serializer):
        payload = _serialize_payload(serializer.validated_data) or {}
        audit_log = self.log_change("create", serializer.Meta.model, payload=payload)
        try:
            instance = _save_with_retry(serializer)
            payload['id'] = instance.pk
            audit_log.payload = payload
            audit_log.content_type = _safe_get_content_type(instance.__class__)
            audit_log.object_id = instance.pk
            audit_log.save()
            AuditLogStatus.objects.filter(audit_log=audit_log).update(status='success', updated_at=timezone.now())
            return instance
        except Exception as e:
            error_msg = _resolve_audit_failure_traceback(e)
            AuditLogStatus.objects.filter(audit_log=audit_log) \
                .update(status='failure', error_traceback=error_msg, updated_at=timezone.now())
            if transaction.get_connection().in_atomic_block:
                # The failure status above rolls back with the surrounding request
                # transaction, so the view persists a queued replacement afterward.
                self.queue_failed_audit_log(
                    "create",
                    serializer.Meta.model,
                    payload=payload,
                    error_traceback=error_msg,
                )
            else:
                # Non-atomic requests keep the failure row written above; only mark it
                # so request-level fallback logging does not duplicate the audit.
                self._failed_audit_logged = True
            raise

    def perform_update(self, serializer):
        related_instance = getattr(serializer, "instance", None)
        initial_payload = _serialize_payload(serializer.validated_data) or {}
        initial_payload = self._attach_related_instance_id(initial_payload, related_instance)
        audit_log = self.log_change("update", serializer.Meta.model, payload=initial_payload)
        try:
            instance = _save_with_retry(serializer)
            updated_payload = _serialize_payload(self.get_serializer(instance).data) or {}
            if isinstance(updated_payload, dict):
                updated_payload['id'] = instance.pk
            audit_log.content_type = _safe_get_content_type(instance.__class__)
            audit_log.object_id = instance.pk
            audit_log.payload = updated_payload
            audit_log.save()
            # When a background calculation is pending (HTTP 202 path),
            # leave the status as 'pending' — the terminal audit
            # (ensure_terminal_calculation_audit) will set it to
            # 'success' or 'failure' once the calculation completes.
            if not getattr(instance, "_defer_calculate_hook", False):
                AuditLogStatus.objects.filter(audit_log=audit_log).update(status='success', updated_at=timezone.now())
            return instance
        except Exception as e:
            error_msg = _resolve_audit_failure_traceback(e)
            AuditLogStatus.objects.filter(audit_log=audit_log) \
                .update(status='failure', error_traceback=error_msg, updated_at=timezone.now())
            if transaction.get_connection().in_atomic_block:
                self.queue_failed_audit_log(
                    "update",
                    serializer.Meta.model,
                    payload=initial_payload,
                    error_traceback=error_msg,
                    related_instance=related_instance,
                )
            else:
                self._failed_audit_logged = True
            raise

    def perform_destroy(self, instance):
        serializer = self.get_serializer(instance)
        payload = _serialize_payload(serializer.data) or {}
        instance_pk = instance.pk
        if isinstance(payload, dict):
            payload['id'] = instance_pk
        audit_log = self.log_change("delete", instance, payload=payload)
        try:
            _delete_with_retry(instance)
            audit_log.content_type = _safe_get_content_type(instance.__class__)
            audit_log.object_id = instance_pk
            audit_log.save()
            AuditLogStatus.objects.filter(audit_log=audit_log).update(status='success', updated_at=timezone.now())
        except Exception as e:
            error_msg = _resolve_audit_failure_traceback(e)
            AuditLogStatus.objects.filter(audit_log=audit_log) \
                .update(status='failure', error_traceback=error_msg, updated_at=timezone.now())
            if transaction.get_connection().in_atomic_block:
                self.queue_failed_audit_log(
                    "delete",
                    instance,
                    payload=payload,
                    error_traceback=error_msg,
                    related_instance=instance,
                )
            else:
                self._failed_audit_logged = True
            raise
