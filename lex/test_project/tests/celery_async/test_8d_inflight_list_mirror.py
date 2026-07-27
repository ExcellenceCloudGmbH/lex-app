"""Cluster 8d — the in-flight LIST mirror that scales the recovery pod.

Intent: the recovery pod only has work while calculations are in flight — the
window in which the recovery registry's index SET is non-empty. KEDA's native
``redis`` scaler reads list length (``LLEN``), not set cardinality, so the
registry maintains a parallel LIST (``<id>:lex:recover:inflight``) that mirrors
index membership; KEDA points at its length to scale the pod 0↔1. The mirror
must track the SET exactly: a requeue re-register never double-counts, a
deregister drains every occurrence, and a supervisor booting mid-cutover (or
after a crash between the SET and LIST writes) rebuilds the list from the SET.
The signal fails safe by construction — a leaked list entry keeps the recovery
pod *up* (wasteful), never down (unsafe).
Cluster 8d — scenarios 8.157–8.160. Type: U.
Covers: lex/lex_app/celery_recovery/redis_keys.py (inflight_list_key),
        lex/lex_app/celery_recovery/registry.py (register/deregister mirror,
        reconcile_inflight_list),
        lex/lex_app/management/commands/run_recovery_supervisor.py
        (startup reconcile).
Run: python -m lex pytest lex/test_project/tests/celery_async/test_8d_inflight_list_mirror.py -v
"""

from __future__ import annotations

from unittest import mock

import pytest
from django.test import SimpleTestCase

from lex.lex_app.celery_recovery import redis_keys, registry, supervisor

pytestmark = pytest.mark.celery_async


class FakeRedis:
    """Just enough redis-py surface for the registry's key/set/list ops."""

    def __init__(self):
        self.kv = {}
        self.sets = {}
        self.lists = {}

    # -- strings ------------------------------------------------------
    def set(self, key, value, ex=None, nx=None):
        if nx and key in self.kv:
            return None
        self.kv[key] = value
        return True

    def get(self, key):
        return self.kv.get(key)

    def delete(self, key):
        self.kv.pop(key, None)
        self.sets.pop(key, None)
        self.lists.pop(key, None)

    def exists(self, key):
        return int(key in self.kv)

    def expire(self, key, ttl):
        return key in self.kv

    # -- sets ---------------------------------------------------------
    def sadd(self, key, *members):
        target = self.sets.setdefault(key, set())
        added = sum(1 for m in members if m not in target)
        target.update(members)
        return added

    def srem(self, key, *members):
        target = self.sets.get(key, set())
        removed = sum(1 for m in members if m in target)
        target.difference_update(members)
        return removed

    def smembers(self, key):
        return set(self.sets.get(key, set()))

    # -- lists --------------------------------------------------------
    def lpush(self, key, *values):
        target = self.lists.setdefault(key, [])
        for value in values:
            target.insert(0, value)
        return len(target)

    def lrem(self, key, count, value):
        target = self.lists.get(key, [])
        removed = target.count(value)
        self.lists[key] = [v for v in target if v != value]
        return removed

    def llen(self, key):
        return len(self.lists.get(key, []))

    def lrange(self, key, start, end):
        target = self.lists.get(key, [])
        end = len(target) if end == -1 else end + 1
        return target[start:end]

    # -- pipeline (sequential; good enough for these semantics) --------
    def pipeline(self):
        fake = self

        class _Pipe:
            def __init__(self):
                self._queued = []

            def __getattr__(self, name):
                def _queue(*args, **kwargs):
                    self._queued.append((name, args, kwargs))
                    return self

                return _queue

            def execute(self):
                return [getattr(fake, n)(*a, **k) for n, a, k in self._queued]

        return _Pipe()


