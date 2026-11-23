"""lex.ui: @ui.load -- filling a page when it opens.

Intent: a page's data is loaded by plain functions that start only after sign-in and the
user's grants are known. Each visit first clears the state, so a revisit never shows the last
visit's data; a result that arrives after the user has left is discarded; a required load that
fails shows an error page with a reference, an optional one fails only its own region. A
regression is a page flashing another record's data, loading before sign-in, one broken region
taking the page down, or the browser choosing which loads run and under which permission.

Cluster 1cj -- scenarios 1.774-1.778. Type: I.
Covers: lex/lex_app/reflex/ui/handlers.py (load, page_loads, register_page, page_of, load_id),
ui/state.py (ui_open, error_page), ui/testing.py (open_tab, navigate),
lex/lex_app/reflex/shell.py (lex_page), lex/lex_app/reflex/app.py.
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
from lex.test_project.tests.init import _ui_load_twin

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

    @handlers.load
    def open_broken_too(self):
        time.sleep(0.1)
        raise RuntimeError("database still down")

    @handlers.load
    def open_late_broken(self):
        time.sleep(0.3)
        raise RuntimeError("database down late")

    @handlers.load(optional=True)
    def region_broken(self):
        raise results.Refuse("No figures for this period.")

    @rx.event
    def plain(self):
        pass


class Twin(State):
    filled: str = ""

    @handlers.load
    def open(self):
        STARTED.append("twin-here")
        self.filled = "here"


def _gotos(harness: testing.Harness) -> list[str]:
    return [r.path for r in harness.results if isinstance(r, results.Goto)]


class TestCluster01cj_Load(TestCase):
    """1.774-1.778."""

    def setUp(self) -> None:
        STARTED.clear()
        _ui_load_twin.STARTED.clear()
        pages = patch.dict(handlers._PAGES)
        pages.start()
        self.addCleanup(pages.stop)

    # -- 1.774 ---------------------------------------------------------
    def test_1_774_a_load_resets_the_state_then_fills_it(self) -> None:
        """
        Scenario 1.774: reset, keep, fill -- once per visit.
        Given: a state holding the last visit's rows and filters; a page whose required load
               fills the rows and whose optional load fills a region of the same state.
        When:  its load runs on a page with a parameter; then the page opens, and later another
               page opens whose only load is the optional one.
        Then:  the rows were emptied before the body ran and then filled from this visit's
               parameter; the kept field was not reset; the browser saw the empty rows first.
               Within one visit the optional load does not reset what the required one filled;
               the next visit's first load clears it.
        """
        result = testing.run(Page, "open", params={"section": "open"}, fields={"rows": ["stale"], "filters": "mine"})
        self.assertEqual((result.fields["rows"], result.fields["filters"]), (["row-for-open"], "mine"),
                         "the rows come from this visit's parameter; the kept filters survive the reset")
        rows_seen = [u["rows"] for u in result.updates if "rows" in u]
        self.assertEqual(rows_seen[0], [], "the last visit's rows are cleared first")
        self.assertEqual(rows_seen[-1], ["row-for-open"], "the browser ends with this visit's rows")

        handlers.register_page("/t774", [Page.open], [Page.open_region], "")
        handlers.register_page("/t774-region", [], [Page.open_region], "")

        async def scenario():
            async with testing.Harness() as h:
                await (await h.send(State, "ui_open", page="/t774"))
                await asyncio.sleep(0.3)
                same_visit = await h.fields(Page)
                await (await h.send(State, "ui_open", page="/t774-region"))
                await asyncio.sleep(0.3)
                return same_visit, await h.fields(Page)

        same_visit, next_visit = asyncio.run(scenario())
        self.assertEqual((same_visit["rows"], same_visit["region"]), (["row-for-?"], "filled"),
                         "the optional load of the same visit keeps the rows the required load filled")
        self.assertEqual((next_visit["rows"], next_visit["region"]), ([], "filled"),
                         "the next visit's first load clears the last visit's rows")

    # -- 1.775 ---------------------------------------------------------
    def test_1_775_the_opener_starts_required_loads_then_optional_ones(self) -> None:
        """
        Scenario 1.775: the order of a page's loads, from the server's page registry.
        Given: a page with a required load and an optional one, beside a plain Reflex handler,
               registered with a permission; and two same-named states in different modules,
               each on a page of its own.
        When:  the on_load list is split, and the opener runs with the page's route for a user
               who may see the page, for one who may not, and with a route nobody registered.
        Then:  the split keeps the handlers and leaves the plain one in place; the opener takes
               only the route, checks the permission the page was registered with, starts a new
               visit and the required load, then the optional one; for a user lacking the
               permission, or an unknown route, no load starts; each twin page starts its own
               state's load.
        """
        required, optional, others = handlers.page_loads([Page.open, Page.open_region, Page.plain])
        self.assertEqual((required, optional), ([Page.open], [Page.open_region]),
                         "the split names the required load and the optional one")
        self.assertEqual(others, [Page.plain], "a plain Reflex handler stays in on_load")
        self.assertEqual(list(inspect.signature(State.ui_open.fn).parameters), ["self", "page"],
                         "the opener takes the route and nothing else from the browser")
        handlers.register_page("/t775", required, optional, "app.Ticket:read")
        asked: list = []

        async def scenario(page, permitted):
            def check(state, requires):
                asked.append(requires)
                return permitted

            async with testing.Harness() as h:
                with patch.object(ui_state, "_page_permitted", side_effect=check):
                    await (await h.send(State, "ui_open", page=page))
                    await asyncio.sleep(0.4)
                return await h.fields(Page)

        fields = asyncio.run(scenario("/t775", True))
        self.assertEqual(STARTED, ["open", "region"], "the optional load starts after the required one")
        self.assertEqual((fields["region"], fields["ui_visit"]), ("filled", 1),
                         "the optional load filled its region, in the visit the opener started")
        self.assertEqual(asked, ["app.Ticket:read"], "the permission is the one the page was registered with")
        STARTED.clear()
        asyncio.run(scenario("/t775", False))
        self.assertEqual(STARTED, [], "no load starts for a user who may not see the page")
        fields = asyncio.run(scenario("/nowhere", True))
        self.assertEqual((STARTED, fields["ui_visit"]), ([], 1), "an unknown route starts a visit and no load")

        handlers.register_page("/twin-here", [Twin.open], [], "")
        handlers.register_page("/twin-other", [_ui_load_twin.Twin.open], [], "")
        self.assertNotEqual(handlers.load_id(Twin.open), handlers.load_id(_ui_load_twin.Twin.open),
                            "same-named loads in different modules have different ids")
        asyncio.run(scenario("/twin-here", True))
        asyncio.run(scenario("/twin-other", True))
        self.assertEqual((STARTED, _ui_load_twin.STARTED), (["twin-here"], ["twin-other"]),
                         "each twin page starts its own state's load")

    # -- 1.776 ---------------------------------------------------------
    def test_1_776_a_result_that_arrives_after_the_user_left_is_discarded(self) -> None:
        """
        Scenario 1.776: leaving during a load, and a cancelled load.
        Given: a load that takes 0.4 s; a page whose required load is that one, with an
               optional load after it.
        When:  the user opens another page (a new visit) while it runs; and, on the second page,
               the required load's task is cancelled while it runs.
        Then:  the load's result is not written; the cancelled load is not running, stays
               pending, and starts no optional load.
        """
        async def scenario():
            async with testing.Harness() as h:
                load = await h.send(Page, "open_slowly")
                await asyncio.sleep(0.15)
                await (await h.send(State, "ui_open", page=""))
                await load
                await asyncio.sleep(0.1)
                return (await h.fields(Page))["rows"]

        self.assertEqual(asyncio.run(scenario()), [], "a stale visit's result is discarded")

        handlers.register_page("/t776", [Page.open_slowly], [Page.open_region], "")

        async def cancelled():
            async with testing.Harness() as h:
                opener = await h.send(State, "ui_open", page="/t776")
                await opener
                await asyncio.sleep(0.1)
                for child in opener.children:
                    child.cancel()
                await asyncio.sleep(0.6)
                return await h.fields(Page), list((await h._state(State))._ui_pending_loads)

        fields, pending = asyncio.run(cancelled())
        self.assertEqual(STARTED, [], "a cancelled required load starts no optional load")
        self.assertEqual(fields["ui_running"], {}, "a cancelled load is not left running")
        self.assertEqual(pending, [handlers.load_id(Page.open_slowly)], "a cancelled required load stays pending")

    # -- 1.777 ---------------------------------------------------------
    def test_1_777_a_failed_required_load_shows_the_error_page_an_optional_one_its_region(self) -> None:
        """
        Scenario 1.777: failures of loads.
        Given: a required load that raises, and an optional one that refuses.
        When:  each runs; the required one on a page with a query string, on the error page
               itself, beside a second failing required load, and after the browser moved on.
        Then:  the required one records a reference and the page to retry (with its query
               string, never the error page), and sends the browser to the error page, without
               the exception's text; the first failure ends the visit, so a second one changes
               nothing; a failure arriving after the browser moved to another path neither
               records an error nor redirects; the optional one sends nobody anywhere and leaves
               its message for ui.failed.
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

        async def failing_on(url):
            async with testing.Harness() as h:
                await h.navigate(url)
                await (await h.send(Page, "open_broken"))
                await asyncio.sleep(0.05)
                return (await h.fields(Page))["ui_error"].get("path")

        with self.assertLogs("lex.lex_app.reflex.ui.handlers", level="ERROR"):
            self.assertEqual(asyncio.run(failing_on("/orders?id=7")), "/orders?id=7",
                             "Try again returns to the page with its query string")
            self.assertNotEqual(asyncio.run(failing_on(ui_state.ERROR_ROUTE)), ui_state.ERROR_ROUTE,
                                "the error page is never the page to return to")

        handlers.register_page("/t777", [Page.open_broken, Page.open_broken_too], [], "")

        async def two_failures():
            async with testing.Harness() as h:
                await h.navigate("/t777")
                await (await h.send(State, "ui_open", page="/t777"))
                await asyncio.sleep(0.4)
                return _gotos(h), await h.fields(Page)

        with self.assertLogs("lex.lex_app.reflex.ui.handlers", level="ERROR"):
            gotos, fields = asyncio.run(two_failures())
        self.assertEqual(gotos, [ui_state.ERROR_ROUTE], "the second failure of the visit sends nobody anywhere")
        self.assertEqual(fields["ui_visit"], 2, "the first failure ended the visit the opener started")
        self.assertNotIn(runs.key_of(Page.open_broken_too), fields["ui_failed"],
                         "the second failure of an ended visit records nothing")

        async def moved_on():
            async with testing.Harness() as h:
                await h.navigate("/a")
                load = await h.send(Page, "open_late_broken")
                await asyncio.sleep(0.1)
                await h.navigate("/b")
                await load
                await asyncio.sleep(0.4)
                return _gotos(h), await h.fields(Page)

        with self.assertLogs("lex.lex_app.reflex.ui.handlers", level="ERROR"):
            gotos, fields = asyncio.run(moved_on())
        self.assertEqual((gotos, fields["ui_error"]), ([], {}),
                         "a failure after the browser moved on neither redirects nor records an error")

    # -- 1.778 ---------------------------------------------------------
    def test_1_778_lex_page_adds_the_opener_and_the_error_page_exists(self) -> None:
        """
        Scenario 1.778: wiring.
        Given: lex_page with a ui load, the error page, and create_app.
        When:  a page is registered, the error page is built, and create_app's preparation runs.
        Then:  the page's on_load is the access load, any plain handler, then the opener
               carrying only the page's route -- the load and the permission are in the
               server's page registry, not in the event; the error page shows the message and
               a way to try again; create_app loads rfds.toast (whose first import defines
               Reflex states) before it adds a page, so it is never first imported in the
               middle of an event.
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
        self.assertIn("/tickets", opener, "the opener carries the page's route")
        self.assertNotIn(runs.key_of(Page.open), opener, "the browser is not told which loads to start")
        self.assertNotIn("app.Ticket:read", opener, "the browser is not told the page's permission")
        self.assertEqual(handlers.page_of("/tickets"), handlers.PageLoads((Page.open,), (), "app.Ticket:read"),
                         "lex_page records the page's loads and permission on the server")
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
