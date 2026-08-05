"""``lex_calculation_streamlit()`` -- trigger one calculation and watch it, natively.

Renders the trigger and its live status as ordinary Streamlit elements: no
iframe, no second React runtime per tile, a Python return value the rest of the
page can branch on, and it flows with the page instead of living in a
fixed-height box. That is what makes a *dashboard of tiles* viable.

:func:`lex.lex_app.streamlit.lex_calculation` is the other half of the choice:
it embeds the lex-app frontend's own record view, so the fields and their
formatting are the product's and stay the product's. Reach for that when you
want the record; reach for this when you want the trigger.

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

**The render path performs no I/O and reruns nothing but itself.** That single
sentence is the design, and both complaints this widget drew were a violation of
one half of it.

A render that fetches does not "load slowly": Streamlit runs the script on one
thread, so a tile blocking on a status read holds up every tile below it, in
series, before any of them exist. Reads therefore belong to
:mod:`lex.lex_app.streamlit._status_poller`, which does them on a thread the
session owns; :func:`lex_calculation_streamlit` only ever looks up what is
already known.
The click follows the same rule -- ``One.update`` re-reads the record, clears
terminal state, saves, registers the calculation and broadcasts it before
answering, and doing that inside the click handler is exactly why pressing
Calculate felt like nothing had happened.

A render that reruns the *app* stops being local. Streamlit greys every element
it has not yet redrawn, so one tile asking for a full run greys the other twelve
-- and each of those was blocking on its own read, which is what turned one
click into seven seconds of a dead page. The poll timer is therefore declared
unconditionally and never rebuilt: ``run_every`` is read only when a fragment is
declared, and only a full script run declares one, so *any* design that adapts
the interval must rerun the page to do it. Holding the timer permanently costs a
redraw of a dictionary lookup every second or so, which is measurably nothing --
thirteen tiles ticking at 1 Hz, with a click among them, produced zero stale
elements and one full app run. Adapting it costs a page.
"""

from __future__ import annotations

from html import escape
from typing import Optional

import streamlit as st

from lex.lex_app.design_system import ERROR, MUTED, SUCCESS, WARNING
from lex.lex_app.streamlit._status_poller import (
    ACTIVE_STATUSES,
    NO_PERMISSION_MESSAGE,
    StatusState,
    get_poller,
    read_status,
    trigger_calculation,
)

__all__ = [
    "lex_calculation_streamlit",
    "NO_PERMISSION_MESSAGE",
    "StatusState",
    "presentation_for",
    "poll_interval_for",
    "optimistic_status",
    "last_run_caption",
    "ui_refresh_for",
    "widget_key_for",
    "read_status",
    "trigger_calculation",
]

#: What an optimistic render claims once a trigger has been accepted. Not a
#: guess: ``One.update`` sets ``is_calculated = IN_PROGRESS`` and saves it
#: *before* it answers the PATCH, so this is what the record becomes. Rendering
#: it is declining to wait for confirmation, not predicting.
_OPTIMISTIC_STATUS = "IN_PROGRESS"

#: Bounds on how often a tile redraws itself. The redraw issues no request -- it
#: reads the poller's last answer out of a dictionary -- so the interval is
#: chosen for how quickly a change should surface, not for what it costs. Kept
#: off zero so a page of tiles cannot saturate the websocket, and off the long
#: end so a finished calculation is never left showing "Running".
UI_REFRESH_FLOOR = 0.5
UI_REFRESH_CEILING = 2.0


class Presentation:
    """How one status is rendered."""

    __slots__ = ("label", "colour", "suggests_rerun")

    def __init__(self, label: str, colour: str, suggests_rerun: bool):
        self.label = label
        self.colour = colour
        self.suggests_rerun = suggests_rerun

    def __eq__(self, other):  # pragma: no cover - convenience for assertions
        return (
            isinstance(other, Presentation)
            and (self.label, self.colour, self.suggests_rerun)
            == (other.label, other.colour, other.suggests_rerun)
        )

    def __repr__(self):  # pragma: no cover - convenience for assertions
        return f"Presentation({self.label!r}, {self.colour!r}, {self.suggests_rerun!r})"


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

