"""The startup reset works from any caller and leaves asyncio usable afterwards.

Intent
------
On ``lex start`` the framework resets every calculation row that a crash left
``IN_PROGRESS``: ``ModelRegistration.register_models`` runs the startup reset
for each ``CalculationModel``. The reset runs wherever Django is set up:

* in a process with no event loop (a management command, a Celery worker);
* inside a running asyncio loop, because uvicorn imports the application
  inside its own loop. ``--loop asyncio`` is the documented deployment;
* inside a running uvloop loop, which uvicorn picks by default whenever uvloop
  is installed. ``uvicorn[standard]`` installs it.

On every route, the reset must abort the stuck row and leave the process
able to call into async code from synchronous code as often as it needs to.
The reset used to run through ``nest_asyncio``, which patched asyncio for
the whole process, and ``asyncio.run`` stopped closing its loop. A thread
that called ``async_to_sync`` twice, with thread-sensitive work such as an
ORM call in between, then waited forever on a loop nobody ran. On uvloop the
patch failed outright and stopped startup (BUG-046).

Each route runs in a fresh interpreter. What is under test is process-wide
state, and a regression must fail this test, not hang the rest of the suite.
If the calls hang, a watchdog in the child prints every thread's stack and
exits.

Cluster 8d -- scenarios 8.154-8.156. Type: I (a real ``CalculationModel`` row,
reset by a separate process on this test's database).
Covers: lex/process_admin/utils/model_registration.py (``register_models``,
        ``_handle_calculation_model_reset``, ``_run_on_own_thread``).
Run: python -m lex pytest lex/test_project/tests/celery_async/test_8d_startup_reset_event_loops.py -v
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path
from unittest import skipUnless

import pytest
from django.db import connection

import lex
from lex.core.models.CalculationModel import CalculationModel
from lex.test_project.tests._e2e_test_case import E2ETestCase

from .models import CelerySyncCalc

pytestmark = pytest.mark.celery_async

#: The checkout holding the ``lex`` package, so the child imports this lex.
_CHECKOUT = Path(lex.__file__).resolve().parents[1]

#: Variables the child must not inherit from the test process. Without
#: DJANGO_ALLOW_ASYNC_UNSAFE, Django refuses ORM calls on a running loop, as it
#: does in production.
_CLEARED = (
    "DJANGO_SETTINGS_MODULE",
    "PROJECT_ROOT",
    "PYTHONPATH",
    "CALLED_FROM_START_COMMAND",
    "DJANGO_ALLOW_ASYNC_UNSAFE",
)

#: Seconds the two async_to_sync calls may take before the child's watchdog
#: dumps every thread and exits. They take milliseconds when nothing is wrong.
_CALLS_WATCHDOG = 60

#: Seconds for the whole child, Django setup included: a backstop only.
_PROCESS_TIMEOUT = 300

_HAS_UVLOOP = importlib.util.find_spec("uvloop") is not None

#: The child. It starts the way the route under test does, runs the startup
#: reset through the public entry point, then crosses into async code twice
#: from its main thread with thread-sensitive ORM work. It prints one JSON line.
_CHILD = r"""
import asyncio, faulthandler, json, os, sys

import lex.bin.lex  # what every `lex` command does first: PROJECT_ROOT, settings
from django.conf import settings

# The parent test's database, so the row it left IN_PROGRESS is the one reset.
settings.DATABASES["default"]["NAME"] = os.environ["CLUSTER8D_DB_NAME"]

import django

django.setup()

from asgiref.sync import async_to_sync, sync_to_async
from lex.process_admin.utils.model_registration import ModelRegistration
from lex.test_project.tests.celery_async.models import CelerySyncCalc


def register_project_models():
    # What LexAppConfig.ready() does with a project's models under `lex start`.
    ModelRegistration.register_models([CelerySyncCalc])


route = sys.argv[1]
os.environ["CALLED_FROM_START_COMMAND"] = "True"
if route == "no-loop":
    register_project_models()
else:
    if route == "uvloop":
        import uvloop

        loop = uvloop.new_event_loop()
    else:
        loop = asyncio.new_event_loop()

    async def load_application():
        # uvicorn imports the application, and with it Django setup and
        # ready(), inside its running loop.
        register_project_models()

    try:
        loop.run_until_complete(load_application())
    finally:
        loop.close()
del os.environ["CALLED_FROM_START_COMMAND"]


async def count_rows():
    return await sync_to_async(CelerySyncCalc.objects.count, thread_sensitive=True)()


faulthandler.dump_traceback_later(int(os.environ["CLUSTER8D_WATCHDOG"]), exit=True)
first = async_to_sync(count_rows)()
second = async_to_sync(count_rows)()
faulthandler.cancel_dump_traceback_later()
print(json.dumps({"asyncio.run": asyncio.run.__module__, "calls": [first, second]}))
"""


def _tail(output) -> str:
    """The last part of a child's output, whether it came back as text or bytes."""
    if output is None:
        return ""
    if isinstance(output, bytes):
        output = output.decode(errors="replace")
    return output[-4000:]


