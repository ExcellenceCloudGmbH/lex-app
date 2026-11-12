"""The initial-data load at server start survives restarts and a second server process.

Intent
------
``lex start`` loads ``INITIAL_DATA`` once, when every model it names is empty, and the
load is all-or-nothing (docs/features/data-pipeline/initial data.md). A customer
expects exactly one of two end states: the whole seed, or none of it with a clear
reason. In production the load ran in a thread that inherited ``daemon=True`` from
the ``lex-async-ready`` thread, so a server process that was stopped mid-load (the
``--reload`` supervisor restarting it, a worker or pod replaced) killed the load
mid-write. The next process found 5 of the quarters and 89 of the 179 companies,
read that as "data already exists", and never loaded again, silently.

So these scenarios pin down:

* the load runs in a non-daemon thread even though it is started from a daemon
  thread, and a process that exits waits for it (1.609, 1.614);
* one process decides at a time: a second process waits for the first and then
  decides on the finished database, never on a half-loaded one (1.610), and never
  loads a second copy;
* a process killed outright (no chance to finish) is recognised by the next start
  as an interrupted load and reported, not passed off as existing data (1.611);
* a stopping process does not wait forever for another one's lock (1.612);
* the MQ/Worker deployment runs the same load inside ``WaitForTasks`` (1.613);
* a failed load is recorded and reported on the next start (1.615), a load that
  died before writing anything is simply redone (1.616), and the lock is released
  after a load (1.617); the ordinary "data exists" skip is unchanged (1.618).

Cluster 1bj — scenarios 1.609–1.618. Type: I (1.614 runs a child interpreter).
Letters 1ay–1bi and scenarios up to 1.608 are taken by open branches, hence 1bj / 1.609.
Covers: lex/lex_app/initial_data_load.py, lex/lex_app/apps.py (async_ready).
Run: python -m lex pytest lex/test_project/tests/init/test_1bj_initial_data_load_restart.py -v
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

import pytest
from django.apps import apps
from django.db import connection

from lex.core.config import LexProjectConfig
from lex.lex_app import initial_data_load as idl
from lex.lex_app.celery_tasks import WaitForTasks
from lex.lex_app.tests.ProcessAdminTestCase import ProcessAdminTestCase
from lex.test_project.tests._e2e_test_case import E2ETestCase
from lex.test_project.tests.crud_api.models import SimpleItem

pytestmark = pytest.mark.init

SEED = "Tests/seed_1bj.json"
MODELS = {"SimpleItem": SimpleItem}

#: The checkout holding the ``lex`` package, so a child interpreter imports this lex.
_CHECKOUT = Path(idl.__file__).resolve().parents[2]

requires_postgres = unittest.skipUnless(
    connection.vendor == "postgresql", "advisory locks exist on PostgreSQL only"
)


class _Seed(ProcessAdminTestCase):
    """A seed naming one model, SimpleItem, without a file on disk."""

    def get_test_data(self):
        return [{"class": "SimpleItem", "action": "create", "parameters": {"name": "seeded"}}]


class _FakeLoad:
    """Stands in for ``load_data`` (the seed body, covered by 1f/1i): writes rows, records how it ran."""

    def __init__(self, rows: int = 3, fail_after: int | None = None):
        self.rows = rows
        self.fail_after = fail_after
        self.calls = []

    def __call__(self, test, generic_app_models, audit_enabled, initial_data):
        self.calls.append(
            {
                "thread": threading.current_thread(),
                "wait_for_tasks": WaitForTasks.get_current_context() is not None,
                "initial_data": initial_data,
            }
        )
        try:
            for index in range(self.rows):
                if self.fail_after is not None and index == self.fail_after:
                    raise RuntimeError("boom")
                SimpleItem.objects.create(name=f"seeded-{index}")
        finally:
            connection.close()


class _OtherProcess:
    """Another server process: its own database session, holding the load lock.

    ``finish(work, die=False)`` runs ``work`` in that session and then either releases
    the lock (a load that ends) or drops the session without releasing it (a process
    that is killed: PostgreSQL frees the lock with the connection).
    """

    def __init__(self):
        self.acquired = threading.Event()
        self._go = threading.Event()
        self._work = None
        self._die = False
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        assert self.acquired.wait(30), "the other session never took the lock"

    def _run(self):
        try:
            assert idl.acquire_lock(should_stop=lambda: False)
            self.acquired.set()
            self._go.wait(60)
            if self._work:
                self._work()
            if not self._die:
                idl.release_lock()
        finally:
            connection.close()

    def finish(self, work=None, die=False):
        self._work, self._die = work, die
        self._go.set()
        self._thread.join(30)


def _run_in_thread(**kwargs):
    """Run the load decision in a thread of its own, as a second server process would."""
    box = {}

    def target():
        box["outcome"] = idl.run_initial_data_load(_Seed(), MODELS, SEED, False, **kwargs)

    thread = threading.Thread(target=target, daemon=True)
    thread.start()
    return thread, box


class _LoadCase(E2ETestCase):
    e2e_models = [SimpleItem]

    def setUp(self):
        super().setUp()
        self._drop_state()
        poll = mock.patch.object(idl, "LOCK_POLL_SECONDS", 0.05)
        poll.start()
        self.addCleanup(poll.stop)
        self.addCleanup(self._drop_state)

    @staticmethod
    def _drop_state():
        with connection.cursor() as cursor:
            cursor.execute(f"DROP TABLE IF EXISTS {idl.STATE_TABLE}")

    def _patch_load(self, fake):
        patcher = mock.patch("lex.lex_app.celery_tasks.load_data", fake)
        patcher.start()
        self.addCleanup(patcher.stop)
        return fake


class TestCluster01bj_StartupEntryPoint(_LoadCase):
    """``async_ready`` (what ``lex start`` runs) hands the load to a thread that outlives a stop."""

    def _async_ready_from_daemon_thread(self, env=None):
        """Call async_ready the way ready() does: from the daemon ``lex-async-ready`` thread."""
        config = LexProjectConfig(initial_data=SEED)
        config._loaded = True
        app_config = apps.get_app_config("lex_app")
        app_module = sys.modules[type(app_config).__module__]
        box = {}

        def ready_thread():
            with mock.patch.object(app_module, "running_in_uvicorn", return_value=True), \
                    mock.patch.object(type(app_config), "is_running_in_celery", return_value=False), \
                    mock.patch.object(type(app_config), "validate_initial_data_path", return_value=True), \
                    mock.patch.object(LexProjectConfig, "load", return_value=config), \
                    mock.patch("lex.lex_app.tests.ProcessAdminTestCase.ProcessAdminTestCase", _Seed):
                box["thread"] = asyncio.run(app_config.async_ready(MODELS))
            connection.close()

        # The environment holds until the load thread has ended: it reads CELERY_ACTIVE.
        with mock.patch.dict(os.environ, env or {}, clear=False):
            starter = threading.Thread(target=ready_thread, name="lex-async-ready", daemon=True)
            starter.start()
            starter.join(60)
            load_thread = box.get("thread")
            self.assertIsNotNone(load_thread, "async_ready did not start the initial-data load")
            load_thread.join(60)
        self.assertFalse(load_thread.is_alive(), "the initial-data load never ended")
        return load_thread

    def test_1_609_the_load_thread_is_not_a_daemon(self) -> None:
        """
        Scenario 1.609: the server start runs the load in a non-daemon thread and loads the seed.
        Given: an empty database and a project naming initial data, started by `lex start`
        When: async_ready runs from the daemon lex-async-ready thread, as ready() calls it
        Then: the load runs in a thread named lex-initial-data-load with daemon=False (a
              daemon one is killed when its process stops), outside WaitForTasks, the
              rows land, and the load is recorded as completed.
        """
        fake = self._patch_load(_FakeLoad(rows=3))
        load_thread = self._async_ready_from_daemon_thread(env={"ARCHITECTURE": ""})

        self.assertEqual(load_thread.name, idl.THREAD_NAME)
        self.assertFalse(load_thread.daemon, "the load thread inherited daemon=True and dies with its process")
        self.assertEqual(len(fake.calls), 1, "the seed was not loaded exactly once")
        self.assertIs(fake.calls[0]["thread"], load_thread, "the load ran outside the non-daemon thread")
        self.assertFalse(fake.calls[0]["wait_for_tasks"], "the plain deployment must not wait for Celery tasks")
        self.assertEqual(SimpleItem.objects.count(), 3)
        self.assertEqual(idl.read_state(SEED)["status"], idl.COMPLETED)

    def test_1_613_mq_worker_loads_inside_wait_for_tasks(self) -> None:
        """
        Scenario 1.613: the MQ/Worker deployment runs the same load inside WaitForTasks.
        Given: DEPLOYMENT_ENVIRONMENT set, ARCHITECTURE=MQ/Worker and CELERY_ACTIVE=true
        When: async_ready starts the load
        Then: the load runs in the non-daemon load thread inside a WaitForTasks context,
              so Celery work it dispatches is awaited, and it is recorded as completed.
        """
        fake = self._patch_load(_FakeLoad(rows=2))
        load_thread = self._async_ready_from_daemon_thread(
            env={"DEPLOYMENT_ENVIRONMENT": "test", "ARCHITECTURE": "MQ/Worker", "CELERY_ACTIVE": "true"}
        )

        self.assertFalse(load_thread.daemon)
        self.assertEqual(len(fake.calls), 1)
        self.assertTrue(fake.calls[0]["wait_for_tasks"], "the MQ/Worker load ran outside WaitForTasks")
        self.assertEqual(SimpleItem.objects.count(), 2)
        self.assertEqual(idl.read_state(SEED)["status"], idl.COMPLETED)


@requires_postgres
class TestCluster01bj_OneProcessAtATime(_LoadCase):
    """A second server process waits for the first one's load, then decides on the result."""

    def test_1_610_second_process_waits_and_never_reads_a_half_load(self) -> None:
        """
        Scenario 1.610: a process that starts during another one's load waits for it.
        Given: another server process holds the load lock and is mid-load (no rows yet visible
               as finished)
        When: this process runs the initial-data decision
        Then: it neither loads nor reports "data exists" while the other load runs; once the
              other load finishes it reports the normal skip, and the seed exists once.
        """
        fake = self._patch_load(_FakeLoad())
        other = _OtherProcess()
        runner, box = _run_in_thread(should_stop=lambda: False)

        time.sleep(0.5)
        self.assertTrue(runner.is_alive(), "the second process decided while the first was still loading")
        self.assertEqual(fake.calls, [], "the second process loaded a second copy")

        def load_in_other_process():
            idl.write_state(SEED, idl.RUNNING)
            for index in range(4):
                SimpleItem.objects.create(name=f"other-{index}")
            idl.write_state(SEED, idl.COMPLETED)

        other.finish(load_in_other_process)
        runner.join(30)

        self.assertEqual(box.get("outcome"), idl.OUTCOME_SKIPPED)
        self.assertEqual(fake.calls, [], "the seed was loaded twice")
        self.assertEqual(SimpleItem.objects.count(), 4)

    def test_1_611_a_killed_load_is_reported_as_interrupted(self) -> None:
        """
        Scenario 1.611: the load of a process that is killed outright is reported as interrupted.
        Given: another process started the load, wrote part of the seed and was killed (its
               session ends without finishing or releasing the lock)
        When: this process, waiting for the lock, gets it
        Then: it does not load over the partial rows, does not call them existing data, logs
              an ERROR naming the interrupted load and how to recover, and keeps the record
              as running.
        """
        fake = self._patch_load(_FakeLoad())
        other = _OtherProcess()
        runner, box = _run_in_thread(should_stop=lambda: False)

        def partial_load():
            idl.write_state(SEED, idl.RUNNING)
            for index in range(2):
                SimpleItem.objects.create(name=f"partial-{index}")

        with self.assertLogs(idl.logger.name, level="ERROR") as logs:
            other.finish(partial_load, die=True)
            runner.join(30)

        self.assertEqual(box.get("outcome"), idl.OUTCOME_INTERRUPTED)
        self.assertEqual(fake.calls, [], "lex loaded over a partial seed")
        self.assertEqual(SimpleItem.objects.count(), 2)
        self.assertIn("never finished", "\n".join(logs.output))
        self.assertEqual(idl.read_state(SEED)["status"], idl.RUNNING)

    def test_1_612_a_stopping_process_does_not_wait_forever(self) -> None:
        """
        Scenario 1.612: a process that is shutting down stops waiting for another one's lock.
        Given: another process holds the load lock
        When: this process runs the decision while it is already stopping
        Then: it returns 'abandoned' without loading, so its exit is not held up.
        """
        fake = self._patch_load(_FakeLoad())
        other = _OtherProcess()
        try:
            outcome = idl.run_initial_data_load(_Seed(), MODELS, SEED, False, should_stop=lambda: True)
        finally:
            other.finish()
        self.assertEqual(outcome, idl.OUTCOME_ABANDONED)
        self.assertEqual(fake.calls, [])
        self.assertEqual(SimpleItem.objects.count(), 0)

    def test_1_617_the_lock_is_free_after_a_load(self) -> None:
        """
        Scenario 1.617: the load releases its lock when it ends.
        Given: an empty database
        When: the load runs to completion
        Then: another session can take the load lock at once.
        """
        self._patch_load(_FakeLoad(rows=1))
        runner, box = _run_in_thread()
        runner.join(30)
        self.assertEqual(box.get("outcome"), idl.OUTCOME_LOADED)

        taken = {}

        def probe():
            try:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT pg_try_advisory_lock(%s)", [idl.LOCK_KEY])
                    taken["lock"] = cursor.fetchone()[0]
            finally:
                connection.close()

        prober = threading.Thread(target=probe)
        prober.start()
        prober.join(30)
        self.assertTrue(taken.get("lock"), "the load kept its lock after it ended")


