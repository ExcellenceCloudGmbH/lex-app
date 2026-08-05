"""The Streamlit calculation widget, its poller and its backend client.

Intent: a dashboard author should be able to trigger one calculation and watch
it, without embedding a React table just to click one button. The widget talks
to the backend over HTTP as the signed-in user -- never the ORM -- so read
permission, audit actor resolution and the ``_defer_calculate_hook`` trigger
path stay identical to the React UI's. A second way to start a calculation is
what produced the ``edited_at`` bug (PR #675), and an in-process ORM call would
be exactly that.

Three regressions these scenarios exist to prevent, all of them silent:

* **Polling that never stops.** A dashboard left open would keep asking the
  backend for a status that will never change again -- permanent load nobody
  notices, multiplied by every open tab.
* **A failure path that raises.** Streamlit renders top-to-bottom, so an
  exception escaping the widget erases every widget below it on the page. The
  page does not report an error; it silently loses its bottom half.
* **Colour drift.** LEX success is teal, not green. A hex literal here would
  keep rendering the old palette after the next token refresh, and nothing
  would fail.

And a fourth, which is the one a reader actually feels, and which two rounds of
partial fixes did not remove. The widget used to read its status *inside the
render*: Streamlit runs a page on one thread, so thirteen tiles over six records
waited through each other's round trips before any of them existed, and the page
painted over seconds. It also decided its poll timer inside its own fragment,
where the only way to obtain one is to rerun the entire script -- and
``st.rerun()`` raises, so every widget below the one that asked lost its turn
and asked again on the next run. A click cost fourteen script runs.

The rule that ends all of it is one sentence: **the render path performs no I/O
and reruns nothing but itself.** Reads and triggers belong to
``_status_poller.StatusPoller``, on a thread the session owns; the render is a
dictionary lookup; the redraw timer is declared unconditionally so it never has
to be rebuilt. Scenarios 1.251-1.266 hold each half of that, and 1.263-1.266
hold it against the real Streamlit runtime, where the claims about script runs
actually live.

Cluster 1ab -- scenarios 1.223-1.266. Type: U.
Covers: lex/lex_app/streamlit/_client.py,
        lex/lex_app/streamlit/_status_poller.py,
        lex/lex_app/streamlit/calculation.py,
        lex/lex_app/streamlit/__init__.py.
Run: python -m lex pytest lex/test_project/tests/init/test_1ab_calculation_widget.py -v
"""

from __future__ import annotations

import contextlib
import threading
from unittest import mock

import pytest
import requests
from django.test import SimpleTestCase

pytestmark = pytest.mark.init


# ── shared fixtures ──────────────────────────────────────────────────────────


def _response(status_code: int = 200, payload=None, text: str = ""):
    """A stand-in for a ``requests`` response, good enough for the client."""
    response = mock.Mock(spec=requests.Response)
    response.status_code = status_code
    response.content = text.encode() or b"{}"
    response.json.return_value = {} if payload is None else payload
    return response


@contextlib.contextmanager
def _transport(**kwargs):
    """Patch the pooled session the client actually calls through.

    The client keeps one :class:`requests.Session` per thread so a page watching
    six records does not renegotiate TLS six times per poll. That pooling is the
    reason these scenarios patch ``_client._session`` rather than
    ``requests.get``: a scenario patching the module-level function would sail
    straight past the session and out to the network.
    """
    from lex.lex_app.streamlit import _client

    session = mock.Mock(spec=requests.Session)
    for name, value in kwargs.items():
        getattr(session, name).return_value = value
    with mock.patch.object(_client, "_session", return_value=session):
        yield session


class _Clock:
    """A monotonic clock a scenario can move.

    Every deadline in the poller is expressed against one clock, so a scenario
    can step past a poll interval or an idle timeout without sleeping. Tests
    that slept would be slow, and -- worse -- would pass or fail depending on
    how loaded the machine was.
    """

    def __init__(self, now: float = 1000.0):
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def _poller(*, answers=None, trigger=None, clock=None):
    """A real poller with no thread, answering from a script.

    Deliberately the real class rather than a mock: every claim below is about
    what the poller *does* -- when it stops polling, when it re-arms, what it
    hands the render path -- and a mock would agree with whatever the scenario
    asserted.
    """
    from lex.lex_app.streamlit._status_poller import StatusPoller, StatusState

    answers = answers or {}
    calls = []

    def _read(model, pk, token, include_log):
        calls.append({"model": model, "pk": pk, "token": token,
                      "include_log": include_log})
        answer = answers.get((model, str(pk)), answers.get("*"))
        if callable(answer):
            answer = answer(len(calls))
        return answer or StatusState(status="NOT_CALCULATED")

    triggers = []

    def _trigger(model, pk, token):
        triggers.append({"model": model, "pk": pk, "token": token})
        return trigger(model, pk, token) if callable(trigger) else trigger

    clock = clock or _Clock()
    instance = StatusPoller(
        reader=_read,
        trigger=_trigger,
        clock=clock,
        autostart=False,
    )
    instance.reads = calls
    instance.triggers = triggers
    instance.clock = clock
    return instance


def _pass(poller, times: int = 1):
    """Step past the settle delay and run one poller pass.

    A brand-new watch is deliberately not due the instant it is registered: one
    script run registers every tile's watch within a few milliseconds, and a
    thread that woke on the first would read that record alone and only then
    discover the rest -- two rounds of backend latency to paint a page instead
    of one. Scenarios drive a frozen clock, so they have to step over it.
    """
    from lex.lex_app.streamlit._status_poller import StatusPoller

    for _ in range(times):
        poller.clock.advance(StatusPoller.NEW_WATCH_SETTLE)
        poller.run_once()


class _FakeStreamlit:
    """Stands in for the ``streamlit`` module at the widget's boundary.

    Real decorators and real context managers, so the widget's own code path is
    what runs -- a plain Mock would let ``@st.fragment`` swallow the render body
    whole and every scenario below would pass without executing anything.
    Records what was rendered so a scenario can assert on it.
    """

    class Rerun(Exception):
        """Stands in for Streamlit's RerunException, which is control flow."""

    def __init__(self, button_returns: bool | int = False):
        self.session_state: dict = {}
        self.rendered: list = []
        self.button_keys: list = []
        self.buttons: list = []
        self.declared_intervals: list = []
        self.fragments: list = []
        self.reruns: list = []
        # A press is reported once, exactly as Streamlit reports it: the run
        # that handled the click, and no later one. A stand-in that answered
        # True on every render would re-click the button on each redraw, which
        # silently rewrites the very state a scenario is checking -- a refusal
        # cleared by a fresh trigger looks like a refusal that never rendered.
        self._clicks_left = int(button_returns)

    def fragment(self, func=None, *, run_every=None, **kwargs):
        self.declared_intervals.append(run_every)

        def _declare(f):
            # Kept so a scenario can call the fragment body on its own, which is
            # what a redraw tick is: Streamlit re-executes the stored fragment
            # and nothing else on the page.
            self.fragments.append(f)
            return f

        return _declare(func) if func is not None else _declare

    def columns(self, spec, **kwargs):
        return [contextlib.nullcontext() for _ in spec]

    def button(self, label, *, key=None, disabled=False, **kwargs):
        self.button_keys.append(key)
        self.buttons.append({"label": label, "key": key, "disabled": disabled})
        self.rendered.append(("button", label))
        # Streamlit never reports a press for a button it drew disabled, so
        # neither does this stand-in -- otherwise a scenario could "click" a
        # button the real host would not have let the reader touch.
        if disabled or self._clicks_left <= 0:
            return False
        self._clicks_left -= 1
        return True

    def rerun(self, *, scope="app"):
        self.reruns.append(scope)
        raise self.Rerun(scope)

    def __getattr__(self, name):
        def _render(*args, **kwargs):
            self.rendered.append((name, args[0] if args else None))

        return _render

    def text_of(self) -> str:
        return " | ".join(f"{kind}:{value}" for kind, value in self.rendered)


#: Thirteen tiles over two records -- the page whose behaviour is being pinned.
#:
#: ``tiles_rendered`` is reset each run and bumped *after* each widget, so a
#: scenario can tell "every tile got its turn" from "the page lost its bottom
#: half". Counting buttons cannot: a tile whose record could not be read
#: deliberately renders a message and no button, so during an outage a page that
#: rendered perfectly and a page that died half way both show too few buttons.
_PAGE_OF_TILES = """
import streamlit as st
from lex.lex_app.streamlit import lex_calculation_streamlit

st.session_state["script_runs"] = st.session_state.get("script_runs", 0) + 1
st.session_state["tiles_rendered"] = 0

for i in range(7):
    lex_calculation_streamlit("quarter", 1, key=f"a{i}")
    st.session_state["tiles_rendered"] += 1
for i in range(6):
    lex_calculation_streamlit("quarter", 2, key=f"b{i}")
    st.session_state["tiles_rendered"] += 1
"""


# ── the HTTP client ──────────────────────────────────────────────────────────


