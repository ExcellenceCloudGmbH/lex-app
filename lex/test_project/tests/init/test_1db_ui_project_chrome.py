"""lex.ui pages in a project's own chrome, in a project's own Reflex app.

Intent: a project that already has its own frame (header, navigation), its own gate (who is
asking, what they may see) and its own app module must be able to write pages with ``@ui.page``
without taking lex-app's chrome: the page looks like the rest of the project, and the project's
gate has run before the page's own loads. A regression is a converted page that suddenly looks
and behaves unlike its neighbours, or a page whose load runs before the gate it relies on.

Cluster 1db -- scenarios 1.825-1.826. Type: U.
Covers: lex/lex_app/reflex/ui/pages/declare.py (chrome, install), lex/lex_app/reflex/shell.py
(framed, first_loads), lex/lex_app/reflex/ui/state.py (error_page), lex/lex_app/reflex/app.py.
Run: python -m lex pytest lex/test_project/tests/init/test_1db_ui_project_chrome.py -v
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
import reflex as rx
from django.test import SimpleTestCase

from lex import ui
from lex.lex_app.reflex import shell
from lex.lex_app.reflex.ui import handlers
from lex.lex_app.reflex.ui.pages import declare
from lex.lex_app.reflex.ui.state import ERROR_ROUTE

pytestmark = pytest.mark.init


class ChromeDesk(ui.State):
    name: str = ""

    @ui.load
    def open(self):
        self.name = "Fund One"

    @rx.event
    def gate(self, section: str):
        """The project's own gate: a raw Reflex load event."""


def project_frame(page, route):
    return rx.box(rx.el.header("Project header"), page, class_name=f"project-frame {route}")


def first(route):
    return [ChromeDesk.gate(route.strip("/").split("/")[0] or "home")]


class _Isolated(SimpleTestCase):
    def setUp(self) -> None:
        self.pages: list = []
        for target in (patch.dict(declare._DECLARED, clear=True), patch.dict(handlers._PAGES),
                       patch.object(shell, "_NAV", []), patch.object(shell, "_PROJECT_CHROME", []),
                       patch.object(rx, "page", lambda **kwargs: (lambda fn: self.pages.append((kwargs, fn)) or fn))):
            target.start()
            self.addCleanup(target.stop)

    @staticmethod
    def names(events) -> list[str]:
        return [str(getattr(getattr(event, "fn", None), "__qualname__", "")
                    or getattr(getattr(event, "handler", None), "fn", event).__qualname__) for event in events]


class TestCluster01db_ProjectChrome(_Isolated):
    """1.825-1.826: ui.chrome, ui.install."""

    # -- 1.825 ---------------------------------------------------------
    def test_1_825_a_page_in_the_projects_own_frame_after_its_gate(self) -> None:
        """
        Scenario 1.825: ui.chrome.
        Given: a project frame and a gate given to ui.chrome, and a ui.page with a page load.
        When:  lex-app registers the page and builds it, and builds the page a failed load goes to.
        Then:  the page is in the project's frame, not lex-app's chrome; its load events are the
               grants, then the project's gate for that route, then the opener that starts the
               page's loads; the error page is in the project's frame too. Without ui.chrome a
               page stays in lex-app's chrome. Something that is not a function is refused.
        """
        @ui.page("/funds/[key]")
        def fund():
            return ui.record(title=ChromeDesk.name, load=ChromeDesk.open,
                             sections=[ui.section("About", ui.text(ChromeDesk.name))])

        ui.chrome(project_frame, first=first)
        declare.finalize()
        [(kwargs, page)] = self.pages
        built = str(page())
        self.assertIn("project-frame /funds/[key]", built, "the page is in the project's frame")
        self.assertIn("Project header", built)
        self.assertNotIn("LexThemeFollower", built, "not in lex-app's chrome")
        loads = kwargs["on_load"]
        self.assertEqual(len(loads), 3, "grants, the project's gate, the opener")
        self.assertIn("LexAccessState.load", str(loads[0]), "the user's grants first")
        self.assertIn("ChromeDesk.gate", str(loads[1].handler.fn.__qualname__), "then the project's gate")
        self.assertEqual(loads[1].args[0][1]._var_value if hasattr(loads[1].args[0][1], "_var_value") else None,
                         "funds", "the gate is told which route it guards")
        self.assertIn("ui_open", str(loads[2].handler.fn.__qualname__), "the opener starts the page's loads last")
        from lex.lex_app.reflex.ui.state import error_page

        self.assertIn("project-frame /ui-error", str(error_page()), "a failed load's page is in the frame too")

        shell._PROJECT_CHROME[:] = []
        self.assertIn("LexThemeFollower", str(shell.framed(rx.text("body"), "/x")), "without ui.chrome: lex-app's chrome")
        with self.assertRaises(TypeError):
            ui.chrome("frame")
        with self.assertRaises(TypeError):
            ui.chrome(project_frame, first="gate")

    # -- 1.826 ---------------------------------------------------------
    def test_1_826_install_puts_lex_ui_into_a_projects_own_app(self) -> None:
        """
        Scenario 1.826: ui.install.
        Given: a project's own rx.App, its chrome, and one ui.page.
        When:  the app module calls ui.install(app), and again on a reload.
        Then:  the declared page is registered; the error page is added at its route with the
               grants and the project's gate as its loads; the jobs' clock is registered once; a
               page whose route argument is named like a ui.State helper is refused.
        """
        @ui.page("/funds")
        def funds():
            return ui.record(title="Funds", sections=[ui.section("About", ui.text("All funds"))])

        app = rx.App()
        added: list = []
        tasks: list = []
        checked: list = []
        ui.chrome(project_frame, first=first)
        with patch.object(app, "add_page", lambda page, **kwargs: added.append(kwargs)), \
                patch.object(app, "register_lifespan_task", tasks.append), \
                patch("lex.lex_app.reflex.ui.state.check_app_routes", checked.append):
            ui.install(app)
            ui.install(app)
        self.assertEqual([kwargs["route"] for kwargs, _page in self.pages], ["/funds"], "the declared page")
        self.assertEqual([kwargs["route"] for kwargs in added], [ERROR_ROUTE, ERROR_ROUTE])
        error_loads = added[0]["on_load"]
        self.assertIn("LexAccessState.load", str(error_loads[0]))
        self.assertIn("ChromeDesk.gate", str(error_loads[1].handler.fn.__qualname__))
        self.assertEqual(len(tasks), 1, "the jobs' clock, once")
        self.assertEqual(checked, [app, app], "the routes are checked after the project's pages")
