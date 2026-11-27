"""lex.ui calculation pages: one run -- followed while it goes, then its result, its error, its log.

Intent: the same calculation page with ``?run=<key>`` is that run's page. While the run goes, the page
reads the record through lex-app's API, as the user, until its status settles -- the same whether
lex-app runs it in its own process (Celery off) or a worker runs it and reports when it ends (Celery
on), because the record's status is the truth either way. Then it shows the result written with the
model's own fields (``RegionReport.orders``), or the calculation's error in its own words, and the
run's log. A regression is a page that says "running" for ever, a result shown before the run ended,
a traceback shown to a user, or a run shown to someone the model does not let read it.

Cluster 1eg -- scenarios 1.985-1.990, 1.999, 1.1116. Type: E (the handlers run against lex-app's real views; 1.1116 U).
Covers: lex/lex_app/reflex/ui/pages/calculation.py (the run's view, the result with the model's
fields), lex/lex_app/reflex/ui/calculations.py (open, follow, error_words, newest_line).
Run: python -m lex pytest lex/test_project/tests/init/test_1eg_ui_calculation_run.py -v
"""

from __future__ import annotations

import asyncio
import threading
from unittest.mock import MagicMock, patch

import pytest
from django.contrib.auth.models import Group
from django.test import SimpleTestCase
from reflex.compiler.compiler import compile_page

from lex import ui
from lex.core.models.CalculationModel import CalculationModel
from lex.lex_app.reflex.ui import calculations, handlers, runs
from lex.lex_app.reflex.ui.pages.parts import Build, PageError
from lex.test_project.tests._e2e_test_case import E2ETestCase
from lex.test_project.tests.init._ui_calc_aids import Session, settled
from lex.test_project.tests.init.models import UI_CALC_MODELS, UiRegionReport, UiRegionReportLine

pytestmark = pytest.mark.init

ROUTE = "/reports/regions"
CTX = Build(page="region_reports", route=ROUTE, requires=())      # a @ui.page without requires=


def region_reports(**changes):
    given = dict(title="Region report",
                 fields=[ui.field("region", required=True), ui.field("year", kind="year", required=True)],
                 result=[UiRegionReport.orders,
                         ui.fact("Revenue", UiRegionReport.revenue, kind="money"),
                         ui.text("{n} orders were read.", n=UiRegionReport.orders),
                         ui.section("Lines", ui.table(UiRegionReportLine, where={"report": UiRegionReport.id},
                                                      columns=["customer", ui.column("amount", kind="money")]))])
    given.update(changes)
    return ui.calculation(UiRegionReport, **given)


