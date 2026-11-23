"""lex.ui: @ui.load -- filling a page when it opens.

Intent: a page's data is loaded by plain functions that start only after sign-in and the
user's grants are known. Each visit first clears the state, so a revisit never shows the last
visit's data; a result that arrives after the user has left is discarded; a required load that
fails shows an error page with a reference, an optional one fails only its own region. A
regression is a page flashing another record's data, loading before sign-in, one broken region
taking the page down, or the browser choosing which loads run and under which permission.
A load started by a component's on_mount (a dashboard) belongs to its component: only a newer
run of it is stale, and its failure stays in place.

Cluster 1cj -- scenarios 1.774-1.778. Type: I.
Covers: lex/lex_app/reflex/ui/handlers.py (load, page_loads, register_page, page_of, load_id),
ui/state.py (ui_open, error_page), ui/testing.py (open_tab, navigate, mount),
lex/lex_app/reflex/shell.py (lex_page), lex/lex_app/reflex/app.py.
Run: python -m lex pytest lex/test_project/tests/init/test_1cj_ui_load.py -v
"""

from __future__ import annotations

import asyncio
import inspect
import subprocess
import sys
import threading
import time
from unittest import TestCase
from unittest.mock import patch

import pytest
import reflex as rx

from lex.lex_app.reflex import app as lex_app_module
from lex.lex_app.reflex import shell
from lex.lex_app.reflex.ui import guard, handlers, results, runs, testing
from lex.lex_app.reflex.ui import state as ui_state
from lex.lex_app.reflex.ui.state import State
from lex.test_project.tests.init import _ui_load_twin
from lex.test_project.tests.init._ui_harness_aids import lock_held, lock_waiters, unstored_on_error

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
    def open_when_released(self):
        if not GATES["slow"].wait(5):
            raise RuntimeError("gate slow was never released")
        self.rows = ["late"]

    @handlers.load
    def open_broken(self):
        raise RuntimeError("database down")

    @handlers.load
    def open_broken_too(self):
        if not GATES["second"].wait(5):
            raise RuntimeError("gate second was never released")
        raise RuntimeError("database still down")

    @handlers.load
    def open_late_broken(self):
        if not GATES["late"].wait(5):
            raise RuntimeError("gate late was never released")
        raise RuntimeError("database down late")

    @handlers.load(optional=True)
    def region_broken(self):
        raise results.Refuse("No figures for this period.")

    @handlers.load
    def open_broken_when_released(self):
        if not GATES["body"].wait(5):
            raise RuntimeError("gate body was never released")
        raise RuntimeError("database down")

    @rx.event
    def plain(self):
        pass

    @rx.event
    async def hold(self):
        """A foreground handler: holds the user's lock until the test releases it."""
        await asyncio.to_thread(GATES["hold"].wait, 5)


class Twin(State):
    filled: str = ""

    @handlers.load
    def open(self):
        STARTED.append("twin-here")
        self.filled = "here"


#: Gate name -> the event a Dash.fill run waits on; the test releases it.
GATES: dict[str, threading.Event] = {}


def _gate(name: str) -> str:
    GATES[name] = threading.Event()
    return name


class Dash(State):
    """A dashboard's state: no lex_page, its load is started by a component's on_mount."""

    rows: list[str] = []
    extra: str = ""

    @handlers.load
    def fill(self, tag: str = "", gate: str = ""):
        if gate and not GATES[gate].wait(5):
            raise RuntimeError(f"gate {gate} was never released")
        self.rows = [tag]
        if tag == "first":
            self.extra = "first"

    @handlers.load
    def fill_broken(self, gate: str = ""):
        if gate and not GATES[gate].wait(5):
            raise RuntimeError(f"gate {gate} was never released")
        raise RuntimeError("dashboard source down")

    @handlers.load(requires=guard.permission("app.Thing", "read"))
    def fill_guarded(self):
        self.rows = ["secret"]


def _gotos(harness: testing.Harness) -> list[str]:
    return [r.path for r in harness.results if isinstance(r, results.Goto)]


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


async def _nothing_running(harness: testing.Harness) -> bool:
    return (await harness.fields(State))["ui_running"] == {}