class TestCluster01ab_CalculationClient(SimpleTestCase):
    """Cluster 1ab: the widget's HTTP client."""

    def test_1_223_reuses_the_frontend_origin_the_embed_helper_already_needs(self):
        """
        Scenario 1.223: no new configuration is needed to reach the backend.
        Given: only REACT_APP_URL, the variable lex_view already requires
        When: the client resolves its base URL
        Then: it targets that origin. Django serves the React bundle and /api
              from the same host, so a dashboard that can embed a lex_view can
              reach the API with nothing further configured.
        """
        from lex.lex_app.streamlit._client import resolve_api_base_url

        with mock.patch.dict(
            "os.environ", {"REACT_APP_URL": "https://app.example.com/"}, clear=True,
        ):
            self.assertEqual(
                resolve_api_base_url(), "https://app.example.com",
                msg=(
                    "The client must reuse the origin lex_view already needs, "
                    "with no trailing slash -- paths are appended directly and "
                    "a doubled slash would 404 on some ingress configurations."
                ),
            )

    def test_1_224_explicit_override_wins(self):
        """
        Scenario 1.224: a split deployment can point the widget elsewhere.
        Given: both LEX_API_URL and REACT_APP_URL are set
        When: the client resolves its base URL
        Then: the explicit override wins, so an instance that does not serve its
              API from the frontend host is still reachable.
        """
        from lex.lex_app.streamlit._client import resolve_api_base_url

        with mock.patch.dict(
            "os.environ",
            {
                "LEX_API_URL": "https://api.example.com",
                "REACT_APP_URL": "https://app.example.com",
            },
            clear=True,
        ):
            self.assertEqual(
                resolve_api_base_url(), "https://api.example.com",
                msg="An explicit LEX_API_URL must outrank the frontend origin.",
            )

    def test_1_225_an_unreachable_backend_surfaces_as_a_lex_api_error(self):
        """
        Scenario 1.225: transport failures are the widget's own error type.
        Given: a backend that cannot be reached at all
        When: the client issues a read
        Then: it raises LexApiError, not a requests exception. The poller catches
              exactly one type; anything else escaping would kill the polling
              thread and freeze every tile in the session on its last answer,
              with nothing on the page to explain it.
        """
        from lex.lex_app.streamlit import _client

        session = mock.Mock(spec=requests.Session)
        session.get.side_effect = requests.ConnectionError("no route")
        with mock.patch.object(_client, "_session", return_value=session):
            with self.assertRaises(
                _client.LexApiError,
                msg=(
                    "A transport failure must arrive as LexApiError; a raw "
                    "requests exception would escape the poller's except clause."
                ),
            ):
                _client.get_json("/api/x", "token")

    def test_1_226_an_error_response_carries_its_status_and_detail(self):
        """
        Scenario 1.226: the caller can tell 403 from 404 from 500.
        Given: the backend refuses with 403 and a DRF detail message
        When: the client issues the call
        Then: both the status code and the message survive onto the error. The
              widget renders a refusal differently from an outage, and it can
              only do that if the status reaches it.
        """
        from lex.lex_app.streamlit import _client

        with _transport(get=_response(403, {"detail": "Nope"})):
            with self.assertRaises(_client.LexApiError) as caught:
                _client.get_json("/api/x", "token")

        self.assertEqual(
            caught.exception.status, 403,
            msg=(
                "The HTTP status must survive onto the error, or the widget "
                f"cannot distinguish a refusal from an outage. Got "
                f"{caught.exception.status}."
            ),
        )
        self.assertIn(
            "Nope", str(caught.exception),
            msg="The backend's own explanation must reach the reader.",
        )

    def test_1_227_every_call_is_made_as_the_signed_in_user(self):
        """
        Scenario 1.227: the widget never authenticates as the instance.
        Given: a user's bearer token
        When: the client reads and triggers
        Then: both carry Authorization: Bearer <token>. The instance's LEX_API_KEY
              is a machine-to-machine secret resolving to a technical user;
              using it here would run the calculation under the wrong actor and
              skip the reader's own permission entirely.
        """
        from lex.lex_app.streamlit import _client

        with _transport(get=_response(200, {}), patch=_response(200, {})) as session:
            _client.get_json("/api/x", "user-token")
            _client.patch_json("/api/y", "user-token", json={"calculate": "true"})

        for call in (session.get.call_args, session.patch.call_args):
            self.assertEqual(
                call.kwargs["headers"]["Authorization"], "Bearer user-token",
                msg=(
                    "Every call must be made as the signed-in user. An Api-Key "
                    "header here would resolve to the technical user and detach "
                    "the run from the person who asked for it."
                ),
            )

    def test_1_263_connections_are_pooled_and_never_shared_across_threads(self):
        """
        Scenario 1.263: a page of tiles does not renegotiate TLS per read.
        Given: several reads from one thread, and a read from another
        When: the client issues them
        Then: one thread reuses one Session, and the other thread gets its own.

        A bare requests.get opens a new connection -- and against HTTPS, a new
        TLS session -- for every call, which is most of what a status read costs
        and none of what it is for. requests.Session is not thread-safe, and the
        poller reads several records at once, so the pool has to be per-thread:
        one shared Session would be a data race on the connection pool.
        """
        from lex.lex_app.streamlit import _client

        seen = []

        def _collect():
            seen.append(id(_client._session()))

        _collect()
        _collect()
        self.assertEqual(
            len(set(seen)), 1,
            msg=(
                "Repeated calls on one thread must reuse one Session; got "
                f"{len(set(seen))} distinct ones, so every read pays a fresh "
                "handshake."
            ),
        )

        other: list = []
        thread = threading.Thread(target=lambda: other.append(id(_client._session())))
        thread.start()
        thread.join()

        self.assertNotIn(
            other[0], seen,
            msg=(
                "A second thread must get its own Session. requests.Session is "
                "not thread-safe, and the poller reads several records "
                "concurrently."
            ),
        )


# ── pure state and presentation ──────────────────────────────────────────────


