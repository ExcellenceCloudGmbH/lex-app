"""
Cluster 8r: Worker-recovery system (Phases 1 and 2).

Scope of *Phase 1*:

    - The ``lex.lex_app.celery_recovery`` package imports cleanly without a
      broker or Django DB.
    - The Redis key namespace helpers return stable, prefixed values.
    - ``WorkerLost`` and ``MaxRequeueExceeded`` carry the recovery context the
      supervisor will inject when the retry budget is exhausted.
    - ``enable()`` respects the ``LEX_TASK_RECOVERY_ENABLED`` master switch and
      is idempotent.

Scope of *Phase 2* (added below):

    - ``task_prerun`` writes the full envelope to ``lex:task:<id>`` and the
      attempt counter is read from the ``lex_attempt`` header on requeue.
    - ``task_postrun`` deletes the hash.
    - ``HeartbeatThread`` writes ``lex:wrk:<host>`` and refreshes
      ``last_hb_iso`` on every in-flight task.

Later phases will extend this file with supervisor + failure-injection tests
(cluster IDs 8.55+ in docs/celery-worker-recovery/plan.md).
"""

from __future__ import annotations

import os
import time
import unittest
from typing import Any, Dict, Iterable
from unittest.mock import MagicMock, patch

from lex.lex_app.celery_recovery import enable, is_enabled
from lex.lex_app.celery_recovery import heartbeat as hb_mod
from lex.lex_app.celery_recovery import redis_client, redis_keys
from lex.lex_app.celery_recovery.exceptions import MaxRequeueExceeded, WorkerLost


# ---------------------------------------------------------------------------
# Minimal in-memory Redis fake. Implements only the verbs the recovery system
# uses. Keeps the test suite independent of the real ``redis-py`` connection.
# ---------------------------------------------------------------------------

class FakeRedis:
    """In-memory shim covering the subset of redis-py used by recovery code.

    Supported: get/set with ``ex``, delete, hset (mapping), hget, hgetall,
    expire, scan_iter, exists, ttl. Everything stores raw bytes to match
    redis-py's ``decode_responses=False`` mode.
    """

    def __init__(self) -> None:
        self._kv: Dict[bytes, bytes] = {}
        self._hashes: Dict[bytes, Dict[bytes, bytes]] = {}
        self._ttls: Dict[bytes, float] = {}

    @staticmethod
    def _b(value: Any) -> bytes:
        if isinstance(value, bytes):
            return value
        if isinstance(value, (int, float)):
            return str(value).encode("utf-8")
        return str(value).encode("utf-8")

    def _expired(self, key: bytes) -> bool:
        deadline = self._ttls.get(key)
        if deadline is None:
            return False
        if time.monotonic() < deadline:
            return False
        # Lazily drop expired entries so behavior matches Redis.
        self._kv.pop(key, None)
        self._hashes.pop(key, None)
        self._ttls.pop(key, None)
        return True

    def set(self, key, value, ex=None, nx=False) -> bool:
        key = self._b(key)
        if nx and (key in self._kv and not self._expired(key)):
            return False
        self._kv[key] = self._b(value)
        if ex is not None:
            self._ttls[key] = time.monotonic() + float(ex)
        return True

    def get(self, key):
        key = self._b(key)
        if self._expired(key):
            return None
        return self._kv.get(key)

    def delete(self, *keys) -> int:
        n = 0
        for k in keys:
            k = self._b(k)
            n += 1 if self._kv.pop(k, None) is not None else 0
            n += 1 if self._hashes.pop(k, None) is not None else 0
            self._ttls.pop(k, None)
        return n

    def hset(self, key, field=None, value=None, mapping=None) -> int:
        key = self._b(key)
        bucket = self._hashes.setdefault(key, {})
        added = 0
        if mapping:
            for f, v in mapping.items():
                f_b = self._b(f)
                if f_b not in bucket:
                    added += 1
                bucket[f_b] = self._b(v)
        if field is not None:
            f_b = self._b(field)
            if f_b not in bucket:
                added += 1
            bucket[f_b] = self._b(value)
        return added

    def hget(self, key, field):
        key = self._b(key)
        if self._expired(key):
            return None
        return self._hashes.get(key, {}).get(self._b(field))

    def hgetall(self, key):
        key = self._b(key)
        if self._expired(key):
            return {}
        return dict(self._hashes.get(key, {}))

    def expire(self, key, seconds) -> bool:
        key = self._b(key)
        if key not in self._kv and key not in self._hashes:
            return False
        self._ttls[key] = time.monotonic() + float(seconds)
        return True

    def ttl(self, key) -> int:
        key = self._b(key)
        deadline = self._ttls.get(key)
        if deadline is None:
            return -1
        remaining = int(deadline - time.monotonic())
        return remaining if remaining > 0 else -2

    def exists(self, key) -> int:
        key = self._b(key)
        if self._expired(key):
            return 0
        return 1 if (key in self._kv or key in self._hashes) else 0

    def scan_iter(self, match: str = "*") -> Iterable[bytes]:
        # Trivial glob: only '*' is meaningful in our keys.
        prefix = match.rstrip("*")
        for k in list(self._kv.keys()) + list(self._hashes.keys()):
            if not self._expired(k) and k.decode("utf-8").startswith(prefix):
                yield k


