"""lex.ui pages: a record page with more than four tables is refused in lex.ui's words, with what to do.

Intent: RFDS's object page refuses more than four tables (`MAX_GRID_TABLES`), with tabs too; its own
sentence suggests tab navigation, which counts the same tables. A record page of a rich record (an
order with lines, invoices, payments, shipments, notes) hits the limit, and the author read RFDS's
sentence naming a navigation mode that does not help. lex.ui now counts the tables of a record page's
sections first and refuses in its own words: how many, in which sections, that tabs do not lift it, and
the way that does -- a page of their own, led to from a section by a link. The limit is RFDS's and
stays. A regression is a page past RFDS's rule accepted, or a refusal that points nowhere.

Cluster 1fa -- scenario 1.1037. Type: U.
Covers: lex/lex_app/reflex/ui/pages/kinds.py (Record._at_most_tables).
Run: python -m lex pytest lex/test_project/tests/init/test_1fa_ui_record_table_limit.py -v
"""

from __future__ import annotations

import pytest
from django.test import SimpleTestCase
from reflex.compiler.compiler import compile_page

from lex import ui
from lex.lex_app.reflex.ui.pages.parts import Build, PageError

pytestmark = pytest.mark.init


class OrderState(ui.State):
    key: str = "1"

    @ui.rows
    def lines(self):
        return [{"id": 1, "name": "Line"}]


def _table():
    return ui.table(OrderState.lines, columns=["name"])


CTX = Build(page="order", route="/orders/[key]")


class TestCluster01fa_RecordTableLimit(SimpleTestCase):
    """1.1037: at most four tables on a record page, said with what to do."""

    # -- 1.1037 --------------------------------------------------------
    def test_1_1037_more_than_four_tables_is_refused_with_the_way_out(self) -> None:
        """
        Scenario 1.1037: a record page with four tables, then five (with and without tabs).
        Given: sections Lines (2 tables), Invoices, Payments, and then Shipments.
        When:  each page is built.
        Then:  four tables compile; five are refused before RFDS is asked, naming the count, the
               sections, that tabs=True does not lift it, and a page of their own led to by a link.
        """
        four = [ui.section("Lines", _table(), _table()), ui.section("Invoices", _table()),
                ui.section("Payments", _table())]
        compile_page("/orders/[key]", ui.record(title="Order", sections=four).build(CTX))
        five = [*four, ui.section("Shipments", _table())]
        for tabs in (False, True):
            with self.subTest(tabs=tabs), self.assertRaises(PageError) as refused:
                ui.record(title="Order", sections=five, tabs=tabs).build(CTX)
            message = str(refused.exception)
            for expected in ("slot `sections`", "5 tables (in `Lines` (2), `Invoices`, `Payments`, `Shipments`)",
                             "at most 4", "with tabs=True too", "a page of their own",
                             "ui.link(\"All invoices\", to=order_invoices"):
                self.assertIn(expected, message, expected)
            self.assertNotIn("use tab navigation", message, "never RFDS's pointer to tabs")