class TestCluster01bj_RecordedOutcome(_LoadCase):
    """How the last load ended decides what the next start says and does."""

    def test_1_615_a_failed_load_is_recorded_and_reported_next_start(self) -> None:
        """
        Scenario 1.615: a load that fails is recorded, and the next start reports it.
        Given: an empty database and a seed that fails after writing one row
        When: the load runs, and then the server starts again
        Then: the first run ends 'failed' with the error recorded; the second does not load
              again and reports the earlier failure instead of "data exists".
        """
        fake = self._patch_load(_FakeLoad(rows=3, fail_after=1))
        runner, box = _run_in_thread()
        runner.join(30)
        self.assertEqual(box.get("outcome"), idl.OUTCOME_FAILED)
        state = idl.read_state(SEED)
        self.assertEqual(state["status"], idl.FAILED)
        self.assertIn("RuntimeError: boom", state["error"])

        with self.assertLogs(idl.logger.name, level="ERROR"):
            runner, box = _run_in_thread()
            runner.join(30)
        self.assertEqual(box.get("outcome"), idl.OUTCOME_FAILED_EARLIER)
        self.assertEqual(len(fake.calls), 1, "a partial seed was loaded over")

    def test_1_616_a_load_that_died_before_writing_is_redone(self) -> None:
        """
        Scenario 1.616: a load recorded as running that left no rows is simply redone.
        Given: a record saying a load started, and every seed model still empty
        When: the server starts
        Then: the seed is loaded and recorded as completed.
        """
        idl.write_state(SEED, idl.RUNNING)
        fake = self._patch_load(_FakeLoad(rows=2))
        runner, box = _run_in_thread()
        runner.join(30)
        self.assertEqual(box.get("outcome"), idl.OUTCOME_LOADED)
        self.assertEqual(len(fake.calls), 1)
        self.assertEqual(SimpleItem.objects.count(), 2)
        self.assertEqual(idl.read_state(SEED)["status"], idl.COMPLETED)

    def test_1_618_existing_data_is_still_a_plain_skip(self) -> None:
        """
        Scenario 1.618: rows that exist after a completed load (or with no record) are left alone.
        Given: a seed model with rows, with no record and with a completed record
        When: the server starts
        Then: nothing is loaded and the outcome is the ordinary skip, logged without ERROR.
        """
        SimpleItem.objects.create(name="existing")
        fake = self._patch_load(_FakeLoad())
        for prepare in (lambda: None, lambda: (idl.write_state(SEED, idl.RUNNING), idl.write_state(SEED, idl.COMPLETED))):
            prepare()
            runner, box = _run_in_thread()
            runner.join(30)
            self.assertEqual(box.get("outcome"), idl.OUTCOME_SKIPPED)
        self.assertEqual(fake.calls, [])
        self.assertEqual(SimpleItem.objects.count(), 1)


