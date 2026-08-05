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

Cluster 1ab -- scenarios 1.223-1.246. Type: U.
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
        self.declared_intervals: list = []
        self._button_returns = button_returns

    def fragment(self, func=None, *, run_every=None, **kwargs):
        self.declared_intervals.append(run_every)
        return func if func is not None else (lambda f: f)

    def columns(self, spec, **kwargs):
        import contextlib

        return [contextlib.nullcontext() for _ in spec]

    def button(self, label, *, key=None, disabled=False, **kwargs):
        self.button_keys.append(key)
        self.rendered.append(("button", label))
        return self._button_returns

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
        poll_keys = [k for k in fake.session_state if k != "access_token"]
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

    def test_1_245_a_record_found_running_reruns_to_start_the_poll_timer(self):
        """
        Scenario 1.245: discovering work in progress actually starts the watch.
        Given: a record that is IN_PROGRESS, and a widget whose fragment was
               declared without a poll interval
        When: the widget renders
        Then: it stores the interval and asks for a full script rerun. run_every
              is read only when a fragment is declared, and only a full script
              run declares one, so without the rerun the widget would render
              "Running" once and then sit there forever -- the status would never
              update and no error would ever appear
        """
        from lex.lex_app.streamlit import calculation

        fake = self._fake_host()
        with mock.patch.object(calculation, "st", fake), mock.patch(
            "lex.lex_app.streamlit.calculation._client.get_json",
            return_value={"status": "IN_PROGRESS", "error": None},
        ):
            with self.assertRaises(
                fake.Rerun,
                msg=(
                    "Finding the record running must rerun the script; without "
                    "it the browser never gets a poll timer."
                ),
            ):
                calculation.lex_calculation("quarter", 1, poll_interval=2.0)

        self.assertIn(
            2.0, fake.session_state.values(),
            msg=(
                "The interval must be stored before the rerun, or the "
                "re-declared fragment reads nothing back. Session state: "
                f"{fake.session_state!r}"
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