class TestCluster08r_RecoveryScaffolding(unittest.TestCase):
    """Phase 1: imports, key formatters, exceptions, master switch."""

    def setUp(self) -> None:
        # enable() flips a module-level flag; reset it between tests so each
        # test exercises the activation path independently.
        import lex.lex_app.celery_recovery as pkg
        pkg._enabled = False

    # -- key namespace ----------------------------------------------------
    def test_worker_key_namespaced(self) -> None:
        self.assertEqual(redis_keys.worker_key("worker1@host"), "lex:wrk:worker1@host")

    def test_task_key_namespaced(self) -> None:
        self.assertEqual(redis_keys.task_key("abc-123"), "lex:task:abc-123")

    def test_task_lock_key_namespaced(self) -> None:
        self.assertEqual(redis_keys.task_lock_key("abc-123"), "lex:task:abc-123:lock")

    def test_scan_patterns_are_globs(self) -> None:
        self.assertEqual(redis_keys.worker_scan_pattern(), "lex:wrk:*")
        self.assertEqual(redis_keys.task_scan_pattern(), "lex:task:*")

    # -- exceptions -------------------------------------------------------
    def test_worker_lost_carries_context(self) -> None:
        exc = WorkerLost(
            "heartbeat stale",
            worker_hostname="worker1@host",
            attempt=2,
            task_id="abc-123",
        )
        self.assertEqual(str(exc), "heartbeat stale")
        self.assertEqual(exc.worker_hostname, "worker1@host")
        self.assertEqual(exc.attempt, 2)
        self.assertEqual(exc.task_id, "abc-123")

    def test_max_requeue_is_worker_lost(self) -> None:
        # The parent calculation should be able to ``except WorkerLost`` and
        # still catch the terminal case.
        self.assertTrue(issubclass(MaxRequeueExceeded, WorkerLost))

    # -- enable() switch --------------------------------------------------
    def test_enable_returns_true_when_flag_on(self) -> None:
        with patch.dict(os.environ, {"LEX_TASK_RECOVERY_ENABLED": "true"}, clear=False):
            self.assertTrue(enable())
            self.assertTrue(is_enabled())

    def test_enable_is_idempotent(self) -> None:
        with patch.dict(os.environ, {"LEX_TASK_RECOVERY_ENABLED": "true"}, clear=False):
            self.assertTrue(enable())
            # Second call should be a no-op and report so.
            self.assertFalse(enable())
            self.assertTrue(is_enabled())

    def test_enable_short_circuits_when_flag_off(self) -> None:
        with patch.dict(os.environ, {"LEX_TASK_RECOVERY_ENABLED": "false"}, clear=False):
            self.assertFalse(enable())
            self.assertFalse(is_enabled())


