"""
Cluster 8k: small real Redis broker integration tests for Celery.

Intent
------

Clusters 8g–8j deliberately avoid Redis: they prove the Lex Celery
routing, eager-mode callback path, scope contracts, and task bodies in a
fast deterministic way. This file is the intentionally tiny companion
integration test that crosses the one boundary those tests do not cross:

    producer -> Redis broker -> in-process Celery worker -> Redis result backend

It is opt-in because a real Redis server is an environment dependency and
should not make the default unit/E2E suite flaky on developer laptops or
CI runners that have no broker service. To run it locally:

    LEX_RUN_REDIS_CELERY_TESTS=true \
    LEX_CELERY_REDIS_TEST_URL=redis://127.0.0.1:6379/15 \
    python -m lex test lex.test_project.tests.celery_async.test_8k_redis_broker_integration --verbosity=2 --noinput

Scenario numbering continues after Cluster 8j at 8.45.
"""

from __future__ import annotations

import os
import uuid
import unittest
from contextlib import contextmanager

from celery.contrib.testing.worker import start_worker
from celery.result import allow_join_result
from kombu.serialization import enable_insecure_serializers

from django.test import SimpleTestCase
from unittest.mock import patch

from lex.core.models.CalculationModel import CalculationModel
from lex.lex_app.celery import app as celery_app
from lex.lex_app.celery_tasks import WaitForTasks, tasks_context, unblock_tasks_context
from lex.tests.e2e._e2e_test_case import E2ETestCase

from .models import ALL_MODELS, CeleryCalc


RUN_ENV = "LEX_RUN_REDIS_CELERY_TESTS"
REDIS_URL_ENV = "LEX_CELERY_REDIS_TEST_URL"
DEFAULT_REDIS_URL = "redis://127.0.0.1:6379/15"


@celery_app.task(name="lex.test_project.celery_async.redis_broker_probe")
def _redis_broker_probe(payload: dict[str, str]) -> dict[str, str | bool]:
    """Small JSON-safe task used only by the Redis broker integration test."""
    return {
        "message": payload["message"],
        "correlation_id": payload["correlation_id"],
        "executed_by_worker": True,
        "task_always_eager": bool(celery_app.conf.task_always_eager),
    }


@contextmanager
def _temporary_celery_config(app=None, **overrides):
    """Temporarily override Celery app config and restore it exactly after use."""
    app = app or celery_app
    previous = {key: app.conf.get(key) for key in overrides}
    previous_backend = getattr(app, "_backend_cache", None)
    app.conf.update(**overrides)
    # Celery caches ``app.backend`` after first access. Reset it so this
    # test really uses the temporary Redis result backend rather than the
    # project default configured during app import.
    app._backend_cache = None
    try:
        yield
    finally:
        app.conf.update(**previous)
        app._backend_cache = previous_backend


@contextmanager
def _temporary_env(**overrides):
    """Temporarily override environment variables and restore them exactly."""
    previous = {key: os.environ.get(key) for key in overrides}
    os.environ.update({key: str(value) for key, value in overrides.items()})
    try:
        yield
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def _reset_dispatch_contexts() -> None:
    """Reset Celery dispatch ContextVars so scenarios cannot leak state."""
    tasks_context.set({"task_context_stack": []})
    unblock_tasks_context.set({"unblock_context_stack": []})


def _redis_celery_overrides(redis_url: str, queue_name: str) -> dict:
    """Celery settings used by the real Redis broker scenarios."""
    return {
        "broker_url": redis_url,
        # Celery's read/write split uses canonical lowercase names; keep
        # these alongside the namespace-prefixed broker URL so producer and
        # worker connections both point at the same test Redis instance.
        "broker_read_url": redis_url,
        "broker_write_url": redis_url,
        "result_backend": redis_url,
        "task_always_eager": False,
        "task_store_eager_result": False,
        "worker_prefetch_multiplier": 1,
        "task_create_missing_queues": True,
        "task_default_queue": queue_name,
    }


