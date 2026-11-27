"""lex.ui calculation pages: a file the run produced, and stopping a run.

Intent: a calculation often produces a file (a report, an export) in a FileField of its record. On
the calculation page that field, written alone in ``result=`` as the model's field, is a button that
downloads the run's file as the user -- through lex-app's own download, with its permission check
and its audit entry. A user who may edit the model may stop a run that goes on a worker, as lex-app's
cancel does; a run in lex-app's own process cannot be stopped, and the page says so in words instead
of failing. A regression is a file downloaded past lex-app's checks, a Stop that pretends to stop a
run that goes on, or a page left saying "running" after a stop.

Cluster 1ei -- scenarios 1.994-1.997. Type: E (the handlers run against lex-app's real views).
Covers: lex/lex_app/reflex/ui/pages/calculation.py (a file field alone in result=, Stop),
lex/lex_app/reflex/ui/calculations.py (download, stop), lex/lex_app/reflex/data.py (LexApi.file),
lex/lex_app/reflex/ui/context.py (self.api.file).
Run: python -m lex pytest lex/test_project/tests/init/test_1ei_ui_calculation_file_stop.py -v
"""

from __future__ import annotations

import asyncio
import threading
from unittest.mock import MagicMock, patch

import pytest
from reflex.compiler.compiler import compile_page

from lex import ui
from lex.core.models.CalculationModel import CalculationModel
from lex.lex_app.reflex.ui import calculations, handlers, results, runs
from lex.lex_app.reflex.ui.pages.parts import Build
from lex.test_project.tests._e2e_test_case import E2ETestCase
from lex.test_project.tests.init._ui_calc_aids import Session, settled
from lex.test_project.tests.init.models import UI_CALC_MODELS, UiRegionReport

pytestmark = pytest.mark.init

ROUTE = "/reports/regions"
CTX = Build(page="region_reports", route=ROUTE, requires=())      # a @ui.page without requires=


def region_reports():
    return ui.calculation(UiRegionReport, title="Region report",
                          fields=[ui.field("region", required=True), ui.field("year", kind="year", required=True)],
                          result=[UiRegionReport.orders, UiRegionReport.report_file],
                          history=ui.table(UiRegionReport, columns=["region", "year"]))


