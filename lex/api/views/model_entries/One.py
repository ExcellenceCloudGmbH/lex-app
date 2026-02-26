import traceback
import logging

from django.db import transaction
from rest_framework_api_key.permissions import HasAPIKey

from lex.audit_logging.utils.ModelContext import model_logging_context
from rest_framework.exceptions import APIException
from rest_framework.generics import RetrieveUpdateDestroyAPIView, CreateAPIView
from rest_framework.mixins import CreateModelMixin

from rest_framework.response import Response
from rest_framework import status
from lex.core.models.CalculationModel import CalculationModel, CalculationModelException

from lex.audit_logging.mixins.AuditLogMixin import AuditLogMixin
from lex.api.utils.Context import OperationContext
from lex.api.views.model_entries.mixins.DestroyOneWithPayloadMixin import (
    DestroyOneWithPayloadMixin,
)
from lex.api.views.model_entries.mixins.ModelEntryProviderMixin import (
    ModelEntryProviderMixin,
)
from rest_framework.permissions import IsAuthenticated
from rest_framework.exceptions import PermissionDenied

from lex.api.views.permissions.UserPermission import UserPermission
from lex.audit_logging.utils.CacheManager import CacheManager
from lex.audit_logging.utils.WebSocketNotifier import WebSocketNotifier

logger = logging.getLogger(__name__)


class OneModelEntry(
    AuditLogMixin,
    ModelEntryProviderMixin,
    DestroyOneWithPayloadMixin,
    RetrieveUpdateDestroyAPIView,
    CreateAPIView,
):
    # Keep parity with ModelEntryProviderMixin defaults so model-level
    # modification_restriction rules (e.g. legacy read-only) are enforced.
    permission_classes = [HasAPIKey | IsAuthenticated, UserPermission]

    def create(self, request, *args, **kwargs):
        model_container = self.kwargs["model_container"]
        instance = model_container.model_class()
        # Check create permission using new system
        try:
            if hasattr(instance, 'permission_create'):
                from lex.core.models.LexModel import UserContext
                user_context = UserContext.from_request(request, instance)
                can_create = instance.permission_create(user_context)
            else:
                # Fallback to legacy method
                can_create = instance.can_create(request)
                
            if not can_create:
                return Response(
                    {
                        "message": f"You are not authorized to create a record in {model_container.model_class.__name__}"
                    },
                    status=status.HTTP_400_BAD_REQUEST
                )
        except Exception:
            # Allow by default on permission check error
            pass

            # return Response(data={}, status=status.HTTP_204_NO_CONTENT, headers={}, exception=e)

        calculationId = self.kwargs["calculationId"]

        with OperationContext(request, calculationId) as context_id:

            try:
                with transaction.atomic():
                    response = CreateModelMixin.create(self, request, *args, **kwargs)

            except Exception as e:
                raise APIException(
                    {"error": f"{e} ", "traceback": traceback.format_exc()}
                )

            return response

    def update(self, request, *args, **kwargs):

        model_container = self.kwargs["model_container"]
        calculationId = self.kwargs["calculationId"]
        partial = kwargs.pop("partial", False)

        with OperationContext(request, calculationId):
            instance = self.get_object()
            with model_logging_context(instance):
                calculate_requested = str(request.data.get("calculate", "")).lower() == "true"
                update_payload = (
                    request.data.copy()
                    if hasattr(request.data, "copy")
                    else dict(request.data)
                )
                update_payload.pop("calculate", None)

                # TODO: For sharepoint preview, find a new way to create an audit log with the new structure
                # if "edited_file" not in request.data:

                # BITEMPORAL UPDATE LOGIC
                # Check if this is a Historical Model (but not a Meta Historical Model)
                # Note: history_date is renamed to valid_from in registration
                is_historical = (hasattr(model_container.model_class, 'valid_from') or hasattr(model_container.model_class, 'history_date')) and hasattr(model_container.model_class, 'history_id')
                is_meta = hasattr(model_container.model_class, 'meta_history_id')

                if is_meta:
                    raise PermissionDenied("Modifying Meta-History records is not allowed.")

                if is_historical:
                    # Bitemporal Correction:
                    # We are correcting a specific "Reality Slice". 
                    # The Historical Record represents {Valid From, Valid To, Data}.
                    # We allow updating this record directly.
                    # The 'Meta History' system (Level 2) will automatically:
                    # 1. Detect the change (via post_save signal).
                    # 2. Create a new Meta Record (New System Version).
                    # 3. Close the previous Meta Record (System Time End).
                    
                    try:
                        serializer = self.get_serializer(
                            instance, data=update_payload, partial=partial
                        )
                        serializer.is_valid(raise_exception=True)
                        updated_instance = self.perform_update(serializer)
                        if getattr(updated_instance, "_prefetched_objects_cache", None):
                            updated_instance._prefetched_objects_cache = {}
                        return Response(serializer.data)

                    except Exception as e:
                        raise APIException(
                            {"error": f"Bitemporal update failed: {e}", "traceback": traceback.format_exc()}
                        )

                # STANDARD UPDATE LOGIC (Main Models)
                try:
                    instance.track()
                    serializer = self.get_serializer(
                        instance, data=update_payload, partial=partial
                    )
                    serializer.is_valid(raise_exception=True)

                    if calculate_requested:
                        serializer.instance.is_calculated = CalculationModel.IN_PROGRESS
                        calculation_record = f"{instance._meta.model_name}_{instance.pk}"
                        WebSocketNotifier.send_calculation_update(
                            calculation_id=calculationId,
                            calculation_record=calculation_record,
                        )
                        cache_key = CacheManager.build_cache_key(
                            calculation_record,
                            calculationId,
                        )
                        CacheManager.store_message(cache_key, "")

                    updated_instance = self.perform_update(serializer)
                    if getattr(updated_instance, "_prefetched_objects_cache", None):
                        updated_instance._prefetched_objects_cache = {}
                    return Response(serializer.data)

                except CalculationModelException as exc:
                    calc_obj = exc.calc_obj
                    exception_details = exc.exception_details
                    stack_trace = exc.stack_trace
                    if calc_obj:
                        calc_obj.is_calculated = CalculationModel.ERROR
                        calc_obj.save(skip_hooks=True)

                    raise APIException(
                        {"message": f"{exception_details} ", "traceback": stack_trace}
                    )

                except Exception as e:
                    raise APIException(
                        {"error": f"{e} ", "traceback": traceback.format_exc()}
                    )
