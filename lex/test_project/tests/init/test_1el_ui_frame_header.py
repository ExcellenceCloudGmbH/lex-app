"""lex.ui's frame: things in the header band -- a search box, a status hint, a bell with a count.

Intent: an application's header holds a few things beside the user menu: a search box that leads to
the project's search page, a status hint ("Sync delayed"), a notifications bell with a count. A
project says so in `ui.frame(search=, actions=[...])` with lex.ui's words (`ui.link`, `ui.status`,
`ui.text`, `ui.button`, `ui.custom_block`); what depends on the user comes from functions of the
user, decided on the server when a page opens. A regression is a header that drops them, a search
that leads where the browser says, a count or a hint computed in the browser, or a header link drawn
for a user who may not open it.

Cluster 1el -- scenarios 1.1009-1.1011. Type: U.
Covers: lex/lex_app/reflex/ui/pages/frame.py (frame search=/actions=, entries, decide),
lex/lex_app/reflex/shell.py (lex_chrome, header_actions, header_search), lex/lex_app/reflex/ui/state.py
(ui_frame_values, ui_frame_type, ui_frame_search).
Run: python -m lex pytest lex/test_project/tests/init/test_1el_ui_frame_header.py -v
"""

from __future__ import annotations

import asyncio
import time

import pytest
import reflex as rx
from reflex.compiler.compiler import compile_page

from lex import ui
from lex.lex_app.reflex.ui.pages import declare
from lex.test_project.tests.init.test_1eb_ui_frame_nav import FrameCase

pytestmark = pytest.mark.init


def _about(title):
    return ui.record(title=title, sections=[ui.section("About", ui.text("All about it."))])


def unread(user):
    return {"anna": 4}.get(user.id, 0)


def sync_state(user):
    if user.id == "ben":
        raise RuntimeError("the sync service is away")
    return "Sync delayed" if user.id == "anna" else None


class HeaderState(ui.State):
    """A project's own state, for a button in the header."""

    refreshed: int = 0
    mode: str = ""

    @ui.action
    def refresh(self):
        self.refreshed += 1


async def decided(*, user: str, grants=()) -> dict:
    from lex.lex_app.reflex.ui import testing
    from lex.lex_app.reflex.ui.state import State

    async with testing.Harness(user=user, grants=grants) as harness:
        await harness.open_tab("/orders")
        await (await harness.send(State, "ui_frame_open"))
        deadline = time.monotonic() + 5
        state = await harness._state(State)
        while not (state.ui_frame_counts or state.ui_frame_values) and time.monotonic() < deadline:
            await asyncio.sleep(0.01)
            state = await harness._state(State)
        await asyncio.sleep(0.05)
        state = await harness._state(State)
        return {"shown": sorted(state.ui_frame_shown), "counts": dict(state.ui_frame_counts),
                "values": dict(state.ui_frame_values)}


