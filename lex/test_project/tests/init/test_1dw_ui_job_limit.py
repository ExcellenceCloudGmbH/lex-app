"""lex.ui: a limit on how many runs of a job go at once -- ``@ui.job(limit=)``.

Intent: an expensive job (reading another system) must not run more than N times at once in the
server, for all users together. A press beyond the limit is refused at once with a sentence the
user understands ("Export is already running 2 times. Try again in a moment."), not queued
behind the others where it would wait unseen. A regression is a third run starting beside two,
a refused press that changes the page or holds a place, or a place that is never given back
(after a failure, a cancel, a refusal or a form's wrong field), which would lock the job for
everyone.

Cluster 1dw -- scenarios 1.927-1.929. Type: U.
Covers: lex/lex_app/reflex/ui/handlers.py (job, _limit_message), lex/lex_app/reflex/ui/runs.py
(take_place, give_place).
Run: python -m lex pytest lex/test_project/tests/init/test_1dw_ui_job_limit.py -v
"""

from __future__ import annotations

import asyncio
import threading
import time

import pytest
from django.test import SimpleTestCase

from lex import ui
from lex.lex_app.reflex.ui import results, runs, testing
from lex.lex_app.reflex.ui.pages.parts import Build

pytestmark = pytest.mark.init

GATE = {"open": threading.Event()}
RAN: list = []
#: What stopped the crunch job's worker thread.
STOPPED: list = []


class ExportState(ui.State):
    """Exports that read another system."""

    _rows: list[dict] = []

    @ui.job(limit=2)
    def export(self, region: str):
        if not GATE["open"].wait(5):
            raise RuntimeError("the gate was never opened")
        RAN.append(region)
        if region == "broken":
            raise RuntimeError("the other system is away")
        if region == "refused":
            raise ui.Refuse("Nothing to export there.")

    @ui.job(limit=1)
    def build_report(self, values):
        if not GATE["open"].wait(5):
            raise RuntimeError("the gate was never opened")
        RAN.append(values["year"])
        self._rows = [{"id": 1, "year": values["year"]}]

    @ui.job(limit=1)
    def stoppable(self):
        for _ in range(100):
            yield
            time.sleep(0.02)

    @ui.job(limit=1)
    def crunch(self):
        """Reports through a library's callback on its own thread (``self.progress``), never at a yield."""
        failed: list = []

        def work() -> None:
            try:
                for step in range(250):
                    self.progress(step / 250, f"Step {step}")
                    time.sleep(0.02)
            except BaseException as exc:  # noqa: BLE001 - handed to the caller, as a library would
                STOPPED.append(type(exc))
                failed.append(exc)
        worker = threading.Thread(target=work, name="crunch-worker")
        worker.start()
        worker.join()
        if failed:
            raise failed[0]


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
            await harness.open_tab()
            return await steps(harness)

    return asyncio.run(scenario())


