"""Stale queued calc task survives the startup reset and re-executes (repro).

Intent: a calculation message parked on the Redis broker (dispatched while no
worker was consuming — e.g. workers down or restarting) must NOT re-execute
after the startup sweep has already reset its record. Production repro
(2026-07-14, PlanningRun "SD 2026"): the user triggered an update while
workers were unavailable → ``calc_and_save([PlanningRun])`` queued; the server
restarted and the startup sweep flipped the row IN_PROGRESS→ABORTED — which
also reopens the One.py duplicate-calculation guard — so the user's re-click
dispatched a SECOND task with a fresh ``calculation_id``. When the workers
finally booted they drained BOTH messages: the non-idempotent ``update()``
body ran twice, created its child ``Sponsor`` rows twice, and the second run
died with ``MultipleObjectsReturned: get() returned more than one Sponsor``.

The hole: ``ModelRegistration._handle_calculation_model_reset`` resets the DB
row but neither purges the broker message nor sets the cluster cancel marker
(``cluster_cancel_index.mark_cancelled``) for the abandoned calculation_id —
so the cooperative marker check that ``calc_and_save`` / the
``lex_shared_task`` wrapper perform at task start finds nothing and the stale
task happily runs. Scenario 8.146 asserts the CORRECT behaviour (the stale
message must not execute the calculation body) and is
``xfail(strict=True)`` until the sweep invalidates abandoned calculations —
see BUG-026 in known-bugs.md. Scenario 8.145 is the passing control that
proves this harness (real Redis broker + real in-process Celery worker,
spawned in CI by the showcase gate) actually executes a dispatched
calculation exactly once.

Opt-in like cluster 8k: set ``LEX_RUN_REDIS_CELERY_TESTS=true`` (and
optionally ``LEX_CELERY_REDIS_TEST_URL``). The showcase CI workflow provides
a Redis service and sets both, so these scenarios run real workers on every
release; without the flag they skip cleanly.

Cluster 8ae — scenarios 8.145–8.146. Type: E.
Covers: lex/process_admin/utils/model_registration.py
        (_handle_calculation_model_reset — no broker/marker invalidation),
        lex/lex_app/celery_tasks.py (calc_and_save; task-start cancel-marker
        check), lex/core/cancellation/cluster_cancel_index.py.
Run: LEX_RUN_REDIS_CELERY_TESTS=true \
     python -m lex pytest lex/test_project/tests/celery_async/test_8ae_stale_queue_survives_startup_reset.py -v
"""

from __future__ import annotations

import unittest
import uuid

import pytest
from celery.contrib.testing.worker import start_worker
from celery.result import allow_join_result
from django.conf import settings as django_settings
from kombu.serialization import enable_insecure_serializers

from lex.core.cancellation import cluster_cancel_index
from lex.core.models.CalculationModel import CalculationModel
from lex.lex_app.celery import app as celery_app
from lex.lex_app.celery_tasks import calc_and_save
from lex.process_admin.utils.model_registration import ModelRegistration
from lex.test_project.tests._e2e_test_case import E2ETestCase

from .models import ALL_MODELS, NonIdempotentCalc, NonIdempotentChild
from .test_8k_redis_broker_integration import (
    _make_redis_result_backend,
    _redis_celery_overrides,
    _require_redis_broker,
    _reset_dispatch_contexts,
    _temporary_celery_config,
    _temporary_env,
)

pytestmark = pytest.mark.celery_async

RESULT_TIMEOUT_SECONDS = 30


def _dispatch_context(instance, calculation_id: str) -> dict:
    """Minimal JSON/pickle-safe operation context for a dispatched calc.

    Mirrors what ``dispatch_calculation_task`` ships to the worker: the
    marker checks in ``calc_and_save`` and the ``lex_shared_task`` wrapper
    key off ``calculation_id``.
    """
    return {
        "calculation_id": calculation_id,
        "operation_id": uuid.uuid4().hex,
        "request_obj": {},
    }


