import os
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
from lex.core.models.base import LexModel
from lex.api.utils import operation_context, OperationContext
from lex.audit_logging.utils.cache_manager import CacheManager
from lex.audit_logging.utils.context_resolver import ContextResolver

logger = logging.getLogger(__name__)


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
        It compares the function object of the instance's method with the function
        object on the base class to detect an override.
        """
        # CORRECT: Compare the bound method's function with the base class's function
        if self.calculate.__func__ is not CalculationModel.calculate:
            return self.calculate
        # CORRECT: Do the same for the 'update' method
        elif self.update.__func__ is not CalculationModel.update:
            return self.update

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
        if not os.getenv("CELERY_ACTIVE", None) == 'true' or not hasattr(self.lex_func(), 'delay'):
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
        from lex.audit_logging.utils.model_context import model_logging_context
        model_context = deepcopy(model_logging_context.get()['model_context'])

        # Dispatch the task
        task_result = func.delay(context=new_context, model_context=model_context)

        # Register with RunInCelery context if one exists
        from lex.lex_app.celery_tasks import register_task_with_context
        return register_task_with_context(task_result)

    def execute_calculation_sync(self):
        """
        Execute calculation synchronously in the current thread.
        """
        from lex.core.signals.calculation_signals import update_calculation_status

        func = self.lex_func()
        try:
            if hasattr(self, "is_atomic") and not self.is_atomic:
                func()
                self.is_calculated = self.SUCCESS
            else:
                with transaction.atomic():
                    func()
                    self.is_calculated = self.SUCCESS

        except Exception as e:
            self.is_calculated = self.ERROR
            raise e
        finally:
            # Clean up cache if context is available
            try:
                context = ContextResolver.resolve()
                calc_id = context.calculation_id
                key_to_clean = CacheManager.build_cache_key(context.current_record, context.calculation_id)
                cleanup_result = CacheManager.cleanup_calculation(specific_keys=[key_to_clean])

                if cleanup_result.success:
                    logger.info(f"Cache cleanup successful after calculation hook for calculation {calc_id}")
                else:
                    logger.warning(f"Cache cleanup had errors after calculation hook for calculation {calc_id}: {cleanup_result.errors}")
            except Exception as cleanup_error:
                logger.error(f"Cache cleanup failed after calculation hook: {str(cleanup_error)}")

            self.save(skip_hooks=True)
            update_calculation_status(self)

    @hook(AFTER_UPDATE, condition=WhenFieldValueIs("is_calculated", IN_PROGRESS))
    @hook(AFTER_CREATE, condition=WhenFieldValueIs("is_calculated", IN_PROGRESS))
    def calculate_hook(self):
        """
        Enhanced calculation hook with Celery integration.

        Dispatches calculations to Celery workers when celery_active=True and Celery
        is available, otherwise falls back to synchronous execution. Proper status
        management ensures IN_PROGRESS -> SUCCESS/ERROR transitions.
        """
        from lex.core.signals.calculation_signals import update_calculation_status
        import logging

        logger = logging.getLogger(__name__)

        try:
            if self.should_use_celery():
                # Dispatch to Celery worker
                logger.info(f"Dispatching calculation for {self} to Celery worker")

                task_result = None
                from lex.lex_app.celery_tasks import RunInCelery
                with RunInCelery():
                    task_result = self.dispatch_calculation_task()

                # Note: Status will be updated by CallbackTask.on_success/on_failure
                # Model remains in IN_PROGRESS state until task completes
                logger.info(f"Calculation task {task_result.id} dispatched for {self}")

            else:
                # Execute synchronously as fallback
                logger.info(f"Executing calculation for {self} synchronously (Celery not available)")
                self.execute_calculation_sync()

        except Exception as e:
            # Handle any errors in task dispatch or synchronous execution
            logger.error(f"Calculation failed for {self}: {e}", exc_info=True)
            self.is_calculated = self.ERROR

            # Store error message if the model has an error_message field
            if hasattr(self, 'error_message'):
                self.error_message = str(e)

            # Clean up cache and save error state
            try:
                context = ContextResolver.resolve()
                calc_id = context.calculation_id
                key_to_clean = CacheManager.build_cache_key(context.current_record, context.calculation_id)
                cleanup_result = CacheManager.cleanup_calculation(specific_keys=[key_to_clean])

                if cleanup_result.success:
                    logger.info(f"Cache cleanup successful after calculation hook for calculation {calc_id}")
                else:
                    logger.warning(f"Cache cleanup had errors after calculation hook for calculation {calc_id}: {cleanup_result.errors}")
            except Exception as cleanup_error:
                logger.error(f"Cache cleanup failed after calculation hook: {str(cleanup_error)}")

            self.save(skip_hooks=True)
            update_calculation_status(self)
            raise e
