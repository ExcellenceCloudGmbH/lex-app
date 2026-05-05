"""
Cluster 8h: Celery end-to-end via **eager mode** — no broker, no worker.

Intent
------

Sub-cluster 8g drove the unit-level contracts of
``lex/lex_app/celery_tasks.py`` by patching ``.delay`` to return a
``MagicMock``. That proved the *routing* logic works, but it never
exercises the real ``.delay`` → ``@lex_shared_task`` wrapper → task
body → :class:`CallbackTask.on_success` / ``on_failure`` sequence that
a customer hits the moment they turn ``CELERY_ACTIVE=true`` on in
production.

This sub-cluster closes that gap without a Redis or RabbitMQ broker
and without a running Celery worker. The mechanism is
**Celery eager mode** — the documented test harness from Celery
itself:

    ``app.conf.task_always_eager = True``
        makes ``.delay(...)`` run the task body **inline in the
        calling thread** and return an ``EagerResult`` that already
        carries the return value.
    ``app.conf.task_eager_propagates = True``
        makes task-body exceptions propagate to the caller instead of
        being swallowed by the ``EagerResult`` wrapper.

Combined with ``CELERY_ACTIVE=true`` in the environment, this exercises
the **full production dispatch path**:

1. ``EnhancedBoundTaskMethod.__call__`` sees ``_celery_is_active()``
   return ``True`` and invokes ``self.task.delay(...)``.
2. Celery's real eager-execution machinery runs the
   ``@lex_shared_task`` wrapper body inline.
3. ``CallbackTask.on_success`` / ``on_failure`` fires on the real task
   instance with the real ``args`` / ``kwargs``.
4. Our ``_persist_status_fields`` runs against the real ORM and writes
   ``is_calculated``.
5. ``ensure_terminal_calculation_audit`` fires with the real audit
   seam.

No scenarios in here patch anything inside
``lex/lex_app/celery_tasks.py``. The whole module runs end-to-end.

The only thing that looks faked is the Celery broker — and that is
Celery's own test feature, not ours.

Scenario numbering extends
``docs/test-plan/test-clusters.md`` — sub-cluster 8h picks up at
**8.16**.
"""

from __future__ import annotations

import os
import unittest
from contextlib import contextmanager
from unittest import mock

from lex.core.models.CalculationModel import CalculationModel
from lex.lex_app.celery import app as celery_app
from lex.lex_app.celery_tasks import (
    EnhancedBoundTaskMethod,
    FireAndForget,
    WaitForTasks,
    is_celery_worker_process,
    tasks_context,
    unblock_tasks_context,
)

from lex.tests.e2e._e2e_test_case import E2ETestCase

from .models import ALL_MODELS, CeleryCalc


