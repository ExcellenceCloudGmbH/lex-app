"""lex.ui: a handler whose process died is not "running" forever.

Intent: the view's spinner comes from the state. If the backend process that ran a job dies,
nobody clears the mark, and with state kept on disk or in Redis the mark survives the restart.
So each mark records the process that set it, every page open sweeps marks whose process is
gone, and with Redis each process keeps a heartbeat another worker can check. A regression is a
button that stays disabled until the user opens a new tab.

Cluster 1ck -- scenarios 1.779-1.780. Type: U.
Covers: lex/lex_app/reflex/ui/runs.py (sweep, mark_finished, alive, heartbeat), ui/state.py (ui_open).
Run: python -m lex pytest lex/test_project/tests/init/test_1ck_ui_stale_runs.py -v
"""

from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

import pytest

from lex.lex_app.reflex.ui import runs, testing
from lex.lex_app.reflex.ui.state import State

pytestmark = pytest.mark.init


def _status():
    return SimpleNamespace(ui_running={}, ui_failed={}, ui_progress={}, ui_progress_message={}, _ui_boots={})


class _FakeRedis:
    """A Redis stand-in: records keys with their expiry; ``set`` can be made to fail or hang."""

    def __init__(self, *, exists_error=None, hang=False, set_failures=0):
        self.keys = {}
        self.exists_error = exists_error
        self.hang = hang
        self.set_failures = set_failures
        self.closed = 0

    async def set(self, key, value, ex=None):
        if self.set_failures:
            self.set_failures -= 1
            raise ConnectionError("down")
        self.keys[key] = ex

    async def exists(self, key):
        if self.hang:
            await asyncio.sleep(30)
        if self.exists_error:
            raise self.exists_error
        return 1 if key in self.keys else 0

    async def delete(self, key):
        self.keys.pop(key, None)

    async def aclose(self):
        self.closed += 1


