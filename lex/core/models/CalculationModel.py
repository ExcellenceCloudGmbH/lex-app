import os
import traceback
from abc import abstractmethod
import logging
from copy import deepcopy

from django.db import models
from django.db import transaction
from django_lifecycle import (
    hook,
    AFTER_UPDATE,
    AFTER_CREATE,
    BEFORE_SAVE,
)
from django_lifecycle.conditions import WhenFieldValueIs
from rest_framework.exceptions import APIException

from lex.core.models.LexModel import LexModel
from lex.api.utils import operation_context, OperationContext
from lex.audit_logging.utils.CacheManager import CacheManager
from lex.audit_logging.utils.ContextResolver import ContextResolver

logger = logging.getLogger(__name__)

class CalculationModelException(APIException):
        @staticmethod
        def _ensure_list(value):
            if value is None:
                return []
            if isinstance(value, list):
                return value
            if isinstance(value, tuple):
                return list(value)
            return [value]

        def __init__(self, *args, **kwargs):
            super().__init__(*args)
            self.calc_obj = self._ensure_list(kwargs.get("calc_obj", None))
            self.exception_details = self._ensure_list(kwargs.get("exception_details", None))
            self.stack_trace = self._ensure_list(kwargs.get("stack_trace", None))


class CalculationModel(LexModel):

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

    @staticmethod
    def persist_error_state(calc_objs):
        persisted_objects = []
        seen_objects = set()

        for obj in CalculationModelException._ensure_list(calc_objs):
            if obj is None:
                continue

            object_identity = id(obj)
            if object_identity in seen_objects:
                continue
            seen_objects.add(object_identity)

            try:
                obj.is_calculated = CalculationModel.ERROR
                obj.save(skip_hooks=True)
                persisted_objects.append(obj)
            except Exception:
                logger.error(
                    f"Failed to persist calculation ERROR state for {obj}",
                    exc_info=True,
                )

        return persisted_objects

    @staticmethod
    def build_exception_chain(exception, current_obj=None):
        calc_obj = CalculationModelException._ensure_list(
            getattr(exception, "calc_obj", None)
        )
        exception_details = CalculationModelException._ensure_list(
            getattr(exception, "exception_details", None)
        )
        stack_trace = CalculationModelException._ensure_list(
            getattr(exception, "stack_trace", None)
        )

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
        with WaitForTasks():
            task_result = func.delay(context=new_context, model_context=model_context)

        # Register with WaitForTasks context if one exists
        from lex.lex_app.celery_tasks import register_task_with_context
        return register_task_with_context(task_result)

    def execute_calculation_sync(self):
        """
        Execute calculation synchronously in the current thread.
        """
        from lex.core.signals.CalculationSignals import update_calculation_status

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
            error_details = f"{str(e)}\n\n{traceback.format_exc()}"
            exception_details = str(e)
            stack_trace = traceback.format_exc()
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

            self.save(skip_hooks=True)
            update_calculation_status(
                self,
                exception_details=exception_details,
                stack_trace=stack_trace,
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

                # IMPORTANT: Do NOT use WaitForTasks() here (which block-waits
                # for the child task in __exit__).  We are still inside the
                # parent's save() transaction that set is_calculated=IN_PROGRESS.
                # Blocking here while holding that DB lock causes a deadlock
                # when the child task tries to write back to the same row.
                #
                # Instead, dispatch fire-and-forget.  The child task reports
                # its own SUCCESS/ERROR via CallbackTask.on_success/on_failure.
                from lex.lex_app.celery_tasks import WaitForTasks
                with WaitForTasks():
                    task_result = self.dispatch_calculation_task()

                    # Note: Status will be updated by CallbackTask.on_success/on_failure
                # Model remains in IN_PROGRESS state until task completes
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
            stack_trace = f"{traceback.format_exc()}"
            exception_details = str(e)
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

            # Persist ERROR state and notify websocket clients.
            # Always save self to ERROR — even when execute_calculation_sync
            # already did — to cover cases where the error originated in
            # calculate_hook itself (e.g. dispatch failure) or where the
            # parent's own calculate() raised directly.
            try:
                self.save(skip_hooks=True)
                if not status_was_error:
                    update_calculation_status(
                        self,
                        exception_details=exception_details,
                        stack_trace=stack_trace,
                    )
            except Exception as status_update_error:
                logger.error(
                    f"Failed to persist/notify ERROR state for {self}: {status_update_error}",
                    exc_info=True,
                )


            calc_obj, exception_chain, stack_trace_chain = self.build_exception_chain(
                e,
                current_obj=self,
            )
            calc_obj_to_persist = calc_obj[:-1] if calc_obj and calc_obj[-1] is self else calc_obj
            self.persist_error_state(calc_obj_to_persist)
            raise CalculationModelException(
                calc_obj=calc_obj,
                exception_details=exception_chain + [exception_details],
                stack_trace=stack_trace_chain + [stack_trace],
            )
        finally:
            # Ensure the guard does not leak across future independent saves.
            if hasattr(self, "_calculation_hook_in_progress"):
                delattr(self, "_calculation_hook_in_progress")
