"""
Cluster 8s: real-broker integration smoke for the Celery worker-recovery
feature (``lex.lex_app.celery_recovery``).

Phase 4 of the recovery plan called out one explicit remaining smoke run
against a live Redis + Celery worker, because the unit suite in cluster
8r exercises the supervisor against an in-memory ``FakeRedis`` and a
mocked Celery app, which cannot prove two things the plan's design rests
on:

1. ``app.send_task(name, args, kwargs, task_id=<same_id>, headers=...)``
   really does flow through a Redis broker into a real worker, and the
   downstream ``AsyncResult.get()`` returns the result keyed by that
   same task_id (i.e. the parent's ``WaitForTasks`` join keeps working).
2. ``app.backend.mark_as_failure(task_id, MaxRequeueExceeded(...))`` on
   a real Redis result backend pickles the exception such that
   ``AsyncResult.get(propagate=True)`` re-raises the exact subclass — so
   ``except WorkerLost`` in calling code catches it.

We deliberately do **not** try to kill an in-process worker with SIGKILL
to simulate worker death — ``celery.contrib.testing.worker.start_worker``
runs inside the test process, so killing it would kill the test runner.
Instead we forge the stale-heartbeat envelope directly in Redis (which is
exactly the state the supervisor sees after a real worker death) and
then drive the production code path: ``sweep_once()`` → ``send_task`` /
``mark_as_failure``. The supervisor cannot tell the difference.

Gating
------

This file is opt-in via ``LEX_RUN_REDIS_CELERY_TESTS=true``, exactly the
same gate as cluster 8k. With the gate off, every scenario raises
``SkipTest`` so the default suite stays green on machines / CI runners
that have no Redis service. To run locally:

    LEX_RUN_REDIS_CELERY_TESTS=true \\
    LEX_CELERY_REDIS_TEST_URL=redis://127.0.0.1:6379/15 \\
    python -m lex test \\
        lex.test_project.tests.celery_async.test_8s_worker_recovery_smoke \\
        --verbosity=2 --noinput

Scenario numbering picks up after cluster 8r at 8.73.
"""

from __future__ import annotations

import base64
import os
import pickle
import time
import unittest
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

from celery.contrib.testing.worker import start_worker
from celery.result import AsyncResult
from django.test import SimpleTestCase

from lex.lex_app.celery import app as celery_app
from lex.lex_app.celery_recovery import redis_keys
from lex.lex_app.celery_recovery.exceptions import MaxRequeueExceeded
from lex.lex_app.celery_recovery.redis_client import (
    get_client,
    set_client_factory,
)
from lex.lex_app.celery_recovery.supervisor import sweep_once

from .test_8k_redis_broker_integration import (
    _make_redis_result_backend,
    _redis_celery_overrides,
    _redis_tests_requested,
    _require_redis_broker,
    _temporary_celery_config,
    _temporary_env,
)


# A trivially-deterministic task body: returns its single arg unchanged.
# Registered on the project Celery app so a real worker can route to it.
@celery_app.task(name="lex.test_project.celery_async.recovery_smoke_echo")
def _recovery_smoke_echo(value: str) -> str:
    """Echo task used as the requeued payload in scenario 8.73."""
    return value


@contextmanager
def _bound_recovery_redis(redis_url: str):
    """Point the recovery package's Redis client at the test broker.

    The recovery package uses its own ``get_client()`` factory so it
    can be swapped for ``FakeRedis`` in unit tests. For this smoke we
    swap it for a real ``redis.Redis`` instance bound to the same URL
    Celery is using as broker + backend, then restore the previous
    factory on exit so we don't leak state into other test files.
    """
    import redis

    real_client = redis.Redis.from_url(redis_url)
    set_client_factory(lambda: real_client)
    try:
        yield real_client
    finally:
        # Best-effort cleanup of any keys this test created — we use a
        # uuid-suffixed task_id per scenario so a wildcard scan is safe.
        try:
            for key in real_client.scan_iter(match="lex:task:*"):
                real_client.delete(key)
            for key in real_client.scan_iter(match="lex:wrk:*"):
                real_client.delete(key)
        except Exception:
            pass
        set_client_factory(None)


