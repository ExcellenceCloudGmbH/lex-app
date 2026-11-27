"""lex.ui: a button that says why it cannot be pressed; a download the browser can place.

Intent: an action is often unavailable for one row only (an order already posted, a file not yet
made). Hiding the button leaves the user guessing; a button that stays, greyed, and says why
answers the question in place and is still reached by the keyboard. A file built from data is
named, and its name tells the browser what it is. A regression is a button that vanishes or
stays pressable for a row it cannot act on, a yes/no value shown as a "reason", or a PDF the
browser treats as text.

Cluster 1df -- scenarios 1.830-1.831. Type: U.
Covers: lex/lex_app/reflex/ui/pages/vocab.py (button disabled=), lex/lex_app/reflex/ui/results.py
(download).
Run: python -m lex pytest lex/test_project/tests/init/test_1df_ui_button_disabled.py -v
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from django.test import SimpleTestCase

from lex import ui
from lex.lex_app.reflex.ui import results
from lex.lex_app.reflex.ui.pages.parts import Build, PageError

pytestmark = pytest.mark.init


class InvoicesState(ui.State):
    """Rows whose action may be unavailable."""

    posted: bool = False

    @ui.rows
    def invoices(self):
        return [{"id": 1, "name": "Invoice one", "blocked": ""},
                {"id": 2, "name": "Invoice two", "blocked": "Already posted."}]

    @ui.action
    def post(self, key: str):
        return None


class TestCluster01df_ButtonDisabled(SimpleTestCase):
    """1.830-1.831: ui.button(disabled=); ui.download's file type."""

    # -- 1.830 ---------------------------------------------------------
    def test_1_830_a_button_says_why_it_cannot_be_pressed(self) -> None:
        """
        Scenario 1.830: a disabled action with its reason.
        Given: a table whose rows each carry why they cannot be posted ("" when they can), and a
               button blocked for a fixed reason.
        When:  they are built; and a button is given a yes/no field, or True, as its reason.
        Then:  each row's button is blocked exactly when its row has a reason, stays focusable and
               shows that reason; the fixed reason is shown; a yes/no field and True are refused
               with what to give instead.
        """
        table = ui.embed(ui.table(InvoicesState.invoices, columns=["name"],
                                  row_actions=lambda row: [ui.button("Post", InvoicesState.post, row["id"],
                                                                     disabled=row["blocked"])]),
                         name="invoices")
        built = str(table)
        self.assertIn('content:(!((r_rx_state_?.["blocked"]?.valueOf?.() === ""?.valueOf?.())) ? r_rx_state_?.["blocked"] : null)',
                      built, "each row's own reason, and none where it is empty")
        self.assertIn('"aria-disabled":(pyOr(!((r_rx_state_?.["blocked"]?.valueOf?.() === ""?.valueOf?.()))', built,
                      "blocked exactly when its row has a reason, and still focusable")
        fixed = str(ui.button("Post all", InvoicesState.post, "all", disabled="Nothing to post yet.").build(Build(page="a")))
        self.assertIn('content:rfdsI18n.t("Nothing to post yet.", "Nothing to post yet."', fixed, "a fixed reason is shown")
        with self.assertRaises(PageError) as flag:
            ui.button("Post all", InvoicesState.post, "all", disabled=InvoicesState.posted).build(Build(page="a"))
        self.assertIn("Give the reason as text", str(flag.exception), "a yes/no field is no reason")
        with self.assertRaises(TypeError):
            ui.button("Post all", InvoicesState.post, disabled=True)

    # -- 1.831 ---------------------------------------------------------
    def test_1_831_a_download_tells_the_browser_what_the_file_is(self) -> None:
        """
        Scenario 1.831: a file built from data.
        Given: a PDF's bytes and a workbook's bytes handed over with their file names, and a file by URL.
        When:  they become browser events.
        Then:  each carries the media type its name says; a download by URL carries none.
        """
        pdf = ui.download(data=b"%PDF-1.7", filename="invoice.pdf")
        book = ui.download(data=b"PK", filename="invoices.xlsx")
        self.assertEqual(pdf.media_type, "application/pdf")
        self.assertIn("spreadsheetml", book.media_type)
        self.assertIsNone(ui.download(url="/files/a.csv", filename="a.csv").media_type)
        with patch.object(results, "RECORDER", None):
            [event] = results.to_events([pdf])
        self.assertIn("application/pdf", str(event), "the browser is told what the file is")
