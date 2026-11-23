"""lex.ui: a handler whose process died is not "running" forever.

Intent: the view's spinner comes from the state. If the backend process that ran a job dies,
nobody clears the mark, and with state kept on disk or in Redis the mark survives the restart.
So each mark records the process that set it, every page open sweeps marks whose process is
gone, and with Redis each process keeps a heartbeat another worker can check. A regression is a
button that stays disabled until the user opens a new tab.

Cluster 1ck -- scenarios 1.779-1.780. Type: U.
Covers: lex/lex_app/reflex/ui/runs.py (sweep, alive, heartbeat), ui/state.py (ui_open).
Run: python -m lex pytest lex/test_project/tests/init/test_1ck_ui_stale_runs.py -v
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

import pytest

from lex.lex_app.reflex.ui import runs

pytestmark = pytest.mark.init


def _status():
    return SimpleNamespace(ui_running={}, ui_failed={}, ui_progress={}, ui_progress_message={}, _ui_boots={})


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
               a process another worker reports alive keeps its run.
        """
        s = _status()
        runs.mark_started(s, "A.mine", "1")
        runs.mark_started(s, "B.theirs", "2")
        s._ui_boots = {**s._ui_boots, "B.theirs|2": "dead-process"}
        s.ui_progress = {"B.theirs": 0.5}

        async def nobody(boot):
            return False

        self.assertEqual(asyncio.run(runs.sweep(s, alive=nobody)), 1)
        self.assertEqual(s.ui_running, {"A.mine": ["1"]})
        self.assertNotIn("B.theirs", s.ui_progress)
        s2 = _status()
        runs.mark_started(s2, "B.theirs", "2")
        s2._ui_boots = {"B.theirs|2": "other-worker"}

        async def everybody(boot):
            return True

        self.assertEqual(asyncio.run(runs.sweep(s2, alive=everybody)), 0, "a live worker's run stays")

    # -- 1.780 ---------------------------------------------------------
    def test_1_780_liveness_is_local_without_redis_and_a_heartbeat_with_it(self) -> None:
        """
        Scenario 1.780: who is alive.
        Given: no Redis; then a Redis stand-in.
        When:  liveness is asked for this process, for another one, and the heartbeat runs.
        Then:  without Redis only this process is alive (one process holds every tab); with
               Redis a process is alive while its heartbeat key exists, and the heartbeat writes
               this process's key with an expiry.
        """
        with patch.object(runs, "_redis", return_value=None):
            self.assertEqual((asyncio.run(runs.alive(runs.BOOT)), asyncio.run(runs.alive("other"))), (True, False))

        class FakeRedis:
            def __init__(self):
                self.keys = {}

            async def set(self, key, value, ex=None):
                self.keys[key] = ex

            async def exists(self, key):
                return 1 if key in self.keys else 0

        redis = FakeRedis()

        async def scenario():
            with patch.object(runs, "_redis", return_value=redis), patch.object(runs, "HEARTBEAT_SECONDS", 0.01):
                async with runs.heartbeat(None):
                    await asyncio.sleep(0.05)
                    return await runs.alive(runs.BOOT), await runs.alive("other")

        self.assertEqual(asyncio.run(scenario()), (True, False))
        self.assertEqual(redis.keys, {f"lex:ui:boot:{runs.BOOT}": runs.HEARTBEAT_TTL_SECONDS})
