import logging
from typing import Any, Optional

from django.db import transaction
from django.utils import timezone
from lex.api.utils import operation_context
from lex.audit_logging.models.AuditLog import AuditLog
from lex.audit_logging.models.AuditLogStatus import AuditLogStatus
from lex.audit_logging.serializers.AuditLogMixinSerializer import generic_instance_payload
from lex.audit_logging.utils.content_types import safe_get_content_type
from lex.core.signals.ActiveCalculationStateStore import ActiveCalculationStateStore

logger = logging.getLogger(__name__)


def _same_model_instance(left: Any, right: Any) -> bool:
    if left is None or right is None:
        return False
    left_meta = getattr(left, "_meta", None)
    right_meta = getattr(right, "_meta", None)
    if left_meta is None or right_meta is None:
        return False
    return (
        left_meta.label_lower == right_meta.label_lower
        and getattr(left, "pk", None) == getattr(right, "pk", None)
    )


def _resolve_model_context(model_context=None):
    if model_context is not None:
        return model_context

    try:
        from lex.audit_logging.utils.ModelContext import _model_context

        return _model_context.get().get("model_context")
    except Exception:
        return None


def _is_root_calculation(instance, *, model_context=None) -> bool:
    resolved_model_context = _resolve_model_context(model_context)
    if resolved_model_context is None:
        return True

    root_model = getattr(resolved_model_context, "get_root", lambda: None)()
    current_model = getattr(resolved_model_context, "current", None)

    if root_model is None:
        return True

    if current_model is None:
        return _same_model_instance(root_model, instance)

    return _same_model_instance(root_model, instance) and _same_model_instance(
        current_model,
        instance,
    )


def _resolve_context_data(context_data=None):
    if isinstance(context_data, dict):
        return context_data

    try:
        resolved = operation_context.get()
        if isinstance(resolved, dict):
            return resolved
    except Exception:
        pass

    return {}


def _resolve_calculation_id(instance, *, context_data=None) -> Optional[str]:
    resolved_context = _resolve_context_data(context_data)
    calculation_id = resolved_context.get("calculation_id")
    if isinstance(calculation_id, str) and calculation_id:
        return calculation_id

    record_id = f"{instance._meta.model_name}_{instance.pk}"
    tracked_calculation_id = ActiveCalculationStateStore.get_calculation_id(record_id)
    if tracked_calculation_id:
        return tracked_calculation_id

    instance_calculation_id = getattr(instance, "calculation_id", None)
    if isinstance(instance_calculation_id, str) and instance_calculation_id:
        return instance_calculation_id

    return None


def _resolve_actor(context_data=None) -> str:
    resolved_context = _resolve_context_data(context_data)

    request_obj = resolved_context.get("request_obj")
    if isinstance(request_obj, dict):
        request_user = request_obj.get("user")
        if request_user:
            return str(request_user)

    request_user = getattr(request_obj, "user", None)
    if request_user:
        return str(request_user)

    return "system"


def _resolve_audit_log_template(context_data=None):
    resolved_context = _resolve_context_data(context_data)
    template = resolved_context.get("audit_log_temp")
    if template is not None and isinstance(template, AuditLog):
        return template
    return None


def _load_persisted_instance(instance):
    try:
        persisted = instance.__class__._default_manager.filter(pk=instance.pk).first()
        persisted_meta = getattr(persisted, "_meta", None)
        if (
            persisted is not None
            and persisted_meta is not None
            and getattr(persisted_meta, "label_lower", None) == instance._meta.label_lower
        ):
            return persisted
    except Exception:
        logger.debug(
            "Falling back to in-memory instance for terminal audit payload.",
            exc_info=True,
        )
    return instance


def _build_payload(instance, *, error_message=None, stack_trace=None):
    payload_instance = _load_persisted_instance(instance)
    payload = generic_instance_payload(payload_instance)
    payload["is_calculated"] = getattr(payload_instance, "is_calculated", None)

    persisted_error_message = getattr(payload_instance, "error_message", None) or getattr(
        payload_instance,
        "calculation_error_message",
        None,
    )
    if persisted_error_message:
        payload["error_message"] = persisted_error_message
    elif error_message:
        payload["error_message"] = error_message

    if stack_trace:
        payload["error_traceback"] = stack_trace

    return payload


def _update_operation_context_audit_log(audit_log):
    try:
        context_data = operation_context.get()
        if not isinstance(context_data, dict):
            return
        updated_context = dict(context_data)
        updated_context["audit_log_temp"] = audit_log
        operation_context.set(updated_context)
    except Exception:
        logger.debug("Failed to refresh operation_context audit_log_temp", exc_info=True)


def ensure_terminal_calculation_audit(
    instance,
    *,
    audit_status: str,
    error_message: Optional[str] = None,
    stack_trace: Optional[str] = None,
    context_data=None,
    model_context=None,
):
    """
    Ensure the root calculation has an AuditLog + AuditLogStatus terminal record.

    Atomic calculation failures roll back any audit row created inside the failed
    transaction. The calculation status is later repaired by dedicated completion
    hooks, so audit logging needs the same recovery path.
    """
    if getattr(instance, "pk", None) is None:
        return None

    if not _is_root_calculation(instance, model_context=model_context):
        return None

    calculation_id = _resolve_calculation_id(instance, context_data=context_data)
    audit_log_template = _resolve_audit_log_template(context_data)

    if not calculation_id and audit_log_template is None:
        return None

    resource = instance.__class__.__name__.lower()
    action = getattr(audit_log_template, "action", None) or "update"
    author = getattr(audit_log_template, "author", None) or _resolve_actor(context_data)
    payload = _build_payload(
        instance,
        error_message=error_message,
        stack_trace=stack_trace,
    )

    audit_log = None
    template_pk = getattr(audit_log_template, "pk", None)

    with transaction.atomic():
        if template_pk is not None:
            audit_log = AuditLog.objects.filter(pk=template_pk).first()

        if audit_log is None and calculation_id:
            audit_log = (
                AuditLog.objects.filter(calculation_id=calculation_id).order_by("-id").first()
            )

        if audit_log is None:
            audit_log = AuditLog.objects.create(
                author=author,
                resource=resource,
                action=action,
                payload=payload,
                calculation_id=calculation_id,
                content_type=safe_get_content_type(instance.__class__),
                object_id=instance.pk,
            )
        else:
            audit_log.author = audit_log.author or author
            audit_log.resource = audit_log.resource or resource
            audit_log.action = audit_log.action or action
            if calculation_id and not audit_log.calculation_id:
                audit_log.calculation_id = calculation_id
            audit_log.content_type = safe_get_content_type(instance.__class__)
            audit_log.object_id = instance.pk
            audit_log.payload = payload
            audit_log.save()

        updated_rows = AuditLogStatus.objects.filter(audit_log=audit_log).update(
            status=audit_status,
            error_traceback=stack_trace if audit_status == "failure" else None,
            updated_at=timezone.now(),
        )
        if updated_rows == 0:
            AuditLogStatus.objects.create(
                audit_log=audit_log,
                status=audit_status,
                error_traceback=stack_trace if audit_status == "failure" else None,
            )

    _update_operation_context_audit_log(audit_log)
    return audit_log
