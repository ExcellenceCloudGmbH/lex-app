"""The Streamlit calculation widget and its backend client.

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

And, since the first page anybody built with thirteen of these widgets, a
fourth -- the one a reader actually feels. Each widget read its own status
synchronously and decided its poll timer *inside* its fragment, where the only
way to get one is to rerun the whole script; ``st.rerun()`` raises, so every
widget below the one that asked lost its turn and had to ask again on the next
run, in turn. A page cost thirteen sequential round trips per run and a run per
running record, and pressing Calculate took seven seconds to say "Running".
Scenarios 1.251-1.262 hold the three properties that fixed it: one read per
record per run, a timer decided before the fragment is declared, and a click
that renders its own answer.

Cluster 1ab -- scenarios 1.223-1.262. Type: U.
Covers: lex/lex_app/streamlit/_client.py, lex/lex_app/streamlit/calculation.py,
        lex/lex_app/streamlit/__init__.py.
Run: python -m lex pytest lex/test_project/tests/init/test_1ab_calculation_widget.py -v
"""

from __future__ import annotations

from unittest import mock

import pytest
import requests
from django.test import SimpleTestCase

pytestmark = pytest.mark.init


class TestCluster01ab_CalculationClient(SimpleTestCase):
    """Cluster 1ab: the widget's HTTP client."""

    def test_1_223_reuses_the_frontend_origin_the_embed_helper_already_needs(self):
        """
        Scenario 1.223: no new configuration is needed to reach the backend.
        Given: only REACT_APP_URL, the variable lex_view already requires
        When: the client resolves its base URL
        Then: it targets that origin. Django serves the React bundle and /api
              from the same host (lex_app/urls.py routes /api/... and then falls
              through to serve_react), so any deployment that can already embed
              a lex_view can reach the API with no chart change and no new
              secret -- and the two helpers cannot be pointed at different hosts
        """
        from lex.lex_app.streamlit import _client

        with mock.patch.dict(
            "os.environ",
            {"REACT_APP_URL": "https://demo-prod-4.lex.example.com/"},
            clear=True,
        ):
            self.assertEqual(
                _client.resolve_api_base_url(),
                "https://demo-prod-4.lex.example.com",
                msg=(
                    "The API base must follow the frontend origin lex_view "
                    "already resolves, with the trailing slash trimmed so paths "
                    "do not concatenate into a double slash."
                ),
            )

    def test_1_224_explicit_override_wins(self):
        """
        Scenario 1.224: a deployment can point the widget elsewhere.
        Given: LEX_API_URL set alongside the frontend origin
        When: the client resolves its base URL
        Then: the override wins, matching the precedence
              embed._resolve_base_url already uses -- a split deployment, where
              the API is not on the frontend host, stays reachable without
              patching the framework
        """
        from lex.lex_app.streamlit import _client

        with mock.patch.dict(
            "os.environ",
            {
                "LEX_API_URL": "http://localhost:9999",
                "REACT_APP_URL": "https://frontend.example.com",
            },
            clear=True,
        ):
            self.assertEqual(
                _client.resolve_api_base_url(),
                "http://localhost:9999",
                msg=(
                    "LEX_API_URL must take precedence over the frontend origin; "
                    "otherwise a split deployment has no way to redirect the "
                    "widget."
                ),
            )

    def test_1_225_an_unreachable_backend_surfaces_as_a_lex_api_error(self):
        """
        Scenario 1.225: transport failures arrive in one catchable shape.
        Given: a backend that cannot be reached at all
        When: the client issues a call
        Then: LexApiError, not requests' own exception. The widget catches
              exactly one type; a raw ConnectionError escaping it would reach
              Streamlit and erase everything rendered below the widget
        """
        from lex.lex_app.streamlit import _client

        with mock.patch.dict("os.environ", {"LEX_API_URL": "http://api"}, clear=True):
            with mock.patch(
                "requests.get", side_effect=requests.ConnectionError("no route to host"),
            ):
                with self.assertRaises(_client.LexApiError) as caught:
                    _client.get_json("/api/thing", token="t")

        self.assertIsNone(
            caught.exception.status,
            msg=(
                "A request that never reached the server has no HTTP status. "
                f"Got {caught.exception.status!r}, which the widget would "
                "translate into a message about a response it never received."
            ),
        )

    def test_1_226_an_error_response_carries_its_status_and_detail(self):
        """
        Scenario 1.226: the caller can tell 403 from 404 from 500.
        Given: a backend answering with an error status and a DRF detail body
        When: the client issues a call
        Then: LexApiError carries both. The widget's whole failure vocabulary is
              built on the status code, and the detail is the only text that can
              explain the refusal to the user
        """
        from lex.lex_app.streamlit import _client

        response = mock.Mock(
            status_code=403,
            content=b'{"detail": "You do not have permission."}',
        )
        response.json.return_value = {"detail": "You do not have permission."}

        with mock.patch.dict("os.environ", {"LEX_API_URL": "http://api"}, clear=True):
            with mock.patch("requests.get", return_value=response):
                with self.assertRaises(_client.LexApiError) as caught:
                    _client.get_json("/api/thing", token="t")

        self.assertEqual(
            caught.exception.status, 403,
            msg=(
                "The HTTP status must survive onto the error, or the widget "
                f"cannot distinguish a refusal from an outage. Got "
                f"{caught.exception.status!r}."
            ),
        )
        self.assertIn(
            "You do not have permission.", str(caught.exception),
            msg=(
                "The backend's own detail is the only text that can explain "
                f"this to the user. Got {str(caught.exception)!r}."
            ),
        )

    def test_1_227_every_call_is_made_as_the_signed_in_user(self):
        """
        Scenario 1.227: the client authenticates as the user, not as the host.
        Given: the signed-in user's access token
        When: the client reads and writes
        Then: both carry it as a bearer token. This is the whole reason the
              widget goes over HTTP instead of touching the ORM: the backend
              resolves read permission and the audit actor from this header, so
              an unauthenticated -- or host-authenticated -- call would run
              somebody else's calculation under the wrong name
        """
        from lex.lex_app.streamlit import _client

        response = mock.Mock(status_code=200, content=b"{}")
        response.json.return_value = {}

        with mock.patch.dict("os.environ", {"LEX_API_URL": "http://api"}, clear=True):
            with mock.patch("requests.get", return_value=response) as get:
                _client.get_json("/api/thing", token="user-token")
            with mock.patch("requests.patch", return_value=response) as patch_call:
                _client.patch_json("/api/thing", token="user-token")

        for name, call in (("GET", get), ("PATCH", patch_call)):
            headers = call.call_args.kwargs["headers"]
            self.assertEqual(
                headers.get("Authorization"), "Bearer user-token",
                msg=(
                    f"The {name} must be made as the signed-in user. Got "
                    f"{headers.get('Authorization')!r} -- without it the "
                    "backend cannot apply that user's permissions or record "
                    "them as the actor."
                ),
            )


