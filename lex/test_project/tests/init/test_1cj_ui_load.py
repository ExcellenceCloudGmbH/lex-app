"""lex.ui: @ui.load -- filling a page when it opens.

Intent: a page's data is loaded by plain functions that start only after sign-in and the
user's grants are known. Each load first clears the state, so a revisit never shows the last
visit's data; a result that arrives after the user has left is discarded; a required load that
fails shows an error page with a reference, an optional one fails only its own region. A
regression is a page flashing another record's data, loading before sign-in, or one broken
region taking the page down.

Cluster 1cj -- scenarios 1.774-1.778. Type: I.
Covers: lex/lex_app/reflex/ui/handlers.py (load, page_loads, registered), ui/state.py (ui_open,
error_page), lex/lex_app/reflex/shell.py (lex_page), lex/lex_app/reflex/app.py.
Run: python -m lex pytest lex/test_project/tests/init/test_1cj_ui_load.py -v
"""

from __future__ import annotations

import asyncio
import inspect
import subprocess
import sys
import time
from unittest import TestCase
from unittest.mock import patch

import pytest
import reflex as rx

from lex.lex_app.reflex import app as lex_app_module
from lex.lex_app.reflex import shell
from lex.lex_app.reflex.ui import handlers, results, runs, testing
from lex.lex_app.reflex.ui import state as ui_state
from lex.lex_app.reflex.ui.state import State

pytestmark = pytest.mark.init

STARTED: list = []


class Page(State):
    rows: list[str] = []
    filters: str = ""
    region: str = ""

    @handlers.load(keep=("filters",))
    def open(self):
        STARTED.append("open")
        self.rows = [f"row-for-{self.params.get('section', '?')}"]

    @handlers.load(optional=True)
    def open_region(self):
        STARTED.append("region")
        self.region = "filled"

    @handlers.load
    def open_slowly(self):
        time.sleep(0.4)
        self.rows = ["late"]

    @handlers.load
    def open_broken(self):
        raise RuntimeError("database down")

    @handlers.load(optional=True)
    def region_broken(self):
        raise results.Refuse("No figures for this period.")

    @rx.event
    def plain(self):
        pass


