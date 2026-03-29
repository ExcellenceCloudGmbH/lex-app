import traceback
import logging
import time
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
            AuditLogStatus.objects.filter(audit_log=audit_log).update(status='success')
            return instance
        except Exception as e:
            error_msg = _resolve_audit_failure_traceback(e)
            AuditLogStatus.objects.filter(audit_log=audit_log) \
                .update(status='failure', error_traceback=error_msg)
            raise

    def perform_update(self, serializer):
        initial_payload = _serialize_payload(serializer.validated_data)
        audit_log = self.log_change("update", serializer.Meta.model, payload=initial_payload)
        try:
            instance = _save_with_retry(serializer)
            updated_payload = _serialize_payload(self.get_serializer(instance).data)
            audit_log.content_type = _safe_get_content_type(instance.__class__)
            audit_log.object_id = instance.pk
            audit_log.payload = updated_payload
            audit_log.save()
            AuditLogStatus.objects.filter(audit_log=audit_log).update(status='success')
            return instance
        except Exception as e:
            error_msg = _resolve_audit_failure_traceback(e)
            AuditLogStatus.objects.filter(audit_log=audit_log) \
                .update(status='failure', error_traceback=error_msg)
            raise

    def perform_destroy(self, instance):
        serializer = self.get_serializer(instance)
        payload = _serialize_payload(serializer.data)
        audit_log = self.log_change("delete", instance, payload=payload)
        try:
            _delete_with_retry(instance)
            audit_log.content_type = _safe_get_content_type(instance.__class__)
            audit_log.object_id = instance.pk
            audit_log.save()
            AuditLogStatus.objects.filter(audit_log=audit_log).update(status='success')
        except Exception as e:
            error_msg = _resolve_audit_failure_traceback(e)
            AuditLogStatus.objects.filter(audit_log=audit_log) \
                .update(status='failure', error_traceback=error_msg)
            raise
