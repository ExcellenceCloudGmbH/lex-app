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

Three properties make a page of these feel like a page rather than a queue, and
all three are about when a script run happens and what it costs. Measured on
thirteen tiles over two records, which is the page that exposed them:

* **One read per record per run** (13 GETs on load -> 2). A status read is a
  blocking HTTP GET inside the render, so widgets showing the same record used
  to wait through each other's round trips in series. :func:`read_status_cached`
  shares one answer, out of ``st.session_state`` -- per-user, because a status
  envelope describes a record the next reader may not be allowed to see.
* **The timer is decided before the fragment is declared** (a click: 14 script
  runs and 104 GETs -> 2 and 1). ``run_every`` is read only at declaration, so a
  widget that discovers *inside* its fragment that it should be polling can only
  get a timer by rerunning the whole script -- and ``st.rerun()`` raises, so
  every widget below it lost its turn and asked for a rerun of its own next time
  round. Reading first makes one pass enough, and leaves exactly two reruns per
  calculation: the one that starts the watch and the one that ends it.
* **A click renders its own answer.** "Running" appears when the trigger
  succeeds, not when a later read agrees -- see :func:`optimistic_status`.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from html import escape
from typing import Optional, Tuple

import streamlit as st

from lex.lex_app.design_system import ERROR, MUTED, SUCCESS, WARNING
from lex.lex_app.streamlit import _client

#: Statuses that mean work is still running. Everything else stops the poll:
#: nothing about the record will change again until somebody acts on it.
_ACTIVE_STATUSES = {"IN_PROGRESS"}

#: What an optimistic render claims once a trigger has succeeded. Not a guess:
#: ``One.update`` sets ``is_calculated = IN_PROGRESS`` and saves it *before* it
#: answers the PATCH, so by the time the click handler holds a response this is
#: already what the record is. Rendering it is declining to ask again, not
#: predicting.
_OPTIMISTIC_STATUS = "IN_PROGRESS"

#: Longest, in seconds, that one status read may stand in for another.
#: :func:`status_cache_max_age` shortens it further for a fast poll, so a poll
#: can never be answered with the value it was sent to check.
STATUS_CACHE_TTL = 1.0

#: Session-state slot holding this session's status cache. One key, so a
#: dashboard inspecting ``st.session_state`` finds one opaque entry rather than
#: one per record.
_CACHE_STATE_KEY = "_lex_calculation_status_cache"