class TestCluster08r_Heartbeat(unittest.TestCase):
    """Phase 2: task_prerun/postrun bookkeeping + heartbeat thread."""

    def setUp(self) -> None:
        self.fake = FakeRedis()
        redis_client.set_client_factory(lambda: self.fake)
        # Clean the registry between tests so a leaked task_id doesn't bleed.
        hb_mod._registry._ids.clear()
        hb_mod._registry.set_hostname("worker1@testhost")

    def tearDown(self) -> None:
        # Make sure no heartbeat thread leaks across tests.
        hb_mod.stop_heartbeat()
        hb_mod._registry._ids.clear()
        redis_client.reset_for_tests()

    # -- task_prerun -----------------------------------------------------
    def _build_task(self, *, name="lex.tests.calc", attempt=None, delivery_tag="dt-1",
                    max_retries_attr=None):
        """Build a Celery-task-shaped MagicMock with request headers + delivery_info."""
        request = MagicMock()
        request.headers = {"lex_attempt": attempt} if attempt is not None else {}
        request.delivery_info = {"delivery_tag": delivery_tag, "routing_key": "celery"}
        request.reply_to = ""
        # Pin ``hostname`` to a real string so the envelope encodes it as a
        # readable hostname rather than the MagicMock repr. ``on_task_prerun``
        # now reads this directly to survive the prefork-fork boundary where
        # the in-process registry isn't populated yet — see heartbeat.py.
        request.hostname = "worker1@testhost"
        task = MagicMock()
        task.name = name
        task.request = request
        if max_retries_attr is not None:
            task.lex_max_retries = max_retries_attr
        else:
            # Mimic a normal Celery task that does not have this attribute set.
            del task.lex_max_retries
        return task

    def test_8_50_task_prerun_writes_envelope_with_attempt_zero(self) -> None:
        """8.50 — first delivery: attempt=0 (no header)."""
        task = self._build_task()
        hb_mod.on_task_prerun(task_id="t-1", task=task, args=("a",), kwargs={"k": 1})
        bucket = self.fake.hgetall(redis_keys.task_key("t-1").encode())
        self.assertEqual(bucket[b"attempt"], b"0")
        self.assertEqual(bucket[b"task_name"], b"lex.tests.calc")
        self.assertEqual(bucket[b"delivery_tag"], b"dt-1")
        self.assertEqual(bucket[b"hostname"], b"worker1@testhost")
        self.assertIn(b"last_hb_iso", bucket)
        self.assertIn(b"args_b64", bucket)
        self.assertIn(b"kwargs_b64", bucket)
        self.assertIn("t-1", hb_mod.get_registry().snapshot())

    def test_8_51_task_prerun_reads_lex_attempt_header_on_requeue(self) -> None:
        """8.51 — supervisor-injected header carries the attempt counter."""
        task = self._build_task(attempt=2)
        hb_mod.on_task_prerun(task_id="t-2", task=task, args=(), kwargs={})
        bucket = self.fake.hgetall(redis_keys.task_key("t-2").encode())
        self.assertEqual(bucket[b"attempt"], b"2")

    def test_8_52_task_postrun_deletes_hash(self) -> None:
        """8.52 — postrun cleans up. Supervisor will not act on completed tasks."""
        task = self._build_task()
        hb_mod.on_task_prerun(task_id="t-3", task=task, args=(), kwargs={})
        self.assertEqual(self.fake.exists(redis_keys.task_key("t-3").encode()), 1)
        hb_mod.on_task_postrun(task_id="t-3", task=task)
        self.assertEqual(self.fake.exists(redis_keys.task_key("t-3").encode()), 0)
        self.assertNotIn("t-3", hb_mod.get_registry().snapshot())

    def test_8_53_heartbeat_thread_writes_worker_key_with_ttl(self) -> None:
        """8.53 — heartbeat writes ``lex:wrk:<host>`` with the configured TTL."""
        with patch.dict(os.environ, {"LEX_TASK_HEARTBEAT_INTERVAL": "1",
                                     "LEX_TASK_HB_TTL_MULTIPLIER": "3"}):
            hb_mod.start_heartbeat("worker1@testhost")
            # Wait for at least one tick (interval=1s + small slack).
            deadline = time.time() + 3.0
            wk = redis_keys.worker_key("worker1@testhost").encode()
            while time.time() < deadline and self.fake.get(wk) is None:
                time.sleep(0.05)
            self.assertEqual(self.fake.get(wk), b"1")
            self.assertGreaterEqual(self.fake.ttl(wk), 1)

    def test_8_54_heartbeat_refreshes_in_flight_task_last_hb(self) -> None:
        """8.54 — heartbeat refreshes ``last_hb_iso`` on every registered task."""
        task = self._build_task()
        hb_mod.on_task_prerun(task_id="t-4", task=task, args=(), kwargs={})
        first = self.fake.hget(redis_keys.task_key("t-4").encode(), b"last_hb_iso")
        with patch.dict(os.environ, {"LEX_TASK_HEARTBEAT_INTERVAL": "1",
                                     "LEX_TASK_HB_TTL_MULTIPLIER": "3"}):
            hb_mod.start_heartbeat("worker1@testhost")
            deadline = time.time() + 3.0
            while time.time() < deadline:
                latest = self.fake.hget(redis_keys.task_key("t-4").encode(), b"last_hb_iso")
                if latest != first:
                    break
                time.sleep(0.05)
            self.assertNotEqual(latest, first)

    def test_8_55_per_task_max_retries_override_is_honored(self) -> None:
        """8.55 — ``task.lex_max_retries`` overrides ``LEX_TASK_MAX_RETRIES``."""
        with patch.dict(os.environ, {"LEX_TASK_MAX_RETRIES": "4"}):
            task = self._build_task(max_retries_attr=1)
            hb_mod.on_task_prerun(task_id="t-5", task=task, args=(), kwargs={})
            bucket = self.fake.hgetall(redis_keys.task_key("t-5").encode())
            self.assertEqual(bucket[b"max_retries"], b"1")

    def test_8_56_worker_shutting_down_clears_worker_key(self) -> None:
        """8.56 — graceful shutdown removes the worker liveness key."""
        with patch.dict(os.environ, {"LEX_TASK_HEARTBEAT_INTERVAL": "1"}):
            hb_mod.start_heartbeat("worker1@testhost")
            deadline = time.time() + 2.0
            wk = redis_keys.worker_key("worker1@testhost").encode()
            while time.time() < deadline and self.fake.get(wk) is None:
                time.sleep(0.05)
            self.assertEqual(self.fake.get(wk), b"1")
            hb_mod.on_worker_shutting_down()
            self.assertIsNone(self.fake.get(wk))