class TestCluster01ab_CalculationWidgetState(SimpleTestCase):
    """Cluster 1ab: poll lifecycle, presentation and the trigger call."""

    def test_1_228_polling_stops_on_a_terminal_status(self):
        """
        Scenario 1.228: a settled record is not polled again.
        Given: each terminal status
        When: the widget decides whether to keep reading
        Then: it stops. A dashboard left open on a finished calculation would
              otherwise ask forever for an answer that cannot change --
              permanent backend load, multiplied by every open tab, and
              invisible because nothing is broken.
        """
        from lex.lex_app.streamlit.calculation import poll_interval_for

        for status in ("SUCCESS", "ERROR", "ABORTED", "CANCELLED", "NOT_CALCULATED"):
            with self.subTest(status=status):
                self.assertIsNone(
                    poll_interval_for(status, 2.0),
                    msg=(
                        f"{status} is terminal, so reading must stop; the widget "
                        "asked to keep polling."
                    ),
                )

    def test_1_229_polling_runs_only_while_work_is_in_progress(self):
        """
        Scenario 1.229: a running record is watched at the requested rate.
        Given: IN_PROGRESS and a 2.5s interval
        When: the widget decides whether to keep reading
        Then: it returns that interval, and a failed read stops it -- there is
              nothing to watch when the record could not be read at all.
        """
        from lex.lex_app.streamlit.calculation import poll_interval_for

        self.assertEqual(
            poll_interval_for("IN_PROGRESS", 2.5), 2.5,
            msg="Work in flight must be polled at the interval the author asked for.",
        )
        self.assertIsNone(
            poll_interval_for(None, 2.5),
            msg=(
                "A failed read must not start a poll loop: there is no record "
                "state to watch, and retrying on a timer is how an outage "
                "becomes a retry storm."
            ),
        )

    def test_1_230_the_redraw_timer_is_declared_unconditionally(self):
        """
        Scenario 1.230: a tile can always redraw itself, whatever its status.
        Given: any poll interval, including nonsense
        When: the tile's redraw interval is chosen
        Then: it is a real number, never None, and stays within its bounds.

        This is the whole reason the page stopped greying. run_every is read only
        when a fragment is *declared*, and only a full script run declares one.
        A tile that dropped its timer when its record settled could not get one
        back after a click without rerunning the entire page -- and st.rerun()
        raises, so that rerun took every widget below it down with it. Holding
        the timer costs a redraw of a dictionary lookup; rebuilding it costs a
        page.
        """
        from lex.lex_app.streamlit.calculation import (
            UI_REFRESH_CEILING, UI_REFRESH_FLOOR, ui_refresh_for,
        )

        for interval in (0.0, 0.1, 2.0, 5.0, 3600.0, None, "nonsense"):
            with self.subTest(interval=interval):
                tick = ui_refresh_for(interval)
                self.assertIsNotNone(
                    tick,
                    msg=(
                        f"poll_interval={interval!r} produced no redraw timer. A "
                        "tile without one can only refresh by rerunning the page."
                    ),
                )
                self.assertGreaterEqual(tick, UI_REFRESH_FLOOR)
                self.assertLessEqual(tick, UI_REFRESH_CEILING)

    def test_1_231_aborted_offers_a_rerun_nudge_and_error_does_not(self):
        """
        Scenario 1.231: an interrupted run is not a failed one.
        Given: ABORTED and ERROR
        When: each is presented
        Then: ABORTED reads as interrupted and suggests running it again; ERROR
              does not. Since PR #675 an aborted row is work cut short by a
              restart, not a calculation that failed -- collapsing the two is
              what made incident 1410 hard to read, because people went hunting
              for a failure that had never happened.
        """
        from lex.lex_app.streamlit.calculation import presentation_for

        aborted = presentation_for("ABORTED")
        errored = presentation_for("ERROR")

        self.assertTrue(
            aborted.suggests_rerun,
            msg="An interrupted run must tell the reader to run it again.",
        )
        self.assertNotIn(
            "error", aborted.label.lower(),
            msg=(
                f"ABORTED must not read as a failure; it rendered "
                f"{aborted.label!r}."
            ),
        )
        self.assertFalse(
            errored.suggests_rerun,
            msg="A genuine failure must not be presented as a retry.",
        )

    def test_1_232_an_unknown_status_renders_its_own_name(self):
        """
        Scenario 1.232: a new backend status is visible, not invisible.
        Given: a status this table has never heard of
        When: it is presented
        Then: it renders verbatim rather than as nothing. A state added to
              CalculationModel before this table catches up should look
              unfamiliar to a reader, not silently render an empty badge.
        """
        from lex.lex_app.streamlit.calculation import presentation_for

        look = presentation_for("QUEUED_FOR_REVIEW")
        self.assertEqual(
            look.label, "QUEUED_FOR_REVIEW",
            msg=f"An unknown status must render its own name; got {look.label!r}.",
        )

    def test_1_233_the_log_is_not_requested_when_it_is_not_shown(self):
        """
        Scenario 1.233: the expensive half of a read is opt-in.
        Given: a widget with show_log off
        When: the status is read
        Then: no include_log parameter is sent, which is what lets the endpoint
              skip the CalculationLog query entirely.
        """
        from lex.lex_app.streamlit._status_poller import read_status

        with mock.patch(
            "lex.lex_app.streamlit._status_poller._client.get_json",
            return_value={"status": "SUCCESS"},
        ) as get_json:
            read_status("quarter", 1, "token", include_log=False)

        self.assertIsNone(
            get_json.call_args.kwargs["params"],
            msg=(
                "A widget not showing the log must not ask for it: the log query "
                "is the expensive half of a read and it repeats every poll."
            ),
        )

    def test_1_234_the_log_flag_is_the_exact_string_the_endpoint_matches(self):
        """
        Scenario 1.234: the flag is "true", not True.
        Given: a widget with show_log on
        When: the status is read
        Then: include_log is the literal string the endpoint compares against. A
              Python True would arrive as "True", match nothing, and leave the
              log panel permanently and silently empty.
        """
        from lex.lex_app.streamlit._status_poller import read_status

        with mock.patch(
            "lex.lex_app.streamlit._status_poller._client.get_json",
            return_value={"status": "SUCCESS"},
        ) as get_json:
            read_status("quarter", 1, "token", include_log=True)

        self.assertEqual(
            get_json.call_args.kwargs["params"], {"include_log": "true"},
            msg=(
                "The endpoint compares the raw query value against 'true'; "
                f"sent {get_json.call_args.kwargs['params']!r}."
            ),
        )

    def test_1_235_the_trigger_is_the_react_uis_own_call(self):
        """
        Scenario 1.235: one way to start a calculation, not two.
        Given: a trigger
        When: it is sent
        Then: it is a PATCH to the same one-entry route the React table uses,
              with calculate=true in the *body*. One.update reads the flag from
              request.data and never looks at the query string, so a flag parked
              in the URL degrades the call into an empty partial update: no
              calculation, no error, a button that appears to do nothing.
        """
        from lex.lex_app.streamlit._status_poller import trigger_calculation

        with mock.patch(
            "lex.lex_app.streamlit._status_poller._client.patch_json",
            return_value={},
        ) as patch_json:
            failure = trigger_calculation("quarter", 7, "token")

        self.assertIsNone(failure, msg="A successful trigger reports nothing.")
        self.assertEqual(
            patch_json.call_args.args[0], "/api/model_entries/quarter/default/one/7",
            msg=(
                "The trigger must use the React UI's own route so permissions, "
                "audit actor and history are identical."
            ),
        )
        self.assertEqual(
            patch_json.call_args.kwargs["json"], {"calculate": "true"},
            msg=(
                "calculate must travel in the body; One.update never reads the "
                "query string for it."
            ),
        )

    def test_1_236_a_refused_trigger_is_reported_not_raised(self):
        """
        Scenario 1.236: a 403 on the trigger becomes a message.
        Given: a backend that refuses the trigger
        When: it is sent
        Then: a permission message comes back rather than an exception. This is
              the backstop behind the disabled button: the status envelope is
              one poll old, and this call is the only thing actually being
              authorised.
        """
        from lex.lex_app.streamlit import _status_poller

        with mock.patch(
            "lex.lex_app.streamlit._status_poller._client.patch_json",
            side_effect=_status_poller._client.LexApiError("nope", status=403),
        ):
            failure = _status_poller.trigger_calculation("quarter", 7, "token")

        self.assertEqual(
            failure, _status_poller.NO_PERMISSION_MESSAGE,
            msg=(
                "A refusal must render as the shared permission message rather "
                f"than raising; got {failure!r}."
            ),
        )

    def test_1_237_colours_come_from_the_design_system(self):
        """
        Scenario 1.237: no hex literals in the widget.
        Given: the presentation table
        When: each status is presented
        Then: every colour is a design-system token. LEX success is teal, not
              green; a literal here would keep rendering the old palette after
              the next token refresh and nothing would fail.
        """
        from lex.lex_app.design_system import ERROR, MUTED, SUCCESS, WARNING
        from lex.lex_app.streamlit.calculation import presentation_for

        tokens = {ERROR, MUTED, SUCCESS, WARNING}
        for status in ("NOT_CALCULATED", "IN_PROGRESS", "SUCCESS", "ERROR",
                       "ABORTED", "CANCELLED"):
            with self.subTest(status=status):
                self.assertIn(
                    presentation_for(status).colour, tokens,
                    msg=(
                        f"{status} uses a colour that is not a design token, so "
                        "it will not follow the next palette change."
                    ),
                )

    def test_1_238_neither_the_widget_nor_the_poller_imports_django(self):
        """
        Scenario 1.238: the backend is reached only over HTTP.
        Given: the widget module and the poller that does its I/O
        When: their imports are inspected
        Then: neither reaches a Django model. An in-process ORM call would skip
              the record's read permission, resolve the wrong audit actor and
              miss the _defer_calculate_hook trigger path in one step -- a
              second way to start a calculation, which is exactly what produced
              the edited_at bug in PR #675.
        """
        import ast
        import pathlib

        import lex.lex_app.streamlit.calculation as widget

        allowed_prefixes = ("lex.lex_app.design_system", "lex.lex_app.streamlit")
        for module in (widget, __import__(
            "lex.lex_app.streamlit._status_poller", fromlist=["x"],
        )):
            with self.subTest(module=module.__name__):
                tree = ast.parse(pathlib.Path(module.__file__).read_text())
                imported = set()
                for node in ast.walk(tree):
                    if isinstance(node, ast.Import):
                        imported.update(a.name for a in node.names)
                    elif isinstance(node, ast.ImportFrom) and node.module:
                        imported.add(node.module)

                first_party = {n for n in imported if n.startswith("lex.")}
                stray = {
                    n for n in first_party
                    if not n.startswith(allowed_prefixes)
                }
                self.assertEqual(
                    stray, set(),
                    msg=(
                        f"{module.__name__} imports {sorted(stray)}. Any model or "
                        "Django import here is a path to the database that "
                        "bypasses DRF permissions and the audit actor."
                    ),
                )
                self.assertNotIn(
                    "django", " ".join(imported),
                    msg=f"{module.__name__} must not import Django at all.",
                )

    def test_1_239_the_last_run_line_separates_never_run_from_a_duration(self):
        """
        Scenario 1.239: "no timings" and "ran instantly" are different things.
        Given: a record that has never run, and one that took 38 seconds
        When: the widget renders its last-run line
        Then: the first says so in words and the second reports the duration.
              The endpoint returns null timings for a record with no log rows,
              and formatting null as a number would put "took 0s" under a record
              that has never been calculated at all.
        """
        from lex.lex_app.streamlit.calculation import StatusState, last_run_caption

        never = last_run_caption(StatusState(status="NOT_CALCULATED"))
        ran = last_run_caption(
            StatusState(
                status="SUCCESS",
                finished_at="2026-08-04T10:00:38",
                duration_seconds=38.0,
            ),
        )

        self.assertIn(
            "never", never.lower(),
            msg=(
                "A record with no run behind it must say so rather than render "
                f"an empty or zeroed duration. Got {never!r}."
            ),
        )
        self.assertIn(
            "38", ran,
            msg=(
                "A finished run must report how long it took -- that is the "
                f"whole point of the line. Got {ran!r}."
            ),
        )

    def test_1_240_the_package_is_the_public_surface(self):
        """
        Scenario 1.240: authors import from the package, not its internals.
        Given: the streamlit package
        When: an author imports the widgets
        Then: both are on the package surface, and the pre-existing
              lex.lex_app.streamlit.embed path still resolves to the same
              function. Dashboards already written against embed must not break
              to make room for the new widget.
        """
        from lex.lex_app.streamlit import (
            lex_calculation, lex_calculation_streamlit, lex_view,
        )
        from lex.lex_app.streamlit.embed import lex_view as legacy_lex_view

        for name, fn in (
            ("lex_calculation", lex_calculation),
            ("lex_calculation_streamlit", lex_calculation_streamlit),
        ):
            self.assertTrue(
                callable(fn),
                msg=(
                    f"{name} must be callable straight off the package. Both "
                    "ways of putting a calculation on a page are supported on "
                    "purpose: the embed renders the product's own record view, "
                    "the native one returns a value a dashboard can branch on."
                ),
            )
        self.assertIs(
            lex_view, legacy_lex_view,
            msg=(
                "The package must re-export the very same lex_view, not a copy: "
                "lex.lex_app.streamlit.embed is a documented import path and "
                "existing dashboards use it."
            ),
        )