class TestCluster01ei_CalculationFileAndStop(E2ETestCase):
    """1.994-1.997: a file of the result; Stop on a worker; Stop where it cannot stop; the whole page."""

    e2e_models = UI_CALC_MODELS
    #: lex-app's own record of a running calculation, which cancel() reads its worker task from.
    e2e_unpatch = {"mark_in_progress"}

    def setUp(self) -> None:
        super().setUp()
        UiRegionReport.gate, UiRegionReport.with_file = None, False
        self.addCleanup(self._release)
        poll = patch.object(calculations, "POLL_SECONDS", 0.02)
        poll.start()
        self.addCleanup(poll.stop)
        self.page = region_reports()
        self.page.build(CTX)
        self.state = self.page.state
        handlers.register_page(ROUTE, *self.page.loads(), "")

    def _release(self) -> None:
        if UiRegionReport.gate is not None:
            UiRegionReport.gate.set()
        UiRegionReport.gate, UiRegionReport.with_file = None, False
        for record in UiRegionReport.objects.exclude(report_file=""):
            record.report_file.delete(save=False)
        import contextlib
        import os

        from django.core.files.storage import default_storage

        with contextlib.suppress(Exception):        # the folder the run wrote into, once empty
            os.rmdir(default_storage.path("ui_region_reports"))

    def _started(self, region: str = "north") -> UiRegionReport:
        response = self.client.post(self.url_create("uiregionreport"), data={"region": region, "year": 2026},
                                    format="json")
        record = UiRegionReport.objects.get(pk=response.data["id"])
        response = self.client.patch(self.url_detail("uiregionreport", record.pk, f"uiregionreport_{record.pk}_update_t"),
                                     data={"calculate": "true"}, format="json")
        self.assertEqual(response.status_code, 202, response.content)
        return record

    def _on_run(self, record, steps):
        async def scenario():
            async with Session(self.client) as session:
                await session.visit(f"{ROUTE}?run={record.pk}")
                return await steps(session)

        return asyncio.run(scenario())

    # -- 1.994 ---------------------------------------------------------
    def test_1_994_a_file_the_run_produced_is_downloaded_as_the_user(self) -> None:
        """
        Scenario 1.994: the run's file.
        Given: a run that wrote a CSV into its FileField; the field alone in result=.
        When:  the page is compiled; the run's view sends the button's download; and a forged
               download names a field that is no file.
        Then:  the field is a button labelled as the model labels it, shown only when the run has a
               file; the download hands the user the run's CSV, named as stored, fetched through
               lex-app's download view (not the disk); the forged field is refused in words.
        """
        _, module = compile_page(ROUTE, self.page.build(CTX))
        self.assertIn('"Report file"', module, "a button labelled as the model labels the field")
        UiRegionReport.with_file = True
        record = self._started()
        self.assertEqual(settled(record), CalculationModel.SUCCESS)

        async def steps(session):
            await (await session.harness.send(self.state, "download", field="report_file"))
            got = list(session.harness.results)
            await (await session.harness.send(self.state, "download", field="region"))
            return got, list(session.harness.outcomes), session.views.calls

        got, outcomes, calls = self._on_run(record, steps)
        files = [one for one in got if isinstance(one, results.Download)]
        self.assertEqual(len(files), 1, f"one file handed over: {got}")
        self.assertEqual(files[0].data, b"customer,amount\nAcme,625.25\nBeta,625.25\n", "the run's CSV")
        record.refresh_from_db()
        self.assertEqual(files[0].filename, record.report_file.name.rsplit("/", 1)[-1], "named as stored")
        self.assertTrue(files[0].filename.startswith("region-north-2026"), files[0].filename)
        self.assertIn(("GET", "/api/uiregionreport/file-download"), calls, "through lex-app's download view")
        self.assertIn("region is not a file of UiRegionReport", " ".join(o.message for o in outcomes))

    # -- 1.995 ---------------------------------------------------------
    def test_1_995_stop_cancels_a_run_on_a_worker_and_the_page_says_so(self) -> None:
        """
        Scenario 1.995: Stop, with the run on a worker.
        Given: Celery in use; the run handed to a worker whose task lex-app recorded.
        When:  the run's page follows it and the user presses Stop.
        Then:  lex-app's cancel revokes the worker's task and the record is CANCELLED; the page stops
               following and holds CANCELLED -- the view's "stopped" sentence -- without an error.
        """
        from lex.core.signals.ActiveCalculationStateStore import ActiveCalculationStateStore

        def dispatch(instance):
            ActiveCalculationStateStore.set_task_id(f"uiregionreport_{instance.pk}", "task-7")
            return MagicMock(id="task-7")

        with patch.object(UiRegionReport, "should_use_celery", return_value=True), \
                patch.object(UiRegionReport, "dispatch_calculation_task", autospec=True, side_effect=dispatch), \
                patch.object(CalculationModel, "_revoke_celery_task") as revoke:
            record = self._started("south")

            async def steps(session):
                following = await session.harness.send(self.state, "follow")
                await asyncio.sleep(0.1)
                await (await session.harness.send(self.state, "stop"))
                await asyncio.wait_for(following, 5)
                return await session.harness.fields(self.state), list(session.harness.outcomes)

            after, outcomes = self._on_run(record, steps)
        revoke.assert_called_once_with("task-7")
        record.refresh_from_db()
        self.assertEqual(record.is_calculated, CalculationModel.CANCELLED, "lex-app's cancel ended the run")
        self.assertEqual(after["run_status"], "CANCELLED", "the page holds the stop")
        self.assertEqual(after["ui_running"], {}, "and stopped following")
        self.assertEqual([o for o in outcomes if o.kind != "ok"], [], "a stop is no failure")

    # -- 1.996 ---------------------------------------------------------
    def test_1_996_a_run_in_lex_apps_process_cannot_be_stopped_and_the_page_says_so(self) -> None:
        """
        Scenario 1.996: Stop where lex-app cannot stop.
        Given: a run held open in lex-app's own process (Celery off).
        When:  the user presses Stop; then, once the run ended, presses it again.
        Then:  the first is refused in words (a run in lex-app's process goes on to its end) and the
               run still ends in SUCCESS; the second says the run has already ended.
        """
        UiRegionReport.gate = threading.Event()
        record = self._started()

        async def steps(session):
            await (await session.harness.send(self.state, "stop"))
            first = [o.message for o in session.harness.outcomes]
            UiRegionReport.gate.set()
            await asyncio.to_thread(settled, record)
            await (await session.harness.send(self.state, "stop"))
            return first, [o.message for o in session.harness.outcomes][len(first):]

        first, second = self._on_run(record, steps)
        self.assertTrue(any("cannot be stopped" in one for one in first), f"refused in words: {first}")
        self.assertEqual(settled(record), CalculationModel.SUCCESS, "the run went on to its end")
        self.assertTrue(any("already ended" in one for one in second), f"after its end: {second}")

    # -- 1.997 ---------------------------------------------------------
    def test_1_997_the_whole_page_declared_with_ui_page_compiles(self) -> None:
        """
        Scenario 1.997: the page as a project declares it.
        Given: @ui.page with requires=ui.read(Model) returning the calculation page with a form, a result
               (a fact, a file, a related table), the past runs.
        When:  lex-app registers the declared pages, and the page is compiled.
        Then:  the page is registered with lex-app's load of the run; it compiles to one Reflex page with
               the form, the past runs, the run's view, Stop asking "Stop this run?" to editors only.
        """
        from lex.lex_app.reflex.access import permission
        from lex.lex_app.reflex.ui.pages import declare

        @ui.page("/reports/regions-whole", requires=ui.read(UiRegionReport))
        def region_reports_whole():
            return region_reports()

        # As lex-app registers it (declare.finalize): built with the page's requires=, then its loads read.
        kind = declare._kind_of(region_reports_whole)
        built = kind.build(Build(page="region_reports_whole", route="/reports/regions-whole",
                                 requires=declare._DECLARED["/reports/regions-whole"].requires))
        required, optional = declare._loads("region_reports_whole", kind)
        self.assertEqual([load.fn.__name__ for load in required], ["open"])
        _, module = compile_page("/reports/regions-whole", built)
        for sign in ("rfds-form", '"Past runs"', "rfds-progress-bar", '"Stop this run?"'):
            self.assertIn(sign, module, sign)
        self.assertIn(permission(UiRegionReport, "edit"), module, "Stop is shown to users who may edit")
        declare._DECLARED.pop("/reports/regions-whole", None)
