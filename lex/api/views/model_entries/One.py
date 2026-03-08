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

    @staticmethod
    def _fail_calculation_chain(
        calculation_id: str,
        triggering_obj,
        exception_details: str,
        stack_trace: str,
    ):
        """
        Mark every CalculationModel in the same calculation chain as ERROR.

        The chain is discovered via ``CalculationLog`` entries that share the
        same ``calculationId``.  Each log entry references a concrete model
        instance through its ``content_type`` / ``object_id`` generic FK.
        We walk the entire set (root, parents, children) so that no orphaned
        IN_PROGRESS spinners remain in the frontend.

        Steps
        -----
        1. Mark the *triggering* object as ERROR (the one that raised).
        2. Query all ``CalculationLog`` rows for this ``calculationId``.
        3. For each referenced ``CalculationModel`` instance that is still
           IN_PROGRESS, set ``is_calculated = ERROR``, persist, and broadcast.
        4. Clean up ``ActiveCalculationStateStore`` entries.
        """
        from lex.core.signals.CalculationSignals import update_calculation_status
        from lex.core.signals.ActiveCalculationStateStore import ActiveCalculationStateStore
        from lex.audit_logging.models.CalculationLog import CalculationLog

        already_handled = set()

        def _mark_error(instance):
            """Set ERROR on a single CalculationModel instance & broadcast."""
            record_id = f"{instance._meta.model_name}_{instance.pk}"
            if record_id in already_handled:
                return
            already_handled.add(record_id)

            if getattr(instance, "is_calculated", None) in (
                CalculationModel.IN_PROGRESS,
                CalculationModel.NOT_CALCULATED,
            ):
                instance.is_calculated = CalculationModel.ERROR
                try:
                    instance.save(skip_hooks=True)
                except Exception as save_err:
                    logger.error(
                        f"Failed to persist ERROR for {record_id}: {save_err}",
                        exc_info=True,
                    )

            # Always broadcast so the frontend clears the spinner.
            try:
                update_calculation_status(
                    instance,
                    exception_details=exception_details,
                    stack_trace=stack_trace,
                )
            except Exception as ws_err:
                logger.error(
                    f"Failed to broadcast ERROR for {record_id}: {ws_err}",
                    exc_info=True,
                )

            # Ensure the state-store entry is cleared.
            ActiveCalculationStateStore.clear(record_id)

        # --- 1. Handle the triggering object first ----------------------
        if triggering_obj and isinstance(triggering_obj, CalculationModel):
            _mark_error(triggering_obj)

        # --- 2. Walk the CalculationLog hierarchy -----------------------
        try:
            chain_logs = CalculationLog.objects.filter(
                calculationId=calculation_id,
            ).select_related("content_type")

            for log_entry in chain_logs:
                if log_entry.content_type and log_entry.object_id:
                    model_class = log_entry.content_type.model_class()
                    if model_class and issubclass(model_class, CalculationModel):
                        try:
                            related_instance = model_class.objects.get(
                                pk=log_entry.object_id
                            )
                            _mark_error(related_instance)
                        except model_class.DoesNotExist:
                            logger.debug(
                                f"CalculationLog references missing "
                                f"{model_class.__name__} pk={log_entry.object_id}"
                            )
                        except Exception as fetch_err:
                            logger.error(
                                f"Error fetching chain member from log "
                                f"entry {log_entry.pk}: {fetch_err}",
                                exc_info=True,
                            )
        except Exception as chain_err:
            logger.error(
                f"Failed to walk CalculationLog chain for "
                f"calculationId={calculation_id}: {chain_err}",
                exc_info=True,
            )

        # --- 3. Final cache cleanup for the whole calculation -----------
        try:
            CacheManager.cleanup_calculation(calculation_id=calculation_id)
        except Exception as cache_err:
            logger.error(
                f"Cache cleanup failed for calculationId={calculation_id}: {cache_err}",
                exc_info=True,
            )

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
                is_historical = (hasattr(model_container.model_class, 'valid_from') or hasattr(
                    model_container.model_class, 'history_date')) and hasattr(model_container.model_class, 'history_id')
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

                    if self._calculate_requested:
                        calculation_record = f"{instance._meta.model_name}_{instance.pk}"
                        instance.untrack()
                        instance.is_calculated = CalculationModel.IN_PROGRESS
                        instance.save(skip_hooks=True)
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
                    # Handle the entire calculation chain: when a calculation
                    # fails, all objects in the same parent-child hierarchy
                    # (tracked via CalculationLog.parent_log) must be marked
                    # as ERROR so the frontend shows a consistent state.
                    self._fail_calculation_chain(
                        calculationId,
                        exc.calc_obj,
                        exc.exception_details,
                        exc.stack_trace,
                    )
                    raise APIException(
                        {"message": f"{exc.exception_details} ", "traceback": exc.stack_trace}
                    )

                except Exception as e:
                    raise APIException(
                        {"error": f"{e} ", "traceback": traceback.format_exc()}
                    )
                finally:
                    self._calculate_requested = False
