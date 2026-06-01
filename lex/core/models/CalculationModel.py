import logging
import os
import traceback
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from contextvars import ContextVar
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
from lex.api.utils import operation_context, OperationContext
from lex.audit_logging.utils.CacheManager import CacheManager
from lex.audit_logging.utils.ContextResolver import ContextResolver
from lex.core.exceptions import (
    ensure_list,
    find_exception_artifacts,
    resolve_exception_detail,
    resolve_exception_traceback,
    select_preferred_exception_detail,
    select_preferred_stack_trace,
)
from lex.core.models.LexModel import LexModel
from rest_framework.exceptions import APIException

logger = logging.getLogger(__name__)

# ContextVar that is True while user code (calculate/update) is executing
# inside a calculation cycle.  Child record saves that happen during this
# window should NOT update edited_by / edited_at because the change is
# system-triggered, not a direct user edit.
_in_calculation_execution: ContextVar[bool] = ContextVar(
    '_in_calculation_execution', default=False,
)

# Dedicated thread pool for calculations.  This keeps long-running
# calculations OFF the ASGI single_thread_executor so they don't starve
# other sync operations (API views, WS auth, DB queries).
# Unbounded (default) so concurrent calculations on different records
# never block each other — the GIL serializes CPU work regardless.
_calculation_executor = ThreadPoolExecutor(
    max_workers=int(os.environ.get("LEX_CALCULATION_THREADS", "10")),
    thread_name_prefix="lex-calc",
)


@contextmanager
def calculation_execution_context():
    """Mark the current thread as inside a calculation's user-code execution."""
    token = _in_calculation_execution.set(True)
    try:
        yield
    finally:
        _in_calculation_execution.reset(token)


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


