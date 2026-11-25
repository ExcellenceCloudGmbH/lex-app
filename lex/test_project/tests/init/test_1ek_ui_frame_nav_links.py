"""lex.ui's frame: navigation beyond the declared pages -- sections of links, counts, per-user entries, a foot.

Intent: an application's navigation holds more than its declared pages: a titled section of links (a
search page, an outside wiki, a raw page), a count beside an entry ("Waiting for you  3"), a section
whose entries depend on the user (favourites, topics the user may see), and a link at its foot (the
version, help). A project says so in `ui.frame(nav={...}, nav_footer=)` with lex.ui's words
(an area's list of `ui.link`s, or a function of the user); values that depend on the user are functions of the user, called on the
server when a page opens. A regression is a lost section, a count computed in the browser or shown to
the wrong user, a per-user entry another user sees, a link the user may not open, or a frame word that
silently does nothing in a page.

Cluster 1ek -- scenarios 1.1004-1.1008, 1.1110. Type: U.
Covers: lex/lex_app/reflex/ui/pages/frame.py (frame nav=/nav_footer=, entries, decide),
lex/lex_app/reflex/shell.py (nav_layout, nav_sections, lex_chrome), lex/lex_app/reflex/ui/pages/vocab.py
(link icon=/badge=/requires=), lex/lex_app/reflex/ui/pages/declare.py (page badge=),
lex/lex_app/reflex/ui/pages/links.py (reflex_page badge=), lex/lex_app/reflex/ui/state.py (ui_frame_open).
Run: python -m lex pytest lex/test_project/tests/init/test_1ek_ui_frame_nav_links.py -v
"""

from __future__ import annotations

import asyncio
import threading
import time
from unittest.mock import patch

import pytest
from reflex.compiler.compiler import compile_page

from lex import ui
from lex.lex_app.reflex import shell
from lex.lex_app.reflex.ui.pages import declare
from lex.lex_app.reflex.ui.pages.parts import Build, PageError
from lex.test_project.tests.init.test_1eb_ui_frame_nav import FrameCase

pytestmark = pytest.mark.init

CALLED: list = []


def _about(title):
    return ui.record(title=title, sections=[ui.section("About", ui.text("All about it."))])


def waiting(user):
    """Requests waiting for the user: anna has 3, ben's count fails, carl has none."""
    CALLED.append(("waiting", user.id, threading.current_thread().name))
    if user.id == "ben":
        raise RuntimeError("the request store is away")
    return {"anna": 3}.get(user.id, 0)


def drafts(user):
    return "many" if user.id == "carl" else 1


@ui.rule
def team_of(user, params):
    CALLED.append(("team_of", user.id, dict(params._values)))
    return user.id == "anna" and params.get("key") == "7"


async def decided(*, user: str, grants=()) -> dict:
    """The frame the server decides for ``user`` when a page opens in a new tab."""
    from lex.lex_app.reflex.ui import testing
    from lex.lex_app.reflex.ui.state import State

    async with testing.Harness(user=user, grants=grants) as harness:
        await harness.open_tab("/orders")
        before = (await harness._state(State)).ui_frame_shown
        await (await harness.send(State, "ui_frame_open"))
        deadline = time.monotonic() + 5
        state = await harness._state(State)
        while not (state.ui_frame_counts or state.ui_frame_links or state.ui_frame_shown) \
                and time.monotonic() < deadline:
            await asyncio.sleep(0.01)
            state = await harness._state(State)
        await asyncio.sleep(0.05)
        state = await harness._state(State)
        del before
        return {"shown": sorted(state.ui_frame_shown), "counts": dict(state.ui_frame_counts),
                "links": {key: [dict(one) for one in value] for key, value in dict(state.ui_frame_links).items()}}


