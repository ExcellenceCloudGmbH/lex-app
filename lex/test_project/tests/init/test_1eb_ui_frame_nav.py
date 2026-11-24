"""lex.ui's frame: the navigation in areas, in the project's order, and the way to sign out.

Intent: a project with several areas of work ("Orders", "Reports", "Tools", each with several
pages) keeps its navigation when its pages become lex.ui pages. lex-app's frame is configured, not
replaced: `ui.frame(nav=[...])`, once, in the dashboard module, gives the areas' order -- the same
word as a page's `nav=`, which names its area -- and a raw Reflex page joins an area with the same
words. The current entry and its area are marked. The way out of the user menu is the project's
when it says so. A regression is a navigation whose areas come in import order, an area made by a
typo, a raw page that cannot be listed, a second frame configured from another module, or a sign-out
that leads to an address lex-app did not check.

Cluster 1eb -- scenarios 1.960-1.964. Type: U.
Covers: lex/lex_app/reflex/ui/pages/frame.py (frame), lex/lex_app/reflex/shell.py (nav_sections,
check_nav, list_in_nav, sign_out_href, lex_chrome), lex/lex_app/reflex/ui/pages/declare.py (page,
finalize, install), lex/lex_app/reflex/ui/pages/links.py (reflex_page), lex/lex_app/reflex/_js/lex_rfds.css,
lex/ui.py.
Run: python -m lex pytest lex/test_project/tests/init/test_1eb_ui_frame_nav.py -v
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
import reflex as rx
from django.test import SimpleTestCase
from reflex.compiler.compiler import compile_page

from lex import ui
from lex.lex_app.reflex import shell
from lex.lex_app.reflex._js import HERE as JS_DIR
from lex.lex_app.reflex.ui import handlers
from lex.lex_app.reflex.ui.pages import declare, frame, links
from lex.lex_app.reflex.ui.pages.parts import PageError

pytestmark = pytest.mark.init


def _record(title: str):
    return ui.record(title=title, sections=[ui.section("About", ui.text("All about it."))])


class FrameCase(SimpleTestCase):
    """Every registry the frame reads starts empty, and the pages Reflex would add are kept here."""

    def setUp(self) -> None:
        self.pages: list = []
        for target in (patch.dict(declare._DECLARED, clear=True), patch.dict(handlers._PAGES),
                       patch.dict(links._REFLEX_PAGES, clear=True), patch.object(shell, "_NAV", []),
                       patch.dict(shell._PLACES, clear=True),
                       patch.object(frame, "_FRAME", None),
                       patch.object(rx, "page", lambda **kwargs: (lambda fn: self.pages.append((kwargs, fn)) or fn))):
            target.start()
            self.addCleanup(target.stop)

    def page_named(self, route: str):
        return next(fn for kwargs, fn in self.pages if kwargs["route"] == route)


class TestCluster01eb_FrameNav(FrameCase):
    """1.960-1.964: areas in the project's order; one frame; raw pages in an area; marked; sign-out."""

    # -- 1.960 ---------------------------------------------------------
    def test_1_960_areas_come_in_the_projects_order_and_pages_in_theirs(self) -> None:
        """
        Scenario 1.960: the navigation in areas.
        Given: ui.frame(nav=["Reports", "Orders"]) and, in this order, a page in Orders, a raw page in
               Orders, a page in Reports and a second page in Orders; then a page whose area is a typo.
        When:  lex-app registers the pages and builds the navigation.
        Then:  the overview comes first, then Reports, then Orders -- the project's order, not the
               order the areas first appear; each area lists its pages in the order they were
               declared, the raw page among them, each by its title; a page with route arguments is in
               its area but not listed (it opens from a page); without ui.frame the areas come
               in the order of their first page; a page in an area ui.frame does not list stops the
               start, naming the page and the areas to choose from.
        """
        ui.frame(nav=["Reports", "Orders"])

        @ui.page("/orders", nav="Orders", icon="list")
        def orders():
            return _record("Orders")

        ui.reflex_page("/orders/export", nav="Orders", title="Export orders", icon="download")

        @ui.page("/reports", nav="Reports")
        def reports():
            return _record("Reports")

        @ui.page("/orders/new", nav="Orders")
        def new_order():
            return _record("New order")

        @ui.page("/orders/[key]", nav="Orders")
        def order():
            return _record("Order")

        declare.finalize()
        shell.check_nav()
        sections = shell.nav_sections()
        self.assertEqual([s.label_default for s in sections], [None, "Reports", "Orders"],
                         "the overview, then the areas in the order ui.frame(nav=) gives")
        self.assertEqual([i.href for i in sections[2].items], ["/orders", "/orders/export", "/orders/new"],
                         "an area's pages in the order they were declared, the raw page among them, "
                         "not the record page")
        self.assertEqual([i.label_default for i in sections[2].items], ["Orders", "Export orders", "New order"])
        self.assertEqual([i.icon for i in sections[2].items], ["list", "download", None])

        with patch.object(frame, "_FRAME", None):
            self.assertEqual([s.label_default for s in shell.nav_sections()], [None, "Orders", "Reports"],
                             "without ui.frame: the order of each area's first page")

        @ui.page("/returns", nav="Ordres")
        def returns():
            return _record("Returns")

        with self.assertRaises(PageError) as caught:
            declare.finalize()
        message = str(caught.exception)
        for expected in ("returns", "Ordres", "ui.frame(nav=", "'Reports'", "'Orders'"):
            self.assertIn(expected, message, f"the error names the page and the areas to choose from: {expected!r}")

    # -- 1.961 ---------------------------------------------------------
    def test_1_961_the_frame_is_configured_once_with_checked_arguments(self) -> None:
        """
        Scenario 1.961: one frame.
        Given: ui.frame called in one module, then again in it (a reload), then from another module;
               and ui.frame given a single area as text, an area twice, and an empty name.
        When:  each call is made.
        Then:  a reload replaces the configuration; another module's call is refused, naming the module
               that configured it; each wrong argument is refused with what to write.
        """
        namespace = {"ui": ui, "__name__": "orders_app.dashboards"}
        exec("ui.frame(nav=['Orders'])", namespace)          # noqa: S102 - a module's top level, as written
        exec("ui.frame(nav=['Orders', 'Reports'])", namespace)  # noqa: S102 - the same module, reloaded
        self.assertEqual(frame.current().nav, ("Orders", "Reports"), "a reload replaces it")
        with self.assertRaises(ValueError) as caught:
            exec("ui.frame(nav=['Tools'])", {"ui": ui, "__name__": "other_app.pages"})  # noqa: S102
        self.assertIn("orders_app.dashboards", str(caught.exception), "it names where the frame is configured")
        self.assertIn("once", str(caught.exception))
        with patch.object(frame, "_FRAME", None):
            with self.assertRaises(TypeError) as caught:
                ui.frame(nav="Orders")
            self.assertIn("nav=[\"Orders\"]", str(caught.exception), "a single area is written as a list")
            for wrong in (["Orders", "Orders"], ["Orders", ""], ["Orders", 3]):
                with self.assertRaises((TypeError, ValueError)):
                    ui.frame(nav=wrong)
            self.assertEqual(frame.current().nav, (), "a refused call configures nothing")

    # -- 1.962 ---------------------------------------------------------
    def test_1_962_a_raw_reflex_page_joins_an_area_with_the_same_words(self) -> None:
        """
        Scenario 1.962: a raw page in the navigation.
        Given: raw Reflex pages declared with ui.reflex_page, with nav=, title=, icon= and requires=.
        When:  the navigation is built.
        Then:  the page is listed in its area by its title, with its icon and the permission that
               hides its entry; it is still somewhere a link may lead; nav= without a title, a title
               or a permission without nav=, and a project rule as its requires= are refused with what
               to write; declared again (a reload) it keeps its place.
        """
        legacy = ui.reflex_page("/legacy", nav="Tools", title="Legacy export", icon="download",
                                requires=ui.permission("lex_app.Order", "read"))
        ui.reflex_page("/other", nav="Tools", title="Other tool")
        ui.reflex_page("/legacy", nav="Tools", title="Legacy export", icon="download",
                       requires=ui.permission("lex_app.Order", "read"))
        [tools] = shell.nav_sections()[1:]
        self.assertEqual(tools.label_default, "Tools")
        self.assertEqual([(i.href, i.label_default, i.icon, i.permission) for i in tools.items],
                         [("/legacy", "Legacy export", "download", "lex_app.Order:read"),
                          ("/other", "Other tool", None, None)], "declared again, it keeps its place")
        self.assertEqual(ui.address(legacy), "/legacy", "still somewhere a link may lead")
        with self.assertRaises(ValueError) as caught:
            ui.reflex_page("/untitled", nav="Tools")
        self.assertIn("title=", str(caught.exception))
        for kwargs in ({"title": "Lonely"}, {"requires": ui.read("lex_app.Order")}, {"icon": "list"}):
            with self.assertRaises(ValueError, msg=f"{kwargs} without nav= has no entry to apply to") as caught:
                ui.reflex_page("/lonely", **kwargs)
            self.assertIn("nav=", str(caught.exception))

    # -- 1.963 ---------------------------------------------------------
    def test_1_963_the_current_entry_and_its_area_are_marked_in_a_compiled_page(self) -> None:
        """
        Scenario 1.963: where am I.
        Given: two areas with a page each, registered by lex-app.
        When:  one page is built in lex-app's frame and compiled with Reflex's page compiler.
        Then:  the compiled page holds both areas' headings and entries, each entry marking itself
               current by the browser's route (a record's route below its list's marks the list); and
               lex-app's stylesheet marks the heading of the area that holds the current entry.
        """
        ui.frame(nav=["Orders", "Reports"])

        @ui.page("/orders", nav="Orders")
        def orders():
            return _record("Orders")

        @ui.page("/reports", nav="Reports")
        def reports():
            return _record("Reports")

        declare.finalize()
        _, module = compile_page("/orders", self.page_named("/orders")())
        for expected in ('"Orders"', '"Reports"', "aria-current", "rfds-side-nav-heading", '"/reports"'):
            self.assertIn(expected, module, f"compiled: {expected}")
        css = (JS_DIR / "lex_rfds.css").read_text()
        self.assertIn('.rfds-side-nav-section:has(.rfds-side-nav-link[aria-current="page"]) .rfds-side-nav-heading',
                      css, "the current entry's area is marked by lex-app's stylesheet")

    # -- 1.964 ---------------------------------------------------------
    def test_1_964_the_user_menu_signs_out_where_the_project_says(self) -> None:
        """
        Scenario 1.964: the way out.
        Given: lex-app's frame with no configuration, then ui.frame(sign_out=) an outside address,
               then a page of the dashboards.
        When:  the frame is built and compiled.
        Then:  by default the user menu signs out at lex-app's /logout; configured, it leads to the
               project's address, or the page's route; a route written as text, a plain-http address
               and a page that needs values are refused with what to write; the product name is still
               LEX_DASHBOARD_TITLE.
        """
        self.assertEqual(shell.sign_out_href(), shell.SIGN_OUT_HREF, "lex-app's own sign-out by default")
        self.assertIn('"/logout"', compile_page("/plain", shell.lex_chrome(rx.text("body")))[1])
        ui.frame(sign_out="https://portal.example.com/sign-out")
        self.assertEqual(shell.sign_out_href(), "https://portal.example.com/sign-out")
        with patch.dict("os.environ", {"LEX_DASHBOARD_TITLE": "Order desk"}):
            _, module = compile_page("/plain", shell.lex_chrome(rx.text("body")))
        self.assertIn("https://portal.example.com/sign-out", module, "the user menu leads there")
        self.assertIn("Order desk", module, "the product name is the deployment's")

        @ui.page("/goodbye")
        def goodbye():
            return _record("Signed out")

        @ui.page("/orders/[key]")
        def order():
            return _record("Order")

        ui.frame(sign_out=goodbye)
        self.assertEqual(shell.sign_out_href(), "/goodbye", "a page of the dashboards, by its route")
        for wrong, says in (("/logout", "not written as text"), ("http://portal.example.com", "https://"),
                            (order, "needs key")):
            with self.assertRaises(ValueError, msg=f"sign_out={wrong!r}") as caught:
                ui.frame(sign_out=wrong)
            self.assertIn(says, str(caught.exception))
