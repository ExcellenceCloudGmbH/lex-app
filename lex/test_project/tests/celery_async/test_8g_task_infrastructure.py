"""
Cluster 8g: Celery task infrastructure (``lex/lex_app/celery_tasks.py``).

Intent
------

Cluster 8 (a) covers the **sync fallback** — what happens when
``CELERY_ACTIVE`` is off.  This sub-cluster (8g) drives the remaining
customer-visible surface of ``lex/lex_app/celery_tasks.py``:

* :class:`CeleryCalculationContext` — the ContextVar gymnastics that
  keeps ``operation_context`` / ``_model_context`` alive inside a
  Celery worker.
* :class:`CallbackTask` — the ``on_success`` / ``on_failure`` callbacks
  Celery invokes after the remote task finishes.  They are the seam
  through which the worker's result becomes a ``is_calculated`` state
  transition, a bitemporal main-table ``.update()``, and a terminal
  audit-log entry on the caller side.
* :class:`EnhancedBoundTaskMethod` — the dispatch router that picks
  between ``.delay(...)`` (remote), sync execution, FireAndForget, and
  WaitForTasks based on the active context.
* :func:`lex_shared_task` — the decorator wrapper that pops the
  reserved ``context`` / ``model_context`` kwargs and wraps the inner
  function in :class:`CeleryCalculationContext`.

No broker required
------------------

Every scenario runs **without a Redis / RabbitMQ connection**:

1. Branches gated on ``CELERY_ACTIVE`` are driven by patching the env
   var or the module-level ``_celery_is_active`` helper — the
   framework never tries to open a connection on its own.
2. Scenarios that need a ``.delay(...)`` result patch ``.delay`` onto
   the task itself to return a ``MagicMock`` stand-in for Celery's
   ``AsyncResult``.  The task body is never dispatched to a remote
   worker; ``.delay`` is just a method we intercept.
3. :meth:`WaitForTasks.wait_for_completion` normally calls
   ``allow_join_result()`` — a Celery context manager that, in
   production, talks to the broker.  The scenario that exercises the
   blocking path swaps ``allow_join_result`` for
   ``contextlib.nullcontext`` so the join seam executes without a
   broker.
4. The callback scenarios (on_success / on_failure) instantiate
   :class:`CallbackTask` directly — Celery never runs, so there is no
   task registration and no broker lookup.  We still drive the real
   ORM, the real signal, and the real audit-log seam.
"""

from __future__ import annotations

import os
import unittest
from contextlib import nullcontext
from unittest.mock import MagicMock, patch

from lex.core.models.CalculationModel import CalculationModel
from lex.lex_app.celery_tasks import (
    CallbackTask,
    CeleryCalculationContext,
    EnhancedBoundTaskMethod,
    FireAndForget,
    WaitForTasks,
    lex_shared_task,
    tasks_context,
    unblock_tasks_context,
)
from lex.tests.e2e._e2e_test_case import E2ETestCase

from .models import ALL_MODELS, CeleryCalc


def _reset_ctx():
    """Reset the module-level ContextVars so each scenario starts clean."""
    tasks_context.set({"task_context_stack": []})
    unblock_tasks_context.set({"unblock_context_stack": []})


