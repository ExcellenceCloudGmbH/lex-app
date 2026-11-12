"""Setting up a Lex project leaves asyncio as the standard library made it.

Intent
------
Every process a Lex project runs (the web server, a Celery worker, a
management command, Streamlit) sets Django up, and ``LexAppConfig.ready()``
runs the project's startup. That startup must not change asyncio for the
whole process. Synchronous code in such a process can then call into async
code with ``async_to_sync`` as often as it needs to, from any thread. This is
the pattern Django's async ORM, Channels' ``group_send`` and the Reflex ORM
bridge all rely on.

``ready()`` used to call ``nest_asyncio.apply()``, which replaced
``asyncio.run`` with a version that never closes its loop. The first
``async_to_sync`` with thread-sensitive work left that open loop in the
calling thread, and the second waited forever on it (BUG-046). It happened in
every downstream process, so a Celery task, a command or a dashboard could
hang on its second call.

Each scenario sets up a real downstream project (a folder with
``lex_config.py``) in a fresh interpreter. The patch is process-wide state,
and a regression must fail here, not hang the rest of the suite. If the calls
hang, a watchdog in the child prints every thread's stack and exits.

Cluster 1bo -- scenarios 1.665-1.666. Type: U (fresh interpreter, no database).
Covers: lex/lex_app/apps.py (``LexAppConfig.ready()``), lex/bin/lex.py (the
        CLI environment the child starts from), lex/lex_app/asgi.py.
Run: python -m lex pytest lex/test_project/tests/init/test_1bo_bootstrap_leaves_asyncio_unpatched.py -v
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest
from django.test import SimpleTestCase

import lex

pytestmark = pytest.mark.init

#: The checkout holding the ``lex`` package, so the child imports this lex.
_CHECKOUT = Path(lex.__file__).resolve().parents[1]

#: Variables the child must not inherit from the test process.
_CLEARED = (
    "DJANGO_SETTINGS_MODULE",
    "PROJECT_ROOT",
    "PYTHONPATH",
    "GIT_CEILING_DIRECTORIES",
    "CALLED_FROM_START_COMMAND",
    "DJANGO_ALLOW_ASYNC_UNSAFE",
)

#: Seconds the two async_to_sync calls may take before the child's watchdog
#: dumps every thread and exits. They take milliseconds when nothing is wrong.
_CALLS_WATCHDOG = 60

#: Seconds for the whole child, Django setup included: a backstop only.
_PROCESS_TIMEOUT = 300

#: The child. It sets the project up the way the route under test does, then
#: calls into async code twice from its main thread. Each call does
#: thread-sensitive work, which runs back on the calling thread. It prints one
#: JSON line.
_CHILD = r"""
import asyncio, faulthandler, json, os, sys, threading

import lex.bin.lex  # what every `lex` command does first: PROJECT_ROOT, settings

route = sys.argv[1]
if route == "no-loop":
    # A management command, a Celery worker, a Streamlit script.
    import django

    django.setup()
else:
    # `lex start`: uvicorn imports the application inside its running loop.
    loop = asyncio.new_event_loop()

    async def load_application():
        import lex_app.asgi  # noqa: F401  -- Django setup and ready() run here

    try:
        loop.run_until_complete(load_application())
    finally:
        loop.close()

from asgiref.sync import async_to_sync, sync_to_async
from lex.lex_app.settings import repo_name


async def calling_thread():
    return await sync_to_async(threading.get_ident, thread_sensitive=True)()


faulthandler.dump_traceback_later(int(os.environ["CLUSTER1AY_WATCHDOG"]), exit=True)
first = async_to_sync(calling_thread)()
second = async_to_sync(calling_thread)()
faulthandler.cancel_dump_traceback_later()
print(json.dumps({
    "project": repo_name,
    "asyncio.run": asyncio.run.__module__,
    "ran on the calling thread": [first == threading.get_ident(), second == threading.get_ident()],
}))
"""


def _tail(output) -> str:
    """The last part of a child's output, whether it came back as text or bytes."""
    if output is None:
        return ""
    if isinstance(output, bytes):
        output = output.decode(errors="replace")
    return output[-4000:]


