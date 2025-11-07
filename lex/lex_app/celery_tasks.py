# Enhanced Celery Task Infrastructure with UnblockCelery Context Manager

"""
Celery task infrastructure with custom decorators and callback handling.

This module provides enhanced Celery task integration with proper lifecycle
management, status tracking, and error handling for calculation models.

NEW: UnblockCelery context manager that reverses RunInCelery blocking behavior.
"""

import asyncio
import contextvars
import logging
import os
import traceback
from copy import deepcopy
from functools import wraps
from typing import Dict, Tuple, Optional, Set, List, Any
from uuid import uuid4

from celery import Task, shared_task
from celery.result import allow_join_result

from lex.audit_logging.utils.model_context import _model_context, model_logging_context
from celery.signals import task_postrun
from django.db import transaction
from django.db.models import Model

from lex.core.signals import update_calculation_status
from lex.api.utils import operation_context, OperationContext
from celery.app.control import Control
import threading
from asgiref.sync import sync_to_async

from lex.core.models.calculation_model import CalculationModel
from lex.authentication.utils.lex_authentication import LexAuthentication

logger = logging.getLogger(__name__)


class CeleryCalculationContext:
    """
    Context manager to set calculation_id for Celery tasks.

    This allows CalculationLog.log() to access the calculation_id
    even when running in a Celery worker process.
    """

    def __init__(self, context, model_context):
        self.context = context
        self.model_context = model_context

    def __enter__(self):
        if self.context:
            logger.warning(f"Operation Context {self.context}")
            new_context = deepcopy(self.context)
            new_context['calculation_id'] = self.context.get('calculation_id', None)
            new_context['operation_id'] = str(uuid4())
            new_context["celery_task"] = True
            new_context["task_name"] = "calc_and_save"
            operation_context.set(new_context)
        if self.model_context:
            _model_context.get()['model_context'] = self.model_context
            logger.warning(f"Operation Context {self.model_context}")
            logger.warning(f"Saved context {_model_context.get()['model_context']}")
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        pass


class CallbackTask(Task):
    """
    Enhanced Celery Task class with proper callback handling for calculation models.

    Provides automatic status updates and error handling for calculation tasks,
    with special handling for the initial_data_upload task.
    """

    def on_success(self, retval: Any, task_id: str, args: Tuple, kwargs: Dict) -> None:
        """Handle successful task completion."""
        try:
            if self.name == "initial_data_upload":
                return
            model_instances = self._extract_model_instances(args)
            for model_instance in model_instances:
                if isinstance(model_instance, CalculationModel):
                    self._update_model_status(
                        model_instance,
                        CalculationModel.SUCCESS,
                        task_id=task_id
                    )
        except Exception as callback_error:
            logger.error(
                f"Success callback failed for task {task_id}: {callback_error}",
                exc_info=True
            )

    def on_failure(self, exc: Exception, task_id: str, args: Tuple, kwargs: Dict, einfo: Any) -> None:
        """Handle task failure."""
        try:
            if self.name == "initial_data_upload":
                return
            model_instances = self._extract_model_instances(args)
            for model_instance in model_instances:
                if isinstance(model_instance, CalculationModel):
                    self._update_model_status(
                        model_instance,
                        CalculationModel.ERROR,
                        error_message=str(exc),
                        task_id=task_id
                    )
        except Exception as callback_error:
            logger.error(
                f"Failure callback failed for task {task_id}: {callback_error}",
                exc_info=True
            )

    def _extract_model_instances(self, args: Tuple) -> List[Model]:
        """Extract model instances from task arguments."""
        model_instances = []
        if args:
            first_arg = args[0]
            if isinstance(first_arg, Model):
                model_instances = [first_arg]
            elif isinstance(first_arg, (list, tuple)):
                model_instances = [item for item in first_arg if isinstance(item, Model)]
        return model_instances

    def _update_model_status(
            self,
            model_instance: CalculationModel,
            status: str,
            error_message: Optional[str] = None,
            task_id: Optional[str] = None
    ) -> None:
        """Update model status and notify connected systems."""
        try:
            with transaction.atomic():
                model_instance.is_calculated = status
                if error_message and hasattr(model_instance, 'error_message'):
                    model_instance.error_message = error_message
                if task_id and hasattr(model_instance, 'task_id'):
                    model_instance.task_id = task_id
                model_instance.save(skip_hooks=True)
                logger.warning(f"Updating status for {model_instance.__class__.__name__} task {task_id}")
                update_calculation_status(model_instance)
        except Exception as update_error:
            logger.error(
                f"Failed to update model status for {model_instance}: {update_error}",
                exc_info=True
            )


