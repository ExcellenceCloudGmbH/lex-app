"""Builds and emits the ``calculation_finished`` fleet-metrics event.

Every root calculation ends in one of four terminal states: ``success`` ->
``SUCCESS``, ``failure`` -> ``ERROR``, ``cancelled`` -> ``CANCELLED``,
``aborted`` -> ``ABORTED`` (the same four strings ``ensure_terminal_
calculation_audit`` writes to ``AuditLogStatus.status``). This module is
called from exactly one place -- that function, inside its own ``guard()``
-- so every terminal path converges here without any extra root-run logic.

The finaliser sometimes runs twice for one root run in the same process (a
cancel can reach both ``CalculationModel`` and ``LexModel._finalize_pending_
terminal_audit``), so this module de-dupes on ``(audit_log.pk, outcome)``.
A duplicate across two *different* processes (a Celery worker and the web
process both finalising the same run) is not de-duped here; the controller's
ingest endpoint keeps one row per ``(instance_id, audit_log_id)`` instead.
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

_OUTCOME_BY_AUDIT_STATUS: Dict[str, str] = {
    "success": "SUCCESS",
    "failure": "ERROR",
    "cancelled": "CANCELLED",
    "aborted": "ABORTED",
}

_DEDUPE_MAX_SIZE = 10_000

_dedupe_lock = threading.Lock()
# (audit_log.pk, outcome) keys already emitted in this process, insertion
# ordered so it can double as an LRU: a repeat moves to the back, a miss is
# appended, the oldest key is evicted once the cache is over-size.
_dedupe_seen: "OrderedDict[Tuple[Any, str], None]" = OrderedDict()


def emit_calculation_finished(audit_log: Any, status_row: Any, audit_status: str) -> None:
    """Queue one ``calculation_finished`` event for a terminal root run.

    A no-op when usage metrics are off, ``audit_log`` has no pk yet,
    ``audit_status`` is unmapped, or this ``(pk, outcome)`` was already sent
    by this process. The deployed-instance gate and delivery itself are
    ``emitter``'s job -- this function only ever calls ``emitter.emit``,
    which never raises.
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
    when it had to create the status row itself -- no earlier 'pending' row
    existed to time the run from, so the start time is unknown, not zero.
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
    """True in the uvicorn web process, false otherwise (a worker).

    Reuses ``running_in_uvicorn()`` (also how ``heartbeat.py`` tells "web"
    from "worker"). Imported locally: ``lex.lex_app.apps`` imports this
    package's sibling modules during ``ready()``, so an eager top-level
    import here would risk a circular import.
    """
    from lex.lex_app.apps import running_in_uvicorn

    return running_in_uvicorn()


def _already_emitted(audit_log_id: Any, outcome: str) -> bool:
    """True if ``(audit_log_id, outcome)`` was already emitted; else record it."""
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
