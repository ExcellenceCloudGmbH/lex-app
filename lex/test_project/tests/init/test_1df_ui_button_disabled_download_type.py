"""lex.ui: a button that says why it cannot be pressed; a download that says what it is; a body that
asks whether another job is running.

Intent: a row's action is often unavailable for that row only (a proof that expired, a file not
yet made). Hiding the button leaves the user guessing; a button that stays, greyed, and says why
answers the question in place, and is still reached by the keyboard. A download built from data
should tell the browser what it is (a PDF, a workbook), so it opens with the right application. A
regression is a button that vanishes or stays pressable for a row it cannot act on, or a file the
browser cannot place. Work that must not overlap another job used to keep a flag of its own,
which stayed set when the job failed; a body now asks lex.ui whether that job is running. A regression
is an overlap the body cannot see, or a "running" that outlives its run.

Cluster 1df -- scenarios 1.830-1.831. Type: U.
Covers: lex/lex_app/reflex/ui/pages/vocab.py (button disabled=), lex/lex_app/reflex/ui/results.py
(download media_type=), lex/lex_app/reflex/ui/state.py and runs.py (is_running).
Run: python -m lex pytest lex/test_project/tests/init/test_1df_ui_button_disabled_download_type.py -v
"""

from __future__ import annotations

import asyncio
import threading
from unittest.mock import patch

import pytest
from django.test import SimpleTestCase

from lex import ui
from lex.lex_app.reflex.ui import results, testing
from lex.lex_app.reflex.ui.pages.parts import Build

pytestmark = pytest.mark.init


class ProofDesk(ui.State):
    """Rows whose proof may have expired."""

    @ui.rows
    def proofs(self):
        return [{"id": 1, "name": "Fund One", "blocked": ""},
                {"id": 2, "name": "Fund Two", "blocked": "The proof has expired."}]

    @ui.action
    def proof(self, key: str):
        return None


GATE = threading.Event()
SEEN: list = []


class OverlapDesk(ui.State):
    """Two jobs that must not overlap."""

    @ui.job
    def build(self):
        GATE.wait(5)

    @ui.job
    def export(self, when: str):
        SEEN.append((when, self.is_running(OverlapDesk.build)))


class TestCluster01df_ButtonDisabledDownloadType(SimpleTestCase):
    """1.830-1.831: ui.button(disabled=), ui.download(media_type=), self.is_running."""

    # -- 1.830 ---------------------------------------------------------
    def test_1_830_a_button_says_why_it_cannot_be_pressed_and_a_file_what_it_is(self) -> None:
        """
        Scenario 1.830: a disabled row action with its reason; a typed download.
        Given: a table whose rows each carry why their proof cannot be fetched ("" when it can),
               a page button blocked for a fixed reason, and a download of a PDF's bytes.
        When:  the table and the button are built, and the download becomes a browser event.
        Then:  each row's button is blocked exactly when its row has a reason, keeps its place
               and shows the reason (RFDS's accessible-disabled path), never its row's key in
               place of it; the page button shows its sentence; the download carries its media
               type; a reason that is not a sentence or a field is refused.
        """
        table = ui.embed(ui.table(ProofDesk.proofs, columns=["name"],
                                  row_actions=lambda row: [ui.button("Proof", ProofDesk.proof, row["id"],
                                                                     disabled=row["blocked"])]),
                         name="proofs")
        built = str(table)
        self.assertIn('content:(!((r_rx_state_?.["blocked"]?.valueOf?.() === ""?.valueOf?.())) ? r_rx_state_?.["blocked"] : null)',
                      built, "each row's own reason, and none where it is empty")
        self.assertIn('"aria-disabled":(pyOr(!((r_rx_state_?.["blocked"]?.valueOf?.() === ""?.valueOf?.()))', built,
                      "the row's button is blocked exactly when its row has a reason, and stays focusable")
        page = str(ui.button("Send", ProofDesk.proof, "all", disabled="Nothing to send yet.").build(Build(page="a")))
        self.assertIn('content:rfdsI18n.t("Nothing to send yet.", "Nothing to send yet."', page, "a fixed reason is shown")
        self.assertIn("aria-disabled", page.replace("ariaDisabled", "aria-disabled"),
                      "the button stays reachable and says it is disabled")

        with patch.object(results, "RECORDER", None):
            [event] = results.to_events([ui.download(data=b"%PDF-1.7", filename="proof.pdf",
                                                     media_type="application/pdf")])
        self.assertIn("application/pdf", str(event), "the browser is told what the file is")
        with self.assertRaises(TypeError):
            ui.button("Send", ProofDesk.proof, disabled=True)

    # -- 1.831 ---------------------------------------------------------
    def test_1_831_a_body_asks_whether_another_job_is_running(self) -> None:
        """
        Scenario 1.831: self.is_running.
        Given: a build job that runs until it is let go, and an export job that asks about it.
        When:  the export runs while the build runs, and again after the build ended.
        Then:  it sees the build running, then not; outside a body the question is refused.
        """
        GATE.clear()
        SEEN.clear()

        async def scenario():
            async with testing.Harness(user="ada") as h:
                await h.open_tab("/exports")
                build = await h.send(OverlapDesk, "build")
                await asyncio.sleep(0.2)
                await (await h.send(OverlapDesk, "export", when="during"))
                GATE.set()
                await build
                await (await h.send(OverlapDesk, "export", when="after"))

        try:
            asyncio.run(scenario())
        finally:
            GATE.set()
        self.assertEqual(SEEN, [("during", True), ("after", False)], "running while it runs, then not")
        with self.assertRaises(RuntimeError):
            OverlapDesk().is_running(OverlapDesk.build)
