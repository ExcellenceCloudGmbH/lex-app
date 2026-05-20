# Enhanced Celery Task Infrastructure

"""
Celery task infrastructure with custom decorators and callback handling.

This module provides enhanced Celery task integration with proper lifecycle
management, status tracking, and error handling for calculation models.

Key context managers:
- ``WaitForTasks``   — dispatch children to Celery and block until they finish.
- ``FireAndForget``  — dispatch children without waiting (overrides WaitForTasks).

Legacy aliases ``RunInCelery`` / ``AwaitDispatch`` and ``UnblockCelery`` are
kept for backward compatibility but should not be used in new code.
"""

import asyncio
import contextvars
import logging
import os
import sys
import traceback
from copy import deepcopy
from functools import wraps
from typing import Dict, Tuple, Optional, Set, List, Any
from uuid import uuid4

from asgiref.sync import sync_to_async
from celery import Task, shared_task
from celery.result import allow_join_result
from django.db import transaction
from django.db.models import Model
from lex.api.utils import operation_context
from lex.audit_logging.utils.ModelContext import _model_context
from lex.audit_logging.utils.calculation_audit import (
    ensure_terminal_calculation_audit,
)
from lex.core.models.CalculationModel import CalculationModel
from lex.core.signals import update_calculation_status

logger = logging.getLogger(__name__)

_current_module = sys.modules[__name__]
if __name__ == "lex.lex_app.celery_tasks":
    sys.modules.setdefault("lex_app.celery_tasks", _current_module)
elif __name__ == "lex_app.celery_tasks":
    sys.modules.setdefault("lex.lex_app.celery_tasks", _current_module)


def _celery_is_active() -> bool:
    """Return True when Celery dispatch is enabled via environment."""
    return os.getenv("CELERY_ACTIVE", "False").lower() == "true"


class CeleryCalculationContext:
    """
    Context manager to set calculation_id for Celery tasks.

    This allows CalculationLog.log() to access the calculation_id
    even when running in a Celery worker process.
    """

    def __init__(self, context, model_context):
        self.context = context
        self.model_context = model_context
        self._op_token = None
        self._model_context_backup = None

    def __enter__(self):
        if self.context:
            logger.warning(f"Operation Context {self.context}")
            new_context = deepcopy(self.context)
            new_context['calculation_id'] = self.context.get('calculation_id', None)
            new_context['operation_id'] = str(uuid4())
            new_context["celery_task"] = True
            new_context["task_name"] = "calc_and_save"
            self._op_token = operation_context.set(new_context)
        else:
            logger.debug(
                "CeleryCalculationContext entered with context=None — "
                "operation_context will NOT be set (expected in synchronous mode)."
            )
        if self.model_context:
            self._model_context_backup = _model_context.get()['model_context']
            _model_context.get()['model_context'] = self.model_context
            logger.warning(f"Operation Context {self.model_context}")
            logger.warning(f"Saved context {_model_context.get()['model_context']}")
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self._op_token is not None:
            operation_context.reset(self._op_token)
        if self._model_context_backup is not None:
            _model_context.get()['model_context'] = self._model_context_backup


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
                        task_id=task_id,
                        context_data=kwargs.get("context"),
                        model_context=kwargs.get("model_context"),
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
                        stack_trace=str(einfo) if einfo else None,
                        task_id=task_id,
                        context_data=kwargs.get("context"),
                        model_context=kwargs.get("model_context"),
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
            stack_trace: Optional[str] = None,
            task_id: Optional[str] = None,
            context_data: Optional[Dict[str, Any]] = None,
            model_context=None,
    ) -> None:
        """Update model status and notify connected systems."""
        try:
            with transaction.atomic():
                update_values = {"is_calculated": status}
                model_instance.is_calculated = status
                if error_message and hasattr(model_instance, 'error_message'):
                    model_instance.error_message = error_message
                    update_values["error_message"] = error_message
                if task_id and hasattr(model_instance, 'task_id'):
                    model_instance.task_id = task_id
                    update_values["task_id"] = task_id
                persisted = self._persist_status_fields(model_instance, update_values)
                if persisted:
                    logger.warning(
                        f"Updating status for {model_instance.__class__.__name__} task {task_id}"
                    )
                update_calculation_status(
                    model_instance,
                    exception_details=error_message,
                    stack_trace=stack_trace,
                )
        except Exception as update_error:
            logger.error(
                f"Failed to update model status for {model_instance}: {update_error}",
                exc_info=True
            )
        finally:
            try:
                ensure_terminal_calculation_audit(
                    model_instance,
                    audit_status="failure" if status == CalculationModel.ERROR else "success",
                    error_message=error_message,
                    stack_trace=stack_trace,
                    context_data=context_data,
                    model_context=model_context,
                )
            except Exception as audit_error:
                logger.error(
                    "Failed to finalize terminal audit log for %s in callback: %s",
                    model_instance,
                    audit_error,
                    exc_info=True,
                )

    def _persist_status_fields(
            self,
            model_instance: CalculationModel,
            update_values: Dict[str, Any],
    ) -> bool:
        """
        Persist callback-managed status fields without saving the full model.

        Celery task arguments may reference a stale model snapshot or even a row
        that was removed and rebuilt by the bitemporal synchronization flow.
        Using a direct queryset update avoids rewriting unrelated fields from the
        stale snapshot and lets us handle a missing row gracefully.
        """
        model_class = model_instance.__class__
        updated_rows = model_class._default_manager.filter(pk=model_instance.pk).update(**update_values)
        if updated_rows:
            return True

        if hasattr(model_class, "history"):
            try:
                from lex.process_admin.utils.bitemporal_sync import BitemporalSynchronizer

                BitemporalSynchronizer.sync_record_for_model(model_class, model_instance.pk)
                updated_rows = model_class._default_manager.filter(pk=model_instance.pk).update(**update_values)
                if updated_rows:
                    return True
            except Exception as sync_error:
                logger.warning(
                    "Failed to resync main-table row before persisting callback status for %s(%s): %s",
                    model_class.__name__,
                    model_instance.pk,
                    sync_error,
                    exc_info=True,
                )

        logger.warning(
            "Skipping callback status persistence for %s(%s); no active row found.",
            model_class.__name__,
            model_instance.pk,
        )
        return False


