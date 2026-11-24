"""lex.ui: a job reports progress from a callback -- ``self.progress(fraction, message)``.

Intent: many libraries report progress through a callback, often called on a thread of their own
(``reports.build(year, on_progress=fn)``). A job hands them ``self.progress`` and the page shows
the progress exactly as ``yield ui.progress(...)`` would, with no helper thread to start and poll.
Stop still works: once the user stopped the job (or left a page with ``stop_on_leave=True``), the
next ``self.progress(...)`` raises the stop the job's next ``yield`` would raise. The callback's
thread only reports progress: it never writes the job's other fields. Anywhere but a running job
-- an action, a load, after the job ended -- ``self.progress`` says that it belongs in a
``@ui.job``. ``progress`` is then a name a ``ui.State`` reserves, like ``user`` and ``api``. A
regression is a progress bar frozen while a library works, a Stop button the library never hears,
half-finished fields written from a foreign thread, or a field called ``progress`` that silently
hides the helper.

Cluster 1dm -- scenarios 1.856-1.859, 1.914. Type: U.
Covers: lex/lex_app/reflex/ui/handlers.py (job, _drive, reporter), lex/lex_app/reflex/ui/state.py
(State.progress), lex/lex_app/reflex/ui/context.py (RESERVED), lex/lex_app/reflex/ui/checks.py
(reserved names).
Run: python -m lex pytest lex/test_project/tests/init/test_1dm_ui_job_progress_callback.py -v
"""

from __future__ import annotations

import asyncio
import tempfile
import threading
import time
from pathlib import Path

import pytest
import reflex as rx
from django.test import SimpleTestCase

from lex import ui
from lex.lex_app.reflex.ui import checks, context, runs, testing
from lex.lex_app.reflex.ui.state import State, check_route

pytestmark = pytest.mark.init

#: What the library's worker thread saw, in order: its name, each step it finished, what stopped it.
SEEN: list = []
#: The body's cleanup ran.
CLEANED: list = []
#: ``self.progress`` kept by a job, to call once the job has ended.
KEPT: list = []


class reports:  # noqa: N801 - stands for a library a project imports: `reports.build(...)`
    """A library that works on a thread of its own and reports through a callback."""

    @staticmethod
    def build(year: int, on_progress) -> str:
        def work() -> None:
            SEEN.append(threading.current_thread().name)
            for part in (1, 2, 3):
                on_progress(part / 3, f"Part {part} of 3")
        worker = threading.Thread(target=work, name="reports-worker")
        worker.start()
        worker.join()
        return f"report {year}"

    @staticmethod
    def crunch(on_progress) -> None:
        """Long work; like ``concurrent.futures``, it raises in the caller what failed in its thread."""
        failed: list = []

        def work() -> None:
            try:
                for step in range(300):
                    on_progress(step / 300, f"Step {step}")
                    SEEN.append(step)
                    time.sleep(0.01)
            except BaseException as exc:  # noqa: BLE001 - handed to the caller, as a library would
                SEEN.append(type(exc))
                failed.append(exc)
        worker = threading.Thread(target=work, name="crunch-worker")
        worker.start()
        worker.join()
        if failed:
            raise failed[0]


class ReportsState(ui.State):
    """Reports built by a library that reports its progress through a callback."""

    status: str = ""
    said: str = ""
    count: int = 0

    @ui.job
    def build(self, year: int):
        self.status = "building"
        self.status = reports.build(year, on_progress=self.progress)
        return ui.toast("Report ready")

    @ui.job
    def tally(self):
        self.progress(0.25, "Counting")          # from the body's own thread
        yield ui.progress(0.5, "Half way")       # the yield still works beside it
        self.progress(0.75)

    @ui.job
    def crunch(self):
        try:
            self.status = "kept"
            yield                                # merged here
            self.status = "changed after the last yield"
            reports.crunch(on_progress=self.progress)
            self.status = "finished"
        finally:
            CLEANED.append("cleaned")

    @ui.job(stop_on_leave=True)
    def count_up(self):
        try:
            for step in range(300):
                self.progress(step / 300)
                self.count = step
                time.sleep(0.01)
        except BaseException as exc:
            SEEN.append(type(exc))
            raise

    @ui.job
    def flood(self):
        def work() -> None:
            started = time.monotonic()
            for step in range(1, 201):
                self.progress(step / 200, f"Step {step}")
            SEEN.append(time.monotonic() - started)
        worker = threading.Thread(target=work, name="flood-worker")
        worker.start()
        worker.join()

    @ui.job
    def keep(self):
        KEPT.append(self.progress)

    @ui.action
    def save(self):
        try:
            self.progress(0.5, "Saving")
        except RuntimeError as exc:
            self.said = str(exc)

    @ui.load
    def fill(self):
        try:
            self.progress(0.5)
        except RuntimeError as exc:
            self.said = str(exc)


