"""lex.ui's frame: something that happens when a page opens -- ``ui.frame(opened=)``.

Intent: a project keeps a "recently used" list, or an audit entry of who opened what, without code
on each page: lex-app calls one function of the project's with the signed-in user and the page once
the page is shown. It never slows the page -- it runs after the page's loads, on a worker thread,
not awaited -- and its failure is the project's to read in the log, never the user's. A regression
is a page that waits for the bookkeeping, a page left broken by it, an entry for a page the user
was refused or that failed to load, or a recently-used entry without the record's name.

Cluster 1ee -- scenarios 1.974-1.976. Type: U.
Covers: lex/lex_app/reflex/ui/pages/frame.py (opened=, OpenedPage, page_opened),
lex/lex_app/reflex/ui/state.py (ui_open), lex/lex_app/reflex/ui/handlers.py (load wind-up, register_page).
Run: python -m lex pytest lex/test_project/tests/init/test_1ee_ui_page_opened.py -v
"""

from __future__ import annotations

import asyncio
import threading
import time

import pytest

from lex import ui
from lex.lex_app.reflex.ui import testing
from lex.lex_app.reflex.ui.pages import declare
from lex.lex_app.reflex.ui.state import State
from lex.test_project.tests.init.test_1eb_ui_frame_nav import FrameCase

pytestmark = pytest.mark.init

OPENED: list = []
GATE = {"hook": threading.Event(), "load": threading.Event()}


def remember(user, page):
    """The project's function: waits for the test, then records what it was given."""
    GATE["hook"].wait(5)
    OPENED.append((user.id, page.route, page.address, page.title, page.area, dict(page.params),
                   threading.current_thread().name))


def broken(user, page):
    raise RuntimeError("the recent-pages table is away")


class OpenedOrderState(ui.State):
    name: str = ""

    @ui.rows
    def orders(self):
        return [{"id": 7, "name": "Order 7"}]

    @ui.load
    def open(self):
        if self.params.get("key") == "0":
            raise RuntimeError("no such order")
        GATE["load"].wait(5)
        self.name = f"Order {self.params.get('key')}"


@ui.rule
def nobody(user, params):
    return "Nobody may."


async def _until(condition, failure: str) -> None:
    deadline = time.monotonic() + 5
    while not condition():
        if time.monotonic() > deadline:
            raise AssertionError(failure)
        await asyncio.sleep(0.005)


class TestCluster01ee_PageOpened(FrameCase):
    """1.974-1.976: after the loads, with the record's name; not for a refused or failed page; checked."""

    def setUp(self) -> None:
        super().setUp()
        OPENED.clear()
        for gate in GATE.values():
            gate.clear()

        @ui.page("/orders/[key]")
        def order():
            return ui.record(title=OpenedOrderState.name, load=OpenedOrderState.open,
                             sections=[ui.section("About", ui.text(OpenedOrderState.name))])

        @ui.page("/orders", nav="Sales")
        def orders():
            return ui.list(title="Orders", table=ui.table(OpenedOrderState.orders, columns=["name"], open=order))

        @ui.page("/secret", requires=nobody)
        def secret():
            return ui.record(title="Secret", sections=[ui.section("About", ui.text("Hidden."))])

        self.order = order
        declare.finalize()

    def visit(self, url: str, route: str, params: dict, *, after=None):
        async def scenario():
            async with testing.Harness(user="anna", params=params) as harness:
                await harness.open_tab(url)
                await (await harness.send(State, "ui_open", page=route))
                if after is not None:
                    await after(harness)
                return await harness.fields(OpenedOrderState)

        return asyncio.run(scenario())

    # -- 1.974 ---------------------------------------------------------
    def test_1_974_the_projects_function_runs_after_the_loads_and_never_holds_the_page(self) -> None:
        """
        Scenario 1.974: a page opened.
        Given: ui.frame(opened=remember) and an order page with a load; remember waits until released.
        When:  anna opens /orders/7 through Reflex's own event processor.
        Then:  remember is not called before the load has finished; the page's load finishes while
               remember is still waiting (the page never waits for it); remember then gets the user
               and the page: its route, its address with the values, its title as the load left it
               (the record's name), its area (its parent's) and its values -- on a worker thread.
        """
        ui.frame(opened=remember)

        async def after(harness):
            await asyncio.sleep(0.05)
            self.assertEqual(OPENED, [], "not before the page's load has finished")
            GATE["load"].set()
            deadline = time.monotonic() + 5
            while (await harness.fields(OpenedOrderState))["name"] != "Order 7":
                if time.monotonic() > deadline:
                    raise AssertionError("the load never finished while remember waited")
                await asyncio.sleep(0.005)
            await asyncio.sleep(0.05)
            self.assertEqual(OPENED, [], "remember is still waiting: the page did not wait for it")
            GATE["hook"].set()
            await _until(lambda: OPENED, "remember was never called")

        self.visit("/orders/7", "/orders/[key]", {"key": "7"}, after=after)
        [(user, route, address, title, area, params, thread)] = OPENED
        self.assertEqual((user, route, address, title, area, params),
                         ("anna", "/orders/[key]", "/orders/7", "Order 7", "Sales", {"key": "7"}))
        self.assertTrue(thread.startswith("lex-ui-"), f"on a worker thread: {thread}")

    # -- 1.975 ---------------------------------------------------------
    def test_1_975_not_for_a_refused_or_failed_page_and_a_failure_is_only_logged(self) -> None:
        """
        Scenario 1.975: when it is not called, and when it fails.
        Given: ui.frame(opened=) with the recording function, then with one that raises.
        When:  anna opens a page without loads, a page a rule refuses, an order whose load fails, and
               the page without loads again with the failing function.
        Then:  the page without loads is recorded at once; the refused and the failed page are not;
               the failing function is logged with the page's route and changes nothing on the page.
        """
        GATE["hook"].set()
        GATE["load"].set()
        ui.frame(opened=remember)

        async def settle(harness):
            await asyncio.sleep(0.1)

        self.visit("/orders", "/orders", {}, after=settle)
        self.assertEqual([entry[1:5] for entry in OPENED], [("/orders", "/orders", "Orders", "Sales")])
        self.visit("/secret", "/secret", {}, after=settle)
        self.visit("/orders/0", "/orders/[key]", {"key": "0"}, after=settle)
        self.assertEqual(len(OPENED), 1, "not for a refused page, nor one whose load failed")
        ui.frame(opened=broken)
        with self.assertLogs("lex.lex_app.reflex.ui.pages.frame", level="ERROR") as logged:
            self.visit("/orders", "/orders", {}, after=settle)
        self.assertIn("/orders", "\n".join(logged.output), "the failure is logged with the page")

    # -- 1.976 ---------------------------------------------------------
    def test_1_976_the_function_is_checked_when_the_frame_is_configured(self) -> None:
        """
        Scenario 1.976: what opened= takes.
        Given: ui.frame(opened=) given text, a function of one argument and an async function.
        When:  each is configured.
        Then:  each is refused with what to write: a plain function of (user, page).
        """
        async def later(user, page):
            return None

        for wrong in ("remember", lambda user: None, later):
            with self.assertRaises(TypeError, msg=repr(wrong)) as caught:
                ui.frame(opened=wrong)
            self.assertIn("(user, page)", str(caught.exception))