class TestCluster01ek_FrameNavLinks(FrameCase):
    """1.1004-1.1008: sections of links, counts, per-user sections, guarded links, the foot."""

    def setUp(self) -> None:
        super().setUp()
        CALLED.clear()

    def declare_pages(self):
        @ui.page("/orders", nav="Orders")
        def orders():
            return _about("Orders")

        @ui.page("/search", params=["q"])
        def search():
            return _about("Search")

        @ui.page("/orders/[key]")
        def order():
            return _about("Order")

        @ui.page("/approvals", nav="Requests", badge=waiting)
        def approvals():
            return _about("Approvals")

        return orders, search, order, approvals

    # -- 1.1004 --------------------------------------------------------
    def test_1_1004_a_section_of_links_joins_the_navigation(self) -> None:
        """
        Scenario 1.1004: sections of links.
        Given: ui.frame(nav={the area Orders with a link to the search page after its pages, an area,
               an area Help with an outside address, a raw page and a record page's address}) (review 2:
               nav= is a dict; ui.section means a part of a page only).
        When:  lex-app registers the pages and builds the navigation, and a page is compiled.
        Then:  the sections come in nav='s order; the Orders section holds its declared page, then its
               link with its icon; Help holds the outside address, the raw page and the record page's
               address with its value; the compiled page holds them; the old list of sections, a link
               with a state value, an area of anything but ui.links, a ui.section as an area's content,
               and links and a function together are refused with what to write.
        """
        orders, search, order, approvals = self.declare_pages()
        legacy = ui.reflex_page("/legacy")
        ui.frame(nav={"Orders": [ui.link("Search orders", to=search, icon="search")], "Requests": [],
                      "Help": [ui.link("All workflows", href="https://wiki.example.com/workflows"),
                               ui.link("Legacy export", to=legacy), ui.link("Order 7", to=order, key=7)]})
        declare.finalize()
        sections = shell.nav_sections()
        self.assertEqual([s.label_default for s in sections], [None, "Orders", "Requests", "Help"])
        self.assertEqual([(i.href, i.label_default, i.icon) for i in sections[1].items],
                         [("/orders", "Orders", None), ("/search", "Search orders", "search")])
        self.assertEqual([i.href for i in sections[3].items],
                         ["https://wiki.example.com/workflows", "/legacy", "/orders/7"])
        _, module = compile_page("/orders", self.page_named("/orders")())
        for expected in ("Search orders", "https://wiki.example.com/workflows", '"/orders/7"', "All workflows"):
            self.assertIn(expected, module, f"compiled: {expected}")

        from lex.lex_app.reflex.ui.state import State

        for nav, says in (([ui.section("Orders", ui.link("Search", to=search))], "ui.section is a titled part"),
                          ({"Help": [ui.link("Order", to=order, key=State.ui_visit)]}, "fixed"),
                          ({"Help": [ui.text("Ask us")]}, "ui.link"),
                          ({"Help": ui.section("Help", ui.link("Search", to=search))}, "write"),
                          ({"Help": [waiting, ui.link("Search", to=search)]}, "ui.link")):
            with self.assertRaises((TypeError, ValueError), msg=f"{nav!r}") as caught:
                ui.frame(nav=nav)
            self.assertIn(says, str(caught.exception))

    # -- 1.1005 --------------------------------------------------------
    def test_1_1005_a_count_beside_an_entry_is_a_function_of_the_user(self) -> None:
        """
        Scenario 1.1005: badges.
        Given: a listed page with badge=waiting, a section link with badge=drafts, a raw page with
               badge=waiting.
        When:  a page is compiled, then the frame is decided for anna, ben and carl.
        Then:  the compiled entries show RFDS's navigation badge from the server's counts; anna's counts
               are 3 and 1, computed on worker threads, each function once per event however many
               entries name it; ben's failing function is logged and leaves no count; carl's wrong answer
               ("many") is logged and leaves no count, his 0 shows no badge; badge= on a page without
               nav=, and a function that takes no user, are refused with what to write.
        """
        orders, search, order, approvals = self.declare_pages()
        ui.reflex_page("/export", nav="Requests", title="Exports", badge=waiting)
        ui.frame(nav={"Orders": [], "Requests": [ui.link("Drafts", to=search, badge=drafts)]})
        declare.finalize()
        _, module = compile_page("/orders", self.page_named("/orders")())
        for expected in ("ui_frame_counts", "rfds-side-nav-badge"):
            self.assertIn(expected, module, f"compiled: {expected}")
        anna = asyncio.run(decided(user="anna"))
        self.assertEqual(sorted(anna["counts"].values()), [1, 3, 3], "the page's, the raw page's, the link's")
        self.assertEqual(len([c for c in CALLED if c[0] == "waiting"]), 1, "one call however many entries")
        self.assertTrue(CALLED[0][2].startswith("lex-ui-"), "on a worker thread")
        with self.assertLogs("lex.lex_app.reflex.ui.pages.frame", level="ERROR") as logged:
            ben = asyncio.run(decided(user="ben"))
        self.assertEqual(sorted(ben["counts"].values()), [1], "the failing function leaves no count")
        self.assertIn("waiting", "\n".join(logged.output))
        with self.assertLogs("lex.lex_app.reflex.ui.pages.frame", level="ERROR") as logged:
            carl = asyncio.run(decided(user="carl"))
        self.assertEqual(sorted(carl["counts"].values()), [0, 0], "0 is a count; 'many' is not")
        self.assertIn("drafts", "\n".join(logged.output))
        with self.assertRaises(ValueError) as caught:
            ui.page("/lonely", badge=waiting)
        self.assertIn("nav=", str(caught.exception))
        with self.assertRaises(TypeError) as caught:
            ui.frame(nav={"Requests": [ui.link("Drafts", to=search, badge=lambda: 3)]})
        self.assertIn("def ", str(caught.exception), "it says how to write a function of the user")

    # -- 1.1006 --------------------------------------------------------
    def test_1_1006_a_per_user_section_lists_what_its_function_returns(self) -> None:
        """
        Scenario 1.1006: a section whose entries come from a function of the user.
        Given: nav={..., "Favourites": favourites} where favourites(user) returns ui.links to a record
               page, an outside address and a forged link with requires=.
        When:  a page is compiled, and the frame is decided for anna and carl.
        Then:  the compiled navigation repeats the server's links under the heading, hidden while there
               are none; anna's entries are the page's address with its value and the outside address,
               each with its words as they are; the link with requires= is left out and logged; carl's
               function returns nothing: no entries.
        """
        orders, search, order, approvals = self.declare_pages()

        def favourites(user):
            if user.id != "anna":
                return []
            return [ui.link("Order 7 (Müller)", to=order, key=7), ui.link("Wiki", href="https://wiki.example.com"),
                    ui.link("Secret", to=search, requires=ui.read("lex_app.Order"))]

        ui.frame(nav={"Orders": [], "Requests": [], "Favourites": favourites})
        declare.finalize()
        _, module = compile_page("/orders", self.page_named("/orders")())
        for expected in ("ui_frame_links", "Favourites", "rfds-side-nav-link"):
            self.assertIn(expected, module, f"compiled: {expected}")
        with self.assertLogs("lex.lex_app.reflex.ui.pages.frame", level="ERROR") as logged:
            anna = asyncio.run(decided(user="anna"))
        [entries] = anna["links"].values()
        self.assertEqual([(e["label"], e["href"], e["outside"]) for e in entries],
                         [("Order 7 (Müller)", "/orders/7", ""), ("Wiki", "https://wiki.example.com", "yes")])
        self.assertIn("requires=", "\n".join(logged.output))
        carl = asyncio.run(decided(user="carl"))
        self.assertEqual(list(carl["links"].values()), [[]], "nothing for carl")

    # -- 1.1007 --------------------------------------------------------
    def test_1_1007_a_link_the_user_may_not_open_is_not_drawn(self) -> None:
        """
        Scenario 1.1007: guarded links.
        Given: section links with requires= a permission, to a page that requires a permission, and to a
               record page guarded by a rule asked with the link's value.
        When:  the frame is decided for anna (who holds the permission and passes the rule) and carl.
        Then:  anna's answer lists all three links; carl's none; the rule was given the link's values.
        """
        orders, search, order, approvals = self.declare_pages()

        @ui.page("/reports", requires=ui.read("lex_app.Report"))
        def reports():
            return _about("Reports")

        @ui.page("/teams/[key]", requires=team_of)
        def team():
            return _about("Team")

        ui.frame(nav={"Orders": [], "Requests": [],
                      "More": [ui.link("Search", to=search, requires=ui.read("lex_app.Report")),
                               ui.link("Reports", to=reports), ui.link("Team 7", to=team, key=7)]})
        declare.finalize()
        anna = asyncio.run(decided(user="anna", grants=["lex_app.Report:read"]))
        self.assertEqual(len(anna["shown"]), 3, f"all three for anna: {anna['shown']}")
        self.assertIn(("team_of", "anna", {"key": "7"}), CALLED, "the rule asked with the link's values")
        self.assertEqual(asyncio.run(decided(user="carl"))["shown"], [])

    # -- 1.1008 --------------------------------------------------------
    def test_1_1008_a_link_at_the_foot_and_frame_words_in_a_page(self) -> None:
        """
        Scenario 1.1008: the foot of the navigation; the frame's words in a page.
        Given: ui.frame(nav_footer=ui.link("Version 2.4.1", href=...)), then a ui.text; then links
               with badge=, requires= and icon= placed in a page.
        When:  the page is compiled; the links are built in a page.
        Then:  the foot is RFDS's navigation footer holding the link, or the text; another part at the
               foot is refused; in a page each frame word is refused with what to write instead.
        """
        self.declare_pages()
        ui.frame(nav_footer=ui.link("Version 2.4.1", href="https://example.com/changes"))
        declare.finalize()
        _, module = compile_page("/orders", self.page_named("/orders")())
        for expected in ("rfds-side-nav-footer", "Version 2.4.1", "https://example.com/changes"):
            self.assertIn(expected, module, f"compiled: {expected}")
        ui.frame(nav_footer=ui.text("Version 2.4.1"))
        self.assertIn("Version 2.4.1", compile_page("/orders", self.page_named("/orders")())[1])
        with self.assertRaises(TypeError) as caught:
            ui.frame(nav_footer=ui.button("Help", to=self.page_named("/orders")))
        self.assertIn("nav_footer=", str(caught.exception))
        for kwargs, says in (({"badge": waiting}, "badge="), ({"requires": ui.read("lex_app.Order")}, "visible="),
                             ({"icon": "search"}, "icon=")):
            with self.assertRaises(PageError, msg=f"{kwargs}") as caught:
                ui.link("Open", href="https://example.com", **kwargs).build(Build(page="p", route="/p"))
            self.assertIn(says, str(caught.exception))