async def _until(condition, failure: str) -> None:
    """Wait until ``condition()`` holds (it may be a coroutine function); fails loudly after 5 s."""
    deadline = time.monotonic() + 5
    while True:
        found = condition()
        if asyncio.iscoroutine(found):
            found = await found
        if found:
            return
        if time.monotonic() > deadline:
            raise AssertionError(failure)
        await asyncio.sleep(0.005)


def _reports(updates: list, handler) -> list:
    """The (fraction, message) pairs the browser was sent for ``handler``, in order."""
    key = runs.key_of(handler)
    return [(u["ui_progress"][key], u.get("ui_progress_message", {}).get(key))
            for u in updates if key in (u.get("ui_progress") or {})]


class TestCluster01dm_JobProgressCallback(SimpleTestCase):
    """1.856-1.859: self.progress -- from a callback, against Stop, elsewhere, and reserved."""

    def setUp(self) -> None:
        SEEN.clear()
        CLEANED.clear()
        KEPT.clear()

    # -- 1.856 ---------------------------------------------------------
    def test_1_856_a_callback_on_the_librarys_thread_shows_the_jobs_progress(self) -> None:
        """
        Scenario 1.856: ``reports.build(year, on_progress=self.progress)``.
        Given: a job that hands self.progress to a library calling it three times from its own
               worker thread, and a job calling self.progress on its own thread beside a
               ``yield ui.progress(...)``.
        When:  each runs through Reflex's event processor.
        Then:  the browser is sent the fractions and messages, in order, as for a yield (the
               latest of reports close together, 1.914); the
               callback ran on the library's thread; a report sends only the progress -- the
               field the body changed before it reaches the browser at the end, not with the
               callback's reports; the job ends as usual, with its result and nothing running.
        """
        built = testing.run(ReportsState, "build", year=2026)
        self.assertEqual(SEEN, ["reports-worker"], "the library called back from its own thread")
        sent = _reports(built.updates, ReportsState.build)
        self.assertEqual(sent[-1], (1.0, "Part 3 of 3"), "the latest report reached the browser, with its message")
        self.assertTrue(set(sent) <= {(1 / 3, "Part 1 of 3"), (2 / 3, "Part 2 of 3"), (1.0, "Part 3 of 3")}
                        and sent == sorted(sent), "in order; reports close together are folded into the latest (1.914)")
        key = runs.key_of(ReportsState.build)
        for update in built.updates:
            if key in (update.get("ui_progress") or {}):
                self.assertNotIn("status", update, "a report from the callback writes no other field")
        self.assertEqual(built.fields["status"], "report 2026", "the body's fields are written at the end")
        self.assertEqual(built.toasts, [ui.toast("Report ready")], "the job's result is shown")
        self.assertEqual((built.running, built.error), ({}, ""), "it ended cleanly")

        tallied = testing.run(ReportsState, "tally")
        self.assertEqual(_reports(tallied.updates, ReportsState.tally),
                         [(0.25, "Counting"), (0.5, "Half way"), (0.75, "")],
                         "self.progress on the body's thread and yield ui.progress() both report")

    # -- 1.857 ---------------------------------------------------------
    def test_1_857_after_stop_the_next_report_raises_the_stop_a_yield_would(self) -> None:
        """
        Scenario 1.857: Stop, heard through the callback.
        Given: a job whose library reports from its worker thread and re-raises what stopped it,
               and a job with stop_on_leave=True calling self.progress in a loop.
        When:  the user presses Stop on the first, and opens another page during the second.
        Then:  the next self.progress raises GeneratorExit -- what a yield raises when a job is
               stopped -- in the thread that called it, so the library stops at once; the body's
               cleanup runs; what the body changed since its last yield is discarded; no error is
               shown; nothing is left running.
        """
        async def stopped():
            async with testing.Harness() as h:
                job = await h.send(ReportsState, "crunch")
                await _until(lambda: len(SEEN) > 5, "the library never started")
                await (await h.send(State, "ui_cancel", key=runs.key_of(ReportsState.crunch)))
                await job
                await asyncio.sleep(0.05)
                return await h.fields(ReportsState), list(h.results)

        fields, shown = asyncio.run(stopped())
        self.assertIs(SEEN[-1], GeneratorExit, "the library's thread got the stop a yield raises")
        steps = [one for one in SEEN if isinstance(one, int)]
        self.assertLess(len(steps), 300, "the library stopped long before its end")
        self.assertEqual(CLEANED, ["cleaned"], "the body's cleanup ran")
        self.assertEqual(fields["status"], "kept", "what the body changed after its last yield is discarded")
        self.assertEqual((shown, fields["ui_running"], fields["ui_failed"]), ([], {}, {}),
                         "a stopped job shows no error and is not running")

        SEEN.clear()

        async def left():
            async with testing.Harness() as h:
                await h.open_tab()
                job = await h.send(ReportsState, "count_up")
                await asyncio.sleep(0.1)
                await (await h.send(State, "ui_open", page=""))
                await job
                await asyncio.sleep(0.05)
                return await h.fields(ReportsState), list(h.results)

        fields, shown = asyncio.run(left())
        self.assertEqual(SEEN, [GeneratorExit], "leaving the page stops the job at its next report")
        self.assertLess(fields["count"], 299, "it stopped long before its end")
        self.assertEqual((shown, fields["ui_running"]), ([], {}), "no error, nothing running")

    # -- 1.858 ---------------------------------------------------------
    def test_1_858_elsewhere_self_progress_says_it_belongs_in_a_job(self) -> None:
        """
        Scenario 1.858: an action, a load, a job that has ended.
        Given: an action and a load calling self.progress, and a job keeping self.progress to
               call after it ended.
        When:  each calls it.
        Then:  it raises a RuntimeError that says self.progress belongs in a @ui.job (its body or
               a thread the body started), and reports nothing; after the job ended, that the
               job has ended.
        """
        for handler in ("save", "fill"):
            with self.subTest(handler=handler):
                result = testing.run(ReportsState, handler)
                self.assertIn("self.progress", result.fields["said"], "the message names the helper")
                self.assertIn("@ui.job", result.fields["said"], "and where it belongs")
                self.assertEqual(_reports(result.updates, getattr(ReportsState, handler)), [],
                                 "no progress is shown")
        testing.run(ReportsState, "keep")
        with self.assertRaises(RuntimeError) as ended:
            KEPT[0](0.5, "too late")
        self.assertIn("self.progress", str(ended.exception), "after the job ended it names the helper")
        self.assertIn("the job has ended", str(ended.exception), "and says the job has ended (1.914)")

    # -- 1.859 ---------------------------------------------------------
    def test_1_859_progress_is_a_name_a_ui_state_reserves(self) -> None:
        """
        Scenario 1.859: ``progress`` beside ``user``, ``api``, ``params``, ``can``, ``state``.
        Given: ui.State, and project states declaring a field, a computed var or a method named
               progress; a route with the dynamic argument [progress]; a file for lex reflex check.
        When:  each is defined, checked or scanned.
        Then:  progress is a property of ui.State that the browser cannot call; each declaration
               is refused when the class is defined, naming the state and saying to rename it;
               the route is refused like [user]; lex reflex check reports the field as an error.
        """
        self.assertIn("progress", context.RESERVED, "progress is reserved like the other helpers")
        self.assertIsInstance(State.__dict__["progress"], property, "a property")
        self.assertNotIn("progress", State.event_handlers, "the browser cannot call it")

        def field():
            class Shadow(ui.State):
                progress: int = 0

        def computed():
            class Shadow(ui.State):
                @rx.var
                def progress(self) -> float:
                    return 0.0

        def method():
            class Shadow(ui.State):
                def progress(self):
                    pass

        for define in (field, computed, method):
            with self.subTest(define=define.__name__), self.assertRaises(TypeError) as raised:
                define()
            for expected in ("Shadow.progress", "reserved", "Rename"):
                self.assertIn(expected, str(raised.exception), f"the message says {expected}")
        with self.assertRaises(ValueError) as route:
            check_route("/runs/[progress]")
        self.assertIn("[progress]", str(route.exception), "the route is refused like [user]")
        self.assertIn("Rename", str(route.exception))

        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "runs.py"
            path.write_text("from lex import ui\n\n\nclass Runs(ui.State):\n    progress: float = 0.0\n")
            found = checks.find([str(path)])
        self.assertEqual([(f.level, f.message.split(":")[0]) for f in found], [("error", "Runs.progress")],
                         "lex reflex check reports it before the app starts")
        self.assertIn("'progress' is reserved", found[0].message)

    # -- 1.914 ---------------------------------------------------------
    def test_1_914_a_flood_of_reports_never_waits_and_the_page_gets_the_latest(self) -> None:
        """
        Scenario 1.914: a library that reports on every row.
        Given: a job whose library calls self.progress 200 times, as fast as it can, from its
               own thread; a job that kept self.progress to call after it ended.
        When:  it runs through Reflex's event processor; the kept callback is called late.
        Then:  the library never waits for the page: the 200 calls take a moment, not 200 state
               round trips; the browser gets at most about four reports a second, never a
               stale one after a newer, and always the last one ("Step 200"); the late call says
               the job has ended.
        """
        flooded = testing.run(ReportsState, "flood")
        sent = _reports(flooded.updates, ReportsState.flood)
        self.assertLess(SEEN[-1], 0.2, "200 reports took a moment: the calls do not wait for the page")
        self.assertLessEqual(len(sent), 3, "the page got the latest report, not every one")
        self.assertEqual(sent[-1], (1.0, "Step 200"), "and always the last")
        self.assertEqual(sent, sorted(sent), "never an older report after a newer one")
        testing.run(ReportsState, "keep")
        with self.assertRaises(RuntimeError) as ended:
            KEPT[0](0.5, "too late")
        self.assertIn("the job has ended", str(ended.exception))