class FireAndForget:
    """
    Dispatch child tasks to Celery **without** waiting for them.

    Use this for side-effects that don't affect the caller's correctness:
    notifications, cache warming, analytics, etc.

    Standalone usage::

        with FireAndForget():
            send_report_email(report)       # dispatched, nobody waits
            notify_slack_channel(report)    # dispatched, nobody waits
        # execution continues immediately

    When nested inside a :class:`WaitForTasks` block, ``FireAndForget``
    overrides the blocking behaviour for the enclosed calls::

        with WaitForTasks():
            compute_nav.delay(q1)               # parent WILL wait

            with FireAndForget():               # override — don't wait
                send_report_email(report)       # parent WON'T wait

            compute_nav.delay(q2)               # parent WILL wait
        # blocks until q1 and q2 finish; email may still be in flight

    No-op when ``CELERY_ACTIVE`` is not set.
    """

    def __init__(self, force_tasks: Optional[Set[str]] = None,
                 exclude_tasks: Optional[Set[str]] = None):
        """
        Args:
            force_tasks: Task names to force-dispatch (``None`` = all).
            exclude_tasks: Task names that should follow WaitForTasks rules.
        """
        self.force_tasks = force_tasks
        self.exclude_tasks = exclude_tasks or set()
        self.dispatched_results: List[Any] = []
        self._active = _celery_is_active()

    def __enter__(self):
        if not self._active:
            return self
        unblock_context = unblock_tasks_context.get()
        unblock_context['unblock_context_stack'].append(self)
        unblock_tasks_context.set(unblock_context)
        logger.debug(f"FireAndForget entered. force={self.force_tasks}, exclude={self.exclude_tasks}")
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if not self._active:
            return
        unblock_context = unblock_tasks_context.get()
        if unblock_context['unblock_context_stack']:
            unblock_context['unblock_context_stack'].pop()
        logger.debug(f"FireAndForget exited. Dispatched {len(self.dispatched_results)} tasks.")

    def should_force_dispatch(self, task_name: str) -> bool:
        """Return True if *task_name* should bypass WaitForTasks blocking."""
        if task_name in self.exclude_tasks:
            return False
        if self.force_tasks is None:
            return True
        return task_name in self.force_tasks

    def add_dispatched_result(self, result):
        self.dispatched_results.append(result)

    def wait_for_completion(self):
        """Wait for all dispatched tasks (rarely needed — prefer WaitForTasks)."""
        if not self.dispatched_results:
            return
        logger.info(f"FireAndForget: Waiting for {len(self.dispatched_results)} tasks")
        for result in self.dispatched_results:
            try:
                with allow_join_result():
                    result.get()
            except Exception as e:
                logger.error(f"FireAndForget: Task {result.id} failed: {e}")
                raise
        self.dispatched_results.clear()
        logger.info("FireAndForget: All tasks completed")

    @classmethod
    def get_current_context(cls) -> Optional['FireAndForget']:
        """Return the innermost active ``FireAndForget`` context, or ``None``."""
        unblock_context = unblock_tasks_context.get()
        if unblock_context['unblock_context_stack']:
            return unblock_context['unblock_context_stack'][-1]
        return None