def _write_stale_envelope(
    client,
    *,
    task_id: str,
    task_name: str,
    queue: str,
    args: tuple,
    kwargs: dict,
    attempt: int,
    max_retries: int,
    hostname: str = "smoke-dead-worker",
    last_hb_age_seconds: int = 600,
) -> None:
    """Forge the exact envelope shape that ``task_prerun`` writes.

    The supervisor's ``sweep_once`` reads ``lex:task:<id>`` plus
    ``lex:wrk:<host>``. Writing the envelope with an old
    ``last_hb_iso`` and *no* worker key is observationally identical
    to a worker that booted, ran ``task_prerun``, and then died before
    its heartbeat thread could renew the worker key — i.e. the exact
    failure mode the feature is designed to recover from.
    """
    long_ago = (
        datetime.now(timezone.utc) - timedelta(seconds=last_hb_age_seconds)
    ).isoformat()
    envelope = {
        b"task_name": task_name.encode("utf-8"),
        b"queue": queue.encode("utf-8"),
        b"args_b64": base64.b64encode(pickle.dumps(args)),
        b"kwargs_b64": base64.b64encode(pickle.dumps(kwargs)),
        b"attempt": str(attempt).encode("utf-8"),
        b"max_retries": str(max_retries).encode("utf-8"),
        b"hostname": hostname.encode("utf-8"),
        b"last_hb_iso": long_ago.encode("utf-8"),
    }
    # ``hset`` with a mapping is the single-round-trip equivalent of the
    # per-field writes ``task_prerun`` does, and matches the bytes-keys
    # convention the supervisor reads with.
    client.hset(redis_keys.task_key(task_id), mapping=envelope)
    # Explicitly do NOT write lex:wrk:<host> — that's the staleness
    # signal the supervisor uses to decide "worker is truly gone".


def _wait_for_result(result: AsyncResult, timeout: float = 30.0) -> object:
    """Block on the result with a clear timeout error message.

    ``result.get()`` raises ``celery.exceptions.TimeoutError`` on its
    own, but the default message is opaque — adding the task_id makes
    diagnosis instantaneous when the broker or worker misbehaves.
    """
    try:
        return result.get(timeout=timeout, propagate=True)
    except Exception as exc:
        # Re-raise with task_id context preserved.
        exc.args = (
            f"task_id={result.id}: {exc}",
            *exc.args[1:],
        )
        raise


