import os
import traceback
from abc import abstractmethod
import logging
from copy import deepcopy

from django.db import models
from django.db import transaction
from django.utils import timezone
from django_lifecycle import (
    hook,
    AFTER_UPDATE,
    AFTER_CREATE,
    BEFORE_SAVE,
)
from django_lifecycle.conditions import WhenFieldValueIs
from rest_framework.exceptions import APIException

from lex.core.models.LexModel import LexModel
from lex.core.exceptions import (
    ensure_list,
    find_exception_artifacts,
    resolve_exception_detail,
    resolve_exception_traceback,
    select_preferred_exception_detail,
    select_preferred_stack_trace,
)
from lex.api.utils import operation_context, OperationContext
from lex.audit_logging.utils.CacheManager import CacheManager
from lex.audit_logging.utils.ContextResolver import ContextResolver

logger = logging.getLogger(__name__)

class CalculationModelException(APIException):
    @staticmethod
    def _ensure_list(value):
        return ensure_list(value)

    def __init__(self, *args, **kwargs):
        self.calc_obj = self._ensure_list(kwargs.get("calc_obj", None))
        self.exception_details = self._ensure_list(
            kwargs.get("exception_details", None)
        )
        self.stack_trace = self._ensure_list(kwargs.get("stack_trace", None))

        preferred_detail = select_preferred_exception_detail(self.exception_details)
        api_args = args
        if not api_args and preferred_detail:
            api_args = (preferred_detail,)

        super().__init__(*api_args)