class TestCluster01ck_StaleRuns(TestCase):
    """1.779-1.780."""

    # -- 1.779 ---------------------------------------------------------
    def test_1_779_a_sweep_clears_runs_of_a_process_that_is_gone(self) -> None:
        """
        Scenario 1.779: the sweep.
        Given: a state marked running by this process for one handler, and by a process that
               no longer exists for another.
        When:  the state is swept.
        Then:  the dead process's run is cleared, with its progress; this process's run stays;
               a process another worker reports alive keeps its run; an argument containing
               "|" is cleared completely; a dead process is asked about once however many
               runs it had; two runs under one key and argument keep their process record until
               the last finishes, and a sweep clears them all; a liveness check that hangs ends
               the sweep within its budget and leaves the marks; opening a page sweeps.
        """
        s = _status()
        runs.mark_started(s, "A.mine", "1")
        runs.mark_started(s, "B.theirs", "2")
        s._ui_boots = {**s._ui_boots, "B.theirs|2": "dead-process"}
        s.ui_progress = {"B.theirs": 0.5}

        async def nobody(boot):
            return False

        self.assertEqual(asyncio.run(runs.sweep(s, alive=nobody)), 1, "one dead run is cleared")
        self.assertEqual(s.ui_running, {"A.mine": ["1"]}, "only this process's run stays")
        self.assertNotIn("B.theirs", s.ui_progress, "the dead run's progress goes with it")
        s2 = _status()
        runs.mark_started(s2, "B.theirs", "2")
        s2._ui_boots = {"B.theirs|2": "other-worker"}

        async def everybody(boot):
            return True

        self.assertEqual(asyncio.run(runs.sweep(s2, alive=everybody)), 0, "a live worker's run stays")

        # an argument with "|" in it
        s3 = _status()
        runs.mark_started(s3, "B.theirs", "a|b|c")
        s3._ui_boots = {"B.theirs|a|b|c": "dead-process"}
        self.assertEqual(asyncio.run(runs.sweep(s3, alive=nobody)), 1, "the run with a '|' argument is swept")
        self.assertEqual(s3.ui_running, {}, "the run is not left running with its record gone")
        self.assertEqual(s3._ui_boots, {}, "its process record is gone too")

        # two runs with the same key and argument: the first to finish keeps the other's record
        s6 = _status()
        runs.mark_started(s6, "B.theirs", "5")
        runs.mark_started(s6, "B.theirs", "5")
        runs.mark_finished(s6, "B.theirs", "5")
        self.assertEqual((s6.ui_running, s6._ui_boots), ({"B.theirs": ["5"]}, {"B.theirs|5": runs.BOOT}),
                         "the run still in flight keeps its mark and its process record")
        runs.mark_finished(s6, "B.theirs", "5")
        self.assertEqual((s6.ui_running, s6._ui_boots), ({}, {}), "the last run to finish removes both")
        s7 = _status()
        runs.mark_started(s7, "B.theirs", "5")
        runs.mark_started(s7, "B.theirs", "5")
        s7._ui_boots = {"B.theirs|5": "dead-process"}
        asyncio.run(runs.sweep(s7, alive=nobody))
        self.assertEqual((s7.ui_running, s7._ui_boots), ({}, {}),
                         "a sweep clears every run of a dead process under one record")

        # one question per dead process
        s4 = _status()
        for arg in ("1", "2", "3"):
            runs.mark_started(s4, "B.theirs", arg)
        s4._ui_boots = {f"B.theirs|{arg}": "dead-process" for arg in ("1", "2", "3")}
        asked = []

        async def counting(boot):
            asked.append(boot)
            return False

        self.assertEqual(asyncio.run(runs.sweep(s4, alive=counting)), 3, "all three runs are cleared")
        self.assertEqual(asked, ["dead-process"], "the process is asked about once, not once per run")

        # a hanging check
        s5 = _status()
        runs.mark_started(s5, "B.theirs", "9")
        s5._ui_boots = {"B.theirs|9": "dead-process"}

        async def hangs(boot):
            await asyncio.sleep(30)

        started = time.monotonic()
        with patch.object(runs, "REDIS_TIMEOUT_SECONDS", 0.05):
            cleared = asyncio.run(runs.sweep(s5, alive=hangs))
        self.assertLess(time.monotonic() - started, 5, "the sweep gives up within its budget")
        self.assertEqual((cleared, s5.ui_running), (0, {"B.theirs": ["9"]}), "an unanswered mark is left for later")

        # a page open sweeps
        async def scenario():
            async with testing.Harness() as h:
                state = await h._state(State)
                runs.mark_started(state, "B.theirs", "7")
                state._ui_boots = {"B.theirs|7": "dead-process"}
                await (await h.send(State, "ui_open", page="/t1ck"))
                return (await h.fields(State))["ui_running"]

        self.assertEqual(asyncio.run(scenario()), {}, "opening a page clears a dead process's mark")

    # -- 1.780 ---------------------------------------------------------
    def test_1_780_liveness_is_local_without_redis_and_a_heartbeat_with_it(self) -> None:
        """
        Scenario 1.780: who is alive.
        Given: no Redis; then a Redis stand-in.
        When:  liveness is asked for this process, for another one, and the heartbeat runs.
        Then:  without Redis only this process is alive (one process holds every tab); with
               Redis a process is alive while its heartbeat key exists, and the heartbeat writes
               this process's key with an expiry; a Redis that errors, hangs or cannot be built
               leaves a process counted alive; a failed beat does not end the heartbeat; ending
               it deletes the key and closes the one client; a forked child gets its own id.
        """
        with patch.object(runs, "_redis", return_value=None):
            self.assertEqual((asyncio.run(runs.alive(runs.BOOT)), asyncio.run(runs.alive("other"))), (True, False),
                             "without Redis only this process is alive")

        redis = _FakeRedis()

        async def scenario():
            with patch.object(runs, "_redis", return_value=redis), patch.object(runs, "HEARTBEAT_SECONDS", 0.01):
                async with runs.heartbeat(None):
                    await asyncio.sleep(0.05)
                    return await runs.alive(runs.BOOT), await runs.alive("other")

        self.assertEqual(asyncio.run(scenario()), (True, False), "with Redis the heartbeat key decides")
        self.assertEqual(redis.keys, {}, "ending the heartbeat deletes this process's key")
        self.assertEqual(redis.closed, 1, "ending the heartbeat closes the client")

        key = f"lex:ui:boot:{runs.BOOT}"

        async def while_running():
            fake = _FakeRedis()
            with patch.object(runs, "_redis", return_value=fake), patch.object(runs, "HEARTBEAT_SECONDS", 0.01):
                async with runs.heartbeat(None):
                    await asyncio.sleep(0.05)
                    return dict(fake.keys)

        self.assertEqual(asyncio.run(while_running()), {key: runs.HEARTBEAT_TTL_SECONDS},
                         "the heartbeat writes this process's key with an expiry")

        # fail open
        def verdict(fake):
            with patch.object(runs, "_redis", return_value=fake), patch.object(runs, "REDIS_TIMEOUT_SECONDS", 0.05):
                return asyncio.run(runs.alive("other"))

        self.assertTrue(verdict(_FakeRedis(exists_error=ConnectionError("down"))), "an erroring Redis fails open")
        self.assertTrue(verdict(_FakeRedis(hang=True)), "a hanging Redis fails open")
        with patch.object(runs, "_redis", side_effect=RuntimeError("misconfigured")):
            self.assertTrue(asyncio.run(runs.alive("other")), "a client that cannot be built fails open")

        # a failed beat does not end the loop
        flaky = _FakeRedis(set_failures=2)

        async def flaky_beats():
            with patch.object(runs, "_redis", return_value=flaky), patch.object(runs, "HEARTBEAT_SECONDS", 0.01):
                async with runs.heartbeat(None):
                    await asyncio.sleep(0.1)
                    return dict(flaky.keys)

        self.assertEqual(asyncio.run(flaky_beats()), {key: runs.HEARTBEAT_TTL_SECONDS},
                         "a later beat still writes the key after failed ones")

        # one client per process
        built = []

        def build():
            built.append(1)
            return _FakeRedis()

        from reflex.utils import prerequisites

        with patch.object(runs, "_CLIENT", None), patch.object(prerequisites, "get_redis", build):
            first, second = runs._redis(), runs._redis()
        self.assertIs(first, second, "the client is cached")
        self.assertEqual(len(built), 1, "the client is built once")

        # a forked child
        boot, client = runs.BOOT, runs._CLIENT
        with patch.object(runs, "BOOT", boot), patch.object(runs, "_CLIENT", _FakeRedis()):
            runs._renew_after_fork()
            self.assertNotEqual(runs.BOOT, boot, "a forked child has its own process id")
            self.assertIsNone(runs._CLIENT, "and drops its parent's client")