#: Shown when this caller may not run the record: beside the disabled button when
#: the status envelope said so up front, and in place of a backend message when a
#: click got through to a 403 anyway. One constant, so the two cannot drift into
#: describing the same refusal differently.
NO_PERMISSION_MESSAGE = "You don't have permission to run this"


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

    ``can_calculate`` is the backend's answer to "may this caller trigger a run
    on this record", not the widget's own judgement -- it defaults to ``True``
    because the widget must never be the thing that refuses.
    """

    status: Optional[str]
    error: Optional[str] = None
    message: str = ""
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    duration_seconds: Optional[float] = None
    log: Optional[list] = None
    log_truncated: bool = False
    can_calculate: bool = True
    calculate_denied_reason: Optional[str] = None


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
        # Only an explicit ``false`` disables the button. A missing key is an
        # endpoint older than the flag, not a refusal -- reading absence as
        # "denied" would disable the button for every user of a deployment that
        # has not shipped the endpoint yet, with no way to press through it.
        can_calculate=payload.get("can_calculate", True) is not False,
        calculate_denied_reason=payload.get("calculate_denied_reason"),
    )


def status_cache_max_age(poll_interval: float) -> float:
    """How long this widget may reuse somebody else's status read.

    Capped at :data:`STATUS_CACHE_TTL`, and never more than half a poll
    interval. The second bound is what keeps the cache from answering the very
    question it was built to make cheap: a poll exists to find out whether the
    status changed, so serving it a value read less than one interval ago would
    freeze the badge on a running calculation. Halving leaves room for a slow
    render without ever reaching the next tick.
    """
    try:
        interval = float(poll_interval)
    except (TypeError, ValueError):
        interval = 0.0
    return max(0.0, min(STATUS_CACHE_TTL, interval / 2.0))


def _status_cache() -> dict:
    """This session's status cache -- and only this session's.

    It lives in ``st.session_state``, which Streamlit scopes to a single browser
    session, meaning a single signed-in user. That is the entire guarantee, and
    it is deliberately the boring one: there is no module-level dict and no
    ``st.cache_data``/``st.cache_resource`` anywhere in this module, because
    both are shared by every user of the server.

    The stake is not a stale badge. The status endpoint answers a record the
    caller may not read with the same 404 as a record that does not exist,
    precisely so that its existence is not disclosed -- and a cache reaching
    across sessions would hand over the answer that machinery exists to
    withhold. The bearer token is deliberately *not* part of the key: keying by
    it is what a cross-session cache would need to be safe, and a cross-session
    cache is exactly what is being refused. Adding the token here would look
    like a defence and would instead park one user's credential in a structure
    another user's request can reach.

    Never raises: ``session_state`` is unavailable without a script context, and
    a widget helper must not turn that into a page-wide exception. The throwaway
    dict returned in that case simply means nothing is cached.
    """
    try:
        cache = st.session_state.setdefault(_CACHE_STATE_KEY, {})
    except Exception:  # pragma: no cover - depends on Streamlit's runtime state
        return {}
    if not isinstance(cache, dict):  # pragma: no cover - defensive
        cache = {}
        st.session_state[_CACHE_STATE_KEY] = cache
    return cache


def _cache_key(model: str, pk, include_log: bool) -> tuple:
    """Everything that changes the answer, and nothing that does not.

    ``include_log`` belongs here because the endpoint omits the log keys
    entirely without it: a log-less envelope handed to a widget that asked for
    the log would leave its panel permanently empty, with nothing anywhere to
    explain why. ``pk`` is normalised because ``4`` and ``"4"`` name one record.
    """
    return (model, str(pk), bool(include_log))


def read_status_cached(
    model: str, pk, token: str, include_log: bool, max_age: float,
) -> Tuple[StatusState, bool]:
    """A status read, shared with every widget on the page watching this record.

    Returns the state and whether it was reused. Callers care about the second
    value because a fresh read is the only thing that can settle an optimistic
    render: it necessarily happened after the trigger it is checking.

    Failed reads are cached too. Thirteen widgets meeting an unreachable backend
    should cost one failed round trip, not thirteen -- and a backend that is
    down is exactly when a page must not multiply its own retries.
    """
    cache = _status_cache()
    key = _cache_key(model, pk, include_log)
    now = time.monotonic()

    entry = cache.get(key)
    if entry is not None and (now - entry[0]) <= max_age:
        return entry[1], True

    state = read_status(model, pk, token, include_log)
    cache[key] = (now, state)
    return state, False


def invalidate_status(model: str, pk) -> None:
    """Forget every cached read of one record.

    Called the moment a trigger succeeds: the record just changed, and the whole
    point of a cache measured in fractions of a second is that it never stands
    between an action and its consequence.
    """
    cache = _status_cache()
    for key in [k for k in cache if k[0] == model and k[1] == str(pk)]:
        cache.pop(key, None)


def optimistic_status(status: Optional[str], expecting_run: bool) -> Optional[str]:
    """The status to render, honouring a trigger that has already succeeded.

    A reader who presses Calculate has to be told something. Waiting for the
    next status read before admitting anything happened is how a click came to
    take seven seconds to change a word -- and for those seven seconds the badge
    still said "Not calculated", so the honest reading of the page was that the
    button had not worked.

    A failed read is left alone. There is no badge to override in that case: the
    widget renders the read's own message instead, and claiming a run is under
    way beneath "Status unavailable" would describe a record nobody can see.
    """
    if not expecting_run or status is None:
        return status
    return _OPTIMISTIC_STATUS


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

    The 403 branch stays even though the status envelope now reports the same
    permission up front: the envelope is one poll old, and this call is the only
    thing actually being authorised. Losing this branch would turn a refusal that
    arrived late into a button that silently does nothing.
    """
    path = f"/api/model_entries/{model}/default/one/{pk}"
    try:
        _client.patch_json(path, token, json={"calculate": "true"})
    except _client.LexApiError as exc:
        if exc.status == 403:
            return NO_PERMISSION_MESSAGE
        return str(exc)
    return None