#: Before the first read lands. A tile appears the moment the page does, which
#: means it appears before anything is known about the record.
#:
#: Deliberately an ellipsis rather than a word. "Checking…" was read as a
#: *status* -- thirteen tiles all announcing they were checking looked like a
#: page that could not reach its backend, when in fact nothing had gone wrong and
#: nothing had yet been asked. A placeholder should hold the space the status
#: will occupy and say nothing, which is the truth at that moment.
CHECKING = Presentation("—", MUTED, False)

#: Between the click and the first read that confirms it. Distinct from
#: "Running" on purpose: the reader pressed a button and is owed an
#: acknowledgement that names their action, not a status that happens to be
#: correct. They also read very differently if the trigger is refused a moment
#: later -- "Starting…" turning into an error is a story; "Running" turning into
#: an error is a contradiction.
STARTING = Presentation("Starting…", WARNING, False)


def presentation_for(status: Optional[str]) -> Presentation:
    """Rendering rules for ``status``, falling back to a neutral treatment.

    An unknown status renders its own name rather than nothing: a state added to
    ``CalculationModel`` before this table catches up should look unfamiliar, not
    invisible.
    """
    return _PRESENTATIONS.get(status, Presentation(status or "Unknown", MUTED, False))


def poll_interval_for(status: Optional[str], requested: float) -> Optional[float]:
    """Seconds between *backend reads*, or ``None`` when nothing will change.

    ``None`` for every terminal status and for a failed read: an idle dashboard
    that kept asking for a status which will never change again is permanent
    backend load, multiplied by every tab anyone left open, and invisible because
    nothing is broken.

    This governs the poller thread only. It is deliberately not the rate at which
    the tile redraws -- see :func:`ui_refresh_for`, and the module preamble for
    why conflating the two cost a page rerun per calculation.
    """
    return requested if status in ACTIVE_STATUSES else None


def ui_refresh_for(poll_interval: float) -> float:
    """How often a tile redraws itself, given how often its record is read.

    Half the read interval, bounded: a redraw slower than the reads behind it
    would leave a finished calculation showing "Running" for up to a whole extra
    interval, and one much faster would spend websocket traffic re-rendering an
    answer that cannot have moved.

    Never ``None``. A tile that dropped its timer when its record settled could
    not get one back -- ``run_every`` is fixed when the fragment is declared, and
    only a full script run declares one -- so it would have to rerun the whole
    page on the next click just to start watching again. That rerun is the
    behaviour this widget was rewritten to remove.
    """
    try:
        interval = float(poll_interval)
    except (TypeError, ValueError):
        interval = 2.0
    return max(UI_REFRESH_FLOOR, min(UI_REFRESH_CEILING, interval / 2.0))


def optimistic_status(status: Optional[str], expecting_run: bool) -> Optional[str]:
    """The status to render, honouring a trigger that has been accepted.

    A reader who presses Calculate has to be told something. Waiting for a read
    to agree before admitting anything happened is how a click came to take
    seconds to change a word -- and for those seconds the badge still said "Not
    calculated", so the honest reading of the page was that the button had not
    worked.

    A failed read is left alone. There is no badge to override in that case: the
    widget renders the read's own message instead, and claiming a run is under
    way beneath "Status unavailable" would describe a record nobody can see.
    """
    if not expecting_run or status is None:
        return status
    return _OPTIMISTIC_STATUS


def widget_key_for(model: str, pk, key: Optional[str] = None) -> str:
    """The namespace this widget's Streamlit elements hang off.

    Derived from the record by default, so two widgets watching two records on
    one page never collide. ``key`` is what makes the *same* record renderable
    twice on one page -- without it Streamlit rejects the second button as a
    duplicate widget ID.
    """
    return key or f"lex_calc_{model}_{pk}"


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


def _envelope(state: StatusState, status: Optional[str]) -> dict:
    return {
        "status": status,
        "error": state.error,
        "started_at": state.started_at,
        "finished_at": state.finished_at,
        "duration_seconds": state.duration_seconds,
    }


