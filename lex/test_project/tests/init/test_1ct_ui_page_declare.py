"""lex.ui pages: @ui.page says where a page lives; lex-app registers it once the module is loaded.

Intent: a page function returns a page kind and may name pages declared further down, so it is
not called when it is decorated. Once the project's module is loaded, lex-app hands every
declared page its chrome, its navigation entry, its permission and its loads: the kind's load as
the page's required load and each section's load as an optional one. A dashboard is not a route:
@ui.dashboard builds the kind and starts its loads on mount. A regression is a page that cannot
link forward, a page whose loads never start, or a navigation entry without words.

Cluster 1ct -- scenarios 1.803-1.805. Type: U.
Covers: lex/lex_app/reflex/ui/pages/declare.py (page, dashboard, finalize),
lex/lex_app/reflex/app.py (create_app).
Run: python -m lex pytest lex/test_project/tests/init/test_1ct_ui_page_declare.py -v
"""

from __future__ import annotations

import inspect
from unittest.mock import patch

import pytest
import reflex as rx
from django.contrib.auth.models import Group
from django.test import SimpleTestCase

from lex import ui
from lex.lex_app.reflex import app as lex_app_module
from lex.lex_app.reflex import shell
from lex.lex_app.reflex.ui import handlers
from lex.lex_app.reflex.ui.pages import declare, kinds, links, tables, vocab
from lex.lex_app.reflex.ui.pages.parts import PageError

pytestmark = pytest.mark.init


class DeclareDesk(ui.State):
    name: str = ""

    @ui.load
    def open(self):
        self.name = "Fund One"

    @ui.load
    def load_documents(self):
        self.name = self.name

    @ui.action
    def touch(self):
        self.name = "touched"