async def _running_mark(harness: testing.Harness, handler) -> dict:
    """Wait until ``handler`` is marked running; fails loudly after 5 s."""
    key = runs.key_of(handler)
    deadline = time.monotonic() + 5
    while key not in (fields := await harness.fields(State))["ui_running"]:
        if time.monotonic() > deadline:
            raise AssertionError(f"{key} was never marked running")
        await asyncio.sleep(0.001)
    return fields["ui_running"]


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
                await (await h.send(State, "ui_open", page="/t774")).wait_all()     # the loads it started too
                same_visit = await h.fields(Page)
                await (await h.send(State, "ui_open", page="/t774-region")).wait_all()
                return same_visit, await h.fields(Page)

        same_visit, next_visit = asyncio.run(scenario())
        self.assertEqual((same_visit["rows"], same_visit["region"]), (["row-for-?"], "filled"),
                         "the optional load of the same visit keeps the rows the required load filled")
        self.assertEqual((next_visit["rows"], next_visit["region"]), ([], "filled"),
                         "the next visit's first load clears the last visit's rows")

    def test_1_774_a_load_started_on_mount_lands_after_the_opener(self) -> None:
        """
        Scenario 1.774: a dashboard's load, started by a component's on_mount.
        Given: a dashboard on / whose load is started on mount, so its event reaches the server
               before the page's opener (Reflex's real order).
        When:  the page is viewed, then viewed again on the same URL with a run that sets fewer
               fields.
        Then:  the first run's data is in the state after the opener has run, and nothing is left
               running; the second run reset the state when it started (the field it does not set
               is back to its default) and its data lands.
        """
        async def scenario():
            async with testing.Harness() as h:
                first = await h.mount(Dash, "fill", url="/", tag="first", gate=_gate("first"))
                GATES["first"].set()                   # the body finishes after the opener has run
                await first
                after_first = await h.fields(Dash)
                second = await h.mount(Dash, "fill", url="/", tag="second", gate=_gate("second"))
                at_start = await h.fields(Dash)        # the second run is held at its gate
                GATES["second"].set()
                await second
                return after_first, at_start, await h.fields(Dash)

        after_first, at_start, after_second = asyncio.run(scenario())
        self.assertEqual((after_first["rows"], after_first["extra"]), (["first"], "first"),
                         "a load started on mount, before the page's opener, lands its data")
        self.assertEqual(after_first["ui_running"], {}, "the load started on mount is not left running")
        self.assertEqual((at_start["rows"], at_start["extra"]), ([], ""),
                         "the second view's run reset the state when it started")
        self.assertEqual((after_second["rows"], after_second["extra"]), (["second"], ""),
                         "the second run's data lands; the field it does not set stays at its default")

    def test_1_774_a_load_started_on_mount_lands_after_a_navigation(self) -> None:
        """
        Scenario 1.774: a dashboard reached by a link from another page.
        Given: a tab on /tickets; the user follows a link to the dashboard on /.
        When:  the dashboard's load is started on mount -- its event still carries /tickets, as
               Reflex's client sends it on a navigation -- then the browser's events from / and
               the page's opener follow, and the load finishes after them.
        Then:  the load's data lands and nothing is left running.
        """
        async def scenario():
            async with testing.Harness() as h:
                await h.navigate("/tickets")
                load = await h.mount(Dash, "fill", url="/", sent_from="/tickets", tag="home", gate=_gate("home"))
                GATES["home"].set()
                await load
                return await h.fields(Dash)

        fields = asyncio.run(scenario())
        self.assertEqual((fields["rows"], fields["ui_running"]), (["home"], {}),
                         "a load started on mount whose event carried the previous page still lands")

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
                    await (await h.send(State, "ui_open", page=page)).wait_all()   # the loads it started too
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

    def test_1_775_a_load_started_on_mount_starts_no_optional_load(self) -> None:
        """
        Scenario 1.775: a load started on mount stays out of the page's load bookkeeping.
        Given: a page whose required load is still pending (its opener-started run was
               cancelled), with an optional load waiting for it.
        When:  the same load is sent again by the browser, as a component's on_mount sends it,
               and succeeds.
        Then:  no optional load starts, and the opener's required load is still pending.
        """
        handlers.register_page("/t775-mount", [Page.open_when_released], [Page.open_region], "")

        async def scenario():
            async with testing.Harness() as h:
                opener = await h.send(State, "ui_open", page="/t775-mount")
                await opener
                await _running_mark(h, Page.open_when_released)
                for child in opener.children:
                    child.cancel()
                GATES["slow"].set()
                await _until(lambda: _nothing_running(h), "the cancelled load was left running")
                await (await h.send(Page, "open_when_released")).wait_all()   # and whatever it started
                return await h.fields(Page), list((await h._state(State))._ui_pending_loads)

        _gate("slow")

        fields, pending = asyncio.run(scenario())
        self.assertEqual(fields["rows"], ["late"], "the load started on mount filled the state")
        self.assertEqual(STARTED, [], "a load started on mount starts no optional load of the page")
        self.assertEqual(pending, [handlers.load_id(Page.open_when_released)],
                         "a load started on mount does not settle the opener's pending load")

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
        handlers.register_page("/t776-slow", [Page.open_when_released], [], "")

        async def scenario():
            async with testing.Harness() as h:
                opener = await h.send(State, "ui_open", page="/t776-slow")
                await opener
                in_flight = await _running_mark(h, Page.open_when_released)
                await (await h.send(State, "ui_open", page=""))
                GATES["slow"].set()                    # the body finishes after the visit changed
                await opener.wait_all()
                return in_flight, (await h.fields(Page))["rows"]

        _gate("slow")
        in_flight, rows = asyncio.run(scenario())
        self.assertIn(runs.key_of(Page.open_when_released), in_flight,
                      "the opener started the load, and it was running when the visit changed")
        self.assertEqual(rows, [], "a stale visit's result is discarded")

        handlers.register_page("/t776", [Page.open_when_released], [Page.open_region], "")

        async def cancelled():
            async with testing.Harness() as h:
                opener = await h.send(State, "ui_open", page="/t776")
                await opener
                await _running_mark(h, Page.open_when_released)
                for child in opener.children:
                    child.cancel()
                GATES["slow"].set()                    # the body ends; the wrapper winds up after it
                await _until(lambda: _nothing_running(h), "the cancelled load was left running")
                return await h.fields(Page), list((await h._state(State))._ui_pending_loads)

        _gate("slow")
        fields, pending = asyncio.run(cancelled())
        self.assertEqual(STARTED, [], "a cancelled required load starts no optional load")
        self.assertEqual(fields["ui_running"], {}, "a cancelled load is not left running")
        self.assertEqual(pending, [handlers.load_id(Page.open_when_released)], "a cancelled required load stays pending")

    def test_1_776_a_load_cancelled_while_it_winds_up_is_not_left_running(self) -> None:
        """
        Scenario 1.776: the next navigation cancels a load at the worst moment.
        Given: a page whose required load has failed and waits for the user's lock to wind up,
               the lock being held by a foreground handler.
        When:  the load's task is cancelled (Reflex cancels the previous page's loads at every
               navigation), then the foreground handler finishes.
        Then:  the wind-up still runs: the load is not left running.
        """
        handlers.register_page("/t776-wind", [Page.open_broken_when_released], [], "")

        async def scenario():
            async with testing.Harness() as h:
                await h.navigate("/t776-wind")
                opener = await h.send(State, "ui_open", page="/t776-wind")
                await opener
                await _running_mark(h, Page.open_broken_when_released)
                holder = await h.send(Page, "hold")
                await _until(lambda: lock_held(h), "the foreground handler never took the lock")
                GATES["body"].set()
                await _until(lambda: h.outcomes and lock_waiters(h),
                             "the failed load never waited for the lock to wind up")
                for child in opener.children:
                    child.cancel()
                GATES["hold"].set()
                await holder
                await _until(lambda: _nothing_running(h), "the cancelled load was left running")
                return await h.fields(Page)

        _gate("body")
        _gate("hold")
        with self.assertLogs("lex.lex_app.reflex.ui.handlers", level="ERROR"):
            fields = asyncio.run(scenario())
        self.assertEqual(fields["ui_running"], {}, "the cancelled load is not left running")

    def test_1_776_a_load_started_on_mount_is_dropped_only_when_a_newer_run_starts(self) -> None:
        """
        Scenario 1.776: when a load started on mount is stale.
        Given: a dashboard load started on mount (no opener lists it).
        When:  a newer run of the same load starts while it runs -- the older one finishing
               last, and the older one finishing first; two runs with the same argument overlap;
               and, separately, the browser moves to another location while a run is in flight.
        Then:  the older run's data never lands and the newer run's data stays; while the newer
               run is in flight its running mark keeps its process record, whatever the older
               one did; a run whose browser moved on still fills its own state (it belongs to
               its component, not to the page view); nothing is left running.
        """
        async def newer(older_first):
            async with testing.Harness() as h:
                older = await h.mount(Dash, "fill", tag="older", gate=_gate("older"))
                recent = await h.mount(Dash, "fill", tag="newer", gate=_gate("newer"))
                seen = []
                for name, future in ((("older", older), ("newer", recent)) if older_first
                                     else (("newer", recent), ("older", older))):
                    GATES[name].set()
                    await future
                    seen.append(list((await h.fields(Dash))["rows"]))
                return seen, await h.fields(Dash)

        seen, fields = asyncio.run(newer(older_first=False))
        self.assertEqual(seen, [["newer"], ["newer"]], "the older run, finishing last, never lands")
        seen, fields = asyncio.run(newer(older_first=True))
        self.assertEqual(seen, [[], ["newer"]],
                         "the older run, finishing while the newer one runs, never lands; the newer one does")
        self.assertEqual(fields["ui_running"], {}, "neither run is left running")

        async def same_argument():
            async with testing.Harness() as h:
                older = await h.mount(Dash, "fill", tag="same", gate=_gate("same-older"))
                recent = await h.mount(Dash, "fill", tag="same", gate=_gate("same-newer"))
                GATES["same-older"].set()
                await older
                mid = (await h.fields(Dash))["ui_running"], dict((await h._state(State))._ui_boots)
                GATES["same-newer"].set()
                await recent
                return mid, (await h.fields(Dash))["ui_running"], dict((await h._state(State))._ui_boots)

        (mid_running, mid_boots), running, boots = asyncio.run(same_argument())
        key = runs.key_of(Dash.fill)
        self.assertEqual(mid_running, {key: ["same"]}, "the newer run is still marked running")
        self.assertIn(f"{key}|same", mid_boots,
                      "the newer run keeps its process record, so a sweep can still clear it")
        self.assertEqual((running, boots), ({}, {}), "once both have finished, neither mark nor record is left")

        async def moved():
            async with testing.Harness() as h:
                load = await h.mount(Dash, "fill", url="/a", tag="left", gate=_gate("left"))
                await h.navigate("/b")
                GATES["left"].set()
                await load
                return await h.fields(Dash)

        fields = asyncio.run(moved())
        self.assertEqual((fields["rows"], fields["ui_running"]), (["left"], {}),
                         "a run whose browser moved on still fills its own state and is not left running")

    # -- 1.777 ---------------------------------------------------------
    def test_1_777_a_failed_required_load_shows_the_error_page_an_optional_one_its_region(self) -> None:
        """
        Scenario 1.777: failures of loads.
        Given: a required load that raises, declared on a page, and an optional one that refuses.
        When:  each runs; the required one, started by its page's opener, on a page with a query
               string, on the error page itself, beside a second failing required load, and after
               the browser moved on.
        Then:  the required one records a reference and the page to retry (with its query
               string, never the error page), and sends the browser to the error page, without
               the exception's text; the first failure ends the visit, so a second one changes
               nothing; a failure arriving after the browser moved to another path neither
               records an error nor redirects; the optional one sends nobody anywhere and leaves
               its message for ui.failed. "Try again" only ever returns to a path of this site
               (one leading slash, no backslash), else to /.
        """
        handlers.register_page("/t777-broken", [Page.open_broken], [], "")
        handlers.register_page("/orders", [Page.open_broken], [], "")
        handlers.register_page(ui_state.ERROR_ROUTE, [Page.open_broken], [], "")

        async def failing_on(url, route):
            async with testing.Harness() as h:
                await h.navigate(url)
                await (await h.send(State, "ui_open", page=route)).wait_all()   # the load it started too
                return _gotos(h), await h.fields(Page)

        with self.assertLogs("lex.lex_app.reflex.ui.handlers", level="ERROR"):
            gotos, fields = asyncio.run(failing_on("/t777-broken", "/t777-broken"))
        self.assertEqual(gotos, [ui_state.ERROR_ROUTE], "a failed required load shows the error page")
        error = fields["ui_error"]
        self.assertEqual(error["kind"], "error", "the error page explains a failure, not a refusal")
        self.assertIn("Reference", error["message"], "the user gets a reference to quote")
        self.assertNotIn("database down", error["message"], "the exception's text stays in the log")
        for location, back in (("/orders?id=7", "/orders?id=7"), ("//evil.example/x", "/"),
                               ("/\\evil.example", "/"), ("https://evil.example", "/"), ("", "/"),
                               (ui_state.ERROR_ROUTE + "?x=1", "/")):
            self.assertEqual(handlers._return_path(location), back,
                             f"Try again from {location!r} goes to {back!r}: only a path of this site")
        region = testing.run(Page, "region_broken")
        self.assertIsNone(region.goto, "an optional load never leaves the page")
        self.assertEqual(region.failed[runs.key_of(Page.region_broken)], "No figures for this period.",
                         "the optional load's message is left for ui.failed")

        with self.assertLogs("lex.lex_app.reflex.ui.handlers", level="ERROR"):
            _, fields = asyncio.run(failing_on("/orders?id=7", "/orders"))
            self.assertEqual(fields["ui_error"].get("path"), "/orders?id=7",
                             "Try again returns to the page with its query string")
            _, fields = asyncio.run(failing_on(ui_state.ERROR_ROUTE, ui_state.ERROR_ROUTE))
            self.assertNotEqual(fields["ui_error"].get("path"), ui_state.ERROR_ROUTE,
                                "the error page is never the page to return to")

        handlers.register_page("/t777", [Page.open_broken, Page.open_broken_too], [], "")

        async def two_failures():
            async with testing.Harness() as h:
                await h.navigate("/t777")
                opener = await h.send(State, "ui_open", page="/t777")
                await opener
                await _until(lambda: _gotos(h), "the first failure never sent the browser to the error page")
                GATES["second"].set()                  # the second load fails after the first ended the visit
                await opener.wait_all()
                return _gotos(h), await h.fields(Page)

        _gate("second")
        with self.assertLogs("lex.lex_app.reflex.ui.handlers", level="ERROR"):
            gotos, fields = asyncio.run(two_failures())
        self.assertEqual(gotos, [ui_state.ERROR_ROUTE], "the second failure of the visit sends nobody anywhere")
        self.assertEqual(fields["ui_visit"], 2, "the first failure ended the visit the opener started")
        self.assertNotIn(runs.key_of(Page.open_broken_too), fields["ui_failed"],
                         "the second failure of an ended visit records nothing")

        handlers.register_page("/a", [Page.open_late_broken], [], "")

        async def moved_on():
            async with testing.Harness() as h:
                await h.navigate("/a")
                opener = await h.send(State, "ui_open", page="/a")
                await opener
                await _running_mark(h, Page.open_late_broken)
                await h.navigate("/b")
                GATES["late"].set()                    # the load fails after the browser moved on
                await opener.wait_all()
                return _gotos(h), await h.fields(Page)

        _gate("late")
        with self.assertLogs("lex.lex_app.reflex.ui.handlers", level="ERROR"):
            gotos, fields = asyncio.run(moved_on())
        self.assertEqual((gotos, fields["ui_error"]), ([], {}),
                         "a failure after the browser moved on neither redirects nor records an error")

    def test_1_777_a_refused_load_is_reported_when_a_failed_block_is_not_stored(self) -> None:
        """
        Scenario 1.777: a dashboard's load the user may not run, under a store like Redis.
        Given: a load started on mount that declares requires=, a user without the grant, and a
               state manager that, like Reflex's Redis manager, does not store a block that raised.
        When:  the load's event arrives.
        Then:  the body never runs; the refusal is in ui.failed, the warning names the handler and
               the user's id, and nothing is left running.
        """
        async def scenario():
            async with testing.Harness(user="anna") as h:
                await h.open_tab("/")
                await (await h.send(Dash, "fill_guarded", _router_data=testing._router_data("/")))
                return await h.fields(Dash), list(h.outcomes)

        with unstored_on_error(), self.assertLogs("lex.lex_app.reflex.ui.handlers", level="WARNING") as logs:
            fields, outcomes = asyncio.run(scenario())
        key = runs.key_of(Dash.fill_guarded)
        self.assertEqual(fields["rows"], [], "the body never ran")
        self.assertEqual([o.kind for o in outcomes], ["denied"], "the user may not run the load")
        self.assertEqual(fields["ui_failed"].get(key), outcomes[0].message, "the refusal is in ui.failed")
        self.assertEqual(fields["ui_running"], {}, "nothing is left running")
        text = "\n".join(logs.output)
        self.assertIn(key, text, "the warning names the handler")
        self.assertIn("anna", text, "the warning names the user's id")

    def test_1_777_a_load_started_on_mount_reports_its_failure_in_place(self) -> None:
        """
        Scenario 1.777: a dashboard's load fails.
        Given: a load without optional=True, started on mount on /, and the page's opener.
        When:  it fails before the opener arrives; and, on another view, after it.
        Then:  neither failure sends the browser to the error page, records an error for it or
               ends the visit; each leaves its message, with a reference, for ui.failed. An error
               an earlier page recorded for the error page survives the next page's opener.
        """
        async def scenario():
            async with testing.Harness() as h:
                early = await h.mount(Dash, "fill_broken", url="/")
                await early
                early_fields = await h.fields(Dash)
                late = await h.mount(Dash, "fill_broken", url="/", gate=_gate("broken"))
                GATES["broken"].set()
                await late
                return _gotos(h), early_fields, await h.fields(Dash)

        with self.assertLogs("lex.lex_app.reflex.ui.handlers", level="ERROR"):
            gotos, early, late = asyncio.run(scenario())
        key = runs.key_of(Dash.fill_broken)
        self.assertEqual(gotos, [], "a load started on mount never sends the browser to the error page")
        for when, fields, visit in (("before", early, 1), ("after", late, 2)):
            self.assertEqual(fields["ui_error"], {}, f"a failure {when} the opener records nothing for the error page")
            self.assertEqual(fields["ui_visit"], visit, f"a failure {when} the opener does not end the visit")
            self.assertIn("Reference", fields["ui_failed"].get(key, ""),
                          f"a failure {when} the opener leaves its message for ui.failed")

        handlers.register_page("/t777-kept", [Page.open_broken], [], "")

        async def error_survives():
            async with testing.Harness() as h:
                await h.navigate("/t777-kept")
                await (await h.send(State, "ui_open", page="/t777-kept"))
                await _until(lambda: _gotos(h), "the failed load never sent the browser to the error page")
                await h.navigate(ui_state.ERROR_ROUTE)
                await (await h.send(State, "ui_open", page=ui_state.ERROR_ROUTE))
                return (await h.fields(State))["ui_error"]

        with self.assertLogs("lex.lex_app.reflex.ui.handlers", level="ERROR"):
            error = asyncio.run(error_survives())
        self.assertEqual(error.get("kind"), "error", "the error page's own opener leaves the error it shows")

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
               middle of an event. lex_page refuses a route whose dynamic argument is named
               like a ui.State helper, and a load declared with arguments; create_app, once the
               project's pages are declared, refuses any page route (added, or declared with
               rx.page) with such an argument.
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

        with patch("reflex.page", fake_page), patch.object(shell, "_NAV", []):
            for route in ("/users/[user]", "/orders/[state]/x", "/p/[[params]]"):
                with self.assertRaises(ValueError, msg=f"{route} is refused") as raised:
                    shell.lex_page(route, title="x")(lambda: rx.text("x"))
                self.assertIn(route, str(raised.exception), "the message names the route")
                self.assertIn("Rename", str(raised.exception), "the message says what to do")
            with self.assertRaises(TypeError) as raised:
                shell.lex_page("/t778-args", title="x", on_load=Dash.fill("x"))(lambda: rx.text("x"))
            self.assertIn("takes no arguments", str(raised.exception),
                          "a load declared on a page with arguments is refused")
            self.assertIn("self.params", str(raised.exception), "the message says where the values come from")
        self.assertIsNone(handlers.page_of("/t778-args"), "the refused page is not registered")

        from reflex_base.registry import RegistrationContext
        from types import SimpleNamespace

        def check(app_routes, declared):
            registry = SimpleNamespace(decorated_pages=[(lambda: None, {"route": r}) for r in declared])
            with patch.object(RegistrationContext, "ensure_context", return_value=registry):
                ui_state.check_app_routes(SimpleNamespace(_unevaluated_pages=dict.fromkeys(app_routes)))

        check(["/", "/tickets/[ticket_id]"], ["/orders/[order]"])       # nothing to refuse
        for app_routes, declared, route in ((["/users/[user]"], [], "/users/[user]"),
                                            ([], ["/a/[api]"], "/a/[api]")):
            with self.assertRaises(ValueError, msg=f"create_app refuses {route}") as raised:
                check(app_routes, declared)
            self.assertIn(route, str(raised.exception), "the message names the route")
        self.assertIn("check_app_routes(app)", source, "create_app checks the app's routes")
        self.assertLess(source.index("load_structure()"), source.index("check_app_routes(app)"),
                        "it checks once the project's pages are declared")
