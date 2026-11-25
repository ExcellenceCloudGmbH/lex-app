"""lex.ui's frame: a page the user may not open can look like a page that does not exist.

Intent: some applications must not reveal that a page exists to a user who may not open it. With
`ui.frame(denied_as_missing=True)` every refused page -- by a permission or a project's rule -- shows
lex-app's not-found page instead of the no-access notice: its body, the browser tab's title, the
breadcrumbs and the navigation entry give nothing away, and lex-app's own not-found page looks the same.
It is decided on the server by the page's opener. A regression is a refused page that still says "no
access" or shows its title or breadcrumbs, a permitted user who sees "not found", or a missing page that
looks different from a refused one.

Cluster 1eo -- scenarios 1.1016-1.1019, 1.1111. Type: U.
Covers: lex/lex_app/reflex/ui/pages/frame.py (frame denied_as_missing=), lex/lex_app/reflex/shell.py
(lex_page, lex_chrome, not_found_body, not_found_page), lex/lex_app/reflex/ui/state.py (_ui_start,
error_page), lex/lex_app/reflex/app.py (create_app adds lex-app's not-found page).
Run: python -m lex pytest lex/test_project/tests/init/test_1eo_ui_frame_denied_as_missing.py -v
"""

from __future__ import annotations

import asyncio
import inspect
from unittest.mock import patch

import pytest
import reflex as rx
from reflex.compiler.compiler import compile_page

from lex import ui
from lex.lex_app.reflex import app as lex_app_module
from lex.lex_app.reflex import shell
from lex.lex_app.reflex.ui.pages import declare, frame
from lex.test_project.tests.init.test_1eb_ui_frame_nav import FrameCase

pytestmark = pytest.mark.init

NOT_FOUND = "This page does not exist"


class SecretState(ui.State):
    figures: list[str] = []

    @ui.load
    def open(self):
        self.figures = ["12"]


@ui.rule
def admins(user, params):
    return user.id == "anna" or "Only administrators."


def _about(title):
    return ui.record(title=title, sections=[ui.section("About", ui.text("All about it."))])