class TestCluster01bj_ProcessExit(unittest.TestCase):
    """A real interpreter that stops while the load runs."""

    def test_1_614_a_stopping_process_waits_for_the_load(self) -> None:
        """
        Scenario 1.614: a process whose main thread ends mid-load finishes the load before exiting.
        Given: a child interpreter whose daemon thread (as lex-async-ready) starts the load,
               and a load that takes 1.5 s
        When: the main thread returns 0.3 s later (the server stopped, e.g. for a reload)
        Then: the process exits only after the load finished; with a daemon load thread the
              load would be cut off and its file never written.
        """
        out = Path(tempfile.mkdtemp(prefix="cluster1bj_")) / "load.done"
        code = (
            "import pathlib, sys, threading, time\n"
            "from lex.lex_app import initial_data_load as m\n"
            "out = pathlib.Path(sys.argv[1])\n"
            "def slow_load(*args):\n"
            "    time.sleep(1.5)\n"
            "    out.write_text('finished')\n"
            "m.run_initial_data_load = slow_load\n"
            "threading.Thread(target=lambda: m.start_initial_data_load(None, {}, 'seed', False),\n"
            "                 name='lex-async-ready', daemon=True).start()\n"
            "time.sleep(0.3)\n"
        )
        env = {k: v for k, v in os.environ.items() if k not in ("DJANGO_SETTINGS_MODULE", "PYTHONPATH")}
        env["PYTHONPATH"] = _CHECKOUT.as_posix()
        result = subprocess.run(
            [sys.executable, "-c", code, out.as_posix()], env=env, capture_output=True, text=True, timeout=60
        )
        self.assertEqual(result.returncode, 0, result.stderr[-3000:])
        self.assertTrue(out.exists(), "the process exited before the initial-data load finished")