class TestCluster01ek_UserLinksAreNoArea(FrameCase):
    """1.1110: the user's own links never take the place of an area's pages."""

    # -- 1.1110 --------------------------------------------------------
    def test_1_1110_a_per_user_section_named_like_an_area_with_pages_is_refused(self) -> None:
        """
        Scenario 1.1110: a function of the user under the name of an area that has pages (review 2, I-2:
        the area's pages silently left the navigation).
        Given: nav={"Orders": favourites} -- a function of the user -- and a page in Orders: declared
               before the frame, after it, and a raw page in it.
        When:  each is declared.
        Then:  each is refused, naming the page, the area and the function, and saying to give one of the
               two sections another name; a per-user section with a name of its own is drawn as before.
        """
        def favourites(user):
            return [ui.link("Wiki", href="https://wiki.example.com")]

        @ui.page("/orders", nav="Orders")
        def orders():
            return _about("Orders")

        with self.assertRaises(ValueError) as before:
            ui.frame(nav={"Orders": favourites})
        message = str(before.exception)
        for words in ("/orders", "'Orders'", "favourites", "another name"):
            self.assertIn(words, message, f"the refusal says {words!r}")
        with patch.object(shell, "_NAV", []):
            ui.frame(nav={"Orders": favourites})
            with self.assertRaises(ValueError) as after:
                @ui.page("/orders/new", nav="Orders")
                def new_order():
                    return _about("New order")
            self.assertIn("/orders/new", str(after.exception))
            with self.assertRaises(ValueError) as raw:
                ui.reflex_page("/orders/export", nav="Orders", title="Export")
            self.assertIn("/orders/export", str(raw.exception))
        ui.frame(nav={"Orders": [], "Orders (mine)": favourites})
        declare.finalize()
        layout = shell.nav_layout()
        self.assertEqual([getattr(one, "label_default", None) or getattr(one, "title", None) for one in layout[1:]],
                         ["Orders", "Orders (mine)"], "the area keeps its pages; the user's links follow")
