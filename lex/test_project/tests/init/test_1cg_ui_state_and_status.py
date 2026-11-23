"""lex.ui: the state base class, and a handler's status without hand-written flags.

Intent: every page state inherits ui.State, which carries what a view needs to show a handler's
status -- is it running (for which row), did it fail, how far is it -- and gives a body self.user,
self.api, self.params, self.can and self.state. A regression is a spinner that never stops, or a
project writing its own busy flags again.

Cluster 1cg -- scenarios 1.759-1.761. Type: U.
Covers: lex/lex_app/reflex/ui/state.py, lex/lex_app/reflex/ui/runs.py.
Run: python -m lex pytest lex/test_project/tests/init/test_1cg_ui_state_and_status.py -v
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest import TestCase

import pytest
import reflex as rx

from lex.lex_app.reflex.ui import results, runs
from lex.lex_app.reflex.ui.state import State

pytestmark = pytest.mark.init


class _Board(State):
    rows: list[dict[str, str]] = []

    @rx.event
    def open_ticket(self, code: str):
        pass


def _status():
    return SimpleNamespace(ui_running={}, ui_failed={}, ui_progress={}, ui_progress_message={}, _ui_boots={})


class TestCluster01cg_State(TestCase):
    """1.759-1.761."""

    # -- 1.759 ---------------------------------------------------------
    def test_1_759_ui_state_adds_fields_and_helpers_but_no_event_handlers_of_its_own_names(self) -> None:
        """
        Scenario 1.759: what ui.State is.
        Given: ui.State and a project state inheriting it.
        When:  their fields and handlers are listed.
        Then:  ui.State is a real Reflex state with the status fields and the visit counter;
               user, api, params, can and state are properties, not event handlers; the
               project's state inherits the status fields and keeps only its own as its own.
               Those five names are reserved: a project state declaring a field (with or
               without a default), a computed var or a method of one of them is refused when the
               class is defined, with a message naming the state and the name and saying to
               rename it.
        """
        for name in ("ui_running", "ui_failed", "ui_progress", "ui_progress_message", "ui_visit", "ui_error"):
            self.assertIn(name, State.base_vars, f"{name} is a field of ui.State")
            self.assertIn(name, _Board.inherited_vars, f"{name} is inherited by a project state")
        self.assertEqual(list(_Board.base_vars), ["rows"], "a project state's own fields are only its own")
        for name in ("user", "api", "params", "can", "state"):
            self.assertIsInstance(State.__dict__[name], property, f"{name} is a property")
            self.assertNotIn(name, State.event_handlers, f"{name} must not be callable from the browser")
        self.assertIn("ui_open", State.event_handlers)
        self.assertIn("ui_cancel", State.event_handlers)

        def field_with_default():
            class Shadow(State):
                state: str = "open"

        def field_without_default():
            class Shadow(State):
                user: str

        def computed():
            class Shadow(State):
                @rx.var
                def params(self) -> str:
                    return ""

        def method():
            class Shadow(State):
                def can(self):
                    pass

        for define, name in ((field_with_default, "state"), (field_without_default, "user"),
                             (computed, "params"), (method, "can")):
            with self.assertRaises(TypeError, msg=f"a {define.__name__} named {name} is refused") as raised:
                define()
            text = str(raised.exception)
            for expected in (f"Shadow.{name}", "reserved", "Rename"):
                self.assertIn(expected, text, f"the message for {name} says {expected}")

    # -- 1.760 ---------------------------------------------------------
    def test_1_760_status_marks_follow_a_runs_life(self) -> None:
        """
        Scenario 1.760: started, progress, finished.
        Given: an empty status.
        When:  a handler starts for row 7, reports progress, and finishes -- once cleanly,
               once with a failure message; and a second run for row 8 overlaps.
        Then:  running lists the rows in flight; progress holds the fraction and the message;
               finishing removes the row and the progress, records the failure message, and a
               new start clears the previous failure; the registry lets one run per key and
               argument in, and a cancel request reaches every run of that handler.
        """
        s = _status()
        key = runs.key_of(_Board.open_ticket)
        self.assertEqual(key, "_Board.open_ticket")
        self.assertEqual((runs.arg_key((7,), {}), runs.arg_key((), {"code": "x"}), runs.arg_key((), {})), ("7", "x", ""))
        runs.mark_started(s, key, "7")
        runs.mark_started(s, key, "8")
        runs.mark_progress(s, key, results.progress(0.4, "Reading"))
        self.assertEqual((s.ui_running[key], s.ui_progress[key], s.ui_progress_message[key]), (["7", "8"], 0.4, "Reading"))
        self.assertEqual(s._ui_boots[f"{key}|7"], runs.BOOT, "each run records the process that started it")
        runs.mark_finished(s, key, "7", error="It failed.")
        self.assertEqual((s.ui_running[key], s.ui_failed[key]), (["8"], "It failed."))
        runs.mark_finished(s, key, "8")
        self.assertNotIn(key, s.ui_running, "nothing is running")
        self.assertNotIn(key, s.ui_progress, "progress goes with the run")
        runs.mark_started(s, key, "9")
        self.assertNotIn(key, s.ui_failed, "a new start clears the last failure")

        first = runs.claim("tab1", key, "7")
        self.assertIsNotNone(first)
        self.assertIsNone(runs.claim("tab1", key, "7"), "the same run cannot be claimed twice")
        self.assertIsNotNone(runs.claim("tab1", key, "8"), "another argument is another run")
        self.assertIsNotNone(runs.claim("tab2", key, "7"), "another tab is another run")
        self.assertEqual(runs.request_cancel("tab1", key), 2)
        self.assertTrue(first.cancel)
        for tab, arg in (("tab1", "7"), ("tab1", "8"), ("tab2", "7")):
            runs.release(tab, key, arg)
        self.assertIsNotNone(runs.claim("tab1", key, "7"), "released, it can run again")
        runs.release("tab1", key, "7")

    # -- 1.761 ---------------------------------------------------------
    def test_1_761_views_read_status_with_running_failed_and_progress_of(self) -> None:
        """
        Scenario 1.761: the view helpers.
        Given: a handler of a project state.
        When:  running, running for one row, failed and progress_of are built.
        Then:  each is a Reflex expression over ui.State's status fields under the handler's
               key, with a default when nothing was recorded; the per-row form compares the row
               as text.
        """
        key = runs.key_of(_Board.open_ticket)
        any_run = str(runs.running(_Board.open_ticket))
        one_row = str(runs.running(_Board.open_ticket, _Board.rows[0]["id"]))
        self.assertIn("ui_running", any_run)
        self.assertIn(key, any_run)
        self.assertIn("String(", one_row, "the row is compared as text")
        self.assertIn(".includes(", one_row)
        self.assertIn('.includes("7")', str(runs.running(_Board.open_ticket, 7)), "a literal is compared as text too")
        self.assertIn("ui_failed", str(runs.failed(_Board.open_ticket)))
        report = runs.progress_of(_Board.open_ticket)
        self.assertIn("ui_progress", str(report["fraction"]))
        self.assertIn("ui_progress_message", str(report["message"]))
