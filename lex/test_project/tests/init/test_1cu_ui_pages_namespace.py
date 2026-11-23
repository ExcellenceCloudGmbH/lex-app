"""lex.ui pages: one import for the page kinds and the vocabulary; two pages written end to end.

Intent: a project writes `from lex import ui` and finds the page decorators, the kinds and the
vocabulary beside the handler layer -- and nothing that takes a styling argument. Two pages
written only with those names, a list that opens a record, build, register and load exactly as
a project's would. A regression is a name missing from the namespace, a styling argument
creeping into the vocabulary, or a page that builds but never loads.

Cluster 1cu -- scenarios 1.806-1.807. Type: I.
Covers: lex/ui.py, lex/lex_app/reflex/ui/pages/.
Run: python -m lex pytest lex/test_project/tests/init/test_1cu_ui_pages_namespace.py -v
"""

from __future__ import annotations

import asyncio
import inspect
from unittest.mock import patch

import pytest
import reflex as rx
from django.contrib.auth.models import Group
from django.test import SimpleTestCase

from lex import ui
from lex.lex_app.reflex import shell
from lex.lex_app.reflex.ui import handlers, testing
from lex.lex_app.reflex.ui import state as ui_state
from lex.lex_app.reflex.ui.pages import declare
from lex.lex_app.reflex.ui.pages.parts import Build

pytestmark = pytest.mark.init

PAGE_NAMES = ("page", "dashboard", "record", "list", "section", "text", "fact", "status", "message", "table",
              "column", "button", "link", "each", "switch", "custom_block", "loaded", "PageError")


class ExampleFund(ui.State):
    """The state of the example's record page."""

    name: str = ""
    members: int = 0
    notes: list[str] = []

    @ui.load(requires=ui.read(Group))
    def open(self):
        group = Group.objects.filter(pk=self.params.get("key", "0")).first()
        if group is None:
            raise ui.Refuse("This group does not exist.")
        self.name, self.members = group.name, group.user_set.count()

    @ui.load
    def load_notes(self):
        self.notes = [f"{self.name} was opened by {self.user.name}"]     # reads what the page load wrote


class TestCluster01cu_PagesNamespace(SimpleTestCase):
    """1.806-1.807: the namespace; a list and a record page, end to end."""

    databases = {"default"}

    # -- 1.806 ---------------------------------------------------------
    def test_1_806_one_import_gives_the_page_kinds_and_the_vocabulary(self) -> None:
        """
        Scenario 1.806: lex.ui.
        Given: `from lex import ui`.
        When:  the names of this piece are looked up.
        Then:  each exists and is listed in __all__ -- except ui.list, the page kind, which a
               star import must not put in place of Python's list; no component or kind takes
               a styling argument.
        """
        for name in PAGE_NAMES:
            with self.subTest(name=name):
                self.assertTrue(hasattr(ui, name), f"ui.{name} exists")
                if name != "list":
                    self.assertIn(name, ui.__all__, f"ui.{name} is listed")
        self.assertEqual(ui.list.__name__, "list_", "ui.list is the page kind")
        self.assertNotIn("list", ui.__all__, "`from lex.ui import *` does not hide Python's list")
        styling = {"class_name", "style", "props", "gap", "width", "padding", "margin", "align", "justify"}
        for name in ("record", "list", "section", "text", "fact", "status", "message", "table", "column", "button",
                     "link", "each", "switch", "custom_block"):
            with self.subTest(component=name):
                self.assertFalse(set(inspect.signature(getattr(ui, name)).parameters) & styling,
                                 f"ui.{name} takes no styling argument")

    # -- 1.807 ---------------------------------------------------------
    def test_1_807_a_list_and_a_record_page_build_register_and_load(self) -> None:
        """
        Scenario 1.807: two pages, as a project writes them.
        Given: a list page over a model whose rows open a record page; the record page has a
               page load that reads the database (it declares what it requires), a section that
               loads on its own, a fact, a status shown only to editors, and an action.
        When:  lex-app registers them, builds them, and the record page is opened by a user
               with the grant.
        Then:  both build without one raw component in the page functions; the list links its
               rows to the record page; opening the record page runs the page load and then the
               section's load, both end loaded, and the state holds what they read.
        """
        for target in (patch.dict(declare._DECLARED, clear=True), patch.dict(handlers._PAGES),
                       patch.object(shell, "_NAV", []), patch.object(rx, "page", lambda **kwargs: (lambda fn: fn)),
                       patch.object(ui_state, "_page_permitted", return_value=True)):
            target.start()
            self.addCleanup(target.stop)

        @ui.page("/groups", nav="Groups")
        def groups():
            return ui.list(title="Groups", table=ui.table(Group, columns=["name"], open=group, empty="No groups yet."))

        @ui.page("/groups/[key]", requires=ui.read(Group))
        def group():
            return ui.record(
                title=ExampleFund.name,
                load=ExampleFund.open,
                status=ui.status("Editable", tone="info", visible=ui.edit(Group)),
                facts=[ui.fact("Members", ExampleFund.members, kind="number")],
                sections=[ui.section("Notes", ui.each(ExampleFund.notes, lambda note: ui.text(note)),
                                     load=ExampleFund.load_notes)],
                actions=[ui.button("All groups", to=groups)],
            )

        declare.finalize()
        listing = str(groups().build(Build(page="groups", route="/groups")))
        self.assertIn("/groups/", listing, "the list's rows open the record page")
        record = str(group().build(Build(page="group", route="/groups/[key]")))
        self.assertIn('rfdsI18n.t("Members", "Members"', record, "the record page builds")
        for function in (groups, group):
            source = inspect.getsource(function)
            self.assertNotIn("rx.", source, f"{function.__name__} uses no raw Reflex component")
            self.assertNotIn("rfds.", source, f"{function.__name__} uses no raw RFDS component")

        made, created = Group.objects.get_or_create(name="Example group")
        self.addCleanup(made.delete)

        async def scenario():
            async with testing.Harness(user="ada", grants=["auth.Group:read"], params={"key": str(made.pk)}) as h:
                await h.open_tab(f"/groups/{made.pk}")
                opener = await h.send(ui.State, "ui_open", page="/groups/[key]")
                await opener
                for child in list(opener.children):
                    await child
                    for grandchild in list(child.children):
                        await grandchild
                return await h.fields(ui.State), await h.fields(ExampleFund)

        status, fields = asyncio.run(scenario())
        self.assertEqual(status["ui_loaded"], {"ExampleFund.open": True, "ExampleFund.load_notes": True},
                         "the page load and then the section's load both finished")
        self.assertEqual(status["ui_failed"], {}, "nothing failed")
        self.assertEqual((fields["name"], fields["members"]), ("Example group", 0), "the state holds what the page read")
        self.assertEqual(fields["notes"], ["Example group was opened by ada"],
                         "the section's load ran after the page's: it read the name the page load wrote")
