"""lex.ui's part of lex-app's app: one step, and every page in lex-app's chrome.

Intent: create_app hands lex.ui's pages to the app in one place (the declared @ui.page pages, the
page a failed load goes to, the clock that ends abandoned jobs, the route check), so nothing of it
is forgotten or done twice. Every @ui.page is in lex-app's chrome, after the user's grants: lex.ui
has no hook for a project's own frame or gate - access rules live in the models and `requires=`.
A page function runs once per start, as a raw Reflex page's does: the build checked at the start
is the one the app compiles. A regression is a page that is registered twice, without its error
page, or outside lex-app's chrome, or a page function run twice (twice the work, and page code that
registers something for the process - RFDS's hotkeys - fails the second time).

Cluster 1db -- scenarios 1.825-1.826, 1.1123. Type: U.
Covers: lex/lex_app/reflex/ui/pages/declare.py (install), lex/lex_app/reflex/app.py, lex/ui.py.
Run: python -m lex pytest lex/test_project/tests/init/test_1db_ui_install.py -v
"""

from __future__ import annotations

import inspect
from unittest.mock import patch

import pytest
import reflex as rx
from django.test import SimpleTestCase

from lex import ui
from lex.lex_app.reflex import app as lex_app_module
from lex.lex_app.reflex import shell
from lex.lex_app.reflex.ui import handlers
from lex.lex_app.reflex.ui.pages import declare
from lex.lex_app.reflex.ui.state import ERROR_ROUTE

pytestmark = pytest.mark.init


class InstallState(ui.State):
    name: str = ""

    @ui.load
    def open(self):
        self.name = "One"


class TestCluster01db_Install(SimpleTestCase):
    """1.825-1.826, 1.1123: create_app's lex.ui step; lex-app's chrome for every page; one run each."""

    def setUp(self) -> None:
        self.pages: list = []
        for target in (patch.dict(declare._DECLARED, clear=True), patch.dict(handlers._PAGES),
                       patch.object(shell, "_NAV", []),
                       patch.object(rx, "page", lambda **kwargs: (lambda fn: self.pages.append((kwargs, fn)) or fn))):
            target.start()
            self.addCleanup(target.stop)

    # -- 1.825 ---------------------------------------------------------
    def test_1_825_create_app_hands_lex_ui_its_pages_in_one_step(self) -> None:
        """
        Scenario 1.825: install, lex-app's own.
        Given: an app and one declared page.
        When:  lex.ui's step runs, twice (a reload), and create_app's source is read.
        Then:  the declared page is registered; the error page is added at its route after the
               user's grants; the jobs' clock is registered once; the routes are checked after the
               pages; create_app runs the step, and a project has no name for it.
        """
        @ui.page("/items")
        def items():
            return ui.record(title="Items", sections=[ui.section("About", ui.text("All items"))])

        app = rx.App()
        added, tasks, checked = [], [], []
        with patch.object(app, "add_page", lambda page, **kwargs: added.append(kwargs)), \
                patch.object(app, "register_lifespan_task", tasks.append), \
                patch("lex.lex_app.reflex.ui.state.check_app_routes", checked.append):
            declare.install(app)
            declare.install(app)
        self.assertEqual([kwargs["route"] for kwargs, _page in self.pages], ["/items"], "the declared page, once")
        self.assertEqual([kwargs["route"] for kwargs in added], [ERROR_ROUTE, ERROR_ROUTE])
        self.assertIn("LexAccessState.load", str(added[0]["on_load"][0]), "the error page after the grants")
        self.assertEqual(len(tasks), 1, "the jobs' clock, once")
        self.assertEqual(checked, [app, app], "the routes are checked after the pages")
        self.assertIn("declare.install(app)", inspect.getsource(lex_app_module.create_app))
        self.assertFalse(hasattr(ui, "install") or hasattr(ui, "chrome"), "lex.ui offers no app or chrome hook")

    # -- 1.826 ---------------------------------------------------------
    def test_1_826_every_page_is_in_lex_apps_chrome_after_the_grants(self) -> None:
        """
        Scenario 1.826: lex-app's chrome.
        Given: a declared page with a page load.
        When:  lex-app registers and builds it, and builds the error page.
        Then:  both are in lex-app's chrome; the page's load events are the user's grants, then the
               opener that starts the page's loads - nothing of the project's between them.
        """
        @ui.page("/items/[key]")
        def item():
            return ui.record(title=InstallState.name, load=InstallState.open,
                             sections=[ui.section("About", ui.text(InstallState.name))])

        declare.finalize()
        [(kwargs, page)] = self.pages
        self.assertIn("LexThemeFollower", str(page()), "the page is in lex-app's chrome")
        loads = kwargs["on_load"]
        self.assertEqual(len(loads), 2, "the grants, then the opener")
        self.assertIn("LexAccessState.load", str(loads[0]))
        self.assertIn("ui_open", str(loads[1].handler.fn.__qualname__))
        from lex.lex_app.reflex.ui.state import error_page

        self.assertIn("LexThemeFollower", str(error_page()), "and so is the page a failed load goes to")

    # -- 1.1123 --------------------------------------------------------
    def test_1_1123_a_page_function_runs_once_per_start(self) -> None:
        """
        Scenario 1.1123: one run of a page function per start.
        Given: a declared page whose function counts its runs.
        When:  lex-app takes the declared pages, and the app compiles the page; then compiles it again
               (a later compile in the same process).
        Then:  the function has run once when the page is compiled - the build checked at the start is
               the page the app gets, in lex-app's chrome; a later compile runs it again and gets a page.
        """
        runs = []

        @ui.page("/counted")
        def counted():
            runs.append(1)
            return ui.record(title="Counted", sections=[ui.section("About", ui.text("Counted"))])

        declare.finalize()
        [(_kwargs, page)] = self.pages
        self.assertIn("LexThemeFollower", str(page()), "the page is in lex-app's chrome")
        self.assertEqual(len(runs), 1, "the page function ran once: checked at the start, and that build compiled")
        self.assertIn("Counted", str(page()), "a later compile builds the page again")
        self.assertEqual(len(runs), 2, "and runs the function for it")