class TestCluster01ct_PageDeclare(SimpleTestCase):
    """1.803-1.805: declaring pages; what finalize hands to lex-app; dashboards."""

    def setUp(self) -> None:
        for target in (patch.dict(declare._DECLARED, clear=True), patch.dict(handlers._PAGES),
                       patch.object(shell, "_NAV", [])):
            target.start()
            self.addCleanup(target.stop)
        added = patch.object(rx, "page", lambda **kwargs: (lambda fn: self.pages.append((kwargs, fn)) or fn))
        self.pages: list = []
        added.start()
        self.addCleanup(added.stop)

    # -- 1.803 ---------------------------------------------------------
    def test_1_803_a_page_is_declared_without_being_called_and_may_link_forward(self) -> None:
        """
        Scenario 1.803: @ui.page.
        Given: two pages, the first linking to the second, which is declared below it.
        When:  they are decorated, and lex-app then registers the declared pages.
        Then:  decorating calls neither function and returns it unchanged, with what a link
               needs (route, its arguments, its declared query parameters); registration builds
               each page in lex-app's chrome under its route, with the permission from
               requires= as RFDS's permission string and the opener last in its load events. A
               route that does not start with "/", a requires= that is not one permission, a
               params= given as one string, and a second function at a route are refused when
               the page is declared.
        """
        calls: list = []

        @declare.page("/funds", nav="Funds")
        def funds():
            calls.append("funds")
            return kinds.list_(title="Funds", table=tables.table(Group, columns=["name"], open=fund),
                               actions=[vocab.button("First", to=fund, key="1")])

        @declare.page("/funds/[key]", requires=ui.read(Group), params=["tab"])
        def fund():
            calls.append("fund")
            return kinds.record(title=DeclareDesk.name, load=DeclareDesk.open,
                                sections=[kinds.section("Documents", vocab.text("x"), load=DeclareDesk.load_documents)])

        self.assertEqual(calls, [], "decorating does not call the page function")
        self.assertEqual(links.page_info(fund), links.PageInfo("fund", "/funds/[key]", ("key",), ("tab",)),
                         "the function carries what a link needs")
        declare.finalize()
        routes = {kwargs["route"]: (kwargs, fn) for kwargs, fn in self.pages}
        self.assertEqual(sorted(routes), ["/funds", "/funds/[key]"], "both pages are handed to Reflex under their routes")
        kwargs, body = routes["/funds"]
        self.assertEqual(kwargs["title"], "Funds", "the browser tab's title is the kind's title")
        self.assertIn("ui_open", str(kwargs["on_load"][-1]), "the opener is the last load event")
        self.assertIn("/funds/1", str(body()), "the forward link is resolved when the page is built")
        self.assertEqual(handlers.page_of("/funds/[key]").requires, "auth.Group:read",
                         "requires= becomes the page's permission string")
        with self.assertRaises(ValueError, msg="a route starts with /"):
            declare.page("funds")
        with self.assertRaises(TypeError) as wrong_requires:
            declare.page("/open", requires=[ui.read(Group)])
        self.assertIn("Leave it out for a page any signed-in user may see", str(wrong_requires.exception),
                      "a wrong requires= is refused when the page is declared, with the route and what to write")
        self.assertIn("'/open'", str(wrong_requires.exception), "the route is named")
        declare.page("/anyone", requires=ui.signed_in)(lambda: None)
        self.assertIsNone(declare._DECLARED["/anyone"].permission, "ui.signed_in is a page for any signed-in user")
        declare._DECLARED.pop("/anyone")
        with self.assertRaises(TypeError, msg="params= is a list of names, not one string"):
            declare.page("/funds", params="tab")
        with self.assertRaises(ValueError) as twice:
            @declare.page("/funds")
            def other_funds():
                return None
        self.assertIn("`other_funds` needs a route of its own", str(twice.exception),
                      "two functions cannot be the page at one route")

    # -- 1.804 ---------------------------------------------------------
    def test_1_804_registration_hands_over_the_navigation_entry_and_the_loads(self) -> None:
        """
        Scenario 1.804: declare.finalize.
        Given: a page in the navigation with a fixed title, a record page with a page load and a
               section load, a page in the navigation whose title is a state value, and a page
               function that returns a raw component.
        When:  lex-app registers the declared pages.
        Then:  the navigation entry carries the kind's title; the kind's load is registered as
               the page's required load and the section's load as an optional one; registering
               twice adds nothing; a navigation page without a fixed title, a load= that is not
               a @ui.load handler, one load given twice, a page function that raises, and a
               function that does not return a kind, are each refused with the page named.
        """
        @declare.page("/funds", nav="Funds", icon="list")
        def funds():
            return kinds.list_(title="All funds", table=tables.table(Group, columns=["name"]))

        @declare.page("/funds/[key]")
        def fund():
            return kinds.record(title=DeclareDesk.name, load=DeclareDesk.open,
                                sections=[kinds.section("Documents", vocab.text("x"), load=DeclareDesk.load_documents)])

        declare.finalize()
        declare.finalize()
        self.assertEqual([(e.route, e.label, e.section, e.icon) for e in shell.nav_entries()],
                         [("/funds", "All funds", "Funds", "list")], "one navigation entry, labelled with the title")
        entry = handlers.page_of("/funds/[key]")
        self.assertEqual(([h.fn.__name__ for h in entry.required], [h.fn.__name__ for h in entry.optional]),
                         (["open"], ["load_documents"]), "the page load is required, the section's load optional")
        self.assertEqual(len(self.pages), 2, "registering twice adds nothing")

        @declare.page("/moving", nav="Funds")
        def moving():
            return kinds.record(title=DeclareDesk.name, sections=[kinds.section("A", vocab.text("a"))])

        with self.assertRaises(PageError) as no_title:
            declare.finalize()
        self.assertEqual(str(no_title.exception),
                         "Page `moving`: a page listed in the navigation needs a fixed title.\n"
                         "Give the kind `title=\"...\"`, or leave `nav=` out.", "a navigation page needs fixed words")
        declare._DECLARED.pop("/moving")

        @declare.page("/acting")
        def acting():
            return kinds.record(title="Acting", load=DeclareDesk.touch, sections=[kinds.section("A", vocab.text("a"))])

        with self.assertRaises(PageError) as not_a_load:
            declare.finalize()
        self.assertTrue(str(not_a_load.exception).startswith("Page `acting`: `load=` got "),
                        f"a handler that is not a load is refused as the page's load: {not_a_load.exception}")
        self.assertIn("Pass a handler declared with `@ui.load`, without arguments", str(not_a_load.exception),
                      "with what to pass")
        declare._DECLARED.pop("/acting")

        @declare.page("/doubled")
        def doubled():
            return kinds.record(title="Doubled", load=DeclareDesk.open,
                                sections=[kinds.section("A", vocab.text("a"), load=DeclareDesk.open)])

        with self.assertRaises(PageError) as same_load:
            declare.finalize()
        self.assertIn("is given as a load twice", str(same_load.exception), "one load is the page's or a section's")
        declare._DECLARED.pop("/doubled")

        @declare.page("/broken")
        def broken():
            return kinds.record(title="Broken", sections=[kinds.section(5)])

        with self.assertRaises(PageError) as raised:
            declare.finalize()
        self.assertTrue(str(raised.exception).startswith("Page `broken`: its function raised TypeError:"),
                        f"an error inside a page function is given the page's name: {raised.exception}")
        declare._DECLARED.pop("/broken")

        @declare.page("/raw")
        def raw():
            return rx.text("not a kind")

        with self.assertRaises(PageError) as not_a_kind:
            declare.finalize()
        self.assertEqual(str(not_a_kind.exception),
                         "Page `raw`: it returned Text.\nReturn a page kind: `ui.record(...)`, `ui.list(...)`.",
                         "a function that does not return a kind is told what to return")

    # -- 1.805 ---------------------------------------------------------
    def test_1_805_a_dashboard_starts_its_loads_on_mount_and_create_app_registers_the_pages(self) -> None:
        """
        Scenario 1.805: @ui.dashboard and create_app.
        Given: a dashboard function returning a record with a load, and lex-app's create_app.
        When:  the dashboard is called, and create_app's source is read.
        Then:  the dashboard returns the built page, a component, whose load starts when it
               mounts (a dashboard has no opener); it is not a link target; on a model it sits
               under @classmethod and receives the model; create_app loads the project's
               module, then registers the declared pages, before it adds the home page.
        """
        @declare.dashboard
        def main():
            return kinds.record(title="Portfolio", load=DeclareDesk.open, sections=[kinds.section("A", vocab.text("a"))])

        built = main()
        self.assertIsInstance(built, rx.Component, "a dashboard returns the built page")
        self.assertIn("on_mount", built.event_triggers, "its load starts when it mounts")
        self.assertIsNone(links.page_info(main), "a dashboard is not a page to link to")
        self.assertEqual(main.__name__, "main", "it keeps its name, for lex-app's structure module")

        class Model:
            """Stands for a project model with a dashboard hook."""

            @classmethod
            @declare.dashboard
            def reflex_main(cls):
                return kinds.record(title=cls.__name__, sections=[kinds.section("A", vocab.text("a"))])

        self.assertIsInstance(Model.reflex_main(), rx.Component, "a model's hook is a class method above @ui.dashboard")
        self.assertIn('rfdsI18n.t("Model", "Model"', str(Model.reflex_main()), "and the function receives the model")
        source = inspect.getsource(lex_app_module.create_app)
        self.assertLess(source.index("load_structure()"), source.index("declare.finalize()"),
                        "the project's module is loaded before its pages are registered")
        self.assertLess(source.index("declare.finalize()"), source.index("dashboard_page(structure)"),
                        "and the pages are registered before the app's pages are added")
