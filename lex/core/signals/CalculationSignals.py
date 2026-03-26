import logging
from typing import Optional

from django.dispatch import Signal

from lex.api.utils import operation_context
from lex.core.calculated_updates.update_handler import CalculatedModelUpdateHandler
from lex.core.models.CalculationModel import CalculationModel
from lex.core.signals.ActiveCalculationStateStore import ActiveCalculationStateStore
from lex.utilities.channel_layer import sync_channel_group_send

logger = logging.getLogger(__name__)


def update_calculation_status(
    instance,
    exception_details: Optional[str] = None,
    stack_trace: Optional[str] = None,
):
    """
    Broadcast the current ``is_calculated`` state of *instance* to all connected
    WebSocket clients via the ``update_calculation_status`` channel group.

    This is the **single authoritative function** for propagating calculation
    state changes.  It is called from:

    * ``calculate_hook``        – when the calculation starts (IN_PROGRESS)
    * ``execute_calculation_sync`` – when the calculation finishes (SUCCESS / ERROR)
    * ``One.py`` exception path – on dispatch failure (ERROR)
    * Startup reset             – when leftover IN_PROGRESS rows are set to ABORTED

    Design rules
    ------------
    1. IN_PROGRESS  → register in ActiveCalculationStateStore, broadcast.
    2. SUCCESS/ERROR → clear from ActiveCalculationStateStore, broadcast.
    3. ABORTED      → clear from ActiveCalculationStateStore, broadcast.
    4. **Never** suppress a broadcast — the frontend idempotently handles
       duplicates and needs every signal to stay in sync (especially across
       multiple browser tabs / users).
    """
    if not issubclass(instance.__class__, CalculationModel):
        return

    record_id = f"{instance._meta.model_name}_{instance.id}"
    calculation_id = _resolve_calculation_id(instance, record_id)
    message_type = ""

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

    elif instance.is_calculated == CalculationModel.ERROR:
        message_type = "calculation_error"
        ActiveCalculationStateStore.clear(record_id)

    elif instance.is_calculated == CalculationModel.ABORTED:
        message_type = "calculation_aborted"
        ActiveCalculationStateStore.clear(record_id)

    if not message_type:
        return

    payload = {
        "record": str(instance),
        "record_id": record_id,
    }
    if calculation_id:
        payload["calculation_id"] = calculation_id

    if exception_details:
        payload["message"] = exception_details
    if stack_trace:
        payload["traceback"] = stack_trace

    message = {
        "type": message_type,
        "payload": payload,
    }

    sync_channel_group_send("update_calculation_status", message)


def _resolve_calculation_id(instance, record_id: str) -> Optional[str]:
    """
    Resolve the ``calculation_id`` for this instance in order of priority:

    1. The current ``operation_context`` (set by the API view).
    2. The ActiveCalculationStateStore (set when the calculation was registered).
    3. An explicit ``calculation_id`` attribute on the instance.
    """
    # 1. Operation context (set by One.py / OperationContext)
    try:
        ctx_calc_id = operation_context.get().get("calculation_id")
        if ctx_calc_id:
            return ctx_calc_id
    except Exception:
        pass

    # 2. Cache store
    tracked = ActiveCalculationStateStore.get_calculation_id(record_id)
    if tracked:
        return tracked

    # 3. Instance attribute (rare fallback)
    if hasattr(instance, "calculation_id"):
        val = getattr(instance, "calculation_id")
        if isinstance(val, str) and val:
            return val

    return None



def do_post_save(sender, **kwargs):
    CalculatedModelUpdateHandler.register_save(kwargs["instance"])


custom_post_save = Signal()