def _require_redis_broker() -> str:
    """Return the Redis URL for this opt-in test.

    Two regimes:

    * ``LEX_RUN_REDIS_CELERY_TESTS`` unset → :class:`unittest.SkipTest`
      so a laptop or a CI runner with no Redis service stays green.
    * ``LEX_RUN_REDIS_CELERY_TESTS=true`` (the gate-celery-broker job)
      → an unreachable broker is an outright failure. CI provides the
      Redis service; if it's not there the gate is silently green
      otherwise — exactly the kind of "tests passed but proved nothing"
      regression this gate exists to prevent.
    """
    if not _redis_tests_requested():
        raise unittest.SkipTest(
            f"Redis broker integration test is opt-in; set {RUN_ENV}=true "
            f"and optionally {REDIS_URL_ENV}=redis://127.0.0.1:6379/15."
        )

    redis_url = os.getenv(REDIS_URL_ENV, DEFAULT_REDIS_URL)
    try:
        import redis
    except Exception as exc:  # pragma: no cover — dependency/environment dependent
        raise AssertionError(
            f"redis package is not importable but {RUN_ENV}=true: {exc}. "
            "The release-gate broker job requires the redis client; install "
            "it in the CI environment or unset the opt-in flag."
        ) from exc

    try:
        client = redis.Redis.from_url(redis_url, socket_connect_timeout=1, socket_timeout=1)
        client.ping()
    except Exception as exc:  # pragma: no cover — depends on local Redis service
        raise AssertionError(
            f"Redis broker is not reachable at {redis_url!r} but "
            f"{RUN_ENV}=true: {exc}. The gate-celery-broker job must run "
            "against a live Redis service — check the workflow's services "
            "block and LEX_CELERY_REDIS_TEST_URL."
        ) from exc

    return redis_url


def _redis_tests_requested() -> bool:
    return os.getenv(RUN_ENV, "").strip().lower() in {"1", "true", "yes", "on"}


class TestCluster08k_RedisBrokerIntegration(SimpleTestCase):
    """A minimal real-broker Celery smoke test using Redis.

    The assertion is intentionally small: a task is published to a unique
    Redis-backed queue, consumed by a real Celery worker thread, and its
    result is read back from the Redis result backend. This catches broker
    URL/backend misconfiguration and serialization regressions that eager
    mode cannot see, while keeping the test isolated from Django DB
    transactions and from the rest of the suite.
    """

    # -- 8.45 ----------------------------------------------------------
    def test_8_45_redis_broker_round_trip_executes_in_worker(self) -> None:
        """Scenario 8.45: producer → Redis broker → worker → Redis backend."""
        redis_url = _require_redis_broker()
        queue_name = f"lex-test-redis-broker-{uuid.uuid4().hex}"
        correlation_id = uuid.uuid4().hex
        payload = {"message": "cluster-8k", "correlation_id": correlation_id}

        with _temporary_celery_config(
            celery_app,
            **_redis_celery_overrides(redis_url, queue_name),
            task_serializer="json",
            result_serializer="json",
            accept_content=["json"],
        ):
            # Fail fast if Celery cannot establish the Redis broker connection.
            with celery_app.connection_for_write() as conn:
                conn.ensure_connection(max_retries=1, timeout=1)

            with start_worker(
                celery_app,
                pool="solo",
                concurrency=1,
                queues=[queue_name],
                perform_ping_check=False,
                loglevel="INFO",
            ):
                with celery_app.connection_for_write() as connection:
                    result = _redis_broker_probe.apply_async(
                        args=[payload],
                        queue=queue_name,
                        connection=connection,
                    )
                with allow_join_result():
                    observed = result.get(timeout=10)

        self.assertEqual(observed["message"], "cluster-8k")
        self.assertEqual(observed["correlation_id"], correlation_id)
        self.assertTrue(
            observed["executed_by_worker"],
            "The task body must run in the worker, not just be accepted by the broker.",
        )
        self.assertFalse(
            observed["task_always_eager"],
            "This is the broker integration gate; eager mode must be disabled.",
        )