class WaitForTasks:
    """
    Dispatch child ``@lex_shared_task`` calls to Celery and **block** until
    every dispatched task has finished.

    Use this when a parent calculation triggers child calculations and
    must wait for all children to complete before continuing.

    Basic usage::

        with WaitForTasks():
            child.is_calculated = "IN_PROGRESS"
            child.save()          # dispatched to Celery
        # ← execution resumes here only after child finishes

    Nesting is supported — each scope tracks its own tasks independently::

        with WaitForTasks():                    # outer scope
            compute_portfolio.delay(fund_a)     # tracked by outer
            with WaitForTasks():                # inner scope
                compute_nav.delay(q1)           # tracked by inner
                compute_nav.delay(q2)           # tracked by inner
            # ← blocks until q1 and q2 finish
            generate_report.delay(fund_a)       # tracked by outer
        # ← blocks until fund_a and the report finish

    No-op when ``CELERY_ACTIVE`` is not set.
    """

    def __init__(self, include_tasks: Optional[Set[str]] = None,
                 exclude_tasks: Optional[Set[str]] = None):
        """
        Args:
            include_tasks: Task names to dispatch (``None`` = all).
            exclude_tasks: Task names to keep synchronous (overrides *include_tasks*).
        """
        self.include_tasks = include_tasks
        self.exclude_tasks = exclude_tasks or set()
        self.dispatched_results: List[Any] = []
        self.block = True
        self._active = _celery_is_active()

    def __enter__(self):
        if self._active:
            tasks_context.get().get('task_context_stack').append(self)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if not self._active:
            return
        if tasks_context.get().get('task_context_stack'):
            tasks_context.get().get('task_context_stack').pop()
        self.wait_for_completion()

    def should_dispatch(self, task_name: str) -> bool:
        """Return True if *task_name* should be dispatched to a Celery worker."""
        if task_name in self.exclude_tasks:
            return False
        if self.include_tasks is None:
            return True
        return task_name in self.include_tasks

    def add_dispatched_result(self, result):
        self.dispatched_results.append(result)

    def wait_for_completion(self):
        """Block until every dispatched task has finished."""
        if not self.dispatched_results:
            return
        logger.info(f"WaitForTasks: waiting for {len(self.dispatched_results)} tasks")
        # If a FireAndForget context is active, skip blocking.
        if FireAndForget.get_current_context():
            return
        for result in self.dispatched_results:
            try:
                with allow_join_result():
                    result.get()
                logger.debug(f"WaitForTasks: task {result.id} completed")
            except Exception as e:
                logger.error(f"WaitForTasks: task {result.id} failed: {e}")
                raise
        self.dispatched_results.clear()
        logger.info("WaitForTasks: all tasks completed")

    @classmethod
    def get_current_context(cls) -> Optional['WaitForTasks']:
        """Return the innermost active ``WaitForTasks`` context, or ``None``."""
        if tasks_context.get().get('task_context_stack'):
            return tasks_context.get().get('task_context_stack')[-1]
        return None


