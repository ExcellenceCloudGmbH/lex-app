"""``lex_calculation()`` -- trigger one calculation and watch it, natively.

Replaces embedding a whole React table just to run one record.

Everything here goes through :mod:`lex.lex_app.streamlit._client`, over HTTP, as
the signed-in user. The module deliberately imports no Django model: an
in-process ORM call would skip the record's read permission, resolve the wrong
audit actor and miss the ``_defer_calculate_hook`` trigger path in one step. A
second way to start a calculation is what produced the ``edited_at`` bug in
PR #675, and cluster 1ab pins the import list so it cannot creep back.

Nothing in here may raise. Streamlit renders a page top-to-bottom, so an
exception escaping a widget erases every widget below it: the page does not
report a problem, it silently loses its bottom half. Every failure -- refused,
missing, broken, unreachable -- becomes a state the widget can render.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from lex.lex_app.design_system import ERROR, MUTED, SUCCESS, WARNING
from lex.lex_app.streamlit import _client

#: Statuses that mean work is still running. Everything else stops the poll:
#: nothing about the record will change again until somebody acts on it.
_ACTIVE_STATUSES = {"IN_PROGRESS"}


@dataclass(frozen=True)
class Presentation:
    """How one status is rendered."""

    label: str
    colour: str
    suggests_rerun: bool


@dataclass(frozen=True)
class StatusState:
    """What one status read produced.

    ``status`` is ``None`` when the read itself failed; ``message`` then carries
    what to show the reader instead.
    """

    status: Optional[str]
    error: Optional[str] = None
    message: str = ""
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    duration_seconds: Optional[float] = None
    log: Optional[list] = None
    log_truncated: bool = False


_PRESENTATIONS = {
    "NOT_CALCULATED": Presentation("Not calculated", MUTED, False),
    "IN_PROGRESS": Presentation("Running", WARNING, False),
    "SUCCESS": Presentation("Success", SUCCESS, False),
    "ERROR": Presentation("Error", ERROR, False),
    # Since PR #675 an aborted row is interrupted work, not a failure: the row
    # was left IN_PROGRESS by a restart and swept. "Run it again" is the only
    # action available to the reader, and it is the correct one. Rendering it as
    # an error is what made incident 1410 hard to read -- people went hunting
    # for a failure that had never happened.
    "ABORTED": Presentation("Interrupted", WARNING, True),
    # The user's own doing, so it needs no nudge and is not a fault.
    "CANCELLED": Presentation("Cancelled", MUTED, False),
}


def presentation_for(status: Optional[str]) -> Presentation:
    """Rendering rules for ``status``, falling back to a neutral treatment.

    An unknown status renders its own name rather than nothing: a state added to
    ``CalculationModel`` before this table catches up should look unfamiliar, not
    invisible.
    """
    return _PRESENTATIONS.get(status, Presentation(status or "Unknown", MUTED, False))


def poll_interval_for(status: Optional[str], requested: float) -> Optional[float]:
    """Seconds between polls, or ``None`` when there is nothing left to watch.

    ``None`` for every terminal status, and for a failed read: an idle dashboard
    that kept asking for a status which will never change again is permanent
    backend load, multiplied by every tab anyone left open, and invisible
    because nothing is broken.
    """
    return requested if status in _ACTIVE_STATUSES else None


def poll_timer_needs_rebuild(
    declared: Optional[float], desired: Optional[float],
) -> bool:
    """Whether the browser's auto-rerun timer has to be rebuilt.

    ``st.fragment`` reads ``run_every`` when the fragment is *declared*, and only
    a full script run declares it: a fragment-scoped rerun re-executes the body
    alone, and Streamlit's frontend drops its auto-rerun intervals in
    ``cleanupAutoReruns``, which it calls only for a full run (or a page change).

    So storing a new interval from inside the fragment never reaches the
    browser. Both directions need the script to run again -- a record found
    running would otherwise render "Running" and never poll, and a run that
    finished mid-poll would leave the browser's interval alive for as long as
    the tab stays open. The comparison is what keeps that rerun rare: rebuilding
    when nothing changed would rerun the whole dashboard on every poll, which is
    worse than the polling.
    """
    return declared != desired


def _status_path(model: str, pk) -> str:
    return f"/api/model_entries/{model}/{pk}/calculation-status"


def read_status(model: str, pk, token: str, include_log: bool) -> StatusState:
    """Read calculation state, turning every failure into a renderable state.

    Never raises.
    """
    # "true" as a literal string: the endpoint compares the raw query value
    # against it, so a Python True would arrive as "True", match nothing, and
    # leave the log panel silently empty. Absent entirely when the panel is off,
    # which is what lets the endpoint skip the CalculationLog query -- the
    # expensive half of a poll that repeats every two seconds.
    params = {"include_log": "true"} if include_log else None
    try:
        payload = _client.get_json(_status_path(model, pk), token, params=params)
    except _client.LexApiError as exc:
        return StatusState(status=None, message=_failure_message(exc.status))

    return StatusState(
        status=payload.get("status"),
        error=payload.get("error"),
        started_at=payload.get("started_at"),
        finished_at=payload.get("finished_at"),
        duration_seconds=payload.get("duration_seconds"),
        log=payload.get("log"),
        log_truncated=bool(payload.get("log_truncated")),
    )


def _failure_message(status_code: Optional[int]) -> str:
    if status_code == 403:
        # Deliberately vague. The status endpoint answers 404 for a record the
        # caller may not read, so a 403 here means the *action* was refused --
        # not that the record is hidden.
        return "Not available"
    if status_code == 404:
        return "Record not found"
    return "Status unavailable"


def trigger_calculation(model: str, pk, token: str) -> Optional[str]:
    """Start the calculation. Returns a message to render, or ``None`` on success.

    The exact call the React UI makes, so permissions, audit actor and history
    are identical to a run started from the table.

    ``calculate`` travels in the request *body*: ``One.update`` reads it from
    ``request.data`` and never looks at the query string, so a flag parked in
    the URL is dropped and the PATCH degrades into an empty partial update --
    no calculation, no error, a button that appears to do nothing.
    """
    path = f"/api/model_entries/{model}/default/one/{pk}"
    try:
        _client.patch_json(path, token, json={"calculate": "true"})
    except _client.LexApiError as exc:
        if exc.status == 403:
            return "You don't have permission to run this"
        return str(exc)
    return None