# ── the rendered widget ──────────────────────────────────────────────────────


class TestCluster01ab_CalculationWidgetSurface(SimpleTestCase):
    """Cluster 1ab: the rendered widget, against a fake Streamlit and a real poller."""

    def _host(self, token: str = "user-token", **kwargs) -> "_FakeStreamlit":
        fake = _FakeStreamlit(**kwargs)
        fake.session_state["access_token"] = token
        return fake

    @contextlib.contextmanager
    def _page(self, host, poller):
        """Render with a fake Streamlit and an injected poller."""
        from lex.lex_app.streamlit import calculation

        with mock.patch.object(calculation, "st", host), mock.patch.object(
            calculation, "get_poller", return_value=poller,
        ):
            yield

    @staticmethod
    def _prime(poller, model="quarter", pk=1, include_log=False):
        """Give the poller its first answer before the page renders.

        The steady state of a dashboard, and what these scenarios are about: the
        very first render of a brand-new session has nothing to draw yet, which
        is scenario 1.267's business, not every other scenario's.
        """
        poller.set_token("user-token")
        poller.watch(model, pk, include_log=include_log, interval=2.0)
        _pass(poller)

    @staticmethod
    def _settled(**kwargs):
        from lex.lex_app.streamlit._status_poller import StatusState

        defaults = {"status": "SUCCESS", "duration_seconds": 12.0}
        defaults.update(kwargs)
        return StatusState(**defaults)

    def test_1_241_two_widgets_on_one_page_keep_separate_state(self):
        """
        Scenario 1.241: a dashboard may watch more than one record.
        Given: two widgets on one page, for two different records
        When: both render
        Then: each gets its own button key and the poller watches each record
              separately. Sharing one key would make Streamlit reject the second
              button as a duplicate widget ID.
        """
        from lex.lex_app.streamlit import calculation

        poller = _poller(answers={"*": self._settled()})
        host = self._host()
        with self._page(host, poller):
            calculation.lex_calculation_streamlit("quarter", 1)
            calculation.lex_calculation_streamlit("quarter", 2)

        self.assertEqual(
            len(set(host.button_keys)), 2,
            msg=(
                "Two records must produce two distinct button keys; got "
                f"{host.button_keys!r}, which Streamlit rejects as a duplicate "
                "widget ID."
            ),
        )
        self.assertTrue(
            poller.is_watching("quarter", 1, False)
            and poller.is_watching("quarter", 2, False),
            msg="Each record must be registered with the poller in its own right.",
        )

    def test_1_242_the_token_comes_from_the_streamlit_session(self):
        """
        Scenario 1.242: the widget uses the host's freshest token.
        Given: a host whose session holds an access token
        When: the widget renders
        Then: that token is what the poller is given. The host refreshes this
              key in the background, so reading it every render is what keeps a
              long-lived dashboard from starting to 401.
        """
        from lex.lex_app.streamlit import calculation

        poller = _poller(answers={"*": self._settled()})
        host = self._host(token="fresh-token")
        with self._page(host, poller):
            calculation.lex_calculation_streamlit("quarter", 1)

        _pass(poller)
        self.assertEqual(
            poller.reads[0]["token"], "fresh-token",
            msg=(
                "The poller must read with the token the host currently holds; "
                f"it used {poller.reads[0]['token']!r}."
            ),
        )

    def test_1_243_an_expired_session_asks_for_a_reload_without_calling_out(self):
        """
        Scenario 1.243: no token is a sign-in problem, not a backend problem.
        Given: a host with no access token
        When: the widget renders
        Then: it asks the reader to reload and makes no call. An unauthenticated
              call would come back 401 and be reported as "status unavailable",
              pointing the reader at the backend for something a reload fixes.
        """
        from lex.lex_app.streamlit import calculation

        poller = _poller(answers={"*": self._settled()})
        host = self._host(token="")
        with self._page(host, poller):
            result = calculation.lex_calculation_streamlit("quarter", 1)

        self.assertIsNone(result, msg="Nothing can be reported without a session.")
        self.assertIn(
            "reload", host.text_of().lower(),
            msg=f"The reader must be told to reload; page said {host.text_of()!r}.",
        )
        self.assertEqual(
            poller.reads, [],
            msg="A widget with no token must not reach the backend at all.",
        )

    def test_1_244_a_failed_read_renders_a_message_instead_of_raising(self):
        """
        Scenario 1.244: a broken record loses its own tile, not the page.
        Given: a record the backend cannot report on
        When: the widget renders
        Then: it renders the failure's message and returns without raising.
              Streamlit renders top-to-bottom, so an exception escaping here
              would erase every widget below it -- the page would not report an
              error, it would silently lose its bottom half.
        """
        from lex.lex_app.streamlit import calculation
        from lex.lex_app.streamlit._status_poller import StatusState

        poller = _poller(
            answers={"*": StatusState(status=None, message="Record not found")},
        )
        host = self._host()
        self._prime(poller)
        with self._page(host, poller):
            calculation.lex_calculation_streamlit("quarter", 1)
            host.fragments[0]()

        self.assertIn(
            "Record not found", host.text_of(),
            msg=(
                "A failed read must render its message; the page showed "
                f"{host.text_of()!r}."
            ),
        )

    def test_1_245_a_record_found_running_is_watched_without_a_page_rerun(self):
        """
        Scenario 1.245: a page opened mid-calculation follows it, quietly.
        Given: a record already IN_PROGRESS when the page loads
        When: the widget renders
        Then: the poller keeps watching it and the page is never re-run.

        The rerun is the thing being pinned. It used to be unavoidable -- the
        poll timer only exists when a fragment is declared -- and it greyed the
        whole page every time any one tile discovered work in flight.
        """
        from lex.lex_app.streamlit import calculation
        from lex.lex_app.streamlit._status_poller import StatusState

        poller = _poller(answers={"*": StatusState(status="IN_PROGRESS")})
        host = self._host()
        self._prime(poller)
        with self._page(host, poller):
            calculation.lex_calculation_streamlit("quarter", 1)
            host.fragments[0]()

        self.assertTrue(
            poller.is_watching("quarter", 1, False),
            msg="A running record must still be watched after the read lands.",
        )
        self.assertEqual(
            host.reruns, [],
            msg=(
                "Discovering work in flight must not rerun the page; it asked "
                f"for {host.reruns!r}, which greys every other tile."
            ),
        )

    def test_1_246_a_settled_record_redraws_without_rerunning_the_page(self):
        """
        Scenario 1.246: a quiet tile stays quiet.
        Given: a settled record whose first read has already landed
        When: the tile redraws on its own timer
        Then: nothing is re-read and the page is not re-run. This is the steady
              state of a dashboard: it must cost nothing.
        """
        from lex.lex_app.streamlit import calculation

        poller = _poller(answers={"*": self._settled()})
        host = self._host()
        self._prime(poller)
        with self._page(host, poller):
            calculation.lex_calculation_streamlit("quarter", 1)
            before = len(poller.reads)
            host.fragments[0]()
            host.fragments[0]()

        self.assertEqual(
            len(poller.reads), before,
            msg=(
                "Redrawing a settled tile must issue no reads; it issued "
                f"{len(poller.reads) - before}."
            ),
        )
        self.assertEqual(
            host.reruns, [],
            msg=f"A quiet redraw must not rerun the page; got {host.reruns!r}.",
        )

    def test_1_247_a_caller_who_may_not_run_gets_a_disabled_button_and_a_reason(self):
        """
        Scenario 1.247: a refusal is shown, not hidden.
        Given: an envelope reporting can_calculate false, with a reason
        When: the widget renders
        Then: the button is disabled and the reason is on the page beside it. A
              missing button reads as a broken dashboard; a disabled one with no
              explanation reads as a bug. The reason is the whole point of
              disabling it up front rather than letting the click 403.
        """
        from lex.lex_app.streamlit import calculation

        poller = _poller(
            answers={"*": self._settled(
                can_calculate=False,
                calculate_denied_reason="Read-only for your role",
            )},
        )
        host = self._host()
        self._prime(poller)
        with self._page(host, poller):
            calculation.lex_calculation_streamlit("quarter", 1)
            host.fragments[0]()

        self.assertTrue(
            host.buttons[-1]["disabled"],
            msg="A caller the backend will refuse must not be offered the button.",
        )
        self.assertIn(
            "Read-only for your role", host.text_of(),
            msg=(
                "The backend's reason must be rendered beside the disabled "
                f"button; page said {host.text_of()!r}."
            ),
        )

    def test_1_248_a_running_record_still_disables_the_button(self):
        """
        Scenario 1.248: a calculation cannot be started twice from one tile.
        Given: a record already running, which this caller may run
        When: the widget renders
        Then: the button is disabled anyway. Permission and "already running"
              are independent reasons to refuse a click and both have to hold.
        """
        from lex.lex_app.streamlit import calculation
        from lex.lex_app.streamlit._status_poller import StatusState

        poller = _poller(
            answers={"*": StatusState(status="IN_PROGRESS", can_calculate=True)},
        )
        host = self._host()
        self._prime(poller)
        with self._page(host, poller):
            calculation.lex_calculation_streamlit("quarter", 1)
            host.fragments[0]()

        self.assertTrue(
            host.buttons[-1]["disabled"],
            msg="A record already running must not offer a second trigger.",
        )

    def test_1_249_a_stale_envelope_still_meets_the_403_backstop(self):
        """
        Scenario 1.249: the last word on permission is the trigger itself.
        Given: an envelope that says the caller may run, and a backend that
               refuses when the trigger actually arrives
        When: the reader presses the button
        Then: the refusal is rendered and the optimism is withdrawn.

        The envelope is one read old. Permission can change between the read and
        the click, and the PATCH is the only call actually being authorised --
        so losing this branch would turn a late refusal into a button that
        silently does nothing.
        """
        from lex.lex_app.streamlit import calculation
        from lex.lex_app.streamlit._status_poller import NO_PERMISSION_MESSAGE

        poller = _poller(
            answers={"*": self._settled(can_calculate=True)},
            trigger=NO_PERMISSION_MESSAGE,
        )
        host = self._host(button_returns=True)
        self._prime(poller)
        with self._page(host, poller):
            calculation.lex_calculation_streamlit("quarter", 1)
            host.fragments[0]()          # the click
            poller.run_once()            # the poller sends it and is refused
            host.fragments[0]()          # the tile redraws

        self.assertIn(
            NO_PERMISSION_MESSAGE, host.text_of(),
            msg=(
                "A refusal arriving after the click must reach the reader; page "
                f"said {host.text_of()!r}."
            ),
        )
        self.assertFalse(
            poller.is_pending("quarter", 1),
            msg=(
                "A refused trigger must take the optimism back, or the tile "
                "shows 'Running' for a calculation that was never started."
            ),
        )

    def test_1_250_an_envelope_without_the_flag_leaves_the_button_alone(self):
        """
        Scenario 1.250: an older backend does not disable the button.
        Given: an envelope with no can_calculate key at all
        When: the widget renders
        Then: the button stays enabled. Reading absence as "denied" would
              disable it for every user of a deployment that has not shipped the
              endpoint yet, with no way to press through it -- so the widget
              fails open and lets the trigger's own 403 be the backstop.
        """
        from lex.lex_app.streamlit import calculation
        from lex.lex_app.streamlit._status_poller import read_status

        with mock.patch(
            "lex.lex_app.streamlit._status_poller._client.get_json",
            return_value={"status": "SUCCESS"},
        ):
            state = read_status("quarter", 1, "token", include_log=False)

        self.assertTrue(
            state.can_calculate,
            msg="A missing flag is an older endpoint, not a refusal.",
        )

        poller = _poller(answers={"*": state})
        host = self._host()
        self._prime(poller)
        with self._page(host, poller):
            calculation.lex_calculation_streamlit("quarter", 1)
            host.fragments[0]()

        self.assertFalse(
            host.buttons[-1]["disabled"],
            msg="The widget must never be the thing that refuses.",
        )


