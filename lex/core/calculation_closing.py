"""Closed calculation records.

A model says a record must not be calculated again by overriding
``calculation_closed_reason()`` (on ``CalculationModel`` and
``CalculatedModelMixin``). Every path that would start a calculation asks
:func:`calculation_closed_reason` first. The paths that cannot answer the user
directly (a calculation started by another calculation, a generated batch row)
record the skip with :func:`report_skipped`.
"""
import logging
from typing import Optional

logger = logging.getLogger(__name__)

#: The message when ``calculation_closed_reason()`` answers ``True`` rather than a reason.
DEFAULT_CLOSED_REASON = "This record is closed, so it can't be calculated again."


def calculation_closed_reason(record) -> Optional[str]:
    """Why ``record`` must not be calculated, or ``None`` when it may be."""
    method = getattr(record, "calculation_closed_reason", None)
    if not callable(method):
        return None
    reason = method()
    if not reason:
        return None
    return reason if isinstance(reason, str) else DEFAULT_CLOSED_REASON


def report_skipped(record, reason: str) -> None:
    """Record that ``record`` was not calculated because it is closed.

    Inside another calculation, the line also goes into that calculation's log,
    so the person reading it sees why this record was left alone.
    """
    message = f"{record} was not calculated: {reason}"
    logger.info(message)

    from lex.core.models.CalculationModel import _in_calculation_execution

    if not _in_calculation_execution.get():
        return
    try:
        from lex.audit_logging.handlers.LexLogger import LexLogger

        LexLogger().add_text(message).log()
    except Exception:
        logger.debug("Could not write the skip of %s to the calculation log", record, exc_info=True)


def skip_closed(record) -> bool:
    """``True``, once reported, when ``record`` is closed and must be skipped."""
    reason = calculation_closed_reason(record)
    if not reason:
        return False
    report_skipped(record, reason)
    return True