class TestCluster01eg_CalculationRun(E2ETestCase):
    """1.985-1.990, 1.999: the run's view; followed with Celery off and on; a failure; a run not the reader's;
    leaving; a reconnect."""

    e2e_models = UI_CALC_MODELS
    #: The live log is lex-app's own cache: a failed atomic run's log is kept from it.
    e2e_unpatch = {"store_message", "build_cache_key"}

    def setUp(self) -> None:
        super().setUp()
        UiRegionReport.gate, UiRegionReport.may_create, UiRegionReport.may_read = None, True, True
        self.addCleanup(self._release)
        poll = patch.object(calculations, "POLL_SECONDS", 0.02)
        poll.start()
        self.addCleanup(poll.stop)
        self.page = region_reports()
        self.page.build(CTX)
        self.state = self.page.state
        handlers.register_page(ROUTE, *self.page.loads(), "")      # as @ui.page does when lex-app starts

    @staticmethod
    def _release() -> None:
        if UiRegionReport.gate is not None:
            UiRegionReport.gate.set()
        UiRegionReport.gate, UiRegionReport.may_create, UiRegionReport.may_read = None, True, True

    def _created(self, region: str = "north") -> UiRegionReport:
        response = self.client.post(self.url_create("uiregionreport"), data={"region": region, "year": 2026},
                                    format="json")
        self.assertEqual(response.status_code, 201, response.content)
        return UiRegionReport.objects.get(pk=response.data["id"])

    def _calculate(self, record: UiRegionReport) -> None:
        response = self.client.patch(self.url_detail("uiregionreport", record.pk, f"uiregionreport_{record.pk}_update_t"),
                                     data={"calculate": "true"}, format="json")
        self.assertEqual(response.status_code, 202, response.content)

    async def _fields(self, session) -> dict:
        return await session.harness.fields(self.state)

    # -- 1.985 ---------------------------------------------------------
    def test_1_985_the_runs_view_shows_the_model_fields_as_the_runs_values(self) -> None:
        """
        Scenario 1.985: the run's view, as declared.
        Given: a result written with the model's own fields -- alone, in a fact, in a sentence, and as
               the value a related table is narrowed by.
        When:  the page is built and compiled.
        Then:  it compiles; a field alone is a fact labelled as the model labels it; every field is the
               run's value (the state's run), never the model's descriptor; the view has the status
               badge, the progress that follows the run when it shows, the error, and the log; a field
               of another model is refused, saying how to show records of another model.
        """
        _, module = compile_page(ROUTE, self.page.build(CTX))
        text = str(self.page.build(CTX))
        self.assertIn('"Orders"', text, "a field alone is a fact labelled with the model's verbose name")
        self.assertIn('"Revenue"', text)
        self.assertIn(".run", module, "the result reads the run's values")
        self.assertNotIn("DeferredAttribute", module, "no model descriptor reaches the page")
        self.assertIn("rfds-progress-bar", module, "the progress of a run that goes")
        self.assertIn('"Done"', text, "the status badge words a run's status")
        self.assertIn("follow", module, "the progress part follows the run when it appears")
        self.assertIn('"Log"', text, "the run's log")
        with self.assertRaises(PageError) as other:
            region_reports(result=[Group.name]).build(CTX)
        self.assertIn("`Group.name` is a field of Group; this page is about UiRegionReport", str(other.exception))
        self.assertIn("ui.table(Group", str(other.exception))

    # -- 1.986 ---------------------------------------------------------
    def test_1_986_a_run_in_lex_apps_process_is_followed_until_it_ends(self) -> None:
        """
        Scenario 1.986: following a run with Celery off.
        Given: a run started through lex-app and held open inside calculate(), in lex-app's process.
        When:  the run's address is opened and the page follows it; then the run is let go.
        Then:  while it is held the page says IN_PROGRESS and its progress message is the newest line the
               run logged, or "Running"; once it ended the following stops by itself, the page holds
               SUCCESS, the run's values (orders 3, the revenue) and its log; the run's name heads it.
        """
        UiRegionReport.gate = threading.Event()
        record = self._created()
        self._calculate(record)

        async def scenario():
            async with Session(self.client) as session:
                await session.visit(f"{ROUTE}?run={record.pk}")
                opened = await self._fields(session)
                following = await session.harness.send(self.state, "follow")
                key = runs.key_of(self.state.follow)

                async def reporting():
                    return key in (await self._fields(session))["ui_progress_message"]

                await session.until(reporting, "the page never reported the run's progress")
                during = await self._fields(session)
                UiRegionReport.gate.set()
                await following
                return opened, during, await self._fields(session)

        opened, during, after = asyncio.run(scenario())
        self.assertEqual((opened["run_id"], opened["run_status"]), (str(record.pk), "IN_PROGRESS"),
                         "the run's address opens that run, running")
        self.assertEqual(opened["run_name"], "north 2026", "the run is named as lex-app names the record")
        message = during["ui_progress_message"][runs.key_of(self.state.follow)]
        self.assertIn(message, ("Running", "Reading the orders of north."), "the run's own reporting, or Running")
        self.assertEqual(after["run_status"], "SUCCESS", "followed until it settled")
        self.assertEqual((after["run"]["orders"], after["run"]["revenue"]), (3, "1250.50"), "the run's values")
        self.assertTrue(any("Reading the orders of north." in entry for entry in after["run_log"]), "and its log")
        self.assertEqual(after["ui_running"], {}, "the following stopped by itself")

    # -- 1.987 ---------------------------------------------------------
    def test_1_987_a_run_on_a_worker_is_followed_until_the_worker_reports(self) -> None:
        """
        Scenario 1.987: following a run with Celery on.
        Given: Celery in use; the run is handed to a worker (recorded), so it stays IN_PROGRESS.
        When:  the run's page follows it for a while; then the worker runs it and reports, as lex-app's
               worker does (its task's success callback writes SUCCESS).
        Then:  meanwhile the page still says IN_PROGRESS, and nothing ran in the API's process; after
               the report the page holds SUCCESS and what the worker computed.
        """
        from lex.lex_app.celery_tasks import calc_and_save

        record = self._created("south")
        with patch.object(UiRegionReport, "should_use_celery", return_value=True), \
                patch.object(UiRegionReport, "dispatch_calculation_task", return_value=MagicMock(id="task-1")) as worker:
            self._calculate(record)
            self.assertTrue(_waited(lambda: worker.called), "the run was handed to a worker")

        def the_worker_runs_and_reports() -> None:
            on_worker = UiRegionReport.objects.get(pk=record.pk)
            on_worker.calculate()
            on_worker.save(skip_hooks=True)
            calc_and_save.on_success(None, "task-1", ([on_worker],), {})

        async def scenario():
            async with Session(self.client) as session:
                await session.visit(f"{ROUTE}?run={record.pk}")
                following = await session.harness.send(self.state, "follow")
                await asyncio.sleep(0.2)
                meanwhile = await self._fields(session)
                await asyncio.to_thread(the_worker_runs_and_reports)
                await following
                return meanwhile, await self._fields(session)

        meanwhile, after = asyncio.run(scenario())
        self.assertEqual(meanwhile["run_status"], "IN_PROGRESS", "the worker has not reported: still running")
        self.assertEqual(meanwhile["ui_running"], {runs.key_of(self.state.follow): [""]}, "and followed")
        self.assertEqual(after["run_status"], "SUCCESS", "the worker's report is what the page shows")
        self.assertEqual(after["run"]["orders"], 3, "with what the worker computed")

    # -- 1.988 ---------------------------------------------------------
    def test_1_988_a_failed_run_shows_its_error_in_its_own_words(self) -> None:
        """
        Scenario 1.988: a run that fails.
        Given: a run whose calculate() raises "There is no region called nowhere.".
        When:  it has ended and its address is opened.
        Then:  the page holds ERROR and the error in the calculation's own words, without the
               traceback lex-app stored after it; the failed run's log is kept and shown.
        """
        record = self._created("nowhere")
        self._calculate(record)
        self.assertEqual(settled(record), CalculationModel.ERROR)
        self.assertIn("Traceback", record.calculation_error_message, "lex-app keeps the traceback")

        async def scenario():
            async with Session(self.client) as session:
                await session.visit(f"{ROUTE}?run={record.pk}")
                return await self._fields(session)

        fields = asyncio.run(scenario())
        self.assertEqual(fields["run_status"], "ERROR")
        self.assertEqual(fields["run_error"], "There is no region called nowhere.", "its own words, no traceback")
        self.assertTrue(fields["run_log"], "the failed run's log is shown")
        self.assertEqual(calculations.error_words("Boom\n\nTraceback (most recent call last):\n  x"), "Boom")
        self.assertEqual(calculations.error_words(""), "")

    # -- 1.989 ---------------------------------------------------------
    def test_1_989_a_run_the_reader_may_not_read_is_no_run_to_them(self) -> None:
        """
        Scenario 1.989: the run's address, opened by a reader the model refuses, or for nothing.
        Given: a finished run; then a model whose permission_read refuses the reader.
        When:  its address is opened; and the address of a key that is no run.
        Then:  nothing of the run reaches the page, and the reader gets the error page saying there is
               no such run -- lex-app answers a record the reader may not read as one that does not
               exist, and the page does not tell them otherwise; a key that is no run says the same.
        """
        record = self._created()
        self._calculate(record)
        settled(record)
        UiRegionReport.may_read = False

        async def scenario(url):
            from lex.lex_app.reflex.ui.state import State

            async with Session(self.client) as session:
                await session.visit(url)
                return await self._fields(session), await session.harness.fields(State)

        refused, refused_base = asyncio.run(scenario(f"{ROUTE}?run={record.pk}"))
        self.assertEqual((refused["run"], refused["run_status"]), ({}, ""), "nothing of the run reaches the page")
        self.assertEqual(refused_base["ui_error"].get("message"), f"There is no run {record.pk}.",
                         f"the error page: {refused_base['ui_error']}")
        UiRegionReport.may_read = True
        _, missing_base = asyncio.run(scenario(f"{ROUTE}?run=999999"))
        self.assertEqual(missing_base["ui_error"].get("message"), "There is no run 999999.")

    # -- 1.990 ---------------------------------------------------------
    def test_1_990_leaving_stops_the_watching_never_the_run(self) -> None:
        """
        Scenario 1.990: the user leaves a running run's page.
        Given: a run held open in lex-app's process, followed by the page.
        When:  the user opens another page; then the run is let go.
        Then:  the following stops (nothing is left running for the tab), and the run itself still
               ends in SUCCESS: watching is the page's, the run is lex-app's.
        """
        UiRegionReport.gate = threading.Event()
        record = self._created()
        self._calculate(record)

        async def scenario():
            async with Session(self.client) as session:
                await session.visit(f"{ROUTE}?run={record.pk}")
                following = await session.harness.send(self.state, "follow")
                key = runs.key_of(self.state.follow)

                async def started():
                    return key in (await self._fields(session))["ui_running"]

                await session.until(started, "the following never started")
                await session.visit("/elsewhere")
                await asyncio.wait_for(following, 5)
                return await self._fields(session)

        after = asyncio.run(scenario())
        self.assertEqual(after["ui_running"], {}, "leaving stopped the following")
        UiRegionReport.gate.set()
        self.assertEqual(settled(record), CalculationModel.SUCCESS, "the run went on and ended")


    # -- 1.999 ---------------------------------------------------------
    def test_1_999_a_reconnect_keeps_following_the_run_and_does_not_open_the_page_again(self) -> None:
        """
        Scenario 1.999: the socket drops while a run is followed (merge of the calculation page with the
        review's same visit and the frame's opened=).
        Given: ui.frame(opened=) recording each opening; a run held open in lex-app's process; its
               address opened and followed.
        When:  the socket drops and Reflex reconnects on the same address (``hydrate`` and ``on_load``
               again, on a new connection): the page's load runs again and the progress part, mounted
               again, sends ``follow`` again; then the run is let go.
        Then:  the reconnect is the same visit: the following goes on (leaving would stop it) and the
               second ``follow`` is a repeat, not a second follower; the load read the run again; the
               page was opened once; when the run ends the one following stops by itself with SUCCESS.
        """
        import reflex as rx

        from lex.lex_app.reflex.ui.pages import frame
        from lex.lex_app.reflex.ui.state import State

        opened: list = []
        unframed = patch.object(frame, "_FRAME", None)
        unframed.start()
        self.addCleanup(unframed.stop)
        ui.frame(opened=lambda user, page: opened.append(page.address))
        UiRegionReport.gate = threading.Event()
        record = self._created()
        self._calculate(record)
        url = f"{ROUTE}?run={record.pk}"

        def at(sid: str) -> dict:
            return {"pathname": ROUTE, "asPath": url, "query": {"run": str(record.pk)}, "sid": sid}

        async def scenario():
            async with Session(self.client) as session:
                harness, key = session.harness, runs.key_of(self.state.open)

                async def connect(sid: str) -> None:
                    await (await harness.send(rx.State, "hydrate", _router_data=at(sid)))
                    await (await harness.send(State, "ui_open", page=ROUTE, _router_data=at(sid)))

                    async def loaded():
                        return (await harness.fields(State))["ui_loaded"].get(key)

                    await session.until(loaded, "the page's load never finished")

                await connect("socket-1")
                visit = (await harness.fields(State))["ui_visit"]
                following = await harness.send(self.state, "follow", _router_data=at("socket-1"))
                follow = runs.key_of(self.state.follow)

                async def running():
                    return follow in (await self._fields(session))["ui_running"]

                await session.until(running, "the following never started")
                await session.until(lambda: opened, "the page was never opened")
                await harness.set_fields(self.state, run_name="")              # what the load reads again
                await connect("socket-2")
                again = await harness.send(self.state, "follow", _router_data=at("socket-2"))
                await asyncio.wait_for(again, 5)
                await asyncio.sleep(0.1)
                during = await self._fields(session)
                during["ui_visit"] = (await harness.fields(State))["ui_visit"]
                UiRegionReport.gate.set()
                await asyncio.wait_for(following, 10)
                return visit, during, await self._fields(session)

        visit, during, after = asyncio.run(scenario())
        follow = runs.key_of(self.state.follow)
        self.assertEqual(during["ui_visit"], visit, "the reconnect is the same visit")
        self.assertEqual(during["ui_running"], {follow: [""]}, "the one following goes on: no second follower")
        self.assertEqual((during["run_status"], during["run_name"]), ("IN_PROGRESS", "north 2026"),
                         "the load read the run again")
        self.assertEqual(opened, [url], "the page was opened once")
        self.assertEqual(after["run_status"], "SUCCESS", "followed until it settled")
        self.assertEqual(after["ui_running"], {}, "and the following stopped by itself")