def is_celery_worker_process() -> bool:
    """
    Return True only when code is executing inside an actual Celery worker.

    ``CELERY_ACTIVE`` merely enables Celery dispatch for the current process;
    it does not mean the current process is itself a worker. That distinction
    matters for nested calculation dispatch, where worker tasks must avoid
    spawning more worker-blocking child tasks unless the caller explicitly
    opened an async context.
    """
    try:
        from celery import current_task

        if current_task and getattr(current_task, "request", None):
            return True
    except (ImportError, AttributeError):
        pass

    if os.getenv("IS_RUNNING_IN_CELERY", "").lower() == "true":
        return True

    if os.getenv("CELERY_WORKER_RUNNING", "").lower() == "true":
        return True

    try:
        import sys

        if "celery" in sys.argv[0] and "worker" in sys.argv:
            return True
    except Exception:
        pass

    return False


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
    Respects :class:`WaitForTasks` and :class:`FireAndForget` contexts.

    Priority hierarchy:
    1. ``FireAndForget`` — forces async dispatch (highest priority)
    2. ``WaitForTasks``  — dispatches and registers for blocking wait
    3. No context         — runs synchronously (default)
    """

    def __init__(self, instance, task):
        self.instance = instance
        self.task = task

    def __call__(self, *args, **kwargs):
        task_name = getattr(self.task, 'name', self.task.__name__)

        if not _celery_is_active():
            return self.task(self.instance, *args, **kwargs)

        # PRIORITY 1: FireAndForget (highest)
        ff_ctx = FireAndForget.get_current_context()
        if ff_ctx and ff_ctx.should_force_dispatch(task_name):
            logger.debug(f"FireAndForget: dispatching {task_name}")
            result = self.task.delay(self.instance, *args, **kwargs)
            ff_ctx.add_dispatched_result(result)
            return result

        # PRIORITY 2: WaitForTasks
        await_ctx = WaitForTasks.get_current_context()
        if await_ctx is None:
            logger.debug(f"No dispatch context — running {task_name} synchronously")
            return self.task(self.instance, *args, **kwargs)

        if ff_ctx and task_name in ff_ctx.exclude_tasks:
            logger.debug(f"FireAndForget excluding {task_name}")
            return self.task(self.instance, *args, **kwargs)

        if await_ctx.should_dispatch(task_name):
            logger.debug(f"WaitForTasks: dispatching {task_name}")
            result = self.task.delay(self.instance, *args, **kwargs)
            await_ctx.add_dispatched_result(result)
            return result
        else:
            logger.debug(f"WaitForTasks: running {task_name} synchronously")
            return self.task(self.instance, *args, **kwargs)

    def delay(self, *args, **kwargs):
        """Always handles asynchronous .delay() calls."""
        return self.task.delay(self.instance, *args, **kwargs)

    def __getattr__(self, name):
        """Proxy any other attributes to the underlying task."""
        return getattr(self.task, name)


class EnhancedTaskMethodDescriptor:
    """Descriptor that uses :class:`EnhancedBoundTaskMethod` for dispatch logic."""

    def __init__(self, task):
        self.task = task

    def __get__(self, instance, owner):
        if instance is None:
            return self
        return EnhancedBoundTaskMethod(instance, self.task)

    def __call__(self, *args, **kwargs):
        task_name = getattr(self.task, 'name', self.task.__name__)

        if not _celery_is_active():
            return self.task(*args, **kwargs)

        ff_ctx = FireAndForget.get_current_context()
        if ff_ctx and ff_ctx.should_force_dispatch(task_name):
            logger.debug(f"FireAndForget: dispatching {task_name}")
            result = self.task.delay(*args, **kwargs)
            ff_ctx.add_dispatched_result(result)
            return result

        await_ctx = WaitForTasks.get_current_context()
        if await_ctx is None:
            return self.task(*args, **kwargs)

        if ff_ctx and task_name in ff_ctx.exclude_tasks:
            return self.task(*args, **kwargs)

        if await_ctx.should_dispatch(task_name):
            logger.debug(f"WaitForTasks: dispatching {task_name}")
            result = self.task.delay(*args, **kwargs)
            await_ctx.add_dispatched_result(result)
            return result
        else:
            logger.debug(f"WaitForTasks: running {task_name} synchronously")
            return self.task(*args, **kwargs)

    def __getattr__(self, name):
        return getattr(self.task, name)


def lex_shared_task(_func=None, *, lex_max_retries=None, **task_opts):
    """
    Decorator that makes a method Celery-capable, respecting
    :class:`WaitForTasks` and :class:`FireAndForget` contexts.

    ``lex_max_retries`` is the per-task cap consumed by the worker-recovery
    supervisor (see :mod:`lex.lex_app.celery_recovery`). When the supervisor
    detects this task's worker died, it re-publishes the task up to
    ``lex_max_retries`` times before injecting a ``MaxRequeueExceeded`` into the
    result backend. If left as ``None``, the supervisor falls back to the
    ``LEX_TASK_MAX_RETRIES`` env default. This is **not** Celery's built-in
    ``max_retries`` (which governs ``self.retry()`` calls inside the task body);
    the two are orthogonal.
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

                # Only enter CeleryCalculationContext when we actually have
                # context to set (i.e. running inside a Celery worker).
                # In synchronous mode the operation_context already exists
                # on the calling thread from the parent CalculationModel,
                # so wrapping again with None would just spam warnings and
                # break the logging chain.
                if context or model_context:
                    with CeleryCalculationContext(context, model_context):
                        result = func(*args, **kwargs)
                else:
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

        if lex_max_retries is not None:
            # Stash on the underlying task so the recovery heartbeat can read
            # it via ``getattr(task, "lex_max_retries", None)`` in task_prerun.
            # ``shared_task`` returns a celery PromiseProxy in some setups;
            # resolving it via ``_get_current_object`` ensures the attribute
            # lands on the actual Task instance rather than the proxy slot.
            try:
                value = int(lex_max_retries)
                if value < 0:
                    raise ValueError("lex_max_retries must be >= 0")
                target = getattr(celery_task, "_get_current_object", None)
                target = target() if callable(target) else celery_task
                target.lex_max_retries = value
            except Exception:
                logger.warning(
                    "lex_shared_task: failed to apply lex_max_retries=%r to %s; "
                    "supervisor will fall back to LEX_TASK_MAX_RETRIES",
                    lex_max_retries, func.__name__, exc_info=True,
                )

        return EnhancedTaskMethodDescriptor(celery_task)

    if _func is not None and callable(_func):
        return decorator(_func)
    else:
        return decorator