class CalculationCancelled(Exception):
    """Raised inside a worker when the caller revoked the calculation.

    A small marker exception so :class:`CallbackTask.on_failure` (and any
    in-process error handlers) can distinguish a user-initiated cancel
    from an unrelated runtime ``Exception`` and persist ``ABORTED``
    instead of ``ERROR``. Carries an optional ``reason`` for the audit
    trail.
    """

    def __init__(self, reason: str = "Calculation cancelled by user"):
        super().__init__(reason)
        self.reason = reason


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
            self._is_calculation_triggered_save = True
            try:
                result = super().save(*args, **kwargs)
            finally:
                self._defer_calculate_hook = False
                self._is_calculation_triggered_save = False

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

    # ------------------------------------------------------------------
    # Public cancellation API (Celery-only design)
    # ------------------------------------------------------------------

    @classmethod
    def cancel(cls, instance, *, recursive: bool = True, reason: str = "") -> dict:
        """Request cancellation of an in-progress calculation.

        Public surface for the abort/cancel button. Per the framework's
        Celery-only cancel design:

        * If the calculation was dispatched to a Celery worker we have
          a ``task_id`` in :class:`ActiveCalculationStateStore` — we
          ``app.control.revoke(task_id, terminate=True, signal="SIGTERM")``
          and immediately persist ``ABORTED``. The worker's
          :class:`CallbackTask.on_failure` maps the resulting
          ``TaskRevokedError`` / ``SoftTimeLimitExceeded`` /
          ``WorkerLostError`` onto ``ABORTED`` rather than ``ERROR``.
        * If no ``task_id`` is registered (sync-dispatched calc, or
          Celery never reached) we return ``cancellable=False`` —
          synchronous calculations cannot be killed in this design;
          the API layer surfaces this as HTTP 409.
        * When ``recursive=True`` (the default), every active descendant
          that shares the cancelled record's ``calculation_id`` is
          revoked the same way. This matches the user expectation that
          "abort" stops the whole running tree, not just the entry
          point the button was attached to.

        Returns a small report dict the REST endpoint serialises back
        to the caller::

            {
                "cancelled": <bool>,            # primary target outcome
                "cancellable": <bool>,          # had task_id?
                "status": "ABORTED" | "...",    # persisted is_calculated
                "revoked_tasks": [<task_id>, ...],
                "descendants_cancelled": <int>,
            }
        """
        from lex.core.signals.ActiveCalculationStateStore import (
            ActiveCalculationStateStore,
        )
        from lex.core.signals.CalculationSignals import update_calculation_status

        if instance is None or getattr(instance, "pk", None) is None:
            return {
                "cancelled": False,
                "cancellable": False,
                "status": getattr(instance, "is_calculated", None),
                "revoked_tasks": [],
                "descendants_cancelled": 0,
            }

        # Only meaningful while running. Idempotent on terminal states.
        current_status = getattr(instance, "is_calculated", None)
        if current_status != cls.IN_PROGRESS:
            return {
                "cancelled": False,
                "cancellable": False,
                "status": current_status,
                "revoked_tasks": [],
                "descendants_cancelled": 0,
                "reason": "not_in_progress",
            }

        record_id = f"{instance._meta.model_name}_{instance.pk}"
        entry = ActiveCalculationStateStore.get_entry(record_id)
        task_id = entry.get("task_id") or None
        calculation_id = entry.get("calculation_id") or None

        # Collect every descendant first so we revoke and persist them
        # in the same pass — children dispatched from inside a parent
        # share the parent's calculation_id.
        descendants = []
        if recursive and calculation_id:
            descendants = [
                d
                for d in ActiveCalculationStateStore.find_descendants(calculation_id)
                if d.get("record_id") != record_id
            ]

        if not task_id and not descendants:
            return {
                "cancelled": False,
                "cancellable": False,
                "status": current_status,
                "revoked_tasks": [],
                "descendants_cancelled": 0,
                "reason": "sync_calculation_not_cancellable",
            }

        revoked = []
        revoke_reason = reason or "Calculation cancelled by user"

        # Issue the revokes BEFORE flipping DB state. If revoke raises,
        # we surface the error rather than leaving the DB in a state
        # that says "ABORTED" while a worker is still computing.
        if task_id:
            cls._revoke_celery_task(task_id)
            revoked.append(task_id)

        descendants_cancelled = 0
        for desc in descendants:
            desc_task = desc.get("task_id") or None
            if desc_task:
                try:
                    cls._revoke_celery_task(desc_task)
                    revoked.append(desc_task)
                except Exception:  # pragma: no cover — best-effort
                    logger.warning(
                        "Failed to revoke descendant task %s during cancel of %s",
                        desc_task,
                        record_id,
                        exc_info=True,
                    )
            # Persist ABORTED on the descendant row too — the worker
            # callback will also do this, but doing it here guarantees
            # the UI sees the new state immediately even if the worker
            # is wedged.
            cls._persist_aborted_by_entry(desc, revoke_reason)
            descendants_cancelled += 1

        # Persist ABORTED on the primary target.
        cls._persist_aborted(instance, revoke_reason)
        try:
            update_calculation_status(instance)
        except Exception:  # pragma: no cover
            logger.warning(
                "Failed to broadcast ABORTED status for %s", record_id, exc_info=True
            )

        return {
            "cancelled": True,
            "cancellable": True,
            "status": cls.ABORTED,
            "revoked_tasks": revoked,
            "descendants_cancelled": descendants_cancelled,
        }

    @staticmethod
    def _revoke_celery_task(task_id: str) -> None:
        """Revoke a Celery task by id with SIGTERM (instant kill)."""
        from celery import current_app

        current_app.control.revoke(task_id, terminate=True, signal="SIGTERM")

    @classmethod
    def _persist_aborted(cls, instance, reason: str) -> None:
        """Flip ``is_calculated=ABORTED`` and persist, mirroring ERROR path."""
        from lex.core.signals.ActiveCalculationStateStore import (
            ActiveCalculationStateStore,
        )

        try:
            instance.is_calculated = cls.ABORTED
            if hasattr(instance, "calculation_error_message"):
                instance.calculation_error_message = reason
            elif hasattr(instance, "error_message"):
                instance.error_message = reason
            instance.save(skip_hooks=True)
            cls._register_terminal_state_persistence(instance)
        except Exception:
            logger.error(
                "Failed to persist ABORTED state for %s", instance, exc_info=True
            )
        finally:
            try:
                record_id = f"{instance._meta.model_name}_{instance.pk}"
                ActiveCalculationStateStore.clear(record_id)
            except Exception:  # pragma: no cover
                pass

    @classmethod
    def _persist_aborted_by_entry(cls, entry: dict, reason: str) -> None:
        """Resolve a state-store entry back to a row and persist ABORTED."""
        from django.apps import apps
        from lex.core.signals.ActiveCalculationStateStore import (
            ActiveCalculationStateStore,
        )

        model_label = entry.get("model_label") or ""
        record_pk = entry.get("record_pk") or ""
        if not model_label or "." not in model_label or not record_pk:
            return
        app_label, model_name = model_label.split(".", 1)
        try:
            model_class = apps.get_model(app_label, model_name)
        except Exception:
            return
        try:
            row = model_class._default_manager.filter(pk=record_pk).first()
        except Exception:
            row = None
        if row is None:
            ActiveCalculationStateStore.clear(entry.get("record_id", ""))
            return
        cls._persist_aborted(row, reason)

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
        task_result = func.delay(context=new_context, model_context=model_context)

        # Record the celery task_id against this record so that a later
        # cancel request has a handle to revoke. The active-state store
        # is already the per-process registry tracking active calcs;
        # piggybacking on it avoids inventing a parallel cache and keeps
        # the parent->child relationship (shared calculation_id) trivially
        # queryable for the recursive-cancel walk in ``cancel()``.
        try:
            from lex.core.signals.ActiveCalculationStateStore import (
                ActiveCalculationStateStore,
            )

            record_id = f"{self._meta.model_name}_{self.pk}"
            ActiveCalculationStateStore.set_task_id(
                record_id, getattr(task_result, "id", None)
            )
        except Exception as store_error:  # pragma: no cover — defensive
            logger.warning(
                "Failed to register task_id for cancel support: %s",
                store_error,
                exc_info=True,
            )

        # Register with WaitForTasks context if one exists
        from lex.lex_app.celery_tasks import register_task_with_context
        return register_task_with_context(task_result)

    def _run_in_calculation_executor(self):
        """
        Synchronous-fallback dispatcher for ``execute_calculation_sync``.

        Historically this method submitted the calculation to a dedicated
        thread pool in order to free the ASGI single-thread executor.  That
        introduced two regressions that the Cluster-7 behavioural tests
        ([test-plan/test-clusters.md#7-calculation-state-machine](../../test_project/test-plan/test-clusters.md))
        immediately surfaced:

        1. **Cross-thread DB deadlock.**  ``LexModel.save()`` runs inside
           ``transaction.atomic()`` and holds row locks on the caller's
           connection.  The worker thread opens its **own** Django
           connection (connections are thread-local) and any write against
           the same row waits forever for the caller's lock — while the
           caller is blocked on ``future.result()``.  Parent → child
           hierarchies (test 7.5+) hang outright.
        2. **`operation_context` ContextVar isolation.**  ``copy_context()``
           gives the worker a *copy*; mutations made inside the worker
           (e.g. ``OperationContext.__enter__`` setting ``calculation_id``)
           **do not propagate back** to the caller.  The caller's
           except-branch cleanup then calls ``ContextResolver.resolve()``
           which raises ``Missing calculation_id in operation context``.

        Both problems disappear when the sync fallback runs inline on the
        caller's thread — which is also the only way to preserve the
        documented "synchronous" contract.

        The original goal of the thread pool — freeing the ASGI
        single-thread executor during heavy calculations — is **already
        achieved upstream** by ``One.update()``, which submits
        ``calculate_hook`` to ``_calculation_executor`` and returns HTTP
        202 immediately.  By the time ``calculate_hook`` reaches this
        fallback it is already off the ASGI thread, so a second hop is
        both unnecessary and harmful.

        ``_calculation_executor`` is still exported at module scope for
        ``One.py``'s fire-and-forget HTTP-202 path; only the nested
        re-submit was removed.
        """
        return self.execute_calculation_sync()

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
                with calculation_execution_context():
                    func()
                self.is_calculated = self.SUCCESS
            else:
                with transaction.atomic():
                    with calculation_execution_context():
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
            # If the exception bubbling up is a cancellation signal
            # (cooperative CalculationCancelled, or a worker-side
            # Terminated/SoftTimeLimit/Revoked/WorkerLost propagated
            # synchronously into this thread), the terminal state must
            # be ABORTED, not ERROR. ERROR would lie to the user — they
            # pressed cancel and the audit would say "the calc crashed".
            from lex.lex_app.celery_tasks import _is_cancellation_exception

            if _is_cancellation_exception(e):
                self.is_calculated = self.ABORTED
            else:
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
            if self.is_calculated == self.SUCCESS:
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
            elif self.is_calculated == self.ABORTED:
                try:
                    from lex.audit_logging.utils.calculation_audit import (
                        ensure_terminal_calculation_audit,
                    )

                    ensure_terminal_calculation_audit(
                        self,
                        audit_status="failure",
                        error_message=exception_details or "Calculation cancelled by user",
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
                self._run_in_calculation_executor()

        except Exception as e:
            # Nested hook invocations may already normalize failures into
            # CalculationModelException. Re-raise as-is to avoid noisy
            # # re-wrapping and duplicated error handling.


            # Handle any errors in task dispatch or synchronous execution
            logger.error(f"Calculation failed for {self}: {e}", exc_info=True)
            # Cancellation must collapse onto ABORTED rather than ERROR —
            # whether it surfaced from a Celery worker that was revoked
            # (TaskRevokedError / WorkerLostError / SoftTimeLimitExceeded /
            # Terminated) or from a cooperative in-process raise
            # (CalculationCancelled). Without this branch, the
            # except-block below would persist ERROR and overwrite the
            # ABORTED state cancel() already wrote, leaving the audit row
            # in the wrong terminal state.
            from lex.lex_app.celery_tasks import _is_cancellation_exception

            is_cancellation = _is_cancellation_exception(e)
            status_was_error = self.is_calculated == self.ERROR
            status_was_aborted = self.is_calculated == self.ABORTED
            target_terminal_state = (
                self.ABORTED if is_cancellation else self.ERROR
            )
            self.is_calculated = target_terminal_state

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

            # Persist terminal state (ERROR or ABORTED) and notify websocket
            # clients. Always save self even when execute_calculation_sync
            # already did — to cover cases where the failure originated in
            # calculate_hook itself (e.g. dispatch failure) or where the
            # parent's own calculate() raised directly.
            terminal_state_already_persisted = self._has_persisted_terminal_state(
                self,
                target_terminal_state,
            )
            try:
                if not terminal_state_already_persisted:
                    self.save(skip_hooks=True)
                    self._register_terminal_state_persistence(self)
                if not (status_was_error or status_was_aborted):
                    update_calculation_status(
                        self,
                        exception_details=preferred_exception_detail,
                        stack_trace=preferred_stack_trace,
                    )
            except Exception as status_update_error:
                logger.error(
                    f"Failed to persist/notify {target_terminal_state} state for {self}: {status_update_error}",
                    exc_info=True,
                )
            self._pending_terminal_audit = {
                "audit_status": "failure",
                "error_message": preferred_exception_detail,
                "stack_trace": preferred_stack_trace,
            }
            calc_obj_to_persist = calc_obj[:-1] if calc_obj and calc_obj[-1] is self else calc_obj
            # On cancellation, descendants were already revoked + flipped to
            # ABORTED by ``cancel()``'s recursive walk; we must not now
            # persist ERROR on top of them via the generic error path.
            if not is_cancellation:
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