class UnblockCelery:
    """
    Context manager that disables the blocking mechanism of RunInCelery,
    allowing tasks to be dispatched to Celery workers even when inside a RunInCelery context.

    This is the reverse of RunInCelery - it forces asynchronous execution by overriding
    the blocking behavior of any active RunInCelery contexts.

    Usage:
        # Inside a RunInCelery context, tasks would normally be blocked (sync)
        with RunInCelery():
            # This would run synchronously normally
            my_task(data)

            # But with UnblockCelery, we can force async execution
            with UnblockCelery():
                my_task(data)  # This will be dispatched to Celery

            # Back to synchronous execution
            my_task(data)
    """

    def __init__(self, force_tasks: Optional[Set[str]] = None,
                 exclude_tasks: Optional[Set[str]] = None):
        """
        Initialize the UnblockCelery context manager.

        Args:
            force_tasks: Set of task names to force dispatch (if None, force all tasks)
            exclude_tasks: Set of task names to keep following RunInCelery rules
        """
        self.force_tasks = force_tasks
        self.exclude_tasks = exclude_tasks or set()
        self.dispatched_results: List[Any] = []

    def __enter__(self):
        # Store the unblock context
        unblock_context = unblock_tasks_context.get()
        unblock_context['unblock_context_stack'].append(self)
        unblock_tasks_context.set(unblock_context)
        logger.debug(f"UnblockCelery context entered. Force tasks: {self.force_tasks}, "
                     f"Exclude tasks: {self.exclude_tasks}")
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        # Remove this context from storage
        unblock_context = unblock_tasks_context.get()
        if unblock_context['unblock_context_stack']:
            unblock_context['unblock_context_stack'].pop()

        # Wait for completion of forced tasks
        # self.wait_for_completion()
        logger.debug(f"UnblockCelery context exited. Dispatched {len(self.dispatched_results)} tasks.")

    def should_force_dispatch(self, task_name: str) -> bool:
        """Determine if a task should be forced to dispatch despite RunInCelery context."""
        if task_name in self.exclude_tasks:
            return False
        if self.force_tasks is None:
            return True
        return task_name in self.force_tasks

    def add_dispatched_result(self, result):
        """Add a dispatched task result to track for completion."""
        self.dispatched_results.append(result)

    def wait_for_completion(self):
        """Wait for all dispatched tasks to complete."""
        if not self.dispatched_results:
            return

        logger.info(f"UnblockCelery: Waiting for {len(self.dispatched_results)} forced tasks to complete")

        for result in self.dispatched_results:
            try:
                with allow_join_result():
                    result.get()
                logger.debug(f"UnblockCelery: Task {result.id} completed successfully")
            except Exception as e:
                logger.error(f"UnblockCelery: Task {result.id} failed: {e}")
                raise
        logger.info("UnblockCelery: All forced tasks completed")

    @classmethod
    def get_current_context(cls) -> Optional['UnblockCelery']:
        """Get the current active UnblockCelery context."""
        unblock_context = unblock_tasks_context.get()
        if unblock_context['unblock_context_stack']:
            return unblock_context['unblock_context_stack'][-1]
        return None