class TestCluster01ab_CalculationWidgetState(SimpleTestCase):
    """Cluster 1ab: widget state, polling lifecycle and failure containment."""

    def test_1_228_polling_stops_on_a_terminal_status(self):
        """
        Scenario 1.228: a finished calculation stops the poll loop.
        Given: each status a calculation can rest in
        When: the widget decides its poll interval
        Then: None -- polling stops. Without this an idle dashboard keeps asking
              for a status that will never change again: permanent backend load,
              multiplied by every tab anyone left open, and invisible because
              nothing is broken
        """
        from lex.core.models.CalculationModel import CalculationModel
        from lex.lex_app.streamlit.calculation import poll_interval_for

        for state in (
            CalculationModel.SUCCESS,
            CalculationModel.ERROR,
            CalculationModel.ABORTED,
            CalculationModel.CANCELLED,
            CalculationModel.NOT_CALCULATED,
        ):
            with self.subTest(state=state):
                self.assertIsNone(
                    poll_interval_for(state, requested=2.0),
                    msg=(
                        f"{state} is terminal -- nothing about this record will "
                        "change until somebody acts, so polling must stop."
                    ),
                )

    def test_1_229_polling_runs_only_while_work_is_in_progress(self):
        """
        Scenario 1.229: a running calculation is polled.
        Given: IN_PROGRESS
        When: the widget decides its poll interval
        Then: the requested interval, so the badge and the log actually move
              while the work runs -- the one case where re-reading is useful
        """
        from lex.core.models.CalculationModel import CalculationModel
        from lex.lex_app.streamlit.calculation import poll_interval_for

        self.assertEqual(
            poll_interval_for(CalculationModel.IN_PROGRESS, requested=2.0), 2.0,
            msg=(
                "A running calculation must be polled at the requested interval; "
                "otherwise the widget renders 'Running' once and never updates."
            ),
        )

    def test_1_230_the_browser_timer_is_rebuilt_only_when_the_interval_changes(self):
        """
        Scenario 1.230: starting and stopping the poll both need a full script run.
        Given: the interval the fragment was declared with, and the one the
               latest read calls for
        When: the widget decides whether to re-declare itself
        Then: only a real change asks for a rebuild.

        st.fragment reads run_every when the fragment is *declared*, and only a
        full script run declares it: a fragment-scoped rerun re-executes the
        body alone, and Streamlit's frontend clears its auto-rerun intervals in
        cleanupAutoReruns, which it calls only for a full run. So storing a new
        interval mid-fragment never reaches the browser -- a record found
        running would render "Running" and never poll, and a run that finished
        mid-poll would keep the browser's interval alive forever. Both need the
        script to run again. Rebuilding when nothing changed would instead rerun
        the entire dashboard on every single poll.
        """
        from lex.lex_app.streamlit.calculation import poll_timer_needs_rebuild

        self.assertTrue(
            poll_timer_needs_rebuild(declared=None, desired=2.0),
            msg=(
                "A record found running while the timer is off must rebuild, or "
                "the widget shows 'Running' and never updates again."
            ),
        )
        self.assertTrue(
            poll_timer_needs_rebuild(declared=2.0, desired=None),
            msg=(
                "A run that reached a terminal status must rebuild, or the "
                "browser keeps polling forever -- only a full script run clears "
                "its auto-rerun interval."
            ),
        )
        self.assertFalse(
            poll_timer_needs_rebuild(declared=2.0, desired=2.0),
            msg=(
                "Nothing changed, so nothing must be rebuilt: a rebuild reruns "
                "the whole dashboard, and doing that on every poll is worse "
                "than the polling itself."
            ),
        )
        self.assertFalse(
            poll_timer_needs_rebuild(declared=None, desired=None),
            msg="An idle widget that stays idle must not rerun the dashboard.",
        )

    def test_1_231_aborted_offers_a_rerun_nudge_and_error_does_not(self):
        """
        Scenario 1.231: ABORTED is stale state, not a failure.
        Given: ABORTED and ERROR
        When: the widget decides how to present each
        Then: ABORTED gets a re-run nudge, ERROR gets the error treatment, and
              the two read differently. Since PR #675 an aborted row means the
              run was interrupted -- left IN_PROGRESS by a restart and swept --
              so "run it again" is the correct affordance. Collapsing it into
              ERROR is what made incident 1410 hard to read: people went looking
              for a failure that had never happened
        """
        from lex.core.models.CalculationModel import CalculationModel
        from lex.lex_app.streamlit.calculation import presentation_for

        aborted = presentation_for(CalculationModel.ABORTED)
        errored = presentation_for(CalculationModel.ERROR)

        self.assertTrue(
            aborted.suggests_rerun,
            msg=(
                "Interrupted work must invite a re-run -- that is the whole "
                "action available to the reader."
            ),
        )
        self.assertFalse(
            errored.suggests_rerun,
            msg=(
                "A real failure is not a re-run prompt: re-running it without "
                "reading the error just fails again."
            ),
        )
        self.assertNotEqual(
            aborted.label, errored.label,
            msg=(
                "ABORTED and ERROR must not render the same word "
                f"({aborted.label!r}) -- the label is all the reader gets."
            ),
        )

    def test_1_232_failures_render_a_message_and_never_raise(self):
        """
        Scenario 1.232: no backend failure may take the dashboard down.
        Given: each failure the widget can meet -- refused, missing, broken,
               unreachable
        When: the widget reads status
        Then: it returns a renderable state instead of raising, and each one
              says something different. Streamlit renders top-to-bottom, so an
              exception escaping here erases every widget below it on the page:
              the dashboard does not report an error, it silently loses its
              bottom half
        """
        from lex.lex_app.streamlit._client import LexApiError
        from lex.lex_app.streamlit.calculation import read_status

        for status_code, expected in (
            (403, "not available"),
            (404, "not found"),
            (500, "unavailable"),
            (None, "unavailable"),
        ):
            with self.subTest(status=status_code):
                with mock.patch(
                    "lex.lex_app.streamlit.calculation._client.get_json",
                    side_effect=LexApiError("boom", status=status_code),
                ):
                    state = read_status("quarter", 1, token="t", include_log=False)

                self.assertIsNone(
                    state.status,
                    msg=(
                        "A read that failed has no calculation status; "
                        f"got {state.status!r}, which the widget would render as "
                        "a real state of the record."
                    ),
                )
                self.assertIn(
                    expected, state.message.lower(),
                    msg=(
                        f"HTTP {status_code} must explain itself to the reader. "
                        f"Got {state.message!r}."
                    ),
                )

    def test_1_233_the_log_is_not_requested_when_it_is_not_shown(self):
        """
        Scenario 1.233: show_log=False costs nothing.
        Given: a widget with the log panel off -- the default
        When: it reads status
        Then: include_log is not sent at all, so the endpoint skips the
              CalculationLog query entirely. That query is the expensive half of
              the poll, and a dashboard that never opens the panel would
              otherwise pay for it every two seconds for the life of the page
        """
        from lex.lex_app.streamlit.calculation import read_status

        with mock.patch(
            "lex.lex_app.streamlit.calculation._client.get_json",
            return_value={"status": "SUCCESS", "error": None},
        ) as get_json:
            read_status("quarter", 1, token="t", include_log=False)

        params = get_json.call_args.kwargs.get("params") or {}
        self.assertNotIn(
            "include_log", params,
            msg=(
                "The default poll must not ask for the log. Sent params: "
                f"{params!r}."
            ),
        )

    def test_1_234_the_log_flag_is_the_exact_string_the_endpoint_matches(self):
        """
        Scenario 1.234: the opt-in actually opts in.
        Given: a widget with the log panel on
        When: it reads status
        Then: include_log is sent as the string "true". The endpoint compares
              the raw query value to that exact string, so a Python True would
              go on the wire as "True", match nothing, and leave the panel
              permanently empty with no error anywhere to explain it
        """
        from lex.lex_app.streamlit.calculation import read_status

        with mock.patch(
            "lex.lex_app.streamlit.calculation._client.get_json",
            return_value={"status": "SUCCESS", "log": ["a"], "log_truncated": False},
        ) as get_json:
            state = read_status("quarter", 1, token="t", include_log=True)

        params = get_json.call_args.kwargs.get("params") or {}
        self.assertEqual(
            params.get("include_log"), "true",
            msg=(
                "The endpoint matches the literal string 'true'. Got "
                f"{params.get('include_log')!r} -- anything else silently "
                "returns an envelope with no log in it."
            ),
        )
        self.assertEqual(
            state.log, ["a"],
            msg="The returned log must reach the widget's state unchanged.",
        )

    def test_1_235_the_trigger_is_the_react_uis_own_call(self):
        """
        Scenario 1.235: starting a calculation uses the one supported path.
        Given: a record to calculate
        When: the widget triggers it
        Then: PATCH /api/model_entries/<model>/default/one/<pk> carrying
              calculate=true in the body -- byte for byte what the React UI
              sends. One.update reads the flag from request.data, so a flag
              parked in the query string is silently dropped and the PATCH
              degrades into an empty update: no calculation, no error, a button
              that does nothing
        """
        from lex.lex_app.streamlit.calculation import trigger_calculation

        with mock.patch(
            "lex.lex_app.streamlit.calculation._client.patch_json",
            return_value={},
        ) as patch_json:
            failure = trigger_calculation("quarter", 42, token="t")

        self.assertIsNone(
            failure, msg=f"A successful trigger reports no failure; got {failure!r}.",
        )
        self.assertEqual(
            patch_json.call_args.args[0],
            "/api/model_entries/quarter/default/one/42",
            msg=(
                "The trigger must hit the detail route the React UI uses. Got "
                f"{patch_json.call_args.args[0]!r}."
            ),
        )
        self.assertEqual(
            (patch_json.call_args.kwargs.get("json") or {}).get("calculate"), "true",
            msg=(
                "calculate=true must travel in the request body -- One.update "
                "reads request.data and never looks at the query string. Sent "
                f"body {patch_json.call_args.kwargs.get('json')!r}."
            ),
        )

    def test_1_236_a_refused_trigger_is_reported_not_raised(self):
        """
        Scenario 1.236: pressing the button cannot take the page down either.
        Given: a backend that refuses the trigger, and one that is broken
        When: the widget triggers a calculation
        Then: a message comes back instead of an exception, and a refusal reads
              as a permission problem rather than as a server fault. The button
              lives inside the widget, so an exception on this path costs the
              reader the rest of the page just as a failed read would
        """
        from lex.lex_app.streamlit._client import LexApiError
        from lex.lex_app.streamlit.calculation import trigger_calculation

        with mock.patch(
            "lex.lex_app.streamlit.calculation._client.patch_json",
            side_effect=LexApiError("Forbidden", status=403),
        ):
            refused = trigger_calculation("quarter", 42, token="t")
        with mock.patch(
            "lex.lex_app.streamlit.calculation._client.patch_json",
            side_effect=LexApiError("Backend unreachable: boom"),
        ):
            broken = trigger_calculation("quarter", 42, token="t")

        self.assertIn(
            "permission", refused.lower(),
            msg=(
                "A 403 means this user may not run this record; the message has "
                f"to say so. Got {refused!r}."
            ),
        )
        self.assertTrue(
            broken,
            msg=(
                "An unreachable backend must still produce something to render "
                f"-- got {broken!r}, which the widget would read as success."
            ),
        )

    def test_1_237_colours_come_from_the_design_system(self):
        """
        Scenario 1.237: the widget cannot drift from the LEX design system.
        Given: the widget module source
        When: it is scanned for colour literals
        Then: none are found. Every colour resolves from design_system, whose
              freshness CI already gates. LEX success is teal, not green, so a
              copied hex would look right today and keep rendering the old
              palette after the next token refresh -- with nothing failing
        """
        import inspect
        import re

        from lex.lex_app.streamlit import calculation

        source = inspect.getsource(calculation)
        self.assertEqual(
            re.findall(r"#[0-9a-fA-F]{6}\b", source), [],
            msg=(
                "Import the tokens from lex.lex_app.design_system rather than "
                "writing colour literals -- a literal cannot be refreshed."
            ),
        )

    def test_1_238_the_widget_imports_no_django_models(self):
        """
        Scenario 1.238: the widget reaches the backend only over HTTP.
        Given: the widget module's imports
        When: they are inspected
        Then: the only first-party modules are the design system and the widget
              package the HTTP client lives in -- no Django, no models. This is
              the load-bearing constraint of the whole feature: an in-process
              ORM call would skip the record's read permission, resolve the
              wrong audit actor, and miss the _defer_calculate_hook trigger
              path. A second way to start a calculation is what produced the
              edited_at bug in PR #675
        """
        import ast
        import inspect

        from lex.lex_app.streamlit import calculation

        imported = set()
        for node in ast.walk(ast.parse(inspect.getsource(calculation))):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)

        self.assertEqual(
            {n for n in imported if n.split(".")[0] in {"lex", "django", "lex_app"}},
            {"lex.lex_app.design_system", "lex.lex_app.streamlit"},
            msg=(
                "The widget may import the design system and its own package's "
                "HTTP client, nothing else first-party. Found "
                f"{sorted(imported)} -- any model or Django import here is a "
                "path to the database that bypasses DRF permissions and the "
                "audit actor."
            ),
        )