# ── the poller ───────────────────────────────────────────────────────────────


class TestCluster01ab_CalculationPoller(SimpleTestCase):
    """Cluster 1ab: what a page costs, and where the waiting happens.

    Every scenario here is about the one rule that makes a page of tiles feel
    like a page: the render path performs no I/O. Streamlit runs a script on one
    thread, so a tile that reads its own status inside the render holds up every
    tile below it, in series, before any of them exist.
    """

    @staticmethod
    def _settled(**kwargs):
        from lex.lex_app.streamlit._status_poller import StatusState

        defaults = {"status": "SUCCESS"}
        defaults.update(kwargs)
        return StatusState(**defaults)

    def test_1_251_rendering_a_tile_issues_no_backend_call(self):
        """
        Scenario 1.251: the render path never waits on the network.
        Given: a page of thirteen tiles over six records
        When: the page renders
        Then: not one read is issued during the render.

        This is the headline claim and the reason the page went from seconds to
        milliseconds. Registering a watch must be a dictionary write; anything
        that fetches here puts the backend's latency on the render thread, where
        every later tile queues behind it.
        """
        from lex.lex_app.streamlit import calculation

        poller = _poller(answers={"*": self._settled()})
        host = _FakeStreamlit()
        host.session_state["access_token"] = "user-token"

        with mock.patch.object(calculation, "st", host), mock.patch.object(
            calculation, "get_poller", return_value=poller,
        ):
            for index in range(13):
                calculation.lex_calculation_streamlit("quarter", index % 6, key=f"t{index}")

        self.assertEqual(
            poller.reads, [],
            msg=(
                f"The render issued {len(poller.reads)} backend reads. Every one "
                "of them is a round trip the tiles below it wait through, in "
                "series, before they exist."
            ),
        )

    def test_1_252_one_record_costs_one_read_however_many_tiles_show_it(self):
        """
        Scenario 1.252: tiles share a watch, they do not each own one.
        Given: seven tiles showing one record and six showing another
        When: the poller runs a pass
        Then: it issues two reads. Thirteen would be thirteen round trips for
              two answers.
        """
        from lex.lex_app.streamlit import calculation

        poller = _poller(answers={"*": self._settled()})
        host = _FakeStreamlit()
        host.session_state["access_token"] = "user-token"

        with mock.patch.object(calculation, "st", host), mock.patch.object(
            calculation, "get_poller", return_value=poller,
        ):
            for index in range(7):
                calculation.lex_calculation_streamlit("quarter", 1, key=f"a{index}")
            for index in range(6):
                calculation.lex_calculation_streamlit("quarter", 2, key=f"b{index}")

        _pass(poller)
        self.assertEqual(
            len(poller.reads), 2,
            msg=(
                "Two records must cost two reads however many tiles show them; "
                f"the poller issued {len(poller.reads)}."
            ),
        )

    def test_1_253_a_click_returns_before_the_trigger_is_sent(self):
        """
        Scenario 1.253: pressing Calculate does not wait for the PATCH.
        Given: a reader who presses the button
        When: the click is handled
        Then: the render finishes with nothing sent yet, and the trigger goes out
              on the poller's next pass.

        One.update re-reads the record, clears terminal state, saves, registers
        the calculation and broadcasts it before answering. Doing that inside
        the click handler is precisely why the button felt dead: the render that
        was supposed to say "Running" was blocked on it.
        """
        from lex.lex_app.streamlit import calculation

        poller = _poller(answers={"*": self._settled()}, trigger=None)
        poller.set_token("user-token")
        poller.watch("quarter", 1, include_log=False, interval=2.0)
        _pass(poller)

        host = _FakeStreamlit(button_returns=True)
        host.session_state["access_token"] = "user-token"

        with mock.patch.object(calculation, "st", host), mock.patch.object(
            calculation, "get_poller", return_value=poller,
        ):
            calculation.lex_calculation_streamlit("quarter", 1)   # the click lands here

        self.assertEqual(
            poller.triggers, [],
            msg=(
                "The click handler must not send the PATCH; it sent "
                f"{poller.triggers!r} while the reader waited."
            ),
        )

        _pass(poller)
        self.assertEqual(
            len(poller.triggers), 1,
            msg="The trigger must actually go out on the poller's next pass.",
        )

    def test_1_254_a_click_is_acknowledged_before_any_read_confirms_it(self):
        """
        Scenario 1.254: the badge answers the click, not the next poll.
        Given: a settled record and a reader who presses Calculate
        When: the same render handles the click
        Then: it already reports IN_PROGRESS.

        Not a guess: One.update sets is_calculated = IN_PROGRESS and saves it
        before it answers, so this is what the record becomes. Waiting for a
        read to agree is how a click came to take seconds to change a word --
        and for those seconds the badge still said "Not calculated", so the
        honest reading of the page was that the button had not worked.
        """
        from lex.lex_app.streamlit import calculation

        poller = _poller(answers={"*": self._settled(status="NOT_CALCULATED")})
        poller.set_token("user-token")
        poller.watch("quarter", 1, include_log=False, interval=2.0)
        _pass(poller)

        host = _FakeStreamlit(button_returns=True)
        host.session_state["access_token"] = "user-token"

        with mock.patch.object(calculation, "st", host), mock.patch.object(
            calculation, "get_poller", return_value=poller,
        ):
            calculation.lex_calculation_streamlit("quarter", 1)   # the click lands here

        self.assertIn(
            "Starting", host.text_of(),
            msg=(
                "The render that handled the click must acknowledge it in words; "
                f"it rendered {host.text_of()!r}. 'Starting…' rather than "
                "'Running' because the reader pressed a button and is owed an "
                "answer to that, not a status that happens to be correct -- and "
                "because a refusal a moment later reads as a story after "
                "'Starting…' and as a contradiction after 'Running'."
            ),
        )

    def test_1_255_a_real_read_replaces_the_optimistic_state(self):
        """
        Scenario 1.255: optimism covers one round trip, not the record.
        Given: a click, and then a read that reports what really happened
        When: the read lands
        Then: the optimism is dropped and the read's answer stands. Optimism
              that outlived its confirmation would become a second opinion about
              the record.
        """
        from lex.lex_app.streamlit._status_poller import StatusState

        poller = _poller(answers={"*": StatusState(status="SUCCESS")})
        poller.request_trigger("quarter", 1)
        self.assertTrue(
            poller.is_pending("quarter", 1),
            msg="A queued trigger must be pending until something confirms it.",
        )

        poller.watch("quarter", 1, include_log=False, interval=2.0)
        poller.set_token("user-token")
        _pass(poller)

        self.assertFalse(
            poller.is_pending("quarter", 1),
            msg=(
                "A read that reached the backend outranks the optimism, in both "
                "directions -- it confirms the run or reveals it never took."
            ),
        )

    def test_1_256_polling_stops_when_a_record_settles_and_a_trigger_re_arms_it(self):
        """
        Scenario 1.256: the poller goes quiet, and wakes for a real reason.
        Given: a record that settles
        When: passes keep running, and then the reader triggers a run
        Then: reads stop while it is settled and resume once it is triggered.

        Stopping is what keeps an idle dashboard from being permanent backend
        load. Re-arming is what stops that from being a one-way door -- without
        it the tile would show "Running" forever and never notice the end.
        """
        clock = _Clock()
        poller = _poller(answers={"*": self._settled()}, clock=clock)
        poller.set_token("user-token")
        poller.watch("quarter", 1, include_log=False, interval=2.0)

        _pass(poller)
        settled_after = len(poller.reads)
        clock.advance(60.0)
        _pass(poller)

        self.assertEqual(
            len(poller.reads), settled_after,
            msg=(
                "A settled record must not be read again; the poller issued "
                f"{len(poller.reads) - settled_after} further reads a minute on."
            ),
        )

        poller.request_trigger("quarter", 1)
        _pass(poller)

        self.assertGreater(
            len(poller.reads), settled_after,
            msg=(
                "A triggered record must be watched again, or the tile shows "
                "'Running' until the page is reloaded."
            ),
        )

    def test_1_257_the_poller_stops_when_the_session_goes_quiet(self):
        """
        Scenario 1.257: a closed tab does not leave a thread behind.
        Given: a poller whose session has stopped rendering
        When: enough time passes
        Then: the loop reports that it should end.

        Streamlit gives no teardown signal a background thread can see; the only
        evidence available is that the render stopped handing over a token. A
        day of opened dashboards would otherwise be a day of accumulated threads
        polling for nobody.
        """
        from lex.lex_app.streamlit._status_poller import StatusPoller

        clock = _Clock()
        poller = _poller(answers={"*": self._settled()}, clock=clock)
        poller.set_token("user-token")
        poller.watch("quarter", 1, include_log=False, interval=2.0)

        self.assertIsNotNone(
            poller.run_once(),
            msg="A live session's poller must keep going.",
        )

        clock.advance(StatusPoller.IDLE_EXIT_SECONDS + 1)
        self.assertIsNone(
            poller.run_once(),
            msg=(
                "A session that has not rendered for longer than the idle "
                "timeout is gone, and its poller must end rather than poll on."
            ),
        )

    def test_1_258_a_widget_showing_the_log_does_not_make_others_pay_for_it(self):
        """
        Scenario 1.258: the log is watched separately from the record.
        Given: one tile showing the log and one not, on the same record
        When: the poller runs a pass
        Then: it reads both views, and only one asks for the log.

        The log doubles the cost of a read on the backend. Sharing one watch
        would either charge every tile for it or leave the log panel of the tile
        that asked permanently empty.
        """
        poller = _poller(answers={"*": self._settled()})
        poller.set_token("user-token")
        poller.watch("quarter", 1, include_log=False, interval=2.0)
        poller.watch("quarter", 1, include_log=True, interval=2.0)
        _pass(poller)

        asked = sorted(call["include_log"] for call in poller.reads)
        self.assertEqual(
            asked, [False, True],
            msg=(
                "The two views of one record must be read independently; the "
                f"poller asked {asked!r}."
            ),
        )

    def test_1_259_the_shortest_requested_interval_wins(self):
        """
        Scenario 1.259: a record cannot be watched at two rates at once.
        Given: two tiles on one record asking for 5s and 0.5s
        When: both register
        Then: the record is watched at 0.5s. An author who asked for the faster
              poll on one tile meant it, and the slower tile loses nothing by
              being told sooner.
        """
        clock = _Clock()
        poller = _poller(
            answers={"*": self._settled(status="IN_PROGRESS")}, clock=clock,
        )
        poller.set_token("user-token")
        poller.watch("quarter", 1, include_log=False, interval=5.0)
        poller.watch("quarter", 1, include_log=False, interval=0.5)

        _pass(poller)
        first = len(poller.reads)
        clock.advance(0.6)
        _pass(poller)

        self.assertGreater(
            len(poller.reads), first,
            msg=(
                "The record must be read again after the shorter interval; it "
                "was still waiting on the longer one."
            ),
        )

    def test_1_260_the_poller_never_touches_streamlit(self):
        """
        Scenario 1.260: nothing on the polling thread reaches into a session.
        Given: the poller module
        When: its source is inspected
        Then: nothing outside get_poller() touches st.

        A thread with no script run context that calls into Streamlit either
        raises or, worse, writes into whichever session happens to be current --
        one reader's status landing in another reader's page. The token is
        handed in from the render thread instead, and results come back through
        plain dictionaries under a lock.
        """
        import ast
        import pathlib

        from lex.lex_app.streamlit import _status_poller

        tree = ast.parse(pathlib.Path(_status_poller.__file__).read_text())
        offenders = []
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if node.name == "get_poller":
                continue
            for inner in ast.walk(node):
                if (
                    isinstance(inner, ast.Attribute)
                    and isinstance(inner.value, ast.Name)
                    and inner.value.id == "st"
                ):
                    offenders.append(f"{node.name} -> st.{inner.attr}")

        self.assertEqual(
            offenders, [],
            msg=(
                f"The poller touches Streamlit outside get_poller(): {offenders}. "
                "Anything reached from the polling thread must not, because that "
                "thread has no session of its own to reach."
            ),
        )

    def test_1_261_two_sessions_never_share_a_poller_or_a_token(self):
        """
        Scenario 1.261: one reader's status can never reach another's page.
        Given: two Streamlit sessions
        When: each asks for its poller
        Then: they are different objects, and neither is a module-level
              singleton.

        The stake is not a stale badge. The status endpoint answers a record the
        caller may not read with the same 404 as one that does not exist,
        precisely so its existence is not disclosed -- and a shared poller would
        hand over the answer that machinery exists to withhold. It holds a
        bearer token too, so a shared instance is also a way for one user's
        credential to be reachable from another user's request.
        """
        from lex.lex_app.streamlit import _status_poller

        first_session, second_session = _FakeStreamlit(), _FakeStreamlit()

        with mock.patch.object(_status_poller, "st", first_session):
            first = _status_poller.get_poller()
            first.set_token("token-of-user-one")
        with mock.patch.object(_status_poller, "st", second_session):
            second = _status_poller.get_poller()

        self.addCleanup(first.stop)
        self.addCleanup(second.stop)

        self.assertIsNot(
            first, second,
            msg=(
                "Two sessions must get two pollers. One shared instance carries "
                "one reader's token and one reader's answers into the other's "
                "page."
            ),
        )
        self.assertNotIn(
            _status_poller.SESSION_KEY, second_session.session_state.get("__none__", {}),
            msg="The poller must live in session state, not module state.",
        )

    def test_1_262_a_transient_failure_is_retried_and_a_refusal_is_not(self):
        """
        Scenario 1.262: a dropped connection is not an answer about the record.
        Given: a backend that fails a read without ever reaching a conclusion,
               and separately one that answers 404
        When: the poller runs passes
        Then: the first is retried on a widening backoff and the second stops.

        Treating every failure as final is what made the page feel unreliable:
        one blip left a tile reading "Status unavailable" for the rest of the
        session, and only a reload cleared it. Treating none as final turns a
        deleted record into a poll that never stops. The line is whether the
        failure says anything *about this record* -- 403 and 404 do, a timeout
        does not.
        """
        from lex.lex_app.streamlit._status_poller import StatusPoller, StatusState

        clock = _Clock()
        poller = _poller(
            answers={"*": StatusState(status=None, message="Status unavailable")},
            clock=clock,
        )
        poller.set_token("user-token")
        poller.watch("quarter", 1, include_log=False, interval=2.0)
        _pass(poller)

        self.assertIsNotNone(
            poller.peek("quarter", 1, False),
            msg="A failed read must still produce a state to render.",
        )
        self.assertTrue(
            poller.is_watching("quarter", 1, False),
            msg=(
                "A read that never reached a conclusion must be tried again. "
                "Giving up here is what left a tile stuck on 'Status "
                "unavailable' until the reader reloaded the page."
            ),
        )

        before = len(poller.reads)
        clock.advance(StatusPoller.RETRY_BACKOFF_START + 0.01)
        poller.run_once()
        self.assertGreater(
            len(poller.reads), before,
            msg="The retry must actually happen once the backoff has elapsed.",
        )

        # ...and it widens, so thirteen tiles meeting an outage do not become a
        # retry storm against a backend that is already struggling.
        after_second = len(poller.reads)
        clock.advance(StatusPoller.RETRY_BACKOFF_START + 0.01)
        poller.run_once()
        self.assertEqual(
            len(poller.reads), after_second,
            msg=(
                "The second failure must wait longer than the first; the poller "
                "retried on the same interval, which is a retry storm."
            ),
        )

        refused = _poller(
            answers={"*": StatusState(
                status=None, message="Record not found", failure_status=404,
            )},
        )
        refused.set_token("user-token")
        refused.watch("quarter", 9, include_log=False, interval=2.0)
        _pass(refused)

        self.assertFalse(
            refused.is_watching("quarter", 9, False),
            msg=(
                "A 404 is an answer about this record and will not change on "
                "its own; retrying it forever is permanent load for nothing."
            ),
        )

    def test_1_267_a_failed_read_never_erases_a_status_that_was_confirmed(self):
        """
        Scenario 1.267: our view lapses; the record does not change.
        Given: a record that read SUCCESS, and then a read that fails
        When: the tile redraws
        Then: it still reports SUCCESS, and says separately that it is
              reconnecting.

        Replacing a status the reader has been watching for ten minutes with
        "Status unavailable" claims something about the record that did not
        happen. The calculation is still finished; we merely stopped being able
        to confirm it, which belongs in a footnote and not in the badge.
        """
        from lex.lex_app.streamlit._status_poller import StatusState

        answers = {"*": StatusState(status="SUCCESS")}
        poller = _poller(answers=answers)
        poller.set_token("user-token")
        poller.watch("quarter", 1, include_log=False, interval=2.0)
        _pass(poller)

        self.assertEqual(poller.peek("quarter", 1, False).status, "SUCCESS")
        self.assertIsNone(
            poller.lapse("quarter", 1, False),
            msg="Nothing has lapsed while reads are landing.",
        )

        answers["*"] = StatusState(status=None, message="Status unavailable")
        poller.request_trigger("quarter", 1)   # re-arms the watch
        poller.run_once()

        self.assertEqual(
            poller.peek("quarter", 1, False).status, "SUCCESS",
            msg=(
                "The confirmed status must survive a failed read; the tile now "
                "reports something the backend never said about the record."
            ),
        )
        self.assertIsNotNone(
            poller.lapse("quarter", 1, False),
            msg="The reader is still owed the fact that we have lost contact.",
        )

    def test_1_268_a_redraw_keeps_the_poller_alive(self):
        """
        Scenario 1.268: the thread outlives a calculation that outlives a page run.
        Given: a session whose only activity is tiles redrawing themselves
        When: more than the idle timeout passes
        Then: the poller is still running.

        Liveness used to be refreshed only by set_token, which only a full
        script run reaches -- and the entire design is that a settled page never
        has one. So a calculation running longer than the idle timeout outlived
        the thread watching it, and its tile sat on "Running" until the reader
        reloaded. A redraw is the honest signal that somebody still has the page
        open, and peek() is what every redraw calls.
        """
        from lex.lex_app.streamlit._status_poller import StatusPoller, StatusState

        clock = _Clock()
        poller = _poller(
            answers={"*": StatusState(status="IN_PROGRESS")}, clock=clock,
        )
        poller.set_token("user-token")
        poller.watch("quarter", 1, include_log=False, interval=2.0)
        _pass(poller)

        # No further app run -- only redraws, which is the steady state.
        for _ in range(4):
            clock.advance(StatusPoller.IDLE_EXIT_SECONDS / 2)
            poller.peek("quarter", 1, False)
            self.assertIsNotNone(
                poller.run_once(),
                msg=(
                    "A page whose tiles are still redrawing is a page somebody "
                    "still has open; its poller must not exit. A calculation "
                    "longer than the idle timeout froze on 'Running' because it "
                    "did."
                ),
            )

    def test_1_269_a_trigger_does_not_wait_for_the_read_pass(self):
        """
        Scenario 1.269: a reader's action never queues behind housekeeping.
        Given: a poller with reads to do
        When: a trigger is requested
        Then: it is sent before the due reads of that pass.

        Reads are not quick -- every request through the Keycloak permissions
        middleware costs two uncached calls to Keycloak -- so a trigger queued
        behind a read pass reached the backend a second or more after the click.
        The button was not slow to *respond*; it was slow to *act*, which is
        worse, because the badge already said the run had started.
        """
        order = []

        def _trigger(model, pk, token):
            order.append("trigger")
            return None

        poller = _poller(trigger=_trigger)
        poller.set_token("user-token")
        poller.watch("quarter", 1, include_log=False, interval=2.0)
        poller.watch("quarter", 2, include_log=False, interval=2.0)
        original_read = poller._reader

        def _slow_read(model, pk, token, include_log):
            order.append("read")
            return original_read(model, pk, token, include_log)

        poller._reader = _slow_read
        poller.request_trigger("quarter", 1)
        _pass(poller)

        self.assertEqual(
            order[0], "trigger",
            msg=(
                f"The trigger must go out before the pass's reads; order was "
                f"{order!r}. Behind them it lands a full round of backend "
                "latency after the click."
            ),
        )

    def test_1_270_one_script_runs_watches_are_read_in_one_batch(self):
        """
        Scenario 1.270: a page fills in one round of latency, not two.
        Given: six records registered in quick succession, as one script run does
        When: the poller takes its first pass
        Then: all six are read in that pass.

        Without the settle delay the thread wakes on the first watch, reads that
        record alone, and only then discovers the other five -- so the last tile
        on the page waits two full backend round trips before it can show
        anything. That is most of the window in which every tile showed a
        placeholder.
        """
        poller = _poller()
        poller.set_token("user-token")
        for pk in range(6):
            poller.watch("quarter", pk, include_log=False, interval=2.0)
        _pass(poller)

        self.assertEqual(
            len(poller.reads), 6,
            msg=(
                f"All six watches registered in one script run must be read in "
                f"one pass; the poller read {len(poller.reads)}."
            ),
        )


    def test_1_271_a_failed_first_read_does_not_silence_the_return_value(self):
        """
        Scenario 1.271: a blip on the first read costs a moment, not the session.
        Given: a record whose first read fails and whose retry succeeds
        When: the tile redraws after each
        Then: the widget still has a page run left to announce the real status.

        The widget re-runs the page exactly once per record, when the first
        answer lands, so that ``status = lex_calculation(...)`` followed by a
        branch is true on a freshly opened page. Spending that run on a *failed*
        read -- which produces a snapshot, but not an answer -- left the widget
        returning None to the dashboard for the rest of the session: the retry
        that finally succeeded had no run left to announce itself with, and the
        page below the tile stayed empty while the tile itself read "Success".
        """
        from lex.lex_app.streamlit import calculation
        from lex.lex_app.streamlit._status_poller import StatusState

        answers = {"*": StatusState(status=None, message="Status unavailable")}
        poller = _poller(answers=answers)
        poller.set_token("user-token")
        poller.watch("quarter", 1, include_log=False, interval=2.0)
        _pass(poller)

        host = _FakeStreamlit()
        host.session_state["access_token"] = "user-token"
        with mock.patch.object(calculation, "st", host), mock.patch.object(
            calculation, "get_poller", return_value=poller,
        ):
            calculation.lex_calculation_streamlit("quarter", 1)
            self.assertFalse(
                host.session_state.get("lex_calc_quarter_1__primed"),
                msg=(
                    "A failed read is not an answer about the record, so it must "
                    "not consume the one page run the widget has to announce one."
                ),
            )

            answers["*"] = StatusState(status="SUCCESS")
            poller.request_trigger("quarter", 1)
            poller.run_once()

            with self.assertRaises(
                _FakeStreamlit.Rerun,
                msg=(
                    "Once a real status lands, the page must run so that code "
                    "outside the tile sees it. Without this the dashboard "
                    "branches on None forever."
                ),
            ):
                host.fragments[0]()