class RunInCelery:
    """
    Context manager that selectively dispatches lex_shared_task decorated functions
    to Celery workers while keeping others synchronous.

    Now checks for UnblockCelery contexts that can override its blocking behavior.
    """

    def __init__(self, include_tasks: Optional[Set[str]] = None,
                 exclude_tasks: Optional[Set[str]] = None):
        """
        Initialize the context manager.

        Args:
            include_tasks: Set of task names to dispatch (if None, dispatch all lex_shared_tasks)
            exclude_tasks: Set of task names to keep synchronous (overrides include_tasks)
        """
        self.include_tasks = include_tasks
        self.exclude_tasks = exclude_tasks or set()
        self.dispatched_results: List[Any] = []
        self.block = True

    def __enter__(self):
        tasks_context.get().get('task_context_stack').append(self)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if tasks_context.get().get('task_context_stack'):
            tasks_context.get().get('task_context_stack').pop()
        self.wait_for_completion()

    def should_dispatch(self, task_name: str) -> bool:
        """Determine if a task should be dispatched based on include/exclude rules."""
        if task_name in self.exclude_tasks:
            return False
        if self.include_tasks is None:
            return True
        return task_name in self.include_tasks

    def add_dispatched_result(self, result):
        """Add a dispatched task result to track for completion."""
        self.dispatched_results.append(result)

    def wait_for_completion(self):
        """Wait for all dispatched tasks to complete."""
        logger.info(f"Waiting for {len(self.dispatched_results)} dispatched tasks to complete")
        context = UnblockCelery.get_current_context()
        if context:
            return
        for result in self.dispatched_results:
            try:
                with allow_join_result():
                    result.get()
                logger.debug(f"Task {result.id} completed successfully")
            except Exception as e:
                logger.error(f"Task {result.id} failed: {e}")
                raise
        logger.info("All dispatched tasks completed")

    @classmethod
    def get_current_context(cls) -> Optional['RunInCelery']:
        """Get the current active context from thread-local storage."""
        if tasks_context.get().get('task_context_stack'):
            return tasks_context.get().get('task_context_stack')[-1]
        return None


# Context variables
tasks_context: contextvars.ContextVar[Dict[str, Any]] = contextvars.ContextVar(
    'tasks_context',
    default={'task_context_stack': []}
)

unblock_tasks_context: contextvars.ContextVar[Dict[str, Any]] = contextvars.ContextVar(
    'unblock_tasks_context',
    default={'unblock_context_stack': []}
)


class EnhancedBoundTaskMethod:
    """
    Enhanced version that respects both RunInCelery and UnblockCelery contexts.

    Priority hierarchy:
    1. UnblockCelery context (forces async) - HIGHEST PRIORITY
    2. RunInCelery context (forces sync unless overridden)
    3. Default behavior (sync)
    """

    def __init__(self, instance, task):
        self.instance = instance
        self.task = task

    def __call__(self, *args, **kwargs):
        """Handles direct calls - checks contexts to decide sync vs async execution."""
        task_name = getattr(self.task, 'name', self.task.__name__)

        # PRIORITY 1: Check UnblockCelery context first (highest priority)
        unblock_context = UnblockCelery.get_current_context()
        if unblock_context and unblock_context.should_force_dispatch(task_name):
            logger.debug(f"UnblockCelery forcing task {task_name} to Celery (overriding RunInCelery)")
            result = self.task.delay(self.instance, *args, **kwargs)
            unblock_context.add_dispatched_result(result)
            return result

        # PRIORITY 2: Check RunInCelery context
        run_context = RunInCelery.get_current_context()
        if run_context is None:
            # No context - default behavior (sync)
            logger.debug(f"No context: Running task {task_name} synchronously")
            return self.task(self.instance, *args, **kwargs)

        # Check if UnblockCelery excludes this task from forced dispatch
        if unblock_context and task_name in unblock_context.exclude_tasks:
            logger.debug(f"UnblockCelery excluding task {task_name} from forced dispatch")
            return self.task(self.instance, *args, **kwargs)

        # Follow RunInCelery rules
        if run_context.should_dispatch(task_name):
            logger.debug(f"RunInCelery dispatching task {task_name} to Celery")
            result = self.task.delay(self.instance, *args, **kwargs)
            run_context.add_dispatched_result(result)
            return result
        else:
            logger.debug(f"RunInCelery running task {task_name} synchronously")
            return self.task(self.instance, *args, **kwargs)

    def delay(self, *args, **kwargs):
        """Always handles asynchronous .delay() calls."""
        return self.task.delay(self.instance, *args, **kwargs)

    def __getattr__(self, name):
        """Proxy any other attributes to the underlying task."""
        return getattr(self.task, name)


