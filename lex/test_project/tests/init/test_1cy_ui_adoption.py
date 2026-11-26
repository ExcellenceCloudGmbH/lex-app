"""lex.ui adoption: one lex.ui part inside a raw Reflex page; the user's permissions in a body.

Intent: a project written in raw Reflex moves to lex.ui one table at a time, so a lex.ui part has
to work inside a page that is not a ``ui.page``; and a project whose own logic takes the user's
Keycloak permission list has to reach it from a plain handler body. A regression is a project
that must convert a whole page (its chrome, gates and error page with it) before it can use one
``@ui.rows`` table, or a handler that cannot call the project's own logic.

Cluster 1cy -- scenarios 1.821-1.822, 1.1128. Type: I.
Covers: lex/lex_app/reflex/ui/pages/declare.py (embed), lex/lex_app/reflex/ui/context.py
(User.permissions), lex/lex_app/reflex/ui/pages/tables.py (the caption of a table given no name).
Run: python -m lex pytest lex/test_project/tests/init/test_1cy_ui_adoption.py -v
"""

from __future__ import annotations

import pytest
import reflex as rx
from django.test import SimpleTestCase

from lex import ui
from lex.lex_app.reflex.ui import testing

pytestmark = pytest.mark.init


class AdoptionState(ui.State):
    """A state whose rows and action a raw page uses."""

    granted: list[str] = []

    @ui.rows
    def items(self):
        return [{"id": 1, "name": "One", "by": ",".join(sorted(p["rsname"] for p in self.user.permissions))}]

    @ui.action
    def remember(self):
        self.granted = [p["rsname"] for p in self.user.permissions] + [f"tab:{self.tab_id}"]


class PortfolioState(ui.State):
    """A state whose handler is not called rows: its rows are named by the state and the handler."""

    @ui.rows
    def holdings(self):
        return [{"id": 1, "name": "One"}]


class TestCluster01cy_Adoption(SimpleTestCase):
    """1.821-1.822: ui.embed; self.user.permissions."""

    # -- 1.821 ---------------------------------------------------------
    def test_1_821_a_lex_ui_table_sits_inside_a_raw_page(self) -> None:
        """
        Scenario 1.821: ui.embed.
        Given: a raw Reflex page that holds one lex.ui table, and things that are not one part.
        When:  the table is embedded and the page is built.
        Then:  the table is RFDS's data table over its generated state, inside the raw page, captioned
               with the name given; a whole
               page kind and something that is not a lex.ui part are refused with what to write; a
               mistake in the embedded part names it by the name given.
        """
        page = rx.box(rx.heading("A page in raw Reflex"),
                      ui.embed(ui.table(AdoptionState.items, columns=["name"], search=True), name="items table"))
        built = str(page)
        self.assertIn("rfds-data-table", built, "the embedded table is RFDS's data table")
        self.assertIn("A page in raw Reflex", built, "inside the raw page")
        self.assertIn('rfdsI18n.t("items table", "items table"', built, "the name given is the table's caption")
        with self.assertRaises(TypeError) as kind:
            ui.embed(ui.record(title="Whole", sections=[ui.section("A", ui.text("a"))]))
        self.assertIn("a whole page kind goes on a @ui.page or a @ui.dashboard", str(kind.exception),
                      "a page kind is refused with where it goes")
        with self.assertRaises(TypeError, msg="a raw component is not a lex.ui part"):
            ui.embed(rx.text("raw"))
        with self.assertRaises(ui.PageError) as wrong:
            ui.embed(ui.table(AdoptionState.items, columns=["name"], where={"x": 1}), name="items table")
        self.assertTrue(str(wrong.exception).startswith("Page `items table`: "),
                        f"a mistake names the embedded part: {wrong.exception}")

    # -- 1.822 ---------------------------------------------------------
    def test_1_822_a_handler_body_reads_the_users_permission_list(self) -> None:
        """
        Scenario 1.822: self.user.permissions.
        Given: a rows handler and an action that read self.user.permissions, and a user with two grants.
        When:  they run as that user; and the permissions are read outside a body.
        Then:  both see the user's Keycloak permissions as Keycloak lists them, and the action sees
               the browser tab it runs for (self.tab_id); outside a handler body reading the
               permissions is refused.
        """
        grants = ["auth.Group:read", "auth.User:read"]
        (row,) = testing.rows(AdoptionState.items, grants=grants)
        self.assertEqual(row["by"], "auth.Group,auth.User", "a rows handler reads the user's permission list")
        ran = testing.run(AdoptionState, "remember", grants=grants)
        self.assertEqual(sorted(ran.fields["granted"]), ["auth.Group", "auth.User", "tab:tab1"],
                         "so does an action, and self.tab_id is the browser tab it runs for")
        from lex.lex_app.reflex.ui.context import User

        with self.assertRaises(RuntimeError, msg="only inside a handler body"):
            User(id="x").permissions

    # -- 1.1128 --------------------------------------------------------
    def test_1_1128_an_embedded_table_given_no_name_shows_no_made_up_caption(self) -> None:
        """
        Scenario 1.1128: ui.embed without name=.
        Given: a table embedded without a name, over a handler called rows and over one called
               holdings; a table embedded with a name; a section embedded without a heading.
        When:  they are built.
        Then:  a table given no name shows no caption -- never a default word such as "Embedded"
               -- and is named for a screen reader by its rows (its state's name, and the
               handler's unless it is called rows), RFDS's caption kept for screen readers only;
               a name given is the caption, shown; a section's title is the caption of its table,
               shown, as the section has no heading of its own there.
        """
        hidden = 'className:"rfds-data-table-caption rfds-sr-only"'
        bare = str(ui.embed(ui.table(AdoptionState.items, columns=["name"])))
        self.assertNotIn("Embedded", bare, "never a default word")
        self.assertIn(hidden, bare, "no caption shown")
        self.assertIn('rfdsI18n.t("Adoption items", "Adoption items"', bare, "named by its rows for a screen reader")
        held = str(ui.embed(ui.table(PortfolioState.holdings, columns=["name"])))
        self.assertIn('rfdsI18n.t("Portfolio holdings", "Portfolio holdings"', held, "the state's name and the handler's")
        named = str(ui.embed(ui.table(AdoptionState.items, columns=["name"]), name="Items"))
        self.assertNotIn(hidden, named, "a name given is shown")
        self.assertIn('rfdsI18n.t("Items", "Items"', named, "as the caption")
        section = str(ui.embed(ui.section("Recent", ui.table(AdoptionState.items, columns=["name"]))))
        self.assertNotIn(hidden, section, "a section without a heading shows its title as the caption")
        self.assertIn('rfdsI18n.t("Recent", "Recent"', section, "its title")