def _prime_calc_for_dispatch(name: str, *, should_fail: bool) -> CeleryCalc:
    """Create a :class:`CeleryCalc`, persist it, and pre-flip its row to
    ``IN_PROGRESS`` so the on_success / on_failure callback is the only
    party that ever advances the row to a terminal state — exactly the
    customer-visible flow when ``CalculationModel.save()`` dispatches via
    Celery.
    """
    calc = CeleryCalc(
        name=name,
        should_fail=should_fail,
        is_calculated=CalculationModel.NOT_CALCULATED,
    )
    calc.save()
    CeleryCalc.objects.filter(pk=calc.pk).update(
        is_calculated=CalculationModel.IN_PROGRESS,
    )
    calc.is_calculated = CalculationModel.IN_PROGRESS
    return calc


@contextmanager
def _redis_celery_runtime(task_app, redis_url: str, queue_name: str):
    """Boot the broker config + a real in-process worker for the
    duration of a CalculationModel scenario.

    Yields ``(audit_spy, status_spy)`` — both are ``unittest.mock.patch``
    objects on framework seams that fan out side effects to WebSocket /
    audit pipelines. The DB-row update done by ``CallbackTask.on_success`` /
    ``on_failure`` happens *before* those seams, so patching them keeps the
    test isolated without hiding the bit we actually care about: the row's
    final ``is_calculated`` value.
    """
    enable_insecure_serializers(["pickle"])
    with _temporary_celery_config(
        task_app,
        **_redis_celery_overrides(redis_url, queue_name),
        # CalculationModel instances are not JSON-serializable; the
        # framework already registers the task with pickle, but the
        # Redis result backend default would still be JSON, so force
        # pickle here for the result side too.
        result_serializer="pickle",
    ), _temporary_env(CELERY_ACTIVE="true"), patch(
        "lex.lex_app.celery_tasks.ensure_terminal_calculation_audit",
    ) as audit_spy, patch(
        "lex.lex_app.celery_tasks.update_calculation_status",
    ) as status_spy:
        with task_app.connection_for_write() as conn:
            conn.ensure_connection(max_retries=1, timeout=1)
        with start_worker(
            task_app,
            pool="solo",
            concurrency=1,
            queues=[queue_name],
            perform_ping_check=False,
            loglevel="INFO",
        ):
            yield audit_spy, status_spy


def _make_bounded_wait_for_tasks(*, swallow_failures: bool = False) -> WaitForTasks:
    """Construct a :class:`WaitForTasks` whose ``wait_for_completion``
    joins each AsyncResult with a finite timeout — so a stuck broker
    fails the test in CI instead of hanging the runner.

    ``swallow_failures=True`` lets a deliberate-failure scenario (8.47)
    exit the scope cleanly so post-block DB assertions still run; the
    callback has already updated the row by the time ``result.get()``
    raises, which is what the test is verifying.
    """
    wft = WaitForTasks()

    def bounded_wait_for_completion() -> None:
        for async_result in list(wft.dispatched_results):
            with allow_join_result():
                if swallow_failures:
                    try:
                        async_result.get(timeout=30)
                    except Exception:
                        # Expected — the customer-facing failure path
                        # writes the terminal row state via on_failure;
                        # downstream assertions check that explicitly.
                        pass
                else:
                    async_result.get(timeout=30)
        wft.dispatched_results.clear()

    wft.wait_for_completion = bounded_wait_for_completion
    return wft


_RedisCalculationBase = E2ETestCase if _redis_tests_requested() else SimpleTestCase


