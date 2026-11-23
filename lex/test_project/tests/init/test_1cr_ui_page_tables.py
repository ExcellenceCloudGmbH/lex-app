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
        self.assertIn("name", built, "the named column is shown")
        with self.assertRaises(PageError) as wrong:
            tables.table([{"name": "a"}], columns=["name"]).build(CTX.at("table"))
        self.assertEqual(str(wrong.exception),
                         "Page `groups`, slot `table`: `ui.table` got list, not a model.\n"
                         "Pass a model class: `ui.table(Fund, columns=[...])`.")

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
        built = str(tables.table(Group, columns=[tables.column("name", label="Group")], open=group_page,
                                 empty="No groups yet.").build(CTX))
        self.assertIn("/groups/", built, "rows link to the page")
        self.assertIn('rfdsI18n.t("No groups yet.", "No groups yet."', built, "the empty sentence is a message")
        self.assertIn("Group", built, "the column's own label is used")

        def no_argument():
            """A page without a route argument."""

        no_argument.__ui_page__ = links.info_of("/groups", "no_argument")
        with self.assertRaises(PageError, msg="open= needs a page with exactly one argument"):
            tables.table(Group, columns=["name"], open=no_argument).build(CTX)
