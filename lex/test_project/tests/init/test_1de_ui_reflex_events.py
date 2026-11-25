"""lex.ui handlers may hand on to another state's handler.

Intent: when a handler has done its work, another state often has to act: read again, open
what was just made. A handler returns that state's handler among its results and the browser
runs it in order. Reflex's own raw events (a browser script, a redirect, a download) are not
accepted: they stay in the custom region, where a review sees them, and toasts, gotos and
downloads have lex.ui results of their own. A regression is a handler that cannot chain to
another state, a result order the browser does not keep, or a browser script slipping out of
an ordinary handler.

Cluster 1de -- scenario 1.829. Type: U.
Covers: lex/lex_app/reflex/ui/results.py (split, to_events).
Run: python -m lex pytest lex/test_project/tests/init/test_1de_ui_reflex_events.py -v
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
import reflex as rx
from django.test import SimpleTestCase
from reflex.event import EventHandler, EventSpec

from lex import ui
from lex.lex_app.reflex.ui import results

pytestmark = pytest.mark.init


class RawTable(rx.State):
    """A raw Reflex state a converted handler starts."""

    opened: str = ""

    @rx.event
    def reload(self):
        pass

    @rx.event
    def open(self, key: str):
        self.opened = key


class Orders(ui.State):
    """A lex.ui state whose handlers try to hand on to the wrong things."""

    @ui.rows
    def rows(self):
        return []

    @ui.action
    def save(self):
        pass


class TestCluster01de_ReflexEvents(SimpleTestCase):
    """1.829: another state's handler among a handler's results."""

    # -- 1.829 ---------------------------------------------------------
    def test_1_829_a_handler_hands_on_to_another_states_handler(self) -> None:
        """
        Scenario 1.829: another state's handler as a result.
        Given: a handler's return value with a toast, another state's handler, and that handler
               with an argument; and a browser script, a redirect and a plain object.
        When:  they are split as a handler's result and turned into browser events.
        Then:  the other state's handlers are results like the toast, in the order written, and
               reach the browser as they are; the browser script, the redirect and the plain
               object are refused with the handler's name and with where raw events go.
        """
        out, report = results.split([ui.toast("Saved"), RawTable.reload, RawTable.open("7")], handler="Orders.save")
        self.assertIsNone(report)
        self.assertEqual(out[0], results.Toast("Saved"), "the toast first, as written")
        self.assertIsInstance(out[1], EventHandler, "another state's handler")
        self.assertIsInstance(out[2], EventSpec, "the same with an argument")
        with patch.object(results, "RECORDER", None):
            events = results.to_events(out)
        self.assertEqual(len(events), 3, "one browser event per result")
        self.assertIn("Saved", str(events[0]), "the toast is RFDS's toast")
        self.assertEqual(events[1:], out[1:], "the handlers reach the browser as they are, in order")
        for raw in (rx.call_script("document.title = 'Saved'"), rx.redirect("/orders"), object()):
            with self.assertRaises(TypeError) as wrong:
                results.split(raw, handler="Orders.save")
            self.assertIn("Orders.save", str(wrong.exception))
            self.assertIn("@ui.custom", str(wrong.exception), "the message says where raw events go")

    def test_1_829_a_rows_handler_is_refused_and_a_handler_of_the_same_state_is_handed_on(self) -> None:
        """
        Scenario 1.829: what may be handed on.
        Given: a @ui.rows handler of another state, and a handler of the state that is running.
        When:  they are split as a result of the running state's handler.
        Then:  the rows handler is refused when the handler returns, not later in the browser, with
               the advice to return ui.refresh() for it; a handler of the same state is handed on
               like any other (an action that starts its own state's job, a table that refreshes
               itself): it runs as the next event, after this one has finished.
        """
        own = Orders.get_full_name()
        with self.assertRaises(TypeError) as table:
            results.split(Orders.rows, handler="Other.save", state=RawTable.get_full_name())
        self.assertIn("@ui.rows", str(table.exception))
        self.assertIn("ui.refresh(Orders.rows)", str(table.exception), "the message says what to write")
        out, _ = results.split(Orders.save, handler="Orders.close", state=own)
        self.assertEqual(len(out), 1, "a handler of the same state is handed on")
        out, _ = results.split(RawTable.reload, handler="Orders.close", state=own)
        self.assertEqual(len(out), 1, "another state's handler still passes")