class _FakeStreamlit:
    """Stands in for the ``streamlit`` module at the widget's boundary.

    Real decorators and real context managers, so the widget's own code path is
    what runs -- a plain Mock would let ``@st.fragment`` swallow the render body
    whole and every scenario below would pass without executing anything.
    Records what was rendered so a scenario can assert on it.
    """

    class Rerun(Exception):
        """Stands in for Streamlit's RerunException, which is control flow."""

    def __init__(self, button_returns: bool = False):
        self.session_state: dict = {}
        self.rendered: list = []
        self.button_keys: list = []
        self.buttons: list = []
        self.declared_intervals: list = []
        self.fragments: list = []
        self._button_returns = button_returns

    def fragment(self, func=None, *, run_every=None, **kwargs):
        self.declared_intervals.append(run_every)

        def _declare(f):
            # Kept so a scenario can call the fragment body on its own, which is
            # what a poll tick is: Streamlit re-executes the stored fragment and
            # nothing else on the page.
            self.fragments.append(f)
            return f

        return _declare(func) if func is not None else _declare

    def columns(self, spec, **kwargs):
        import contextlib

        return [contextlib.nullcontext() for _ in spec]

    def button(self, label, *, key=None, disabled=False, **kwargs):
        self.button_keys.append(key)
        self.buttons.append({"label": label, "key": key, "disabled": disabled})
        self.rendered.append(("button", label))
        # Streamlit never reports a press for a button it drew disabled, so
        # neither does this stand-in -- otherwise a scenario could "click" a
        # button the real host would not have let the reader touch.
        return self._button_returns and not disabled

    def rerun(self, *, scope="app"):
        raise self.Rerun(scope)

    def __getattr__(self, name):
        def _render(*args, **kwargs):
            self.rendered.append((name, args[0] if args else None))

        return _render

    def text_of(self) -> str:
        return " | ".join(f"{kind}:{value}" for kind, value in self.rendered)


