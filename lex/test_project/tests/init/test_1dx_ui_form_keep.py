"""lex.ui forms: a result kept across visits -- ``ui.form(keep=True)``.

Intent: a user starts a long job from a form, leaves the page, comes back, and finds what the job
found -- not an empty form. A new visit starts a form over (its result leaves), which is right for
an entry form; a tool whose result took minutes declares ``keep=True``. The state's fields already
survive a visit (a load's ``keep=`` exempts them from its reset); what did not was the form's
result flag. A kept result shows only while what it shows is kept: a private result
(``ui.private(keep=True)``) past its idle time is said to be gone, under the form, never shown
empty or old. What was typed comes back through ``value=`` on a field the handler set. A
regression is a kept form that comes back empty, a result shown over data that is gone, or a
failure that happened while the user was away and was never shown.

Cluster 1dx -- scenarios 1.930-1.933. Type: U.
Covers: lex/lex_app/reflex/ui/pages/forms.py (form keep=, the failure under a kept form),
lex/lex_app/reflex/ui/forms.py (keep, kept, gone_message), lex/lex_app/reflex/ui/state.py (ui_open).
Run: python -m lex pytest lex/test_project/tests/init/test_1dx_ui_form_keep.py -v
"""

from __future__ import annotations

import asyncio
import threading
import time
from unittest.mock import patch

import pytest
from django.test import SimpleTestCase
from reflex.compiler.compiler import compile_page

from lex import ui
from lex.lex_app.reflex.ui import forms as rules
from lex.lex_app.reflex.ui import private, runs, testing
from lex.lex_app.reflex.ui.pages.parts import Build
from lex.lex_app.reflex.ui.state import State

pytestmark = pytest.mark.init

GATE = {"open": threading.Event()}
GONE_FORM = "The last result is no longer available: results are kept only for a while. Send the form again."


class ReportState(ui.State):
    """A tool whose job takes a while; its result is kept across visits."""

    customer: str = ""
    regions: list[str] = ["north", "south"]
    _lines: list[dict] = ui.private(keep=True, idle_minutes=30)
    _summary: dict = ui.private()

    @ui.load(keep=("customer",))
    def open(self):
        self.regions = ["north", "south"]

    @ui.job
    def build(self, values):
        self.customer = values["customer"]
        if not GATE["open"].wait(5):
            raise RuntimeError("the gate was never opened")
        yield ui.progress(0.5, "Reading invoices")
        self._lines = [{"id": 1, "customer": values["customer"], "amount": "12.50"}]
        if values["customer"] == "summary":
            self._summary = {"total": "12.50"}
        if values["customer"] == "broken":
            raise RuntimeError("the invoice system is away")

    @ui.rows
    def lines(self):
        return self._lines

    @ui.action
    def search(self, values):
        self.customer = values["customer"]


def report():
    return ui.form(title="Invoice report", load=ReportState.open, keep=True, submit=ReportState.build,
                   fields=[ui.field("customer", required=True, value=ReportState.customer)],
                   result=[ui.table(ReportState.lines, columns=["customer", ui.column("amount", kind="money")])])


def lookup():
    return ui.form(title="Look up", submit=ReportState.search, fields=[ui.field("customer")],
                   result=[ui.text("Found.")])


async def _until(condition, failure: str) -> None:
    deadline = time.monotonic() + 5
    while True:
        found = condition()
        if asyncio.iscoroutine(found):
            found = await found
        if found:
            return
        if time.monotonic() > deadline:
            raise AssertionError(failure)
        await asyncio.sleep(0.001)


def _session(steps):
    async def scenario():
        async with testing.Harness() as harness:
            await harness.open_tab("/report")
            return await steps(harness)

    return asyncio.run(scenario())


async def _open(harness, page: str) -> dict:
    await (await harness.send(State, "ui_open", page=page))
    await asyncio.sleep(0.05)
    return await harness.fields(ReportState)


