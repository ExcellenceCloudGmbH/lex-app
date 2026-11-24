"""lex.ui forms: the handler runs once, with checked values; the page follows what it did.

Intent: a form is sent once, to one handler. lex-app stands between the browser and the handler's
body: it checks every field first, keeps the messages for the fields, and runs the body only
when all is well -- so the body starts with values it can compute with, whoever sent the event.
What the page shows around the form follows from the same run: the button shows it running, a
job its progress, a refusal its own words, and the result appears after a success and leaves
when the form is sent again. A wizard is the same form shown in steps: its Continue button
sends it, and only the last step's reaches the body. A regression is a body that runs on
unchecked values, a result that outlives the input it belongs to, or a wizard that runs its
handler once per step.

Cluster 1di -- scenarios 1.837-1.841. Type: U.
Covers: lex/lex_app/reflex/ui/forms.py (receive, done), lex/lex_app/reflex/ui/handlers.py (action,
job), lex/lex_app/reflex/ui/state.py (ui_invalid, ui_step, ui_done, ui_open, ui_step_back),
lex/lex_app/reflex/ui/testing.py (Result.invalid).
Run: python -m lex pytest lex/test_project/tests/init/test_1di_ui_form_submit.py -v
"""

from __future__ import annotations

import asyncio

import pytest
from django.test import SimpleTestCase

from lex import ui
from lex.lex_app.reflex.ui import testing
from lex.lex_app.reflex.ui.pages.parts import Build
from lex.lex_app.reflex.ui.state import State

pytestmark = pytest.mark.init


class OrdersState(ui.State):
    """An entry form's state, a tool's and a wizard's."""

    saved: dict = {}
    runs: int = 0
    _report: list[dict] = []

    @ui.action
    def create(self, values):
        self.runs += 1
        if values["name"] == "taken":
            raise ui.Refuse("An order of that name exists.")
        if values["name"] == "broken":
            raise RuntimeError("the database is away")
        self.saved = {"name": values["name"], "amount": str(values["amount"]), "type": type(values["amount"]).__name__}
        return ui.toast("Order created", tone="success")

    @ui.job
    def report(self, values):
        self.runs += 1
        yield ui.progress(0.5, "Reading orders")
        if values["year"] == 1999:
            raise ui.Refuse("Nothing was ordered that year.")
        self._report = [{"id": 1, "name": f"Orders of {values['year']}"}]

    @ui.rows
    def report_rows(self):
        return self._report

    @ui.action
    def register(self, values):
        self.runs += 1
        self.saved = {"name": values["name"], "street": values["street"], "terms": values["terms"]}


def new_order():
    return ui.form(title="New order", submit=OrdersState.create,
                   fields=[ui.field("name", required=True), ui.field("amount", kind="money", required=True)])


def orders_report():
    return ui.form(title="Orders report", submit=OrdersState.report, fields=[ui.field("year", kind="year", required=True)],
                   result=[ui.table(OrdersState.report_rows, columns=["name"])])


def register():
    return ui.form(title="Register", submit=OrdersState.register, steps=[
        ui.step("Who", fields=[ui.field("name", required=True)]),
        ui.step("Where", fields=[ui.field("street", required=True)]),
        ui.step("Terms", fields=[ui.field("terms", kind="bool", required=True)])])


def _build() -> None:
    """What lex-app does when it registers the pages: each is built, which tells the handlers their fields."""
    for page in (new_order, orders_report, register):
        page().build(Build(page=page.__name__, route=f"/{page.__name__}"))


def _session(steps):
    """Send several events in one browser tab; ``steps`` is a coroutine function of the harness."""

    async def scenario():
        async with testing.Harness() as harness:
            await harness.open_tab()
            return await steps(harness)

    return asyncio.run(scenario())


async def _send(harness, handler: str, state=OrdersState, **arguments):
    await (await harness.send(state, handler, **arguments))
    await asyncio.sleep(0.05)
    return await harness.fields(OrdersState)