class TestCluster01ab_CalculationWidgetSurface(SimpleTestCase):
    """Cluster 1ab: the rendered widget and the package's public surface."""

    ENVELOPE = {"status": "SUCCESS", "error": None, "duration_seconds": 12.0}

    def _fake_host(self, token: str = "user-token", **kwargs) -> "_FakeStreamlit":
        fake = _FakeStreamlit(**kwargs)
        fake.session_state["access_token"] = token
        return fake

    def test_1_239_the_last_run_line_separates_never_run_from_a_duration(self):
        """
        Scenario 1.239: "no timings" and "ran instantly" are different things.
        Given: a record that has never run, and one that took 38 seconds
        When: the widget renders its last-run line
        Then: the first says so in words and the second reports the duration.
              The endpoint returns null timings for a record with no log rows,
              and formatting null as a number would put "took 0s" under a record
              that has never been calculated at all
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
              to make room for the new widget
        """
        from lex.lex_app.streamlit import lex_calculation, lex_view
        from lex.lex_app.streamlit.embed import lex_view as legacy_lex_view

        self.assertTrue(
            callable(lex_calculation),
            msg="lex_calculation must be callable straight off the package.",
        )
        self.assertIs(
            lex_view, legacy_lex_view,
            msg=(
                "The package must re-export the very same lex_view, not a copy: "
                "lex.lex_app.streamlit.embed is a documented import path and "
                "existing dashboards use it."
            ),
        )

    def test_1_241_two_widgets_on_one_page_keep_separate_state(self):
        """
        Scenario 1.241: a dashboard may watch more than one record.
        Given: two widgets on one page -- two different records, then the same
               record twice under explicit keys
        When: both render
        Then: each gets its own widget key and its own poll bookkeeping. Every
              piece of widget state is namespaced by that key; sharing one would
              make Streamlit reject the second button as a duplicate widget ID
              and let either record's status overwrite the other's poll interval
        """
        from lex.lex_app.streamlit import calculation

        fake = self._fake_host()
        with mock.patch.object(calculation, "st", fake), mock.patch(
            "lex.lex_app.streamlit.calculation._client.get_json",
            return_value=self.ENVELOPE,
        ):
            calculation.lex_calculation("quarter", 1)
            calculation.lex_calculation("quarter", 2)

        self.assertEqual(
            len(set(fake.button_keys)), 2,
            msg=(
                "Two records must produce two distinct button keys; got "
                f"{fake.button_keys!r}, which Streamlit rejects as a duplicate "
                "widget ID."
            ),
        )
        poll_keys = [k for k in fake.session_state if str(k).endswith("__every")]
        self.assertEqual(
            len(set(poll_keys)), 2,
            msg=(
                "Each widget must keep its own poll bookkeeping; got "
                f"{poll_keys!r}, so one record's status would decide the "
                "other's polling."
            ),
        )

        same_record = self._fake_host()
        with mock.patch.object(calculation, "st", same_record), mock.patch(
            "lex.lex_app.streamlit.calculation._client.get_json",
            return_value=self.ENVELOPE,
        ):
            calculation.lex_calculation("quarter", 1, key="left")
            calculation.lex_calculation("quarter", 1, key="right")

        self.assertEqual(
            len(set(same_record.button_keys)), 2,
            msg=(
                "The key argument is what lets one record appear twice on a "
                f"page; got {same_record.button_keys!r}."
            ),
        )

    def test_1_242_the_token_comes_from_the_streamlit_session(self):
        """
        Scenario 1.242: the widget acts as the signed-in user, never as the host.
        Given: a Streamlit session holding an access token, and one without
        When: the widget resolves the token it will call the backend with
        Then: it reads the host's own session_state["access_token"] -- the key
              lex/streamlit_app.py writes and keeps refreshed -- and produces
              nothing when the session is empty, so an expired session asks for
              a reload instead of calling the backend unauthenticated.

              It must never fall back to LEX_API_KEY: that is the instance's
              machine-to-machine secret, sent as "Api-Key" and resolving to a
              technical user. As a bearer token it would not authenticate at
              all, and if it did it would run one user's calculation under an
              actor that is nobody
        """
        import inspect

        from lex.lex_app.streamlit import calculation

        with mock.patch.object(calculation, "st", self._fake_host("abc")):
            self.assertEqual(
                calculation._session_token(), "abc",
                msg=(
                    "The token must come from the host's session_state, which "
                    "is where lex/streamlit_app.py stores and refreshes it."
                ),
            )
        with mock.patch.object(calculation, "st", self._fake_host("")):
            self.assertIsNone(
                calculation._session_token(),
                msg=(
                    "An empty access_token is not a token; returning it would "
                    "send 'Bearer ' to the backend and read as a server fault "
                    "rather than an expired session."
                ),
            )

        self.assertNotIn(
            "LEX_API_KEY", inspect.getsource(calculation),
            msg=(
                "LEX_API_KEY is the instance's machine secret (Authorization: "
                "Api-Key), not a user token. Falling back to it would silently "
                "detach the run from the person who asked for it."
            ),
        )

    def test_1_243_an_expired_session_asks_for_a_reload_without_calling_out(self):
        """
        Scenario 1.243: a dead session is a message, not a failed request.
        Given: a Streamlit session with no access token
        When: the widget renders
        Then: it renders a prompt, returns None, and never calls the backend.
              An unauthenticated call would come back 401 and be reported as
              "status unavailable" -- pointing the reader at the backend for a
              problem that a page reload fixes
        """
        from lex.lex_app.streamlit import calculation

        fake = _FakeStreamlit()
        with mock.patch.object(calculation, "st", fake), mock.patch(
            "lex.lex_app.streamlit.calculation._client.get_json",
        ) as get_json:
            returned = calculation.lex_calculation("quarter", 1)

        self.assertIsNone(
            returned, msg=f"There is no status to report; got {returned!r}.",
        )
        get_json.assert_not_called()
        self.assertIn(
            "reload", fake.text_of().lower(),
            msg=(
                "The reader needs the action that fixes it. Rendered: "
                f"{fake.text_of()!r}"
            ),
        )

    def test_1_244_a_failed_read_renders_a_message_instead_of_raising(self):
        """
        Scenario 1.244: the whole failure path survives a real render.
        Given: a backend that answers 500
        When: the widget renders
        Then: it renders the failure, returns None and raises nothing -- and it
              does not schedule a poll. Streamlit renders top-to-bottom, so an
              exception escaping here erases every widget below this one; and a
              widget that kept polling a broken backend would have every open
              dashboard retrying it in lockstep for as long as it stays down
        """
        from lex.lex_app.streamlit import calculation
        from lex.lex_app.streamlit._client import LexApiError

        fake = self._fake_host()
        with mock.patch.object(calculation, "st", fake), mock.patch(
            "lex.lex_app.streamlit.calculation._client.get_json",
            side_effect=LexApiError("boom", status=500),
        ):
            returned = calculation.lex_calculation("quarter", 1)

        self.assertIsNone(
            returned,
            msg=f"A failed read reports no status; got {returned!r}.",
        )
        self.assertIn(
            "unavailable", fake.text_of().lower(),
            msg=f"The failure must be rendered. Rendered: {fake.text_of()!r}",
        )
        self.assertEqual(
            [i for i in fake.declared_intervals if i is not None], [],
            msg=(
                "A widget that cannot read status must not schedule a poll; "
                f"declared intervals {fake.declared_intervals!r}."
            ),
        )

    def test_1_245_a_record_found_running_starts_its_watch_in_the_same_run(self):
        """
        Scenario 1.245: discovering work in progress actually starts the watch.
        Given: a record that is IN_PROGRESS
        When: the widget renders
        Then: the fragment is *declared* with the poll interval, in that same
              script run, and no rerun is asked for.

        run_every is read only when a fragment is declared, and only a full
        script run declares one -- so a widget that learns the record is running
        after the declaration can get a timer only by rerunning the whole
        script. This widget learns it before, by reading status ahead of the
        declaration, which is why one render is enough. The rerun it no longer
        asks for was never free: st.rerun() raises, so every widget below this
        one on the page lost its turn and had to ask for its own rerun next time
        round -- one full script run per running widget, each re-reading the
        whole page.
        """
        from lex.lex_app.streamlit import calculation

        fake = self._fake_host()
        with mock.patch.object(calculation, "st", fake), mock.patch(
            "lex.lex_app.streamlit.calculation._client.get_json",
            return_value={"status": "IN_PROGRESS", "error": None},
        ):
            calculation.lex_calculation("quarter", 1, poll_interval=2.0)

        self.assertEqual(
            fake.declared_intervals, [2.0],
            msg=(
                "A record found running must have its poll timer declared in "
                "the very run that found it. Declared "
                f"{fake.declared_intervals!r} -- None here means the widget "
                "renders 'Running' and then never updates."
            ),
        )
        self.assertIn(
            2.0, fake.session_state.values(),
            msg=(
                "The interval the widget is carrying must be recorded, or a "
                "later poll cannot tell whether the browser's timer still "
                f"matches what it wants. Session state: {fake.session_state!r}"
            ),
        )

    def test_1_246_a_settled_record_renders_once_without_rerunning(self):
        """
        Scenario 1.246: a finished record costs one render and nothing more.
        Given: a record in SUCCESS
        When: the widget renders
        Then: it returns the status, declares no poll interval and does not
              rerun. This is the steady state of every dashboard: if a settled
              record still asked for a rerun, the page would rerun itself in a
              loop for as long as it stayed open
        """
        from lex.lex_app.streamlit import calculation

        fake = self._fake_host()
        with mock.patch.object(calculation, "st", fake), mock.patch(
            "lex.lex_app.streamlit.calculation._client.get_json",
            return_value=self.ENVELOPE,
        ):
            returned = calculation.lex_calculation("quarter", 1)

        self.assertEqual(
            (returned or {}).get("status"), "SUCCESS",
            msg=(
                "The widget must hand the envelope back so the dashboard can "
                f"branch on it; got {returned!r}."
            ),
        )
        self.assertEqual(
            fake.declared_intervals, [None],
            msg=(
                "A settled record must declare no poll interval; got "
                f"{fake.declared_intervals!r}."
            ),
        )

    def test_1_247_a_caller_who_may_not_run_gets_a_disabled_button_and_a_reason(self):
        """
        Scenario 1.247: the refusal is rendered before the click, not after it.
        Given: a status envelope reporting that this caller may not calculate,
               and the backend's reason for it
        When: the widget renders
        Then: the button is drawn disabled, the reason is rendered beside it, and
              no trigger is issued. A button that looks usable and is not is a
              worse experience than one that explains itself -- and the point of
              this widget is that the reader does not have to go elsewhere to
              find out what happened. Nothing here decides the permission: the
              server already did, and the widget is only drawing the answer
        """
        from lex.lex_app.streamlit import calculation

        reason = "Only the close team may run the quarterly close."
        fake = self._fake_host(button_returns=True)
        with mock.patch.object(calculation, "st", fake), mock.patch(
            "lex.lex_app.streamlit.calculation._client.get_json",
            return_value={
                "status": "NOT_CALCULATED",
                "error": None,
                "can_calculate": False,
                "calculate_denied_reason": reason,
            },
        ), mock.patch(
            "lex.lex_app.streamlit.calculation._client.patch_json",
        ) as patch_json:
            calculation.lex_calculation("quarter", 1)

        self.assertTrue(
            all(b["disabled"] for b in fake.buttons),
            msg=(
                "A caller the backend will refuse must not be offered a live "
                f"button. Buttons drawn: {fake.buttons!r}."
            ),
        )
        self.assertIn(
            reason, fake.text_of(),
            msg=(
                "A disabled control with no explanation reads as a broken "
                f"dashboard. Rendered: {fake.text_of()!r}"
            ),
        )
        patch_json.assert_not_called()

    def test_1_248_a_running_record_still_disables_the_button(self):
        """
        Scenario 1.248: permission is a second reason to disable, not a
        replacement for the first.
        Given: a record already IN_PROGRESS, whose envelope says this caller may
               calculate
        When: the widget renders
        Then: the button is still disabled. The two reasons are independent --
              somebody allowed to run a record must still not be able to start a
              second run on top of the one already going, which is what the
              duplicate-calculation guard in One.update would otherwise have to
              absorb on every impatient click
        """
        from lex.lex_app.streamlit import calculation

        fake = self._fake_host()
        with mock.patch.object(calculation, "st", fake), mock.patch(
            "lex.lex_app.streamlit.calculation._client.get_json",
            return_value={
                "status": "IN_PROGRESS",
                "error": None,
                "can_calculate": True,
            },
        ):
            calculation.lex_calculation("quarter", 1, poll_interval=2.0)

        self.assertTrue(
            all(b["disabled"] for b in fake.buttons),
            msg=(
                "A record already running must not offer a second trigger. "
                f"Buttons drawn: {fake.buttons!r}."
            ),
        )

    def test_1_249_a_stale_envelope_still_meets_the_403_backstop(self):
        """
        Scenario 1.249: the envelope is an optimisation, never the authority.
        Given: an envelope claiming this caller may calculate, and a backend that
               refuses the trigger anyway
        When: the reader presses the button
        Then: the refusal is rendered, not raised. The flag is read once per poll
              and the permission behind it can change between the poll and the
              click -- and only the PATCH is authoritative, because only the
              PATCH is the thing being authorised. Dropping this path would move
              the decision into the client, where a stale answer becomes a
              silent no-op instead of a message
        """
        from lex.lex_app.streamlit import calculation
        from lex.lex_app.streamlit._client import LexApiError

        fake = self._fake_host(button_returns=True)
        with mock.patch.object(calculation, "st", fake), mock.patch(
            "lex.lex_app.streamlit.calculation._client.get_json",
            return_value={
                "status": "NOT_CALCULATED",
                "error": None,
                "can_calculate": True,
            },
        ), mock.patch(
            "lex.lex_app.streamlit.calculation._client.patch_json",
            side_effect=LexApiError("Forbidden", status=403),
        ):
            calculation.lex_calculation("quarter", 1)

        self.assertFalse(
            any(b["disabled"] for b in fake.buttons),
            msg=(
                "The envelope said this caller may run the record, so the button "
                f"must be live. Buttons drawn: {fake.buttons!r}."
            ),
        )
        self.assertIn(
            "permission", fake.text_of().lower(),
            msg=(
                "A refusal that arrives only on the click must still be "
                f"rendered. Rendered: {fake.text_of()!r}"
            ),
        )

    def test_1_250_an_envelope_without_the_flag_leaves_the_button_alone(self):
        """
        Scenario 1.250: a missing flag is not a denial.
        Given: a status envelope with no can_calculate key at all -- a backend
               older than this field, or a response shaped by an older widget's
               expectations
        When: the widget renders
        Then: the button stays enabled. Absence of an answer must fall back to
              the pre-existing behaviour (press it and find out), because the
              opposite default would disable the button for every user of every
              deployment that has not shipped the endpoint change yet -- with no
              way to press through it
        """
        from lex.lex_app.streamlit import calculation

        fake = self._fake_host()
        with mock.patch.object(calculation, "st", fake), mock.patch(
            "lex.lex_app.streamlit.calculation._client.get_json",
            return_value={"status": "NOT_CALCULATED", "error": None},
        ):
            calculation.lex_calculation("quarter", 1)

        self.assertFalse(
            any(b["disabled"] for b in fake.buttons),
            msg=(
                "An envelope that says nothing about permission must not be "
                f"read as a refusal. Buttons drawn: {fake.buttons!r}."
            ),
        )