def widget_key_for(model: str, pk, key: Optional[str] = None) -> str:
    """The namespace every piece of this widget's state hangs off.

    Derived from the record by default, so two widgets watching two records on
    one page never share a slot. ``key`` is what makes the *same* record
    renderable twice on one page -- without it Streamlit rejects the second
    button as a duplicate widget ID, and either widget's status would decide the
    other's poll interval.
    """
    return key or f"lex_calc_{model}_{pk}"


def _every_key(widget_key: str) -> str:
    return f"{widget_key}__every"


def _pending_key(widget_key: str) -> str:
    return f"{widget_key}__pending"


def _session_token() -> Optional[str]:
    """The signed-in user's access token, as the Streamlit host holds it.

    ``lex/streamlit_app.py`` writes and refreshes ``session_state["access_token"]``
    (see ``_update_tokens_from_response`` / ``ensure_valid_access_token``), so
    reading that key means the widget always uses the freshest token the host
    has, including one just renewed by the background refresher.

    Deliberately no environment fallback: the instance's API key is a
    machine-to-machine secret sent as ``Api-Key``, resolving to a technical
    user. As a bearer token it would not authenticate, and if it did it would
    detach the run from the person who asked for it.

    Never raises -- ``session_state`` is unavailable when there is no script
    context (an import-time scan, a worker thread), and a widget helper must not
    turn that into a page-wide exception.
    """
    try:
        token = st.session_state.get("access_token") or ""
    except Exception:  # pragma: no cover - depends on Streamlit's runtime state
        return None
    return token.strip() or None


def last_run_caption(state: StatusState) -> str:
    """One line describing the record's last run.

    "Never run" rather than a zeroed duration when the endpoint reports null
    timings: a record with no log rows has not run, and rendering that as
    "took 0s" claims a run that never happened.
    """
    stamp = state.finished_at or state.started_at
    if not stamp:
        return "Never run"
    if state.duration_seconds is None:
        return f"Last run: {stamp}"
    return f"Last run: {stamp} (took {state.duration_seconds:g}s)"


