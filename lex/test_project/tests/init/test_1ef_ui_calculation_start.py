"""lex.ui calculation pages: the page kind, its form, and starting a run as the user.

Intent: a tool that writes to other systems and keeps a record runs as a lex-app calculation model,
and lex.ui gives it one page: ``ui.calculation(Model, title=, fields=, result=, history=)``. The form's
values become a record created through lex-app's API as the user -- the model's permission_create,
its validation and its audit entry apply -- and its run is started as lex-app's Calculate button
starts it: in lex-app's process with Celery off, on a worker with Celery on. The browser then goes
to the run's address on the same page. A regression is a run nobody may trace to its user, a record
created past the model's permissions, a page that blocks while the run goes, or a run whose log the
grid cannot find.

Cluster 1ef -- scenarios 1.980-1.984. Type: E (the handlers run against lex-app's real views).
Covers: lex/lex_app/reflex/ui/pages/calculation.py (ui.calculation, its checks, the form view),
lex/lex_app/reflex/ui/calculations.py (the state, calculate), lex/lex_app/reflex/data.py and
lex/lex_app/reflex/ui/context.py (self.api.calculate, self.api.log).
Run: python -m lex pytest lex/test_project/tests/init/test_1ef_ui_calculation_start.py -v
"""

from __future__ import annotations

import asyncio
import threading
from unittest.mock import MagicMock, patch

import pytest
from django.contrib.auth.models import Group
from reflex.compiler.compiler import compile_page

from lex import ui
from lex.audit_logging.models.CalculationLog import CalculationLog
from lex.core.models.CalculationModel import CalculationModel
from lex.lex_app.reflex.ui import calculations
from lex.lex_app.reflex.ui.pages.parts import Build, PageError
from lex.test_project.tests._e2e_test_case import E2ETestCase
from lex.test_project.tests.init._ui_calc_aids import Session, settled
from lex.test_project.tests.init.models import UI_CALC_MODELS, UiRegionReport

pytestmark = pytest.mark.init

ROUTE = "/reports/regions"
CTX = Build(page="region_reports", route=ROUTE)


def region_reports(**changes):
    given = dict(title="Region report",
                 fields=[ui.field("region", options={"north": "North", "south": "South", "nowhere": "Nowhere"},
                                  required=True),
                         ui.field("year", kind="year", required=True)])
    given.update(changes)
    return ui.calculation(UiRegionReport, **given)


def _start(test, values, *, grants=((UiRegionReport, "create"),)):
    """Send the page's form as the browser would; returns (where the browser went, the outcome, the state)."""
    page = region_reports()
    page.build(CTX)
    state = page.state

    async def scenario():
        async with Session(test.client, grants=grants) as session:
            await session.visit(ROUTE)
            await (await session.harness.send(state, "calculate", values=values))
            await asyncio.sleep(0.05)
            return list(session.harness.results), list(session.harness.outcomes), \
                await session.harness.fields(state), session.views.calls

    return asyncio.run(scenario())


