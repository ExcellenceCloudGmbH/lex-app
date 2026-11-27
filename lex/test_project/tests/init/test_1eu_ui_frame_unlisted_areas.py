"""lex.ui's frame: areas ui.frame(nav=) does not list follow the listed ones; a typo is still refused.

Intent: `ui.frame(nav={...})` orders the navigation's areas. A project moving its pages one at a time
still has raw Reflex pages in areas of their own ("Admin", "Tools"); requiring every such area in
the list stopped the app when the author ordered only the areas they cared about. Now an area the
list does not name follows the listed ones, in the order its first page was declared. The check
that caught a typo stays where it matters: a `@ui.page` whose area neither the list nor any raw page
has is refused, naming the areas to choose from. A regression is an app that does not start because
an old page's area is unlisted, or a typo that silently makes a new area.

Cluster 1eu -- scenario 1.1030. Type: U.
Covers: lex/lex_app/reflex/shell.py (check_nav, check_area, _raw_areas, _areas),
lex/lex_app/reflex/ui/pages/frame.py (frame nav=), lex/lex_app/reflex/ui/pages/declare.py (finalize).
Run: python -m lex pytest lex/test_project/tests/init/test_1eu_ui_frame_unlisted_areas.py -v
"""

from __future__ import annotations

import pytest

from lex import ui
from lex.lex_app.reflex import shell
from lex.lex_app.reflex.ui.pages import declare
from lex.lex_app.reflex.ui.pages.parts import PageError
from lex.test_project.tests.init.test_1eb_ui_frame_nav import FrameCase, _record

pytestmark = pytest.mark.init


class TestCluster01eu_FrameUnlistedAreas(FrameCase):
    """1.1030: unlisted areas follow; a lex.ui page's typo is refused."""

    # -- 1.1030 --------------------------------------------------------
    def test_1_1030_unlisted_areas_follow_and_a_typo_is_still_refused(self) -> None:
        """
        Scenario 1.1030: ui.frame(nav={"Orders": []}) with raw pages in other areas.
        Given: a raw page in "Admin", then a raw page in "Tools", a @ui.page in "Orders" and a @ui.page
               that joins the raw area "Admin"; then a @ui.page in "Ordres" (a typo), and a record page
               with route arguments in "Reprots".
        When:  lex-app registers the pages and builds the navigation.
        Then:  the start goes on; the areas are Orders, then Admin and Tools in the order their first
               page was declared; the lex.ui page sits in the raw area it joined; each typo stops the
               start, naming the page, the listed areas and the raw pages' areas to choose from.
        """
        ui.frame(nav={"Orders": []})
        ui.reflex_page("/admin/users", nav="Admin", title="Users")
        ui.reflex_page("/tools/import", nav="Tools", title="Import")

        @ui.page("/orders", nav="Orders")
        def orders():
            return _record("Orders")

        @ui.page("/admin/audit", nav="Admin")
        def audit():
            return _record("Audit")

        declare.finalize()
        sections = shell.nav_sections()
        self.assertEqual([s.label_default for s in sections], [None, "Orders", "Admin", "Tools"],
                         "the listed area first, then the others in the order their first page was declared")
        self.assertEqual([i.href for i in sections[2].items], ["/admin/users", "/admin/audit"],
                         "a lex.ui page joins a raw page's area")

        @ui.page("/returns", nav="Ordres")
        def returns():
            return _record("Returns")

        with self.assertRaises(PageError) as typo:
            declare.finalize()
        message = str(typo.exception)
        for expected in ("returns", "'Ordres'", "'Orders'", "'Admin'", "'Tools'", "add 'Ordres': [] to ui.frame(nav=)"):
            self.assertIn(expected, message, f"the refusal names {expected!r}")
        declare._DECLARED.pop("/returns")

        @ui.page("/reports/[key]", nav="Reprots")
        def report():
            return _record("Report")

        with self.assertRaises(PageError) as record_typo:
            declare.finalize()
        self.assertIn("'Reprots'", str(record_typo.exception))
