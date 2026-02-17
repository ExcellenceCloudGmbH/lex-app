import logging
from typing import Optional

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer

from django.dispatch import Signal

from lex.api.utils import operation_context
from lex.core.calculated_updates.update_handler import CalculatedModelUpdateHandler
from lex.core.models.CalculationModel import CalculationModel
from lex.core.signals.ActiveCalculationStateStore import ActiveCalculationStateStore

logger = logging.getLogger(__name__)

def update_calculation_status(
    instance,
    exception_details: Optional[str] = None,
    stack_trace: Optional[str] = None,
):
    if not issubclass(instance.__class__, CalculationModel):
        return

    channel_layer = get_channel_layer()
    message_type = ""
    record_id = f"{instance._meta.model_name}_{instance.id}"
    calculation_id = _resolve_calculation_id(instance, record_id)

    if instance.is_calculated == CalculationModel.IN_PROGRESS:
        message_type = "calculation_in_progress"
        ActiveCalculationStateStore.mark_in_progress(
            record_id=record_id,
            calculation_id=calculation_id,
            record=str(instance),
            model_label=instance._meta.label_lower,
            record_pk=instance.pk,
        )
    elif instance.is_calculated == CalculationModel.SUCCESS:
        message_type = "calculation_success"
        ActiveCalculationStateStore.clear(record_id)
        _perform_cache_cleanup_for_status_update(instance, "SUCCESS")
    elif instance.is_calculated == CalculationModel.ERROR:
        message_type = "calculation_error"
        ActiveCalculationStateStore.clear(record_id)
        _perform_cache_cleanup_for_status_update(instance, "ERROR")
    elif instance.is_calculated == CalculationModel.ABORTED:
        ActiveCalculationStateStore.clear(record_id)
        _perform_cache_cleanup_for_status_update(instance, "ABORTED")

    if not message_type or not channel_layer:
        return

    payload = {
        "record": str(instance),
        "record_id": record_id,
    }
    if calculation_id:
        payload["calculation_id"] = calculation_id

    # Keep websocket error payload aligned with API exception payloads.
    if exception_details:
        payload["message"] = exception_details
    if stack_trace:
        payload["traceback"] = stack_trace

    message = {
        "type": message_type,
        "payload": payload,
    }
    async_to_sync(channel_layer.group_send)("update_calculation_status", message)


def _resolve_calculation_id(instance, record_id: str) -> Optional[str]:
    try:
        context_calculation_id = operation_context.get().get("calculation_id")
        if context_calculation_id:
            return context_calculation_id
    except Exception:
        pass

    tracked_calculation_id = ActiveCalculationStateStore.get_calculation_id(record_id)
    if tracked_calculation_id:
        return tracked_calculation_id

    if hasattr(instance, "calculation_id"):
        instance_calculation_id = getattr(instance, "calculation_id")
        if isinstance(instance_calculation_id, str) and instance_calculation_id:
            return instance_calculation_id

    return None


def _perform_cache_cleanup_for_status_update(instance, status):
    """
    Perform cache cleanup when calculation status is updated to a completed state.
    
    Args:
        instance: The CalculationModel instance
        status: The status that triggered the cleanup (SUCCESS, ERROR, or ABORTED)
    """
    pass
    # try:
    #     calc_id = operation_context.get()["calculation_id"]
    #     cleanup_result = CacheManager.cleanup_calculation(calc_id)
    #
    #     if cleanup_result.success:
    #         logger.info(f"Cache cleanup successful for calculation {calc_id} status update to {status}")
    #     else:
    #         logger.warning(f"Cache cleanup had errors for calculation {calc_id} status update to {status}: {cleanup_result.errors}")
    #
    # except Exception as e:
    #     logger.error(f"Cache cleanup failed for calculation status update to {status}: {str(e)}")


def do_post_save(sender, **kwargs):
    CalculatedModelUpdateHandler.register_save(kwargs["instance"])


custom_post_save = Signal()