def _downstream_project() -> Path:
    """A minimal downstream Lex project: a folder holding ``lex_config.py``.

    The folder's name is the project's name, so it does not start with ``lex``
    (Lex's own checkouts skip the project startup) and is a valid identifier.
    """
    project = Path(tempfile.mkdtemp(prefix="cluster1bo_")).resolve()
    (project / "__init__.py").write_text("")
    (project / "lex_config.py").write_text(f'PROJECT_GROUPS = ["{project.name}"]\n')
    return project


class TestCluster01bo_BootstrapLeavesAsyncioUnpatched(SimpleTestCase):
    """Cluster 1bo: a project's startup leaves asyncio and async_to_sync usable."""

    def _set_up_project(self, route: str) -> dict:
        """Set a fresh downstream project up by ``route``; return what it reports."""
        project = _downstream_project()
        self.addCleanup(shutil.rmtree, project, ignore_errors=True)
        env = {key: value for key, value in os.environ.items() if key not in _CLEARED}
        env["PYTHONPATH"] = _CHECKOUT.as_posix()
        env["CLUSTER1AY_WATCHDOG"] = str(_CALLS_WATCHDOG)
        try:
            result = subprocess.run(
                [sys.executable, "-c", _CHILD, route],
                cwd=project,
                env=env,
                capture_output=True,
                text=True,
                timeout=_PROCESS_TIMEOUT,
            )
        except subprocess.TimeoutExpired as exc:
            self.fail(f"setting the project up ({route}) did not finish in {_PROCESS_TIMEOUT}s:\n{_tail(exc.stderr)}")
        self.assertEqual(
            result.returncode,
            0,
            f"setting the project up ({route}) failed. A 'Timeout' dump here means an "
            f"async_to_sync call waited forever:\n{_tail(result.stderr)}",
        )
        report = json.loads(result.stdout.strip().splitlines()[-1])
        self.assertEqual(
            report["project"], project.name,
            "precondition: the child must have set up the downstream project, whose startup is under test",
        )
        return report

    def _assert_unpatched_and_usable(self, route: str, report: dict) -> None:
        """asyncio is the standard library's, and both calls returned on the calling thread."""
        self.assertEqual(
            report["asyncio.run"],
            "asyncio.runners",
            f"a project's startup ({route}) must leave asyncio.run as the standard library's, "
            "not patched for the whole process",
        )
        self.assertEqual(
            report["ran on the calling thread"],
            [True, True],
            f"after a project's startup ({route}), two async_to_sync calls must both return, "
            "each running its thread-sensitive work on the calling thread",
        )

    # -- 1.665 ---------------------------------------------------------
    def test_1_665_startup_with_no_loop_leaves_async_to_sync_usable(self) -> None:
        """
        Scenario 1.665: a project set up with no event loop leaves asyncio alone.
        Given: a downstream Lex project and a fresh process with no running
               loop: a management command, a Celery worker, a Streamlit script.
        When:  Django is set up (``LexAppConfig.ready()`` runs the project's
               startup), and then the main thread calls ``async_to_sync`` twice,
               each time with thread-sensitive work.
        Then:  ``asyncio.run`` is still the standard library's, and both calls
               return. The second one used to wait forever (BUG-046).
        """
        self._assert_unpatched_and_usable("no running loop", self._set_up_project("no-loop"))

    # -- 1.666 ---------------------------------------------------------
    def test_1_666_startup_inside_the_servers_loop_leaves_async_to_sync_usable(self) -> None:
        """
        Scenario 1.666: a project set up inside a running loop leaves asyncio alone.
        Given: a downstream Lex project whose ASGI application (``lex_app.asgi``,
               what ``lex start`` serves) is imported inside a running asyncio
               loop, as uvicorn does.
        When:  Django is set up there, and afterwards the main thread calls
               ``async_to_sync`` twice, each time with thread-sensitive work.
        Then:  ``asyncio.run`` is still the standard library's, and both calls
               return.
        """
        self._assert_unpatched_and_usable("inside a running loop", self._set_up_project("asyncio"))