class TestCluster08r_Decorator(unittest.TestCase):
    """Phase 5: ``@lex_shared_task(lex_max_retries=N)`` decorator surface.

    Verifies the decorator stamps the per-task cap onto the underlying Celery
    task so that ``task_prerun`` (covered by 8.55) finds it via
    ``getattr(task, "lex_max_retries", None)``.
    """

    _counter = [0]

    def _decorate(self, **kw):
        from lex.lex_app.celery_tasks import lex_shared_task

        # Each call produces a uniquely-named celery task so the global
        # shared_task registry does not collide across tests in this class.
        self._counter[0] += 1
        unique_name = f"lex.tests.cluster8r.dec_task_{self._counter[0]}"

        @lex_shared_task(name=unique_name, **kw)
        def my_task():
            return "ok"

        return my_task

    def _resolve(self, descriptor):
        """Unwrap the ``EnhancedTaskMethodDescriptor`` down to the celery task."""
        inner = descriptor.task
        resolver = getattr(inner, "_get_current_object", None)
        return resolver() if callable(resolver) else inner

    # -- 8.70 ----------------------------------------------------------
    def test_8_70_decorator_sets_lex_max_retries_on_underlying_task(self) -> None:
        """lex_max_retries=2 must land on the underlying celery task object."""
        descriptor = self._decorate(lex_max_retries=2)
        task = self._resolve(descriptor)
        self.assertEqual(task.lex_max_retries, 2)

    # -- 8.71 ----------------------------------------------------------
    def test_8_71_decorator_omits_attribute_when_kwarg_not_passed(self) -> None:
        """No kwarg → attribute absent → heartbeat falls back to env default."""
        descriptor = self._decorate()
        task = self._resolve(descriptor)
        self.assertIsNone(getattr(task, "lex_max_retries", None))

    # -- 8.72 ----------------------------------------------------------
    def test_8_72_decorator_rejects_negative_values_without_breaking_task(self) -> None:
        """Bad value logs a warning but the task itself still registers."""
        descriptor = self._decorate(lex_max_retries=-1)
        task = self._resolve(descriptor)
        # Bad value → attribute not set; supervisor falls back to env default.
        self.assertIsNone(getattr(task, "lex_max_retries", None))
        # Task is still usable.
        self.assertTrue(hasattr(task, "name"))


