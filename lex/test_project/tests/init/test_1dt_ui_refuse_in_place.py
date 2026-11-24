"""lex.ui: a refusal shown where the user is looking -- above a form's submit button, with a title.

Intent: a form's handler that cannot do what was asked says why with `raise ui.Refuse("...")`. The
user pressed the submit button and is looking at it: the sentence belongs there, above the button,
in RFDS's inline message, until the form is sent again -- not only in a toast in a corner that a
long form scrolls away from. A short `title=` heads it ("Nothing to export"). Outside a form there
is no form to put it in, and a refusal stays a toast, now headed by its title. One way to refuse,
`ui.Refuse`, wherever the handler runs. A regression is a refusal the user never sees, one that
stays after the form was sent again, a toast and an in-place message saying the same thing twice,
or a title that is lost.

Cluster 1dt -- scenarios 1.904-1.907. Type: U.
Covers: lex/lex_app/reflex/ui/results.py (Refuse, to_events), lex/lex_app/reflex/ui/handlers.py (_classify,
_toast, action, job), lex/lex_app/reflex/ui/forms.py (receive, refused, done),
lex/lex_app/reflex/ui/pages/forms.py (Form._page, dialog), lex/lex_app/reflex/ui/state.py (ui_refused).
Run: python -m lex pytest lex/test_project/tests/init/test_1dt_ui_refuse_in_place.py -v
"""

from __future__ import annotations

import asyncio

import pytest
import reflex as rx
from django.test import SimpleTestCase
from reflex.compiler.compiler import compile_page

from lex import ui
from lex.lex_app.reflex.ui import results, testing
from lex.lex_app.reflex.ui.pages.parts import Build
from lex.lex_app.reflex.ui.state import State

pytestmark = pytest.mark.init

NOTHING = "No order in this region has an invoice."
CLOSED = "That region is closed."


class ExportsState(ui.State):
    """Exports of a region's invoices: a form, a long one, and buttons on each order that ask."""

    runs: int = 0

    def _refuse(self, region: str) -> None:
        if region == "empty":
            raise ui.Refuse(NOTHING, title="Nothing to export")
        if region == "closed":
            raise ui.Refuse(CLOSED)
        if region == "broken":
            raise RuntimeError("the archive is away")
        if region == "denied":
            raise ui.ApiError(403, {"detail": "no"})

    @ui.action
    def export(self, values):
        self.runs += 1
        self._refuse(values["region"])

    @ui.job
    def export_all(self, values):
        yield ui.progress(0.5, "Reading invoices")
        self._refuse(values["region"])

    @ui.action
    def export_in_steps(self, values):
        self._refuse(values["region"])

    @ui.action
    def purge(self):
        raise ui.Refuse("Nothing is old enough to purge.", title="Nothing to purge")

    @ui.action
    def resend(self, key, values):
        self.runs += 1
        self._refuse(values["region"])

    @ui.job
    def archive(self, key, values):
        yield ui.progress(0.5, "Archiving")
        self._refuse(values["region"])

    @ui.rows
    def orders(self):
        return [{"id": 1, "name": "Order one"}]


def export_invoices():
    return ui.form(title="Export invoices", submit=ExportsState.export, fields=[ui.field("region", required=True)])


def export_everything():
    return ui.form(title="Export everything", submit=ExportsState.export_all, fields=[ui.field("region")])


def _orders():
    return ui.embed(ui.table(ExportsState.orders, columns=["name"], row_actions=lambda row: [
        ui.button("Resend", ExportsState.resend, row["id"], ask=[ui.field("region")]),
        ui.button("Archive", ExportsState.archive, row["id"], ask=[ui.field("region")])]), name="orders")


def _build() -> None:
    for page in (export_invoices, export_everything):
        page().build(Build(page=page.__name__, route=f"/{page.__name__}"))
    _orders()


def _text(component: rx.Component) -> str:
    return str(component) + "\n".join(component._get_all_hooks())


async def _session(steps):
    async with testing.Harness() as harness:
        await harness.open_tab()
        return await steps(harness), list(harness.results), list(harness.outcomes)


async def _settle() -> None:
    """A job runs in the background: let it finish."""
    await asyncio.sleep(0.1)