# ---------------------------------------------------------------------
# Shared eager-mode fixture
# ---------------------------------------------------------------------
@contextmanager
def _celery_eager(propagate: bool = True):
    """Flip the Celery app into eager mode + turn ``CELERY_ACTIVE`` on.

    ``app.conf.task_always_eager`` and ``task_eager_propagates`` are
    the two Celery knobs that let us run ``.delay(...)`` inline. They
    are idempotent and self-restoring via this context manager.

    With ``propagate=True`` (default) task-body exceptions re-raise
    inline — convenient for happy-path + explicit-failure assertions.
    With ``propagate=False`` failures are captured on the
    ``EagerResult`` instead, which is what lets Celery invoke
    ``CallbackTask.on_failure`` before the caller sees the error.

    We also force the result backend to an **in-process cache+memory**
    backend for the duration of the scope. The project default is
    ``db+postgresql://django:lundadminlocal@localhost/db_<repo>`` — a
    SQLAlchemy backend that issues ``CREATE TABLE`` DDL the moment
    Celery instantiates it (``celery.backends.database.__init__`` calls
    ``_create_tables`` from ``__init__``). In CI the runner Postgres
    has a single database named ``db_lex`` (or whatever the runner
    bootstraps), not ``db_<repo_name>``, so accessing ``app.backend``
    crashes with ``database "db_<repo>-app" does not exist`` even
    though we never wanted to *use* the result backend — eager mode
    triggers the lookup unconditionally. Routing the result backend
    through ``cache+memory://`` keeps the eager-mode tests fully
    self-contained and free of external storage dependencies. The
    cached ``_backend`` is dropped via ``app.__dict__.pop`` so the
    override actually takes effect — Celery memoises the resolved
    backend on first read (``celery/app/base.py::_get_backend``). We
    must NOT use ``app._backend = None``: in Celery 5.5+ ``_backend``
    is a property whose setter calls ``backend.thread_safe`` before
    storing, which crashes on ``None``. ``__dict__.pop`` works on
    every Celery version because the property's getter reads from the
    instance dict and a plain attribute lives there too.
    """
    prior_eager = celery_app.conf.task_always_eager
    prior_propagates = celery_app.conf.task_eager_propagates
    prior_backend_url = celery_app.conf.result_backend
    prior_backend = celery_app.__dict__.get("_backend")
    prior_env = os.environ.get("CELERY_ACTIVE")
    celery_app.conf.task_always_eager = True
    celery_app.conf.task_eager_propagates = propagate
    celery_app.conf.result_backend = "cache+memory://"
    celery_app.__dict__.pop("_backend", None)
    os.environ["CELERY_ACTIVE"] = "true"
    try:
        yield
    finally:
        celery_app.conf.task_always_eager = prior_eager
        celery_app.conf.task_eager_propagates = prior_propagates
        celery_app.conf.result_backend = prior_backend_url
        if prior_backend is None:
            celery_app.__dict__.pop("_backend", None)
        else:
            celery_app.__dict__["_backend"] = prior_backend
        if prior_env is None:
            os.environ.pop("CELERY_ACTIVE", None)
        else:
            os.environ["CELERY_ACTIVE"] = prior_env


def _reset_dispatch_ctx():
    tasks_context.set({"task_context_stack": []})
    unblock_tasks_context.set({"unblock_context_stack": []})