class TestCluster01ef_CalculationStart(E2ETestCase):
    """1.980-1.984: the page as declared; a run started as the user, in-process and on a worker; a refusal;
    self.api.calculate and self.api.log."""

    e2e_models = UI_CALC_MODELS

    def setUp(self) -> None:
        super().setUp()
        UiRegionReport.gate, UiRegionReport.may_create, UiRegionReport.may_read = None, True, True
        self.addCleanup(self._release)

    @staticmethod
    def _release() -> None:
        if UiRegionReport.gate is not None:
            UiRegionReport.gate.set()
        UiRegionReport.gate, UiRegionReport.may_create, UiRegionReport.may_read = None, True, True

    # -- 1.980 ---------------------------------------------------------
    def test_1_980_the_page_is_declared_with_form_words_and_checked_against_the_model(self) -> None:
        """
        Scenario 1.980: a calculation page, as a reader writes it.
        Given: ui.calculation over a calculation model, with a form's fields.
        When:  the page is built and compiled; and written wrongly in each way a reader might.
        Then:  it compiles to a real page whose form is RFDS's, sent once, with a "Calculate" button;
               the page's load is lex-app's own (it reads ?run=); the same page built again has the
               same state; a model that is no calculation, a field the model has not, a field the model
               needs left out, and neither fields nor steps are refused, saying what to write.
        """
        page = region_reports()
        _, module = compile_page(ROUTE, page.build(CTX))
        self.assertIn("rfds-form", module, "the form is RFDS's")
        self.assertIn('"Calculate"', module, "the button reads Calculate, lex-app's word for starting a run")
        self.assertIs(region_reports().state, page.state, "one state per page, whatever the number of builds")
        self.assertEqual([load.fn.__name__ for load in page.loads()[0]], ["open"], "the page's load is lex-app's")
        with self.assertRaises(PageError) as other:
            ui.page("/groups/run")(lambda: ui.calculation(Group, title="Groups", fields=[ui.field("name")]))
            from lex.lex_app.reflex.ui.pages.declare import _kind_of

            _kind_of(lambda: ui.calculation(Group, title="Groups", fields=[ui.field("name")]))
        self.assertIn("not a calculation model", str(other.exception))
        self.assertIn("ui.form", str(other.exception), "it says what enters a record of another model")
        with self.assertRaises(PageError) as unknown:
            region_reports(fields=[ui.field("region"), ui.field("year"), ui.field("colour")]).build(CTX)
        self.assertIn("field `colour` is not a field of UiRegionReport", str(unknown.exception))
        self.assertIn("region, report_file, revenue, year", str(unknown.exception), "it lists the fields a record is given")
        with self.assertRaises(PageError) as missing:
            region_reports(fields=[ui.field("region")]).build(CTX)
        self.assertIn("needs `year` when a record is created", str(missing.exception))
        self.assertIn('ui.field("year")', str(missing.exception))
        with self.assertRaises(PageError) as managed:
            region_reports(fields=[ui.field("region"), ui.field("year"), ui.field("is_calculated")]).build(CTX)
        self.assertIn("is_calculated", str(managed.exception), "the status is lex-app's, never an input")
        from lex.lex_app.reflex.ui.pages.declare import _kind_of

        with self.assertRaises(PageError) as neither:
            _kind_of(lambda: ui.calculation(UiRegionReport, title="Region report"))
        self.assertIn("fields=", str(neither.exception))

    # -- 1.981 ---------------------------------------------------------
    def test_1_981_the_form_creates_the_record_as_the_user_and_starts_its_run_in_process(self) -> None:
        """
        Scenario 1.981: Calculate, with Celery off.
        Given: the page; Celery off, as in development.
        When:  the form is sent with a region and a year.
        Then:  the record exists with those values, created through lex-app's create view and started
               through its Calculate request (no direct write); the browser goes to the run's address
               on the same page (?run=<key>); the run happens in lex-app's process and settles in
               SUCCESS with what calculate() computed; its log is filed under an id of the grid's
               shape, so the grid's log button finds it.
        """
        went, outcomes, _, calls = _start(self, {"region": "north", "year": "2026"})
        self.assertEqual([o for o in outcomes if o.kind != "ok"], [], "the form was not refused")
        record = UiRegionReport.objects.get()
        self.assertEqual((record.region, record.year), ("north", 2026), "the form's values are the record's")
        self.assertEqual([g.path for g in went if isinstance(g, ui.goto("/").__class__)],
                         [f"{ROUTE}?run={record.pk}"], "the browser goes to the run, on the same page")
        methods = [method for method, _ in calls]
        self.assertEqual(methods[:2], ["POST", "PATCH"], "created by the create view, started by Calculate")
        self.assertEqual(settled(record), CalculationModel.SUCCESS, "the run ends in lex-app's own process")
        self.assertEqual(record.orders, 3, "what the run computed is on the record")
        self.assertTrue(CalculationLog.objects.filter(calculationId__startswith=f"uiregionreport_{record.pk}_update_")
                        .exists(), "the run's log is filed under the id the grid's log button looks for")

    # -- 1.982 ---------------------------------------------------------
    def test_1_982_a_user_the_model_does_not_let_create_is_refused_above_the_button(self) -> None:
        """
        Scenario 1.982: permissions are the model's.
        Given: a model whose permission_create refuses the user.
        When:  the user sends the form anyway (the page shows it only to users with a create grant,
               which decides nothing).
        Then:  no record exists and no run started; the refusal is the form's, above its button,
               in lex-app's words; the browser stays on the form.
        """
        UiRegionReport.may_create = False
        went, outcomes, fields, _ = _start(self, {"region": "north", "year": "2026"})
        self.assertFalse(UiRegionReport.objects.exists(), "lex-app refused the create")
        key = next(iter(fields["ui_refused"]), "")
        self.assertTrue(key.endswith(".calculate"), f"the refusal is the form's, above its button: {fields['ui_refused']}")
        self.assertTrue(fields["ui_refused"][key]["message"], "in words")
        self.assertEqual([g for g in went if hasattr(g, "path")], [], "the browser stays on the form")
        self.assertTrue(any(o.kind in ("refused", "denied") for o in outcomes))

    # -- 1.983 ---------------------------------------------------------
    def test_1_983_with_celery_on_the_run_goes_to_a_worker_and_the_page_does_not_wait(self) -> None:
        """
        Scenario 1.983: Calculate, with Celery on.
        Given: Celery in use (should_use_celery answers True); the worker is a recorder.
        When:  the form is sent.
        Then:  the run is handed to the worker, from lex-app's calculation thread pool; nothing runs in
               the API's process; the record is IN_PROGRESS, and the browser already went to the run.
        """
        handed = threading.Event()

        def dispatch(*_a, **_k):
            handed.set()
            return MagicMock(id="task-1")

        with patch.object(UiRegionReport, "should_use_celery", return_value=True), \
                patch.object(UiRegionReport, "dispatch_calculation_task", side_effect=dispatch) as worker, \
                patch.object(UiRegionReport, "_run_in_calculation_executor") as in_process:
            went, outcomes, _, _ = _start(self, {"region": "south", "year": "2025"})
            self.assertTrue(handed.wait(10), "the run was handed to a worker")
        record = UiRegionReport.objects.get()
        worker.assert_called_once_with()
        in_process.assert_not_called()
        self.assertEqual(record.is_calculated, CalculationModel.IN_PROGRESS, "the worker has not reported yet")
        self.assertEqual(record.orders, None, "nothing ran in the API's process")
        self.assertEqual([g.path for g in went if hasattr(g, "path")], [f"{ROUTE}?run={record.pk}"])

    # -- 1.984 ---------------------------------------------------------
    def test_1_984_self_api_calculate_and_log_are_the_grids_calculate_and_log(self) -> None:
        """
        Scenario 1.984: what a project's own handler may call.
        Given: a record created through the API, not yet calculated.
        When:  self.api.log is read before any run; self.api.calculate starts it; self.api.log is read
               after it settled.
        Then:  before: []; calculate answers the record IN_PROGRESS; after: the run's log, as Markdown
               (what calculate() logged), read through the run the grid's list names for the row.
        """
        from lex.lex_app.reflex.data import LexApi
        from lex.test_project.tests.init._ui_calc_aids import ViewsClient

        api = LexApi("test-token", base_url="http://testserver", client=ViewsClient(self.client))

        async def scenario():
            made = await api.create(UiRegionReport, {"region": "south", "year": 2024})
            before = await api.log(UiRegionReport, made["id"])
            started = await api.calculate(UiRegionReport, made["id"])
            await asyncio.to_thread(lambda: settled(UiRegionReport.objects.get(pk=made["id"])))
            return before, started, await api.log(UiRegionReport, made["id"])

        before, started, after = asyncio.run(scenario())
        self.assertEqual(before, [], "no run, no log")
        self.assertEqual(started["is_calculated"], CalculationModel.IN_PROGRESS, "answered at once, running")
        self.assertTrue(any("Reading the orders of south." in entry for entry in after), f"the run's log: {after}")
        self.assertEqual(calculations.newest_line(after), "Reading the orders of south.",
                         "the newest line a run logged, as the progress message shows it")
