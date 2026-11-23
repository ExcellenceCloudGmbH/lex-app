"""lex.ui: @ui.action -- a short handler written as a plain function.

Intent: a click that finishes in under a second is a plain function on the state. lex-app runs
its body off the event loop on the live state, flushes changes at each yield, marks it running
for the view, and on failure discards what the browser was not yet shown and tells the user: a
refusal in the handler's own words, "not permitted", or a generic message with a reference.
A regression is a slow click stalling every user, half-applied values after an error, or an
exception text reaching the browser.

Cluster 1ch -- scenarios 1.762-1.767. Type: I.
Covers: lex/lex_app/reflex/ui/handlers.py (action, _drive, _classify), lex/lex_app/reflex/ui/testing.py.
Run: python -m lex pytest lex/test_project/tests/init/test_1ch_ui_action.py -v
"""

from __future__ import annotations

import asyncio
import os
import threading
import time
from typing import Any
from unittest.mock import patch

import pytest
from django.contrib.auth.models import Group
from django.test import TransactionTestCase

from lex.lex_app.reflex.data import LexApiError
from lex.lex_app.reflex.ui import context, guard, handlers, results, runs, testing
from lex.lex_app.reflex.ui.state import State

pytestmark = pytest.mark.init

SEEN: list = []
#: Set by ``sleeps_then_yields`` when its body has finished, cancelled or not.
FINISHED = threading.Event()
#: ``cleanup_breaks``: set when its body has started; the test sets RELEASE to let it go on.
BODY_STARTED = threading.Event()
RELEASE = threading.Event()


class Board(State):
    status: str = ""
    items: list[str] = []
    meta: dict[str, int] = {}
    steps: int = 0
    _holder: Any = None

    @handlers.action
    def approve(self, code: str):
        SEEN.append((threading.current_thread().name, self.user.username, self.params.get("section")))
        self.status = f"approved-{code}"
        self.items.append(code)
        self.meta["n"] = len(self.items)
        return results.toast("Saved", tone="success")

    @handlers.action
    def stepwise(self):
        self.steps = 1
        yield
        time.sleep(0.3)
        self.steps = 2
        return results.goto("/done")

    @handlers.action
    def breaks_after_a_yield(self):
        self.status = "shown"
        yield
        self.status = "not-shown"
        self.items.append("half")
        raise ValueError("secret detail")

    @handlers.action
    def refuses(self):
        self.status = "half"
        raise results.Refuse("This request is closed.")

    @handlers.action
    def api_denied(self):
        raise LexApiError(403, {"detail": "no"})

    @handlers.action
    def api_invalid(self):
        raise LexApiError(400, {"name": ["Too long."]})

    @handlers.action
    def api_refused_by_lex(self):
        raise LexApiError(400, {"code": "EDIT_NOT_ALLOWED", "detail": "You may not edit: amount.",
                                "error": "You may not edit: amount.", "fields": {"amount": "Not editable."}})

    @handlers.action
    def api_refused_with_fields(self):
        raise LexApiError(400, {"code": "INVALID", "error": "Invalid.", "traceback": "Traceback (most recent call",
                                "fields": {"amount": "Not editable.", "name": ["Too long."]}})

    @handlers.action
    def reload(self):
        self.status = "reloaded"

    @handlers.action
    def save_and_reload(self):
        self.status = "saved"
        self.reload()                        # a sibling handler: it would not run

    @handlers.action
    def cleanup_breaks(self):
        try:
            BODY_STARTED.set()
            RELEASE.wait(5)
            yield
        finally:
            raise ValueError("cleanup broke")

    @handlers.action
    def counts_unguarded(self):
        self.steps = Group.objects.count()

    @handlers.action(requires=guard.read(Group))
    def counts_guarded(self):
        self.steps = Group.objects.count()

    @handlers.action
    def sleeps(self, seconds: float):
        time.sleep(seconds)

    @handlers.action
    def yields_a_wrong_value(self):
        try:
            self.status = "changed"
            yield "not a result"
        finally:
            self.status = "set-in-finally"

    @handlers.action
    def sleeps_then_yields(self):
        try:
            time.sleep(0.3)
            yield
            self.steps = 99
        finally:
            FINISHED.set()