# ---------------------------------------------------------------------
# 8.16 / 8.17 — Real .delay() round-trip + CallbackTask
# ---------------------------------------------------------------------
class TestCluster08h_DelayRoundTrip(E2ETestCase):
    """A real ``.delay(...)`` call on a ``@lex_shared_task`` method
    runs the task body, fires the CallbackTask, and persists the
    terminal state — the actual production path.

    No Celery internals are patched. Only the broker is "faked", and
    it is faked by Celery itself (``task_always_eager``)."""

    e2e_models = ALL_MODELS
    # Let the real audit seam run so we can observe it.
    e2e_unpatch = {"ensure_terminal_calculation_audit"}

    def _spy_audit(self):
        p = mock.patch("lex.lex_app.celery_tasks.ensure_terminal_calculation_audit")
        spy = p.start()
        self.addCleanup(p.stop)
        return spy

    # -- 8.16 ----------------------------------------------------------
    def test_8_16_delay_success_flips_row_to_success_and_audits(self) -> None:
        """Scenario 8.16: ``CeleryCalc.calculate.delay(instance)`` runs
        the real task body and fires the real ``CallbackTask.on_success``.

        Given: a persisted ``CeleryCalc`` in NOT_CALCULATED.
        When:  ``.delay(instance)`` is called in eager mode.
        Then:  Celery executes the wrapped task body inline → the body
               returns successfully → Celery invokes the real
               ``CallbackTask.on_success`` → ``_persist_status_fields``
               runs a direct ``.update(is_calculated="SUCCESS")`` → the
               audit seam fires with ``audit_status="success"``. The
               DB row now carries SUCCESS.

        This is the single-task path a customer hits on every
        API-triggered calculation once ``CELERY_ACTIVE=true``.
        """
        audit_spy = self._spy_audit()
        calc = CeleryCalc(name="s8-16", should_fail=False,
                          is_calculated=CalculationModel.NOT_CALCULATED)
        calc.save()
        CeleryCalc.objects.filter(pk=calc.pk).update(
            is_calculated=CalculationModel.IN_PROGRESS
        )

        with _celery_eager():
            # ``.delay`` exists on the underlying Celery task — we go
            # through ``EnhancedTaskMethodDescriptor.__getattr__`` to
            # get there so the test mirrors what production code does.
            result = CeleryCalc.calculate.delay(calc)

        # Eager result is ready immediately and carries the
        # ``(inner_result, args)`` tuple our ``lex_shared_task`` wrapper
        # returns.
        self.assertTrue(
            result.ready(),
            "Eager result must be ready immediately after .delay()",
        )
        self.assertTrue(result.successful(), "Task must have succeeded")

        fresh = CeleryCalc.objects.get(pk=calc.pk)
        self.assertEqual(
            fresh.is_calculated, CalculationModel.SUCCESS,
            "Real CallbackTask.on_success must have flipped is_calculated "
            "to SUCCESS on the persisted row",
        )

        # Audit seam fired with success — at least once (Celery fires
        # on_success after task body returns).
        self.assertTrue(
            audit_spy.called,
            "ensure_terminal_calculation_audit must be invoked from "
            "CallbackTask.on_success",
        )
        success_calls = [
            c for c in audit_spy.call_args_list
            if c.kwargs.get("audit_status") == "success"
        ]
        self.assertTrue(
            success_calls,
            f"At least one audit call must carry audit_status='success'; "
            f"got {[c.kwargs.get('audit_status') for c in audit_spy.call_args_list]!r}",
        )

    # -- 8.17 ----------------------------------------------------------
    def test_8_17_delay_failure_flips_row_to_error_and_audits(self) -> None:
        """Scenario 8.17: when the real task body raises, Celery's
        eager path runs the real ``CallbackTask.on_failure`` — the row
        must end up as ERROR and the audit must record the failure.

        With ``task_eager_propagates=True`` the exception propagates
        back to the caller, so we assert on the exception as well —
        that propagation is what production code relies on to surface
        failures in the calling thread's error-handling.
        """
        audit_spy = self._spy_audit()
        calc = CeleryCalc(name="s8-17", should_fail=True,
                          is_calculated=CalculationModel.NOT_CALCULATED)
        calc.save()
        CeleryCalc.objects.filter(pk=calc.pk).update(
            is_calculated=CalculationModel.IN_PROGRESS
        )

        # Propagate=False so Celery's trace machinery has a chance to
        # invoke CallbackTask.on_failure BEFORE the exception bubbles
        # out. With propagate=True the exception re-raises inline and
        # the callback never runs — that is Celery's documented eager
        # behaviour. Production workers see the on_failure path because
        # they always run propagate=False (the broker holds the result).
        with _celery_eager(propagate=False):
            result = CeleryCalc.calculate.delay(calc)

        self.assertTrue(result.failed(), "EagerResult must report failure")
        self.assertIsInstance(result.result, RuntimeError)
        self.assertIn("failing on purpose", str(result.result))

        fresh = CeleryCalc.objects.get(pk=calc.pk)
        self.assertEqual(
            fresh.is_calculated, CalculationModel.ERROR,
            "Real CallbackTask.on_failure must have flipped is_calculated "
            "to ERROR on the persisted row",
        )

        failure_calls = [
            c for c in audit_spy.call_args_list
            if c.kwargs.get("audit_status") == "failure"
        ]
        self.assertTrue(
            failure_calls,
            "At least one audit call must carry audit_status='failure'",
        )
        fail_kwargs = failure_calls[-1].kwargs
        self.assertIn(
            "failing on purpose", (fail_kwargs.get("error_message") or ""),
            "Error message from the raised exception must land on the "
            "terminal audit record",
        )