class TestCluster01di_FormSubmit(SimpleTestCase):
    """1.837-1.841: checked before the body; refusal, failure, result; a job; a wizard; a new visit."""

    def setUp(self) -> None:
        _build()

    # -- 1.837 ---------------------------------------------------------
    def test_1_837_the_values_are_checked_before_the_handlers_body_runs(self) -> None:
        """
        Scenario 1.837: required and kind, before the body.
        Given: a form with a required name and a required amount of money, built on its page.
        When:  the browser sends it with the name missing and "abc" as the amount; then filled in.
        Then:  the first time the body does not run: each field has its message, nothing is
               left running and no result shows. The second time the body runs once, with the
               amount as a Decimal; the messages are gone and the form's result flag is set.
        """
        wrong = ui.testing.run(OrdersState, "create", values={"name": " ", "amount": "abc"})
        self.assertEqual(wrong.invalid, {"name": "Enter a value.", "amount": "Enter a number, such as 1250.50."},
                         "each field's own message")
        self.assertEqual((wrong.fields["runs"], wrong.running, wrong.fields["ui_done"]), (0, {}, {"OrdersState.create": False}),
                         "the body did not run, nothing is running, no result")
        self.assertEqual(wrong.toasts, [], "the messages are at the fields, not in a toast")
        right = ui.testing.run(OrdersState, "create", values={"name": "Desks", "amount": "1250.50", "extra": "x"})
        self.assertEqual(right.fields["saved"], {"name": "Desks", "amount": "1250.50", "type": "Decimal"},
                         "the body computes with a Decimal")
        self.assertEqual((right.invalid, right.fields["runs"]), ({}, 1))
        self.assertEqual(right.fields["ui_done"], {"OrdersState.create": True}, "the result may show")
        self.assertEqual(right.fields["ui_running"], {}, "and the run is over")
        self.assertNotIn("Desks", str([update.get("ui_running") for update in right.updates]),
                         "what was entered is not what a run is told apart by")

        async def corrected(harness):
            await _send(harness, "create", values={"name": "", "amount": "1"})
            return await _send(harness, "create", values={"name": "Desks", "amount": "1"})

        self.assertEqual(_session(corrected)["ui_invalid"], {}, "a corrected field loses its message")

    # -- 1.838 ---------------------------------------------------------
    def test_1_838_a_refusal_and_a_failure_are_shown_and_no_result_appears(self) -> None:
        """
        Scenario 1.838: when the body says no, or breaks.
        Given: the same form, and a body that refuses one name and fails on another.
        When:  each is sent; and a good one after a success.
        Then:  the refusal is shown in its own words and the failure with a reference, as for
               every handler; neither sets the result flag; sending the form again takes the
               last result away before the body runs, and brings it back after it succeeded;
               sending it with a field refused takes it away too.
        """
        refused = ui.testing.run(OrdersState, "create", values={"name": "taken", "amount": "1"})
        self.assertEqual(refused.refused, "An order of that name exists.")
        self.assertEqual(refused.fields["ui_done"], {"OrdersState.create": False}, "no result after a refusal")
        failed = ui.testing.run(OrdersState, "create", values={"name": "broken", "amount": "1"})
        self.assertIn("Reference:", failed.error, "a failure is shown with its reference")
        self.assertEqual(failed.fields["ui_done"], {"OrdersState.create": False})

        async def twice(harness):
            await _send(harness, "create", values={"name": "Desks", "amount": "1"})
            harness.updates.clear()
            await _send(harness, "create", values={"name": "Chairs", "amount": "2"})
            return [update["ui_done"]["OrdersState.create"] for update in harness.updates if "ui_done" in update]

        self.assertEqual(_session(twice), [False, True], "the old result leaves when the form is sent again")

        async def then_wrong(harness):
            await _send(harness, "create", values={"name": "Desks", "amount": "1"})
            return (await _send(harness, "create", values={"name": "", "amount": "1"}))["ui_done"]

        self.assertEqual(_session(then_wrong), {"OrdersState.create": False},
                         "also when the new input is refused at a field: the result was for the old input")

    # -- 1.839 ---------------------------------------------------------
    def test_1_839_a_job_reports_its_progress_and_then_its_result(self) -> None:
        """
        Scenario 1.839: a tool -- a form whose handler is a job.
        Given: a form with a year, submitted to a job that yields its progress and keeps its
               rows in a server-only field.
        When:  it is sent without a year, with a year, and with a year the job refuses.
        Then:  without a year the job does not start; with one the browser sees it running, its
               progress, and the result flag only once it has finished; the rows stay on the
               server until the result's table reads them; a refused run shows no result.
        """
        empty = ui.testing.run(OrdersState, "report", values={"year": ""})
        self.assertEqual((empty.invalid, empty.fields["runs"], empty.running), ({"year": "Enter a value."}, 0, {}))
        done = ui.testing.run(OrdersState, "report", values={"year": "2026"})
        seen = [name for update in done.updates for name in ("ui_running", "ui_progress", "ui_done") if name in update]
        self.assertEqual(seen.index("ui_done"), 0, "first the last result leaves")
        self.assertLess(seen.index("ui_progress"), len(seen) - 1 - seen[::-1].index("ui_done"),
                        f"progress before the result: {seen}")
        self.assertIn({"OrdersState.report": 0.5}, [update.get("ui_progress") for update in done.updates])
        self.assertEqual(done.fields["ui_done"], {"OrdersState.report": True})
        self.assertNotIn("Orders of 2026", str(done.updates), "the rows are kept on the server")
        self.assertEqual(ui.testing.rows(OrdersState.report_rows, fields={"_report": [{"id": 1, "name": "x"}]}),
                         [{"id": 1, "name": "x"}], "and the result's table reads them")
        refused = ui.testing.run(OrdersState, "report", values={"year": "1999"})
        self.assertEqual(refused.refused, "Nothing was ordered that year.")
        self.assertEqual(refused.fields["ui_done"], {"OrdersState.report": False})

    # -- 1.840 ---------------------------------------------------------
    def test_1_840_a_wizard_sends_one_form_and_runs_its_handler_once(self) -> None:
        """
        Scenario 1.840: steps.
        Given: a wizard of three steps, each with one required field.
        When:  Continue is pressed with the first step empty; with it filled; Back; Continue
               twice; the last step is sent without its box ticked, then ticked; and the last
               step is sent after an earlier field was emptied.
        Then:  an empty step keeps its place and shows its message, and fields of later steps
               are not judged yet; a filled step moves on without running the body; Back
               returns one step; only the last step's submit runs the body, once, with every
               step's values; a problem in an earlier step returns to that step.
        """
        key = "OrdersState.register"

        async def through(harness):
            seen = []
            for name, arguments in (
                    ("register", {"values": {"name": ""}}),
                    ("register", {"values": {"name": "Ada"}}),
                    ("back", {}),
                    ("register", {"values": {"name": "Ada"}}),
                    ("register", {"values": {"name": "Ada", "street": "Main St"}}),
                    ("register", {"values": {"name": "Ada", "street": "Main St"}}),
                    ("register", {"values": {"name": "Ada", "street": "Main St", "terms": "on"}})):
                if name == "back":
                    fields = await _send(harness, "ui_step_back", state=State, key=key)
                else:
                    fields = await _send(harness, name, **arguments)
                seen.append((fields["ui_step"].get(key), fields["ui_invalid"], fields["runs"]))
            return seen, fields

        seen, last = _session(through)
        self.assertEqual(seen[0], (0, {f"{key}|name": "Enter a value."}, 0), "an empty step stays, later steps unjudged")
        self.assertEqual(seen[1], (1, {}, 0), "a filled step moves on; the body has not run")
        self.assertEqual(seen[2][0], 0, "Back returns one step")
        self.assertEqual([step for step, _, _ in seen[3:5]], [1, 2], "Continue, twice")
        self.assertEqual(seen[5], (2, {f"{key}|terms": "Tick this box to continue."}, 0), "the last step is checked too")
        self.assertEqual(seen[6], (2, {}, 1), "the last step's submit runs the body, once")
        self.assertEqual(last["saved"], {"name": "Ada", "street": "Main St", "terms": True}, "with every step's values")
        self.assertEqual(last["ui_done"], {key: True})

        async def emptied(harness):
            await harness.set_fields(State, ui_step={key: 2})
            return await _send(harness, "register", values={"name": "", "street": "Main St", "terms": "on"})

        back = _session(emptied)
        self.assertEqual((back["ui_step"], back["ui_invalid"], back["runs"]),
                         ({key: 0}, {f"{key}|name": "Enter a value."}, 0), "a problem in an earlier step returns there")

    # -- 1.841 ---------------------------------------------------------
    def test_1_841_a_new_visit_starts_the_form_over(self) -> None:
        """
        Scenario 1.841: leaving and coming back.
        Given: a tab with a message at a field, a wizard on its second step, a result showing
               and a dialog open.
        When:  the browser opens a page (the opener's event).
        Then:  the messages, the wizard's step, the result flag and the open dialog are gone:
               the form the user comes back to is a fresh one.
        """
        async def revisit(harness):
            await harness.set_fields(State, ui_invalid={"OrdersState.create|name": "Enter a value."},
                                     ui_step={"OrdersState.register": 1}, ui_done={"OrdersState.report": True},
                                     ui_asking="orders-ask-1", ui_asking_args=["7"])
            await (await harness.send(State, "ui_open", page="/new_order"))
            fields = await harness.fields(OrdersState)
            return {name: fields[name] for name in ("ui_invalid", "ui_step", "ui_done", "ui_asking")}

        self.assertEqual(_session(revisit), {"ui_invalid": {}, "ui_step": {}, "ui_done": {}, "ui_asking": ""})