class TestCluster08s_WorkerRecoverySmoke(SimpleTestCase):
    """Live-broker proof that supervisor requeue + failure-injection work.

    Two scenarios, both gated:

    * 8.73 — requeue happy path. Forge a stale envelope; ``sweep_once``
      should re-publish to the real broker; a real worker consumes it;
      ``AsyncResult.get()`` returns the expected value.
    * 8.74 — failure-injection terminal path. Same setup but with
      ``attempt == max_retries`` so the supervisor calls
      ``mark_as_failure``; ``AsyncResult.get()`` must raise
      ``MaxRequeueExceeded`` (a ``WorkerLost`` subclass).

    Both scenarios share the same temporary Celery config — distinct
    per-scenario queue names + uuid-suffixed ``task_id`` keep them
    independent and make the test rerunnable without manual Redis
    flushing.
    """

    # -- 8.73 ----------------------------------------------------------
    def test_8_73_supervisor_requeue_completes_on_live_worker(self) -> None:
        """Forge stale envelope → sweep → worker runs requeued task → ok."""
        redis_url = _require_redis_broker()
        queue_name = f"lex-test-recovery-{uuid.uuid4().hex}"
        task_id = uuid.uuid4().hex
        payload = f"requeue-ok-{uuid.uuid4().hex}"

        with _temporary_celery_config(
            celery_app,
            **_redis_celery_overrides(redis_url, queue_name),
            task_serializer="pickle",
            result_serializer="pickle",
            accept_content=["json", "pickle"],
            backend=_make_redis_result_backend(
                celery_app,
                redis_url,
                serializer="pickle",
                accept=("json", "pickle"),
            ),
        ), _bound_recovery_redis(redis_url) as rc, _temporary_env(
            LEX_TASK_HEARTBEAT_INTERVAL="1",
            LEX_TASK_HB_TTL_MULTIPLIER="2",
            LEX_TASK_MAX_RETRIES="3",
            LEX_TASK_RECOVERY_ENABLED="true",
        ):
            # 1) Plant the stale envelope as if a worker had died mid-task.
            _write_stale_envelope(
                rc,
                task_id=task_id,
                task_name="lex.test_project.celery_async.recovery_smoke_echo",
                queue=queue_name,
                args=(payload,),
                kwargs={},
                attempt=0,
                max_retries=3,
            )

            # 2) Run the supervisor sweep. The dead-worker envelope should
            #    be re-published to ``queue_name`` with the same task_id.
            summary = sweep_once()
            self.assertEqual(
                summary["requeued"], 1,
                f"supervisor should have requeued exactly one task, got {summary!r}",
            )
            self.assertEqual(
                summary["failed"], 0,
                f"supervisor should not have injected failure yet, got {summary!r}",
            )

            # 3) Boot a real worker on that queue and join on the result.
            with start_worker(
                celery_app,
                pool="solo",
                concurrency=1,
                queues=[queue_name],
                perform_ping_check=False,
                loglevel="INFO",
            ):
                result = AsyncResult(task_id, app=celery_app)
                value = _wait_for_result(result, timeout=30.0)

            # 4) Same task_id, original payload returned.
            self.assertEqual(value, payload)
            self.assertEqual(result.id, task_id)

    # -- 8.74 ----------------------------------------------------------
    def test_8_74_supervisor_injects_max_retries_exceeded(self) -> None:
        """Stale envelope at the cap → sweep injects failure → get() raises."""
        redis_url = _require_redis_broker()
        queue_name = f"lex-test-recovery-{uuid.uuid4().hex}"
        task_id = uuid.uuid4().hex

        with _temporary_celery_config(
            celery_app,
            **_redis_celery_overrides(redis_url, queue_name),
            task_serializer="pickle",
            result_serializer="pickle",
            accept_content=["json", "pickle"],
            backend=_make_redis_result_backend(
                celery_app,
                redis_url,
                serializer="pickle",
                accept=("json", "pickle"),
            ),
        ), _bound_recovery_redis(redis_url) as rc, _temporary_env(
            LEX_TASK_HEARTBEAT_INTERVAL="1",
            LEX_TASK_HB_TTL_MULTIPLIER="2",
            LEX_TASK_MAX_RETRIES="2",
            LEX_TASK_RECOVERY_ENABLED="true",
        ):
            # attempt == max_retries → supervisor's ``attempt + 1 > max``
            # branch fires → mark_as_failure with MaxRequeueExceeded.
            _write_stale_envelope(
                rc,
                task_id=task_id,
                task_name="lex.test_project.celery_async.recovery_smoke_echo",
                queue=queue_name,
                args=("never-runs",),
                kwargs={},
                attempt=2,
                max_retries=2,
            )

            summary = sweep_once()
            self.assertEqual(
                summary["failed"], 1,
                f"supervisor should have injected exactly one failure, got {summary!r}",
            )
            self.assertEqual(
                summary["requeued"], 0,
                f"supervisor must not requeue past the cap, got {summary!r}",
            )

            # No worker is started — the result must already be terminal
            # in the Redis backend because mark_as_failure wrote it there.
            result = AsyncResult(task_id, app=celery_app)

            # Brief poll loop: mark_as_failure writes to Redis async via
            # the backend; up to ~1s to settle on a slow machine.
            deadline = time.monotonic() + 5.0
            while time.monotonic() < deadline:
                if result.ready():
                    break
                time.sleep(0.05)
            self.assertTrue(
                result.ready(),
                f"backend never reported terminal state for task_id={task_id}; "
                f"summary={summary!r}",
            )
            self.assertEqual(result.state, "FAILURE")

            with self.assertRaises(MaxRequeueExceeded) as raised:
                result.get(timeout=2.0, propagate=True)

            exc = raised.exception
            # Exception carries the diagnostic fields the parent's
            # error_message will surface.
            self.assertEqual(getattr(exc, "task_id", None), task_id)
            self.assertEqual(getattr(exc, "attempt", None), 2)


if __name__ == "__main__":  # pragma: no cover — manual invocation aid
    if not _redis_tests_requested():
        print(
            "Skipping: set LEX_RUN_REDIS_CELERY_TESTS=true to run the "
            "worker-recovery smoke test against a live Redis."
        )
    unittest.main()

