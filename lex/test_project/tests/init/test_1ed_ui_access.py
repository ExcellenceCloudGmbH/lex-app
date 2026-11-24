"""lex.ui's frame: who may see a page -- a project's no-access sentence and way out, and its own rules.

Intent: a user who opens a page they may not see is told so in the project's own words and offered
the project's way out ("Ask your administrator for access", a request page, signing in with another
account), on every refused page -- not lex-app's generic sentence with no way forward. And a project
guards pages and handlers with rules of its own ("only the region's team"), checked on the server
like lex-app's permissions, refused with the rule's own sentence. A regression is a refused page that
strands the user, a sentence that is lex-app's where the project said its own, a rule that the browser
decides, a rule asked more than once per event, or a rule that fails open.

Cluster 1ed -- scenarios 1.968-1.973. Type: U.
Covers: lex/lex_app/reflex/ui/pages/frame.py (denied=, request_access=), lex/lex_app/reflex/shell.py
(denied_notice, lex_page), lex/lex_app/reflex/ui/state.py (error_page, ui_open),
lex/lex_app/reflex/ui/guard.py (rule), lex/lex_app/reflex/ui/handlers.py (_session),
lex/lex_app/reflex/ui/pages/declare.py (page requires=).
Run: python -m lex pytest lex/test_project/tests/init/test_1ed_ui_access.py -v
"""

from __future__ import annotations

import pytest
import reflex as rx
from reflex.compiler.compiler import compile_page

from lex import ui
from lex.lex_app.reflex import shell
from lex.lex_app.reflex.ui.pages import declare
from lex.lex_app.reflex.ui.state import error_page
from lex.test_project.tests.init.test_1eb_ui_frame_nav import FrameCase

pytestmark = pytest.mark.init


def _about(title):
    return ui.record(title=title, sections=[ui.section("About", ui.text("All about it."))])


class TestCluster01ed_NoAccess(FrameCase):
    """1.968-1.969: the project's sentence and way out, on a refused page and on the page a refused load goes to."""

    # -- 1.968 ---------------------------------------------------------
    def test_1_968_a_refused_page_says_the_projects_sentence_and_offers_its_way_out(self) -> None:
        """
        Scenario 1.968: the no-access notice.
        Given: a page that requires a permission, with lex-app's defaults, then with
               ui.frame(denied=, request_access=, sign_out=).
        When:  the page is built in lex-app's frame and compiled.
        Then:  the page falls back to RFDS's access-denied notice: by default RFDS's sentence, no
               request link, "Switch account" to lex-app's sign-out; configured, the project's sentence,
               its request-access address and "Switch account" to its sign-out; a request page that
               needs values, and a route written as text, are refused with what to write.
        """
        @ui.page("/reports", nav="Reports", requires=ui.permission("lex_app.Report", "read"))
        def reports():
            return _about("Reports")

        declare.finalize()
        _, module = compile_page("/reports", self.page_named("/reports")())
        for expected in ("rfds-access-denied", "does not have permission", '"/logout"', "lex_app.Report:read"):
            self.assertIn(expected, module, f"by default, RFDS's notice: {expected}")
        self.assertNotIn("rfds-access-denied-request", module, "no request link the project did not give")

        @ui.page("/access")
        def access_request():
            return _about("Request access")

        ui.frame(denied="Ask your administrator for access.", request_access=access_request,
                 sign_out="https://portal.example.com/sign-out")
        _, module = compile_page("/reports", self.page_named("/reports")())
        for expected in ("Ask your administrator for access.", "rfds-access-denied-request", '"/access"',
                         "https://portal.example.com/sign-out"):
            self.assertIn(expected, module, f"the project's notice: {expected}")
        ui.frame(request_access="mailto:access@example.com")
        self.assertIn("mailto:access@example.com", str(shell.denied_notice()), "a mail address is a way out too")

        @ui.page("/reports/[region]")
        def region_report():
            return _about("Region")

        for wrong, says in ((region_report, "needs region"), ("/access", "not written as text")):
            with self.assertRaises(ValueError) as caught:
                ui.frame(request_access=wrong)
            self.assertIn(says, str(caught.exception))
        with self.assertRaises(TypeError) as caught:
            ui.frame(denied=["Ask your administrator."])
        self.assertIn("denied=", str(caught.exception))

    # -- 1.969 ---------------------------------------------------------
    def test_1_969_the_page_a_refused_load_goes_to_offers_the_same_way_out(self) -> None:
        """
        Scenario 1.969: a load was refused.
        Given: ui.frame(request_access=) and the page a failed load goes to (/ui-error).
        When:  it is built and compiled.
        Then:  a refusal there is the same notice -- the refusal's own sentence, the project's request
               link, "Switch account" -- while any other failure keeps its "Try again".
        """
        ui.frame(request_access="mailto:access@example.com")
        _, module = compile_page("/ui-error", error_page())
        for expected in ("rfds-access-denied", "mailto:access@example.com", "Try again", '"denied"'):
            self.assertIn(expected, module, f"the error page: {expected}")