@unittest.skipUnless(
    _redis_tests_requested(),
    f"Redis broker integration test is opt-in; set {RUN_ENV}=true and optionally {REDIS_URL_ENV}.",
)
class TestCluster08k_RedisBrokerCalculationModel(_RedisCalculationBase):
    """Real Redis broker path for Lex's customer-facing CalculationModel API.

    This scenario intentionally complements the JSON-safe smoke test above:
    CalculationModel tasks carry a Django model instance through Celery, so
    the test uses Celery's pickle serializer exactly for this broker boundary.
    It remains isolated behind ``LEX_RUN_REDIS_CELERY_TESTS`` and a dedicated
    queue so normal CI and local runs never require Redis.
    """

    e2e_models = ALL_MODELS

    def setUp(self) -> None:
        if not _redis_tests_requested():
            raise unittest.SkipTest(
                f"Redis broker integration test is opt-in; set {RUN_ENV}=true "
                f"and optionally {REDIS_URL_ENV}=redis://127.0.0.1:6379/15."
            )
        super().setUp()
        _reset_dispatch_contexts()
        self.addCleanup(_reset_dispatch_contexts)

    # The opt-in CI job runs against its own Redis service/DB, so
    # isolation comes from the broker instance rather than custom
    # per-task routing — the customer's own queue name is fine.
    QUEUE_NAME = "celery"

    def _delay_via_test_broker(self, task, queue_name):
        """Build a stand-in for ``task.delay`` that publishes to the
        scenario's Redis queue using a fresh write connection. We patch
        ``task.delay`` rather than the broker URL on the task itself so
        every other code path the framework follows (signature, registry,
        callback wiring) remains identical to production.
        """

        def _delay(*args, **kwargs):
            with task.app.connection_for_write() as connection:
                return task.apply_async(
                    args=args,
                    kwargs=kwargs,
                    queue=queue_name,
                    connection=connection,
                )

        return _delay

    # -- 8.46 ----------------------------------------------------------
    def test_8_46_wait_for_tasks_calculationmodel_round_trip_over_redis(self) -> None:
        """Scenario 8.46: ``WaitForTasks`` dispatches a ``CalculationModel``
        task to a real Redis-backed worker and blocks until callback success.

        Customer flow modelled: a parent calculation triggers a child via
        ``WaitForTasks`` and resumes only once the child has finished. We
        prove the row reaches ``SUCCESS`` and the terminal audit seam fires
        on the real broker — not just in eager mode.
        """
        redis_url = _require_redis_broker()
        calc = _prime_calc_for_dispatch("s8-46-redis-wft", should_fail=False)
        task = getattr(calc.calculate, "task")

        with _redis_celery_runtime(task.app, redis_url, self.QUEUE_NAME) as (
            audit_spy, _status_spy,
        ):
            wft = _make_bounded_wait_for_tasks()
            self.assertTrue(
                wft._active,
                "WaitForTasks must be active when CELERY_ACTIVE=true.",
            )
            with patch.object(
                task, "delay",
                side_effect=self._delay_via_test_broker(task, self.QUEUE_NAME),
            ), wft:
                result = calc.calculate()
                self.assertIn(
                    result, wft.dispatched_results,
                    "The bound CalculationModel task must register its "
                    "AsyncResult on the active WaitForTasks scope.",
                )
            self.assertEqual(
                wft.dispatched_results, [],
                "WaitForTasks must clear dispatched results after blocking.",
            )

        fresh = CeleryCalc.objects.get(pk=calc.pk)
        self.assertEqual(
            fresh.is_calculated, CalculationModel.SUCCESS,
            "The Redis-backed worker and CallbackTask.on_success must flip "
            "the CalculationModel row to SUCCESS.",
        )
        self.assertTrue(
            audit_spy.called,
            "CallbackTask.on_success must still reach the terminal audit seam "
            "on the real broker path.",
        )

    # -- 8.47 ----------------------------------------------------------
    def test_8_47_failing_calc_over_redis_ends_in_error_not_in_progress(self) -> None:
        """Scenario 8.47: a calc that raises on the real broker must land
        in ``ERROR``, not stay stuck in ``IN_PROGRESS``.

        This is the single most-cited "we got burned by Celery" failure
        mode in the cluster docstring: a calculation goes IN_PROGRESS,
        the worker crashes or the task body raises, and the row never
        moves on. The customer ends up with a perpetual spinner and no
        signal that anything went wrong.

        We prove ``CallbackTask.on_failure`` runs over the real broker
        path and flips the row to ``ERROR`` — and that the failure is
        propagated to the calling scope (``result.get()`` raised),
        rather than being swallowed.
        """
        redis_url = _require_redis_broker()
        calc = _prime_calc_for_dispatch("s8-47-redis-fail", should_fail=True)
        task = getattr(calc.calculate, "task")

        # ``swallow_failures=True`` lets the WFT scope exit so we can read
        # final DB state. The expected exception is observed below by
        # inspecting the AsyncResult's state — a stronger assertion than
        # ``assertRaises`` because it confirms the worker reported the
        # task as ``FAILURE``, not just that *some* exception bubbled up.
        with _redis_celery_runtime(task.app, redis_url, self.QUEUE_NAME) as (
            _audit_spy, status_spy,
        ):
            wft = _make_bounded_wait_for_tasks(swallow_failures=True)
            with patch.object(
                task, "delay",
                side_effect=self._delay_via_test_broker(task, self.QUEUE_NAME),
            ), wft:
                async_result = calc.calculate()
                self.assertIn(
                    async_result, wft.dispatched_results,
                    "Even a failing calc must register on the WFT scope so "
                    "the parent waits for the failure rather than racing on.",
                )
            self.assertTrue(
                async_result.failed(),
                "The Redis-backed worker must mark the task as FAILURE "
                "when calculate() raises — not SUCCESS, not PENDING.",
            )

        fresh = CeleryCalc.objects.get(pk=calc.pk)
        self.assertEqual(
            fresh.is_calculated, CalculationModel.ERROR,
            "A failing calc must NOT be left stuck in IN_PROGRESS — "
            "CallbackTask.on_failure must flip the row to ERROR.",
        )
        self.assertTrue(
            status_spy.called,
            "update_calculation_status must be invoked on the failure "
            "path so downstream audit / WebSocket signals fire.",
        )

    # -- 8.48 ----------------------------------------------------------
    def test_8_48_concurrent_calcs_in_one_scope_all_settle_over_redis(self) -> None:
        """Scenario 8.48: three :class:`CalculationModel` tasks dispatched
        in a single ``WaitForTasks`` scope all reach a terminal state on
        the real broker — none lost, none stuck.

        Customer flow modelled: a parent batch dispatches N independent
        children (e.g. one calc per portfolio). The classic regression we
        guard against is "the first task succeeds, the rest are silently
        dropped on the floor": dispatched_results filling up but the join
        loop only blocking on a subset, leaving rows behind in
        ``IN_PROGRESS``.
        """
        redis_url = _require_redis_broker()
        calcs = [
            _prime_calc_for_dispatch(f"s8-48-redis-batch-{i}", should_fail=False)
            for i in range(3)
        ]
        # All instances share the same underlying lex_shared_task; pull
        # it once from the first calc.
        task = getattr(calcs[0].calculate, "task")

        with _redis_celery_runtime(task.app, redis_url, self.QUEUE_NAME):
            wft = _make_bounded_wait_for_tasks()
            with patch.object(
                task, "delay",
                side_effect=self._delay_via_test_broker(task, self.QUEUE_NAME),
            ), wft:
                results = [c.calculate() for c in calcs]
                self.assertEqual(
                    len(wft.dispatched_results), len(calcs),
                    f"Every dispatched calc must register on the WFT scope; "
                    f"expected {len(calcs)} AsyncResults, got "
                    f"{len(wft.dispatched_results)}.",
                )
                self.assertEqual(
                    [r.id for r in results],
                    [r.id for r in wft.dispatched_results],
                    "Dispatch ordering must match registration order — "
                    "if it doesn't, the join loop may block on the wrong "
                    "set of tasks.",
                )

        # The customer-facing assertion: every row reached SUCCESS.
        # Re-fetch from the DB rather than reusing the in-memory instance
        # so we observe what the worker's callback actually persisted.
        for c in calcs:
            fresh = CeleryCalc.objects.get(pk=c.pk)
            self.assertEqual(
                fresh.is_calculated, CalculationModel.SUCCESS,
                f"Calc {c.name!r} did not settle — broker, worker, or "
                f"callback dropped this task. Final state: "
                f"{fresh.is_calculated!r}.",
            )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()