# ── Utility functions ────────────────────────────────────────────────────

def is_in_fire_and_forget_context(task_name: str = None) -> bool:
    """True when inside a :class:`FireAndForget` that would force dispatch."""
    ctx = FireAndForget.get_current_context()
    if not ctx:
        return False
    if task_name is None:
        return True
    return ctx.should_force_dispatch(task_name)

# Backward-compatible alias
is_in_unblock_context = is_in_fire_and_forget_context


def register_task_with_context(task_result):
    """
    Register a task result with the active dispatch context.

    Checks :class:`FireAndForget` first, then :class:`WaitForTasks`.
    """
    ff_ctx = FireAndForget.get_current_context()
    if ff_ctx is not None:
        ff_ctx.add_dispatched_result(task_result)
        return task_result

    await_ctx = WaitForTasks.get_current_context()
    if await_ctx is not None:
        await_ctx.add_dispatched_result(task_result)

    return task_result


def respect_fire_and_forget(func):
    """Decorator that injects ``_force_async=True`` when inside a FireAndForget."""

    @wraps(func)
    def wrapper(*args, **kwargs):
        if is_in_fire_and_forget_context(func.__name__):
            logger.debug(f"FireAndForget detected for {func.__name__}")
            kwargs['_force_async'] = True
        return func(*args, **kwargs)

    return wrapper

# Backward-compatible alias
respect_unblock_celery = respect_fire_and_forget