def _waited(condition, seconds: float = 10) -> bool:
    import time

    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.02)
    return False



class SlowRunApi:
    """lex-app's API as a page following a run sees it: running for ten readings, then done."""

    def __init__(self):
        self.reads, self.logs = 0, 0

    def get(self, model, key):
        self.reads += 1
        status = "IN_PROGRESS" if self.reads <= 10 else "SUCCESS"
        return {"id": int(key), "is_calculated": status, "short_description": "north 2026"}

    def log(self, model, key):
        self.logs += 1
        return [f"## Step {self.reads}"]


class TestCluster01eg_FollowReadsTheLog(SimpleTestCase):
    """1.1116: following a run reads its record each time and its log every few times."""

    # -- 1.1116 --------------------------------------------------------
    def test_1_1116_following_a_run_reads_its_log_every_few_readings(self) -> None:
        """
        Scenario 1.1116: a run followed for ten readings (review 2, minor: the log is the heavier read).
        Given: the page's state; a run that is IN_PROGRESS for ten readings of its record, then SUCCESS.
        When:  the page follows it.
        Then:  the record is read each time; the log, for the line the progress shows, every
               calculations.LOG_EVERY readings, and once more when the run has ended; the progress still
               shows the newest line read.
        """
        page = region_reports()
        page.build(CTX)
        api = SlowRunApi()
        with patch.object(calculations, "POLL_SECONDS", 0):
            ran = ui.testing.run(page.state, "follow", api=api, fields={"run_id": "7"})
        self.assertEqual(ran.error, "")
        self.assertEqual(api.reads, 11, "the record, each reading")
        every = calculations.LOG_EVERY
        self.assertGreater(every, 1)
        self.assertEqual(api.logs, -(-10 // every) + 1, "the log every few readings, and at the end")
        self.assertEqual(ran.fields["run_log"], ["## Step 11"])