class TestCluster08g_CallbackTask(E2ETestCase):
    """``CallbackTask.on_success`` / ``on_failure`` end-to-end.

    These callbacks run on the **caller side** after the Celery worker
    finishes.  The test invokes them directly against a real persisted
    ``CeleryCalc`` — no broker needed — and asserts the row is updated
    via a direct queryset ``.update()`` (not a full model save, so
    stale snapshots can't overwrite unrelated columns) and that
    ``ensure_terminal_calculation_audit`` is invoked with the right
    ``audit_status``.
    """

    e2e_models = ALL_MODELS
    # We want to observe — not silence — the audit seam for 8.7 / 8.8 / 8.9.
    # NOTE: celery_tasks.py does ``from lex.audit_logging.utils.calculation_audit
    # import ensure_terminal_calculation_audit`` at module load, so patching
    # the source module is too late — patch the re-exported symbol directly.
    e2e_unpatch = {"ensure_terminal_calculation_audit"}

    def _spy_audit(self):
        """Patch ``ensure_terminal_calculation_audit`` at the import site used
        by ``CallbackTask`` so the spy actually sees the call."""
        p = patch("lex.lex_app.celery_tasks.ensure_terminal_calculation_audit")
        spy = p.start()
        self.addCleanup(p.stop)
        return spy

    # -- 8.7 -----------------------------------------------------------
    def test_8_7_callback_on_success_flips_state_to_success(self) -> None:
        """Scenario 8.7: ``on_success`` flips ``is_calculated`` → SUCCESS.

        Given: a persisted ``CeleryCalc`` in IN_PROGRESS.
        When:  ``CallbackTask.on_success`` is invoked with the row as args[0].
        Then:  the DB row carries ``is_calculated = "SUCCESS"`` afterwards,
               proving ``_persist_status_fields`` performed a direct
               queryset ``.update(is_calculated="SUCCESS")`` without
               rewriting any other column from the (possibly stale) snapshot.
        """
        audit_spy = self._spy_audit()

        calc = CeleryCalc(name="s8-7", is_calculated=CalculationModel.NOT_CALCULATED)
        calc.save()
        CeleryCalc.objects.filter(pk=calc.pk).update(
            is_calculated=CalculationModel.IN_PROGRESS
        )

        task = CallbackTask()
        task.name = "lex.test_project.celery_task_spy"
        task.on_success(
            retval=None,
            task_id="task-8-7",
            args=(calc,),
            kwargs={"context": None, "model_context": None},
        )

        fresh = CeleryCalc.objects.get(pk=calc.pk)
        self.assertEqual(
            fresh.is_calculated, CalculationModel.SUCCESS,
            "on_success must flip is_calculated → SUCCESS on the persisted row",
        )
        audit_spy.assert_called_once()
        _, kwargs = audit_spy.call_args
        self.assertEqual(
            kwargs.get("audit_status"), "success",
            "Terminal audit must be logged with audit_status='success'",
        )

    # -- 8.8 -----------------------------------------------------------
    def test_8_8_callback_on_failure_flips_state_to_error(self) -> None:
        """Scenario 8.8: ``on_failure`` flips ``is_calculated`` → ERROR and
        records the error details on the terminal audit entry.

        Given: a persisted ``CeleryCalc`` in IN_PROGRESS.
        When:  ``CallbackTask.on_failure`` is invoked with an exception.
        Then:  the DB row carries ``is_calculated = "ERROR"`` and
               ``ensure_terminal_calculation_audit`` is called with
               ``audit_status="failure"`` plus the exception message
               and the stack-trace string.
        """
        audit_spy = self._spy_audit()

        calc = CeleryCalc(name="s8-8", is_calculated=CalculationModel.NOT_CALCULATED)
        calc.save()
        CeleryCalc.objects.filter(pk=calc.pk).update(
            is_calculated=CalculationModel.IN_PROGRESS
        )

        task = CallbackTask()
        task.name = "lex.test_project.celery_task_spy"
        task.on_failure(
            exc=RuntimeError("broker unreachable"),
            task_id="task-8-8",
            args=(calc,),
            kwargs={"context": {"calculation_id": "c-8-8"}, "model_context": None},
            einfo="Traceback (most recent call last): ...",
        )

        fresh = CeleryCalc.objects.get(pk=calc.pk)
        self.assertEqual(
            fresh.is_calculated, CalculationModel.ERROR,
            "on_failure must flip is_calculated → ERROR on the persisted row",
        )
        audit_spy.assert_called_once()
        _, kwargs = audit_spy.call_args
        self.assertEqual(kwargs.get("audit_status"), "failure")
        self.assertIn("broker unreachable", kwargs.get("error_message", ""))
        self.assertEqual(
            kwargs.get("stack_trace"),
            "Traceback (most recent call last): ...",
            "Stack-trace string from einfo must be forwarded verbatim",
        )

    # -- 8.9 -----------------------------------------------------------
    def test_8_9_callbacks_skip_initial_data_upload(self) -> None:
        """Scenario 8.9: the ``initial_data_upload`` task short-circuits both
        callbacks — no status update, no audit row.

        ``initial_data_upload`` is not a ``CalculationModel``, so its
        args[0] is not a ``Model`` instance and the callback must NOT
        try to ``.update()`` anything.  Regression gate against the
        callback ever being generalised without remembering this
        opt-out.
        """
        audit_spy = self.spy_on("ensure_terminal_calculation_audit")

        task = CallbackTask()
        task.name = "initial_data_upload"
        # Deliberately pass nonsense args — if the callback didn't
        # short-circuit on the task name it would crash here.
        task.on_success(retval=None, task_id="t-boot", args=(), kwargs={})
        task.on_failure(
            exc=RuntimeError("doesn't matter"),
            task_id="t-boot",
            args=("not-a-model",),
            kwargs={},
            einfo=None,
        )

        audit_spy.assert_not_called()


