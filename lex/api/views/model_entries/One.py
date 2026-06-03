import copy
import logging
import traceback
from contextlib import nullcontext
from datetime import date, datetime
from typing import Iterable, cast

from django.core.files.base import File
from django.db import transaction
from django.db.models import Model, QuerySet
from lex.api.utils.Context import OperationContext
from lex.api.views.model_entries.mixins.DestroyOneWithPayloadMixin import (
    DestroyOneWithPayloadMixin,
)
from lex.api.views.model_entries.mixins.ModelEntryProviderMixin import (
    ModelEntryProviderMixin,
)
from lex.api.views.permissions.UserPermission import UserPermission
from lex.audit_logging.mixins.AuditLogMixin import AuditLogMixin
from lex.audit_logging.utils.CacheManager import CacheManager
from lex.audit_logging.utils.ModelContext import model_logging_context
from lex.audit_logging.utils.WebSocketNotifier import WebSocketNotifier
from lex.core.exceptions import resolve_exception_detail, resolve_exception_traceback
from lex.core.models.CalculationModel import CalculationModel, CalculationModelException
from lex.core.signals.ModelMutationSignal import broadcast_model_mutation
from lex.core.models.LexModel import should_use_atomic_model_operations
from rest_framework import status
from rest_framework.exceptions import APIException, ValidationError
from rest_framework.exceptions import PermissionDenied
from rest_framework.generics import RetrieveUpdateDestroyAPIView, CreateAPIView
from rest_framework.mixins import CreateModelMixin, UpdateModelMixin
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework_api_key.permissions import HasAPIKey

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

    def _broadcast_update(self, instance):
        """Notify open list views that *instance* was updated.

        Emits a generic ``record_mutation`` broadcast (deferred to commit) so
        grids open in other tabs / users / embedded views refresh without a
        manual reload. Calculation-driven refreshes are handled separately by
        the ``update_calculation_status`` broadcast.
        """
        model_name = instance._meta.model_name
        broadcast_model_mutation(
            model_name, "updated", f"{model_name}_{instance.pk}"
        )

    def _build_sharepoint_history_change_reason(self, request):
        edited_file = ""
        if hasattr(request, "data"):
            edited_file = str(request.data.get("edited_file", "")).strip()
        file_label = edited_file or "SharePoint file"
        reason = f"SharePoint edit opened for {file_label}; calculation reset"
        return reason[:100]

    def _should_reset_is_calculated_for_update(self, instance):
        return (
            isinstance(instance, CalculationModel)
            and not getattr(self, "_calculate_requested", False)
        )

    def perform_update(self, serializer):
        if self._should_reset_is_calculated_for_update(serializer.instance):
            serializer.instance.is_calculated = CalculationModel.NOT_CALCULATED
        if hasattr(self, "request") and self._should_skip_history_for_sharepoint_edit(
            self.request,
            serializer.instance,
        ):
            serializer.instance._history_change_reason = self._build_sharepoint_history_change_reason(
                self.request
            )
        if (
            getattr(self, "_calculate_requested", False)
            and isinstance(serializer.instance, CalculationModel)
        ):
            serializer.instance._skip_history_for_current_save_only = True
            # ── Async-202 contract: defer the AFTER_UPDATE calculate_hook ──
            # ``OneModelEntry.update`` submits ``calculate_hook`` to the
            # background ``_calculation_executor`` and returns HTTP 202.
            # For that split to work, the request-thread save must NOT
            # fire the calc inline.  We set the deferral flag on
            # ``serializer.instance`` (rather than on the view-local
            # ``instance``) because DRF's ``UpdateModelMixin.update``
            # calls ``self.get_object()`` independently, producing a
            # *different* Python object for the same DB row — any flag
            # set on the view-local copy never reaches the actual save.
            # ``CalculationModel.calculate_hook`` checks this flag at
            # entry and returns early without running ``calculate()``.
            serializer.instance._defer_calculate_hook = True
        return super().perform_update(serializer)

    def _normalize_temporal_value_for_noop_check(self, value):
        if isinstance(value, datetime):
            return value.isoformat()
        if isinstance(value, date):
            return value.isoformat()
        return None

    def _unordered_iterables_match_for_noop_check(self, incoming_values, current_values):
        if len(incoming_values) != len(current_values):
            return False

        remaining_values = list(current_values)
        for incoming_value in incoming_values:
            match_index = next(
                (
                    index
                    for index, current_value in enumerate(remaining_values)
                    if self._values_match_for_noop_check(incoming_value, current_value)
                ),
                None,
            )
            if match_index is None:
                return False
            remaining_values.pop(match_index)

        return not remaining_values

    def _values_match_for_noop_check(self, incoming_value, current_value):
        if isinstance(incoming_value, File):
            return False

        incoming_temporal = self._normalize_temporal_value_for_noop_check(incoming_value)
        current_temporal = self._normalize_temporal_value_for_noop_check(current_value)
        if incoming_temporal is not None or current_temporal is not None:
            return incoming_temporal is not None and incoming_temporal == current_temporal

        if isinstance(incoming_value, Model):
            return getattr(current_value, "pk", current_value) == incoming_value.pk

        if isinstance(current_value, Model):
            incoming_pk = getattr(incoming_value, "pk", incoming_value)
            return current_value.pk == incoming_pk

        if hasattr(current_value, "all") and callable(current_value.all):
            if isinstance(incoming_value, QuerySet):
                incoming_values = list(incoming_value)
            elif isinstance(incoming_value, (list, tuple, set)):
                incoming_values = list(incoming_value)
            else:
                return False
            current_values = current_value.all()
            if not hasattr(current_values, "__iter__"):
                return False
            return self._unordered_iterables_match_for_noop_check(
                incoming_values,
                list(cast(Iterable, current_values)),
            )

        if isinstance(incoming_value, QuerySet):
            incoming_value = list(incoming_value)
        if isinstance(current_value, QuerySet):
            current_value = list(current_value)

        if isinstance(incoming_value, dict) or isinstance(current_value, dict):
            if not isinstance(incoming_value, dict) or not isinstance(current_value, dict):
                return False
            if incoming_value.keys() != current_value.keys():
                return False
            return all(
                self._values_match_for_noop_check(incoming_value[key], current_value[key])
                for key in incoming_value
            )

        if isinstance(incoming_value, (list, tuple)) or isinstance(current_value, (list, tuple)):
            if not isinstance(incoming_value, (list, tuple)) or not isinstance(
                current_value, (list, tuple)
            ):
                return False
            if len(incoming_value) != len(current_value):
                return False
            return all(
                self._values_match_for_noop_check(left, right)
                for left, right in zip(incoming_value, current_value)
            )

        if isinstance(incoming_value, (set, frozenset)) or isinstance(
            current_value, (set, frozenset)
        ):
            if not isinstance(incoming_value, (set, frozenset)) or not isinstance(
                current_value, (set, frozenset)
            ):
                return False
            return self._unordered_iterables_match_for_noop_check(
                list(incoming_value),
                list(current_value),
            )

        return incoming_value == current_value

    def _serializer_update_is_noop(self, serializer):
        validated_data = getattr(serializer, "validated_data", {}) or {}
        if len(validated_data) == 0:
            return True

        instance = serializer.instance
        return all(
            self._values_match_for_noop_check(value, getattr(instance, field_name))
            for field_name, value in validated_data.items()
        )

    def _build_noop_update_response(self, request, instance, *, partial=False):
        if (
            not hasattr(request, "data")
            or self._calculate_requested
            or "edited_file" in request.data
        ):
            return None

        serializer = self.get_serializer(instance, data=request.data, partial=partial)
        serializer.is_valid(raise_exception=True)
        if not self._serializer_update_is_noop(serializer):
            return None

        return Response(self.get_serializer(instance).data, status=status.HTTP_200_OK)

    def _reset_instance_is_calculated(
        self,
        response,
        *,
        skip_history=False,
        history_change_reason=None,
    ):
        instance = self.get_object()
        if not isinstance(instance, CalculationModel):
            return response
        if instance.is_calculated != CalculationModel.NOT_CALCULATED:
            instance.is_calculated = CalculationModel.NOT_CALCULATED
            if history_change_reason:
                instance._history_change_reason = history_change_reason
            if skip_history and hasattr(instance, "save_without_historical_record"):
                instance.save_without_historical_record(skip_hooks=True)
            else:
                instance.save(skip_hooks=True)
        if isinstance(getattr(response, "data", None), dict):
            response.data["is_calculated"] = CalculationModel.NOT_CALCULATED
        return response

    def _prepare_update_request(self, request, *, reset_is_calculated=False):
        payload = copy.copy(request.data) if hasattr(request, "data") else {}
        payload.pop("calculate", None)
        if reset_is_calculated:
            payload["is_calculated"] = CalculationModel.NOT_CALCULATED
        request._data = payload
        request._full_data = payload
        return request

    def _should_skip_history_for_sharepoint_edit(self, request, instance):
        return (
            self._should_reset_is_calculated_for_update(instance)
            and hasattr(request, "data")
            and "edited_file" in request.data
        )

    def _log_failed_request_audit_if_needed(
        self,
        *,
        action,
        target,
        exception,
        payload=None,
        related_instance=None,
    ):
        """Persist a failure audit after request-level control leaves the failed mutation.

        perform_create/update/destroy already try to mark failures inline. In atomic
        request flows those writes roll back with the failed transaction, so the mixin
        can only queue the fallback there. This helper runs after the failure bubbles
        out, flushes that queued fallback, and also covers exceptions raised before or
        around the perform_* methods where no inline audit was written.
        """
        try:
            if self.flush_pending_failed_audit_log() is not None:
                return

            if self.has_logged_failure_audit():
                return

            self.log_failed_change(
                action,
                target,
                payload=payload,
                error_traceback=resolve_exception_traceback(
                    exception,
                    fallback=traceback.format_exc(),
                ),
                related_instance=related_instance,
            )
        except Exception:
            logger.exception(
                "Failed to write failure audit log for %s on %s",
                action,
                getattr(target, "__name__", target),
            )

    def _validation_error_response(self, exc: ValidationError) -> Response:
        """Return DRF validation failures as customer-visible HTTP 400.

        The view wraps mutation paths so it can add audit logging. That
        wrapper must not turn serializer validation errors into generic
        ``APIException`` responses, because DRF's public contract is a
        400 with field-level details.
        """
        return Response(exc.detail, status=status.HTTP_400_BAD_REQUEST)

    def create(self, request, *args, **kwargs):
        self.reset_failed_audit_log_state()
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
                # BUG-008 fix: authorization failures must surface as
                # 401 (anonymous) or 403 (authenticated-but-denied),
                # not 400 — 400 is for client-side validation errors.
                # Frontends and API clients route on this status code
                # (auto-redirect to login on 401, "permission denied"
                # banner on 403); collapsing both into 400 breaks
                # both code paths.
                is_anonymous = not bool(
                    getattr(getattr(request, "user", None), "is_authenticated", False)
                )
                deny_status = (
                    status.HTTP_401_UNAUTHORIZED
                    if is_anonymous
                    else status.HTTP_403_FORBIDDEN
                )
                return Response(
                    {
                        "message": f"You are not authorized to create a record in {model_container.model_class.__name__}"
                    },
                    status=deny_status,
                )
        except Exception:
            # Allow by default on permission check error
            pass

            # return Response(data={}, status=status.HTTP_204_NO_CONTENT, headers={}, exception=e)

        calculationId = self.kwargs["calculationId"]

        with OperationContext(request, calculationId) as context_id:

            try:
                atomic_context = (
                    transaction.atomic()
                    if should_use_atomic_model_operations(model_container.model_class)
                    else nullcontext()
                )
                with atomic_context:
                    response = CreateModelMixin.create(self, request, *args, **kwargs)

            except ValidationError as e:
                self._log_failed_request_audit_if_needed(
                    action="create",
                    target=model_container.model_class,
                    payload=getattr(request, "data", {}),
                    exception=e,
                )
                return self._validation_error_response(e)
            except Exception as e:
                self._log_failed_request_audit_if_needed(
                    action="create",
                    target=model_container.model_class,
                    payload=getattr(request, "data", {}),
                    exception=e,
                )
                raise APIException(
                    {"error": f"{e} ", "traceback": traceback.format_exc()}
                ) from e

            # Notify open list views (other tabs / users / embedded grids) that
            # a row was created, so they refresh without a manual reload.
            model_name = model_container.model_class._meta.model_name
            created_id = (
                response.data.get(model_container.pk_name)
                if isinstance(getattr(response, "data", None), dict)
                else None
            )
            record_id = (
                f"{model_name}_{created_id}" if created_id is not None else None
            )
            broadcast_model_mutation(model_name, "created", record_id)

            return response

    def destroy(self, request, *args, **kwargs):
        self.reset_failed_audit_log_state()
        calculationId = self.kwargs["calculationId"]

        with OperationContext(request, calculationId):
            instance = self.get_object()
            audit_payload = self.get_serializer(instance).data

            with model_logging_context(instance):
                model_name = instance._meta.model_name
                record_id = f"{model_name}_{instance.pk}"
                try:
                    response = super().destroy(request, *args, **kwargs)
                    broadcast_model_mutation(model_name, "deleted", record_id)
                    return response
                except Exception as e:
                    self._log_failed_request_audit_if_needed(
                        action="delete",
                        target=instance,
                        payload=audit_payload,
                        exception=e,
                        related_instance=instance,
                    )
                    raise APIException(
                        {"error": f"{e} ", "traceback": traceback.format_exc()}
                    ) from e

    def update(self, request, *args, **kwargs):
        self.reset_failed_audit_log_state()

        model_container = self.kwargs["model_container"]
        calculationId = self.kwargs["calculationId"]

        with OperationContext(request, calculationId):
            instance = self.get_object()
            with model_logging_context(instance):
                # ── Cancel button short-circuit ────────────────────────
                # PATCH .../{pk}/?cancel=true (or body {"cancel":"true"})
                # asks the framework to revoke the in-progress Celery
                # calculation for this row. Mirrors the existing
                # ``calculate=true`` trigger pattern so no new URL route
                # is needed. The handler runs *before* any other update
                # logic — cancel does not touch any other field.
                if (
                    isinstance(instance, CalculationModel)
                    and str(request.data.get("cancel", "")).lower() == "true"
                ):
                    report = CalculationModel.cancel(instance, recursive=True)
                    http_status = (
                        status.HTTP_202_ACCEPTED
                        if report.get("cancelled")
                        else status.HTTP_409_CONFLICT
                    )
                    return Response(report, status=http_status)

                self._calculate_requested = (
                        isinstance(instance, CalculationModel)
                        and str(request.data.get("calculate", "")).lower() == "true"
                )
                should_reset_is_calculated = self._should_reset_is_calculated_for_update(instance)
                should_skip_history_for_sharepoint_edit = (
                    self._should_skip_history_for_sharepoint_edit(request, instance)
                )
                sharepoint_history_change_reason = (
                    self._build_sharepoint_history_change_reason(request)
                    if should_skip_history_for_sharepoint_edit
                    else None
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

                try:
                    noop_response = self._build_noop_update_response(
                        request,
                        instance,
                        partial=kwargs.get("partial", False),
                    )
                except ValidationError as e:
                    self._log_failed_request_audit_if_needed(
                        action="update",
                        target=model_container.model_class,
                        payload=getattr(request, "data", {}),
                        exception=e,
                        related_instance=instance,
                    )
                    return self._validation_error_response(e)
                except Exception as e:
                    self._log_failed_request_audit_if_needed(
                        action="update",
                        target=model_container.model_class,
                        payload=getattr(request, "data", {}),
                        exception=e,
                        related_instance=instance,
                    )
                    raise APIException(
                        {"error": f"{e} ", "traceback": traceback.format_exc()}
                    ) from e

                if noop_response is not None:
                    return noop_response

                if is_historical:
                    # Bitemporal Correction:
                    # We are correcting a specific "Reality Slice".
                    # The Historical Record represents {Valid From, Valid To, Data}.
                    # We allow updating this record directly.
                    # The 'Meta History' system (Level 2) will automatically:
                    # 1. Detect the change (via post_save signal).
                    # 2. Create a new Meta Record (New System Version).
                    # 3. Close the previous Meta Record (System Time End).

                    audit_payload = getattr(request, "data", {})
                    try:
                        prepared_request = self._prepare_update_request(
                            request,
                            reset_is_calculated=should_reset_is_calculated,
                        )
                        audit_payload = getattr(prepared_request, "_data", getattr(prepared_request, "data", {}))
                        response = UpdateModelMixin.update(self, prepared_request, *args, **kwargs)
                        self._broadcast_update(instance)
                        if should_reset_is_calculated:
                            return self._reset_instance_is_calculated(
                                response,
                                skip_history=should_skip_history_for_sharepoint_edit,
                                history_change_reason=sharepoint_history_change_reason,
                            )
                        return response

                    except ValidationError as e:
                        self._log_failed_request_audit_if_needed(
                            action="update",
                            target=model_container.model_class,
                            payload=audit_payload,
                            exception=e,
                            related_instance=instance,
                        )
                        return self._validation_error_response(e)
                    except Exception as e:
                        self._log_failed_request_audit_if_needed(
                            action="update",
                            target=model_container.model_class,
                            payload=audit_payload,
                            exception=e,
                            related_instance=instance,
                        )
                        raise APIException(
                            {"error": f"Bitemporal update failed: {e}", "traceback": traceback.format_exc()}
                        ) from e

                # STANDARD UPDATE LOGIC (Main Models)
                try:
                    audit_payload = getattr(request, "data", {})
                    if self._calculate_requested:
                        calculation_record = f"{instance._meta.model_name}_{instance.pk}"

                        # ── Duplicate calculation guard ─────────────────────
                        # If this record already has an active calculation
                        # (DB shows IN_PROGRESS or the in-memory state store
                        # has a registered entry), refuse to start a second
                        # calculation.  Return the current record state so the
                        # frontend stays in sync without side effects.
                        from lex.core.signals.ActiveCalculationStateStore import ActiveCalculationStateStore
                        instance.refresh_from_db()
                        already_running = (
                            instance.is_calculated == CalculationModel.IN_PROGRESS
                            or ActiveCalculationStateStore.get_calculation_id(calculation_record) is not None
                        )
                        if already_running:
                            logger.info(
                                "Duplicate calculation request ignored for %s (already IN_PROGRESS)",
                                calculation_record,
                            )
                            return Response(
                                self.get_serializer(instance).data,
                                status=status.HTTP_200_OK,
                            )

                        CalculationModel._clear_terminal_state_persistence(instance)
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

                        # ── Async calculation (HTTP 202) ────────────────────
                        # The actual deferral that prevents the calc from
                        # running on the request thread is applied in
                        # ``perform_update`` on ``serializer.instance``
                        # (the object DRF's UpdateModelMixin re-fetches
                        # and saves).  Setting the flag here on the
                        # view-local ``instance`` would have no effect —
                        # it's a *different* Python object than the one
                        # going through ``save()``.  Kept as a no-op
                        # safety net in case any code path reads from
                        # this in-memory object before the background
                        # submit clears it (see below).
                        instance._defer_calculate_hook = True
                    prepared_request = self._prepare_update_request(
                        request,
                        reset_is_calculated=should_reset_is_calculated,
                    )
                    audit_payload = getattr(prepared_request, "_data", getattr(prepared_request, "data", {}))
                    response = UpdateModelMixin.update(self, prepared_request, *args, **kwargs)
                    # A calculate=true request refreshes open lists via the
                    # calculation_success broadcast, so only emit the generic
                    # data-mutation broadcast for plain updates here.
                    if not self._calculate_requested:
                        self._broadcast_update(instance)
                    if should_reset_is_calculated:
                        return self._reset_instance_is_calculated(
                            response,
                            skip_history=should_skip_history_for_sharepoint_edit,
                            history_change_reason=sharepoint_history_change_reason,
                        )

                    if self._calculate_requested:
                        # Fire the calculation in the background and return
                        # HTTP 202 immediately.  The frontend already listens
                        # for calculation_success / calculation_error via the
                        # update_calculation_status WebSocket.
                        from contextvars import copy_context
                        from lex.core.models.CalculationModel import _calculation_executor
                        from lex.audit_logging.utils.ModelContext import (
                            _model_context,
                            ModelContext,
                        )
                        from django.db import close_old_connections

                        ctx = copy_context()

                        def _invoke_calculate_hook():
                            # Runs inside ``ctx.run(...)`` below so the
                            # request's ``operation_context`` (with
                            # ``calculation_id``) is visible to both the
                            # hook and the terminal-audit finalizer.
                            #
                            # Install a FRESH model context for the
                            # background thread.  ``copy_context()``
                            # captures a reference to the same mutable
                            # ``ModelContext`` object that the request
                            # thread's ``model_logging_context`` will
                            # pop upon returning the 202 response.  By
                            # the time this thread starts, the shared
                            # stack is empty → ContextResolver.resolve()
                            # cannot determine ``current_record`` →
                            # WebSocket log delivery and cache routing
                            # break.  Setting a new ModelContext here
                            # (inside ``ctx.run``) scopes it to this
                            # execution without affecting the request
                            # thread.
                            _model_context.set(
                                {"model_context": ModelContext([instance])}
                            )
                            instance._defer_calculate_hook = False
                            try:
                                instance.calculate_hook()
                            except Exception:
                                # The terminal failure audit is normally
                                # flushed by ``LexModel.save``'s except
                                # block (see
                                # ``_finalize_pending_terminal_audit``).
                                # In the async path we invoke
                                # ``calculate_hook`` directly — outside
                                # any ``save()`` — so we own that flush
                                # here.  Without it, the pending audit
                                # dict that ``calculate_hook`` set on
                                # the instance never becomes a row and
                                # the operator sees a 'success' audit
                                # for a calc that actually failed.
                                try:
                                    instance._finalize_pending_terminal_audit()
                                except Exception:
                                    logger.exception(
                                        "Failed to finalize terminal failure "
                                        "audit for %s after background "
                                        "calculation raised",
                                        calculation_record,
                                    )
                                raise

                        def _background_calculate():
                            try:
                                ctx.run(_invoke_calculate_hook)
                            except Exception as exc:
                                logger.error(
                                    "Background calculation failed for %s: %s",
                                    calculation_record, exc, exc_info=True,
                                )
                            finally:
                                close_old_connections()

                        _calculation_executor.submit(_background_calculate)

                        # Return the IN_PROGRESS state immediately
                        return Response(
                            self.get_serializer(instance).data,
                            status=status.HTTP_202_ACCEPTED,
                        )

                    return response

                except CalculationModelException as exc:
                    # CalculationModel's own exception path already:
                    CalculationModel.persist_error_state(exc.calc_obj)

                    self._log_failed_request_audit_if_needed(
                        action="update",
                        target=model_container.model_class,
                        payload=audit_payload,
                        exception=exc,
                        related_instance=instance,
                    )

                    raise APIException(
                        {
                            "message": f"{resolve_exception_detail(exc) or str(exc)} ",
                            "traceback": resolve_exception_traceback(
                                exc,
                                fallback=traceback.format_exc(),
                            ),
                        }
                    ) from exc

                except ValidationError as e:
                    self._log_failed_request_audit_if_needed(
                        action="update",
                        target=model_container.model_class,
                        payload=audit_payload,
                        exception=e,
                        related_instance=instance,
                    )
                    return self._validation_error_response(e)
                except Exception as e:
                    self._log_failed_request_audit_if_needed(
                        action="update",
                        target=model_container.model_class,
                        payload=audit_payload,
                        exception=e,
                        related_instance=instance,
                    )
                    raise APIException(
                        {"error": f"{e} ", "traceback": traceback.format_exc()}
                    ) from e
                finally:
                    self._calculate_requested = False