class TestCluster01dx_FormKeep(SimpleTestCase):
    """1.930-1.933: the result after a visit away; a private result; the page; with a load."""

    def setUp(self) -> None:
        GATE["open"] = threading.Event()
        private.clear()
        self.addCleanup(private.clear)
        for page in (report, lookup):
            page().build(Build(page=page.__name__, route=f"/{page.__name__}"))

    # -- 1.930 ---------------------------------------------------------
    def test_1_930_a_kept_form_shows_what_its_job_found_after_the_user_was_away(self) -> None:
        """
        Scenario 1.930: leaving while the job runs, and coming back.
        Given: a kept form whose job is held at a gate, and a form that is not kept.
        When:  the job is started; the user opens another page; comes back while it still runs;
               leaves again; the job finishes; the user comes back.
        Then:  back while it runs, the form shows it running with its progress; back after it
               finished, the result flag is still set and the result's rows are there; the
               customer typed is the field's start value again; the form that is not kept
               starts over on a new visit, as before.
        """
        key = runs.key_of(ReportState.build)

        async def steps(harness):
            running = await harness.send(ReportState, "build", values={"customer": "Acme"})

            async def started():
                return key in (await harness.fields(ReportState))["ui_running"]

            await _until(started, "the job never started")
            await _open(harness, "/elsewhere")
            back_running = await _open(harness, "/report")
            await _open(harness, "/elsewhere")
            GATE["open"].set()
            await running
            back = await _open(harness, "/report")
            rows = await testing_rows(harness)
            await (await harness.send(ReportState, "search", values={"customer": "x"}))
            not_kept = await _open(harness, "/lookup")
            return back_running, back, rows, not_kept

        async def testing_rows(harness):
            from lex.lex_app.reflex.ui import rows as rows_mod

            return await rows_mod.read(ReportState.lines, await harness._state(ReportState))

        back_running, back, rows, not_kept = _session(steps)
        self.assertEqual(back_running["ui_running"], {key: [""]}, "back while it runs: it shows running")
        self.assertEqual(back["ui_done"], {key: True}, "back after it finished: the result shows")
        self.assertEqual(rows, [{"id": 1, "customer": "Acme", "amount": "12.50"}], "with what the job found")
        self.assertEqual(back["customer"], "Acme", "and the field starts from what was typed")
        self.assertNotIn("ReportState.search", not_kept["ui_done"], "a form that is not kept starts over")

    # -- 1.931 ---------------------------------------------------------
    def test_1_931_a_kept_result_whose_data_is_gone_says_so(self) -> None:
        """
        Scenario 1.931: a private result and its limits.
        Given: the kept form's result in `ui.private(keep=True, idle_minutes=30)`, and a
               second private field that is not kept.
        When:  the user comes back within 30 minutes; after 31; after a run that also filled the
               field that is not kept; and after a run that failed while the user was away.
        Then:  within the idle time the result shows; past it, the result does not show over
               nothing: the form says, under it, "The last result is no longer available ...
               Send the form again."; the field that is not kept left with the visit but does
               not decide (what reads it says it is gone there); a failure while away is kept
               for the form to show; sending the form again clears it.
        """
        key = runs.key_of(ReportState.build)
        now = [1000.0]

        async def steps(harness):
            GATE["open"].set()
            with patch.object(private, "_now", lambda: now[0]):
                await (await harness.send(ReportState, "build", values={"customer": "Acme"}))
                now[0] += 29 * 60
                within = await _open(harness, "/report")
                now[0] += 31 * 60
                after = await _open(harness, "/report")
                await (await harness.send(ReportState, "build", values={"customer": "summary"}))
                unkept = await _open(harness, "/report")
                summary = private.peek(await harness._state(ReportState), "_summary")
                await (await harness.send(ReportState, "build", values={"customer": "broken"}))
                failed = await _open(harness, "/report")
                await (await harness.send(ReportState, "build", values={"customer": "Acme"}))
                again = await harness.fields(ReportState)
            return within, after, unkept, summary, failed, again

        within, after, unkept, summary, failed, again = _session(steps)
        self.assertEqual((within["ui_done"], within["ui_failed"]), ({key: True}, {}), "within its idle time it shows")
        self.assertEqual((after["ui_done"], after["ui_failed"]), ({}, {key: GONE_FORM}),
                         "past it: no result over nothing, and a sentence that says why")
        self.assertEqual((unkept["ui_done"], unkept["ui_failed"]), ({key: True}, {}),
                         "the kept result shows: a field that is not kept does not decide")
        self.assertIs(summary, private.GONE, "but it left with the visit: what reads it is told so")
        self.assertIn("Reference:", failed["ui_failed"][key], "a failure while away is there to be shown")
        self.assertEqual((again["ui_done"], again["ui_failed"]), ({key: True}, {}), "sending again clears it")

    # -- 1.932 ---------------------------------------------------------
    def test_1_932_the_kept_form_page_compiles_and_shows_its_failure_in_place(self) -> None:
        """
        Scenario 1.932: the page.
        Given: the kept form and the form that is not kept.
        When:  each is built and compiled with Reflex's page compiler.
        Then:  the kept form shows its handler's last failure under it, in RFDS's inline
               message, and the field starts from the state's field; the form that is not kept
               does not; `keep=` takes True or False only; the kept form is registered under
               its handler with its state.
        """
        kept_text = str(report().build(Build(page="report", route="/report")))
        plain_text = str(lookup().build(Build(page="lookup", route="/lookup")))
        failed_var = 'ui_failed_rx_state_?.["ReportState.build"]'
        self.assertIn(failed_var, kept_text, "a kept form shows its last failure")
        self.assertIn('className:"rfds-inline-message"', kept_text, "in RFDS's inline message")
        self.assertNotIn('ui_failed_rx_state_?.["ReportState.search"]', plain_text, "a form not kept does not")
        self.assertIn("report_state.customer_rx_state_", kept_text, "the field starts from what was typed")
        _, module = compile_page("/report", report().build(Build(page="report", route="/report")))
        self.assertIn(failed_var, module, "compiled")
        self.assertEqual(rules.kept().get("ReportState.build"), ReportState)
        self.assertNotIn("ReportState.search", rules.kept())
        with self.assertRaisesRegex(TypeError, r"ui.form\(keep=\) is True or False; got 'yes'"):
            ui.form(title="Report", submit=ReportState.build, fields=[ui.field("customer")], keep="yes")

    # -- 1.933 ---------------------------------------------------------
    def test_1_933_a_kept_form_with_a_load_keeps_what_the_load_keeps(self) -> None:
        """
        Scenario 1.933: the page's load resets its state on a new visit.
        Given: the kept form, whose load keeps `customer` (`@ui.load(keep=("customer",))`) and
               whose result is a private field declared `keep=True`.
        When:  the job ran; the user comes back and the page's load runs.
        Then:  the load leaves both alone: the result and what was typed are there; it resets
               what it does not keep, as every load does.
        """
        key = runs.key_of(ReportState.build)

        async def steps(harness):
            GATE["open"].set()
            await (await harness.send(ReportState, "build", values={"customer": "Acme"}))
            await harness.set_fields(ReportState, regions=["east"])
            await _open(harness, "/report")
            await (await harness.send(ReportState, "open"))
            await asyncio.sleep(0.05)
            return await harness.fields(ReportState)

        fields = _session(steps)
        self.assertEqual(fields["ui_done"], {key: True})
        self.assertEqual((fields["customer"], fields["_lines"][0]["customer"]), ("Acme", "Acme"),
                         "what was typed and the result survived the load")
        self.assertEqual(fields["regions"], ["north", "south"], "what the load does not keep is reset")