class _Clock:
    """A monotonic clock a scenario can move.

    The status cache is time-bounded, so the only way to distinguish "the same
    script run" from "two seconds later, on a poll" without sleeping is to own
    the clock. Substituted for the ``time`` module inside the widget's own
    namespace, so nothing global is touched.
    """

    def __init__(self, start: float = 1000.0):
        self.t = start

    def monotonic(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


class TestCluster01ab_CalculationSharedReads(SimpleTestCase):
    """Cluster 1ab: what a page of these widgets costs the backend."""

    SETTLED = {"status": "SUCCESS", "error": None, "duration_seconds": 12.0}

    def _fake_host(self, token: str = "user-token", **kwargs) -> "_FakeStreamlit":
        fake = _FakeStreamlit(**kwargs)
        fake.session_state["access_token"] = token
        return fake

    def test_1_251_widgets_watching_one_record_share_a_single_read(self):
        """
        Scenario 1.251: a page costs one read per record, not one per widget.
        Given: thirteen widgets on one page over two records -- a status tile
               per line of a report, which is what this widget is for
        When: the page renders
        Then: two backend reads are issued, not thirteen.

        Every read is a blocking HTTP GET inside the render, and Streamlit
        renders top-to-bottom, so thirteen of them are thirteen round trips the
        reader waits through in series before the page finishes. That is the
        whole of "the dashboard feels non-interactive": nothing is broken, there
        is simply a queue. Widgets over the same record ask the same question
        and must get one answer.
        """
        from lex.lex_app.streamlit import calculation

        fake = self._fake_host()
        clock = _Clock()
        with mock.patch.object(calculation, "st", fake), mock.patch.object(
            calculation, "time", clock,
        ), mock.patch(
            "lex.lex_app.streamlit.calculation._client.get_json",
            return_value=self.SETTLED,
        ) as get_json:
            for index in range(13):
                calculation.lex_calculation(
                    "quarter", 1 if index % 2 == 0 else 2, key=f"tile{index}",
                )

        self.assertEqual(
            get_json.call_count, 2,
            msg=(
                "Thirteen widgets over two records must cost two reads; got "
                f"{get_json.call_count}. Each extra read is another blocking "
                "round trip the reader waits through before the page finishes."
            ),
        )
        self.assertEqual(
            len(fake.buttons), 13,
            msg=(
                "Sharing the read must not cost a widget its render; drew "
                f"{len(fake.buttons)} buttons."
            ),
        )

    def test_1_252_a_shared_read_is_never_answered_with_another_question(self):
        """
        Scenario 1.252: the cache answers only the question it was asked.
        Given: widgets over two different records, and two widgets over one
               record that disagree about whether they want the log
        When: they render
        Then: each distinct question costs its own read. A cache keyed loosely
              enough to collapse them would put one record's status under
              another record's name -- and would hand a widget that asked for
              the log an envelope that has no log in it, leaving the panel
              permanently empty with nothing anywhere to explain why
        """
        from lex.lex_app.streamlit import calculation

        fake = self._fake_host()
        clock = _Clock()
        seen = []

        def _record(path, token, params=None):
            seen.append((path, (params or {}).get("include_log")))
            return {"status": "SUCCESS", "error": None, "log": ["a"]}

        with mock.patch.object(calculation, "st", fake), mock.patch.object(
            calculation, "time", clock,
        ), mock.patch(
            "lex.lex_app.streamlit.calculation._client.get_json", side_effect=_record,
        ):
            calculation.lex_calculation("quarter", 1, key="plain")
            calculation.lex_calculation("quarter", 2, key="other-record")
            calculation.lex_calculation("quarter", 1, key="with-log", show_log=True)
            calculation.lex_calculation("quarter", 1, key="plain-again")

        self.assertEqual(
            len(seen), 3,
            msg=(
                "Three distinct questions -- record 1, record 2, record 1 with "
                f"the log -- must cost three reads and no more. Issued {seen!r}."
            ),
        )
        self.assertEqual(
            len({path for path, _ in seen}), 2,
            msg=f"Two records must be read under two distinct paths; got {seen!r}.",
        )
        self.assertIn(
            "true", [flag for _, flag in seen],
            msg=(
                "The widget that asked for the log must issue its own read; "
                f"otherwise its panel stays empty forever. Issued {seen!r}."
            ),
        )

    def test_1_253_an_idle_widget_does_not_read_again_when_the_page_reruns(self):
        """
        Scenario 1.253: a rerun is not a reason to re-read a settled record.
        Given: a widget on a record in SUCCESS, already rendered once
        When: the script runs again -- which is what any other widget's start or
              finish does to the whole page
        Then: no second read is issued.

        This is the point of caching the read rather than merely sharing it
        within one run. A dashboard of finished calculations greys and
        re-renders every time any one of them changes state, and if each rerun
        re-read every record, the cost of one calculation starting would be a
        full sweep of the backend -- for twelve records whose answer nobody
        expects to have changed in the intervening moment.
        """
        from lex.lex_app.streamlit import calculation

        fake = self._fake_host()
        clock = _Clock()
        with mock.patch.object(calculation, "st", fake), mock.patch.object(
            calculation, "time", clock,
        ), mock.patch(
            "lex.lex_app.streamlit.calculation._client.get_json",
            return_value=self.SETTLED,
        ) as get_json:
            calculation.lex_calculation("quarter", 1)
            self.assertEqual(
                get_json.call_count, 1,
                msg="The first render must actually read the status once.",
            )
            clock.advance(0.05)
            calculation.lex_calculation("quarter", 1)

        self.assertEqual(
            get_json.call_count, 1,
            msg=(
                "A rerun moments later must re-render from the read it already "
                f"has; got {get_json.call_count} reads. Multiply the extra one "
                "by every idle widget on the page for the cost of leaving this "
                "out."
            ),
        )

    def test_1_254_a_cached_status_can_never_reach_another_user(self):
        """
        Scenario 1.254: the cache is confined to one signed-in user.
        Given: two sessions -- two readers, two permissions, two bearer tokens
        When: both render a widget on the same record
        Then: neither is served the other's answer; each reads for itself.

        This is the one place the optimisation could do real harm rather than
        merely fail. A status envelope describes a record the other reader may
        have no permission to see at all -- the endpoint answers an unreadable
        record with the same 404 as a missing one precisely so that its
        existence is not disclosed. A cache shared across sessions would hand
        that answer straight over. So the cache lives in session_state, which
        Streamlit scopes to one session; the module holds no process-wide store
        and uses none of Streamlit's own cross-session caches, which is what the
        source gate below keeps true after this test stops being read.
        """
        import ast
        import inspect

        from lex.lex_app.streamlit import calculation

        clock = _Clock()
        alice = self._fake_host("alice-token")
        bob = self._fake_host("bob-token")

        answers = {"alice-token": "SUCCESS", "bob-token": "ERROR"}
        seen_tokens = []

        def _by_caller(path, token, params=None):
            seen_tokens.append(token)
            return {"status": answers[token], "error": None}

        with mock.patch.object(calculation, "time", clock), mock.patch(
            "lex.lex_app.streamlit.calculation._client.get_json", side_effect=_by_caller,
        ):
            with mock.patch.object(calculation, "st", alice):
                calculation.lex_calculation("quarter", 1)
            with mock.patch.object(calculation, "st", bob):
                calculation.lex_calculation("quarter", 1)

        self.assertEqual(
            seen_tokens, ["alice-token", "bob-token"],
            msg=(
                "The second session must issue its own read as itself. Reads "
                f"made as {seen_tokens!r} -- one entry here means one user was "
                "served the other's view of the record."
            ),
        )
        self.assertIn(
            "Error", bob.text_of(),
            msg=(
                "The second reader must see the status their own permissions "
                f"produced. Rendered: {bob.text_of()!r}"
            ),
        )

        # Prose may discuss the caches that are refused; code may not call them.
        # Walked rather than grepped so the reasoning above stays writable.
        tree = ast.parse(inspect.getsource(calculation))
        referenced = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
        referenced |= {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
        for shared in ("cache_data", "cache_resource", "lru_cache"):
            self.assertNotIn(
                shared, referenced,
                msg=(
                    f"{shared} is keyed by arguments and shared by every "
                    "session this server renders, so one reader's status "
                    "envelope would answer another reader's widget. The cache "
                    "must stay in st.session_state, which is already per-user."
                ),
            )
        self.assertIn(
            "session_state", inspect.getsource(calculation._status_cache),
            msg=(
                "The cache must be fetched out of st.session_state and nowhere "
                "else -- that is the only thing making it one user's."
            ),
        )

    def test_1_255_a_click_renders_running_before_any_read_confirms_it(self):
        """
        Scenario 1.255: pressing Calculate answers immediately.
        Given: a record in NOT_CALCULATED, and a reader who presses Calculate
        When: the trigger succeeds
        Then: the badge already reads "Running" in that same render, without a
              second status read.

        The reader pressed a button; the interface has to say something. Waiting
        for the trigger to land and then re-reading the record before admitting
        anything happened is how a click came to take seven seconds to show a
        word -- and for those seven seconds the badge still said "Not
        calculated", so the honest reading of the page was that the button had
        not worked. The trigger has in fact already set the record IN_PROGRESS
        by the time it answers, so this is not a guess: it is declining to ask
        again.
        """
        from lex.lex_app.streamlit import calculation

        fake = self._fake_host(button_returns=True)
        clock = _Clock()
        with mock.patch.object(calculation, "st", fake), mock.patch.object(
            calculation, "time", clock,
        ), mock.patch(
            "lex.lex_app.streamlit.calculation._client.get_json",
            return_value={"status": "NOT_CALCULATED", "error": None},
        ) as get_json, mock.patch(
            "lex.lex_app.streamlit.calculation._client.patch_json", return_value={},
        ) as patch_json:
            with self.assertRaises(
                fake.Rerun,
                msg=(
                    "Starting a run is one of the two moments the script may "
                    "rerun: the browser's poll timer is only built when a "
                    "fragment is declared."
                ),
            ):
                calculation.lex_calculation("quarter", 1)

        patch_json.assert_called_once()
        self.assertIn(
            "Running", fake.text_of(),
            msg=(
                "The click must render its own answer. Rendered: "
                f"{fake.text_of()!r} -- the only status read said "
                "NOT_CALCULATED, so anything else here means the reader is "
                "waiting on a second round trip to be told what just happened."
            ),
        )
        self.assertEqual(
            get_json.call_count, 1,
            msg=(
                "No status read may stand between the trigger and the word "
                f"'Running'; the widget made {get_json.call_count}."
            ),
        )

    def test_1_256_a_refused_trigger_takes_the_optimism_back(self):
        """
        Scenario 1.256: nothing is claimed when nothing was started.
        Given: a backend that refuses the trigger on the click
        When: the reader presses Calculate
        Then: the badge shows the record's real status and the refusal is
              rendered -- and the widget does not start polling. Rendering
              "Running" ahead of the backend is only defensible while the
              backend agrees; a click that was refused and still showed Running
              would leave the reader watching a calculation that does not exist,
              and the widget polling for it
        """
        from lex.lex_app.streamlit import calculation
        from lex.lex_app.streamlit._client import LexApiError

        fake = self._fake_host(button_returns=True)
        clock = _Clock()
        with mock.patch.object(calculation, "st", fake), mock.patch.object(
            calculation, "time", clock,
        ), mock.patch(
            "lex.lex_app.streamlit.calculation._client.get_json",
            return_value={"status": "NOT_CALCULATED", "error": None},
        ), mock.patch(
            "lex.lex_app.streamlit.calculation._client.patch_json",
            side_effect=LexApiError("Forbidden", status=403),
        ):
            calculation.lex_calculation("quarter", 1)

        self.assertIn(
            "permission", fake.text_of().lower(),
            msg=f"The refusal must be rendered. Rendered: {fake.text_of()!r}",
        )
        self.assertNotIn(
            "Running", fake.text_of(),
            msg=(
                "A refused trigger started nothing, so nothing may be claimed. "
                f"Rendered: {fake.text_of()!r}"
            ),
        )
        self.assertEqual(
            [i for i in fake.declared_intervals if i is not None], [],
            msg=(
                "Nothing is running, so nothing must be watched; declared "
                f"{fake.declared_intervals!r}."
            ),
        )

    def test_1_257_the_next_real_read_replaces_the_optimistic_state(self):
        """
        Scenario 1.257: the optimism lasts exactly one render.
        Given: a successful click, and the run that follows it
        When: the widget reads status again
        Then: what the backend says is what is rendered, and the optimistic note
              is gone from session_state. The optimism exists to cover one round
              trip, not to become a second source of truth about the record --
              a flag left set would keep claiming "Running" over a run that had
              already finished, with the real status sitting in hand unrendered
        """
        from lex.lex_app.streamlit import calculation

        fake = self._fake_host(button_returns=True)
        clock = _Clock()
        with mock.patch.object(calculation, "st", fake), mock.patch.object(
            calculation, "time", clock,
        ), mock.patch(
            "lex.lex_app.streamlit.calculation._client.get_json",
            return_value={"status": "NOT_CALCULATED", "error": None},
        ), mock.patch(
            "lex.lex_app.streamlit.calculation._client.patch_json", return_value={},
        ):
            with self.assertRaises(fake.Rerun):
                calculation.lex_calculation("quarter", 1)

        fake._button_returns = False
        with mock.patch.object(calculation, "st", fake), mock.patch.object(
            calculation, "time", clock,
        ), mock.patch(
            "lex.lex_app.streamlit.calculation._client.get_json",
            return_value={"status": "IN_PROGRESS", "error": None},
        ) as get_json:
            calculation.lex_calculation("quarter", 1)

        self.assertGreaterEqual(
            get_json.call_count, 1,
            msg=(
                "The run that follows a click must actually re-read the record "
                "-- the click invalidated what was cached for it, precisely so "
                "the optimism is checked rather than trusted."
            ),
        )
        self.assertFalse(
            any(v is True for k, v in fake.session_state.items() if "pending" in str(k)),
            msg=(
                "The optimistic note must be spent by the first read that "
                f"follows it. Session state: {fake.session_state!r}"
            ),
        )

    def test_1_258_a_running_record_does_not_cost_the_widgets_below_it(self):
        """
        Scenario 1.258: one running record does not rerun the page.
        Given: three widgets on a page, the middle one on a record that is
               already IN_PROGRESS -- a page loaded while something runs
        When: the page renders
        Then: all three render, in one pass, with no rerun asked for.

        st.rerun() raises. A widget that decided its poll timer after rendering
        could only get one by rerunning the whole script, and that exception
        travelled out through every widget still to come -- so widget four
        onwards never rendered, discovered their own running records on the next
        run, and each asked for a rerun of their own. A page watching several
        calculations paid a full script run, and a full sweep of the backend,
        per running widget. Reading before declaring is what makes one pass
        enough.
        """
        from lex.lex_app.streamlit import calculation

        fake = self._fake_host()
        clock = _Clock()
        statuses = {1: "SUCCESS", 2: "IN_PROGRESS", 3: "SUCCESS"}

        def _by_record(path, token, params=None):
            # /api/model_entries/<model>/<pk>/calculation-status
            pk = int(path.rstrip("/").split("/")[-2])
            return {"status": statuses[pk], "error": None}

        with mock.patch.object(calculation, "st", fake), mock.patch.object(
            calculation, "time", clock,
        ), mock.patch(
            "lex.lex_app.streamlit.calculation._client.get_json", side_effect=_by_record,
        ):
            for pk in (1, 2, 3):
                calculation.lex_calculation("quarter", pk, poll_interval=2.0)

        self.assertEqual(
            len(fake.buttons), 3,
            msg=(
                "Every widget on the page must render in the pass that found "
                f"the running record; only {len(fake.buttons)} did. The rest "
                "lost their turn to an exception raised on their behalf."
            ),
        )
        self.assertEqual(
            fake.declared_intervals, [None, 2.0, None],
            msg=(
                "Only the running record may carry a timer, and it must carry "
                f"it from this pass. Declared {fake.declared_intervals!r}."
            ),
        )

    def test_1_259_a_poll_reruns_the_page_when_the_run_ends_and_not_before(self):
        """
        Scenario 1.259: polling is silent until there is news.
        Given: a widget watching a running record, polled twice -- once while
               the work continues, once after it finishes
        When: each poll runs
        Then: the first re-reads and asks for nothing; the second asks for a
              rerun, because the browser's poll timer can only be taken down by
              a run that re-declares the fragment without one.

        A rerun greys the whole page and re-renders every widget on it. Doing
        that on each poll is what made a page with one calculation running feel
        like a page that had stopped working. Doing it never would leave the
        timer alive in the browser for as long as the tab stays open, polling a
        record that will not change again -- so it happens exactly twice per
        calculation, at the two moments the reader can account for.
        """
        from lex.lex_app.streamlit import calculation

        fake = self._fake_host()
        clock = _Clock()
        envelope = {"status": "IN_PROGRESS", "error": None}

        with mock.patch.object(calculation, "st", fake), mock.patch.object(
            calculation, "time", clock,
        ), mock.patch(
            "lex.lex_app.streamlit.calculation._client.get_json",
            side_effect=lambda *a, **k: envelope,
        ) as get_json:
            calculation.lex_calculation("quarter", 1, poll_interval=2.0)
            tick = fake.fragments[-1]

            reads_after_render = get_json.call_count
            clock.advance(2.0)
            tick()
            self.assertEqual(
                get_json.call_count, reads_after_render + 1,
                msg=(
                    "A poll must actually re-read the record; the cache exists "
                    "to spare a page its duplicates, not to answer the poll "
                    "with the value it is checking."
                ),
            )

            envelope = {"status": "SUCCESS", "error": None, "duration_seconds": 3.0}
            clock.advance(2.0)
            with self.assertRaises(
                fake.Rerun,
                msg=(
                    "The poll that finds the run finished must rerun the "
                    "script: only a full run re-declares the fragment, and only "
                    "a re-declared fragment drops the browser's timer. Without "
                    "it the tab polls a settled record until it is closed."
                ),
            ):
                tick()

        self.assertIsNone(
            calculation.poll_interval_for("SUCCESS", 2.0),
            msg="A finished run must ask for no further polling.",
        )


#: A page of status tiles over two records -- the shape that exposed all of
#: this. The run counter is in session_state because that is the only thing
#: that survives a rerun, and counting reruns is the point.
_PAGE_OF_TILES = """
import streamlit as st
from lex.lex_app.streamlit import lex_calculation

st.session_state["script_runs"] = st.session_state.get("script_runs", 0) + 1
for index in range(13):
    lex_calculation("quarter", 1 if index % 2 == 0 else 2, key=f"tile{index}")
"""


class TestCluster01ab_CalculationRerunBudget(SimpleTestCase):
    """Cluster 1ab: the page's cost, measured against the real Streamlit runtime.

    The claims here -- how many times a script runs, how many reads a page
    costs, whether one session can see another's cache -- are claims about
    Streamlit's own behaviour: how it re-executes a script after st.rerun(), and
    how far session_state reaches. A stand-in for the streamlit module can be
    made to agree with any of them, so these use AppTest, which runs the real
    thing.
    """

    SETTLED = {"status": "SUCCESS", "error": None, "duration_seconds": 12.0}

    def _page(self, script: str = _PAGE_OF_TILES, token: str = "user-token"):
        from streamlit.testing.v1 import AppTest

        page = AppTest.from_string(script)
        page.session_state["access_token"] = token
        return page

    def test_1_260_a_page_of_tiles_loads_in_one_run_and_two_reads(self):
        """
        Scenario 1.260: the whole page costs what one record costs, twice.
        Given: thirteen tiles over two settled records
        When: the page loads, and then reruns
        Then: the script runs once, issuing two reads, and the rerun issues
              none.

        Two separate failures are being held down at once, and only the real
        runtime can show both. Thirteen reads is thirteen blocking round trips
        in series. And a script that reruns itself while rendering is a page
        that greys, discards what it drew and starts again -- for a reader,
        indistinguishable from a page that will not settle.
        """
        page = self._page()
        with mock.patch(
            "lex.lex_app.streamlit.calculation._client.get_json",
            return_value=self.SETTLED,
        ) as get_json:
            page.run()

        self.assertEqual(
            page.exception, [],
            msg=f"The page must render cleanly; got {page.exception!r}.",
        )
        self.assertEqual(
            page.session_state["script_runs"], 1,
            msg=(
                "Thirteen settled tiles must not rerun the script at all; it "
                f"ran {page.session_state['script_runs']} times. Each rerun "
                "greys the page and re-renders every widget on it."
            ),
        )
        self.assertEqual(
            get_json.call_count, 2,
            msg=(
                "Two records must cost two reads however many tiles show them; "
                f"got {get_json.call_count}."
            ),
        )

        with mock.patch(
            "lex.lex_app.streamlit.calculation._client.get_json",
            return_value=self.SETTLED,
        ) as rerun_reads:
            page.run()

        self.assertEqual(
            rerun_reads.call_count, 0,
            msg=(
                "A rerun moments later must re-render from what it already "
                f"read; it issued {rerun_reads.call_count} reads. This is the "
                "cost every other widget pays when any one of them changes "
                "state."
            ),
        )

    def test_1_261_pressing_calculate_costs_one_rerun_not_one_per_tile(self):
        """
        Scenario 1.261: a click starts a run; it does not restart the page.
        Given: a page of thirteen tiles, and a reader who presses one Calculate
        When: the trigger succeeds
        Then: the script runs once more -- the one rerun that builds the poll
              timer -- and the page re-reads only the record that changed.

        Before, the click reran the page and every tile on it re-read its own
        record in series before the badge was allowed to say "Running": the
        seven seconds the reader was actually complaining about. The rerun
        itself cannot go, because the browser's poll timer is only built when a
        fragment is declared and only a full run declares one. What goes is its
        price.
        """
        page = self._page()
        with mock.patch(
            "lex.lex_app.streamlit.calculation._client.get_json",
            return_value=self.SETTLED,
        ):
            page.run()

        runs_before = page.session_state["script_runs"]

        with mock.patch(
            "lex.lex_app.streamlit.calculation._client.get_json",
            return_value={"status": "IN_PROGRESS", "error": None},
        ) as get_json, mock.patch(
            "lex.lex_app.streamlit.calculation._client.patch_json", return_value={},
        ) as patch_json:
            page.button(key="tile0__btn").click().run()

        patch_json.assert_called_once()
        self.assertEqual(
            page.exception, [],
            msg=f"The click must not break the page; got {page.exception!r}.",
        )
        self.assertLessEqual(
            page.session_state["script_runs"] - runs_before, 2,
            msg=(
                "A click may cost the run that handles it and the one rerun "
                "that installs the poll timer, and no more. The script ran "
                f"{page.session_state['script_runs'] - runs_before} times."
            ),
        )
        self.assertLessEqual(
            get_json.call_count, 2,
            msg=(
                "Only the record that was just triggered has news; the other "
                "twelve tiles must not be re-read to find that out. The click "
                f"issued {get_json.call_count} reads."
            ),
        )

    def test_1_262_two_sessions_never_see_each_others_cached_status(self):
        """
        Scenario 1.262: the cache stops at the session boundary, for real.
        Given: two Streamlit sessions -- two readers on the same server -- both
               showing a widget on the same record
        When: each renders
        Then: each issues its own read, as itself, and renders its own answer.

        1.254 pins the same rule against a stand-in for session_state; this
        pins it against the runtime that actually enforces the boundary,
        because the consequence of being wrong is not a slow page. The status
        endpoint answers a record this caller may not read with the same 404 as
        a record that does not exist, specifically so that its existence is not
        disclosed -- and a cache reaching across sessions would hand over the
        answer that machinery exists to withhold.
        """
        one_tile = """
import streamlit as st
from lex.lex_app.streamlit import lex_calculation

lex_calculation("quarter", 1)
"""
        answers = {"alice-token": "SUCCESS", "bob-token": "ERROR"}
        callers = []

        def _by_caller(path, token, params=None):
            callers.append(token)
            return {"status": answers[token], "error": None}

        alice = self._page(one_tile, token="alice-token")
        bob = self._page(one_tile, token="bob-token")
        with mock.patch(
            "lex.lex_app.streamlit.calculation._client.get_json", side_effect=_by_caller,
        ):
            alice.run()
            bob.run()

        self.assertEqual(
            callers, ["alice-token", "bob-token"],
            msg=(
                "Each session must read as itself. Reads made as "
                f"{callers!r} -- a single entry means one reader was shown a "
                "record's status through the other's permissions."
            ),
        )
        rendered = " ".join(m.value for m in bob.markdown)
        self.assertIn(
            "Error", rendered,
            msg=(
                "The second session must render the answer its own read "
                f"produced. Rendered: {rendered!r}"
            ),
        )
