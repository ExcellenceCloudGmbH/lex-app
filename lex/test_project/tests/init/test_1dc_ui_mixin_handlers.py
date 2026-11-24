"""lex.ui handlers declared on a state mixin are each state's own.

Intent: a project shares a handler between states with a Reflex state mixin (an export every
section of a page offers, say). Each state that uses the mixin must keep its own running status
and its own repeat guard: an export running in one section must neither show the other sections as
busy nor make their own export be ignored. A regression is a button that stays busy, or a press
that silently does nothing, because another state is running "the same" handler.

Cluster 1dc -- scenario 1.827. Type: U.
Covers: lex/lex_app/reflex/ui/handlers.py (own_handlers), lex/lex_app/reflex/ui/state.py,
lex/lex_app/reflex/ui/rows.py.
Run: python -m lex pytest lex/test_project/tests/init/test_1dc_ui_mixin_handlers.py -v
"""

from __future__ import annotations

import asyncio
import threading

import pytest
import reflex as rx
from django.test import SimpleTestCase

from lex import ui
from lex.lex_app.reflex.ui import runs, testing

pytestmark = pytest.mark.init

GATE = threading.Event()
RAN: list = []


class SectionExport(rx.State, mixin=True):
    """A handler every section of a page offers."""

    @ui.job
    def export(self, kind: str):
        RAN.append(kind)
        GATE.wait(5)

    @ui.action
    def note(self):
        return None


class FirstSection(SectionExport, ui.State):
    @ui.action
    def own(self):
        return None


class SecondSection(SectionExport, ui.State):
    pass


class TestCluster01dc_MixinHandlers(SimpleTestCase):
    """1.827: a handler from a state mixin is its state's own."""

    # -- 1.827 ---------------------------------------------------------
    def test_1_827_a_handler_from_a_mixin_is_each_states_own(self) -> None:
        """
        Scenario 1.827: two states share an export through a mixin.
        Given: a mixin with a job and an action, used by two states, one of which has an action of
               its own.
        When:  their keys are read, and the first state's export is running when the second
               state's export is pressed with the same argument.
        Then:  each state keeps the handler under its own name, so a view of one state's running
               export does not show the other's; the second export runs too instead of being
               ignored as a repeat; a handler written on the state itself keeps its name.
        """
        self.assertEqual((runs.key_of(FirstSection.export), runs.key_of(SecondSection.export)),
                         ("FirstSection.export", "SecondSection.export"), "each state's own status")
        self.assertEqual(runs.key_of(SecondSection.note), "SecondSection.note", "an action too")
        self.assertEqual(runs.key_of(FirstSection.own), "FirstSection.own", "a handler of the state keeps its name")
        self.assertNotEqual(str(ui.running(FirstSection.export)), str(ui.running(SecondSection.export)),
                            "a view of one state's export does not show the other's")

        GATE.clear()
        RAN.clear()

        async def scenario():
            async with testing.Harness(user="ada") as h:
                await h.open_tab("/sections")
                first = await h.send(FirstSection, "export", kind="xlsx")
                for _ in range(50):
                    if RAN:
                        break
                    await asyncio.sleep(0.02)
                second = await h.send(SecondSection, "export", kind="xlsx")
                for _ in range(50):
                    if len(RAN) == 2:
                        break
                    await asyncio.sleep(0.02)
                GATE.set()
                await first
                await second

        try:
            asyncio.run(scenario())
        finally:
            GATE.set()
        self.assertEqual(RAN, ["xlsx", "xlsx"], "the second state's export ran too: it is no repeat of the first")