class TestCluster01ab_CalculationEmbed(SimpleTestCase):
    """Cluster 1ab: the embedded record view.

    ``lex_calculation()`` renders the lex-app frontend's own record view in a
    frame, so the fields shown and their formatting are the product's and stay
    the product's. It is URL construction over ``lex_view`` and nothing else --
    no new transport, no new permission path, no new component -- and these
    scenarios pin that it stays that way.
    """

    def test_1_272_the_embed_targets_the_records_own_route(self):
        """
        Scenario 1.272: one record, not a table filtered down to one.
        Given: a model and a primary key
        When: the path is built for each supported view
        Then: it is the frontend's own per-record route.

        These are React Admin routes the frontend already registers for every
        lex resource -- it passes both ``list`` and ``show`` when it builds each
        Resource -- so nothing here invents a URL the app does not serve.
        """
        from lex.lex_app.streamlit.calculation_embed import record_path

        self.assertEqual(record_path("quarter", 42), "quarter/42/show")
        self.assertEqual(record_path("quarter", 42, "edit"), "quarter/42")
        self.assertEqual(record_path("quarter", 42, "list"), "quarter")

        with self.assertRaises(
            ValueError,
            msg=(
                "An unknown view must fail loudly here rather than produce a "
                "URL the frontend answers with its 404 page inside a frame, "
                "where it looks like the widget is broken."
            ),
        ):
            record_path("quarter", 42, "nonsense")

    def test_1_273_the_serializer_reaches_the_frontend(self):
        """
        Scenario 1.273: the field list is chosen on the model, not in Python.
        Given: a named serializer
        When: the record is embedded
        Then: it is handed to lex_view, which puts it in the query string.

        This is the whole point of the signature. The same name is a path
        segment of the REST API (/api/model_entries/<model>/<serializer>/one/<pk>),
        so declaring the serializer on the model decides which fields appear --
        with no frontend change and nothing listed twice.
        """
        from lex.lex_app.streamlit import calculation_embed

        with mock.patch.object(calculation_embed, "lex_view") as embed:
            calculation_embed.lex_calculation(
                "quarter", 42, serializer="dashboard",
            )

        self.assertEqual(
            embed.call_args.args[0], "quarter/42/show",
            msg="The embed must point at the record's own detail route.",
        )
        self.assertEqual(
            embed.call_args.kwargs["serializer"], "dashboard",
            msg=(
                "The serializer name must reach lex_view; without it the "
                "frontend falls back to the model's default and the argument "
                "silently does nothing."
            ),
        )

    def test_1_274_the_embed_hides_the_chrome_by_default(self):
        """
        Scenario 1.274: a page embedding one record wants the record.
        Given: default arguments
        When: the record is embedded
        Then: the toolbar and actions are hidden.

        lex_view defaults these to False, which is right for a full-page table
        embed and wrong here: an iframe is a fixed box, and the surrounding
        navigation is a large fraction of a 420px one.
        """
        from lex.lex_app.streamlit import calculation_embed

        with mock.patch.object(calculation_embed, "lex_view") as embed:
            calculation_embed.lex_calculation("quarter", 42)

        self.assertTrue(embed.call_args.kwargs["hide_toolbar"])
        self.assertTrue(embed.call_args.kwargs["hide_actions"])

    def test_1_275_the_embed_adds_no_second_way_to_reach_the_backend(self):
        """
        Scenario 1.275: it is URL construction, and nothing else.
        Given: the embed module
        When: its imports are inspected
        Then: it reaches nothing first-party but lex_view.

        The value of embedding the product's own view is that permissions, the
        audit actor and the trigger path are the product's. A module that also
        spoke to the API directly would be a second implementation of exactly
        the thing it exists to avoid re-implementing.
        """
        import ast
        import pathlib

        from lex.lex_app.streamlit import calculation_embed

        tree = ast.parse(pathlib.Path(calculation_embed.__file__).read_text())
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(a.name for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)

        first_party = {n for n in imported if n.startswith("lex.")}
        self.assertEqual(
            first_party, {"lex.lex_app.streamlit.embed"},
            msg=(
                f"The embed module imports {sorted(first_party)}. It must reach "
                "the backend only through lex_view -- anything else is a second "
                "path to the API, which is the thing embedding the product's "
                "own view exists to avoid."
            ),
        )



