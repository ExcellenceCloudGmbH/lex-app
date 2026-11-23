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
import os
import threading
import time
from unittest import TestCase
from unittest.mock import patch

import pytest
import reflex as rx

from lex.lex_app.reflex.ui import guard, handlers, pools, results, runs, testing
from lex.lex_app.reflex.ui.state import State
from lex.test_project.tests.init._ui_harness_aids import lock_held, lock_waiters, unstored_on_error

pytestmark = pytest.mark.init

CLEANED: list = []
RAN: list = []
#: Gate name -> the event a body (or the foreground ``hold``) waits on; the test releases it.
GATES: dict[str, threading.Event] = {}
#: How many ``queued`` bodies run at once, and the most seen.
CONCURRENCY = {"now": 0, "most": 0}
_COUNT = threading.Lock()


def _gate(name: str) -> str:
    GATES[name] = threading.Event()
    return name


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
        await asyncio.sleep(0.001)


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

    @handlers.job
    def export(self, year: int, month: int):
        if not GATES["export"].wait(5):
            raise RuntimeError("gate export was never released")
        RAN.append((year, month))

    @handlers.job
    def queued(self, code: str):
        with _COUNT:
            CONCURRENCY["now"] += 1
            CONCURRENCY["most"] = max(CONCURRENCY["most"], CONCURRENCY["now"])
        time.sleep(0.05)                     # long enough to overlap if the pool ran both at once
        with _COUNT:
            CONCURRENCY["now"] -= 1
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
            self.status = "set-in-cleanup"

    @handlers.job
    def fails_when_released(self):
        if not GATES["body"].wait(5):
            raise RuntimeError("gate body was never released")
        raise RuntimeError("export source down")

    @handlers.job(requires=guard.permission("app.Thing", "read"))
    def guarded(self):
        RAN.append("guarded")
        self.status = "ran"

    @rx.event
    async def hold(self):
        """A foreground handler: holds the user's lock until the test releases it."""
        await asyncio.to_thread(GATES["hold"].wait, 5)

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
        CONCURRENCY.update(now=0, most=0)

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

    def test_1_770_a_run_is_one_set_of_arguments_and_a_full_pool_queues(self) -> None:
        """
        Scenario 1.770: which repeats are ignored, and a full job pool.
        Given: a job with two arguments, held at a gate; and a job pool of one thread.
        When:  the job is sent for (2025, 1), then for (2025, 2) and once more for (2025, 1) while
               the first runs; and, on the one-thread pool, two jobs with different arguments
               are sent at once.
        Then:  the call with a different second argument runs beside the first (only the exact
               same arguments are a repeat); the view still sees each run under its first
               argument; on the full pool the second job waits its turn instead of failing:
               both run, never at the same time.
        """
        async def distinct():
            async with testing.Harness() as h:
                first = await h.send(Export, "export", year=2025, month=1)
                second = await h.send(Export, "export", year=2025, month=2)
                key = runs.key_of(Export.export)

                async def both_running():
                    return (await h.fields(Export))["ui_running"].get(key) == ["2025", "2025"]

                await _until(both_running, "the call with another second argument was not started")
                repeat = await h.send(Export, "export", year=2025, month=1)
                await repeat
                GATES["export"].set()
                await first
                await second
                return await h.fields(Export)

        _gate("export")
        fields = asyncio.run(distinct())
        self.assertEqual(sorted(RAN), [(2025, 1), (2025, 2)],
                         "a call with other arguments runs; the exact repeat is ignored")
        self.assertEqual(fields["ui_running"], {}, "nothing is running afterwards")

        RAN.clear()

        async def full_pool():
            async with testing.Harness() as h:
                one = await h.send(Export, "queued", code="x")
                two = await h.send(Export, "queued", code="y")
                await one
                await two
                return list(h.outcomes)

        pools.shutdown()                      # the next pool is built with the size below
        self.addCleanup(pools.shutdown)       # and later tests get pools of the usual size again
        with patch.dict(os.environ, {"LEX_UI_JOB_THREADS": "1"}):
            outcomes = asyncio.run(full_pool())
        self.assertEqual((sorted(RAN), outcomes), (["x", "y"], []), "both jobs ran; neither failed")
        self.assertEqual(CONCURRENCY["most"], 1, "the second job waited for the only thread")

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
        self.assertEqual(fields["status"], "", "what the stopped body changed in its cleanup is never merged")
        self.assertEqual(fields["ui_running"], {})
        self.assertIn("ui_cancel", str(runs.cancel(Export.stoppable)))

    def test_1_771_a_job_cancelled_while_it_winds_up_is_not_left_running(self) -> None:
        """
        Scenario 1.771: a job's task is cancelled at the worst moment.
        Given: a job whose body has failed and whose wind-up waits for the user's lock, held by a
               foreground handler.
        When:  the job's task is cancelled (as Reflex cancels a superseded event), then the
               foreground handler finishes.
        Then:  the wind-up still runs: the job is not left running, its failure is recorded for
               ui.failed, and the run's claim is released.
        """
        async def scenario():
            async with testing.Harness() as h:
                job = await h.send(Export, "fails_when_released")
                key = runs.key_of(Export.fails_when_released)

                async def marked():
                    return key in (await h.fields(Export))["ui_running"]

                await _until(marked, "the job was never marked running")
                holder = await h.send(Export, "hold")
                await _until(lambda: lock_held(h), "the foreground handler never took the lock")
                GATES["body"].set()
                await _until(lambda: h.outcomes and lock_waiters(h),
                             "the failed job never waited for the lock to wind up")
                job.cancel()
                GATES["hold"].set()
                await holder

                async def wound_up():
                    return (await h.fields(Export))["ui_running"] == {}

                await _until(wound_up, "the cancelled job was left running")
                return await h.fields(Export), [name for (_, name, _) in runs._ACTIVE]

        _gate("body")
        _gate("hold")
        with self.assertLogs("lex.lex_app.reflex.ui.handlers", level="ERROR"):
            fields, claimed = asyncio.run(scenario())
        key = runs.key_of(Export.fails_when_released)
        self.assertIn("Reference", fields["ui_failed"].get(key, ""), "the failure is recorded for ui.failed")
        self.assertNotIn(key, claimed, "the run's claim is released")

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
                await (await h.send(State, "ui_open", page=""))
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

    def test_1_773_a_job_whose_requirement_is_not_met_never_runs(self) -> None:
        """
        Scenario 1.773: requires= not met.
        Given: a job declaring requires=, a user without the grant, and a state manager that,
               like Reflex's Redis manager, does not store a block that raised.
        When:  the job is sent.
        Then:  the body never runs; the user is told they are not permitted, the message is
               recorded for ui.failed, the warning names the handler and the user's id, and
               nothing is left running.
        """
        with unstored_on_error(), self.assertLogs("lex.lex_app.reflex.ui.handlers", level="WARNING") as logs:
            result = testing.run(Export, "guarded", user="anna")
        key = runs.key_of(Export.guarded)
        self.assertEqual((RAN, result.fields["status"]), ([], ""), "the body never ran")
        self.assertTrue(result.denied, "the user is told they are not permitted")
        self.assertEqual(result.failed.get(key), result.toasts[-1].message, "the message is recorded for ui.failed")
        self.assertEqual(result.running, {}, "nothing is left running")
        text = "\n".join(logs.output)
        self.assertIn(key, text, "the warning names the handler")
        self.assertIn("anna", text, "the warning names the user's id")
