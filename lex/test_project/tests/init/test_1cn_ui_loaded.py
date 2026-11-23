"""lex.ui: a load says when it has finished, and its page decides whether it owns the error page.

Intent: a page shows a skeleton until its load has finished, so the layer has to say "finished" --
"not running" is also true before the load has started, which showed content before any data.
And a page kind starts the loads of its sections as the page's optional loads whatever the
handler declares, so where a load is registered, not its own ``optional=``, decides whether its
failure takes over the page. A regression is a page that flashes its empty content before
loading, or a failed section that replaces the whole page with the error page.

Cluster 1cn -- scenarios 1.786-1.787. Type: I.
Covers: lex/lex_app/reflex/ui/state.py (ui_loaded, ui_open), ui/runs.py (loaded),
ui/handlers.py (load).
Run: python -m lex pytest lex/test_project/tests/init/test_1cn_ui_loaded.py -v
"""

from __future__ import annotations

import asyncio
import threading
from unittest.mock import patch

import pytest
from django.test import SimpleTestCase

from lex import ui
from lex.lex_app.reflex.ui import handlers, runs, testing
from lex.lex_app.reflex.ui import state as ui_state

pytestmark = pytest.mark.init

#: Open while the test lets the required load finish; closed, the load waits, so the test can look mid-run.
GATE = threading.Event()


class LoadedDesk(ui.State):
    """One required load that succeeds, one section-style load that refuses."""

    rows: list[str] = []
    notes: list[str] = []

    @ui.load
    def open(self):
        GATE.wait(5)
        self.rows = ["a"]

    @ui.load
    def load_notes(self):
        raise ui.Refuse("Notes are not available.")


async def _open(h: testing.Harness, route: str) -> dict:
    opener = await h.send(ui.State, "ui_open", page=route)
    await opener
    for child in list(opener.children):
        await child
        for grandchild in list(child.children):
            await grandchild
    return await h.fields(ui.State)


class TestCluster01cn_Loaded(SimpleTestCase):
    """1.786-1.787: the loaded status, and the page registry deciding who owns the error page."""

    def setUp(self) -> None:
        pages = patch.dict(handlers._PAGES)
        pages.start()
        self.addCleanup(pages.stop)
        permitted = patch.object(ui_state, "_page_permitted", return_value=True)
        permitted.start()
        self.addCleanup(permitted.stop)

    # -- 1.786 ---------------------------------------------------------
    def test_1_786_a_load_says_when_it_has_finished(self) -> None:
        """
        Scenario 1.786: the loaded status.
        Given: a page whose required load succeeds.
        When:  the page is opened, and opened again.
        Then:  ui.loaded(handler) is a view value that is false until the load has finished and
               true afterwards; opening the page again puts it back to false before the load
               runs, so a revisit shows the skeleton and not the last visit's content.
        """
        handlers.register_page("/desk", [LoadedDesk.open], [], "")
        self.assertIn("ui_loaded", str(runs.loaded(LoadedDesk.open)), "ui.loaded reads the state's ui_loaded")
        self.assertIs(ui.loaded, runs.loaded, "exported as ui.loaded")

        async def scenario():
            async with testing.Harness() as h:
                await h.open_tab("/desk")
                GATE.set()
                first = await _open(h, "/desk")
                GATE.clear()                                   # the second visit's load waits
                opener = await h.send(ui.State, "ui_open", page="/desk")
                await opener
                during = await h.fields(ui.State)
                GATE.set()
                for child in list(opener.children):
                    await child
                return first, during, await h.fields(ui.State)

        self.addCleanup(GATE.set)
        first, during, second = asyncio.run(scenario())
        self.assertEqual(first["ui_loaded"], {"LoadedDesk.open": True}, "true once the load has finished")
        self.assertEqual(during["ui_loaded"], {"LoadedDesk.open": False},
                         "opening the page again puts it back to false while the load runs")
        self.assertEqual(second["ui_loaded"], {"LoadedDesk.open": True}, "and true again after the second run")

    # -- 1.787 ---------------------------------------------------------
    def test_1_787_where_a_load_is_registered_decides_whether_it_owns_the_error_page(self) -> None:
        """
        Scenario 1.787: a section's load fails in place.
        Given: a page that registers one load as required and another -- not declared
               optional= -- as optional, the way a page kind registers a section's load.
        When:  the page is opened and the second load refuses.
        Then:  its message is in ui_failed, it is not marked loaded, and neither the error page
               nor a redirect follows: the page stays, the section reports in place.
        """
        handlers.register_page("/desk", [LoadedDesk.open], [LoadedDesk.load_notes], "")

        async def scenario():
            async with testing.Harness() as h:
                await h.open_tab("/desk")
                GATE.set()
                return await _open(h, "/desk"), [r for r in h.results if isinstance(r, ui.goto("/x").__class__)]

        fields, gotos = asyncio.run(scenario())
        self.assertEqual(fields["ui_failed"], {"LoadedDesk.load_notes": "Notes are not available."},
                         "the refusal is recorded for the view")
        self.assertEqual(fields["ui_loaded"], {"LoadedDesk.open": True, "LoadedDesk.load_notes": False},
                         "a failed load is not loaded")
        self.assertEqual((fields["ui_error"], gotos), ({}, []), "no error page: the load was registered as optional")
