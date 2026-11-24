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

Cluster 1dx -- scenarios 1.930-1.936. Type: U.
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
from lex.lex_app.reflex.ui import rows as rows_mod
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
        if values["customer"] == "nobody":
            raise ui.Refuse("There are no invoices for that customer.", title="Nothing to report")
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
        if values["customer"] == "nobody":
            raise ui.Refuse("Nobody is called that.")
        self.customer = values["customer"]


class TallyState(ui.State):
    """A kept tool whose result is two tabs, one reading a private field, chosen by a searched customer."""

    _north: list[dict] = ui.private(keep=True, idle_minutes=30)

    @ui.rows
    def customers(self, search):
        return [row for row in ({"id": 7, "label": "Acme"}, {"id": 8, "label": "Beta"})
                if search.casefold() in row["label"].casefold()]

    @ui.job
    def tally(self, values):
        self._north = [{"id": 1, "customer": values["customer"]}]

    @ui.rows
    def north(self):
        return self._north

    @ui.rows
    def south(self):
        return [{"id": 2, "name": "Order two"}]


def report():
    return ui.form(title="Invoice report", load=ReportState.open, keep=True, submit=ReportState.build,
                   fields=[ui.field("customer", required=True, value=ReportState.customer)],
                   result=[ui.table(ReportState.lines, columns=["customer", ui.column("amount", kind="money")])])


def lookup():
    return ui.form(title="Look up", submit=ReportState.search, fields=[ui.field("customer")],
                   result=[ui.text("Found.")])