class CalculationModel(LexModel):

    _TERMINAL_STATE_PERSISTENCE_ATTR = "_persisted_terminal_calculation_state"
    _IN_PROGRESS_STATE_PERSISTENCE_ATTR = "_persisted_in_progress_calculation_state"
    _PENDING_IN_PROGRESS_HISTORY_ATTR = "_pending_in_progress_history_snapshot"

    IN_PROGRESS = "IN_PROGRESS"
    ERROR = "ERROR"
    SUCCESS = "SUCCESS"
    NOT_CALCULATED = "NOT_CALCULATED"
    ABORTED = "ABORTED"
    STATUSES = [
        (IN_PROGRESS, "IN_PROGRESS"),
        (ERROR, "ERROR"),
        (SUCCESS, "SUCCESS"),
        (NOT_CALCULATED, "NOT_CALCULATED"),
        (ABORTED, "ABORTED"),
    ]

    is_calculated = models.CharField(
        max_length=50, choices=STATUSES, default=NOT_CALCULATED, editable=False
    )

    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        skip_hooks = kwargs.get("skip_hooks", False)
        # Detect when this save is the trigger for a new calculation cycle.
        # We split it into two phases:
        #   Phase 1: persist IN_PROGRESS (inside LexModel.save's atomic)
        #   Phase 2: run calculate_hook (outside the atomic, so the
        #            IN_PROGRESS history survives even if the calculation fails)
        is_calculation_trigger = (
            not skip_hooks
            and getattr(self, "is_calculated", None) == self.IN_PROGRESS
            and not getattr(self, "_calculation_hook_in_progress", False)
            and not getattr(self, "_defer_calculate_hook", False)
        )

        if is_calculation_trigger:
            # Phase 1: Save IN_PROGRESS with calculate_hook deferred.
            # LexModel.save wraps base_save + hooks in transaction.atomic().
            # By deferring calculate_hook, the atomic commits IN_PROGRESS
            # to the DB (or to the enclosing savepoint) *before* the
            # calculation runs.
            self._defer_calculate_hook = True
            try:
                result = super().save(*args, **kwargs)
            finally:
                self._defer_calculate_hook = False

            # Register that IN_PROGRESS has been written (on_commit for
            # nested atomics, or immediately for autocommit).
            self._register_in_progress_state_persistence(self)

            # Phase 2: Run the actual calculation outside LexModel.save's
            # atomic block.  SUCCESS / ERROR will be persisted by
            # execute_calculation_sync in their own transactions/savepoints.
            try:
                self.calculate_hook()
            except Exception:
                # calculate_hook sets _pending_terminal_audit on failure.
                # In the original (non-deferred) flow, LexModel.save's except
                # block calls _finalize_pending_terminal_audit.  Since Phase 2
                # runs outside LexModel.save, we must finalize here.
                self._finalize_pending_terminal_audit()
                raise
            return result
        else:
            result = super().save(*args, **kwargs)
            self._register_in_progress_state_persistence(self)
            return result

    @classmethod
    def _terminal_state_identity(cls, obj):
        pk = getattr(obj, "pk", None)
        if pk is not None:
            return obj.__class__, pk
        return id(obj)

    @classmethod
    def _mark_terminal_state_persisted(cls, obj):
        if obj is None:
            return

        setattr(
            obj,
            cls._TERMINAL_STATE_PERSISTENCE_ATTR,
            getattr(obj, "is_calculated", None),
        )

    @classmethod
    def _apply_in_progress_state_persistence(cls, obj, state):
        if obj is None:
            return

        if state == cls.IN_PROGRESS:
            setattr(obj, cls._IN_PROGRESS_STATE_PERSISTENCE_ATTR, True)
            if hasattr(obj, cls._PENDING_IN_PROGRESS_HISTORY_ATTR):
                delattr(obj, cls._PENDING_IN_PROGRESS_HISTORY_ATTR)
            return

        if hasattr(obj, cls._IN_PROGRESS_STATE_PERSISTENCE_ATTR):
            delattr(obj, cls._IN_PROGRESS_STATE_PERSISTENCE_ATTR)

    @classmethod
    def _register_in_progress_state_persistence(cls, obj):
        if obj is None:
            return

        state = getattr(obj, "is_calculated", None)
        if transaction.get_connection().in_atomic_block:
            transaction.on_commit(
                lambda: cls._apply_in_progress_state_persistence(obj, state)
            )
            return

        cls._apply_in_progress_state_persistence(obj, state)

    @classmethod
    def _has_persisted_in_progress_state(cls, obj):
        return bool(getattr(obj, cls._IN_PROGRESS_STATE_PERSISTENCE_ATTR, False))

    @classmethod
    def _queue_missing_in_progress_history(cls, obj):
        if obj is None or cls._has_persisted_in_progress_state(obj):
            if obj is not None and hasattr(obj, cls._PENDING_IN_PROGRESS_HISTORY_ATTR):
                delattr(obj, cls._PENDING_IN_PROGRESS_HISTORY_ATTR)
            return

        setattr(
            obj,
            cls._PENDING_IN_PROGRESS_HISTORY_ATTR,
            {
                "snapshot": obj._capture_snapshot(),
                "history_date": getattr(obj, "_history_date", timezone.now()),
                "history_user": getattr(obj, "_history_user", None),
                "history_change_reason": getattr(obj, "_history_change_reason", ""),
            },
        )

    @classmethod
    def _restore_missing_in_progress_history(cls, obj):
        pending_history = getattr(obj, cls._PENDING_IN_PROGRESS_HISTORY_ATTR, None)
        if (
            obj is None
            or not isinstance(pending_history, dict)
            or cls._has_persisted_in_progress_state(obj)
        ):
            return False

        current_snapshot = obj._capture_snapshot()
        had_history_date = hasattr(obj, "_history_date")
        previous_history_date = getattr(obj, "_history_date", None)
        had_history_user = hasattr(obj, "_history_user")
        previous_history_user = getattr(obj, "_history_user", None)
        had_history_change_reason = hasattr(obj, "_history_change_reason")
        previous_history_change_reason = getattr(obj, "_history_change_reason", None)

        try:
            obj._restore_from_snapshot(pending_history.get("snapshot", {}))
            obj.is_calculated = cls.IN_PROGRESS
            obj._history_date = pending_history.get("history_date", timezone.now())
            obj._history_user = pending_history.get("history_user")
            obj._history_change_reason = pending_history.get("history_change_reason", "")
            obj.save(skip_hooks=True)
            return True
        finally:
            # Clean up the pending attr so it cannot be applied a second time
            # (e.g. if persist_error_state is called twice for the same obj).
            if hasattr(obj, cls._PENDING_IN_PROGRESS_HISTORY_ATTR):
                delattr(obj, cls._PENDING_IN_PROGRESS_HISTORY_ATTR)

            obj._restore_from_snapshot(current_snapshot)

            if had_history_date:
                obj._history_date = previous_history_date
            elif hasattr(obj, "_history_date"):
                delattr(obj, "_history_date")

            if had_history_user:
                obj._history_user = previous_history_user
            elif hasattr(obj, "_history_user"):
                delattr(obj, "_history_user")

            if had_history_change_reason:
                obj._history_change_reason = previous_history_change_reason
            elif hasattr(obj, "_history_change_reason"):
                delattr(obj, "_history_change_reason")

    @classmethod
    def _register_terminal_state_persistence(cls, obj):
        if obj is None:
            return

        if transaction.get_connection().in_atomic_block:
            transaction.on_commit(lambda: cls._mark_terminal_state_persisted(obj))
            return

        cls._mark_terminal_state_persisted(obj)

    @classmethod
    def _clear_terminal_state_persistence(cls, obj):
        if obj is not None and hasattr(obj, cls._TERMINAL_STATE_PERSISTENCE_ATTR):
            delattr(obj, cls._TERMINAL_STATE_PERSISTENCE_ATTR)

    @classmethod
    def _has_persisted_terminal_state(cls, obj, state=None):
        persisted_state = getattr(obj, cls._TERMINAL_STATE_PERSISTENCE_ATTR, None)
        if state is None:
            return persisted_state is not None
        return persisted_state == state

    @staticmethod
    def persist_error_state(calc_objs):
        persisted_objects = []
        seen_objects = set()

        for obj in CalculationModelException._ensure_list(calc_objs):
            if obj is None:
                continue

            object_identity = CalculationModel._terminal_state_identity(obj)
            if object_identity in seen_objects:
                continue
            seen_objects.add(object_identity)

            if CalculationModel._has_persisted_terminal_state(
                obj,
                CalculationModel.ERROR,
            ):
                persisted_objects.append(obj)
                continue

            try:
                CalculationModel._restore_missing_in_progress_history(obj)
                obj.is_calculated = CalculationModel.ERROR
                obj.save(skip_hooks=True)
                CalculationModel._register_terminal_state_persistence(obj)
                persisted_objects.append(obj)
            except Exception:
                logger.error(
                    f"Failed to persist calculation ERROR state for {obj}",
                    exc_info=True,
                )

        return persisted_objects

    @staticmethod
    def build_exception_chain(exception, current_obj=None):
        calc_obj, exception_details, stack_trace = find_exception_artifacts(exception)

        if current_obj is not None and (not calc_obj or calc_obj[-1] is not current_obj):
            calc_obj = calc_obj + [current_obj]

        return calc_obj, exception_details, stack_trace


    def update(self):
        """
        Placeholder for update logic. Subclasses should override this method
        if they provide 'update' functionality.
        """
        raise NotImplementedError(
            f"{self.__class__.__name__} must override the 'update' or 'calculate' method."
        )

    def calculate(self):
        """
        Placeholder for calculation logic. Subclasses should override this method
        if they provide 'calculate' functionality.
        """
        raise NotImplementedError(
            f"{self.__class__.__name__} must override the 'update' or 'calculate' method."
        )




    @hook(BEFORE_SAVE)
    def before_save(self):
        pass

        # Check if it's a new instance
        if self._state.adding:
            self.is_creation = True
        else:
            self.is_creation = False

    def lex_func(self):
        """
        Dynamically selects the overridden calculation method ('calculate' or 'update').

        A subclass may override ``calculate`` or ``update`` with a plain method
        **or** with a ``@lex_shared_task``-decorated method.  In the latter case
        the attribute on the instance is an ``EnhancedBoundTaskMethod`` proxy
        (from celery_tasks.py) which does **not** expose ``__func__``.

        Detection strategy:
        1. If the attribute has ``__func__``, compare it to the base-class
           implementation (the classic plain-method path).
        2. If ``__func__`` is missing, the attribute is a task proxy — which
           means the subclass *did* override the method with a Celery task.
        """
        calculate_attr = self.calculate
        update_attr = self.update

        # Check 'calculate' first
        func = getattr(calculate_attr, '__func__', None)
        if func is not None:
            # Plain method — check if it was overridden
            if func is not CalculationModel.calculate:
                return calculate_attr
        else:
            # No __func__ → task-wrapped descriptor → subclass overrode it
            return calculate_attr

        # Then check 'update'
        func = getattr(update_attr, '__func__', None)
        if func is not None:
            if func is not CalculationModel.update:
                return update_attr
        else:
            return update_attr

        # Fallback will raise NotImplementedError when called
        return self.calculate




    def should_use_celery(self) -> bool:
        """
        Determine if calculation should use Celery based on configuration and availability.

        Returns:
            bool: True if Celery should be used, False for synchronous execution
        """
        from lex.lex_app import settings

        # Check if Celery is enabled in setting
        if not os.getenv("CELERY_ACTIVE", "").lower() == 'true' or not hasattr(self.lex_func(), 'delay'):
            return False

        # Check if Celery is available by trying to import and test connection
        try:
            from celery import current_app
            # Test if we can access Celery (this will fail if broker is down)
            current_app.control.inspect()
            return True
        except Exception:
            # Celery not available, fall back to synchronous execution
            return False

    def dispatch_calculation_task(self):
        """
        Dispatch calculation to Celery worker using the calc_and_save task.

        Returns:
            AsyncResult: Celery task result object
        """

        # Extract only the calculation_id from context to avoid pickling issues
        context = operation_context.get()
        request_obj = context['request_obj'] or {}
        request_obj_extracted = OperationContext.extract_info_request(request_obj)
        new_context = {**context, "request_obj": request_obj_extracted}

        # For backward compatibility
        func = self.lex_func()

        # Dispatch single model calculation to Celery with calculation_id
        from lex.audit_logging.utils.ModelContext import _model_context
        model_context = deepcopy(_model_context.get()['model_context'])

        # Dispatch the task
        from lex.lex_app.celery_tasks import WaitForTasks
        task_result = func.delay(context=new_context, model_context=model_context)

        # Register with WaitForTasks context if one exists
        from lex.lex_app.celery_tasks import register_task_with_context
        return register_task_with_context(task_result)

    def execute_calculation_sync(self):
        """
        Execute calculation synchronously in the current thread.
        """
        from lex.core.signals.CalculationSignals import update_calculation_status

        self._clear_terminal_state_persistence(self)
        func = self.lex_func()
        exception_details = None
        stack_trace = None
        try:
            if hasattr(self, "is_atomic") and not self.is_atomic:
                func()
                self.is_calculated = self.SUCCESS
            else:
                with transaction.atomic():
                    func()
                    self.is_calculated = self.SUCCESS

        except Exception as e:
            # Store error details
            fallback_stack_trace = traceback.format_exc()
            exception_details = resolve_exception_detail(e, fallback=str(e))
            stack_trace = resolve_exception_traceback(
                e,
                fallback=fallback_stack_trace,
            )
            error_details = f"{exception_details}\n\n{stack_trace}"
            self.is_calculated = self.ERROR

            if hasattr(self, 'calculation_error_message'):
                self.calculation_error_message = error_details
            elif hasattr(self, 'error_message'):
                self.error_message = error_details

            raise
        finally:
            try:
                context = ContextResolver.resolve()

                # Only perform cleanup if this is the ROOT process
                # If we are a child process, we leave our logs in cache so the parent/frontend
                # can still access them until the entire operation completes.
                is_root = False
                if context.root_record and context.current_record:
                    if context.root_record == context.current_record:
                        is_root = True
                elif context.current_record and not context.parent_record:
                    # If no explicit root but also no parent, we are effectively root
                    is_root = True

                if is_root:
                    calc_id = context.calculation_id
                    # If we are root, we can clean up everything for this calculation ID
                    # or just our specific key. Cleaning everything ensures no orphaned child keys.
                    cleanup_result = CacheManager.cleanup_calculation(calculation_id=calc_id)

                    if cleanup_result.success:
                        logger.info(f"Root process cleanup successful for calculation {calc_id}")
                    else:
                        logger.warning(
                            f"Root process cleanup had errors for calculation {calc_id}: {cleanup_result.errors}")
                else:
                    logger.debug(f"Skipping cache cleanup for child process {context.current_record}")

            except Exception as cleanup_error:
                logger.error(f"Cache cleanup failed after calculation hook: {str(cleanup_error)}")

            try:
                self.save(skip_hooks=True)
                self._register_terminal_state_persistence(self)
                update_calculation_status(
                    self,
                    exception_details=exception_details,
                    stack_trace=stack_trace,
                )
            except Exception as status_update_error:
                logger.error(
                    "Failed to persist/notify terminal calculation state for %s: %s",
                    self,
                    status_update_error,
                    exc_info=True,
                )
            if self.is_calculated != self.ERROR:
                try:
                    from lex.audit_logging.utils.calculation_audit import (
                        ensure_terminal_calculation_audit,
                    )

                    ensure_terminal_calculation_audit(
                        self,
                        audit_status="success",
                        error_message=exception_details,
                        stack_trace=stack_trace,
                    )
                except Exception as audit_error:
                    logger.error(
                        "Failed to finalize terminal audit log for %s: %s",
                        self,
                        audit_error,
                        exc_info=True,
                    )

    @hook(AFTER_UPDATE, condition=WhenFieldValueIs("is_calculated", IN_PROGRESS))
    @hook(AFTER_CREATE, condition=WhenFieldValueIs("is_calculated", IN_PROGRESS))
    def calculate_hook(self):
        """
        Enhanced calculation hook with Celery integration.

        Dispatches calculations to Celery workers when celery_active=True and Celery
        is available, otherwise falls back to synchronous execution. Proper status
        management ensures IN_PROGRESS -> SUCCESS/ERROR transitions.
        """
        # When CalculationModel.save() defers the hook, return immediately.
        # The save method will call calculate_hook() directly after the
        # IN_PROGRESS state is committed.
        if getattr(self, "_defer_calculate_hook", False):
            return

        from lex.core.signals.CalculationSignals import update_calculation_status
        from lex.core.signals.ActiveCalculationStateStore import ActiveCalculationStateStore
        import logging
        logger = logging.getLogger(__name__)

        # Prevent recursive execution when internal save paths (e.g. FileField.save)
        # call self.save() while this hook is already processing the same instance.
        if getattr(self, "_calculation_hook_in_progress", False):
            logger.debug(f"Skipping re-entrant calculate_hook for {self}")
            return

        self._calculation_hook_in_progress = True
        self._clear_terminal_state_persistence(self)
        self._queue_missing_in_progress_history(self)
        task_dispatched = False
        try:
            # ── Ensure this record is registered in the cache store ──
            # For child calculations (triggered from within a parent's
            # calculate()), the API-level early registration in One.py didn't
            # run.  Register here so the reconciliation snapshot includes
            # child records and page-refresh shows the correct spinner.
            #
            # IMPORTANT: Child calculations MUST use the parent's
            # calculation_id from operation_context to preserve the
            # parent→child hierarchy that the AuditLog/ContextResolver
            # system depends on.  We must NOT generate synthetic IDs.
            record_id = f"{self._meta.model_name}_{self.pk}"
            existing_calc_id = ActiveCalculationStateStore.get_calculation_id(record_id)

            if not existing_calc_id:
                from lex.api.utils import operation_context as _op_ctx
                try:
                    calc_id = _op_ctx.get().get("calculation_id") or ""
                except Exception:
                    calc_id = ""

                ActiveCalculationStateStore.mark_in_progress(
                    record_id=record_id,
                    calculation_id=calc_id,
                    record=str(self),
                    model_label=self._meta.label_lower,
                    record_pk=self.pk,
                )

            try:
                # Broadcast IN_PROGRESS so all WebSocket subscribers see
                # this calculation as active immediately.
                update_calculation_status(self)
            except Exception as status_error:
                logger.warning(
                    f"Failed to publish IN_PROGRESS status for {self}: {status_error}",
                    exc_info=True,
                )

            if self.should_use_celery():
                # Dispatch to Celery worker
                logger.info(f"Dispatching calculation for {self} to Celery worker")

                from lex.lex_app.celery_tasks import (
                    FireAndForget,
                    WaitForTasks,
                    is_celery_worker_process,
                )

                has_explicit_async_context = (
                    FireAndForget.get_current_context() is not None
                    or WaitForTasks.get_current_context() is not None
                )

                if has_explicit_async_context:
                    task_result = self.dispatch_calculation_task()
                    task_dispatched = True
                elif is_celery_worker_process():
                    logger.info(
                        "Executing nested calculation for %s synchronously inside Celery worker",
                        self,
                    )
                    self.execute_calculation_sync()
                else:
                    with WaitForTasks():
                        task_result = self.dispatch_calculation_task()
                        task_dispatched = True

                # Note: Status will be updated by CallbackTask.on_success/on_failure
                # Model remains in IN_PROGRESS state until task completes
                if task_dispatched:
                    logger.info(f"Calculation task {task_result.id} dispatched for {self}")

            else:
                # Execute synchronously as fallback
                logger.info(f"Executing calculation for {self} synchronously (Celery not available)")
                self.execute_calculation_sync()

        except Exception as e:
            # Nested hook invocations may already normalize failures into
            # CalculationModelException. Re-raise as-is to avoid noisy
            # # re-wrapping and duplicated error handling.


            # Handle any errors in task dispatch or synchronous execution
            logger.error(f"Calculation failed for {self}: {e}", exc_info=True)
            status_was_error = self.is_calculated == self.ERROR
            self.is_calculated = self.ERROR

            # Store error message if the model has an error_message field
            stack_trace = traceback.format_exc()
            exception_details = resolve_exception_detail(e, fallback=str(e))
            # Clean up cache and save error state
            try:
                context = ContextResolver.resolve()

                # Only perform cleanup if this is the ROOT process
                is_root = False
                if context.root_record and context.current_record:
                    if context.root_record == context.current_record:
                        is_root = True
                elif context.current_record and not context.parent_record:
                    is_root = True

                if is_root:
                    calc_id = context.calculation_id
                    # Clean up all keys associated with this calculation ID
                    cleanup_result = CacheManager.cleanup_calculation(calculation_id=calc_id)

                    if cleanup_result.success:
                        logger.info(f"Root process cleanup successful after calculation hook for calculation {calc_id}")
                    else:
                        logger.warning(f"Root process cleanup had errors after calculation hook for calculation {calc_id}: {cleanup_result.errors} ")
                else:
                    logger.debug(f"Skipping cache cleanup for child process {context.current_record}")

            except Exception as cleanup_error:
                logger.error(f"Cache cleanup failed after calculation hook: {str(cleanup_error)}")

            calc_obj, exception_chain, stack_trace_chain = self.build_exception_chain(
                e,
                current_obj=self,
            )
            full_exception_chain = exception_chain + [exception_details]
            full_stack_trace_chain = stack_trace_chain + [stack_trace]
            preferred_exception_detail = (
                select_preferred_exception_detail(full_exception_chain)
                or exception_details
            )
            preferred_stack_trace = (
                select_preferred_stack_trace(full_stack_trace_chain)
                or stack_trace
            )

            # Persist ERROR state and notify websocket clients.
            # Always save self to ERROR — even when execute_calculation_sync
            # already did — to cover cases where the error originated in
            # calculate_hook itself (e.g. dispatch failure) or where the
            # parent's own calculate() raised directly.
            error_state_already_persisted = self._has_persisted_terminal_state(
                self,
                self.ERROR,
            )
            try:
                if not error_state_already_persisted:
                    self.save(skip_hooks=True)
                    self._register_terminal_state_persistence(self)
                if not status_was_error:
                    update_calculation_status(
                        self,
                        exception_details=preferred_exception_detail,
                        stack_trace=preferred_stack_trace,
                    )
            except Exception as status_update_error:
                logger.error(
                    f"Failed to persist/notify ERROR state for {self}: {status_update_error}",
                    exc_info=True,
                )
            self._pending_terminal_audit = {
                "audit_status": "failure",
                "error_message": preferred_exception_detail,
                "stack_trace": preferred_stack_trace,
            }
            calc_obj_to_persist = calc_obj[:-1] if calc_obj and calc_obj[-1] is self else calc_obj
            self.persist_error_state(calc_obj_to_persist)
            raise CalculationModelException(
                calc_obj=calc_obj,
                exception_details=full_exception_chain,
                stack_trace=full_stack_trace_chain,
            ) from e
        finally:
            # Ensure the guard does not leak across future independent saves.
            if hasattr(self, "_calculation_hook_in_progress"):
                delattr(self, "_calculation_hook_in_progress")