class TestCluster01dt_RefuseInPlace(SimpleTestCase):
    """1.904-1.907: a title; in a form above the button; on the page and in a dialog; a toast elsewhere."""

    def setUp(self) -> None:
        _build()

    # -- 1.904 ---------------------------------------------------------
    def test_1_904_outside_a_form_a_refusal_is_a_toast_headed_by_its_title(self) -> None:
        """
        Scenario 1.904: ui.Refuse(message, title=) where there is no form.
        Given: a handler that receives no values and refuses with a title; a refusal without one.
        When:  it runs; and the toasts become Reflex events.
        Then:  the refusal keeps its sentence and its title; the toast is a warning whose first
               line is the title and whose second is the sentence; without a title the sentence
               alone, as before; nothing is kept in place, for there is no form to show it in;
               a title that is no text is refused.
        """
        refusal = ui.Refuse(NOTHING, title="Nothing to export")
        self.assertEqual((refusal.message, refusal.title, str(refusal)), (NOTHING, "Nothing to export", NOTHING))
        self.assertEqual(ui.Refuse(CLOSED).title, "", "a title is optional")
        with self.assertRaises(TypeError):
            ui.Refuse(CLOSED, title=["Closed"])

        ran = ui.testing.run(ExportsState, "purge")
        self.assertEqual(ran.refused, "Nothing is old enough to purge.")
        self.assertEqual(ran.toasts, [results.Toast("Nothing to purge", "warning", "Nothing is old enough to purge.")],
                         "the title heads the toast, the sentence under it")
        self.assertEqual(ran.fields["ui_refused"], {}, "outside a form, nothing is kept in place")
        event = str(results.to_events(ran.toasts)[0])
        self.assertIn('["warning"]("Nothing to purge"', event, "RFDS's warning toast, headed by the title")
        self.assertIn('["description"] : "Nothing is old enough to purge."', event, "the sentence as its second line")
        plain = str(results.to_events([results.Toast(CLOSED, "warning")])[0])
        self.assertNotIn("description", plain, "without a title, the sentence alone")

    # -- 1.905 ---------------------------------------------------------
    def test_1_905_in_a_form_the_refusal_stays_above_the_button_until_the_form_is_sent_again(self) -> None:
        """
        Scenario 1.905: a form's handler refuses.
        Given: the export form (an action) and the export-everything form (a job).
        When:  each is sent with a region that is refused -- with a title, without one, by the
               lex-app API (not permitted) -- then sent again, wrongly and rightly; a handler
               fails unexpectedly; the page is opened anew.
        Then:  the refusal is kept for the form, title and sentence, and is not also a toast;
               the next send removes it, also one with a problem at a field; a job's refusal is
               kept the same way; an unexpected failure stays a toast with its reference and is
               not kept in place; a new visit starts the form over.
        """

        async def steps(harness):
            seen = []
            for values in ({"region": "empty"}, {"region": ""}, {"region": "closed"}, {"region": "north"},
                           {"region": "denied"}, {"region": "broken"}):
                await (await harness.send(ExportsState, "export", values=values))
                seen.append(dict((await harness.fields(ExportsState))["ui_refused"]))
            await (await harness.send(ExportsState, "export", values={"region": "empty"}))
            await (await harness.send(State, "ui_open", page="/export_invoices"))
            seen.append(dict((await harness.fields(ExportsState))["ui_refused"]))
            await (await harness.send(ExportsState, "export_all", values={"region": "empty"}))
            await _settle()
            seen.append(dict((await harness.fields(ExportsState))["ui_refused"]))
            return seen

        seen, toasts, _ = asyncio.run(_session(steps))
        titled, wrong, plain, right, denied, broken, revisited, job = seen
        self.assertEqual(titled, {"ExportsState.export": {"title": "Nothing to export", "message": NOTHING}},
                         "kept for the form: the title and the sentence")
        self.assertEqual(wrong, {}, "sending again removes it, also when a field has a problem")
        self.assertEqual(plain, {"ExportsState.export": {"title": "", "message": CLOSED}}, "a title is optional")
        self.assertEqual(right, {}, "a send that succeeds removes it")
        self.assertEqual(denied, {"ExportsState.export": {"title": "", "message": "You are not permitted to do this."}},
                         "lex-app's API refusing is a refusal too")
        self.assertEqual(broken, {}, "an unexpected failure is not kept in place")
        self.assertEqual(revisited, {}, "a new visit starts the form over")
        self.assertEqual(job, {"ExportsState.export_all": {"title": "Nothing to export", "message": NOTHING}},
                         "a job's refusal, the same way")
        self.assertEqual([toast.tone for toast in toasts], ["error"],
                         "no toast repeats a refusal shown in place; the unexpected failure is a toast")
        self.assertTrue(toasts[0].message.startswith("Something went wrong. Reference: "))

    # -- 1.906 ---------------------------------------------------------
    def test_1_906_the_form_shows_it_above_its_submit_button(self) -> None:
        """
        Scenario 1.906: the form page as the browser gets it.
        Given: the export form and a wizard.
        When:  they are built.
        Then:  above the submit button is RFDS's inline message, shown while the form has a
               refusal, in the warning tone, announced, with the title above the sentence when
               there is one; the page compiles with Reflex's page compiler; a wizard shows it
               above its buttons too.
        """
        text = _text(export_invoices().build(Build(page="export_invoices", route="/exports")))
        message = text.index('"data-slot":"rfds-inline-message"')
        self.assertLess(message, text.index('"data-slot":"rfds-form-actions"'), "above the submit button")
        self.assertGreater(message, text.index('"data-slot":"rfds-form"'), "inside the form")
        for needle, why in (('ui_refused_rx_state_?.["ExportsState.export"]', "the form's own refusal"),
                            ('"data-rfds-tone":"warning"', "a refusal's tone, as its toast had"),
                            ('role:"alert"', "announced: it is new and stops the user"),
                            ("rfds-inline-message-title", "the title above the sentence")):
            with self.subTest(needle=needle):
                self.assertIn(needle, text, why)
        _, module = compile_page("/exports", export_invoices().build(Build(page="export_invoices", route="/exports")))
        self.assertIn("RadixThemesCallout", module, "compiled: RFDS's inline message is Radix's callout")
        wizard = _text(ui.form(title="Export in steps", submit=ExportsState.export_in_steps, steps=[
            ui.step("Where", fields=[ui.field("region", required=True)]),
            ui.step("How", fields=[ui.field("note")])]).build(Build(page="export_in_steps", route="/exports/steps")))
        self.assertLess(wizard.index('ui_refused_rx_state_?.["ExportsState.export_in_steps"]'),
                        wizard.index('"data-slot":"rfds-form-actions"'), "a wizard shows it above its buttons too")

    # -- 1.907 ---------------------------------------------------------
    def test_1_907_a_dialog_that_asked_stays_open_with_the_refusal(self) -> None:
        """
        Scenario 1.907: a button's ask= dialog whose handler refuses.
        Given: buttons on each order that ask for a region: one runs an action, one a job.
        When:  the table is built; the action's dialog is answered with a refused region, then
               with a good one; the job's dialog with a refused region.
        Then:  the dialog holds RFDS's inline message above its buttons; the action's dialog
               stays open with the refusal and no toast, and closes once the action succeeds;
               the job's dialog closed when the job started -- the user keeps working -- so its
               refusal is a toast, headed by its title.
        """
        text = _text(_orders())
        dialog = text[text.index('"data-slot":"rfds-dialog"'):]
        self.assertLess(dialog.index('ui_refused_rx_state_?.["ExportsState.resend"]'),
                        dialog.index('"data-slot":"rfds-form-actions"'), "the dialog shows the refusal above its buttons")

        async def steps(harness):
            await (await harness.send(State, "ui_ask", dialog="orders-ask-1", args=[1]))
            await (await harness.send(ExportsState, "resend", key=1, values={"region": "empty"}))
            refused = await harness.fields(ExportsState)
            await (await harness.send(ExportsState, "resend", key=1, values={"region": "north"}))
            done = await harness.fields(ExportsState)
            await (await harness.send(State, "ui_ask", dialog="orders-ask-2", args=[1]))
            await (await harness.send(ExportsState, "archive", key=1, values={"region": "empty"}))
            await _settle()
            job = await harness.fields(ExportsState)
            return refused, done, job

        (refused, done, job), toasts, _ = asyncio.run(_session(steps))
        self.assertEqual((refused["ui_asking"], refused["ui_refused"]),
                         ("orders-ask-1", {"ExportsState.resend": {"title": "Nothing to export", "message": NOTHING}}),
                         "the action's dialog stays open, with the refusal")
        self.assertEqual((done["ui_asking"], done["ui_refused"], done["runs"]), ("", {}, 1),
                         "it closes once the action succeeded (what the refused run changed was discarded)")
        self.assertEqual((job["ui_asking"], job["ui_refused"]), ("", {}), "the job's dialog closed when it started")
        self.assertEqual(toasts, [results.Toast("Nothing to export", "warning", NOTHING)],
                         "so the job's refusal is a toast, headed by its title")
