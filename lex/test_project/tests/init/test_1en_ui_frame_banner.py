"""lex.ui's frame: a banner across the top of every page when the project says so.

Intent: when something is wrong for a user ("Some configuration could not be loaded"), every page of
the dashboards says so at the top, in RFDS's banner, until it is right again. A project says so in
`ui.frame(banner=ui.message(...))`; the sentence is fixed, a state value or a function of the user,
decided on the server when a page opens. A regression is a banner that never shows, one shown to a
user it is not about, an empty banner, or a failing function that breaks the page.

Cluster 1en -- scenarios 1.1014-1.1015. Type: U.
Covers: lex/lex_app/reflex/ui/pages/frame.py (frame banner=, entries, decide), lex/lex_app/reflex/shell.py
(lex_chrome, frame_banner), lex/lex_app/reflex/_js/lex_rfds.css.
Run: python -m lex pytest lex/test_project/tests/init/test_1en_ui_frame_banner.py -v
"""

from __future__ import annotations

import asyncio
import time

import pytest
from reflex.compiler.compiler import compile_page

from lex import ui
from lex.lex_app.reflex._js import HERE as JS_DIR
from lex.lex_app.reflex.ui.pages import declare
from lex.test_project.tests.init.test_1eb_ui_frame_nav import FrameCase

pytestmark = pytest.mark.init


def _about(title):
    return ui.record(title=title, sections=[ui.section("About", ui.text("All about it."))])


def config_problem(user):
    if user.id == "ben":
        raise RuntimeError("the configuration store is away")
    return "Some configuration could not be loaded." if user.id == "anna" else None


class NoticeState(ui.State):
    """A project's state holding a notice."""

    notice: str = ""


async def banner_for(user: str) -> dict:
    from lex.lex_app.reflex.ui import testing
    from lex.lex_app.reflex.ui.state import State

    async with testing.Harness(user=user) as harness:
        await harness.open_tab("/orders")
        await (await harness.send(State, "ui_frame_open"))
        deadline = time.monotonic() + 1
        while not (await harness._state(State)).ui_frame_values and time.monotonic() < deadline:
            await asyncio.sleep(0.01)
        return dict((await harness._state(State)).ui_frame_values)


class TestCluster01en_FrameBanner(FrameCase):
    """1.1014-1.1015: RFDS's banner above every page, from a sentence, a state value or a function of the user."""

    def setUp(self) -> None:
        super().setUp()

        @ui.page("/orders", nav="Orders")
        def orders():
            return _about("Orders")

    # -- 1.1014 --------------------------------------------------------
    def test_1_1014_the_banner_says_what_the_project_says_to_whom_it_concerns(self) -> None:
        """
        Scenario 1.1014: a banner from a function of the user, a sentence; not a state value.
        Given: ui.frame(banner=ui.message(config_problem, tone="warning")); then a state value; then a
               fixed sentence.
        When:  a page is compiled, and the frame is decided for anna, carl and ben.
        Then:  RFDS's banner is in the shell's banner region (above the header) with the message's tone, shown while its sentence is
               not empty; anna's sentence is the function's; carl's is nothing (no banner); ben's failing
               function is logged and shows no banner; a fixed sentence is shown as the message shows it;
               a state value is refused (review 2: no page loads a state for the frame, so it would show
               what the last page left there); the banner is not hidden inside lex-app's frame.
        """
        ui.frame(banner=ui.message(config_problem, tone="warning"))
        declare.finalize()
        _, module = compile_page("/orders", self.page_named("/orders")())
        for expected in ("rfds-shell-banner", "rfds-banner", "ui_frame_values", '"warning"'):
            self.assertIn(expected, module, f"compiled: {expected}")
        self.assertEqual(asyncio.run(banner_for("anna")), {"banner": "Some configuration could not be loaded."})
        self.assertEqual(asyncio.run(banner_for("carl")), {}, "nothing to say: no banner")
        with self.assertLogs("lex.lex_app.reflex.ui.pages.frame", level="ERROR") as logged:
            self.assertEqual(asyncio.run(banner_for("ben")), {})
        self.assertIn("config_problem", "\n".join(logged.output))

        with self.assertRaises(TypeError) as caught:
            ui.frame(banner=ui.message(NoticeState.notice, tone="info"))
        self.assertIn("function of the user", str(caught.exception), "a state value is refused, saying what to write")
        ui.frame(banner=ui.message("Maintenance tonight from 22:00.", tone="info"))
        self.assertIn("Maintenance tonight from 22:00.", compile_page("/orders", self.page_named("/orders")())[1])
        css = (JS_DIR / "lex_rfds.css").read_text()
        self.assertNotIn('[data-rfds-shell="banner"]', css, "inside lex-app's frame the banner still shows")

    # -- 1.1015 --------------------------------------------------------
    def test_1_1015_the_banner_is_one_message(self) -> None:
        """
        Scenario 1.1015: banner= written wrongly.
        Given: a sentence as text, a ui.text, a list of messages, a message whose function takes nothing.
        When:  each is given as ui.frame(banner=).
        Then:  each is refused with what to write.
        """
        for wrong, says in (("Maintenance tonight.", "ui.message"), (ui.text("Maintenance"), "ui.message"),
                            ([ui.message("A"), ui.message("B")], "one"),
                            (ui.message(lambda: "x", tone="warning"), "def ")):
            with self.assertRaises(TypeError, msg=f"{wrong!r}") as caught:
                ui.frame(banner=wrong)
            self.assertIn(says, str(caught.exception))
