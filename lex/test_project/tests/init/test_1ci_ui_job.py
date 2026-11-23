"""lex.ui: @ui.job -- long work that does not block the user.

Intent: work that takes longer than a second runs as a job. Its body works on a copy of the
state; lex-app merges only what the job changed at each yield and at the end, so the user keeps
working and what they typed meanwhile survives. One run at a time per user and argument; a
cancel takes effect at the next yield; a job can stop when the user leaves the page. A regression
is a frozen screen during an export, a job overwriting the user's input, or the same export
running twice.

Cluster 1ci -- scenarios 1.768-1.773. Type: I.
Covers: lex/lex_app/reflex/ui/handlers.py (job, _merge), lex/lex_app/reflex/ui/runs.py (cancel).
Run: python -m lex pytest lex/test_project/tests/init/test_1ci_ui_job.py -v
"""

from __future__ import annotations

import asyncio
import time
from unittest import TestCase

import pytest
import reflex as rx

from lex.lex_app.reflex.ui import handlers, results, runs, testing
from lex.lex_app.reflex.ui.state import State

pytestmark = pytest.mark.init

CLEANED: list = []
RAN: list = []


class Other(State):
    count: int = 0


class Export(State):
    note: str = ""
    status: str = ""
    progress: int = 0
    seen: list[str] = []
    parts: list[str] = []

    @rx.event
    def set_note(self, value: str):
        self.note = value

    @rx.event
    def set_status(self, value: str):
        self.status = value

    @handlers.job
    def build(self):
        self.seen.append(f"start note={self.note!r}")
        self.progress = 10
        yield results.progress(0.1, "Starting")
        time.sleep(0.5)
        self.seen.append(f"before-yield note={self.note!r}")
        self.parts.append("p1")
        self.state(Other).count += 1
        yield results.progress(0.6, "Half way")
        self.seen.append(f"after-yield note={self.note!r}")
        self.progress = 100
        return results.toast("Export ready", tone="success")

    @handlers.job
    def conflicts(self):
        time.sleep(0.3)
        self.status = "set-by-job"

    @handlers.job
    def slow_for(self, code: str):
        time.sleep(0.4)
        RAN.append(code)

    @handlers.job(stop_on_leave=True)
    def stoppable(self):
        try:
            for index in range(10):
                self.progress = index
                yield
                time.sleep(0.1)
        finally:
            CLEANED.append("closed")

    @handlers.job
    def breaks(self):
        self.status = "shown"
        yield
        self.status = "not-shown"
        raise RuntimeError("boom")


