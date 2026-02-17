import logging
import sys
from typing import Optional

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.core.signals import got_request_exception

from core.models.CalculationModel import CalculationModelException, CalculationModel
from lex.core.calculated_updates.update_handler import (
    CalculatedModelUpdateHandler,
)
from lex.audit_logging.utils.CacheManager import CacheManager
from lex.api.utils import operation_context

logger = logging.getLogger(__name__)

from django.db.models.signals import post_save
from django.dispatch import receiver, Signal
from django.contrib.auth.models import User
from lex.authentication.models.Profile import Profile


@receiver(post_save, sender=User)
def create_profile(sender, instance, created, **kwargs):
    if created:
        Profile.objects.create(user=instance)


@receiver(post_save, sender=User)
def save_profile(sender, instance, **kwargs):
    # ensures profile.save() runs even on updates
    instance.profile.save()



def update_calculation_status(
    instance,
    exception_details: Optional[str] = None,
    stack_trace: Optional[str] = None,
):
    from lex.core.models.CalculationModel import CalculationModel

    if issubclass(instance.__class__, CalculationModel):
        channel_layer = get_channel_layer()
        message_type = ""
        if instance.is_calculated == CalculationModel.IN_PROGRESS:
            message_type = "calculation_in_progress"
        elif instance.is_calculated == CalculationModel.SUCCESS:
            message_type = "calculation_success"
            # Perform cache cleanup for successful calculations
            _perform_cache_cleanup_for_status_update(instance, "SUCCESS")
        elif instance.is_calculated == CalculationModel.ERROR:
            message_type = "calculation_error"
            # Perform cache cleanup for failed calculations
            _perform_cache_cleanup_for_status_update(instance, "ERROR")
        elif instance.is_calculated == CalculationModel.ABORTED:
            # Perform cache cleanup for aborted calculations
            _perform_cache_cleanup_for_status_update(instance, "ABORTED")

        if not message_type or not channel_layer:
            return

        payload = {
            "record": str(instance),
            "record_id": f"{instance._meta.model_name}_{instance.id}",
        }

        # Keep websocket error payload aligned with API exception payloads.
        if exception_details:
            payload["message"] = exception_details
        if stack_trace:
            payload["traceback"] = stack_trace

        message = {
            "type": message_type,  # This is the correct naming convention
            "payload": payload,
        }
        # notification = Notifications(message="Calculation is finished", timestamp=datetime.now())
        # notification.save()
        async_to_sync(channel_layer.group_send)(f"update_calculation_status", message)


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