class TestCluster08g_CeleryContext(E2ETestCase):
    """``CeleryCalculationContext`` ContextVar juggling."""

    e2e_models = ALL_MODELS

    # -- 8.10 ----------------------------------------------------------
    def test_8_10_context_sets_and_restores_operation_context(self) -> None:
        """Scenario 8.10: ``CeleryCalculationContext`` stamps the worker-side
        operation context with the incoming ``calculation_id`` and a
        fresh ``operation_id``; on exit the previous token is restored.

        This is the seam that lets ``CalculationLog.log()`` inside a
        Celery worker reach back to the originating calculation row.
        """
        from lex.api.utils import operation_context

        baseline = operation_context.get()
        incoming = {"calculation_id": "calc-xyz", "request_obj": {}}

        with CeleryCalculationContext(context=incoming, model_context=None):
            ctx = operation_context.get()
            self.assertEqual(
                ctx["calculation_id"], "calc-xyz",
                "Calculation id from the dispatching side must be preserved",
            )
            self.assertTrue(
                ctx.get("celery_task"),
                "Worker-side context must carry celery_task=True",
            )
            self.assertEqual(ctx.get("task_name"), "calc_and_save")
            self.assertTrue(
                ctx.get("operation_id"),
                "A fresh operation_id must be minted for the worker run",
            )

        self.assertEqual(
            operation_context.get(), baseline,
            "operation_context must be restored to its prior value on exit",
        )

    # -- 8.11 ----------------------------------------------------------
    def test_8_11_context_without_context_is_noop(self) -> None:
        """Scenario 8.11: ``CeleryCalculationContext(context=None, …)`` is a
        safe no-op — ``operation_context`` is *not* overwritten.

        This matches the synchronous-execution branch in
        ``lex_shared_task``: the parent thread already holds a valid
        operation_context, so wrapping again with None would clobber it.
        """
        from lex.api.utils import operation_context

        baseline = operation_context.get()
        with CeleryCalculationContext(context=None, model_context=None):
            self.assertEqual(
                operation_context.get(), baseline,
                "operation_context must NOT be mutated when no context was passed",
            )
        self.assertEqual(operation_context.get(), baseline)


class TestCluster08g_DispatchRouting(E2ETestCase):
    """``EnhancedBoundTaskMethod`` dispatch priority.

    1. ``FireAndForget`` (highest)  → ``.delay`` always
    2. ``WaitForTasks``             → ``.delay`` + result registered
    3. No context                   → sync (``task(*args, **kwargs)``)

    All three paths are exercised here without hitting a broker: we
    patch ``.delay`` onto a fake task to return a ``MagicMock``
    (stand-in for an ``AsyncResult``), and swap
    ``allow_join_result`` for ``nullcontext`` when the WaitForTasks
    scope exits and tries to join.
    """

    e2e_models = ALL_MODELS

    def setUp(self):
        super().setUp()
        _reset_ctx()
        self.addCleanup(_reset_ctx)

    def _make_task(self, name="compute_nav"):
        """Build a fake task object that counts sync invocations and
        exposes a patchable ``.delay``."""
        task = MagicMock()
        task.__name__ = name
        task.name = name
        task.return_value = "sync-result"
        task.delay = MagicMock(return_value=MagicMock(name=f"AsyncResult<{name}>"))
        return task

    # -- 8.12 ----------------------------------------------------------
    def test_8_12_sync_when_no_context_and_celery_active(self) -> None:
        """Scenario 8.12: CELERY_ACTIVE=true but no FF/WFT scope active →
        the descriptor runs the task body synchronously.

        ``.delay`` must NOT be called.  This is the "dispatch-capable
        but nobody asked for async" default.
        """
        task = self._make_task()
        instance = object()
        bound = EnhancedBoundTaskMethod(instance=instance, task=task)

        with patch.dict(os.environ, {"CELERY_ACTIVE": "true"}, clear=False):
            result = bound(42, x=7)

        self.assertEqual(result, "sync-result")
        task.delay.assert_not_called()
        task.assert_called_once_with(instance, 42, x=7)

    # -- 8.13 ----------------------------------------------------------
    def test_8_13_fire_and_forget_forces_delay_and_registers_result(self) -> None:
        """Scenario 8.13: inside FireAndForget, even with a WaitForTasks
        scope open, ``.delay`` is called and the result lands on the
        FF context (not the WFT context) — FF takes priority.
        """
        task = self._make_task("send_email")
        bound = EnhancedBoundTaskMethod(instance=object(), task=task)

        with patch.dict(os.environ, {"CELERY_ACTIVE": "true"}, clear=False):
            wft = WaitForTasks()
            ff = FireAndForget()
            wft._active = True
            ff._active = True

            tasks_context.get()["task_context_stack"].append(wft)
            unblock_tasks_context.get()["unblock_context_stack"].append(ff)

            result = bound("payload")

        task.delay.assert_called_once()
        # Sync body must NOT have run.
        self.assertEqual(task.call_count, 0)
        self.assertIn(result, ff.dispatched_results,
                      "FireAndForget must win over WaitForTasks — the result "
                      "is registered on the inner FF scope, not the outer WFT")
        self.assertNotIn(result, wft.dispatched_results)

    # -- 8.14 ----------------------------------------------------------
    def test_8_14_wait_for_tasks_dispatches_and_blocks_on_exit(self) -> None:
        """Scenario 8.14: inside WaitForTasks (no FF), ``.delay`` is called,
        the result is registered on the WFT scope, and on scope exit
        the blocking ``wait_for_completion`` loop calls ``.get()`` on
        the fake AsyncResult.

        No broker: ``allow_join_result`` is patched to a ``nullcontext``
        so the join seam executes without talking to anyone.
        """
        task = self._make_task("compute_nav")
        bound = EnhancedBoundTaskMethod(instance=object(), task=task)

        with patch.dict(os.environ, {"CELERY_ACTIVE": "true"}, clear=False), \
             patch("lex.lex_app.celery_tasks.allow_join_result",
                   return_value=nullcontext()):
            wft = WaitForTasks()
            # WaitForTasks captures CELERY_ACTIVE once at __init__ time; force
            # the flag on so the tests don't depend on env-var timing.
            wft._active = True
            with wft:
                # __enter__ only pushes when _active is True at enter-time.
                if wft not in tasks_context.get()["task_context_stack"]:
                    tasks_context.get()["task_context_stack"].append(wft)

                fake_result = bound("payload")
                self.assertIn(fake_result, wft.dispatched_results)

        task.delay.assert_called_once()
        self.assertEqual(task.call_count, 0, "Sync body must NOT have executed")
        fake_result.get.assert_called_once_with()
        self.assertEqual(
            wft.dispatched_results, [],
            "dispatched_results must be cleared after blocking join",
        )