class TestCluster01ch_Action(TransactionTestCase):
    """1.762-1.767. Transactional: the body runs on another thread, with its own connection."""

    def setUp(self) -> None:
        SEEN.clear()
        FINISHED.clear()
        BODY_STARTED.clear()
        RELEASE.clear()

    # -- 1.762 ---------------------------------------------------------
    def test_1_762_a_plain_function_runs_off_the_loop_on_the_live_state(self) -> None:
        """
        Scenario 1.762: the happy path.
        Given: an action that assigns a field, changes a list and a dict in place, and returns a toast.
        When:  it is run for a signed-in user on a page with a parameter.
        Then:  the body ran on an interactive pool thread and saw the user and the parameter; the
               fields changed and reached the browser; the toast was shown; nothing is running
               afterwards and no failure is recorded; an async def under @ui.action is refused.
        """
        result = testing.run(Board, "approve", code="A1", user="anna", params={"section": "open"})
        thread, username, section = SEEN[0]
        self.assertTrue(thread.startswith("lex-ui-interactive"), thread)
        self.assertEqual((username, section), ("anna", "open"))
        self.assertEqual((result.fields["status"], result.fields["items"], result.fields["meta"]),
                         ("approved-A1", ["A1"], {"n": 1}))
        self.assertEqual(result.toasts, [results.Toast("Saved", "success")])
        self.assertIn({"status": "approved-A1"}, [{k: v for k, v in u.items() if k == "status"} for u in result.updates])
        self.assertEqual((result.refused, result.denied, result.error), ("", False, ""))
        self.assertEqual(result.running, {}, "nothing is running afterwards")
        with self.assertRaises(TypeError) as raised:
            @handlers.action
            async def bad(self):
                pass
        self.assertIn("plain function", str(raised.exception))

    # -- 1.763 ---------------------------------------------------------
    def test_1_763_running_is_shown_first_and_each_yield_flushes(self) -> None:
        """
        Scenario 1.763: what the browser sees, in order.
        Given: an action that sets a field, yields, works 0.3 s, sets it again and goes to a page.
        When:  it runs.
        Then:  the browser first learns the handler is running, then sees steps=1 before the
               body has finished, then steps=2; "running" is cleared at the end; the goto is sent.
        """
        result = testing.run(Board, "stepwise")
        key = runs.key_of(Board.stepwise)
        marks = [u["ui_running"] for u in result.updates if "ui_running" in u]
        self.assertIn({key: [""]}, marks, "the browser is told the handler is running")
        self.assertEqual(marks[-1], {}, "the last update says it is not")
        # Only real steps count (an update carrying steps=0 is not one).
        steps = [(t, u["steps"]) for t, u in zip(result.update_times, result.updates) if u.get("steps")]
        self.assertEqual([value for _, value in steps], [1, 2])
        first_running = next(i for i, u in enumerate(result.updates) if u.get("ui_running") == {key: [""]})
        first_step = next(i for i, u in enumerate(result.updates) if u.get("steps") == 1)
        self.assertLessEqual(first_running, first_step, "running is shown no later than the body's first change")
        self.assertGreaterEqual(steps[1][0] - steps[0][0], 0.25, "steps=1 reached the browser before the body finished")
        self.assertEqual(result.goto, "/done")

    # -- 1.764 ---------------------------------------------------------
    def test_1_764_a_failure_discards_what_was_not_shown_and_hides_the_detail(self) -> None:
        """
        Scenario 1.764: an unexpected error.
        Given: an action that shows a value at a yield, changes more, then raises.
        When:  it runs, with full logging and with LEX_UI_LOG_ERROR_TEXT=false.
        Then:  the fields are back at what the yield showed; the user gets an error toast with a
               reference and without the exception's text; the failure message is recorded for
               the view; the log holds the traceback by default and only the type when limited.
               A state field holding an object that cannot be copied (a lock) ends the same way:
               the body never runs, the user gets the standard error toast, the log names the
               field, and nothing is left marked running. A session that cannot be resolved
               ends the same way: an unexpected error with a reference, an error toast, nothing
               left running. A body that yields a wrong value is an unexpected error too, and
               its own cleanup (a finally) runs before the restore, so the field ends restored.
        """
        with self.assertLogs("lex.lex_app.reflex.ui.handlers", level="ERROR") as logs:
            result = testing.run(Board, "breaks_after_a_yield")
        self.assertEqual((result.fields["status"], result.fields["items"]), ("shown", []),
                         "what the browser saw stays; the rest is discarded")
        self.assertTrue(result.error, "an unexpected error is reported")
        self.assertIn("Reference", result.error)
        self.assertNotIn("secret detail", result.error, "the exception text never reaches the browser")
        self.assertEqual(result.toasts[-1].tone, "error")
        self.assertEqual(result.failed[runs.key_of(Board.breaks_after_a_yield)], result.error)
        self.assertIn("secret detail", "\n".join(logs.output), "the log has the traceback")
        with patch.dict(os.environ, {"LEX_UI_LOG_ERROR_TEXT": "false"}):
            with self.assertLogs("lex.lex_app.reflex.ui.handlers", level="ERROR") as limited:
                testing.run(Board, "breaks_after_a_yield")
        text = "\n".join(limited.output)
        self.assertIn("ValueError", text)
        self.assertNotIn("secret detail", text, "limited logging keeps the text out")

        async def uncopyable():
            async with testing.Harness() as harness:
                await harness.set_fields(Board, _holder=threading.Lock())
                await (await harness.send(Board, "breaks_after_a_yield"))
                await harness.set_fields(Board, _holder=None)
                return await harness.fields(Board), list(harness.results), list(harness.outcomes)

        with self.assertLogs("lex.lex_app.reflex.ui.handlers", level="ERROR") as locked:
            fields, shown, outcomes = asyncio.run(uncopyable())
        self.assertEqual([o.kind for o in outcomes], ["error"], "a field that cannot be copied is an unexpected error")
        self.assertIn("Reference", outcomes[0].message, "the user gets the standard error message")
        self.assertEqual(shown[-1].tone, "error", "the user is shown an error toast")
        self.assertEqual(fields["status"], "", "the body never ran")
        self.assertEqual(fields["ui_running"], {}, "nothing is left marked running")
        self.assertIn("Board._holder cannot be copied", "\n".join(locked.output), "the log names the field")

        async def unresolvable():
            async def broken(state):
                raise ConnectionError("identity provider unreachable")

            async with testing.Harness() as harness:
                context.set_resolver(broken)
                await (await harness.send(Board, "approve", code="A1"))
                return await harness.fields(Board), list(harness.results), list(harness.outcomes)

        with self.assertLogs("lex.lex_app.reflex.ui.handlers", level="ERROR") as unresolved:
            fields, shown, outcomes = asyncio.run(unresolvable())
        self.assertEqual([o.kind for o in outcomes], ["error"], "a session that cannot be resolved is an unexpected error")
        self.assertIn("Reference", outcomes[0].message, "the user gets the standard error message with a reference")
        self.assertEqual(shown[-1].tone, "error", "the user is shown an error toast")
        self.assertEqual((fields["status"], fields["ui_running"]), ("", {}), "the body never ran and nothing is left running")
        self.assertIn("identity provider unreachable", "\n".join(unresolved.output), "the log carries the error")

        with self.assertLogs("lex.lex_app.reflex.ui.handlers", level="ERROR"):
            wrong = testing.run(Board, "yields_a_wrong_value")
        self.assertTrue(wrong.error, "a wrongly yielded value is reported as an unexpected error")
        self.assertEqual(wrong.fields["status"], "",
                         "the body's finally ran before the restore, so the field ends at its restored value")
        self.assertEqual(wrong.running, {}, "nothing is left marked running")

    def test_1_764_an_action_that_calls_another_handler_fails_and_says_why(self) -> None:
        """
        Scenario 1.764: a body calls a sibling handler (self.reload()).
        Given: an action that sets a field, then calls another @ui.action of its state.
        When:  it runs.
        Then:  it fails like an unexpected error (the field is restored, the user gets the error
               toast) instead of silently doing nothing; the log names both handlers and says to
               move the shared work into an underscore method; the sibling did not run.
        """
        with self.assertLogs("lex.lex_app.reflex.ui.handlers", level="ERROR") as logs:
            result = testing.run(Board, "save_and_reload")
        self.assertTrue(result.error, "calling a sibling handler fails the action")
        self.assertEqual(result.fields["status"], "", "the action's change is discarded and the sibling never ran")
        self.assertEqual(result.running, {}, "nothing is left marked running")
        text = "\n".join(logs.output)
        for expected in ("Board.save_and_reload", "self.reload()", "self._reload()"):
            self.assertIn(expected, text, f"the log says {expected}")

    # -- 1.765 ---------------------------------------------------------
    def test_1_765_refusals_and_api_answers_are_shown_in_their_own_words(self) -> None:
        """
        Scenario 1.765: expected endings.
        Given: actions that raise ui.Refuse, get a 403 from the API, and get a validation error.
        When:  each runs.
        Then:  the refusal's message is shown as written, as a warning, and its changes are
               discarded; the 403 is the standard "not permitted" message; the validation error
               is shown with the API's detail -- lex-app's own refusal body by its detail alone,
               and a body without one as its fields' name: text pairs, never its code, error or
               traceback; none is reported as an unexpected error.
        """
        refused = testing.run(Board, "refuses")
        self.assertEqual((refused.refused, refused.fields["status"]), ("This request is closed.", ""))
        self.assertEqual((refused.toasts[-1].tone, refused.error), ("warning", ""))
        denied = testing.run(Board, "api_denied")
        self.assertTrue(denied.denied)
        self.assertIn("not permitted", denied.toasts[-1].message.lower())
        invalid = testing.run(Board, "api_invalid")
        self.assertIn("Too long.", invalid.refused, "the API's detail is the message")
        lex_body = testing.run(Board, "api_refused_by_lex")
        self.assertEqual(lex_body.refused, "You may not edit: amount.",
                         "lex-app's refusal shows its detail alone, without code, error or fields")
        fields_only = testing.run(Board, "api_refused_with_fields")
        self.assertEqual(fields_only.refused, "amount: Not editable. name: Too long.",
                         "without a detail, the fields are shown as name: text, without code, error or traceback")

    # -- 1.766 ---------------------------------------------------------
    def test_1_766_the_database_is_reachable_only_with_requires(self) -> None:
        """
        Scenario 1.766: the ORM rule, end to end.
        Given: two rows; an action that counts them without requires=, and one with requires=read.
        When:  each runs, the second with and without the grant.
        Then:  without requires= the query is refused and reported as an unexpected error whose
               log names the handler and the way out; with the grant the count is read; without
               the grant the body never runs and the user is told they are not permitted; that
               message is recorded as the handler's failure and nothing is left running.
        """
        Group.objects.create(name="a")
        Group.objects.create(name="b")
        with self.assertLogs("lex.lex_app.reflex.ui.handlers", level="ERROR") as logs:
            unguarded = testing.run(Board, "counts_unguarded")
        self.assertTrue(unguarded.error)
        self.assertIn("requires=", "\n".join(logs.output))
        granted = testing.run(Board, "counts_guarded", grants=[(Group, "read")])
        self.assertEqual((granted.fields["steps"], granted.error, granted.denied), (2, "", False))
        refused = testing.run(Board, "counts_guarded")
        self.assertTrue(refused.denied)
        self.assertEqual(refused.fields["steps"], 0, "the body never ran")
        self.assertEqual(refused.failed.get(runs.key_of(Board.counts_guarded)), refused.toasts[-1].message,
                         "the not-permitted message is recorded as the handler's failure")
        self.assertEqual(refused.running, {}, "a refused run leaves nothing marked running")

    # -- 1.767 ---------------------------------------------------------
    def test_1_767_the_loop_stays_free_and_a_slow_action_is_called_out(self) -> None:
        """
        Scenario 1.767: one user's slow action and everyone else.
        Given: an action that sleeps.
        When:  it sleeps 0.4 s while a ticker runs on the event loop; and 1.1 s in development;
               and an action that sleeps then yields is cancelled while it sleeps; and one whose
               cleanup (a finally) raises is cancelled.
        Then:  the loop keeps ticking during the sleep; the 1.1 s run logs a warning that names
               the handler and says to make it a job; the cancelled body's thread finishes (it
               is not left blocked at its yield), nothing is left marked running, and a
               following action on the same tab still runs; the failing cleanup is logged as
               a warning, not swallowed.
        """
        async def scenario():
            async with testing.Harness() as harness:
                ticks = 0

                async def tick():
                    nonlocal ticks
                    while True:
                        await asyncio.sleep(0.01)
                        ticks += 1

                ticker = asyncio.create_task(tick())
                await (await harness.send(Board, "sleeps", seconds=0.4))
                ticker.cancel()
                return ticks

        self.assertGreater(asyncio.run(scenario()), 20, "the event loop was not blocked by the sleeping body")
        with patch.object(handlers, "_is_development", return_value=True):
            with self.assertLogs("lex.lex_app.reflex.ui.handlers", level="WARNING") as logs:
                testing.run(Board, "sleeps", seconds=1.1)
        text = "\n".join(logs.output)
        self.assertIn("Board.sleeps", text)
        self.assertIn("@ui.job", text)

        async def cancelled():
            async with testing.Harness() as harness:
                future = await harness.send(Board, "sleeps_then_yields")
                await asyncio.sleep(0.1)
                future.cancel()
                for _ in range(200):
                    if FINISHED.is_set():
                        break
                    await asyncio.sleep(0.01)
                finished = FINISHED.is_set()
                await asyncio.wait_for(await harness.send(Board, "approve", code="B1"), 5)
                return finished, await harness.fields(Board)

        finished, fields = asyncio.run(cancelled())
        self.assertTrue(finished, "the cancelled body's thread finished instead of blocking at its yield")

        async def broken_cleanup(logs):
            async with testing.Harness() as harness:
                future = await harness.send(Board, "cleanup_breaks")
                deadline = time.monotonic() + 5
                while not BODY_STARTED.is_set():
                    self.assertLess(time.monotonic(), deadline, "the body never started")
                    await asyncio.sleep(0.001)
                future.cancel()
                RELEASE.set()                        # the body reaches its yield, is stopped, and its finally raises
                while not any("cleanup" in record.getMessage() for record in logs.records):
                    self.assertLess(time.monotonic(), deadline, "the failed cleanup was never logged")
                    await asyncio.sleep(0.001)

        with self.assertLogs("lex.lex_app.reflex.ui.handlers", level="WARNING") as cleanup_logs:
            asyncio.run(broken_cleanup(cleanup_logs))
        self.assertIn("cleanup broke", "\n".join(cleanup_logs.output),
                      "a stopped body whose cleanup fails is logged as a warning, not swallowed")
        self.assertEqual(fields["steps"], 0, "the cancelled body did not go on past its yield")
        self.assertEqual(fields["status"], "approved-B1", "a following action on the same tab still runs")
        self.assertEqual(fields["ui_running"], {}, "nothing is left marked running")