def lex_calculation(
    model: str,
    pk,
    *,
    label: str = "Calculate",
    show_status: bool = True,
    show_last_run: bool = True,
    show_error: bool = True,
    show_log: bool = False,
    poll_interval: float = 2.0,
    key: Optional[str] = None,
) -> Optional[dict]:
    """Render a calculate button and live status for one record.

    Returns the latest status envelope, or ``None`` before the first successful
    read, so the dashboard can branch on it::

        status = lex_calculation("quarter", pk=42)
        if status and status["status"] == "SUCCESS":
            st.dataframe(load_results())

    Pass ``key`` when the same record appears more than once on a page.
    """
    widget_key = widget_key_for(model, pk, key)
    token = _session_token()

    if not token:
        # Not an error state: an unauthenticated call would come back 401 and be
        # reported as "status unavailable", pointing the reader at the backend
        # for something a page reload fixes.
        st.warning("Session expired — reload the page to sign in again.")
        return None

    every_key = _every_key(widget_key)
    pending_key = _pending_key(widget_key)
    max_age = status_cache_max_age(poll_interval)

    # Read the status *before* declaring the fragment, not inside it. run_every
    # is read only at declaration, and only a full script run declares one, so a
    # widget that learns it should be polling from inside its own fragment can
    # obtain a timer in one way only: rerun the entire script. That rerun was
    # never cheap. st.rerun() raises, so the exception travelled out through
    # every widget still to be drawn -- they never rendered, discovered their
    # own running records on the next run, and each asked for a rerun of its
    # own. A page of thirteen tiles cost fourteen script runs and 104 reads for
    # one click. Knowing by now is what makes one pass enough; the read costs
    # nothing extra because the fragment reuses it below.
    prelude, from_cache = read_status_cached(
        model, pk, token, include_log=show_log, max_age=max_age,
    )
    if not from_cache and st.session_state.get(pending_key):
        # A read the click cannot have preceded, so it outranks the optimism --
        # which exists to cover exactly one round trip, not to become a second
        # opinion about the record.
        st.session_state[pending_key] = False

    declared = poll_interval_for(
        optimistic_status(prelude.status, bool(st.session_state.get(pending_key))),
        poll_interval,
    )
    st.session_state[every_key] = declared
    state_box: dict = {}

    @st.fragment(run_every=declared)
    def _render():
        expecting_run = bool(st.session_state.get(pending_key))
        state, _ = read_status_cached(
            model, pk, token, include_log=show_log, max_age=max_age,
        )
        state_box["state"] = state
        status = optimistic_status(state.status, expecting_run)

        if status is None:
            st.caption(state.message)
        else:
            # Two independent reasons to refuse a click, and both have to hold:
            # a record already running must not be triggered a second time, and
            # a caller the backend will refuse should not have to press the
            # button to find that out. Neither is a decision made here --
            # ``blocked`` is the server's own answer, carried in the envelope.
            blocked = not state.can_calculate

            left, right = st.columns([1, 2])
            with left:
                if st.button(
                    label,
                    key=f"{widget_key}__btn",
                    disabled=status in _ACTIVE_STATUSES or blocked,
                ):
                    failure = trigger_calculation(model, pk, token)
                    if failure:
                        # Nothing was started, so nothing may be claimed:
                        # rendering "Running" over a refusal would leave the
                        # reader watching a calculation that does not exist.
                        st.session_state[pending_key] = False
                        st.error(failure)
                    else:
                        # The record is IN_PROGRESS as of this response -- the
                        # PATCH sets and saves it before answering -- so say so
                        # now rather than after another read agrees.
                        st.session_state[pending_key] = True
                        invalidate_status(model, pk)
                        status = _OPTIMISTIC_STATUS

            # After the button, so a click is reflected in the same render that
            # handled it. This is the whole of the optimistic behaviour.
            look = presentation_for(status)
            with right:
                if show_status:
                    # escape(): an unrecognised status is rendered verbatim, and
                    # this is the one place widget text reaches the page as HTML.
                    st.markdown(
                        "<span style='color:{colour};font-weight:600'>{label}</span>".format(
                            colour=look.colour, label=escape(look.label),
                        ),
                        unsafe_allow_html=True,
                    )
                if blocked:
                    # Beside the button, not somewhere below it: a disabled
                    # control with no explanation reads as a broken dashboard,
                    # and the reason is the whole reason to disable it up front.
                    # st.caption does not render HTML, so this text -- which
                    # comes from the backend -- needs no escaping.
                    st.caption(state.calculate_denied_reason or NO_PERMISSION_MESSAGE)

            if look.suggests_rerun:
                st.caption("This run was interrupted — run it again.")
            if show_last_run:
                st.caption(last_run_caption(state))
            if show_error and state.error:
                st.error(state.error)
            if show_log and state.log:
                st.code("\n".join(state.log), language=None)
                if state.log_truncated:
                    st.caption("Showing the most recent lines only.")

        state_box["status"] = status
        desired = poll_interval_for(status, poll_interval)
        st.session_state[every_key] = desired
        if poll_timer_needs_rebuild(declared, desired):
            # App scope, not fragment scope: only a full script run re-declares
            # the fragment, and only a full script run clears the browser's
            # existing auto-rerun interval. st.rerun(scope="fragment") would do
            # neither -- and raises outright during a full-app run.
            #
            # Reachable from two moments only, both of which the reader can
            # account for. On a full run the interval was decided from this same
            # read before the fragment was declared, so the two already agree
            # and nothing fires. What is left is a fragment-only rerun: the
            # click that starts a run and needs a timer built, and the poll that
            # finds the run over and needs it taken down. Two greys per
            # calculation, and no widget but this one is mid-render when either
            # happens.
            st.rerun()

    _render()

    state = state_box.get("state")
    status = state_box.get("status")
    if state is None or status is None:
        return None
    # The status the badge is showing, so a dashboard branching on the return
    # value cannot render finished results underneath a "Running" badge.
    return {
        "status": status,
        "error": state.error,
        "started_at": state.started_at,
        "finished_at": state.finished_at,
        "duration_seconds": state.duration_seconds,
    }
