"""Every backend call the calculation widget makes, off the render thread.

Streamlit renders a page by running a script top to bottom on one thread. A
widget that reads its status with a blocking HTTP GET therefore does not "load
slowly" -- it holds up every widget below it, in series, before any of them
exist. Thirteen tiles over six distinct records made the page paint over several
seconds, and the seconds were spent waiting, not rendering.

So nothing here is called from a render. The widget registers what it wants
watched and reads whatever answer is already in hand; a daemon thread owned by
the session does the waiting. The render path becomes a dictionary lookup, which
is what lets the widget hold its poll timer permanently and never rerun the page
-- see :mod:`lex.lex_app.streamlit.calculation` for why that matters.

Three rules keep the thread out of trouble:

* **It never touches ``st``.** Not ``session_state``, not an element. A thread
  without a script run context that calls into Streamlit either raises or, worse,
  writes into whichever session happens to be current. The token is handed in
  from the render thread instead (:meth:`StatusPoller.set_token`), and results
  come back through plain dictionaries under a lock.
* **It never holds the lock across I/O.** The lock guards dictionary reads and
  writes only. A poller that held it through a ten-second timeout would block
  the render thread on ``peek`` and reintroduce the freeze this module exists to
  remove.
* **It stops on its own.** A tab that closes takes its session with it but not
  its thread; without :data:`StatusPoller.IDLE_EXIT_SECONDS` a day of opened
  dashboards is a day of accumulated threads polling for nobody.
"""

from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple

import streamlit as st

from lex.lex_app.streamlit import _client

#: Statuses that mean work is still running. Everything else settles the record:
#: nothing about it changes again until somebody acts, so polling it further is
#: load with no possible finding.
ACTIVE_STATUSES = frozenset({"IN_PROGRESS"})

#: ``(model, pk)`` -- one business record, whatever a widget asked to see of it.
RecordKey = Tuple[str, str]

#: ``(model, pk, include_log)`` -- one *view* of a record. The log doubles the
#: cost of a read on the backend, so a widget showing it and a widget not
#: showing it are watched separately rather than both paying for it.
WatchKey = Tuple[str, str, bool]


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


def _status_path(model: str, pk) -> str:
    return f"/api/model_entries/{model}/{pk}/calculation-status"


def read_status(model: str, pk, token: str, include_log: bool) -> StatusState:
    """Read calculation state, turning every failure into a renderable state.

    Never raises. Runs on the poller thread, so an exception escaping here would
    not surface on a page at all -- it would kill the thread and leave every
    widget in the session frozen on its last answer, with nothing to explain it.
    """
    # "true" as a literal string: the endpoint compares the raw query value
    # against it, so a Python True would arrive as "True", match nothing, and
    # leave the log panel silently empty. Absent entirely when the panel is off,
    # which is what lets the endpoint skip the CalculationLog query -- the
    # expensive half of a read.
    params = {"include_log": "true"} if include_log else None
    try:
        payload = _client.get_json(_status_path(model, pk), token, params=params)
    except _client.LexApiError as exc:
        return StatusState(status=None, message=_failure_message(exc.status))
    except Exception as exc:  # pragma: no cover - defensive, keeps the thread alive
        return StatusState(status=None, message=f"Status unavailable: {exc}")

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


def _failure_message(status_code: Optional[int]) -> str:
    if status_code == 403:
        # Deliberately vague. The status endpoint answers 404 for a record the
        # caller may not read, so a 403 here means the *action* was refused --
        # not that the record is hidden.
        return "Not available"
    if status_code == 404:
        return "Record not found"
    return "Status unavailable"


#: Shown when this caller may not run the record: beside the disabled button when
#: the status envelope said so up front, and in place of a backend message when a
#: trigger got through to a 403 anyway. One constant, so the two cannot drift
#: into describing the same refusal differently.
NO_PERMISSION_MESSAGE = "You don't have permission to run this"