def lex_calculation_streamlit(
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
    sync_page: bool = False,
) -> Optional[dict]:
    """Render a calculate button and live status for one record.

    Returns the latest status envelope, or ``None`` before the first read has
    landed, so the dashboard can branch on it::

        status = lex_calculation_streamlit("quarter", pk=42)
        if status and status["status"] == "SUCCESS":
            st.dataframe(load_results())

    ``poll_interval`` is how often the *backend* is asked, not how often the tile
    redraws; the tile keeps itself current on its own (:func:`ui_refresh_for`)
    and stops asking once the record settles.

    ``sync_page`` re-runs the whole page when this record's status changes, which
    is what the example above needs: the tile updates itself in place, but code
    *outside* it -- the ``st.dataframe`` -- is only re-evaluated by a page run.
    Off by default, because a page run greys every element until it is redrawn
    and a dashboard of tiles should not flicker because one of them finished.
    Turn it on for the one tile whose result the page is built around, not for
    all of them.

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

    poller = get_poller()
    poller.set_token(token)
    poller.watch(model, pk, include_log=show_log, interval=poll_interval)

    # Dictionary lookups, not requests. Nothing on this path reaches the
    # backend, which is what lets a tile be declared and drawn in the time it
    # takes to look up two keys, however many tiles precede it.
    seen_key = f"{widget_key}__page_status"
    primed_key = f"{widget_key}__primed"
    st.session_state[seen_key] = _visible_status(poller, model, pk, show_log)
    if _has_status(poller.peek(model, pk, show_log)):
        # Already known when the page ran, so the dashboard below has the real
        # value and there is nothing for the priming rerun to fix. Marking it
        # here is what keeps a page that reruns for its own reasons -- a filter
        # changed, a button elsewhere -- from paying for one it does not need.
        st.session_state[primed_key] = True
    state_box: dict = {}

    # True only while the script is running this widget for the first time on
    # this pass; the fragment closes over it and Streamlit's later ticks see
    # False. Nothing below may call st.rerun() while it is True.
    #
    # st.rerun() raises, so one fired during a full script run takes every
    # widget still to be drawn with it -- the cascade that once turned a single
    # click into fourteen script runs. A session_state flag set just above is
    # *not* enough to prevent it: the poller fills its snapshots on another
    # thread, so a record can go from unknown to known in the gap between that
    # check and the fragment body, and a page of tiles hits that gap constantly.
    # Only the call stack knows which kind of run this is, so that is what is
    # asked.
    declaring = {"now": True}

    @st.fragment(run_every=ui_refresh_for(poll_interval))
    def _render():
        state = poller.peek(model, pk, show_log)
        pending = poller.is_pending(model, pk)
        status = optimistic_status(state.status if state else None, pending)

        if state is None:
            # The first read has not landed. Show the control rather than a
            # spinner: the button is enabled because nothing yet says otherwise,
            # and the module's rule is that the widget is never the thing that
            # refuses. A click that beats the first read is safe -- ``One.update``
            # guards a record that is already running and answers 200 without
            # starting a second one.
            left, right = st.columns([1, 2])
            with left:
                clicked = _button(label, widget_key, disabled=False)
            with right:
                _badge(STARTING if (clicked or pending) else CHECKING, show_status)
            if clicked:
                poller.request_trigger(model, pk)
                status = _OPTIMISTIC_STATUS
        elif status is None:
            # Every read of this record has failed, so there is nothing to draw
            # a badge or a button from. Reached only when the *first* read
            # failed: once one has succeeded, a later failure keeps the answer
            # it produced and becomes the caption below instead.
            st.caption(state.message)
        else:
            status = _tile(
                state=state,
                status=status,
                pending=pending,
                label=label,
                widget_key=widget_key,
                poller=poller,
                model=model,
                pk=pk,
                show_status=show_status,
                show_last_run=show_last_run,
                show_error=show_error,
                show_log=show_log,
            )

        lapse = poller.lapse(model, pk, show_log)
        if lapse and status is not None:
            # A footnote, deliberately, and only when there is a status to
            # footnote. The record has not changed -- our view of it has lapsed,
            # and that is a much smaller thing to say than replacing a status
            # the reader has been watching with an error.
            st.caption(lapse)

        failure = poller.trigger_error(model, pk)
        if failure:
            st.error(failure)

        state_box["state"] = state
        state_box["status"] = status

        if declaring["now"]:
            # Whatever else is true, this pass may not rerun the page.
            return

        if _has_status(state) and not st.session_state.get(primed_key):
            # The first answer for this record has just landed, and it landed
            # after the page run that returned ``None`` to the dashboard. One
            # run here is what makes the documented pattern -- branch on the
            # value the widget returns -- true on a freshly opened page rather
            # than only after the reader happens to interact with something.
            # Once per record per session; thirteen tiles asking within the same
            # moment coalesce into a single run.
            #
            # Keyed on there being a *status*, not merely a snapshot. A first
            # read that failed is a snapshot too, and spending the one priming
            # run on it left the widget returning None to the dashboard for the
            # rest of the session -- the retry that finally succeeded had no run
            # left to announce itself with.
            st.session_state[primed_key] = True
            st.rerun()

        if sync_page and status != st.session_state.get(seen_key):
            # Opt-in, and at most once per change: the page is re-run only so
            # that code *outside* this tile sees the new status. Guarded by the
            # status recorded during the last page run, so a tile redrawing on
            # its own timer with nothing new to say never triggers one.
            st.session_state[seen_key] = status
            st.rerun()

    try:
        _render()
    finally:
        # Even if the render raised, later ticks of this fragment are ticks.
        declaring["now"] = False

    state = state_box.get("state")
    if state is None:
        return None
    # The status the badge is showing, so a dashboard branching on the return
    # value cannot render finished results underneath a "Running" badge.
    return _envelope(state, state_box.get("status"))


def _has_status(state) -> bool:
    """Whether a snapshot carries a status the dashboard can branch on.

    A failed read produces a state with no status. It is worth rendering -- the
    reader is owed the message -- but it is not an answer about the record, and
    treating it as one is what let a single failed first read silence a widget's
    return value permanently.
    """
    return state is not None and state.status is not None


def _visible_status(poller, model: str, pk, include_log: bool) -> Optional[str]:
    state = poller.peek(model, pk, include_log)
    return optimistic_status(
        state.status if state else None, poller.is_pending(model, pk),
    )


def _badge(look: Presentation, show_status: bool) -> None:
    if not show_status:
        return
    # escape(): an unrecognised status is rendered verbatim, and this is the one
    # place widget text reaches the page as HTML.
    st.markdown(
        "<span style='color:{colour};font-weight:600'>{label}</span>".format(
            colour=look.colour, label=escape(look.label),
        ),
        unsafe_allow_html=True,
    )


def _button(label: str, widget_key: str, *, disabled: bool) -> bool:
    return st.button(label, key=f"{widget_key}__btn", disabled=disabled)


def _tile(
    *, state, status, pending, label, widget_key, poller, model, pk,
    show_status, show_last_run, show_error, show_log,
) -> Optional[str]:
    """One record's button, badge and detail, in that order.

    Returns the status actually rendered, which a click makes differ from the one
    passed in. The button is drawn before the badge so that difference lands in
    the same render that handled the click -- that ordering is the whole of the
    optimistic behaviour.
    """
    # Two independent reasons to refuse a click, and both have to hold: a record
    # already running must not be triggered a second time, and a caller the
    # backend will refuse should not have to press the button to find that out.
    # Neither is a decision made here -- ``blocked`` is the server's own answer,
    # carried in the envelope.
    blocked = not state.can_calculate

    left, right = st.columns([1, 2])
    with left:
        if _button(label, widget_key, disabled=status in ACTIVE_STATUSES or blocked):
            # Dispatched, not awaited: the PATCH goes out on the poller's
            # trigger thread, so this render finishes now and acknowledges the
            # press in the same pass that handled it.
            poller.request_trigger(model, pk)
            status = _OPTIMISTIC_STATUS
            pending = True

    # "Starting…" until a read confirms the run, then "Running". Both are
    # honest -- ``One.update`` saves IN_PROGRESS before it answers -- but only
    # the first is an answer to what the reader just did, and a button that
    # produces no word of its own is the definition of feeling unresponsive.
    look = STARTING if (pending and status == _OPTIMISTIC_STATUS) else presentation_for(status)
    with right:
        _badge(look, show_status)
        if blocked:
            # Beside the button, not somewhere below it: a disabled control with
            # no explanation reads as a broken dashboard, and the reason is the
            # whole reason to disable it up front. st.caption does not render
            # HTML, so this text -- which comes from the backend -- needs no
            # escaping.
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
    return status
