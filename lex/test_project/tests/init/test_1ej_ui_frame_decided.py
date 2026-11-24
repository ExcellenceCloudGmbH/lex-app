"""lex.ui's frame: what a user may see in the navigation is decided on the server.

Intent: the navigation draws only the entries a user may open -- the shell's usual rule -- and that is
decided on the server when a page opens, for lex-app's permissions and a project's rules alike, from
Keycloak's grants (not from what the browser was told), so it is right on the first page of a new tab.
A regression is an entry the user may not open drawn anyway, an entry a rule refuses still listed, a
browser that decides by itself what is listed, or a failing rule that shows its entry.

Cluster 1ej -- scenarios 1.1000-1.1003. Type: U.
Covers: lex/lex_app/reflex/ui/pages/frame.py (guards, decide), lex/lex_app/reflex/shell.py (nav_sections,
lex_chrome), lex/lex_app/reflex/ui/state.py (ui_frame_open, ui_frame_shown),
lex/lex_app/reflex/ui/pages/links.py (reflex_page requires=), lex/lex_app/reflex/ui/pages/declare.py (install).
Run: python -m lex pytest lex/test_project/tests/init/test_1ej_ui_frame_decided.py -v
"""

from __future__ import annotations

import asyncio
import threading
import time
from unittest.mock import patch

import pytest
import reflex as rx
from reflex.compiler.compiler import compile_page

from lex import ui
from lex.lex_app.reflex import shell
from lex.lex_app.reflex.ui.pages import declare
from lex.test_project.tests.init.test_1eb_ui_frame_nav import FrameCase

pytestmark = pytest.mark.init

ASKED: list = []


@ui.rule
def reviewers(user, params):
    """Only anna reviews; ben's directory lookup fails."""
    ASKED.append((user.id, dict(params._values), threading.current_thread().name))
    if user.id == "ben":
        raise RuntimeError("the directory is away")
    return user.id == "anna" or "Only reviewers."


def _about(title):
    return ui.record(title=title, sections=[ui.section("About", ui.text("All about it."))])


def declare_pages():
    """Orders for everyone; Reports behind a permission; Reviews and a raw page behind the rule."""
    @ui.page("/orders", nav="Orders")
    def orders():
        return _about("Orders")

    @ui.page("/reports", nav="Reports", requires=ui.read("lex_app.Report"))
    def reports():
        return _about("Reports")

    @ui.page("/reviews", nav="Reports", requires=[ui.read("lex_app.Report"), reviewers])
    def reviews():
        return _about("Reviews")

    ui.reflex_page("/legacy", nav="Tools", title="Legacy export", requires=ui.permission("lex_app.Order", "export"))
    ui.reflex_page("/audit", nav="Tools", title="Audit", requires=reviewers)
    declare.finalize()
    return orders


async def decided(*, user: str, grants=(), token: str = "tab1", before=None) -> list[str]:
    """Open a page in a new tab as ``user``: the shell's mount asks the server; its answer."""
    from lex.lex_app.reflex.ui import testing
    from lex.lex_app.reflex.ui.state import State

    async with testing.Harness(user=user, grants=grants, token=token) as harness:
        await harness.open_tab("/orders")
        if before is not None:
            await before(harness)
        await (await harness.send(State, "ui_frame_open"))
        deadline = time.monotonic() + 5
        while True:
            shown = list((await harness._state(State)).ui_frame_shown)
            if shown or time.monotonic() > deadline:
                return shown
            await asyncio.sleep(0.01)


def install(case) -> list:
    """lex-app's start-up step (``declare.install``, as create_app runs it) on an app that keeps what it is given."""
    from types import SimpleNamespace

    added: list = []
    app = SimpleNamespace(add_page=lambda page, **kwargs: added.append((kwargs, page)),
                          register_lifespan_task=lambda task: None, _lex_ui_installed=True)
    with patch("lex.lex_app.reflex.ui.state.check_app_routes", lambda app: None):
        declare.install(app)
    return added


