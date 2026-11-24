"""lex.ui: what a handler returns, and the thread pools its body runs on.

Intent: a consultant's handler returns plain values -- go to a page, show a toast, hand over a
file, report progress -- and lex-app turns them into browser events; its body runs on one of two
sized thread pools, never on the event loop, so one slow handler cannot stall every user. A
regression is a handler whose result is silently dropped, or long jobs starving page loads.

Cluster 1cc -- scenarios 1.747-1.749. Type: U.
Covers: lex/lex_app/reflex/ui/results.py, lex/lex_app/reflex/ui/pools.py.
Run: python -m lex pytest lex/test_project/tests/init/test_1cc_ui_results_and_pools.py -v
"""

from __future__ import annotations

import asyncio
import contextvars
import os
import threading
from unittest import TestCase
from unittest.mock import patch

import pytest

from lex.lex_app.reflex.ui import pools, results

pytestmark = pytest.mark.init


class TestCluster01cc_Results(TestCase):
    """1.747-1.748: return values and their browser events."""

    # -- 1.747 ---------------------------------------------------------
    def test_1_747_a_handler_returns_plain_values_and_nothing_else(self) -> None:
        """
        Scenario 1.747: what a handler may return or yield.
        Given: the four value builders.
        When:  values are built and split as a handler's return value would be.
        Then:  goto, toast and download are results; progress is the progress report, clamped
               to 0..1; None is nothing; a wrong tone, an empty path, a download of nothing and
               any other object are refused with the handler's name (a Reflex event is allowed: 1.829).
        """
        out, report = results.split([results.goto("/a"), results.toast("Saved", tone="success"),
                                     results.progress(1.7, "Reading")], handler="Tickets.open")
        self.assertEqual(out, [results.Goto("/a"), results.Toast("Saved", "success")], "results keep their order")
        self.assertEqual(report, results.Progress(1.0, "Reading"), "progress is clamped")
        self.assertEqual(results.split(None, handler="h"), ([], None), "None is nothing")
        self.assertEqual(results.split([results.toast("Saved"), None], handler="h")[0], [results.Toast("Saved", "info", "")],
                         "a None inside a list is nothing too: a helper that had nothing to return")
        self.assertEqual(results.split(results.goto("/b", replace=True), handler="h")[0], [results.Goto("/b", True)])
        for bad in (lambda: results.toast("x", tone="loud"), lambda: results.goto(""), lambda: results.download()):
            with self.assertRaises(ValueError, msg="invalid values are refused when built"):
                bad()
        with self.assertRaises(TypeError) as raised:
            results.split(object(), handler="Tickets.open")
        self.assertIn("Tickets.open", str(raised.exception), "the error names the handler")
        self.assertEqual(results.Refuse("This request is closed.").message, "This request is closed.")

    # -- 1.748 ---------------------------------------------------------
    def test_1_748_values_become_browser_events_and_are_recorded_for_tests(self) -> None:
        """
        Scenario 1.748: results turn into Reflex events.
        Given: a goto inside the app, a goto to another site, a toast and a download.
        When:  they are converted.
        Then:  an in-app path is Reflex's redirect, another site (a full URL, or //host) a
               whole-page navigation, the
               toast RFDS's toast of that tone, the download Reflex's download; with the test
               recorder on, the values themselves are recorded in order.
        """
        with patch.object(results, "RECORDER", []) as recorded:
            events = results.to_events([results.goto("/tickets/1"), results.goto("https://example.org/x"),
                                        results.toast("Saved", tone="success", description="All good"),
                                        results.download(url="/files/a.csv", filename="a.csv"),
                                        results.goto("//example.org/y")])
            self.assertEqual(len(events), 5, "one event per result")
            rendered = [str(event) for event in events]
            self.assertIn("/tickets/1", rendered[0])
            self.assertIn("window.location.assign", rendered[1], "another site is a whole-page load")
            self.assertIn("Saved", rendered[2])
            self.assertIn("a.csv", rendered[3])
            self.assertIn("window.location.assign", rendered[4], "//host is another site too")
            self.assertEqual([type(r).__name__ for r in recorded], ["Goto", "Goto", "Toast", "Download", "Goto"])
        self.assertEqual(results.message("lex.ui.x", "Reference: {incident}", incident="AB12"), "Reference: AB12")


class TestCluster01cc_Pools(TestCase):
    """1.749: the two pools."""

    def tearDown(self) -> None:
        pools.shutdown()

    # -- 1.749 ---------------------------------------------------------
    def test_1_749_bodies_run_on_sized_pools_with_the_callers_context(self) -> None:
        """
        Scenario 1.749: where a handler body runs.
        Given: the interactive and the job pool, with their sizes set by environment.
        When:  functions are run on each.
        Then:  they run off the calling thread, on a thread named for their pool; the caller's
               context variables are visible; the sizes come from LEX_UI_ACTION_THREADS and
               LEX_UI_JOB_THREADS (16 and 8 by default, at least 1); a full pool queues.
        """
        marker = contextvars.ContextVar("marker", default="unset")

        counts = {"now": 0, "most": 0}
        lock = threading.Lock()

        def counted():
            with lock:
                counts["now"] += 1
                counts["most"] = max(counts["most"], counts["now"])
            threading.Event().wait(0.05)        # long enough to overlap if the pool ran both at once
            with lock:
                counts["now"] -= 1
            return "done"

        async def scenario():
            marker.set("from-caller")
            name, seen = await pools.run("interactive", lambda: (threading.current_thread().name, marker.get()))
            job_name = await pools.run("jobs", lambda: threading.current_thread().name)
            queued = await asyncio.gather(pools.run("jobs", counted), pools.run("jobs", counted))
            return name, seen, job_name, queued

        with patch.dict(os.environ, {"LEX_UI_ACTION_THREADS": "3", "LEX_UI_JOB_THREADS": "0"}):
            self.assertEqual((pools.size("interactive"), pools.size("jobs")), (3, 1), "sizes from the environment, at least 1")
            name, seen, job_name, queued = asyncio.run(scenario())
        self.assertEqual((queued, counts["most"]), (["done", "done"], 1),
                         "on a full one-thread pool the second call waits its turn and does not fail")
        self.assertTrue(name.startswith("lex-ui-interactive"), name)
        self.assertTrue(job_name.startswith("lex-ui-jobs"), job_name)
        self.assertEqual(seen, "from-caller", "the body sees the caller's context variables")
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("LEX_UI_ACTION_THREADS", None)
            os.environ.pop("LEX_UI_JOB_THREADS", None)
            self.assertEqual((pools.size("interactive"), pools.size("jobs")), (16, 8), "the defaults")