class TestCluster08g_LexSharedTaskWrapper(E2ETestCase):
    """``lex_shared_task`` decorator — pop ``context`` / ``model_context``
    from kwargs before calling the inner function, and only enter
    :class:`CeleryCalculationContext` when at least one of them is set.
    """

    e2e_models = ALL_MODELS

    # -- 8.15 ----------------------------------------------------------
    def test_8_15_wrapper_pops_reserved_kwargs_and_enters_context(self) -> None:
        """Scenario 8.15: the wrapper strips ``context`` + ``model_context``
        from the kwargs that reach the inner function, enters
        :class:`CeleryCalculationContext` when context is truthy, and
        returns ``(inner_result, args)`` as documented.

        This is the shape ``CallbackTask._extract_model_instances``
        depends on — a regression in the wrapper's return tuple would
        silently stop callbacks from seeing their model instances.
        """
        recorded = {}

        @lex_shared_task
        def my_calc(instance, *, flag):
            # Should NOT see context/model_context here — the wrapper pops them.
            from lex.api.utils import operation_context
            recorded["kwargs_seen"] = {"flag": flag}
            recorded["op_context"] = dict(operation_context.get())
            return f"done-{flag}"

        # The decorator returns an ``EnhancedTaskMethodDescriptor``;
        # calling it as a plain function goes through
        # ``EnhancedTaskMethodDescriptor.__call__``, which — with
        # CELERY_ACTIVE=False — runs the task body synchronously and
        # returns whatever the underlying Celery task returns.  The
        # Celery task *is* our wrapper, so we get (result, args) back.
        incoming_ctx = {"calculation_id": "calc-8-15", "request_obj": {}}

        # CELERY_ACTIVE is False by default in E2ETestCase.
        # NOTE: the wrapper only pops ``model_context`` when it is TRUTHY
        # (``if model_context: kwargs.pop(...)``).  Passing ``model_context=None``
        # would leak the kwarg through to ``my_calc`` and crash with
        # ``unexpected keyword argument``.  Omit it entirely to exercise the
        # context-only branch.
        out = my_calc(
            "fake-instance",
            flag=True,
            context=incoming_ctx,
        )

        # lex_shared_task wrapper returns (inner_result, args).
        self.assertIsInstance(out, tuple)
        result, args = out
        self.assertEqual(result, "done-True")
        self.assertEqual(args, ("fake-instance",))

        # Reserved kwargs must not have leaked through to the body.
        self.assertEqual(recorded["kwargs_seen"], {"flag": True})

        # Inside the body the CeleryCalculationContext had stamped the
        # incoming calculation_id onto operation_context.
        self.assertEqual(recorded["op_context"].get("calculation_id"), "calc-8-15")
        self.assertTrue(recorded["op_context"].get("celery_task"))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()