class TestCluster08d_StartupResetLeavesAsyncioUsable(E2ETestCase):
    """Cluster 8d: the startup reset, on every route a process can start by."""

    e2e_models = [CelerySyncCalc]

    def _stuck_row(self, name: str) -> CelerySyncCalc:
        """A row that a crash left IN_PROGRESS and no recovery task owns."""
        row = CelerySyncCalc.objects.create(name=name)
        row.is_calculated = CalculationModel.IN_PROGRESS
        row.save(skip_hooks=True)
        return row

    def _start(self, route: str) -> dict:
        """Start a fresh process by ``route``; return the JSON it reports."""
        env = {key: value for key, value in os.environ.items() if key not in _CLEARED}
        env["PYTHONPATH"] = _CHECKOUT.as_posix()
        env["CLUSTER8D_DB_NAME"] = connection.settings_dict["NAME"]
        env["CLUSTER8D_WATCHDOG"] = str(_CALLS_WATCHDOG)
        try:
            result = subprocess.run(
                [sys.executable, "-c", _CHILD, route],
                cwd=_CHECKOUT,
                env=env,
                capture_output=True,
                text=True,
                timeout=_PROCESS_TIMEOUT,
            )
        except subprocess.TimeoutExpired as exc:
            self.fail(f"the {route} start did not finish in {_PROCESS_TIMEOUT}s:\n{_tail(exc.stderr)}")
        self.assertEqual(
            result.returncode,
            0,
            f"the {route} start failed. A 'Timeout' dump here means an async_to_sync "
            f"call waited forever:\n{_tail(result.stderr)}",
        )
        return json.loads(result.stdout.strip().splitlines()[-1])

    def _assert_reset_and_usable(self, route: str, row: CelerySyncCalc, report: dict) -> None:
        """The row is aborted, asyncio is the standard library's, both calls returned."""
        row.refresh_from_db()
        self.assertEqual(
            row.is_calculated,
            CalculationModel.ABORTED,
            f"the startup reset ({route}) must abort a stuck row that no recovery task owns",
        )
        self.assertEqual(
            report["asyncio.run"],
            "asyncio.runners",
            f"the startup reset ({route}) must leave asyncio as the standard library's, "
            "not patched for the whole process",
        )
        self.assertEqual(
            report["calls"],
            [1, 1],
            f"after the startup reset ({route}), two async_to_sync calls with ORM work "
            "in between must both return the row count",
        )

    # -- 8.154 ---------------------------------------------------------
    def test_08_154_reset_with_no_running_loop_leaves_async_to_sync_usable(self) -> None:
        """
        Scenario 8.154: the startup reset from a process with no event loop.
        Given: a row a crash left IN_PROGRESS, and a fresh process with no
               running loop, as a management command or a Celery worker has.
        When:  the process registers its models under ``lex start``, and then
               its main thread calls ``async_to_sync`` twice, each time with
               thread-sensitive ORM work.
        Then:  the row is ABORTED, ``asyncio.run`` is still the standard
               library's, and both calls return.
        """
        row = self._stuck_row("stuck before a command")
        self._assert_reset_and_usable("no running loop", row, self._start("no-loop"))

    # -- 8.155 ---------------------------------------------------------
    def test_08_155_reset_inside_a_running_asyncio_loop_leaves_async_to_sync_usable(self) -> None:
        """
        Scenario 8.155: the startup reset inside uvicorn's asyncio loop.
        Given: a row a crash left IN_PROGRESS, and a fresh process whose models
               are registered inside a running asyncio loop, where
               ``lex start --loop asyncio`` sets Django up.
        When:  the reset runs there, and afterwards the main thread calls
               ``async_to_sync`` twice, each time with thread-sensitive ORM work.
        Then:  the row is ABORTED (no SynchronousOnlyOperation, no "loop is
               already running"), ``asyncio.run`` is still the standard
               library's, and both calls return.
        """
        row = self._stuck_row("stuck before an asyncio start")
        self._assert_reset_and_usable("asyncio loop", row, self._start("asyncio"))

    # -- 8.156 ---------------------------------------------------------
    @skipUnless(_HAS_UVLOOP, "uvloop is not installed; uvicorn[standard] installs it everywhere but Windows")
    def test_08_156_reset_inside_a_running_uvloop_loop_does_not_stop_startup(self) -> None:
        """
        Scenario 8.156: the startup reset inside uvicorn's default uvloop loop.
        Given: a row a crash left IN_PROGRESS, and a fresh process whose models
               are registered inside a running uvloop loop. Plain ``lex start``
               picks uvloop whenever it is installed.
        When:  the reset runs there, and afterwards the main thread calls
               ``async_to_sync`` twice, each time with thread-sensitive ORM work.
        Then:  startup goes on (it used to fail with "Can't patch loop of type
               uvloop.Loop"), the row is ABORTED, ``asyncio.run`` is still the
               standard library's, and both calls return.
        """
        row = self._stuck_row("stuck before a uvloop start")
        self._assert_reset_and_usable("uvloop loop", row, self._start("uvloop"))
