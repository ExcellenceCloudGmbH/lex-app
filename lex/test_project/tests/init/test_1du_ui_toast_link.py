"""lex.ui: a toast that offers to open the result elsewhere -- `ui.toast(..., link=ui.link(...))`.

Intent: a long job finishes while the user works elsewhere; its toast says so and offers to open the
result -- the history page, the record, an outside report -- with one button. A consultant writes the
link the way every link is written, `ui.link("Open", to=history_page, key=...)`: the same targets
(a page, a raw Reflex page, a record in lex-app, an outside address), the same checks, no second way
to say where something leads. RFDS's toast carries the action and keeps itself open long enough to
press it. A regression is a toast without its button, a button that leads to the wrong page or a
page that does not take the values given, or a link whose mistake is found only when a user presses
it.

Cluster 1du -- scenarios 1.908-1.910. Type: U.
Covers: lex/lex_app/reflex/ui/results.py (toast, Toast.link, to_events), lex/lex_app/reflex/ui/pages/vocab.py
(Link.target), lex/lex_app/reflex/ui/testing.py (Result.toasts).
Run: python -m lex pytest lex/test_project/tests/init/test_1du_ui_toast_link.py -v
"""

from __future__ import annotations

import pytest
from django.contrib.auth.models import Group
from django.test import SimpleTestCase

from lex import ui
from lex.lex_app.reflex.ui import results
from lex.lex_app.reflex.ui.pages import links

pytestmark = pytest.mark.init


def export_page():
    """Stands for a page declared with @ui.page("/exports/[key]", params=["tab"])."""


export_page.__ui_page__ = links.info_of("/exports/[key]", "export_page", ["tab"])


class ExportState(ui.State):
    """An export that runs long and, when done, offers to open what it made."""

    @ui.job
    def export(self):
        yield ui.progress(0.5, "Building")
        return ui.toast("Your export is ready.", tone="success", link=ui.link("Open", to=export_page, key=7))

    @ui.action
    def archive(self):
        return ui.toast("The export was archived.", link=ui.link("Open the archive", href="https://archive.example.test/7"))


class TestCluster01du_ToastLink(SimpleTestCase):
    """1.908-1.910: where the link leads; the toast as the browser gets it; links written wrongly."""

    # -- 1.908 ---------------------------------------------------------
    def test_1_908_a_toast_link_leads_where_a_ui_link_leads(self) -> None:
        """
        Scenario 1.908: ui.toast(link=ui.link(...)) for each of a link's targets.
        Given: links to a page with its key and a parameter, to a raw Reflex page, to a record
               in lex-app and to an outside address.
        When:  each is given to a toast.
        Then:  the toast keeps the link's words and the address the same ui.link would lead to:
               the page's route with its values encoded, the raw page's route, lex-app's page
               of the record, the outside address; only the last two leave the dashboards.
        """
        legacy = ui.reflex_page("/legacy/exports")
        self.addCleanup(links._REFLEX_PAGES.pop, "/legacy/exports", None)   # no such page in the test app
        cases = {
            "page": (ui.link("Open", to=export_page, key="7/8", tab="files"), ("Open", "/exports/7%2F8?tab=files", False)),
            "raw page": (ui.link("Open", to=legacy), ("Open", "/legacy/exports", False)),
            "outside": (ui.link("Report", href="https://reports.example.test/7"),
                        ("Report", "https://reports.example.test/7", True)),
        }
        for name, (link, expected) in cases.items():
            with self.subTest(target=name):
                self.assertEqual(ui.toast("Your export is ready.", link=link).link, expected)
        label, where, outside = ui.toast("Saved.", link=ui.link("Open", record=(Group, 5))).link
        self.assertEqual((label, outside), ("Open", True), "a record opens in lex-app")
        self.assertTrue(where.endswith("/group/5/show"), f"lex-app's page of the record: {where}")
        self.assertEqual(ui.toast("Saved.").link, (), "a toast without a link has none")

    # -- 1.909 ---------------------------------------------------------
    def test_1_909_the_toast_is_rfds_toast_with_its_action(self) -> None:
        """
        Scenario 1.909: a handler returns the toast; the browser gets it.
        Given: a job that ends with a success toast linking to the export's page; an action whose
               toast links to an outside address.
        When:  they run, and their toasts become Reflex events.
        Then:  the run reports the toast with its link; the event is RFDS's toast with one
               action labelled with the link's words, which sends the browser to the page --
               an outside address as an outside one -- and RFDS keeps a toast with an action
               open longer (10 seconds) so there is time to press it.
        """
        ran = ui.testing.run(ExportState, "export")
        self.assertEqual(ran.error, "", "the job ran")
        self.assertEqual(ran.toasts, [results.Toast("Your export is ready.", "success", "", ("Open", "/exports/7", False))])
        event = str(results.to_events(ran.toasts)[0])
        for needle, why in (('["success"]("Your export is ready."', "RFDS's success toast"),
                            ('["label"] : "Open"', "one action, worded as the link"),
                            ('ReflexEvent("_redirect", {path:"/exports/7",external:false', "pressing it opens the page"),
                            ('["duration"] : 10000', "RFDS keeps a toast with an action open longer")):
            with self.subTest(needle=needle):
                self.assertIn(needle, event, why)
        outside = ui.testing.run(ExportState, "archive")
        self.assertIn('{path:"https://archive.example.test/7",external:true',
                      str(results.to_events(outside.toasts)[0]), "an outside address leaves as one")
        plain = str(results.to_events([results.Toast("Saved.")])[0])
        self.assertNotIn('["action"]', plain, "a toast without a link has no button")

    # -- 1.910 ---------------------------------------------------------
    def test_1_910_a_toast_link_written_wrongly_is_refused_where_it_is_written(self) -> None:
        """
        Scenario 1.910: what is refused when the handler makes the toast.
        Given: a link that is no ui.link (an address, a button), a link with visible=, a link to
               a page without the value its route needs, with a value it does not take, or with
               an empty key.
        When:  each is given to ui.toast.
        Then:  each is refused at once, saying what to write -- not when a user presses it.
        """
        cases = {
            "an address": ("/exports/7", TypeError,
                           "ui.toast(link=) takes a ui.link: `link=ui.link(\"Open\", to=history_page)`; got '/exports/7'."),
            "a button": (ui.button("Open", to=export_page, key=7), TypeError,
                         "ui.toast(link=) takes a ui.link: `link=ui.link(\"Open\", to=history_page)`; got a ui.button."),
            "visible": (ui.link("Open", to=export_page, key=7, visible=True), ValueError,
                        "ui.toast(link=): a toast's link is always shown; leave `visible=` out."),
            "missing": (ui.link("Open", to=export_page), ValueError,
                        "ui.toast(link=): a link to page `export_page` (/exports/[key]) is missing key. "
                        "Page `export_page` takes: key, tab. Write `to=export_page, key=...`."),
            "unknown": (ui.link("Open", to=export_page, key=7, year=2026), ValueError,
                        "ui.toast(link=): a link to page `export_page` (/exports/[key]) does not accept year. "
                        "Page `export_page` takes: key, tab. Write `to=export_page, key=...`."),
            "empty key": (ui.link("Open", to=export_page, key=None), ValueError,
                          "ui.toast(link=): the value for `key` in a link is None. "
                          "Pass text or a number: `key=row.id`, `key=7`."),
        }
        for name, (link, error, message) in cases.items():
            with self.subTest(case=name), self.assertRaises(error) as wrong:
                ui.toast("Your export is ready.", link=link)
            self.assertEqual(str(wrong.exception), message, name)
