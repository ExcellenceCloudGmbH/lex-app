"""lex.ui pages: a table over a model, paged on the server, whose rows open a page.

Intent: finding things is the commonest page. ui.table over a model is lex-app's server-paged
table through the API as the user -- the same component and look everywhere -- with the columns
named by field, a row that opens a page named by its function, and a sentence for "nothing here".
A regression is a table that reads all rows into the browser, a row link written as a string, or
an empty table with no words.

Cluster 1cr -- scenarios 1.797-1.798. Type: U.
Covers: lex/lex_app/reflex/ui/pages/tables.py (table, column).
Run: python -m lex pytest lex/test_project/tests/init/test_1cr_ui_page_tables.py -v
"""

from __future__ import annotations

import pytest
from django.contrib.auth.models import Group
from django.test import SimpleTestCase

from lex.lex_app.reflex.ui.pages import links, tables
from lex.lex_app.reflex.ui.pages.parts import Build, PageError

pytestmark = pytest.mark.init


def group_page():
    """Stands for a page declared with @ui.page("/groups/[key]")."""


group_page.__ui_page__ = links.info_of("/groups/[key]", "group_page")

CTX = Build(page="groups", route="/groups")


class TestCluster01cr_PageTables(SimpleTestCase):
    """1.797-1.798: the model table; opening a page from a row, the empty sentence, columns."""

    # -- 1.797 ---------------------------------------------------------
    def test_1_797_a_table_over_a_model_is_the_server_paged_table(self) -> None:
        """
        Scenario 1.797: ui.table(Model).
        Given: a model and the names of the columns to show.
        When:  the table is built.
        Then:  it is RFDS's data table over lex-app's paged table state for that model, showing
               the named columns under the model's own field labels; something that is not a
               model is refused with what to pass.
        """
        built = str(tables.table(Group, columns=["name"]).build(CTX))
        self.assertIn("rfds-data-table", built, "RFDS's data table")
        self.assertIn("page_rows", built, "rows come a page at a time from the table's state")
        self.assertIn('"Name"', built, "the named column is shown under the field's own label")
        self.assertIn("auth.Group:read", built, "the table is shown only to users who may read the model")
        self.assertNotIn("auth.Group:read", str(tables.table(Group, columns=["name"], visible=True).build(CTX)),
                         "unless visible= says otherwise")
        with self.assertRaises(PageError) as wrong:
            tables.table([{"name": "a"}], columns=["name"]).build(CTX.at("table"))
        self.assertEqual(str(wrong.exception),
                         "Page `groups`, slot `table`: `ui.table` takes a model class and reads its rows from the "
                         "server; it cannot show rows you hold yourself (a list, or a state value) yet.\n"
                         "Pass a model: `ui.table(Fund, columns=[...])`.", "the refusal says what a table can show")
        with self.assertRaises(PageError) as unknown:
            tables.table(Group, columns=["nmae"]).build(CTX.at("table"))
        self.assertEqual(str(unknown.exception),
                         "Page `groups`, slot `table`: Group has no column nmae.\n"
                         "Its columns are: id, name. A many-to-many field cannot be a column.",
                         "a wrong column name lists the model's columns")

    # -- 1.798 ---------------------------------------------------------
    def test_1_798_rows_open_a_page_an_empty_table_says_so_and_a_column_can_be_relabelled(self) -> None:
        """
        Scenario 1.798: open=, empty= and ui.column.
        Given: a table with open= a page, empty= a sentence, and a column with its own label.
        When:  it is built.
        Then:  each row links to the page's address with the row's key in place of the page's
               argument; the empty sentence is translated and shown by RFDS's empty state; the
               column carries the given label; open= a page without exactly one argument is
               refused.
        """
        built = str(tables.table(Group, columns=[tables.column("name", label="Team", kind="text")], open=group_page,
                                 empty="No groups yet.").build(CTX))
        self.assertIn('"/groups/"', built, "rows link to the page")
        self.assertIn("encodeURIComponent", built, "with the row's key encoded into the address")
        self.assertIn('rfdsI18n.t("No groups yet.", "No groups yet."', built, "the empty sentence is a message")
        self.assertIn('rfdsI18n.t("Team", "Team"', built,
                      "the column's own label is used, and translated like every other sentence")
        self.assertNotIn('"Name"', built, "in place of the field's label")
        for bad in (dict(kind="colour"), dict(label=""), dict(currency="EUR")):
            with self.subTest(bad=bad), self.assertRaises(ValueError, msg="a column is checked when it is declared"):
                tables.column("name", **bad)
        self.assertEqual(tables._RFDS_KIND["datetime"], "instant", "datetime is RFDS's instant")

        def no_argument():
            """A page without a route argument."""

        no_argument.__ui_page__ = links.info_of("/groups", "no_argument")
        with self.assertRaises(PageError) as no_key:
            tables.table(Group, columns=["name"], open=no_argument).build(CTX)
        self.assertEqual(str(no_key.exception),
                         "Page `groups`: `open=` needs a page with exactly one route argument, got 'no_argument'.\n"
                         "Declare the page as `@ui.page(\"/things/[key]\")`.", "open= needs a page with one argument")
