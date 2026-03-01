import traceback
import logging

from django.db import transaction
from rest_framework_api_key.permissions import HasAPIKey

from lex.audit_logging.utils.ModelContext import model_logging_context
from rest_framework.exceptions import APIException
from rest_framework.generics import RetrieveUpdateDestroyAPIView, CreateAPIView
from rest_framework.mixins import CreateModelMixin, UpdateModelMixin

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

    def _prepare_update_request(self, request):
        payload = (
            request.data.copy()
            if hasattr(request.data, "copy")
            else dict(request.data)
        )
        payload.pop("calculate", None)
        request._data = payload
        request._full_data = payload
        return request

    def perform_update(self, serializer):
        if getattr(self, "_calculate_requested", False):
            if isinstance(serializer.instance, CalculationModel):
                # Inject the IN_PROGRESS status into validated_data so
                # AuditLogMixin.perform_update() saves it naturally.
                # AuditLogMixin.log_change() already stores the audit_log
                # in operation_context['audit_log_temp'] for ContextResolver.
                serializer.validated_data['is_calculated'] = CalculationModel.IN_PROGRESS
        super().perform_update(serializer)

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

        with OperationContext(request, calculationId):
            instance = self.get_object()
            with model_logging_context(instance):
                self._calculate_requested = (
                    isinstance(instance, CalculationModel)
                    and str(request.data.get("calculate", "")).lower() == "true"
                )

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
                        prepared_request = self._prepare_update_request(request)
                        return UpdateModelMixin.update(self, prepared_request, *args, **kwargs)

                    except Exception as e:
                        raise APIException(
                            {"error": f"Bitemporal update failed: {e}", "traceback": traceback.format_exc()}
                        )

                # STANDARD UPDATE LOGIC (Main Models)
                try:
                    instance.track()

                    if self._calculate_requested:
                        calculation_record = f"{instance._meta.model_name}_{instance.pk}"

                        # ── Early registration ──────────────────────────────
                        # Register the calculation in the authoritative cache
                        # store and broadcast IN_PROGRESS **before** entering
                        # the atomic transaction.  This guarantees that:
                        #   a) A page-refresh during the calculation will see
                        #      the IN_PROGRESS entry in the reconciliation
                        #      snapshot (no DB read needed).
                        #   b) Other users/tabs receive the IN_PROGRESS
                        #      WebSocket message immediately.
                        from lex.core.signals.ActiveCalculationStateStore import ActiveCalculationStateStore
                        ActiveCalculationStateStore.mark_in_progress(
                            record_id=calculation_record,
                            calculation_id=calculationId,
                            record=str(instance),
                            model_label=instance._meta.label_lower,
                            record_pk=instance.pk,
                        )

                        # Notify the "calculations" group (GenericSocket) so
                        # the triggering client can pair its temp ID with the
                        # server-side calculation_id.
                        WebSocketNotifier.send_calculation_update(
                            calculation_id=calculationId,
                            calculation_record=calculation_record,
                        )

                        cache_key = CacheManager.build_cache_key(
                            calculation_record,
                            calculationId,
                        )
                        CacheManager.store_message(cache_key, "")

                    prepared_request = self._prepare_update_request(request)
                    return UpdateModelMixin.update(self, prepared_request, *args, **kwargs)

                except CalculationModelException as exc:
                    # CalculationModel's own exception path already:
                    #   1. set is_calculated = ERROR
                    #   2. saved with skip_hooks=True
                    #   3. broadcast the ERROR status via WebSocket
                    # We only need to surface the error to the API caller.
                    raise APIException(
                        {"message": f"{exc.exception_details} ", "traceback": exc.stack_trace}
                    )

                except Exception as e:
                    raise APIException(
                        {"error": f"{e} ", "traceback": traceback.format_exc()}
                    )
                finally:
                    self._calculate_requested = False