def trigger_calculation(model: str, pk, token: str) -> Optional[str]:
    """Start the calculation. Returns a message to render, or ``None`` on success.

    The exact call the React UI makes, so permissions, audit actor and history
    are identical to a run started from the table.

    ``calculate`` travels in the request *body*: ``One.update`` reads it from
    ``request.data`` and never looks at the query string, so a flag parked in
    the URL is dropped and the PATCH degrades into an empty partial update --
    no calculation, no error, a button that appears to do nothing.

    The 403 branch stays even though the status envelope reports the same
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
    except Exception as exc:  # pragma: no cover - defensive, keeps the thread alive
        return str(exc)
    return None


@dataclass
class _Watch:
    """One record this session is interested in, and when to look again."""

    include_log: bool
    interval: float
    due_at: float
    #: ``False`` once a read settles the record. The entry stays -- its snapshot
    #: is what every later render displays -- but it stops costing requests.
    active: bool = True


@dataclass
class _Snapshot:
    state: StatusState
    read_at: float
    #: Bumped on every stored read. A widget comparing generations knows the
    #: answer moved without having to compare envelopes field by field, which is
    #: what ``sync_page`` needs to rerun a page exactly once per change.
    generation: int = 0


@dataclass
class _Record:
    """Per-record state that is not about any one view of it."""

    #: A trigger has succeeded and no read has confirmed it yet. Rendering
    #: "Running" from this is not a guess: ``One.update`` sets and saves
    #: ``is_calculated = IN_PROGRESS`` before it answers the PATCH.
    pending: bool = False
    #: The last refusal, kept until the next trigger replaces it. Deliberately
    #: not popped on read: two widgets watching one record must both be able to
    #: show why the click failed, and whichever rendered first would otherwise
    #: consume it.
    trigger_error: Optional[str] = None


class StatusPoller:
    """One session's watch list, its snapshots, and the thread that fills them.

    Constructed with its I/O injected so the whole class is testable without a
    backend, a browser or a thread: pass ``autostart=False`` and drive it with
    :meth:`run_once`.
    """

    #: How long the thread keeps polling after the last render touched it. A
    #: closed tab never says goodbye -- Streamlit drops the session, and the only
    #: evidence available here is that :meth:`set_token` stopped being called.
    #: Comfortably longer than any UI refresh, so a slow render never looks like
    #: a closed tab.
    IDLE_EXIT_SECONDS = 90.0

    #: Reads issued at once. A page watching six records should cost one round
    #: trip's worth of latency, not six -- but an unbounded fan-out would let a
    #: dashboard open as many backend connections as it has tiles.
    MAX_PARALLEL_READS = 4

    #: Floor on the sleep between passes, so a zero or negative interval cannot
    #: turn the loop into a spin.
    TICK_FLOOR = 0.05

    def __init__(
        self,
        *,
        reader: Callable[..., StatusState] = read_status,
        trigger: Callable[..., Optional[str]] = trigger_calculation,
        clock: Callable[[], float] = time.monotonic,
        autostart: bool = True,
    ):
        self._reader = reader
        self._trigger = trigger
        self._clock = clock
        self._autostart = autostart

        self._lock = threading.RLock()
        self._watches: Dict[WatchKey, _Watch] = {}
        self._snapshots: Dict[WatchKey, _Snapshot] = {}
        self._records: Dict[RecordKey, _Record] = {}
        self._queue: List[RecordKey] = []
        self._token: Optional[str] = None
        self._generation = 0
        self._last_seen = clock()

        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._pool: Optional[ThreadPoolExecutor] = None

    # ── called from the render thread ────────────────────────────────────────

    def set_token(self, token: str) -> None:
        """Hand the thread the freshest access token, and prove the tab is open.

        Called on every render. Both halves matter: the host refreshes the token
        periodically, so a poller holding the one it was given at page load would
        start 401ing on a long-lived dashboard; and this is the only signal that
        the session still exists (see :data:`IDLE_EXIT_SECONDS`).
        """
        with self._lock:
            self._token = token
            self._last_seen = self._clock()

    def watch(self, model: str, pk, include_log: bool, interval: float) -> None:
        """Register interest in a record, starting the thread if needed.

        Idempotent per view: thirteen widgets over six records produce six
        watches. A record already settled is *not* reopened -- the snapshot it
        settled on is still the truth, and re-arming on every rerun would make an
        idle dashboard poll forever.
        """
        key = self._watch_key(model, pk, include_log)
        with self._lock:
            existing = self._watches.get(key)
            if existing is None:
                self._watches[key] = _Watch(
                    include_log=include_log,
                    interval=self._sane_interval(interval),
                    due_at=self._clock(),
                )
                self._wake.set()
            else:
                # A second widget may ask for a faster poll than the first. Take
                # the shortest asked for: an author who set poll_interval=0.5 on
                # one tile means it, and the record cannot be watched at two
                # rates at once.
                existing.interval = min(
                    existing.interval, self._sane_interval(interval),
                )
        self._ensure_running()

    def peek(self, model: str, pk, include_log: bool) -> Optional[StatusState]:
        """The latest answer for this view, or ``None`` if there is not one yet.

        Never blocks and never fetches: this is the whole of the render path's
        contact with the backend. ``None`` means the first read has not landed,
        which the widget renders as "Checking" -- not as an error, and above all
        not as a reason to wait.
        """
        with self._lock:
            snapshot = self._snapshots.get(self._watch_key(model, pk, include_log))
            return snapshot.state if snapshot else None

    def generation(self, model: str, pk, include_log: bool) -> int:
        """How many times this view's answer has been replaced."""
        with self._lock:
            snapshot = self._snapshots.get(self._watch_key(model, pk, include_log))
            return snapshot.generation if snapshot else 0

    def is_pending(self, model: str, pk) -> bool:
        """Whether a trigger has succeeded that no read has yet confirmed."""
        with self._lock:
            record = self._records.get(self._record_key(model, pk))
            return bool(record and record.pending)

    def trigger_error(self, model: str, pk) -> Optional[str]:
        """The last refusal for this record, or ``None``."""
        with self._lock:
            record = self._records.get(self._record_key(model, pk))
            return record.trigger_error if record else None

    def request_trigger(self, model: str, pk) -> None:
        """Queue a run and return immediately.

        The PATCH is real work -- ``One.update`` re-reads the record, clears
        terminal state, saves, registers the calculation and broadcasts it before
        answering -- and doing it inside the click handler is what made pressing
        the button feel like nothing had happened. Queueing it lets the same
        render that handled the click paint "Running", because by the time the
        PATCH returns the record already is.
        """
        record_key = self._record_key(model, pk)
        with self._lock:
            record = self._records.setdefault(record_key, _Record())
            record.pending = True
            record.trigger_error = None
            self._queue.append(record_key)
            # Whatever settled this record is now wrong, and its watch stopped.
            # Re-arm every view of it so the run is followed to its end.
            for key, watch in self._watches.items():
                if key[0] == record_key[0] and key[1] == record_key[1]:
                    watch.active = True
                    watch.due_at = self._clock()
        self._wake.set()
        self._ensure_running()

    def is_watching(self, model: str, pk, include_log: bool) -> bool:
        """Whether this view is still being polled."""
        with self._lock:
            watch = self._watches.get(self._watch_key(model, pk, include_log))
            return bool(watch and watch.active)

    def stop(self) -> None:
        """Shut the thread down. For tests and for an explicit teardown."""
        self._stop.set()
        self._wake.set()
        thread, pool = self._thread, self._pool
        self._thread, self._pool = None, None
        if thread and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=2.0)
        if pool:
            pool.shutdown(wait=False)

    # ── the thread ───────────────────────────────────────────────────────────

    def _ensure_running(self) -> None:
        if not self._autostart or self._stop.is_set():
            return
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._thread = threading.Thread(
                target=self._loop,
                name="lex-calculation-poller",
                # Daemon: a dashboard's poller must never be the reason a
                # Streamlit server refuses to exit.
                daemon=True,
            )
            self._thread.start()

    def _loop(self) -> None:  # pragma: no cover - exercised via run_once
        while not self._stop.is_set():
            wait = self.run_once()
            if wait is None:
                return
            self._wake.wait(wait)
            self._wake.clear()

    def run_once(self) -> Optional[float]:
        """One pass: send queued triggers, refresh what is due, report the sleep.

        Returns the seconds to wait before the next pass, or ``None`` when there
        is nothing left to do -- the session has gone quiet, and the thread
        should end rather than poll for a tab nobody has open.

        Public because it is also how the tests drive the poller: every scenario
        below runs the loop body directly, on one thread, with a clock it
        controls. A test that had to start a real thread and sleep would be slow
        and would only sometimes be right.
        """
        with self._lock:
            token = self._token
            idle_for = self._clock() - self._last_seen
            queued, self._queue = self._queue, []
            due = [
                (key, watch)
                for key, watch in self._watches.items()
                if watch.active and watch.due_at <= self._clock()
            ]

        if idle_for > self.IDLE_EXIT_SECONDS:
            return None
        if not token:
            # Nothing can be read without one. Come back rather than exit: the
            # host refreshes tokens, and the next render will hand one over.
            return self._sleep_hint()

        for record_key in queued:
            self._send_trigger(record_key, token)

        if due:
            self._refresh(due, token)

        return self._sleep_hint()

    def _send_trigger(self, record_key: RecordKey, token: str) -> None:
        model, pk = record_key
        failure = self._trigger(model, pk, token)
        with self._lock:
            record = self._records.setdefault(record_key, _Record())
            if failure:
                # Nothing was started, so nothing may be claimed: leaving
                # ``pending`` set would show "Running" over a refusal and leave
                # the reader watching a calculation that does not exist.
                record.pending = False
                record.trigger_error = failure
                for key, watch in self._watches.items():
                    if key[0] == model and key[1] == pk:
                        watch.active = False
            else:
                record.trigger_error = None

    def _refresh(self, due: List[Tuple[WatchKey, _Watch]], token: str) -> None:
        """Read every due view, in parallel, and store what came back.

        Parallel because latency here is entirely the backend's: six records read
        in series cost six round trips before the last tile has ever shown a
        status, and the whole point of this module is that no reader waits for
        that.
        """
        if len(due) == 1:
            results = [(due[0][0], self._read(due[0][0], token))]
        else:
            pool = self._get_pool()
            futures = {key: pool.submit(self._read, key, token) for key, _ in due}
            results = [(key, future.result()) for key, future in futures.items()]

        now = self._clock()
        with self._lock:
            for key, state in results:
                self._generation += 1
                self._snapshots[key] = _Snapshot(
                    state=state, read_at=now, generation=self._generation,
                )
                watch = self._watches.get(key)
                if watch is None:
                    continue
                watch.due_at = now + watch.interval
                # A read that reached the backend outranks the optimism, in both
                # directions: it confirms the run, or it reveals that the trigger
                # never took. Optimism covers exactly one round trip and must not
                # become a second opinion about the record.
                record = self._records.get((key[0], key[1]))
                if record is not None and state.status is not None:
                    record.pending = False
                if state.status not in ACTIVE_STATUSES:
                    # Settled, or unreadable. Either way nothing here changes
                    # again on its own, and an idle dashboard that kept asking is
                    # permanent load multiplied by every tab left open -- and
                    # invisible, because nothing is broken.
                    watch.active = False

    def _read(self, key: WatchKey, token: str) -> StatusState:
        model, pk, include_log = key
        return self._reader(model, pk, token, include_log)

    def _get_pool(self) -> ThreadPoolExecutor:
        with self._lock:
            if self._pool is None:
                self._pool = ThreadPoolExecutor(
                    max_workers=self.MAX_PARALLEL_READS,
                    thread_name_prefix="lex-calculation-read",
                )
            return self._pool

    def _sleep_hint(self) -> float:
        """How long until the next thing is due, floored so this cannot spin."""
        with self._lock:
            if self._queue:
                return self.TICK_FLOOR
            due_times = [w.due_at for w in self._watches.values() if w.active]
        if not due_times:
            # Nothing active. Wake on the next trigger, or after long enough to
            # notice the session has gone.
            return self.IDLE_EXIT_SECONDS
        return max(self.TICK_FLOOR, min(due_times) - self._clock())

    # ── keys ─────────────────────────────────────────────────────────────────

    @staticmethod
    def _record_key(model: str, pk) -> RecordKey:
        # ``pk`` normalised because ``4`` and ``"4"`` name one record, and a
        # dashboard that passes an int where a widget passed a string would
        # otherwise watch it twice.
        return (model, str(pk))

    @staticmethod
    def _watch_key(model: str, pk, include_log: bool) -> WatchKey:
        return (model, str(pk), bool(include_log))

    def _sane_interval(self, interval: float) -> float:
        try:
            value = float(interval)
        except (TypeError, ValueError):
            return 2.0
        return max(self.TICK_FLOOR, value)