class TestCluster01ej_FrameDecidedOnTheServer(FrameCase):
    """1.1000-1.1003: guarded entries drawn on the server's answer, right on a new tab's first page."""

    def setUp(self) -> None:
        super().setUp()
        ASKED.clear()

    # -- 1.1000 --------------------------------------------------------
    def test_1_1000_a_guarded_entry_is_drawn_only_on_the_servers_answer(self) -> None:
        """
        Scenario 1.1000: the compiled navigation.
        Given: listed pages for everyone, behind a permission, behind a permission and a rule; raw pages
               behind a permission and behind a rule.
        When:  a page is built in lex-app's frame and compiled with Reflex's page compiler.
        Then:  the entry for everyone is drawn as it is; every guarded entry is drawn only while the
               server's answer (ui_frame_shown) holds its key -- its route -- and no permission string
               reaches the navigation; the shell asks the server when it mounts; a frame with nothing to
               decide does not ask.
        """
        orders = declare_pages()
        sections = shell.nav_sections()
        listed = {item.href: item.permission for section in sections for item in section.items}
        self.assertEqual(listed, {"/": None, "/orders": None, "/reports": "/reports", "/reviews": "/reviews",
                                  "/legacy": "/legacy", "/audit": "/audit"},
                         "a guarded entry carries its key, the route; an open one nothing")
        _, module = compile_page("/orders", self.page_named("/orders")())
        self.assertIn("ui_frame_shown", module, "drawn on the server's answer")
        self.assertIn('"/audit"', module)
        self.assertIn("ui_frame_open", module, "the shell asks when it mounts")
        self.assertNotIn("lex_app.Order:export", module, "no permission string decides the navigation in the browser")
        self.assertIsNotNone(orders)
        with patch.object(shell, "_NAV", []):
            self.assertNotIn("ui_frame_open", compile_page("/plain", shell.lex_chrome(rx.text("body")))[1],
                             "nothing to decide: nothing asked")

    # -- 1.1001 --------------------------------------------------------
    def test_1_1001_the_server_decides_permissions_and_rules_for_the_user(self) -> None:
        """
        Scenario 1.1001: decided for each user.
        Given: the pages of 1.1000; anna holds Report:read and is a reviewer; carl holds nothing; ben
               holds Report:read and Order:export, and the rule fails for him.
        When:  a page opens for each, in a new tab.
        Then:  anna's answer lists Reports, Reviews and Audit; carl's nothing; ben's Reports and Legacy
               export -- the failing rule hides its entries; the rule was asked once per event however
               many entries name it, on a worker thread, with no page values.
        """
        declare_pages()
        anna = asyncio.run(decided(user="anna", grants=["lex_app.Report:read"]))
        self.assertEqual(sorted(anna), ["/audit", "/reports", "/reviews"])
        self.assertEqual(len(ASKED), 1, "the rule asked once for both entries that name it")
        self.assertEqual(ASKED[0][1], {}, "a listed page's entry is asked without values")
        self.assertTrue(ASKED[0][2].startswith("lex-ui-"), f"on a worker thread: {ASKED[0][2]}")
        carl = asyncio.run(decided(user="carl"))
        self.assertEqual(carl, [], "nothing guarded for a user who may open nothing")
        with self.assertLogs("lex.lex_app.reflex.ui.pages.frame", level="ERROR") as logged:
            ben = asyncio.run(decided(user="ben", grants=["lex_app.Report:read", "lex_app.Order:export"]))
        self.assertEqual(sorted(ben), ["/legacy", "/reports"], "a failing rule hides its entries")
        self.assertIn("reviewers", "\n".join(logged.output))

    # -- 1.1002 --------------------------------------------------------
    def test_1_1002_what_the_browser_was_told_does_not_decide(self) -> None:
        """
        Scenario 1.1002: the server's grants, on the first page of a new tab.
        Given: carl, who holds nothing, while the permissions the browser is told (RFDS's AccessState,
               loaded from the grants lex-app remembered for RFDS's resolver) claim Report:read and
               Order:export.
        When:  the first page of that new tab opens.
        Then:  the answer comes from Keycloak's grants, fetched with the user's token: nothing is
               listed; and anna's first page of a fresh tab is right at once, without a page having
               been opened before in it.
        """
        declare_pages()
        claimed = frozenset({"lex_app.Report:read", "lex_app.Order:export"})
        with patch("lex.lex_app.reflex.access.lex_permission_resolver", lambda identity, **_: claimed):
            self.assertEqual(asyncio.run(decided(user="carl")), [], "what the browser was told decides nothing")
        self.assertIn("/reports", asyncio.run(decided(user="anna", grants=["lex_app.Report:read"], token="fresh")))

    # -- 1.1003 --------------------------------------------------------
    def test_1_1003_lex_apps_start_up_draws_the_decided_navigation(self) -> None:
        """
        Scenario 1.1003: through lex-app's start-up.
        Given: the pages of 1.1000, handed to an app by lex-app's start-up step (declare.install, as
               create_app runs it); a raw page given a list of permissions and a wrong requires=.
        When:  the installed page is compiled.
        Then:  it draws the guarded entries on the server's answer and asks when it mounts; a raw page's
               requires= takes what a page's does, and a wrong one is refused with what to write.
        """
        declare_pages()
        install(self)
        _, module = compile_page("/reports", self.page_named("/reports")())
        for expected in ("ui_frame_shown", "ui_frame_open", '"/reviews"'):
            self.assertIn(expected, module, f"installed: {expected}")
        ui.reflex_page("/both", nav="Tools", title="Both",
                       requires=[ui.permission("lex_app.Order", "read"), ui.permission("lex_app.Order", "export")])
        from lex.lex_app.reflex.ui.pages import frame

        self.assertEqual(frame.guards()["/both"].permissions, ("lex_app.Order:read", "lex_app.Order:export"))
        with self.assertRaises(ValueError) as caught:
            ui.reflex_page("/wrong", nav="Tools", title="Wrong", requires=lambda user, params: True)
        self.assertIn("@ui.rule", str(caught.exception))