class EnhancedTaskMethodDescriptor:
    """
    Enhanced version that uses EnhancedBoundTaskMethod with UnblockCelery support.
    """

    def __init__(self, task):
        self.task = task

    def __get__(self, instance, owner):
        if instance is None:
            return self
        return EnhancedBoundTaskMethod(instance, self.task)

    def __call__(self, *args, **kwargs):
        """Handle direct calls on class-level access."""
        task_name = getattr(self.task, 'name', self.task.__name__)

        # Check UnblockCelery context first
        unblock_context = UnblockCelery.get_current_context()
        if unblock_context and unblock_context.should_force_dispatch(task_name):
            logger.debug(f"UnblockCelery forcing task {task_name} to Celery")
            result = self.task.delay(*args, **kwargs)
            unblock_context.add_dispatched_result(result)
            return result

        # Check RunInCelery context
        run_context = RunInCelery.get_current_context()
        if run_context is None:
            return self.task(*args, **kwargs)

        # Check UnblockCelery exclusions
        if unblock_context and task_name in unblock_context.exclude_tasks:
            return self.task(*args, **kwargs)

        if run_context.should_dispatch(task_name):
            logger.debug(f"RunInCelery dispatching task {task_name} to Celery")
            result = self.task.delay(*args, **kwargs)
            run_context.add_dispatched_result(result)
            return result
        else:
            logger.debug(f"RunInCelery running task {task_name} synchronously")
            return self.task(*args, **kwargs)

    def __getattr__(self, name):
        """Proxy attribute access to the underlying task."""
        return getattr(self.task, name)


def lex_shared_task(_func=None, **task_opts):
    """
    Enhanced version of lex_shared_task that works with both RunInCelery and UnblockCelery contexts.
    """

    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            try:
                model_context = kwargs.get("model_context", None)
                context = kwargs.get("context", None)
                if context:
                    kwargs.pop('context')
                if model_context:
                    kwargs.pop('model_context')

                with CeleryCalculationContext(context, model_context):
                    result = func(*args, **kwargs)

                return result, args
            except Exception as e:
                logger.error(
                    f"Task {func.__name__} failed with args {args}, kwargs {kwargs}: {e}",
                    exc_info=True
                )
                raise

        options = {
            'base': CallbackTask,
            'bind': False,
        }
        options.update(task_opts)

        celery_task = shared_task(**options)(wrapper)
        return EnhancedTaskMethodDescriptor(celery_task)

    if _func is not None and callable(_func):
        return decorator(_func)
    else:
        return decorator


# Utility functions
def is_in_unblock_context(task_name: str = None) -> bool:
    """
    Check if we're currently in an UnblockCelery context that would force dispatch.

    Args:
        task_name: Optional task name to check specifically

    Returns:
        bool: True if in UnblockCelery context and task should be forced to dispatch
    """
    unblock_context = UnblockCelery.get_current_context()
    if not unblock_context:
        return False

    if task_name is None:
        return True

    return unblock_context.should_force_dispatch(task_name)


def register_task_with_context(task_result):
    """
    Register a task result with the current contexts.
    Checks UnblockCelery first, then RunInCelery.
    """
    # Register with UnblockCelery if present (highest priority)
    unblock_context = UnblockCelery.get_current_context()
    if unblock_context is not None:
        unblock_context.add_dispatched_result(task_result)
        return task_result

    # Otherwise register with RunInCelery if present
    run_context = RunInCelery.get_current_context()
    if run_context is not None:
        run_context.add_dispatched_result(task_result)

    return task_result


def respect_unblock_celery(func):
    """
    Decorator that makes a function respect UnblockCelery context.
    Useful for methods that dispatch Celery tasks internally.
    """

    @wraps(func)
    def wrapper(*args, **kwargs):
        if is_in_unblock_context(func.__name__):
            logger.debug(f"UnblockCelery context detected for {func.__name__}, forcing async behavior")
            kwargs['_force_async'] = True
        return func(*args, **kwargs)

    return wrapper


# Task definitions (your existing tasks)
@lex_shared_task(name="initial_data_upload")
def load_data(test, generic_app_models, audit_logging_enabled=None, initial_data_load=None):
    """Load data asynchronously if conditions are met."""
    if not initial_data_load:
        return
    from lex.lex_app.apps import should_load_data, _create_audit_logger_for_task

    audit_logger = _create_audit_logger_for_task(audit_logging_enabled)

    try:
        test.test_path = initial_data_load
        print("All models are empty: Starting Initial Data Fill")

        if audit_logger:
            print(f"Audit logging enabled for initial data upload")
        else:
            print("Audit logging disabled for initial data upload")

        # Handle both synchronous and asynchronous contexts
        if os.getenv("STORAGE_TYPE", "LEGACY") == "LEGACY":
            if is_running_in_celery():
                test.setUp(audit_logger)
            else:
                asyncio.run(sync_to_async(test.setUp)(audit_logger))
        else:
            if os.getenv("CELERY_ACTIVE") or is_running_in_celery():
                test.setUpCloudStorage(generic_app_models, audit_logger)
            else:
                asyncio.run(sync_to_async(test.setUpCloudStorage)(generic_app_models, audit_logger))

        # Finalize audit logging if enabled
        if audit_logger:
            try:
                summary = audit_logger.finalize_batch()
                print(f"Audit logging summary: {summary}")
            except Exception as e:
                print(f"Warning: Failed to finalize audit logging: {e}")

        print("Initial Data Fill completed Successfully")
    except Exception as e:
        print("Initial Data Fill aborted with Exception:")
        print(f"Error type: {type(e).__name__}")
        print(f"Error message: {str(e)}")
        traceback.print_exc()
        raise e