class TestCluster01el_FrameHeader(FrameCase):
    """1.1009-1.1011: the header's actions, decided on the server; the search box."""

    def declare_pages(self):
        @ui.page("/orders", nav="Orders")
        def orders():
            return _about("Orders")

        @ui.page("/inbox", requires=ui.read("lex_app.Message"))
        def inbox():
            return _about("Inbox")

        @ui.page("/search", params=["q"])
        def search():
            return _about("Search")

        return orders, inbox, search

    # -- 1.1009 --------------------------------------------------------
    def test_1_1009_the_headers_actions_are_lex_ui_parts(self) -> None:
        """
        Scenario 1.1009: actions in the header band.
        Given: ui.frame(actions=[a status from a function of the user, a bell link to a guarded inbox with
               icon= and badge=, a fixed status, a button of the project's state, a custom block]) (review
               2: the parts at the top right take no ui.text; a fixed word is a status).
        When:  a page is compiled, and the frame is decided for anna (who may read messages) and ben.
        Then:  the header's actions slot holds them, before the user menu, with the status and the count
               from the server's answers and the bell drawn on its answer; anna's status is "Sync
               delayed", her count 4, and the bell is hers; ben's failing status is logged and left out,
               no bell; a part the header does not take -- a text among them -- and a status from a state
               value are refused with what to write.
        """
        orders, inbox, search = self.declare_pages()
        ui.frame(actions=[ui.status(sync_state, tone="warning"),
                          ui.link("Notifications", to=inbox, icon="notification", badge=unread),
                          ui.status("Read-only"), ui.button("Refresh", HeaderState.refresh),
                          ui.custom_block(rx.el.span("custom-mark"), reason="A mark the vocabulary has no word for.")])
        declare.finalize()
        _, module = compile_page("/orders", self.page_named("/orders")())
        for expected in ("rfds-header-actions", "Notifications", "ui_frame_values", "ui_frame_counts",
                         "ui_frame_shown", "Read-only", "Refresh", "custom-mark", '"/inbox"'):
            self.assertIn(expected, module, f"compiled: {expected}")
        self.assertLess(module.index("rfds-header-actions"), module.index("rfds-header-user"), "before the user menu")
        anna = asyncio.run(decided(user="anna", grants=["lex_app.Message:read"]))
        self.assertEqual(list(anna["values"].values()), ["Sync delayed"])
        self.assertEqual(list(anna["counts"].values()), [4])
        self.assertEqual(len(anna["shown"]), 1, "the bell, for a user who may open the inbox")
        with self.assertLogs("lex.lex_app.reflex.ui.pages.frame", level="ERROR") as logged:
            ben = asyncio.run(decided(user="ben"))
        self.assertEqual((ben["values"], ben["shown"]), ({}, []))
        self.assertIn("sync_state", "\n".join(logged.output))
        for wrong in (ui.fact("Region", "north"), ui.section("More", ui.text("x")), ui.text("Read-only")):
            with self.assertRaises(TypeError) as caught:
                ui.frame(actions=[wrong])
            self.assertIn("actions=", str(caught.exception))
            self.assertIn("ui.custom_block", str(caught.exception))
        with self.assertRaises(TypeError) as caught:
            ui.frame(actions=[ui.status(HeaderState.mode)])
        self.assertIn("function of the user", str(caught.exception), "a frame value is fixed or a function of the user")

    # -- 1.1010 --------------------------------------------------------
    def test_1_1010_the_search_box_opens_the_projects_search_page(self) -> None:
        """
        Scenario 1.1010: the search box.
        Given: ui.frame(search=search), a page that takes one query parameter.
        When:  a page is compiled; the user types "north east & co" and presses Enter.
        Then:  RFDS's search box is in the header's search slot; Enter opens the search page with what
               was typed in its parameter, encoded -- the route is the server's, only the text comes from
               the browser; an empty box opens the page without it.
        """
        orders, inbox, search = self.declare_pages()
        ui.frame(search=search)
        declare.finalize()
        _, module = compile_page("/orders", self.page_named("/orders")())
        for expected in ("rfds-header-search", "rfds-search-input", "ui_frame_search"):
            self.assertIn(expected, module, f"compiled: {expected}")
        from lex.lex_app.reflex.ui import testing
        from lex.lex_app.reflex.ui.state import State

        async def press(text):
            async with testing.Harness(user="anna") as harness:
                await harness.open_tab("/orders")
                sent: list = []                         # the events the server sends the browser
                emit = harness._sink.emit_update

                async def capture(update, token, **kwargs):
                    sent.extend(getattr(update, "events", None) or [])
                    await emit(update, token, **kwargs)

                harness._sink.emit_update = capture
                await (await harness.send(State, "ui_frame_type", text=text))
                await (await harness.send(State, "ui_frame_search", form_data={}))
                return [str(event) for event in sent]

        typed = "\n".join(asyncio.run(press("north east & co")))
        self.assertIn("/search?q=north%20east%20%26%20co", typed, "the search page, with what was typed")
        self.assertIn("/search", "\n".join(asyncio.run(press(""))))
        self.assertNotIn("?q=", "\n".join(asyncio.run(press("  "))), "an empty box: the page without it")

    # -- 1.1011 --------------------------------------------------------
    def test_1_1011_a_search_page_must_take_one_value(self) -> None:
        """
        Scenario 1.1011: search= written wrongly.
        Given: pages without a query parameter, with two, with a route argument; a route as text; a raw
               page with one query parameter.
        When:  each is given as ui.frame(search=).
        Then:  each wrong one is refused with what to write; the raw page is accepted.
        """
        orders, inbox, search = self.declare_pages()

        @ui.page("/find", params=["q", "region"])
        def find():
            return _about("Find")

        @ui.page("/regions/[key]", params=["q"])
        def region():
            return _about("Region")

        for wrong, says in ((orders, 'params=["q"]'), (find, "one"), (region, "route"), ("/search", "page")):
            with self.assertRaises((TypeError, ValueError), msg=f"{wrong!r}") as caught:
                ui.frame(search=wrong)
            self.assertIn(says, str(caught.exception))
        ui.frame(search=ui.reflex_page("/legacy-search", params=["term"]))
        from lex.lex_app.reflex.ui.pages import frame

        self.assertEqual(frame.current().search, ("/legacy-search", "term"))