# ── against the real Streamlit runtime ───────────────────────────────────────


class TestCluster01ab_CalculationRerunBudget(SimpleTestCase):
    """Cluster 1ab: the page's cost, measured against the real Streamlit runtime.

    The claims here -- how many times a script runs, whether a click reruns the
    page -- are claims about Streamlit's own behaviour: how it re-executes a
    script after st.rerun(), and how far session_state reaches. A stand-in for
    the streamlit module can be made to agree with any of them, so these use
    AppTest, which runs the real thing.
    """

    SETTLED = {"status": "SUCCESS", "error": None, "duration_seconds": 12.0}

    def _page(self, script: str = _PAGE_OF_TILES, token: str = "user-token"):
        from streamlit.testing.v1 import AppTest

        page = AppTest.from_string(script)
        page.session_state["access_token"] = token
        return page

    def test_1_264_a_page_of_tiles_loads_in_one_run_and_no_reads(self):
        """
        Scenario 1.264: the page paints before anything has been read.
        Given: thirteen tiles over two records
        When: the page loads
        Then: the script runs once and issues no reads at all during the render.

        Two failures are held down at once, and only the real runtime shows
        both. Reads inside the render are blocking round trips in series, which
        is what made the page paint over seconds. And a script that reruns
        itself while rendering greys, discards what it drew and starts again --
        for a reader, indistinguishable from a page that will not settle.
        """
        reading_threads = []

        def _record(*args, **kwargs):
            reading_threads.append(threading.current_thread().name)
            return self.SETTLED

        page = self._page()
        with mock.patch(
            "lex.lex_app.streamlit._status_poller._client.get_json",
            side_effect=_record,
        ):
            script_thread = threading.current_thread().name
            page.run()

        self.assertEqual(
            page.exception, [],
            msg=f"The page must render cleanly; got {page.exception!r}.",
        )
        self.assertEqual(
            page.session_state["script_runs"], 1,
            msg=(
                "Thirteen tiles must not rerun the script while loading; it ran "
                f"{page.session_state['script_runs']} times. Each rerun greys "
                "the page and re-renders every widget on it."
            ),
        )
        # Not "no reads happened" -- the poller is already working by now, which
        # is the point. The claim is about *where*: a read on the script's own
        # thread is one every tile below it waits through.
        on_script_thread = [t for t in reading_threads if t == script_thread]
        self.assertEqual(
            on_script_thread, [],
            msg=(
                f"{len(on_script_thread)} of {len(reading_threads)} reads ran on "
                "the script's own thread. Every one of them blocks the tiles "
                "below it, in series, before they exist."
            ),
        )

    def test_1_265_every_tile_declares_a_timer_so_none_has_to_rerun_the_page(self):
        """
        Scenario 1.265: the redraw timer exists from the first render.
        Given: a page of tiles over settled records
        When: it loads
        Then: no tile has asked to rerun the page.

        A tile whose timer depended on its status would have none while settled,
        and could only get one back by rerunning the whole script -- which
        raises, taking every widget below it with it. That cascade is what
        turned one click into fourteen script runs.
        """
        page = self._page()
        with mock.patch(
            "lex.lex_app.streamlit._status_poller._client.get_json",
            return_value=self.SETTLED,
        ):
            page.run()

        self.assertEqual(
            page.session_state["script_runs"], 1,
            msg=(
                "No tile may rerun the page to obtain a timer; the script ran "
                f"{page.session_state['script_runs']} times."
            ),
        )
        self.assertEqual(
            page.session_state["tiles_rendered"], 13,
            msg=(
                f"All thirteen tiles must get their turn; only "
                f"{page.session_state['tiles_rendered']} did, so something "
                "aborted the run part-way down the page."
            ),
        )

    def test_1_266_the_widget_survives_a_backend_that_is_completely_down(self):
        """
        Scenario 1.266: an outage costs the tiles' content, never the page.
        Given: a backend that refuses every connection
        When: the page loads and the tiles redraw
        Then: the page renders without raising and every tile is still drawn.

        Streamlit renders top-to-bottom: one exception escaping a widget erases
        every widget below it. A dashboard that loses its bottom half during an
        outage is worse than one that says thirteen times that it cannot reach
        the backend.
        """
        from lex.lex_app.streamlit import _client

        session = mock.Mock(spec=requests.Session)
        session.get.side_effect = requests.ConnectionError("refused")
        session.patch.side_effect = requests.ConnectionError("refused")

        page = self._page()
        with mock.patch.object(_client, "_session", return_value=session):
            page.run()

        self.assertEqual(
            page.exception, [],
            msg=(
                "A backend outage must not raise out of a widget; got "
                f"{page.exception!r}, which erases every widget below it."
            ),
        )
        self.assertEqual(
            page.session_state["tiles_rendered"], 13,
            msg=(
                f"Every tile must still get its turn during an outage; "
                f"{page.session_state['tiles_rendered']} did, so the page lost "
                "the rest."
            ),
        )