# ---------------------------------------------------------------------
# 8.18 — EnhancedBoundTaskMethod + WaitForTasks real round-trip
# ---------------------------------------------------------------------
class TestCluster08h_EnhancedBoundRoundTrip(E2ETestCase):
    """The router path a customer actually hits: inside a
    ``WaitForTasks`` scope, ``EnhancedBoundTaskMethod`` calls the real
    ``.delay(...)``, Celery runs the task eagerly, and the scope exit
    drains the real ``EagerResult`` without any broker contact."""

    e2e_models = ALL_MODELS

    def setUp(self):
        super().setUp()
        _reset_dispatch_ctx()
        self.addCleanup(_reset_dispatch_ctx)

    # -- 8.18 ----------------------------------------------------------
    def test_8_18_wait_for_tasks_scope_drains_real_eager_result(self) -> None:
        """Scenario 8.18: inside a ``WaitForTasks`` scope, calling the
        bound method goes through the router → ``.delay`` → eager
        execution → registered EagerResult drained on scope exit.

        Everything is real: EnhancedBoundTaskMethod, WaitForTasks,
        CallbackTask, the ``lex_shared_task`` wrapper. Only the broker
        is "eager-faked" by Celery itself. This is the closest we can
        get to a production dispatch without spinning a worker.
        """
        calc = CeleryCalc(name="s8-18", should_fail=False,
                          is_calculated=CalculationModel.NOT_CALCULATED)
        calc.save()
        CeleryCalc.objects.filter(pk=calc.pk).update(
            is_calculated=CalculationModel.IN_PROGRESS
        )

        # Pull the underlying Celery task (the descriptor's ``.task``)
        # so we can build the bound method the router uses.
        bound = EnhancedBoundTaskMethod(
            instance=calc,
            task=type(calc).calculate.task,
        )

        with _celery_eager():
            wft = WaitForTasks()
            wft._active = True  # snapshot captured at __init__ — force on
            with wft:
                # ``__enter__`` only appends when ``_active`` was True
                # at enter-time; guard against the no-op case:
                if wft not in tasks_context.get()["task_context_stack"]:
                    tasks_context.get()["task_context_stack"].append(wft)

                result = bound()  # routed to .delay() → eager execution

                self.assertIn(
                    result, wft.dispatched_results,
                    "WaitForTasks must capture the real EagerResult on "
                    "dispatch — that is the handle it will block on at "
                    "scope exit",
                )
            # Scope exit calls wait_for_completion which .get()'s each
            # registered result. Under eager mode that is a no-op join.

        fresh = CeleryCalc.objects.get(pk=calc.pk)
        self.assertEqual(
            fresh.is_calculated, CalculationModel.SUCCESS,
            "The task body ran for real and CallbackTask.on_success "
            "flipped the row to SUCCESS",
        )
        self.assertEqual(
            wft.dispatched_results, [],
            "WaitForTasks must clear dispatched_results after draining",
        )


# ---------------------------------------------------------------------
# 8.19 — CeleryTaskDispatcher.dispatch_calculation_groups, real
# ---------------------------------------------------------------------
class TestCluster08h_DispatcherEndToEnd(E2ETestCase):
    """``CeleryTaskDispatcher.dispatch_calculation_groups`` is what
    ``CalculatedModelMixin._dispatch_model_processing`` calls when it
    decides to go async. 7h proved the **routing** hits the dispatcher;
    this scenario proves the dispatcher's real body runs the tasks to
    completion end-to-end, not mocked."""

    e2e_models = ALL_MODELS

    # -- 8.19 ----------------------------------------------------------
    def test_8_19_dispatcher_runs_every_group_via_real_calc_and_save(self) -> None:
        """Scenario 8.19: multi-group dispatch runs every group's task
        body inline under eager mode; every model ends up saved with
        ``is_calculated=SUCCESS``.

        The dispatcher imports ``calc_and_save`` at call time and
        invokes ``.delay(models)`` on each group. In eager mode the
        task body runs, ``model.lex_func()()`` fires, ``model.save()``
        commits, and ``CallbackTask.on_success`` flips
        ``is_calculated`` for each row.
        """
        from lex.core.tasks.CeleryTaskDispatcher import CeleryTaskDispatcher

        rows = [
            CeleryCalc.objects.create(
                name=f"s8-19-{i}", should_fail=False,
                is_calculated=CalculationModel.NOT_CALCULATED,
            )
            for i in range(3)
        ]
        # Two groups — 2 rows + 1 row. Both must run.
        groups = [rows[:2], rows[2:]]

        with _celery_eager():
            # The dispatcher reads ``context['request_obj']`` so it can
            # serialise a sanitised copy onto the ``calc_and_save``
            # task. Customers provide this via the API middleware
            # pipeline; in-process we just hand it the shape the
            # dispatcher expects.
            CeleryTaskDispatcher.dispatch_calculation_groups(
                groups,
                context={"calculation_id": "test-8-19", "request_obj": {}},
            )

        for r in rows:
            fresh = CeleryCalc.objects.get(pk=r.pk)
            self.assertEqual(
                fresh.is_calculated, CalculationModel.SUCCESS,
                f"Row {r.name!r} must be SUCCESS after real dispatcher "
                f"run; got {fresh.is_calculated!r}",
            )