class TestCluster08ae_StaleQueueSurvivesStartupReset(E2ETestCase):
    """Cluster 8ae: a calc message queued before the startup reset must not
    re-execute after it — reproduced with a real Redis broker and a real
    in-process Celery worker draining the queue."""

    e2e_models = ALL_MODELS

    def setUp(self) -> None:
        super().setUp()
        NonIdempotentCalc.executions.clear()
        _reset_dispatch_contexts()
        self.addCleanup(NonIdempotentCalc.executions.clear)
        self.addCleanup(_reset_dispatch_contexts)
        self.addCleanup(cluster_cancel_index.reset_client_cache)

    # ── harness helpers ────────────────────────────────────────────────

    def _celery_scope(self, redis_url: str, queue_name: str):
        """Celery config scope for a real pickle round-trip over Redis.

        Pickle (the production serializer) is required because
        ``calc_and_save`` ships real model INSTANCES over the broker —
        exactly as ``dispatch_calculation_task`` does for undecorated
        calculate methods (the customer's PlanningRun path).
        """
        return _temporary_celery_config(
            celery_app,
            **_redis_celery_overrides(redis_url, queue_name),
            task_serializer="pickle",
            result_serializer="pickle",
            accept_content=["pickle", "json"],
            backend=_make_redis_result_backend(
                celery_app, redis_url, serializer="pickle"
            ),
        )

    def _enqueue(self, instance, calculation_id: str, queue_name: str):
        """Publish ``calc_and_save([instance])`` to the broker (no worker yet).

        ``_calculation_hook_in_progress`` is set exactly as the real
        dispatch path does: ``calculate_hook`` flips it True on the
        instance BEFORE ``dispatch_calculation_task`` pickles it, and the
        flag travels in the pickled ``__dict__``. In the worker it is what
        stops ``model.save()`` (still IN_PROGRESS) from re-entering
        ``calculate_hook`` and running the body a second time within ONE
        task. Without it the harness would double-execute for the wrong
        reason and mask the actual stale-queue duplication under test.
        """
        instance._calculation_hook_in_progress = True
        return calc_and_save.apply_async(
            args=([instance],),
            kwargs={"context": _dispatch_context(instance, calculation_id)},
            queue=queue_name,
            serializer="pickle",
        )

    def _drain(self, queue_name: str, results) -> None:
        """Start a real worker on ``queue_name`` and wait for ``results``.

        ``propagate=False``: a task that fails (e.g. the second execution
        dying with ``MultipleObjectsReturned``) must not abort the test —
        the assertions read the aftermath explicitly.
        """
        with start_worker(
            celery_app,
            pool="solo",
            concurrency=1,
            queues=[queue_name],
            perform_ping_check=False,
        ):
            with allow_join_result():
                for result in results:
                    result.get(timeout=RESULT_TIMEOUT_SECONDS, propagate=False)

    def _run_startup_sweep(self) -> None:
        """Run the real boot-time reset exactly as ``lex start`` does.

        ``tracked_record_ids=set()`` matches the shipped default
        (``LEX_TASK_RECOVERY_ENABLED=false`` → the supervisor ownership
        set is empty), so every stuck IN_PROGRESS row is blind-aborted.
        """
        with _temporary_env(CALLED_FROM_START_COMMAND="True"):
            ModelRegistration._handle_calculation_model_reset(
                NonIdempotentCalc, tracked_record_ids=set()
            )

    def _enable_cancel_index(self, redis_url: str) -> None:
        """Turn the cluster cancel index ON for this scenario.

        The task-start marker check no-ops unless ``CELERY_ACTIVE`` is
        set and a broker URL resolves — enabling it here means the
        scenario exercises the REAL cooperative-cancellation net, so the
        assertion flips green the moment the startup sweep starts
        marking abandoned calculations cancelled (the BUG-026 fix).
        """
        previous = {
            "CELERY_ACTIVE": getattr(django_settings, "CELERY_ACTIVE", False),
            "LEX_CLUSTER_CANCEL_ENABLED": getattr(
                django_settings, "LEX_CLUSTER_CANCEL_ENABLED", True
            ),
            "CELERY_BROKER_URL": getattr(
                django_settings, "CELERY_BROKER_URL", None
            ),
        }

        def _restore():
            for key, value in previous.items():
                setattr(django_settings, key, value)
            cluster_cancel_index.reset_client_cache()

        self.addCleanup(_restore)
        django_settings.CELERY_ACTIVE = True
        django_settings.LEX_CLUSTER_CANCEL_ENABLED = True
        django_settings.CELERY_BROKER_URL = redis_url
        cluster_cancel_index.reset_client_cache()

    # -- 8.145 ----------------------------------------------------------
    def test_8_145_single_dispatch_executes_exactly_once(self) -> None:
        """Scenario 8.145 (control): one dispatch → one execution.
        Given: a NonIdempotentCalc dispatched once via calc_and_save over a
               real Redis broker, then a real in-process worker drains the
               queue.
        When:  the worker consumes the message.
        Then:  the calculation body executes exactly once and creates exactly
               one child row — proving the CI worker-spawning harness works,
               so 8.146's xfail is a meaningful reproduction rather than a
               broken harness.
        """
        redis_url = _require_redis_broker()
        queue_name = f"lex-test-8ae-{uuid.uuid4().hex}"

        instance = NonIdempotentCalc.objects.create(name="SD 2026")
        instance.is_calculated = CalculationModel.IN_PROGRESS
        instance.save(skip_hooks=True)

        with self._celery_scope(redis_url, queue_name):
            enable_insecure_serializers(["pickle"])
            with celery_app.connection_for_write() as conn:
                conn.ensure_connection(max_retries=1, timeout=1)

            result = self._enqueue(
                instance,
                f"nonidempotentcalc_{instance.pk}_update_{uuid.uuid4().hex}",
                queue_name,
            )
            self._drain(queue_name, [result])

        self.assertEqual(
            len(NonIdempotentCalc.executions),
            1,
            "A single dispatched calculation must execute exactly once — "
            f"got executions for {NonIdempotentCalc.executions!r}",
        )
        self.assertEqual(
            NonIdempotentChild.objects.filter(plan=instance).count(),
            1,
            "One execution must create exactly one child row",
        )
        self.assertEqual(
            result.state,
            "SUCCESS",
            f"The dispatched task must succeed, got {result.state!r}",
        )

    # -- 8.146 ----------------------------------------------------------
    @pytest.mark.xfail(
        strict=True,
        reason=(
            "BUG-026: the startup sweep resets IN_PROGRESS rows but neither "
            "purges the queued broker message nor sets the cluster cancel "
            "marker for the abandoned calculation_id, so the stale task "
            "re-executes when workers boot — double execution, duplicate "
            "child rows, MultipleObjectsReturned in the new run."
        ),
    )
    def test_8_146_stale_pre_reset_message_must_not_reexecute(self) -> None:
        """Scenario 8.146 (repro): stale queued message + startup reset.
        Given: a calc dispatched to the broker while NO worker is consuming
               (workers down); the server restarts and the startup sweep
               flips the row IN_PROGRESS→ABORTED (reopening the duplicate
               guard); the user re-triggers, dispatching a second task with a
               fresh calculation_id. Both messages now sit on the broker —
               the production 2026-07-14 PlanningRun log, step for step.
        When:  a real worker boots and drains the queue.
        Then:  the calculation body executes exactly ONCE (the stale
               pre-reset message self-aborts via the cancel marker), exactly
               one child row exists, and the user's new run succeeds instead
               of dying with MultipleObjectsReturned.
        Currently: the sweep leaves the broker and the cancel index
               untouched → both messages execute → 2 executions, 2 child
               rows, the new run fails — hence strict xfail (BUG-026).
        """
        redis_url = _require_redis_broker()
        queue_name = f"lex-test-8ae-{uuid.uuid4().hex}"

        instance = NonIdempotentCalc.objects.create(name="SD 2026")
        instance.is_calculated = CalculationModel.IN_PROGRESS
        instance.save(skip_hooks=True)

        stale_calc_id = f"nonidempotentcalc_{instance.pk}_update_{uuid.uuid4().hex}"
        fresh_calc_id = f"nonidempotentcalc_{instance.pk}_update_{uuid.uuid4().hex}"

        self._enable_cancel_index(redis_url)

        with self._celery_scope(redis_url, queue_name):
            enable_insecure_serializers(["pickle"])
            with celery_app.connection_for_write() as conn:
                conn.ensure_connection(max_retries=1, timeout=1)

            # 1. The pre-restart click: message parked, no worker running.
            stale_result = self._enqueue(instance, stale_calc_id, queue_name)

            # 2. Server restart: the boot sweep blind-aborts the row.
            #    The broker message and the cancel index are NOT touched —
            #    this is the hole under test.
            self._run_startup_sweep()
            instance.refresh_from_db()
            self.assertEqual(
                instance.is_calculated,
                CalculationModel.ABORTED,
                "Precondition: the startup sweep must abort the stuck row "
                "(reopening One.py's duplicate-calculation guard)",
            )

            # 3. The user's re-click, now allowed by the reopened guard.
            instance.is_calculated = CalculationModel.IN_PROGRESS
            instance.save(skip_hooks=True)
            fresh_result = self._enqueue(instance, fresh_calc_id, queue_name)

            # 4. Workers boot and drain BOTH messages (FIFO: stale first).
            self._drain(queue_name, [stale_result, fresh_result])

        self.assertEqual(
            len(NonIdempotentCalc.executions),
            1,
            "The stale pre-reset message must NOT execute the calculation "
            "body — the startup sweep must invalidate the abandoned "
            f"calculation. Executions seen: {NonIdempotentCalc.executions!r}",
        )
        self.assertEqual(
            NonIdempotentChild.objects.filter(plan=instance).count(),
            1,
            "Double execution created duplicate child rows — the customer's "
            "duplicate-Sponsor symptom",
        )
        self.assertEqual(
            fresh_result.state,
            "SUCCESS",
            "The user's re-triggered calculation must succeed; today it dies "
            "with MultipleObjectsReturned because the stale run already "
            "created the child rows",
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