class TestCluster01ci_Job(TestCase):
    """1.768-1.773."""

    def setUp(self) -> None:
        CLEANED.clear()
        RAN.clear()

    # -- 1.768 ---------------------------------------------------------
    def test_1_768_the_user_keeps_working_and_their_changes_survive(self) -> None:
        """
        Scenario 1.768: a job and the user at the same time.
        Given: a job that yields, works 0.5 s, yields and finishes; the user types a note during
               the work.
        When:  both run.
        Then:  the user's event returns at once; the job saw the old note until its next yield
               and the new one after it; at the end the job's fields and the user's note both
               stand; another state the job changed through self.state() is merged too; the
               toast is shown; the browser got progress at each yield.
        """
        async def scenario():
            async with testing.Harness() as h:
                job = await h.send(Export, "build")
                await asyncio.sleep(0.2)
                started = time.perf_counter()
                await (await h.send(Export, "set_note", value="typed-by-user"))
                waited = time.perf_counter() - started
                await job
                await asyncio.sleep(0.1)
                return waited, await h.fields(Export), await h.fields(Other), list(h.updates), list(h.results)

        waited, fields, other, updates, shown = asyncio.run(scenario())
        self.assertLess(waited, 0.2, "the user's click did not wait for the job")
        self.assertEqual(fields["seen"], ["start note=''", "before-yield note=''", "after-yield note='typed-by-user'"],
                         "the job sees the user's change after its next yield")
        self.assertEqual((fields["note"], fields["progress"], fields["parts"]), ("typed-by-user", 100, ["p1"]))
        self.assertEqual(other["count"], 1, "another state changed through self.state() is merged")
        key = runs.key_of(Export.build)
        fractions = [u["ui_progress"].get(key) for u in updates if key in u.get("ui_progress", {})]
        self.assertEqual(fractions[:2], [0.1, 0.6], "progress reached the browser at each yield")
        self.assertEqual(shown, [results.Toast("Export ready", "success")])
        self.assertEqual(fields["ui_running"], {}, "nothing is running afterwards")

    # -- 1.769 ---------------------------------------------------------
    def test_1_769_the_job_wins_when_both_change_the_same_field(self) -> None:
        """
        Scenario 1.769: a same-field conflict.
        Given: a job that sets `status` after 0.3 s; the user sets `status` meanwhile.
        When:  both run.
        Then:  the job's value stands.
        """
        async def scenario():
            async with testing.Harness() as h:
                job = await h.send(Export, "conflicts")
                await asyncio.sleep(0.1)
                await (await h.send(Export, "set_status", value="set-by-user"))
                await job
                await asyncio.sleep(0.1)
                return (await h.fields(Export))["status"]

        self.assertEqual(asyncio.run(scenario()), "set-by-job")

    # -- 1.770 ---------------------------------------------------------
    def test_1_770_one_run_at_a_time_per_user_and_argument(self) -> None:
        """
        Scenario 1.770: repeats.
        Given: a job that takes 0.4 s.
        When:  it is sent three times rapidly for "a", once for "b" meanwhile, and once more
               for "a" after it finished.
        Then:  "a" ran once, the two repeats were ignored, "b" ran beside it, and the later
               "a" ran again.
        """
        async def scenario():
            async with testing.Harness() as h:
                first = [await h.send(Export, "slow_for", code="a") for _ in range(3)]
                other = await h.send(Export, "slow_for", code="b")
                for future in (*first, other):
                    await future
                await asyncio.sleep(0.1)
                await (await h.send(Export, "slow_for", code="a"))
                await asyncio.sleep(0.1)

        asyncio.run(scenario())
        self.assertEqual(sorted(RAN), ["a", "a", "b"], "one run per argument at a time; the repeats were ignored")

    # -- 1.771 ---------------------------------------------------------
    def test_1_771_cancel_takes_effect_at_the_next_yield(self) -> None:
        """
        Scenario 1.771: Cancel.
        Given: a job that yields every 0.1 s inside try/finally.
        When:  the cancel event is sent while it runs.
        Then:  the job stops within a few steps, its finally block runs, and it is no longer
               running; ui.cancel(handler) is the event for a Cancel button.
        """
        async def scenario():
            async with testing.Harness() as h:
                job = await h.send(Export, "stoppable")
                await asyncio.sleep(0.25)
                await (await h.send(State, "ui_cancel", key=runs.key_of(Export.stoppable)))
                await job
                await asyncio.sleep(0.1)
                return await h.fields(Export)

        fields = asyncio.run(scenario())
        self.assertLess(fields["progress"], 9, "the job did not run to the end")
        self.assertEqual(CLEANED, ["closed"], "the body's finally block ran")
        self.assertEqual(fields["ui_running"], {})
        self.assertIn("ui_cancel", str(runs.cancel(Export.stoppable)))

    # -- 1.772 ---------------------------------------------------------
    def test_1_772_stop_on_leave_ends_the_job_when_the_page_changes(self) -> None:
        """
        Scenario 1.772: leaving the page.
        Given: a job with stop_on_leave=True.
        When:  the user opens another page (a new visit) while it runs.
        Then:  the job stops at its next yield and its cleanup runs.
        """
        async def scenario():
            async with testing.Harness() as h:
                job = await h.send(Export, "stoppable")
                await asyncio.sleep(0.25)
                await (await h.send(State, "ui_open", required=[], optional=[], requires=""))
                await job
                await asyncio.sleep(0.1)
                return await h.fields(Export)

        fields = asyncio.run(scenario())
        self.assertLess(fields["progress"], 9)
        self.assertEqual(CLEANED, ["closed"])

    # -- 1.773 ---------------------------------------------------------
    def test_1_773_a_failing_job_keeps_what_was_merged_and_reports(self) -> None:
        """
        Scenario 1.773: failure.
        Given: a job that yields once, changes more, then raises.
        When:  it runs.
        Then:  what was merged at the yield stays, the later change is discarded, an error
               toast is shown, the failure message is readable with ui.failed, and it is no
               longer running.
        """
        result = testing.run(Export, "breaks")
        self.assertEqual(result.fields["status"], "shown")
        self.assertTrue(result.error)
        self.assertEqual(result.toasts[-1].tone, "error")
        self.assertEqual(result.failed[runs.key_of(Export.breaks)], result.error)
        self.assertEqual(result.running, {})