class TestCluster01dw_JobLimit(SimpleTestCase):
    """1.927-1.929: two run, the third is refused at once; places given back; the declaration."""

    def setUp(self) -> None:
        GATE["open"] = threading.Event()
        RAN.clear()
        ui.form(title="Report", submit=ExportState.build_report,
                fields=[ui.field("year", kind="year", required=True)]).build(Build(page="report", route="/report"))

    # -- 1.927 ---------------------------------------------------------
    def test_1_927_beyond_the_limit_a_press_is_refused_at_once(self) -> None:
        """
        Scenario 1.927: `@ui.job(limit=2)`.
        Given: an export held at a gate.
        When:  it is pressed for three regions while the first two run; then once more after
               they finished.
        Then:  two run at once; the third is refused at once -- before the others finish --
               with "Export is already running 2 times. Try again in a moment.", as a toast
               and in ui.failed, and its body never runs; once a run ends its place is free.
        """
        key = runs.key_of(ExportState.export)

        async def steps(harness):
            first = await harness.send(ExportState, "export", region="north")
            second = await harness.send(ExportState, "export", region="south")

            async def both():
                return len((await harness.fields(ExportState))["ui_running"].get(key, [])) == 2

            await _until(both, "the first two runs did not start")
            third = await harness.send(ExportState, "export", region="east")
            await asyncio.wait_for(third, 2)                 # refused at once: it does not wait for the others
            refused = await harness.fields(ExportState)
            told = [toast.message for toast in harness.results if isinstance(toast, results.Toast)]
            GATE["open"].set()
            await first
            await second
            again = await harness.send(ExportState, "export", region="east")
            await again
            await asyncio.sleep(0.05)
            return refused, told, await harness.fields(ExportState)

        refused, told, after = _session(steps)
        sentence = "Export is already running 2 times. Try again in a moment."
        self.assertEqual(told, [sentence], "the user is told why, in a sentence")
        self.assertEqual(refused["ui_failed"], {"ExportState.export": sentence}, "and it stays in place")
        self.assertEqual(sorted(refused["ui_running"][key]), ["north", "south"], "the third never started")
        self.assertEqual(sorted(RAN[:2]), ["north", "south"])
        self.assertEqual(RAN[2:], ["east"], "after the others ended, it runs")
        self.assertEqual((after["ui_running"], after["ui_failed"]), ({}, {}), "its new run cleared the message")
        self.assertEqual(runs._PLACES, {}, "every place is given back")

    # -- 1.928 ---------------------------------------------------------
    def test_1_928_a_place_is_given_back_however_the_run_ends(self) -> None:
        """
        Scenario 1.928: no place is lost.
        Given: `limit=1` and `limit=2` jobs.
        When:  a run fails, one is refused by its own body, one is cancelled, one reporting
               through `self.progress` from a library's thread is stopped, a form's job is sent
               with a wrong field, and a form's job is pressed while it runs.
        Then:  after each the places are free again; the run reporting through `self.progress`
               holds its place while it reports, its thread gets the stop at its next report,
               and the next run starts; a refused press of a form's job leaves the page as it
               was -- the last result still shows and no field has a message -- and a limit of
               one says "Build report is already running. Try again in a moment." in a toast,
               not above the submit button as a form's refusal is: it is about the press.
        """
        GATE["open"].set()
        for region in ("broken", "refused"):
            result = ui.testing.run(ExportState, "export", region=region)
            self.assertTrue(result.error or result.refused, f"{region}: the run ended badly")
            self.assertEqual(runs._PLACES, {}, f"{region}: its place is given back")

        async def cancelled(harness):
            running = await harness.send(ExportState, "stoppable")
            await _until(lambda: runs._PLACES.get("ExportState.stoppable") == 1, "the job never took its place")
            await (await harness.send(ui.State, "ui_cancel", key="ExportState.stoppable"))
            await running
            return dict(runs._PLACES)

        self.assertEqual(_session(cancelled), {}, "a cancelled run gives its place back")

        async def crunched(harness):
            key = runs.key_of(ExportState.crunch)

            async def reporting():
                return (await harness.fields(ExportState))["ui_progress"].get(key, 0) > 0

            held = []
            for _ in range(2):                                 # the second run starts: the place came back
                running = await harness.send(ExportState, "crunch")
                await _until(reporting, "the library never reported through self.progress")
                held.append(dict(runs._PLACES))
                await (await harness.send(ui.State, "ui_cancel", key=key))
                await running
                await _until(lambda: not runs._PLACES, "the stopped run kept its place")
            return held

        STOPPED.clear()
        self.assertEqual(_session(crunched), [{"ExportState.crunch": 1}] * 2,
                         "a run reporting through self.progress holds its place while it reports")
        self.assertEqual((STOPPED, runs._PLACES), ([GeneratorExit] * 2, {}),
                         "its thread got the stop at its next report, and the place was given back")

        wrong = ui.testing.run(ExportState, "build_report", values={"year": "abc"})
        self.assertEqual((wrong.invalid, runs._PLACES), ({"year": "Enter a year with four digits, such as 2026."}, {}),
                         "a form's wrong field: the body never ran, the place is free")

        GATE["open"] = threading.Event()
        key = runs.key_of(ExportState.build_report)

        async def pressed_twice(harness):
            await harness.set_fields(ui.State, ui_done={key: True})          # an earlier result shows
            first = await harness.send(ExportState, "build_report", values={"year": "2025"})
            await _until(lambda: runs._PLACES.get(key) == 1, "the first run never took its place")
            harness.outcomes.clear()
            harness.results.clear()
            await (await harness.send(ExportState, "build_report", values={"year": "x"}))
            during = await harness.fields(ExportState)
            told = [outcome.message for outcome in harness.outcomes]
            toasted = [toast.message for toast in harness.results if isinstance(toast, results.Toast)]
            GATE["open"].set()
            await first
            return during, told, toasted, await harness.fields(ExportState)

        during, told, toasted, after = _session(pressed_twice)
        sentence = "Build report is already running. Try again in a moment."
        self.assertEqual(told, [sentence], "refused before its values are judged, named as its button")
        self.assertEqual((during["ui_invalid"], during["ui_done"]), ({}, {key: False}),
                         "no field message; the running run's own start took the old result away, not the refusal")
        self.assertEqual((toasted, during["ui_refused"]), ([sentence], {}),
                         "a toast, not above the submit button: it is about the press, not what was entered")
        self.assertEqual((after["ui_done"], after["ui_refused"]), ({key: True}, {}),
                         "once the running run ends its result shows, under no stale refusal")
        self.assertEqual(runs._PLACES, {})

    # -- 1.929 ---------------------------------------------------------
    def test_1_929_the_limit_is_declared_as_a_whole_number(self) -> None:
        """
        Scenario 1.929: the declaration.
        Given: `@ui.job(limit=...)`.
        When:  the limit is 0, True, 2.5 or "2"; and a state copies a mixin's limited job.
        Then:  each wrong limit is refused when the state is written, saying what to write; the
               handler carries its limit, also when a mixin's job is copied into a state.
        """
        for wrong in (0, True, 2.5, "2"):
            with self.subTest(limit=wrong), self.assertRaisesRegex(
                    ValueError, r"@ui.job\(limit=\) is how many runs of the job may go at once: a whole number, "
                                r"1 or more"):
                ui.job(limit=wrong)
        self.assertEqual(ExportState.export.fn.__ui_options__["limit"], 2)
        self.assertEqual(ExportState.build_report.fn.__ui_again__.keywords["limit"], 1,
                         "a mixin's copy is decorated again with the same limit")
