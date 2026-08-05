"""Read-only calculation-status endpoint for the Streamlit widget.

Intent: a Streamlit dashboard polls this every couple of seconds to render a
calculation's live state. Polling the full record serialization to learn one
enum is wasteful on wide models and cannot carry the log tail, so the widget
gets a purpose-built endpoint. It must expose exactly the state the widget
renders and nothing the caller is not allowed to see -- a response that
confirms a record exists and errored, to someone who cannot read that record,
is a leak.

Cluster 10o — scenarios 10.72–10.83. Type: E.
Covers: lex/api/views/calculations/CalculationStatus.py.
Run: python -m lex pytest lex/test_project/tests/api_layer/test_10o_calculation_status_endpoint.py -v
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from django.contrib.auth.models import Group
from django.contrib.contenttypes.models import ContentType
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from lex.audit_logging.models.CalculationLog import CalculationLog
from lex.core.models.CalculationModel import CalculationModel
from lex.test_project.tests._authenticated_e2e_test_case import AuthenticatedE2ETestCase
from lex.test_project.tests._e2e_test_case import E2ETestCase

from .models import (
    ALL_MODELS,
    API_LAYER_CALC_RESTRICTED,
    API_LAYER_CALC_RUN_RESTRICTED,
    NO_CALCULATE_GROUP,
    NO_CALCULATE_VIOLATION,
    ApiLayerCalc,
    ApiLayerCalcCalculateRestricted,
    ApiLayerCalcReadRestricted,
)

pytestmark = pytest.mark.api_layer

CALC = "apilayercalc"


class TestCluster10o_CalculationStatusEndpoint(E2ETestCase):
    """Cluster 10o: the status contract the Streamlit widget polls."""

    e2e_models = ALL_MODELS

    #: A pk that certainly does not exist — a dashboard pinned to a deleted record.
    MISSING_PK = 999999

    def url_status(self, model_name: str, pk: int) -> str:
        return f"/api/model_entries/{model_name}/{pk}/calculation-status"

    def _make_logs(
        self,
        instance,
        count: int,
        *,
        calculation_id: str | None = None,
        first_at=None,
        step=timedelta(seconds=1),
        prefix: str = "line",
    ):
        """Create ``count`` CalculationLog rows for ``instance``, ``step`` apart.

        ``calculation_id`` identifies the run the rows belong to — pass it to
        give a record more than one run's worth of log. ``prefix`` labels the
        line text, so a scenario with two runs can tell whose line it is
        looking at.

        ``timestamp`` is ``auto_now_add``, so rows written in a loop can land on
        the same microsecond and leave "which line is newest" up to the
        database. The stamps are rewritten afterwards so the log has one
        unambiguous chronological order for the assertions to hold against.
        Returns those timestamps, oldest first.
        """
        content_type = ContentType.objects.get_for_model(type(instance))
        calculation_id = calculation_id or f"calc-{instance.pk}"
        first_at = timezone.now() - step * count if first_at is None else first_at
        stamps = []
        for i in range(count):
            row = CalculationLog.objects.create(
                calculationId=calculation_id,
                calculation_log=f"{prefix} {i}",
                content_type=content_type,
                object_id=instance.pk,
            )
            at = first_at + step * i
            CalculationLog.objects.filter(pk=row.pk).update(timestamp=at)
            stamps.append(at)
        return stamps

    def test_10_72_returns_status_for_a_never_calculated_record(self):
        """
        Scenario 10.72: a fresh record reports NOT_CALCULATED with no run data.
        Given: a calculation record that has never been run
        When: the widget polls its status
        Then: status is NOT_CALCULATED and the timing fields are null, so the
              widget can render "Never run" without guessing
        """
        item = ApiLayerCalc.objects.create(name="fresh")

        resp = self.client.get(self.url_status(CALC, item.pk))

        self.assertEqual(resp.status_code, 200, resp.content)
        body = resp.json()
        self.assertEqual(body["status"], CalculationModel.NOT_CALCULATED)
        self.assertIsNone(body["started_at"], "A record never run has no start time.")
        self.assertIsNone(body["finished_at"])
        self.assertIsNone(body["error"])

    def test_10_73_reports_each_terminal_status_distinctly(self):
        """
        Scenario 10.73: ABORTED and CANCELLED are not collapsed into ERROR.
        Given: records parked in each state a calculation can end (or sit) in
        When: the widget polls each one
        Then: each reports its own state verbatim. The widget picks its message
              from this one value: an aborted row is a stale state that earns a
              re-run nudge, a cancelled row is the user's own doing, and neither
              is a failure. Collapsing them into ERROR is what made incident
              1410 unreadable.
        """
        for state in (
            CalculationModel.SUCCESS,
            CalculationModel.ERROR,
            CalculationModel.ABORTED,
            CalculationModel.CANCELLED,
            CalculationModel.IN_PROGRESS,
        ):
            with self.subTest(state=state):
                item = ApiLayerCalc.objects.create(name=f"c-{state}")
                ApiLayerCalc.objects.filter(pk=item.pk).update(is_calculated=state)

                resp = self.client.get(self.url_status(CALC, item.pk))

                self.assertEqual(
                    resp.status_code, 200,
                    msg=(
                        f"Polling a record in {state} must succeed. Got "
                        f"{resp.status_code} with body {resp.content!r}."
                    ),
                )
                self.assertEqual(
                    resp.json()["status"], state,
                    msg=(
                        f"A record in {state} must be reported as {state!r}, not "
                        f"{resp.json()['status']!r}. The widget has nothing else "
                        "to distinguish a stale run from a failed one."
                    ),
                )

    def test_10_74_surfaces_the_calculation_error_message(self):
        """
        Scenario 10.74: a failed calculation returns its error text.
        Given: a record in ERROR carrying the message its calculation died with
        When: the widget polls
        Then: status and message arrive together in the same envelope, so the
              dashboard can explain the failure in place instead of sending the
              user into the table to find out why.
        """
        message = "ValueError: no FX rate for 2026-03-31"
        item = ApiLayerCalc.objects.create(name="failed")
        ApiLayerCalc.objects.filter(pk=item.pk).update(
            is_calculated=CalculationModel.ERROR,
            calculation_error_message=message,
        )

        resp = self.client.get(self.url_status(CALC, item.pk))

        self.assertEqual(
            resp.status_code, 200,
            msg=f"Polling a failed record must succeed. Got {resp.content!r}.",
        )
        body = resp.json()
        self.assertEqual(
            body["status"], CalculationModel.ERROR,
            msg=(
                "The error text is only rendered for a record the envelope also "
                f"reports as ERROR. Got status {body['status']!r}."
            ),
        )
        self.assertEqual(
            body["error"], message,
            msg=(
                "The envelope must carry the calculation's own error text, "
                f"verbatim. Expected {message!r}, got {body['error']!r} — a "
                "widget with no message can only say 'it failed'."
            ),
        )

    def test_10_75_unknown_pk_is_a_404(self):
        """
        Scenario 10.75: a stale pk in a dashboard does not 500.
        Given: a pk that no longer exists, e.g. a dashboard left pointing at a
               deleted record
        When: the widget polls it
        Then: a plain 404, so the widget renders "Record not found" rather than
              an exception that would take out everything below it on the page.
        """
        resp = self.client.get(self.url_status(CALC, self.MISSING_PK))

        self.assertEqual(
            resp.status_code, 404,
            msg=(
                "Polling a pk that does not exist must be a clean 404, not "
                f"{resp.status_code}. Body: {resp.content!r}"
            ),
        )

    def test_10_77_log_is_absent_unless_requested(self):
        """
        Scenario 10.77: the default poll never pays for log data.
        Given: a record that has log lines
        When: the widget polls it without asking for the log
        Then: the envelope carries no log at all, and no query reads the log
              line column. The widget's log panel is off by default, and a
              dashboard that never opens it must not drag the lines out of the
              database every two seconds for the life of the page.
        """
        item = ApiLayerCalc.objects.create(name="quiet")
        self._make_logs(item, count=3)
        line_column = CalculationLog._meta.get_field("calculation_log").column

        with CaptureQueriesContext(connection) as queries:
            body = self.client.get(self.url_status(CALC, item.pk)).json()

        self.assertNotIn(
            "log", body,
            msg=(
                "A poll that did not ask for the log must not carry one. "
                f"Got envelope keys {sorted(body)}."
            ),
        )
        self.assertNotIn(
            "log_truncated", body,
            msg=(
                "log_truncated describes a log that is not there — it must be "
                f"absent too. Got envelope keys {sorted(body)}."
            ),
        )
        line_reads = [
            q["sql"] for q in queries.captured_queries if line_column in q["sql"]
        ]
        self.assertEqual(
            line_reads, [],
            msg=(
                "The default poll must not read log lines from the database. "
                f"These queries touched the {line_column!r} column: {line_reads}"
            ),
        )

    def test_10_78_log_is_bounded_and_reports_truncation(self):
        """
        Scenario 10.78: a long calculation cannot bloat a 2-second poll.
        Given: a record with more log lines than the tail limit
        When: the widget polls with include_log=true
        Then: exactly the newest LOG_TAIL_LIMIT lines come back, oldest first,
              and log_truncated tells the widget that earlier lines were
              dropped. A calculation that logs per row would otherwise put its
              whole history on the wire every two seconds — and a tail printed
              backwards is worse than no tail.
        """
        from lex.api.views.calculations.CalculationStatus import LOG_TAIL_LIMIT

        overflow = 10
        count = LOG_TAIL_LIMIT + overflow
        item = ApiLayerCalc.objects.create(name="chatty")
        self._make_logs(item, count=count)

        body = self.client.get(
            self.url_status(CALC, item.pk) + "?include_log=true"
        ).json()

        self.assertEqual(
            len(body["log"]), LOG_TAIL_LIMIT,
            msg=(
                f"A {count}-line log must arrive trimmed to the "
                f"{LOG_TAIL_LIMIT}-line tail. Got {len(body['log'])} lines."
            ),
        )
        self.assertEqual(
            body["log"], [f"line {i}" for i in range(overflow, count)],
            msg=(
                "The tail must be the NEWEST lines, oldest first — that is the "
                f"order the widget prints them in. Got {body['log'][:3]} … "
                f"{body['log'][-3:]}."
            ),
        )
        self.assertIs(
            body["log_truncated"], True,
            msg=(
                f"{overflow} lines were dropped, so the widget must be told the "
                f"log is partial. Got log_truncated={body['log_truncated']!r}."
            ),
        )

    def test_10_79_short_log_comes_back_whole_and_unflagged(self):
        """
        Scenario 10.79: a log that fits is not reported as truncated.
        Given: a record with fewer log lines than the tail limit
        When: the widget polls with include_log=true
        Then: every line comes back and log_truncated is False, so the widget
              says "earlier lines omitted" only when lines really were omitted.
              A flag that is always on tells the reader nothing.
        """
        from lex.api.views.calculations.CalculationStatus import LOG_TAIL_LIMIT

        count = 3
        self.assertLess(
            count, LOG_TAIL_LIMIT,
            msg="This scenario needs a log that fits inside the tail limit.",
        )
        item = ApiLayerCalc.objects.create(name="brief")
        self._make_logs(item, count=count)

        body = self.client.get(
            self.url_status(CALC, item.pk) + "?include_log=true"
        ).json()

        self.assertEqual(
            body["log"], [f"line {i}" for i in range(count)],
            msg=(
                "A log shorter than the limit must come back whole and in "
                f"order. Got {body['log']}."
            ),
        )
        self.assertIs(
            body["log_truncated"], False,
            msg=(
                f"All {count} lines were returned, so nothing was truncated. "
                f"Got log_truncated={body['log_truncated']!r}."
            ),
        )

    def test_10_80_reports_the_last_run_timings_from_the_calculation_log(self):
        """
        Scenario 10.80: "last run" timings come from the log, and from the last
        run only.
        Given: a record calculated yesterday and again just now, the later run
               spanning 38 seconds
        When: the widget polls
        Then: started_at, finished_at and duration_seconds describe the later
              run alone. The record carries no timestamp of its own — since PR
              #675 a calculation deliberately does not stamp edited_at — so the
              log is the only trace of when a run happened; and a window
              stretched across every run the record ever had would render "took
              1 day" under a result that took under a minute.
        """
        item = ApiLayerCalc.objects.create(name="timed")
        ApiLayerCalc.objects.filter(pk=item.pk).update(
            is_calculated=CalculationModel.SUCCESS,
        )
        self._make_logs(
            item,
            count=3,
            calculation_id="run-yesterday",
            first_at=timezone.now() - timedelta(days=1),
            step=timedelta(seconds=5),
        )
        last_run = self._make_logs(
            item,
            count=3,
            calculation_id="run-current",
            first_at=timezone.now() - timedelta(seconds=38),
            step=timedelta(seconds=19),
        )

        body = self.client.get(self.url_status(CALC, item.pk)).json()

        self.assertIsNotNone(
            body["started_at"],
            msg=(
                "A record that has been calculated must report when its last "
                "run started — the widget's 'Last run' line has no other source."
            ),
        )
        self.assertIsNotNone(
            body["finished_at"],
            msg="A finished run must report when it finished.",
        )
        self.assertEqual(
            datetime.fromisoformat(body["started_at"]), last_run[0],
            msg=(
                "started_at must be the first log line of the LATEST run "
                f"({last_run[0].isoformat()}), not of the record's whole log "
                f"history. Got {body['started_at']}."
            ),
        )
        self.assertEqual(
            datetime.fromisoformat(body["finished_at"]), last_run[-1],
            msg=(
                "finished_at must be the last log line of the latest run "
                f"({last_run[-1].isoformat()}). Got {body['finished_at']}."
            ),
        )
        self.assertEqual(
            body["duration_seconds"], 38.0,
            msg=(
                "The widget prints this as 'took 38s'. Got "
                f"{body['duration_seconds']!r} — a duration covering the "
                "yesterday run too would read as 86438s."
            ),
        )

    def test_10_81_the_tail_covers_only_the_latest_run(self):
        """
        Scenario 10.81: a re-run's tail carries none of the previous run's lines.
        Given: a record whose earlier run logged plenty, re-run just now with a
               handful of lines — fewer than the tail limit
        When: the widget polls with include_log=true
        Then: only the current run's lines come back. started_at/finished_at are
              already scoped to the newest calculationId (10.80), so an unscoped
              tail pads the newest run out of yesterday's rows and prints them
              under a header claiming the run took 38 seconds — the log then
              contradicts the timings in the same envelope.
        """
        item = ApiLayerCalc.objects.create(name="rerun")
        ApiLayerCalc.objects.filter(pk=item.pk).update(
            is_calculated=CalculationModel.SUCCESS,
        )
        self._make_logs(
            item,
            count=8,
            calculation_id="run-previous",
            first_at=timezone.now() - timedelta(days=1),
            prefix="previous",
        )
        current_lines = 3
        self._make_logs(
            item,
            count=current_lines,
            calculation_id="run-current",
            first_at=timezone.now() - timedelta(seconds=30),
            prefix="current",
        )

        body = self.client.get(
            self.url_status(CALC, item.pk) + "?include_log=true"
        ).json()

        self.assertEqual(
            body["log"], [f"current {i}" for i in range(current_lines)],
            msg=(
                "The tail must be exactly the latest run's lines, oldest first. "
                f"Got {body['log']} — anything from 'run-previous' is a line "
                "from yesterday rendered as part of a run that took 30s."
            ),
        )
        self.assertIs(
            body["log_truncated"], False,
            msg=(
                f"The latest run logged {current_lines} lines, well inside the "
                "tail limit, so nothing about it was truncated. Got "
                f"log_truncated={body['log_truncated']!r} — counting the "
                "previous run's rows towards the limit is the same bug seen "
                "from the other side."
            ),
        )


class TestCluster10o_CalculationStatusEndpoint_ReadDenied(AuthenticatedE2ETestCase):
    """Cluster 10o: the endpoint must never confirm a record the caller cannot read.

    The caller here is a real, authenticated user in the ``deny_all`` group, so
    ``ApiLayerCalcReadRestricted.permission_read`` denies every row through the
    framework's own permission pipeline — no monkeypatching.
    """

    e2e_models = ALL_MODELS
    as_superuser = False
    extra_groups = frozenset({"deny_all"})

    #: A pk that certainly does not exist — the "record is missing" baseline.
    MISSING_PK = 999999

    def url_status(self, model_name: str, pk: int) -> str:
        return f"/api/model_entries/{model_name}/{pk}/calculation-status"

    def test_10_76_unreadable_record_is_indistinguishable_from_missing(self):
        """
        Scenario 10.76: the endpoint never confirms a record the caller cannot read.
        Given: an errored record the requesting user has no read permission for
        When: the widget polls that record's status and a nonexistent pk's status
        Then: the two responses are byte-for-byte identical — same status code and
              same body. A distinguishable 403 (or any differing body) would
              confirm the record exists and leak its calculation state to someone
              not allowed to see it
        """
        item = ApiLayerCalcReadRestricted.objects.create(name="secret")
        ApiLayerCalcReadRestricted.objects.filter(pk=item.pk).update(
            is_calculated=CalculationModel.ERROR,
            calculation_error_message="boom: secret internal failure",
        )

        denied = self.client.get(self.url_status(API_LAYER_CALC_RESTRICTED, item.pk))
        missing = self.client.get(
            self.url_status(API_LAYER_CALC_RESTRICTED, self.MISSING_PK),
        )

        self.assertEqual(
            denied.status_code, missing.status_code,
            msg=(
                "A record the caller may not read must return the SAME status code "
                "as a record that does not exist. Got "
                f"{denied.status_code} for the unreadable record vs "
                f"{missing.status_code} for the missing pk — the difference alone "
                "confirms the record exists."
            ),
        )
        self.assertEqual(
            denied.json(), missing.json(),
            msg=(
                "A record the caller may not read must return the SAME body as a "
                "record that does not exist. Got "
                f"{denied.json()!r} vs {missing.json()!r} — the difference leaks "
                "the record's calculation state."
            ),
        )
        self.assertNotIn(
            "boom: secret internal failure", denied.content.decode(),
            msg=(
                "The denied response must not carry the record's error text. "
                f"Got body: {denied.content.decode()!r}"
            ),
        )
        # Pin the shared contract itself: without this, both responses
        # degenerating to the same 200 would satisfy the equality checks above
        # while still leaking.
        self.assertEqual(
            denied.status_code, 404,
            msg=(
                "The shared response for unreadable/missing must be a plain 404, "
                f"not {denied.status_code}."
            ),
        )
        self.assertEqual(
            denied.json(), {"detail": "Not found."},
            msg=(
                "The shared 404 body must carry no record-specific detail. "
                f"Got {denied.json()!r}"
            ),
        )


class TestCluster10o_CalculationStatus_CalculatePermission(E2ETestCase):
    """Cluster 10o: ``can_calculate`` must agree with what ``One.update`` enforces.

    The widget draws its Calculate button from this one flag, so the flag is a
    promise about a *different* endpoint. Both scenarios below therefore assert
    the envelope **and** issue the real trigger, because a flag that merely looks
    plausible is exactly the failure this pair exists to catch: disagree one way
    and the button is disabled for someone who may run the record, disagree the
    other and it is enabled for someone who may not.

    ``ApiLayerCalcCalculateRestricted`` is readable by everyone and runnable only
    outside ``no_calculate``, so read permission cannot stand in for the answer.
    """

    e2e_models = ALL_MODELS

    def url_status(self, model_name: str, pk: int) -> str:
        return f"/api/model_entries/{model_name}/{pk}/calculation-status"

    def _block_this_caller(self):
        """Put the signed-in user in the group the model's restriction refuses."""
        group, _ = Group.objects.get_or_create(name=NO_CALCULATE_GROUP)
        self.user.groups.add(group)

    def test_10_82_a_caller_who_may_not_run_is_told_before_pressing(self):
        """
        Scenario 10.82: read permission does not imply permission to run.
        Given: a record this caller may read but whose modification the model's
               restriction refuses them
        When: the widget polls its status
        Then: the poll succeeds — proving the record IS readable — and reports
              can_calculate false with the restriction's own reason, and the
              PATCH that the button would issue really is refused. Without the
              flag the widget can only render an enabled button, take the click,
              and relay a 403 the backend already knew about before the page was
              drawn.
        """
        self._block_this_caller()
        item = ApiLayerCalcCalculateRestricted.objects.create(name="hands-off")

        resp = self.client.get(self.url_status(API_LAYER_CALC_RUN_RESTRICTED, item.pk))

        self.assertEqual(
            resp.status_code, 200,
            msg=(
                "This caller may read the record, so the status poll must "
                f"succeed — got {resp.status_code} with {resp.content!r}. A 404 "
                "here would mean the scenario is testing read permission "
                "(10.76) rather than run permission."
            ),
        )
        body = resp.json()
        self.assertIs(
            body["can_calculate"], False,
            msg=(
                "The caller may not run this record, so the envelope must say "
                f"so. Got can_calculate={body['can_calculate']!r} — the widget "
                "would render an enabled button that only fails on click."
            ),
        )
        self.assertIn(
            NO_CALCULATE_VIOLATION, body["calculate_denied_reason"] or "",
            msg=(
                "A disabled button with no reason reads as a broken dashboard. "
                "The envelope must carry the restriction's own explanation, "
                f"{NO_CALCULATE_VIOLATION!r}; got "
                f"{body['calculate_denied_reason']!r}."
            ),
        )

        # The load-bearing half: the flag is a claim about One.update, so pin it
        # against One.update rather than against our reading of it.
        triggered = self.client.patch(
            self.url_detail(API_LAYER_CALC_RUN_RESTRICTED, item.pk),
            data={"calculate": "true"},
            format="json",
        )
        self.assertEqual(
            triggered.status_code, 403,
            msg=(
                "can_calculate=false has to mean the trigger is actually "
                f"refused. The PATCH returned {triggered.status_code} instead — "
                "the endpoint and the enforcement path have drifted apart, and "
                "the widget is disabling a button that works."
            ),
        )

    def test_10_83_a_caller_who_may_run_gets_an_enabled_button(self):
        """
        Scenario 10.83: the flag is not simply always false.
        Given: the same record, polled by a caller the restriction allows
        When: the widget polls its status
        Then: can_calculate is true with no reason attached, and the PATCH the
              button would issue really is accepted. This is the direction that
              must never be wrong: a false negative disables the button for
              somebody who may run the record, and no click can recover from
              that — there is nothing left to press.
        """
        item = ApiLayerCalcCalculateRestricted.objects.create(name="go-ahead")

        resp = self.client.get(self.url_status(API_LAYER_CALC_RUN_RESTRICTED, item.pk))

        self.assertEqual(
            resp.status_code, 200,
            msg=f"Polling a readable record must succeed. Got {resp.content!r}.",
        )
        body = resp.json()
        self.assertIs(
            body["can_calculate"], True,
            msg=(
                "This caller may run the record, so the button must be left "
                f"enabled. Got can_calculate={body['can_calculate']!r} — a "
                "disabled button here is an unrecoverable dead end."
            ),
        )
        self.assertIsNone(
            body["calculate_denied_reason"],
            msg=(
                "Nothing was denied, so there is no reason to render beside the "
                f"button. Got {body['calculate_denied_reason']!r}."
            ),
        )

        triggered = self.client.patch(
            self.url_detail(API_LAYER_CALC_RUN_RESTRICTED, item.pk),
            data={"calculate": "true"},
            format="json",
        )
        self.assertEqual(
            triggered.status_code, 202,
            msg=(
                "can_calculate=true promises the trigger goes through. The PATCH "
                f"returned {triggered.status_code} with {triggered.content!r} — "
                "the widget is offering a button that cannot work."
            ),
        )