class TestCluster08r_Supervisor(unittest.TestCase):
    """Phase 3: supervisor sweep — stale detection, lock, bounded requeue."""

    def setUp(self) -> None:
        self.fake = FakeRedis()
        redis_client.set_client_factory(lambda: self.fake)
        hb_mod._registry._ids.clear()
        hb_mod._registry.set_hostname("worker1@testhost")

    def tearDown(self) -> None:
        hb_mod.stop_heartbeat()
        hb_mod._registry._ids.clear()
        redis_client.reset_for_tests()

    def _seed_task(self, task_id, *, hostname="worker1@testhost", attempt=0,
                   max_retries=4, args=(), kwargs=None, hb_age_seconds=0.0):
        """Write a task envelope into the fake Redis with a heartbeat ``hb_age_seconds`` old."""
        from datetime import datetime, timedelta, timezone

        kwargs = kwargs or {}
        last_hb = datetime.now(timezone.utc) - timedelta(seconds=hb_age_seconds)
        mapping = {
            b"task_name": b"lex.tests.calc",
            b"queue": b"celery",
            b"attempt": str(attempt).encode(),
            b"max_retries": str(max_retries).encode(),
            b"delivery_tag": b"dt-x",
            b"args_b64": hb_mod._pickle_b64(args),
            b"kwargs_b64": hb_mod._pickle_b64(kwargs),
            b"hostname": hostname.encode(),
            b"last_hb_iso": last_hb.isoformat().encode(),
        }
        self.fake.hset(redis_keys.task_key(task_id).encode(), mapping=mapping)

    def _set_worker_alive(self, hostname, ttl=15):
        self.fake.set(redis_keys.worker_key(hostname).encode(), b"1", ex=ttl)

    # -- 8.60 ----------------------------------------------------------
    def test_8_60_sweep_ignores_fresh_heartbeats(self) -> None:
        """Fresh heartbeat → not stale, no action."""
        from lex.lex_app.celery_recovery.supervisor import sweep_once

        self._seed_task("t-fresh", hb_age_seconds=0)
        self._set_worker_alive("worker1@testhost")
        summary = sweep_once()
        self.assertEqual(summary["stale"], 0)
        self.assertEqual(summary["requeued"], 0)

    # -- 8.61 ----------------------------------------------------------
    def test_8_61_sweep_requeues_when_heartbeat_stale_and_worker_dead(self) -> None:
        """Stale heartbeat + dead worker → app.send_task with same task_id, attempt+1."""
        from lex.lex_app.celery_recovery import supervisor

        self._seed_task("t-stale", attempt=1, max_retries=4, hb_age_seconds=120.0,
                        args=("a",), kwargs={"k": 1})
        # Worker key absent ⇒ worker dead.

        sends = []
        class _App:
            def send_task(self, name, *, args, kwargs, task_id, queue=None, headers=None):
                sends.append({
                    "name": name, "args": args, "kwargs": kwargs,
                    "task_id": task_id, "queue": queue, "headers": headers,
                })
        with patch("celery.current_app", _App()):
            summary = supervisor.sweep_once()

        self.assertEqual(summary["requeued"], 1)
        self.assertEqual(len(sends), 1)
        sent = sends[0]
        self.assertEqual(sent["name"], "lex.tests.calc")
        self.assertEqual(sent["task_id"], "t-stale")
        self.assertEqual(sent["args"], ("a",))
        self.assertEqual(sent["kwargs"], {"k": 1})
        self.assertEqual(sent["headers"], {"lex_attempt": 2})
        # Attempt counter persisted to the hash so a header drop still works.
        self.assertEqual(self.fake.hget(redis_keys.task_key("t-stale").encode(), b"attempt"), b"2")

    # -- 8.62 ----------------------------------------------------------
    def test_8_62_sweep_skips_when_worker_key_still_alive(self) -> None:
        """Stale task heartbeat but the worker key is still present → log + skip."""
        from lex.lex_app.celery_recovery import supervisor

        self._seed_task("t-zombie", hb_age_seconds=120.0)
        self._set_worker_alive("worker1@testhost", ttl=15)

        sends = []
        class _App:
            def send_task(self, *a, **kw):
                sends.append(kw)
        with patch("celery.current_app", _App()):
            summary = supervisor.sweep_once()

        self.assertEqual(summary["stale"], 1)
        self.assertEqual(summary["skipped"], 1)
        self.assertEqual(summary["requeued"], 0)
        self.assertEqual(sends, [])

    # -- 8.63 ----------------------------------------------------------
    def test_8_63_supervisor_lock_prevents_double_action(self) -> None:
        """A second sweep_once() while the lock is held must not re-publish."""
        from lex.lex_app.celery_recovery import supervisor

        self._seed_task("t-locked", hb_age_seconds=120.0)
        # Simulate a peer supervisor already holding the lock.
        self.fake.set(redis_keys.task_lock_key("t-locked").encode(), b"peer", ex=30)

        sends = []
        class _App:
            def send_task(self, *a, **kw):
                sends.append(kw)
        with patch("celery.current_app", _App()):
            summary = supervisor.sweep_once()

        self.assertEqual(summary["skipped"], 1)
        self.assertEqual(summary["requeued"], 0)
        self.assertEqual(sends, [])

    # -- 8.64 ----------------------------------------------------------
    def test_8_64_sweep_at_cap_marks_failure_and_drops_envelope(self) -> None:
        """attempt + 1 > max_retries → mark_as_failure + envelope deleted."""
        from lex.lex_app.celery_recovery import supervisor

        self._seed_task("t-cap", attempt=4, max_retries=4, hb_age_seconds=120.0)

        sends = []
        marks = []
        class _Backend:
            def get_task_meta(self, task_id):
                return {"status": "PENDING"}
            def mark_as_failure(self, task_id, exc):
                marks.append((task_id, exc))
        class _App:
            backend = _Backend()
            def send_task(self, *a, **kw):
                sends.append(kw)
        with patch("celery.current_app", _App()):
            summary = supervisor.sweep_once()

        self.assertEqual(summary["failed"], 1)
        self.assertEqual(summary["requeued"], 0)
        self.assertEqual(summary["deferred_at_cap"], 0)
        self.assertEqual(sends, [])
        self.assertEqual(len(marks), 1)
        injected_id, injected_exc = marks[0]
        self.assertEqual(injected_id, "t-cap")
        self.assertIsInstance(injected_exc, MaxRequeueExceeded)
        self.assertEqual(injected_exc.attempt, 4)
        self.assertEqual(injected_exc.task_id, "t-cap")
        # Envelope cleaned up so the next sweep doesn't see this task again.
        self.assertEqual(
            self.fake.hgetall(redis_keys.task_key("t-cap").encode()), {},
        )

    # -- 8.65 ----------------------------------------------------------
    def test_8_65_lock_keys_are_filtered_from_scan(self) -> None:
        """Lock keys share the ``lex:task:`` prefix; the scan must skip them."""
        from lex.lex_app.celery_recovery import supervisor

        self.fake.set(redis_keys.task_lock_key("only-lock").encode(), b"1", ex=30)
        sends = []
        class _App:
            def send_task(self, *a, **kw):
                sends.append(kw)
        with patch("celery.current_app", _App()):
            summary = supervisor.sweep_once()
        self.assertEqual(summary["scanned"], 0)
        self.assertEqual(sends, [])

    # -- 8.66 ----------------------------------------------------------
    def test_8_66_failure_injection_skipped_when_backend_already_terminal(self) -> None:
        """If the result is already SUCCESS/FAILURE, do not overwrite it."""
        from lex.lex_app.celery_recovery import supervisor

        self._seed_task("t-already-done", attempt=4, max_retries=4, hb_age_seconds=120.0)

        marks = []
        class _Backend:
            def get_task_meta(self, task_id):
                # A worker finished the task right before the supervisor saw it.
                return {"status": "SUCCESS"}
            def mark_as_failure(self, task_id, exc):
                marks.append((task_id, exc))
        class _App:
            backend = _Backend()
            def send_task(self, *a, **kw):
                raise AssertionError("send_task should not run at the cap")
        with patch("celery.current_app", _App()):
            summary = supervisor.sweep_once()

        # We didn't write anything to the backend.
        self.assertEqual(marks, [])
        # And we didn't double-count it as a failure.
        self.assertEqual(summary["failed"], 0)
        self.assertEqual(summary["deferred_at_cap"], 1)
        # Envelope still cleaned up so we don't keep scanning it.
        self.assertEqual(
            self.fake.hgetall(redis_keys.task_key("t-already-done").encode()), {},
        )

    # -- 8.67 ----------------------------------------------------------
    def test_8_67_failure_injection_proceeds_when_meta_lookup_raises(self) -> None:
        """get_task_meta failure must not block mark_as_failure — better dup than stuck."""
        from lex.lex_app.celery_recovery import supervisor

        self._seed_task("t-meta-broken", attempt=4, max_retries=4, hb_age_seconds=120.0)

        marks = []
        class _Backend:
            def get_task_meta(self, task_id):
                raise RuntimeError("backend transient error")
            def mark_as_failure(self, task_id, exc):
                marks.append((task_id, exc))
        class _App:
            backend = _Backend()
            def send_task(self, *a, **kw):
                raise AssertionError("send_task should not run at the cap")
        with patch("celery.current_app", _App()):
            summary = supervisor.sweep_once()

        self.assertEqual(summary["failed"], 1)
        self.assertEqual(len(marks), 1)
        self.assertEqual(marks[0][0], "t-meta-broken")
        self.assertIsInstance(marks[0][1], MaxRequeueExceeded)

    # -- 8.68 ----------------------------------------------------------
    def test_8_68_failure_injection_keeps_envelope_when_mark_as_failure_raises(self) -> None:
        """If mark_as_failure itself fails, leave the envelope so the next sweep retries."""
        from lex.lex_app.celery_recovery import supervisor

        self._seed_task("t-mark-broken", attempt=4, max_retries=4, hb_age_seconds=120.0)

        class _Backend:
            def get_task_meta(self, task_id):
                return {"status": "PENDING"}
            def mark_as_failure(self, task_id, exc):
                raise RuntimeError("backend write failed")
        class _App:
            backend = _Backend()
            def send_task(self, *a, **kw):
                raise AssertionError("send_task should not run at the cap")
        with patch("celery.current_app", _App()):
            summary = supervisor.sweep_once()

        # The failure wasn't recorded, so it shouldn't be counted.
        self.assertEqual(summary["failed"], 0)
        # The envelope stays so the next sweep can retry the failure injection.
        envelope = self.fake.hgetall(redis_keys.task_key("t-mark-broken").encode())
        self.assertTrue(envelope, "envelope must be preserved when mark_as_failure raises")
        self.assertEqual(envelope.get(b"attempt"), b"4")


if __name__ == "__main__":
    unittest.main()
