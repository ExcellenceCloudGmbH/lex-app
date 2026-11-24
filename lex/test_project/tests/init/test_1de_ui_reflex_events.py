"""lex.ui handlers may send a Reflex event as it is.

Intent: a project moving to lex.ui keeps raw Reflex states for a while, and a converted handler
still has to start another state's handler (a table's refresh, a load after a choice) or run a
small browser script (an announcement for screen readers). Without that, each such handler
stayed a raw async handler, with everything lex.ui removes. A regression is a converted handler
that cannot chain to the handler it used to start, or a result order the browser does not keep.

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

    key: str = ""

    @rx.event
    def reload(self):
        pass

    @rx.event
    def open(self, key: str):
        self.key = key


class TestCluster01de_ReflexEvents(SimpleTestCase):
    """1.829: a Reflex event among a handler's results."""

    # -- 1.829 ---------------------------------------------------------
    def test_1_829_a_handler_sends_a_reflex_event_after_its_results(self) -> None:
        """
        Scenario 1.829: a Reflex event as a result.
        Given: a handler's return value with a toast, another state's handler, that handler with
               an argument, and a browser script.
        When:  it is split as a handler's result and turned into browser events.
        Then:  the Reflex events are results like the others, in the order written; they reach
               the browser as they are, after the toast before them; a plain object is still
               refused with the handler's name and with what a handler may return.
        """
        script = rx.call_script("document.title = 'Saved'")
        out, report = results.split([ui.toast("Saved"), RawTable.reload, RawTable.open("7"), script],
                                    handler="Desk.save")
        self.assertIsNone(report)
        self.assertEqual(out[0], results.Toast("Saved"), "the toast first, as written")
        self.assertIsInstance(out[1], EventHandler, "another state's handler")
        self.assertIsInstance(out[2], EventSpec, "the same with an argument")
        self.assertIs(out[3], script, "a browser script")
        with patch.object(results, "RECORDER", None):
            events = results.to_events(out)
        self.assertEqual(len(events), 4, "one browser event per result")
        self.assertIn("Saved", str(events[0]), "the toast is RFDS's toast")
        self.assertEqual(events[1:], out[1:], "the Reflex events reach the browser as they are, in order")
        with self.assertRaises(TypeError) as wrong:
            results.split(object(), handler="Desk.save")
        self.assertIn("Desk.save", str(wrong.exception))
        self.assertIn("a Reflex event", str(wrong.exception), "the message says what a handler may return")
