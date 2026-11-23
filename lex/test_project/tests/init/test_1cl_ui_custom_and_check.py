"""lex.ui: the custom region is marked, and the check lists what leaves the default path.

Intent: raw Reflex stays possible, but only where it is marked -- @ui.custom -- so a reviewer
sees every place a page leaves lex-app's vocabulary. `lex reflex check` fails on an unmarked raw
handler on a ui.State, lists the marked ones and every requires=ui.signed_in, and warns about a
handler that names a model manager without declaring what it requires. A regression is raw
Reflex creeping back into project code unnoticed.

Cluster 1cl -- scenarios 1.781-1.782. Type: U.
Covers: lex/lex_app/reflex/ui/handlers.py (custom), lex/lex_app/reflex/ui/checks.py,
lex/lex_app/reflex/scaffold.py (run_check).
Run: python -m lex pytest lex/test_project/tests/init/test_1cl_ui_custom_and_check.py -v
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

import pytest

from lex.lex_app.reflex import scaffold
from lex.lex_app.reflex.ui import checks, handlers
from lex.lex_app.reflex.ui.state import State

pytestmark = pytest.mark.init

_SOURCE = '''
import reflex as rx
from lex import ui


class Tickets(ui.State):
    rows: list[dict] = []

    @ui.action
    def approve(self, code: str):
        self.api.update("ticket", code, {"status": "approved"})

    @rx.event
    def raw(self):
        pass

    @ui.custom
    async def upload(self, files: list[rx.UploadFile]):
        pass

    @ui.action(requires=ui.signed_in)
    def anyone(self):
        pass

    @ui.job
    def totals(self):
        self.rows = list(Ticket.objects.values("id"))

    @ui.load(requires=ui.read(Ticket))
    def open(self):
        self.rows = list(Ticket.objects.values("id"))


class Plain(rx.State):
    @rx.event
    def fine(self):
        pass
'''


class TestCluster01cl_CustomAndCheck(TestCase):
    """1.781-1.782."""

    # -- 1.781 ---------------------------------------------------------
    def test_1_781_ui_custom_is_a_marked_reflex_handler(self) -> None:
        """
        Scenario 1.781: the custom region.
        Given: an async handler under @ui.custom on a ui.State.
        When:  the class is created.
        Then:  it is an ordinary Reflex event handler -- async allowed, background on request --
               that carries the custom mark.
        """
        class Uploads(State):
            @handlers.custom
            async def take(self):
                pass

            @handlers.custom(background=True)
            async def stream(self):
                pass

        self.assertEqual(Uploads.take.fn.__ui_kind__, "custom", "the handler carries the custom mark")
        self.assertFalse(Uploads.take.is_background, "a plain custom handler is not a background task")
        self.assertTrue(Uploads.stream.is_background, "background=True is passed on to Reflex")

    # -- 1.782 ---------------------------------------------------------
    def test_1_782_the_check_fails_on_unmarked_raw_handlers_and_lists_the_rest(self) -> None:
        """
        Scenario 1.782: lex reflex check.
        Given: a project file with a ui.State holding a raw @rx.event, a @ui.custom, a
               requires=ui.signed_in, a handler naming a model manager without requires=, one
               with requires=, and a plain rx.State.
        When:  the findings are collected and reported, and run_check runs over the project.
        Then:  the raw handler is an error; the custom handler and the signed_in requirement
               are notes; the unguarded manager is a warning; the guarded one and the plain
               state raise nothing; the report returns 1, and run_check returns non-zero even
               when RFDS's own checks pass.
        """
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "_reflex_structure.py"
            path.write_text(_SOURCE)
            found = checks.find([str(path)])
            by_level = {level: sorted(f.message.split(":")[0] for f in found if f.level == level)
                        for level in ("error", "warning", "note")}
            self.assertEqual(by_level["error"], ["Tickets.raw"], "an unmarked raw handler fails the check")
            self.assertEqual(by_level["warning"], ["Tickets.totals"], "a model manager without requires= is flagged")
            self.assertEqual(by_level["note"], ["Tickets.anyone", "Tickets.upload"],
                             "custom handlers and signed_in requirements are listed")
            self.assertTrue(all(f.line > 0 and f.path == str(path) for f in found), "each finding has its place")
            self.assertEqual(checks.report(found), 1, "an error makes the report return 1")
            self.assertEqual(checks.report([f for f in found if f.level != "error"]), 0, "notes and warnings pass")
            with patch("rfds.cli.main", return_value=0):
                self.assertNotEqual(scaffold.run_check(Path(folder)), 0, "the lex.ui error fails lex reflex check")