# Task definitions (your existing tasks)
@lex_shared_task(name="initial_data_upload")
def load_data(test, generic_app_models, audit_logging_enabled=None, initial_data_load=None):
    """Load data asynchronously if conditions are met."""
    if not initial_data_load:
        return
    from lex.lex_app.apps import _create_audit_logger_for_task

    audit_logger = _create_audit_logger_for_task(audit_logging_enabled)
    finalization_error = None

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
            if _celery_is_active() or is_running_in_celery():
                test.setUpCloudStorage(generic_app_models, audit_logger)
            else:
                asyncio.run(sync_to_async(test.setUpCloudStorage)(generic_app_models, audit_logger))

        print("Initial Data Fill completed Successfully")
    except Exception as e:
        finalization_error = (
            f"Initial data upload failed with {type(e).__name__}: {str(e)}\n{traceback.format_exc()}"
        )
        print("Initial Data Fill aborted with Exception:")
        print(f"Error type: {type(e).__name__}")
        print(f"Error message: {str(e)}")
        traceback.print_exc()
        raise e
    finally:
        # Always finalize audit logging to prevent lingering pending statuses.
        if audit_logger:
            try:
                summary = audit_logger.finalize_batch(failure_error=finalization_error)
                print(f"Audit logging summary: {summary}")
            except Exception as e:
                print(f"Warning: Failed to finalize audit logging: {e}")


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
            from lex.audit_logging.utils.ModelContext import model_logging_context
            from lex.core.models.CalculationModel import calculation_execution_context
            with calculation_execution_context():
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

                    from lex.core.models.CalculationModel import calculation_execution_context as _cec
                    with _cec():
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
        if is_celery_worker_process():
            return True

        import celery
        from celery import current_task

        if current_task and current_task.request:
            return True

        if os.getenv('CELERY_WORKER_RUNNING'):
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
        from lex.audit_logging.utils.ModelContext import _model_context
        model_ctx = _model_context.get()
        print(f"Model Context: {model_ctx}")
        if hasattr(model_ctx, '__dict__'):
            for key, value in model_ctx.__dict__.items():
                print(f"  {key}: {value}")
    except Exception as e:
        print(f"Model Context ERROR: {e}")

    print("=" * 50)


# ── Backward-compatible aliases ──────────────────────────────────────────
AwaitDispatch = WaitForTasks
RunInCelery = WaitForTasks
UnblockCelery = FireAndForget


__all__ = [
    'lex_shared_task',
    'CallbackTask',
    'calc_and_save',
    'get_calc_and_save_task',
    # Current names
    'WaitForTasks',
    'FireAndForget',
    'is_in_fire_and_forget_context',
    'register_task_with_context',
    'respect_fire_and_forget',
    # Backward-compatible aliases
    'AwaitDispatch',
    'RunInCelery',
    'UnblockCelery',
    'is_in_unblock_context',
    'respect_unblock_celery',
]

@lex_shared_task(name="activate_history_version")
def activate_history_version(model_app_label: str, model_name: str, history_id: int):
    """
    Event-Driven Activation Task.
    Triggered by Celery Beat when a specific History Record becomes valid.
    """
    from django.apps import apps
    from django.utils import timezone
    from lex.process_admin.utils.bitemporal_sync import BitemporalSynchronizer
    
    try:
        # 1. Resolve Model
        try:
             model = apps.get_model(model_app_label, model_name)
        except LookupError:
             logger.error(f"Activation Failed: Model {model_app_label}.{model_name} not found.")
             return "failed_model_lookup"
             
        HistoryModel = model.history.model
        MetaModel = HistoryModel.meta_history.model
        
        # 2. Fetch Record
        try:
            history_record = HistoryModel.objects.get(pk=history_id)
        except HistoryModel.DoesNotExist:
            # Record might have been deleted before activation?
            logger.warning(f"Activation Skipped: History Record {history_id} not found.")
            return "skipped_missing_record"
            
        # 3. Validation Check (Double Check against Time)
        # Even though task was scheduled, check if we are actually past valid_from.
        # Allow small clock drift (e.g. 1 sec).
        now = timezone.now()
        if history_record.valid_from > now + timezone.timedelta(seconds=5):
             # Reschedule? Or just fail?
             logger.error(f"Activation Too Early: Record {history_id} valid from {history_record.valid_from}, now is {now}")
             return "failed_too_early"
             
        # 4. Sync
        pk_name = model._meta.pk.name
        pk_val = getattr(history_record, pk_name)
        
        logger.info(f"Activating History Record {history_id} for {model_name} {pk_val}")
        BitemporalSynchronizer.sync_record_for_model(model, pk_val, HistoryModel)
        
        # 5. Update Meta Status
        # We need to find the specific meta record that scheduled this? 
        # Or just update any meta record pointing to this?
        # The user design puts task_name ON the meta record. 
        # But our MetaHistory records are versions themselves.
        # We should find the Meta Record that has this task name?
        # Wait, the task doesn't know the task name explicitly unless passed?
        # But we can update all meta records for this history_id that are 'SCHEDULED' to 'DONE'.
        
        MetaModel.objects.filter(
            history_object_id=history_id,
            meta_task_status="SCHEDULED"
        ).update(meta_task_status="DONE")
        
        return "success"
        
    except Exception as e:
        logger.error(f"Activation Error for History {history_id}: {e}", exc_info=True)
        raise e