class TestCluster08d_InflightListMirror(SimpleTestCase):
    """Cluster 8d: LIST length == index membership, at every transition."""

    def setUp(self):
        self.fake = FakeRedis()
        patcher = mock.patch.object(registry, "_get_client", return_value=self.fake)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _llen(self):
        return self.fake.llen(redis_keys.inflight_list_key())

    def _index(self):
        return self.fake.smembers(redis_keys.index_key())

    def test_8_157_register_mirrors_once_even_across_requeues(self):
        """
        Scenario 8.157: registration adds the id to the LIST exactly once.
        Given: an empty registry
        When: register() runs for a task, then runs AGAIN for the same id
              (a requeue re-register keeps the id in the SET)
        Then: the id is in the index and LLEN == 1 after both — the mirror
              never double-counts a still-tracked task
        """
        registry.register("tid-1", "calc_and_save", (), {}, "celery")
        self.assertIn("tid-1", self._index())
        self.assertEqual(self._llen(), 1, "First registration mirrors onto the list.")

        registry.register("tid-1", "calc_and_save", (), {}, "celery")
        self.assertEqual(
            self._llen(), 1,
            "A requeue-style re-register of a tracked id must not grow the "
            "list — KEDA would over-scale and the drain would under-count.",
        )

    def test_8_158_deregister_drains_every_occurrence(self):
        """
        Scenario 8.158: deregister removes the id from SET and LIST entirely.
        Given: a tracked task, plus a duplicated list entry (leaked by a
               hypothetical crash between writes)
        When: deregister() runs
        Then: index empty and LLEN == 0 — LREM count 0 removes all occurrences,
              so the scale signal returns to zero with the index
        """
        registry.register("tid-1", "calc_and_save", (), {}, "celery")
        # Simulate a leaked duplicate.
        self.fake.lpush(redis_keys.inflight_list_key(), "tid-1")
        self.assertEqual(self._llen(), 2)

        registry.deregister("tid-1")
        self.assertEqual(self._index(), set(), "The index entry is gone.")
        self.assertEqual(
            self._llen(), 0,
            "Deregister must drain every list occurrence, or the recovery pod "
            "would stay scaled up forever on a leaked duplicate.",
        )

    def test_8_159_reconcile_rebuilds_list_from_index(self):
        """
        Scenario 8.159: startup reconcile makes the LIST reflect the SET.
        Given: an index with three tracked ids and a stale/patchy list
        When: reconcile_inflight_list() runs
        Then: the list holds exactly the index members (count returned), so a
              pod booting mid-cutover exposes the true in-flight work to KEDA
        """
        for tid in ("a", "b", "c"):
            self.fake.sadd(redis_keys.index_key(), tid)
        self.fake.lpush(redis_keys.inflight_list_key(), "stale-junk")

        rebuilt = registry.reconcile_inflight_list()

        self.assertEqual(rebuilt, 3)
        self.assertEqual(self._llen(), 3)
        self.assertEqual(
            set(self.fake.lrange(redis_keys.inflight_list_key(), 0, -1)),
            {"a", "b", "c"},
            "The rebuilt list must contain exactly the index members.",
        )

    def test_8_160_recovery_give_up_drains_the_scale_signal(self):
        """
        Scenario 8.160: the sweep's terminal path drives the mirror to zero.
        Given: a tracked task whose heartbeat expired and whose retry budget
               is exhausted
        When: scan_and_recover() runs
        Then: the task is given up (ABORTED path), deregistered, and the
              in-flight list is empty — the recovery pod may scale back to 0
        """
        registry.register("tid-1", "calc_and_save", (), {}, "celery")
        # Exhaust the budget and kill the heartbeat.
        payload = registry.get_payload("tid-1")
        payload["retries"] = supervisor._max_retries()
        registry.persist_payload("tid-1", payload)
        self.fake.kv.pop(redis_keys.heartbeat_key("tid-1"), None)

        app = mock.MagicMock()
        app.AsyncResult.return_value.ready.return_value = False
        with mock.patch.object(supervisor, "_is_cancelled", return_value=False), \
             mock.patch.object(supervisor, "_rows_already_settled", return_value=False), \
             mock.patch.object(supervisor, "_abort_calculation_rows"):
            stats = supervisor.scan_and_recover(app)

        self.assertEqual(stats["gave_up"], 1, "The exhausted task must be finalized.")
        self.assertEqual(self._index(), set())
        self.assertEqual(
            self._llen(), 0,
            "Give-up must drain the scale signal so the pod can scale to 0.",
        )