#: Session-state slot holding this session's poller.
SESSION_KEY = "_lex_calculation_poller"


def get_poller() -> StatusPoller:
    """This session's poller, created on first use.

    Lives in ``st.session_state``, which Streamlit scopes to one browser session
    and therefore one signed-in user. That scoping is the entire security
    argument and it is deliberately the boring one: there is no module-level
    registry and no ``st.cache_resource``, because both are shared by every user
    of the server.

    The stake is not a stale badge. The status endpoint answers a record the
    caller may not read with the same 404 as a record that does not exist,
    precisely so its existence is not disclosed -- and a poller reaching across
    sessions would hand over the answer that machinery exists to withhold. It
    holds a bearer token, too, which makes a shared instance a way for one user's
    credential to be reachable from another user's request.

    Without a session -- an import-time scan, a doc build, a worker thread --
    returns a fresh detached poller that has no thread and never starts one. A
    module-level singleton would have been cheaper and is exactly the wrong
    shape: it is a process-wide object accumulating whichever bearer token was
    handed over last.
    """
    try:
        poller = st.session_state.get(SESSION_KEY)
        if poller is None:
            poller = StatusPoller()
            st.session_state[SESSION_KEY] = poller
        return poller
    except Exception:  # pragma: no cover - depends on Streamlit's runtime state
        return StatusPoller(autostart=False)