class TestCluster01cj_Load(TestCase):
    """1.774-1.778."""

    def setUp(self) -> None:
        STARTED.clear()

    # -- 1.774 ---------------------------------------------------------
    def test_1_774_a_load_resets_the_state_then_fills_it(self) -> None:
        """
        Scenario 1.774: reset, keep, fill.
        Given: a state holding the last visit's rows and filters.
        When:  its load runs on a page with a parameter.
        Then:  the rows were emptied before the body ran and then filled from this visit's
               parameter; the kept field was not reset; the browser saw the empty rows first.
        """
        result = testing.run(Page, "open", params={"section": "open"}, fields={"rows": ["stale"], "filters": "mine"})
        self.assertEqual((result.fields["rows"], result.fields["filters"]), (["row-for-open"], "mine"),
                         "the rows come from this visit's parameter; the kept filters survive the reset")
        rows_seen = [u["rows"] for u in result.updates if "rows" in u]
        self.assertEqual(rows_seen[0], [], "the last visit's rows are cleared first")
        self.assertEqual(rows_seen[-1], ["row-for-open"], "the browser ends with this visit's rows")

    # -- 1.775 ---------------------------------------------------------
    def test_1_775_the_opener_starts_required_loads_then_optional_ones(self) -> None:
        """
        Scenario 1.775: the order of a page's loads.
        Given: a page with a required load and an optional one, registered through lex_page's
               on_load, beside a plain Reflex handler.
        When:  the on_load list is split, and the opener runs for a user who may see the page
               and for one who may not.
        Then:  the split names the required and the optional load and leaves the plain handler
               in place; the opener starts a new visit and the required load, and the optional
               load starts after the required one succeeded; for a user lacking the page's
               permission no load starts.
        """
        required, optional, others = handlers.page_loads([Page.open, Page.open_region, Page.plain])
        self.assertEqual((required, optional), ([runs.key_of(Page.open)], [runs.key_of(Page.open_region)]),
                         "the split names the required load and the optional one")
        self.assertEqual(others, [Page.plain], "a plain Reflex handler stays in on_load")
        self.assertIs(handlers.registered(required[0]), Page.open, "the opener finds the load by its name")

        async def scenario(requires):
            async with testing.Harness() as h:
                with patch.object(ui_state, "_page_permitted", return_value=requires != "missing.Permission:read"):
                    await (await h.send(State, "ui_open", required=required, optional=optional, requires=requires))
                    await asyncio.sleep(0.4)
                return await h.fields(Page)

        fields = asyncio.run(scenario(""))
        self.assertEqual(STARTED, ["open", "region"], "the optional load starts after the required one")
        self.assertEqual((fields["region"], fields["ui_visit"]), ("filled", 1),
                         "the optional load filled its region, in the visit the opener started")
        STARTED.clear()
        asyncio.run(scenario("missing.Permission:read"))
        self.assertEqual(STARTED, [], "no load starts for a user who may not see the page")

    # -- 1.776 ---------------------------------------------------------
    def test_1_776_a_result_that_arrives_after_the_user_left_is_discarded(self) -> None:
        """
        Scenario 1.776: leaving during a load.
        Given: a load that takes 0.4 s.
        When:  the user opens another page (a new visit) while it runs.
        Then:  the load's result is not written.
        """
        async def scenario():
            async with testing.Harness() as h:
                load = await h.send(Page, "open_slowly")
                await asyncio.sleep(0.15)
                await (await h.send(State, "ui_open", required=[], optional=[], requires=""))
                await load
                await asyncio.sleep(0.1)
                return (await h.fields(Page))["rows"]

        self.assertEqual(asyncio.run(scenario()), [], "a stale visit's result is discarded")

    # -- 1.777 ---------------------------------------------------------
    def test_1_777_a_failed_required_load_shows_the_error_page_an_optional_one_its_region(self) -> None:
        """
        Scenario 1.777: failures of loads.
        Given: a required load that raises, and an optional one that refuses.
        When:  each runs.
        Then:  the required one records a reference and the page to retry, and sends the browser
               to the error page, without the exception's text; the optional one sends nobody
               anywhere and leaves its message for ui.failed.
        """
        with self.assertLogs("lex.lex_app.reflex.ui.handlers", level="ERROR"):
            broken = testing.run(Page, "open_broken")
        self.assertEqual(broken.goto, ui_state.ERROR_ROUTE, "a failed required load shows the error page")
        error = broken.fields["ui_error"]
        self.assertEqual(error["kind"], "error", "the error page explains a failure, not a refusal")
        self.assertIn("Reference", error["message"], "the user gets a reference to quote")
        self.assertNotIn("database down", error["message"], "the exception's text stays in the log")
        region = testing.run(Page, "region_broken")
        self.assertIsNone(region.goto, "an optional load never leaves the page")
        self.assertEqual(region.failed[runs.key_of(Page.region_broken)], "No figures for this period.",
                         "the optional load's message is left for ui.failed")

    # -- 1.778 ---------------------------------------------------------
    def test_1_778_lex_page_adds_the_opener_and_the_error_page_exists(self) -> None:
        """
        Scenario 1.778: wiring.
        Given: lex_page with a ui load, the error page, and create_app.
        When:  a page is registered, the error page is built, and create_app's preparation runs.
        Then:  the page's on_load is the access load, any plain handler, then the opener
               carrying the load's name and the page's permission -- the load itself is not in
               on_load; the error page shows the message and a way to try again; create_app
               loads rfds.toast (whose first import defines Reflex states) before it adds a
               page, so it is never first imported in the middle of an event.
        """
        captured = {}

        def fake_page(**kwargs):
            captured.update(kwargs)
            return lambda fn: fn

        with patch("reflex.page", fake_page), patch.object(shell, "_NAV", []):
            shell.lex_page("/tickets", title="Tickets", requires="app.Ticket:read",
                           on_load=[Page.open, Page.plain])(lambda: rx.text("x"))
        loads = captured["on_load"]
        self.assertEqual(len(loads), 3, "access load, the plain handler, the opener")
        self.assertIs(loads[1], Page.plain, "a plain handler keeps its place after the access load")
        opener = str(loads[2])
        self.assertIn("ui_open", opener, "the opener is the page's last load event")
        self.assertIn(runs.key_of(Page.open), opener, "the opener carries the load's name")
        self.assertIn("app.Ticket:read", opener, "the opener carries the page's permission")
        page = str(ui_state.error_page())
        self.assertIn("ui_error", page, "the error page shows what the failed load recorded")
        self.assertIn("Try again", page, "the error page offers a way back")

        probe = (
            "import sys\n"
            "from lex.lex_app.reflex import app\n"
            "module = 'rfds.components.feedback.toast'\n"
            "before = module in sys.modules\n"
            "app._load_lazy_states()\n"
            "print(before, module in sys.modules)\n"
        )
        out = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True, check=True)
        self.assertEqual(out.stdout.split(), ["False", "True"], "the helper loads rfds.toast, which was not loaded")
        source = inspect.getsource(lex_app_module.create_app)
        self.assertIn("_load_lazy_states()", source, "create_app loads the lazily defined states")
        self.assertLess(source.index("_load_lazy_states()"), source.index("app.add_page("),
                        "the states are loaded before the app's pages are added")
