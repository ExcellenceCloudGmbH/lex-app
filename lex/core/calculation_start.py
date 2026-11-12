"""Start a record's calculation off the caller's thread, the way the Calculate button does.

One start path, shared by everything that starts a run on a record's behalf rather
than running it inline: the Calculate button and a ``calculate_on_create`` create
(``lex.api.views.model_entries.One``), and a scheduled job firing
(``lex.scheduling.dispatch``). Keeping it in one place keeps those runs
indistinguishable: same IN_PROGRESS announcement, same log cache, same background
executor, same Celery dispatch when Celery is on.

Both halves run inside the caller's ``OperationContext`` (which carries the run's
``calculation_id`` and its audit entry) and ``model_logging_context(instance)``.
"""

from __future__ import annotations

import logging
from concurrent.futures import Future
from contextvars import copy_context
from uuid import uuid4

from django.db import close_old_connections

from lex.audit_logging.utils.CacheManager import CacheManager
from lex.audit_logging.utils.WebSocketNotifier import WebSocketNotifier
from lex.core.models.CalculationModel import CalculationModel, _calculation_executor

logger = logging.getLogger(__name__)

__all__ = [
    "calculate_in_background",
    "calculation_record_id",
    "mark_calculation_started",
    "new_calculation_id",
]


def calculation_record_id(instance) -> str:
    """The id the state store, the log cache and the grid know a record's run by."""
    return f"{instance._meta.model_name}_{instance.pk}"


def new_calculation_id(instance) -> str:
    """A fresh run id of the shape a Calculate click mints (``<model>_<pk>_update_<uuid>``).

    The grid finds a row's log by this shape, so every started run uses it.
    """
    return f"{calculation_record_id(instance)}_update_{uuid4()}"


def mark_calculation_started(instance, calculation_record, calculation_id) -> None:
    """Put a record into IN_PROGRESS and announce its run, before it starts."""
    from lex.core.signals.ActiveCalculationStateStore import ActiveCalculationStateStore

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
        calculation_id=calculation_id,
        record=str(instance),
        model_name=instance._meta.object_name,
        model_label=instance._meta.label_lower,
        record_pk=instance.pk,
    )

    # Notify the "calculations" group (GenericSocket) so
    # the triggering client can pair its temp ID with the
    # server-side calculation_id.
    WebSocketNotifier.send_calculation_update(
        calculation_id=calculation_id,
        calculation_record=calculation_record,
    )

    cache_key = CacheManager.build_cache_key(
        calculation_record,
        calculation_id,
    )
    CacheManager.store_message(cache_key, "")


def calculate_in_background(instance, calculation_record, *, executor=None) -> Future:
    """Run ``instance``'s calculation on the calculation thread pool.

    ``calculate_hook`` runs off the caller's thread, under the current operation
    context (its ``calculation_id`` and audit entry), and dispatches to Celery
    when Celery is on, as for a Calculate click. Returns the pool's future; a
    caller that must outlive the run (a management command) can wait on it.

    ``executor`` defaults to the shared calculation pool (``LEX_CALCULATION_THREADS``).
    """
    from lex.audit_logging.utils.ModelContext import (
        ModelContext,
        _model_context,
    )

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
        # pop upon returning the response.  By
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

    return (executor or _calculation_executor).submit(_background_calculate)
