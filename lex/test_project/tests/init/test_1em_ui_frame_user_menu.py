"""lex.ui's frame: entries of the user menu above "Sign out".

Intent: an application's user menu holds the user's own pages -- "Account", "Settings" -- above "Sign
out", each shown only to users who may open it. A project says so in `ui.frame(user_menu=[...])` with
`ui.link`s; who sees an entry is decided on the server, as for the navigation. A regression is a menu
that drops them, an entry below "Sign out", or an entry drawn for a user who may not open it.

Cluster 1em -- scenarios 1.1012-1.1013. Type: U.
Covers: lex/lex_app/reflex/ui/pages/frame.py (frame user_menu=, entries), lex/lex_app/reflex/shell.py
(lex_chrome, user_menu_items).
Run: python -m lex pytest lex/test_project/tests/init/test_1em_ui_frame_user_menu.py -v
"""

from __future__ import annotations

import asyncio

import pytest
from reflex.compiler.compiler import compile_page

from lex import ui
from lex.lex_app.reflex.ui.pages import declare
from lex.test_project.tests.init.test_1eb_ui_frame_nav import FrameCase
from lex.test_project.tests.init.test_1ej_ui_frame_decided import decided

pytestmark = pytest.mark.init


def _about(title):
    return ui.record(title=title, sections=[ui.section("About", ui.text("All about it."))])


class TestCluster01em_FrameUserMenu(FrameCase):
    """1.1012-1.1013: the user menu's entries, above Sign out, drawn on the server's answer."""

    def declare_pages(self):
        @ui.page("/orders", nav="Orders")
        def orders():
            return _about("Orders")

        @ui.page("/account")
        def account():
            return _about("Account")

        @ui.page("/settings", requires=ui.edit("lex_app.Setting"))
        def settings():
            return _about("Settings")

        return orders, account, settings

    # -- 1.1012 --------------------------------------------------------
    def test_1_1012_the_user_menu_lists_the_projects_entries_above_sign_out(self) -> None:
        """
        Scenario 1.1012: Account and Settings.
        Given: ui.frame(user_menu=[Account, Settings (a page that requires Setting:edit), Help (an
               outside address, with its own requires= and an icon)]).
        When:  a page is compiled, and the frame is decided for anna (who may edit settings and read
               the help) and carl.
        Then:  the user menu holds the three entries, in their order, above "Sign out"; Settings and
               Help are drawn on the server's answer -- anna's lists both, carl's neither; Account is
               drawn for everyone.
        """
        orders, account, settings = self.declare_pages()
        ui.frame(user_menu=[ui.link("Account", to=account), ui.link("Settings", to=settings),
                            ui.link("Help", href="https://help.example.com", icon="help",
                                    requires=ui.read("lex_app.Manual"))])
        declare.finalize()
        _, module = compile_page("/orders", self.page_named("/orders")())
        for expected in ('"/account"', '"/settings"', "https://help.example.com", "ui_frame_shown"):
            self.assertIn(expected, module, f"compiled: {expected}")
        places = [module.index(word) for word in ('"Account"', '"Settings"', '"Help"', '"Sign out"')]
        self.assertEqual(places, sorted(places), "in their order, above Sign out")
        anna = asyncio.run(decided(user="anna", grants=["lex_app.Setting:edit", "lex_app.Manual:read"]))
        self.assertEqual(sorted(anna), ["user_menu#1", "user_menu#2"])
        self.assertEqual(asyncio.run(decided(user="carl")), [])

    # -- 1.1013 --------------------------------------------------------
    def test_1_1013_the_user_menu_takes_links(self) -> None:
        """
        Scenario 1.1013: user_menu= written wrongly.
        Given: a button, a text, a link with badge=, and a link with a state value.
        When:  each is given as ui.frame(user_menu=).
        Then:  each is refused with what to write.
        """
        orders, account, settings = self.declare_pages()
        from lex.lex_app.reflex.ui.state import State

        for wrong, says in ((ui.button("Account", to=account), "ui.link"), (ui.text("Hello"), "ui.link"),
                            (ui.link("Account", to=account, badge=lambda user: 1), "badge="),
                            (ui.link("Order", to=orders, region=State.ui_visit), "fixed")):
            with self.assertRaises((TypeError, ValueError), msg=f"{wrong!r}") as caught:
                ui.frame(user_menu=[wrong])
            self.assertIn(says, str(caught.exception))