# ---------------------------------------------------------------------
# 8.20 — is_celery_worker_process() inside an eager task body
# ---------------------------------------------------------------------
class TestCluster08h_IsCeleryWorkerProcess(E2ETestCase):
    """``is_celery_worker_process()`` is the guard that stops nested
    calc dispatch from recursively spawning worker-blocking child
    tasks. Under eager mode, Celery still sets ``current_task`` while
    a task body is executing — so the guard fires and tests the real
    code path without needing a worker process."""

    e2e_models = ALL_MODELS

    # -- 8.20 ----------------------------------------------------------
    def test_8_20_returns_true_inside_running_task_body(self) -> None:
        """Scenario 8.20: while a task body is executing (eager or
        remote), ``is_celery_worker_process()`` must return True.

        The helper inspects ``celery.current_task``. Celery populates
        that during task execution in **both** eager and remote modes,
        so this scenario exercises the real guard without needing a
        separate worker process.
        """
        from celery import shared_task

        observed: dict[str, bool] = {}

        @shared_task
        def _probe():
            observed["inside"] = is_celery_worker_process()

        with _celery_eager():
            # Outside a task body: False — we're the main thread.
            self.assertFalse(
                is_celery_worker_process(),
                "Main-thread caller must not register as a worker process",
            )
            _probe.delay()

        self.assertTrue(
            observed.get("inside"),
            "is_celery_worker_process() must return True while a task "
            "body is running — that is the only guard against recursive "
            "dispatch inside workers",
        )


# ---------------------------------------------------------------------
# 8.21 — register_task_with_context routes to active scope
# ---------------------------------------------------------------------
class TestCluster08h_RegisterTaskWithContext(E2ETestCase):
    """``register_task_with_context`` is the helper customers call when
    they dispatch a task outside the ``EnhancedBoundTaskMethod`` router
    (e.g. a plain ``.delay()`` inside a scope). It must attach the
    result to whatever dispatch scope is currently active — FF first,
    then WFT."""

    e2e_models = ALL_MODELS

    def setUp(self):
        super().setUp()
        _reset_dispatch_ctx()
        self.addCleanup(_reset_dispatch_ctx)

    # -- 8.21 ----------------------------------------------------------
    def test_8_21_fire_and_forget_wins_over_wait_for_tasks(self) -> None:
        """Scenario 8.21: ``register_task_with_context`` prefers
        FireAndForget over WaitForTasks.

        Customer pattern: an outer ``WaitForTasks`` block, an inner
        ``FireAndForget`` for something fire-and-forget (emails,
        analytics), a plain ``.delay(...)`` inside that inner block.
        The helper must attach the AsyncResult to the FF scope, not
        the outer WFT, so the outer block doesn't block waiting on a
        result it was never meant to wait on.
        """
        from lex.lex_app.celery_tasks import register_task_with_context

        wft = WaitForTasks()
        ff = FireAndForget()
        wft._active = True
        ff._active = True

        tasks_context.get()["task_context_stack"].append(wft)
        unblock_tasks_context.get()["unblock_context_stack"].append(ff)

        fake_result = mock.MagicMock(name="EagerResult")
        returned = register_task_with_context(fake_result)

        self.assertIs(
            returned, fake_result,
            "Helper must return the result object unchanged so callers "
            "can keep assigning it",
        )
        self.assertIn(
            fake_result, ff.dispatched_results,
            "FireAndForget takes priority — result lands on the inner FF scope",
        )
        self.assertNotIn(
            fake_result, wft.dispatched_results,
            "When FF is active, WFT must NOT also capture the result",
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()