class TestCluster01eo_RefusedAsMissing(FrameCase):
    """1.1016-1.1019: a refused page looks missing; decided by the opener; lex-app's not-found page."""

    def declare_pages(self):
        @ui.page("/orders", nav="Orders")
        def orders():
            return _about("Orders")

        @ui.page("/secret", nav="Admin", requires=ui.read("lex_app.Secret"))
        def secret():
            return ui.record(title="Secret figures", load=SecretState.open,
                             sections=[ui.section("Figures", ui.text("The figures."))])

        @ui.page("/admin", nav="Admin", requires=admins)
        def admin():
            return _about("Administration")

        declare.finalize()

    # -- 1.1016 --------------------------------------------------------
    def test_1_1016_a_refused_page_is_drawn_as_a_missing_one(self) -> None:
        """
        Scenario 1.1016: what the browser is given.
        Given: ui.frame(denied_as_missing=True); a page behind a permission and one behind a rule.
        When:  lex-app registers and compiles them.
        Then:  neither carries RFDS's access-denied notice; each shows lex-app's not-found body when the
               opener refuses, and its content, breadcrumbs included, only once it grants; the tab's title
               is the not-found title until then; the Admin area, all of whose entries are guarded, is
               drawn only while one of them is; without the switch the permission page still shows the
               no-access notice and its own title.
        """
        ui.frame(denied_as_missing=True)
        self.declare_pages()
        for route in ("/secret", "/admin"):
            _, module = compile_page(route, self.page_named(route)())
            self.assertNotIn("rfds-access-denied", module, f"{route}: no no-access notice")
            for expected in (NOT_FOUND, "ui_access", "rfds-breadcrumbs"):
                self.assertIn(expected, module, f"{route}: {expected}")
        orders_area, admin_area = (str(one) for one in shell.side_nav().children[1:3])
        self.assertIn("Admin", admin_area)
        self.assertLess(admin_area.index("ui_frame_shown"), admin_area.index("Admin"),
                        "an area whose entries are all guarded waits, heading and all, for one of them")
        self.assertLess(orders_area.find("ui_frame_shown"), 0, "an area with an open entry is drawn")
        titles = {kwargs["route"]: kwargs["title"] for kwargs, _fn in self.pages}
        self.assertIsInstance(titles["/secret"], rx.Var, "the title waits for the opener's answer")
        self.assertIn("404", str(titles["/secret"]))
        self.assertEqual(titles["/orders"], "Orders", "an open page keeps its title")

    def test_1_1016b_without_the_switch_a_refused_page_says_no_access(self) -> None:
        """Scenario 1.1016 (continued): without denied_as_missing the notice and the title stay."""
        self.declare_pages()
        _, module = compile_page("/secret", self.page_named("/secret")())
        self.assertIn("rfds-access-denied", module)
        self.assertNotIn(NOT_FOUND, module)
        self.assertEqual({kwargs["route"]: kwargs["title"] for kwargs, _fn in self.pages}["/secret"], "Secret figures")

    # -- 1.1017 --------------------------------------------------------
    def test_1_1017_the_opener_decides_a_permission_page_on_the_server(self) -> None:
        """
        Scenario 1.1017: decided by the opener.
        Given: ui.frame(denied_as_missing=True) and the pages of 1.1016.
        When:  carl (no grants) and anna (Secret:read) open /secret, and carl opens /admin.
        Then:  carl is refused on the server -- "no", no load started -- and anna granted with her load
               run; the rule's page is refused for carl too.
        """
        ui.frame(denied_as_missing=True)
        self.declare_pages()
        from lex.lex_app.reflex.ui import testing
        from lex.lex_app.reflex.ui.state import State

        async def visit(user, route, grants=()):
            async with testing.Harness(user=user, grants=grants) as harness:
                await harness.open_tab(route)
                await (await harness.send(State, "ui_open", page=route))
                await asyncio.sleep(0.05)
                return (await harness.fields(SecretState))["figures"], dict((await harness._state(State)).ui_access)

        # The opener asks RFDS's resolver for the grants LexAccessState.load remembered just before it.
        with patch("lex.lex_app.reflex.ui.state._page_permitted", lambda state, requires: False):
            figures, access = asyncio.run(visit("carl", "/secret"))
        self.assertEqual((figures, access["/secret"]["granted"]), ([], "no"), "refused: no load")
        with patch("lex.lex_app.reflex.ui.state._page_permitted", lambda state, requires: True):
            figures, access = asyncio.run(visit("anna", "/secret", ["lex_app.Secret:read"]))
        self.assertEqual((figures, access["/secret"]["granted"]), (["12"], "yes"), "granted: its load ran")
        _figures, access = asyncio.run(visit("carl", "/admin"))
        self.assertEqual(access["/admin"]["granted"], "no")

    # -- 1.1018 --------------------------------------------------------
    def test_1_1018_lex_apps_not_found_page_looks_the_same(self) -> None:
        """
        Scenario 1.1018: a missing page and a refused one.
        Given: ui.frame(denied_as_missing=True).
        When:  lex-app's not-found page and the page a refused load goes to are compiled, and create_app's
               source is read.
        Then:  the not-found page is in lex-app's frame with the same body a refused page shows; a refused
               load's page shows that body instead of the no-access notice, any other failure keeps "Try
               again"; create_app adds the not-found page at Reflex's 404 route, unless the project has one.
        """
        ui.frame(denied_as_missing=True)
        from lex.lex_app.reflex.ui.state import error_page

        _, page = compile_page("404", shell.not_found_page())
        for expected in (NOT_FOUND, "Overview", "LexThemeFollower"):
            self.assertIn(expected, page, f"lex-app's not-found page: {expected}")
        _, failed = compile_page("/ui-error", error_page())
        self.assertIn(NOT_FOUND, failed)
        self.assertIn("Try again", failed)
        self.assertNotIn("rfds-access-denied", failed)
        source = inspect.getsource(lex_app_module.create_app)
        self.assertIn("not_found_page", source)
        self.assertIn("404", source)

    # -- 1.1019 --------------------------------------------------------
    def test_1_1019_denied_as_missing_written_wrongly(self) -> None:
        """
        Scenario 1.1019: mistakes.
        Given: denied_as_missing="yes"; denied_as_missing=True beside denied= and request_access=.
        When:  each is configured.
        Then:  each is refused with what to write.
        """
        with self.assertRaises(TypeError) as caught:
            ui.frame(denied_as_missing="yes")
        self.assertIn("True", str(caught.exception))
        for kwargs in ({"denied": "Ask your administrator."}, {"request_access": "mailto:it@example.com"}):
            with self.assertRaises(ValueError, msg=f"{kwargs}") as caught:
                ui.frame(denied_as_missing=True, **kwargs)
            self.assertIn("never shows", str(caught.exception))


    # -- 1.1111 --------------------------------------------------------
    def test_1_1111_the_switch_is_named_like_the_other_denied_words(self) -> None:
        """
        Scenario 1.1111: one stem for what a refused user sees (review 2): denied=, request_access=,
        denied_as_missing=.
        Given: the switch under its new name, then under its old one.
        When:  ui.frame is configured with each.
        Then:  denied_as_missing=True is the switch; refused_as_missing= is refused, saying the new name and
               the exact argument to write.
        """
        ui.frame(denied_as_missing=True)
        self.assertTrue(frame.current().denied_as_missing)
        with self.assertRaises(TypeError) as caught:
            ui.frame(refused_as_missing=True)
        self.assertIn("write denied_as_missing=True", str(caught.exception))