@lex_shared_task
def calc_and_save(models: List[Model], *args, **kwargs):
    """
    Calculates and saves a list of models with robust error handling and
    conflict resolution.
    """
    from django.db import IntegrityError
    summary = {
        "total_models": len(models),
        "processed_successfully": 0,
        "conflicts_resolved": 0,
        "errors": 0
    }

    for model in models:
        try:
            logger.info(f"Processing model {model}")
            model.save()
            model.lex_func()()
            logger.info(f"Finished calculating model {model}")
            model.save()
            summary["processed_successfully"] += 1

        except IntegrityError as integrity_error:
            try:
                logger.warning(f"Integrity error for {model}, attempting conflict resolution.")

                if hasattr(model, 'delete_models_with_same_defining_fields'):
                    existing_model = model.delete_models_with_same_defining_fields()

                    if existing_model != model and existing_model.pk:
                        model.pk = existing_model.pk
                        logger.info(f"Using existing model PK {existing_model.pk} for conflict resolution")
                    else:
                        model.pk = None
                        if hasattr(model, 'id'):
                            model.id = None

                    model.save()
                    logger.info(f"Successfully resolved conflict and saved model {model}")
                    summary["conflicts_resolved"] += 1
                    summary["processed_successfully"] += 1
                else:
                    raise integrity_error

            except Exception as resolution_error:
                logger.error(f"Conflict resolution FAILED for model {model}: {resolution_error}")
                summary["errors"] += 1
                raise resolution_error

        except Exception as e:
            logger.error(f"Unexpected error processing model {model}: {e}")
            summary["errors"] += 1
            raise e

    logger.info(f"Task finished. Summary: {summary}")
    return summary


def get_calc_and_save_task():
    """Get the calc_and_save task for use in other modules."""
    return calc_and_save


@lex_shared_task
def debug_context_in_celery():
    """Celery task to print context state inside worker."""
    print_context_state("INSIDE CELERY TASK")
    return "Context debug completed in Celery"


def is_running_in_celery():
    """Check if the current code is running in a Celery worker context."""
    try:
        import celery
        from celery import current_task

        if current_task and current_task.request:
            return True

        if os.getenv('CELERY_WORKER_RUNNING') or os.getenv('CELERY_ACTIVE'):
            return True

        import sys
        if 'celery' in sys.argv[0] and 'worker' in sys.argv:
            return True

        return False
    except (ImportError, AttributeError):
        return False


def print_context_state(location: str = "Unknown"):
    """Print comprehensive context state for debugging."""
    print(f"\n=== CONTEXT STATE AT {location.upper()} ===")

    try:
        from lex.api.utils import operation_context
        op_context = operation_context.get()
        print(f"Operation Context: {op_context}")
        if op_context:
            for key, value in op_context.items():
                print(f"  {key}: {value}")
    except Exception as e:
        print(f"Operation Context ERROR: {e}")

    try:
        from lex.audit_logging.utils.model_context import _model_context
        model_ctx = _model_context.get()
        print(f"Model Context: {model_ctx}")
        if hasattr(model_ctx, '__dict__'):
            for key, value in model_ctx.__dict__.items():
                print(f"  {key}: {value}")
    except Exception as e:
        print(f"Model Context ERROR: {e}")

    print("=" * 50)


# Export everything
__all__ = [
    'lex_shared_task',
    'CallbackTask',
    'calc_and_save',
    'get_calc_and_save_task',
    'RunInCelery',
    'UnblockCelery',
    'is_in_unblock_context',
    'register_task_with_context',
    'respect_unblock_celery'
]