def tally(*, keep=True):
    return ui.form(title="Tally", submit=TallyState.tally, keep=keep, tabs=True,
                   fields=[ui.field("customer", options=TallyState.customers, required=True)],
                   result=[ui.section("North", ui.table(TallyState.north, columns=["customer"])),
                           ui.section("South", ui.table(TallyState.south, columns=["name"]))])


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
    """1.930-1.935: the result after a visit away; a private result; the page; with a load; its refusal;
    with tabs and a searched choice."""

    def setUp(self) -> None:
        GATE["open"] = threading.Event()
        private.clear()
        self.addCleanup(private.clear)
        for page in (report, lookup, tally):
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

    # -- 1.934 ---------------------------------------------------------
    def test_1_934_a_kept_form_keeps_its_refusal_above_its_submit_button(self) -> None:
        """
        Scenario 1.934: a refusal (`ui.Refuse(title=)`) on a kept form.
        Given: the kept form, whose job refuses a customer with a title, and the form that is
               not kept, whose action refuses too.
        When:  both refuse; the user opens the form that is not kept again and comes back to the
               kept one; then sends the kept form again with a customer it accepts.
        Then:  the refusal shows in one place, above the submit button with its title: kept
               across the visit for the kept form, dropped for the form that is not kept (as
               before); the line under the kept form does not repeat it (it shows only when no
               refusal does); sending again clears both and the result shows.
        """
        key = runs.key_of(ReportState.build)
        refusal = {"title": "Nothing to report", "message": "There are no invoices for that customer."}

        async def steps(harness):
            GATE["open"].set()
            await (await harness.send(ReportState, "build", values={"customer": "nobody"}))
            await (await harness.send(ReportState, "search", values={"customer": "nobody"}))
            refused = await harness.fields(ReportState)
            await _open(harness, "/lookup")
            back = await _open(harness, "/report")
            await (await harness.send(ReportState, "build", values={"customer": "Acme"}))
            return refused, back, await harness.fields(ReportState)

        refused, back, again = _session(steps)
        self.assertEqual(refused["ui_refused"], {key: refusal, "ReportState.search": {
            "title": "", "message": "Nobody is called that."}}, "each form shows its refusal above its button")
        self.assertEqual(back["ui_refused"], {key: refusal}, "a new visit keeps the kept form's refusal only")
        self.assertEqual(back["ui_failed"][key], refusal["message"],
                         "ui.failed holds the same sentence: the line under the form must not repeat it")
        self.assertEqual((again["ui_refused"], again["ui_failed"].get(key), again["ui_done"]), ({}, None, {key: True}),
                         "sending again clears both, and the result shows")

        under = 'ui_refused_rx_state_?.["ReportState.build"]'
        kept_text = str(report().build(Build(page="report", route="/report")))
        plain = ui.form(title="Invoice report", load=ReportState.open, submit=ReportState.build,
                        fields=[ui.field("customer", required=True, value=ReportState.customer)],
                        result=[ui.table(ReportState.lines, columns=["customer"])])
        plain_text = str(plain.build(Build(page="report", route="/report")))
        self.assertGreater(kept_text.count(under), plain_text.count(under),
                           "the line under a kept form asks whether a refusal already shows")

    # -- 1.935 ---------------------------------------------------------
    def test_1_935_a_kept_form_with_tabs_and_a_searched_choice(self) -> None:
        """
        Scenario 1.935: `ui.form(keep=True, tabs=True)`, a private result read in a tab, and a
        customer chosen by a search on the server.
        Given: a kept tool whose result is two tabs, the first reading `ui.private(keep=True,
               idle_minutes=30)`, its customer a searched choice.
        When:  it is built and compiled; the user searches, chooses, sends, leaves and comes
               back within the idle time; sends the customer chosen before the visit again;
               comes back past the idle time.
        Then:  the page compiles with its tabs, its failure line and the search; back within
               the idle time the result shows and the tab's table reads what the job kept; the
               customer offered before the visit is still accepted (what a tab was offered is
               not part of the visit); past the idle time no result shows, the form says why,
               and the tab's table says the value is gone.
        """
        key = runs.key_of(TallyState.tally)
        page = tally().build(Build(page="tally", route="/tally"))
        text = str(page)
        self.assertIn('ui_failed_rx_state_?.["TallyState.tally"]', text, "the kept form's failure line")
        self.assertIn("rfds-tabs", text, "its result as tabs")
        _, module = compile_page("/tally", page)
        self.assertIn("ui_search", module, "the searched choice is compiled")
        field = next(one.search for one in rules.of(TallyState.tally.fn.__ui_fn__).fields if one.search)
        now = [1000.0]

        async def steps(harness):
            with patch.object(private, "_now", lambda: now[0]):
                await (await harness.send(State, "ui_search", field=field, text="ac"))
                offered = list(dict((await harness._state(State)).ui_matches).get(field, []))
                await (await harness.send(TallyState, "tally", values={"customer": "7"}))
                await _open(harness, "/elsewhere")
                now[0] += 10 * 60
                back = await _open(harness, "/tally")
                north = await rows_mod.read(TallyState.north, await harness._state(TallyState))
                await (await harness.send(TallyState, "tally", values={"customer": "7"}))
                resent = await harness.fields(TallyState)
                now[0] += 31 * 60
                gone = await _open(harness, "/tally")
                try:
                    await rows_mod.read(TallyState.north, await harness._state(TallyState))
                    told = ""
                except ui.Refuse as refused:
                    told = rows_mod.describe(refused, "TallyState.north", "")
            return offered, back, north, resent, gone, told

        offered, back, north, resent, gone, told = _session(steps)
        self.assertEqual(offered, [{"value": "7", "label": "Acme"}])
        self.assertEqual(back["ui_done"], {key: True}, "back within the idle time: the tabs show")
        self.assertEqual(north, [{"id": 1, "customer": 7}], "the tab's table reads what the job kept privately")
        self.assertEqual((resent["ui_invalid"], resent["ui_done"]), ({}, {key: True}),
                         "the customer offered before the visit is still accepted")
        self.assertEqual((gone["ui_done"], gone["ui_failed"]), ({}, {key: GONE_FORM}),
                         "past the idle time: no tabs over nothing, and the sentence")
        self.assertEqual(told, "This is no longer available: it is kept only for a while. Start again.",
                         "the tab's table says so too")

    # -- 1.936 ---------------------------------------------------------
    def test_1_936_a_reconnect_is_not_a_new_visit(self) -> None:
        """
        Scenario 1.936: the socket drops and comes back on the same page.
        Given: a page opened on one connection; a job's private result that is not kept, a
               form's result that is not kept, a field the user's run set.
        When:  the connection drops and Reflex reconnects: it sends `hydrate` and the page's
               `on_load` again, on a new connection, for the same address (as `state.js` does
               on every reconnect); then the user follows a link to the same page on that
               connection; then the tab reconnects on another address.
        Then:  the reconnect is the same visit: the visit count, the private value and both
               forms' results stay, and the page's loads run again without resetting what the
               visit holds; following the link is a new visit, as is a reconnect elsewhere.
        """
        build, search = runs.key_of(ReportState.build), runs.key_of(ReportState.search)

        def at(url: str, sid: str) -> dict:
            return {"pathname": url, "asPath": url, "query": {}, "sid": sid}

        async def visit(harness, url: str, sid: str, *, reconnect: bool) -> dict:
            import reflex as rx

            if reconnect:
                await (await harness.send(rx.State, "hydrate", _router_data=at(url, sid)))
            await (await harness.send(State, "ui_open", page=url, _router_data=at(url, sid)))
            await asyncio.sleep(0.05)
            found = await harness.fields(ReportState)
            found["_summary"] = private.peek(await harness._state(ReportState), "_summary")
            return found

        from lex.lex_app.reflex.ui import handlers

        handlers.register_page("/report", [ReportState.open], [], "")       # the page's load, as ui.page registers it
        self.addCleanup(handlers._PAGES.pop, "/report", None)

        async def steps(harness):
            GATE["open"].set()
            first = await visit(harness, "/report", "socket-1", reconnect=True)
            await (await harness.send(ReportState, "build", values={"customer": "summary"}))
            await (await harness.send(ReportState, "search", values={"customer": "Acme"}))
            await harness.set_fields(ReportState, regions=["west"])        # what the load sets again
            back = await visit(harness, "/report", "socket-2", reconnect=True)
            linked = await visit(harness, "/report", "socket-2", reconnect=False)
            await (await harness.send(ReportState, "build", values={"customer": "summary"}))
            elsewhere = await visit(harness, "/lookup", "socket-3", reconnect=True)
            return first, back, linked, elsewhere

        first, back, linked, elsewhere = _session(steps)
        self.assertEqual(back["ui_visit"], first["ui_visit"], "a reconnect is the same visit")
        self.assertEqual(back["_summary"], {"total": "12.50"}, "the private result was not dropped")
        self.assertEqual(back["ui_done"], {build: True, search: True}, "neither form started over")
        self.assertEqual((back["customer"], back["regions"]), ("Acme", ["north", "south"]),
                         "the load ran again, without resetting the visit's fields")
        self.assertEqual(linked["ui_visit"], first["ui_visit"] + 1, "a link to the same page is a new visit")
        self.assertIn(linked["_summary"], ({}, private.GONE), "which drops what is not kept")
        self.assertEqual(linked["ui_done"], {build: True}, "and starts the form that is not kept over")
        self.assertEqual(elsewhere["ui_visit"], linked["ui_visit"] + 1, "a reconnect on another address is a new visit")
        self.assertIn(elsewhere["_summary"], ({}, private.GONE))
