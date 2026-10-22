"""Builds and emits the ``calculation_finished`` fleet-metrics event
(source-plan.md section 3.3, task 12).

Every root calculation ends in exactly one of four terminal states, and the
controller's Collection dashboard needs to see every one of them to build a
fleet-wide success/failure picture: ``success`` -> ``SUCCESS``, ``failure`` ->
``ERROR``, ``cancelled`` -> ``CANCELLED``, ``aborted`` -> ``ABORTED`` (the
same four strings :func:`lex.audit_logging.utils.calculation_audit.
ensure_terminal_calculation_audit` already uses for ``AuditLogStatus.status``).

:func:`emit_calculation_finished` is called from exactly one place --
``ensure_terminal_calculation_audit``, inside its own ``guard()`` -- so this
module never has to re-derive "is this a root run" or re-implement the
outcome mapping anywhere else: every terminal path (sync success, sync
error, a Celery success/failure callback, a user cancel, or the startup
recovery sweep's abort) converges on that one function, and so converges
here too.

**Why a run can reach this twice.** The finaliser is not always a single
call: a sync cancel calls ``ensure_terminal_calculation_audit`` once from
``CalculationModel`` and once more from ``LexModel._finalize_pending_
terminal_audit`` when the cancel also raises; a Celery failure is finalised
once in the worker process (``celery_tasks.py``'s ``CallbackTask.
on_failure`` -> ``_update_model_status``) and, when the web process's
``WaitForTasks.get()`` re-raises the same remote exception, a second time in
the web process via that same ``_finalize_pending_terminal_audit`` path. The
10,000-entry in-process LRU on ``(audit_log.pk, outcome)`` below absorbs
exactly the *same-process* half of that duplication (two calls in one
Python process produce one event); it cannot and is not meant to catch the
cross-process half -- that is what ``audit_log_id`` makes the controller's
ingest endpoint idempotent for (source-plan.md 3.3: "the controller keeps
one row per (instance_id, audit_log_id)").

**Duration.** ``status_row`` is the ``AuditLogStatus`` row
``ensure_terminal_calculation_audit`` just wrote. Normally that row already
existed in ``'pending'`` (written when the calculation started), so
``updated_at - created_at`` is a real wall-clock span. But when the
finaliser itself had to create the row from scratch -- no earlier pending
row existed to time from, because the finaliser is the first and only thing
that ever wrote an audit row for this run -- the start time is unknown, and
reporting ``updated_at - created_at`` would just report "however long this
function call took", not the calculation's duration. ``ensure_terminal_
calculation_audit`` marks that case by setting a transient, non-persisted
``_freshly_created`` attribute on the ``status_row`` instance it hands us
(this module never writes to the database itself).
"""

from __future__ import annotations

import logging
import threading
from collections import OrderedDict
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple

from lex.core.services.usage_config import usage_metrics_enabled
from lex.lex_app.fleet_metrics import emitter

logger = logging.getLogger(__name__)

#: The finaliser's ``audit_status`` -> the controller's ``outcome``
#: (source-plan.md 3.3). Keys match the four strings ``ensure_terminal_
#: calculation_audit`` writes to ``AuditLogStatus.status``.
_OUTCOME_BY_AUDIT_STATUS: Dict[str, str] = {
    "success": "SUCCESS",
    "failure": "ERROR",
    "cancelled": "CANCELLED",
    "aborted": "ABORTED",
}

#: Bound on the in-process de-dupe cache (task 12 brief).
_DEDUPE_MAX_SIZE = 10_000

_dedupe_lock = threading.Lock()
#: Insertion-ordered set of ``(audit_log.pk, outcome)`` keys already emitted
#: in this process, used as an LRU: a repeat moves to the back, a miss is
#: appended, and the oldest key is evicted once the cache is over-size.
_dedupe_seen: "OrderedDict[Tuple[Any, str], None]" = OrderedDict()


def emit_calculation_finished(audit_log: Any, status_row: Any, audit_status: str) -> None:
    """Queue one ``calculation_finished`` event for a terminal root run.

    A no-op when usage metrics are switched off (``usage_metrics_enabled()``
    is false), when ``audit_log`` has no pk yet, when ``audit_status`` is not
    one of the four mapped values, or when this exact ``(audit_log.pk,
    outcome)`` pair was already emitted by this process. Delegates the
    deployed-instance gate and the actual delivery to :mod:`emitter` --
    this function only ever calls :func:`emitter.emit`, which never raises.
    """
    if not usage_metrics_enabled():
        return

    audit_log_id = getattr(audit_log, "pk", None)
    if audit_log_id is None:
        return

    outcome = _OUTCOME_BY_AUDIT_STATUS.get(audit_status)
    if outcome is None:
        logger.debug(
            "fleet-metrics: unmapped audit_status %r for audit_log %s; "
            "no calculation_finished event emitted.",
            audit_status, audit_log_id,
        )
        return

    if _already_emitted(audit_log_id, outcome):
        return

    emitter.emit(
        {
            "type": "calculation_finished",
            "occurred_at": datetime.now(timezone.utc).isoformat(),
            "audit_log_id": audit_log_id,
            "calculation_id": getattr(audit_log, "calculation_id", None),
            "resource": getattr(audit_log, "resource", None),
            "outcome": outcome,
            "duration_seconds": _duration_seconds(status_row),
            "process": "web" if _running_in_web_process() else "worker",
        }
    )


def _duration_seconds(status_row: Any) -> Optional[float]:
    """``status_row.updated_at - status_row.created_at``, or ``None``.

    ``None`` when ``status_row`` is missing either timestamp, or carries the
    ``_freshly_created`` marker ``ensure_terminal_calculation_audit`` sets
    when it had to create the status row itself in this very call -- see the
    module docstring's "Duration" section.
    """
    if status_row is None:
        return None
    if getattr(status_row, "_freshly_created", False):
        return None
    created_at = getattr(status_row, "created_at", None)
    updated_at = getattr(status_row, "updated_at", None)
    if created_at is None or updated_at is None:
        return None
    return (updated_at - created_at).total_seconds()


def _running_in_web_process() -> bool:
    """True in the uvicorn web process, false everywhere else (a worker).

    Reuses ``running_in_uvicorn()`` (also how ``heartbeat.py`` tells "web"
    from "worker") rather than inventing a second process classifier.
    Imported locally to avoid a circular import at module load time
    (``lex.lex_app.apps`` imports this package's sibling modules during
    ``ready()``).
    """
    from lex.lex_app.apps import running_in_uvicorn

    return running_in_uvicorn()


def _already_emitted(audit_log_id: Any, outcome: str) -> bool:
    """True if ``(audit_log_id, outcome)`` was already emitted; else record it.

    LRU over ``_DEDUPE_MAX_SIZE`` entries: a hit is moved to the back (most
    recently used), a miss is appended and the oldest entry is evicted once
    the cache is over-size.
    """
    key = (audit_log_id, outcome)
    with _dedupe_lock:
        if key in _dedupe_seen:
            _dedupe_seen.move_to_end(key)
            return True
        _dedupe_seen[key] = None
        if len(_dedupe_seen) > _DEDUPE_MAX_SIZE:
            _dedupe_seen.popitem(last=False)
        return False


def _reset_for_tests() -> None:
    """Test-only: clear the in-process de-dupe cache."""
    with _dedupe_lock:
        _dedupe_seen.clear()
