"""
Cluster 8i: ``WaitForTasks`` / ``FireAndForget`` scope contracts.

Intent (from ``docs/lex_topics/12-celery-async-dispatch.md``):

    ``WaitForTasks`` / ``FireAndForget`` (and their aliases
    ``RunInCelery`` / ``UnblockCelery``) are the **customer-facing**
    dispatch primitives for opting into async execution. The docs
    define a handful of hard contracts every deployment depends on:

    * **Priority** — ``FireAndForget`` > ``WaitForTasks`` > sync.
    * **Nesting** — ``FireAndForget`` inside ``WaitForTasks`` forces
      dispatch for its scope **without** letting the outer WFT wait
      on it; each nested ``WaitForTasks`` tracks its own dispatched
      results independently.
    * **Filters** — ``WaitForTasks(include_tasks=…)`` /
      ``WaitForTasks(exclude_tasks=…)`` /
      ``FireAndForget(force_tasks=…)`` / ``FireAndForget(exclude_tasks=…)``
      let customers carve out specific tasks from whichever scope is
      active.
    * **No-op** — both context managers are pure pass-throughs when
      ``CELERY_ACTIVE`` is not set.
    * **Exception propagation** — an exception raised by a
      WFT-dispatched task must surface on scope exit (the whole point
      of "wait for tasks" — the caller must know if a child failed).

Sub-clusters 8g (unit, mocked ``.delay``) and 8h (end-to-end, eager
mode) only covered the happy-path priority case. This sub-cluster
closes every other branch — filters, nesting, no-op, exception
propagation — using a mix of:

* Eager Celery mode (broker-free, real task execution) for anything
  that wants to see ``is_calculated`` actually flip.
* ContextVar inspection for the pure routing contracts (filter
  mechanics, no-op pass-through) because those are decided before any
  ``.delay`` call.

Scenario numbering extends sub-cluster 8h — 8i picks up at **8.22**.
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
    tasks_context,
    unblock_tasks_context,
)

from lex.tests.e2e._e2e_test_case import E2ETestCase

from .models import ALL_MODELS, CeleryCalc


# ---------------------------------------------------------------------
# Shared fixture — eager Celery + clean ContextVars
# ---------------------------------------------------------------------
@contextmanager
def _celery_eager(propagate: bool = True):
    prior = (
        celery_app.conf.task_always_eager,
        celery_app.conf.task_eager_propagates,
        os.environ.get("CELERY_ACTIVE"),
    )
    celery_app.conf.task_always_eager = True
    celery_app.conf.task_eager_propagates = propagate
    os.environ["CELERY_ACTIVE"] = "true"
    try:
        yield
    finally:
        celery_app.conf.task_always_eager = prior[0]
        celery_app.conf.task_eager_propagates = prior[1]
        if prior[2] is None:
            os.environ.pop("CELERY_ACTIVE", None)
        else:
            os.environ["CELERY_ACTIVE"] = prior[2]


def _reset_ctx():
    tasks_context.set({"task_context_stack": []})
    unblock_tasks_context.set({"unblock_context_stack": []})


def _fake_task(name: str = "compute"):
    """Minimal fake task object for routing-only tests (no eager needed)."""
    t = mock.MagicMock()
    t.__name__ = name
    t.name = name
    t.return_value = f"sync:{name}"
    t.delay = mock.MagicMock(
        return_value=mock.MagicMock(name=f"AsyncResult<{name}>")
    )
    return t


# ---------------------------------------------------------------------
# 8.22 — FireAndForget nested inside WaitForTasks (real eager)
# ---------------------------------------------------------------------
class TestCluster08i_FFInsideWFT(E2ETestCase):
    """The headline nesting contract from the docs:

        with WaitForTasks():
            compute_nav.delay(q1)           # parent WILL wait
            with FireAndForget():
                send_report_email.delay()   # parent WON'T wait on this
            compute_nav.delay(q2)           # parent WILL wait
        # ← blocks until q1 and q2 finish; email may still be in flight

    In eager mode everything runs inline anyway, so the practical
    difference is **where the AsyncResult lands** — outer WFT must
    carry q1+q2, inner FF must carry the email, and at no point does
    any result appear in both."""

    e2e_models = ALL_MODELS

    def setUp(self):
        super().setUp()
        _reset_ctx()
        self.addCleanup(_reset_ctx)

    # -- 8.22 ----------------------------------------------------------
    def test_8_22_ff_inside_wft_partitions_results(self) -> None:
        """Scenario 8.22: FF nested inside WFT partitions results —
        "parent" tasks land on the outer WFT, "fire-and-forget" tasks
        land on the inner FF, and the outer WFT's ``dispatched_results``
        never contains the FF result.
        """
        parent_a = CeleryCalc.objects.create(
            name="s8-22-parent-a", is_calculated=CalculationModel.NOT_CALCULATED,
        )
        parent_b = CeleryCalc.objects.create(
            name="s8-22-parent-b", is_calculated=CalculationModel.NOT_CALCULATED,
        )
        ff_only = CeleryCalc.objects.create(
            name="s8-22-ff", is_calculated=CalculationModel.NOT_CALCULATED,
        )

        for row in (parent_a, parent_b, ff_only):
            CeleryCalc.objects.filter(pk=row.pk).update(
                is_calculated=CalculationModel.IN_PROGRESS
            )

        with _celery_eager():
            wft = WaitForTasks()
            wft._active = True
            with wft:
                if wft not in tasks_context.get()["task_context_stack"]:
                    tasks_context.get()["task_context_stack"].append(wft)

                # Parent task A — should register on outer WFT.
                bound_a = EnhancedBoundTaskMethod(parent_a, type(parent_a).calculate.task)
                r_a = bound_a()
                self.assertIn(r_a, wft.dispatched_results)

                ff = FireAndForget()
                ff._active = True
                with ff:
                    if ff not in unblock_tasks_context.get()["unblock_context_stack"]:
                        unblock_tasks_context.get()["unblock_context_stack"].append(ff)

                    # FF dispatch — should land on ff, NOT wft.
                    bound_ff = EnhancedBoundTaskMethod(ff_only, type(ff_only).calculate.task)
                    r_ff = bound_ff()
                    self.assertIn(
                        r_ff, ff.dispatched_results,
                        "FF must capture the result — it is the scope "
                        "customers rely on for 'don't block on this'",
                    )
                    self.assertNotIn(
                        r_ff, wft.dispatched_results,
                        "Outer WFT must NOT capture an FF result — else "
                        "the outer wait would block on a task the "
                        "customer explicitly opted out of",
                    )

                # Parent task B — should register on outer WFT.
                bound_b = EnhancedBoundTaskMethod(parent_b, type(parent_b).calculate.task)
                r_b = bound_b()
                self.assertIn(r_b, wft.dispatched_results)

        # All three rows executed; outer WFT drained its two on exit.
        for row in (parent_a, parent_b, ff_only):
            fresh = CeleryCalc.objects.get(pk=row.pk)
            self.assertEqual(
                fresh.is_calculated, CalculationModel.SUCCESS,
                f"{row.name!r} must be SUCCESS after real task body ran",
            )


# ---------------------------------------------------------------------
# 8.23 — Nested WaitForTasks scopes
# ---------------------------------------------------------------------
class TestCluster08i_NestedWFT(E2ETestCase):
    """Nested WFT scopes — each tracks its own tasks independently.

    From the docs:

        with WaitForTasks():                 # outer
            compute_portfolio.delay(a)       # outer
            with WaitForTasks():             # inner
                compute_nav.delay(q1)        # inner
                compute_nav.delay(q2)        # inner
            # ← blocks on q1, q2
            generate_report.delay(a)         # outer
        # ← blocks on a, report
    """

    e2e_models = ALL_MODELS

    def setUp(self):
        super().setUp()
        _reset_ctx()
        self.addCleanup(_reset_ctx)

    # -- 8.23 ----------------------------------------------------------
    def test_8_23_inner_exit_drains_only_its_own_tasks(self) -> None:
        """Scenario 8.23: inner WFT exit drains the inner result set;
        the outer WFT's ``dispatched_results`` is untouched until the
        outer block exits.

        This is what lets customers compose a coarse-grained outer
        "wait for everything related to fund A" around a finer-grained
        inner "wait for just these two quarter NAVs" without the two
        interfering.
        """
        outer_row = CeleryCalc.objects.create(
            name="s8-23-outer", is_calculated=CalculationModel.IN_PROGRESS,
        )
        inner_row = CeleryCalc.objects.create(
            name="s8-23-inner", is_calculated=CalculationModel.IN_PROGRESS,
        )

        with _celery_eager():
            outer = WaitForTasks()
            outer._active = True
            with outer:
                if outer not in tasks_context.get()["task_context_stack"]:
                    tasks_context.get()["task_context_stack"].append(outer)

                bound_out = EnhancedBoundTaskMethod(
                    outer_row, type(outer_row).calculate.task,
                )
                outer_result = bound_out()
                self.assertIn(outer_result, outer.dispatched_results)

                inner = WaitForTasks()
                inner._active = True
                with inner:
                    if inner not in tasks_context.get()["task_context_stack"]:
                        tasks_context.get()["task_context_stack"].append(inner)

                    bound_in = EnhancedBoundTaskMethod(
                        inner_row, type(inner_row).calculate.task,
                    )
                    inner_result = bound_in()

                    self.assertIn(
                        inner_result, inner.dispatched_results,
                        "Inner WFT must capture its own dispatch",
                    )
                    self.assertNotIn(
                        inner_result, outer.dispatched_results,
                        "Outer WFT must NOT see a result dispatched "
                        "inside an inner scope",
                    )
                    self.assertIn(
                        outer_result, outer.dispatched_results,
                        "Outer's result stays on the outer scope — "
                        "opening an inner scope does not steal it",
                    )

                # Inner exit drained its own list.
                self.assertEqual(
                    inner.dispatched_results, [],
                    "Inner WFT must clear its dispatched_results on exit",
                )
                # Outer still carries its result until the outer block exits.
                self.assertIn(outer_result, outer.dispatched_results)


# ---------------------------------------------------------------------
# 8.24 / 8.25 — WaitForTasks include/exclude filters (routing-only)
# ---------------------------------------------------------------------
class TestCluster08i_WFTFilters(unittest.TestCase):
    """``include_tasks`` / ``exclude_tasks`` on WaitForTasks decide,
    per task name, whether the router dispatches or runs sync.

    These contracts don't need the Celery broker at all — they're
    pure decisions made by ``EnhancedBoundTaskMethod.__call__`` based
    on what ``WaitForTasks.should_dispatch(name)`` returns. We use
    fake tasks + mocked ``.delay`` so we can assert on which path was
    taken without persisting anything."""

    def setUp(self):
        _reset_ctx()
        self.addCleanup(_reset_ctx)
        self._env_patch = mock.patch.dict(
            os.environ, {"CELERY_ACTIVE": "true"}, clear=False,
        )
        self._env_patch.start()
        self.addCleanup(self._env_patch.stop)

    def _activate_wft(self, include_tasks=None, exclude_tasks=None):
        wft = WaitForTasks(include_tasks=include_tasks, exclude_tasks=exclude_tasks)
        wft._active = True
        tasks_context.get()["task_context_stack"].append(wft)
        return wft

    # -- 8.24 ----------------------------------------------------------
    def test_8_24_include_tasks_dispatches_only_matches(self) -> None:
        """Scenario 8.24: ``WaitForTasks(include_tasks={"a"})`` —
        only task ``a`` gets ``.delay``'d; everything else runs sync.

        Customer use case: a selective async block that only opts
        one known task out of sync execution, leaving the rest
        unchanged. Misfiring this would mean either over-dispatching
        (customer's "keep this sync" task goes through Celery) or
        under-dispatching (expected async task quietly runs sync).
        """
        task_a = _fake_task("compute_a")
        task_b = _fake_task("compute_b")

        wft = self._activate_wft(include_tasks={"compute_a"})

        bound_a = EnhancedBoundTaskMethod(instance="obj-a", task=task_a)
        bound_b = EnhancedBoundTaskMethod(instance="obj-b", task=task_b)

        result_a = bound_a("x")
        result_b = bound_b("y")

        task_a.delay.assert_called_once_with("obj-a", "x")
        self.assertEqual(task_a.call_count, 0, "compute_a must NOT run sync")
        self.assertIn(result_a, wft.dispatched_results)

        task_b.delay.assert_not_called()
        task_b.assert_called_once_with("obj-b", "y")
        self.assertEqual(
            result_b, "sync:compute_b",
            "compute_b must have returned the sync body's value",
        )

    # -- 8.25 ----------------------------------------------------------
    def test_8_25_exclude_tasks_keeps_excluded_sync(self) -> None:
        """Scenario 8.25: ``WaitForTasks(exclude_tasks={"b"})`` —
        ``b`` stays sync; every other task inside the scope dispatches.

        Customer use case: a blanket async block with a carve-out for
        one task that is known to be cheap or has side-effects that
        break when run async (e.g. audit-log replay).
        """
        task_a = _fake_task("compute_a")
        task_b = _fake_task("compute_b")

        wft = self._activate_wft(exclude_tasks={"compute_b"})

        bound_a = EnhancedBoundTaskMethod(instance="obj-a", task=task_a)
        bound_b = EnhancedBoundTaskMethod(instance="obj-b", task=task_b)

        bound_a()
        bound_b()

        task_a.delay.assert_called_once()
        task_b.delay.assert_not_called()
        task_b.assert_called_once_with("obj-b")


# ---------------------------------------------------------------------
# 8.26 — FireAndForget force_tasks filter (routing-only)
# ---------------------------------------------------------------------
class TestCluster08i_FFFilters(unittest.TestCase):
    """``FireAndForget(force_tasks={…})`` lets a caller restrict the
    force-dispatch to a specific subset — everything outside the set
    falls through to whatever scope rule is active (WFT or sync)."""

    def setUp(self):
        _reset_ctx()
        self.addCleanup(_reset_ctx)
        self._env_patch = mock.patch.dict(
            os.environ, {"CELERY_ACTIVE": "true"}, clear=False,
        )
        self._env_patch.start()
        self.addCleanup(self._env_patch.stop)

    # -- 8.26 ----------------------------------------------------------
    def test_8_26_force_tasks_only_forces_named_subset(self) -> None:
        """Scenario 8.26: ``FireAndForget(force_tasks={"email"})``
        inside a ``WaitForTasks`` — only ``email`` is force-dispatched
        on the FF scope; other tasks inside the block follow the WFT
        rules (dispatched + tracked by WFT).

        Customer use case: "I want to ignore email latency but still
        block on everything else my batch job dispatches."
        """
        email = _fake_task("email")
        nav = _fake_task("compute_nav")

        wft = WaitForTasks()
        wft._active = True
        tasks_context.get()["task_context_stack"].append(wft)

        ff = FireAndForget(force_tasks={"email"})
        ff._active = True
        unblock_tasks_context.get()["unblock_context_stack"].append(ff)

        email_bound = EnhancedBoundTaskMethod(instance="e", task=email)
        nav_bound = EnhancedBoundTaskMethod(instance="n", task=nav)

        email_result = email_bound()
        nav_result = nav_bound()

        # email forced onto FF scope.
        self.assertIn(email_result, ff.dispatched_results)
        self.assertNotIn(email_result, wft.dispatched_results)

        # nav fell through to WFT rules.
        self.assertIn(
            nav_result, wft.dispatched_results,
            "A task outside force_tasks must fall through to the "
            "enclosing WaitForTasks scope, not get forced or go sync",
        )
        self.assertNotIn(nav_result, ff.dispatched_results)


# ---------------------------------------------------------------------
# 8.27 — No-op when CELERY_ACTIVE is not set
# ---------------------------------------------------------------------
class TestCluster08i_NoOpWhenCeleryInactive(unittest.TestCase):
    """Both context managers are **pure pass-through** when
    ``CELERY_ACTIVE`` is not set. Entering or nesting them must not
    mutate the ContextVars, must not call ``.delay``, and must still
    run task bodies synchronously.

    This is what the docs promise every customer who hasn't turned
    Celery on: "your code doesn't change" — they can leave a
    ``WaitForTasks`` block around their calculation and it simply
    becomes a no-op, with zero behavioural difference from removing
    it."""

    def setUp(self):
        _reset_ctx()
        self.addCleanup(_reset_ctx)
        # Explicitly NOT setting CELERY_ACTIVE.
        self._env_patch = mock.patch.dict(
            os.environ, {}, clear=False,
        )
        # Ensure it is absent.
        self._env_patch.start()
        self.addCleanup(self._env_patch.stop)
        self._prior_active = os.environ.pop("CELERY_ACTIVE", None)
        if self._prior_active is not None:
            self.addCleanup(
                lambda: os.environ.__setitem__("CELERY_ACTIVE", self._prior_active),
            )

    # -- 8.27 ----------------------------------------------------------
    def test_8_27_wft_and_ff_are_pure_pass_through(self) -> None:
        """Scenario 8.27: with ``CELERY_ACTIVE`` unset, entering WFT
        or FF does **not** push onto the ContextVar stacks and the
        router runs the task body synchronously.

        Checked state:
        * ``tasks_context`` stack stays empty through the WFT block.
        * ``unblock_tasks_context`` stack stays empty through the FF block.
        * ``.delay`` is not called.
        * Task body returns its sync result to the caller.
        """
        task = _fake_task("some_task")

        with WaitForTasks():
            self.assertEqual(
                tasks_context.get()["task_context_stack"], [],
                "WFT enter must be a no-op when CELERY_ACTIVE is unset",
            )
            with FireAndForget():
                self.assertEqual(
                    unblock_tasks_context.get()["unblock_context_stack"], [],
                    "FF enter must be a no-op when CELERY_ACTIVE is unset",
                )

                bound = EnhancedBoundTaskMethod(instance="o", task=task)
                result = bound(42)

                self.assertEqual(result, "sync:some_task")
                task.delay.assert_not_called()
                task.assert_called_once_with("o", 42)


# ---------------------------------------------------------------------
# 8.28 — Exception propagation through WaitForTasks exit
# ---------------------------------------------------------------------
class TestCluster08i_ExceptionPropagation(E2ETestCase):
    """When a WFT-dispatched task fails, the caller must learn about
    it. In production this happens via ``AsyncResult.get()`` re-raising
    from inside ``WaitForTasks.wait_for_completion``. In eager mode
    the task has already failed by the time ``.delay`` returns, but
    the ``.get()`` call at scope exit must still surface the exception
    — that is the contract customers depend on."""

    e2e_models = ALL_MODELS

    def setUp(self):
        super().setUp()
        _reset_ctx()
        self.addCleanup(_reset_ctx)

    # -- 8.28 ----------------------------------------------------------
    def test_8_28_failing_task_surfaces_on_wft_exit(self) -> None:
        """Scenario 8.28: a task that raises inside a WFT scope causes
        ``wait_for_completion`` to re-raise on scope exit. The caller
        sees the exception and can route it through their own
        error-handling — silent failure in a WFT block would be a
        data-loss bug.
        """
        # Create with NOT_CALCULATED so ``AFTER_CREATE`` hook doesn't
        # fire the calc synchronously outside our eager scope. We
        # dispatch the failing task explicitly below via the bound
        # method — that is the code path under test.
        failing = CeleryCalc(name="s8-28", should_fail=True)
        failing.save()

        with _celery_eager(propagate=False):
            wft = WaitForTasks()
            wft._active = True
            with self.assertRaises(RuntimeError) as ctx:
                with wft:
                    if wft not in tasks_context.get()["task_context_stack"]:
                        tasks_context.get()["task_context_stack"].append(wft)

                    bound = EnhancedBoundTaskMethod(failing, type(failing).calculate.task)
                    result = bound()
                    self.assertIn(result, wft.dispatched_results)
                    # Scope exit will call result.get() → re-raises.

            self.assertIn(
                "failing on purpose", str(ctx.exception),
                "The original exception must surface verbatim; the "
                "caller uses its message to diagnose the failure",
            )


# ---------------------------------------------------------------------
# 8.29 — Multiple dispatches within a single WFT block
# ---------------------------------------------------------------------
class TestCluster08i_MultipleDispatchesInWFT(E2ETestCase):
    """A WFT block typically dispatches more than one task. Every
    dispatched result must be drained on exit; no row may be left in
    IN_PROGRESS because the caller skipped a wait."""

    e2e_models = ALL_MODELS

    def setUp(self):
        super().setUp()
        _reset_ctx()
        self.addCleanup(_reset_ctx)

    # -- 8.29 ----------------------------------------------------------
    def test_8_29_every_dispatch_is_drained(self) -> None:
        """Scenario 8.29: three dispatches in one WFT block → three
        successful calcs after exit; ``dispatched_results`` is
        cleared.
        """
        rows = [
            CeleryCalc.objects.create(
                name=f"s8-29-{i}", is_calculated=CalculationModel.IN_PROGRESS,
            )
            for i in range(3)
        ]

        with _celery_eager():
            wft = WaitForTasks()
            wft._active = True
            with wft:
                if wft not in tasks_context.get()["task_context_stack"]:
                    tasks_context.get()["task_context_stack"].append(wft)

                for r in rows:
                    bound = EnhancedBoundTaskMethod(r, type(r).calculate.task)
                    bound()

                self.assertEqual(
                    len(wft.dispatched_results), 3,
                    "All three dispatches must register on the scope",
                )

        self.assertEqual(
            wft.dispatched_results, [],
            "wait_for_completion must clear every dispatched result",
        )
        for r in rows:
            fresh = CeleryCalc.objects.get(pk=r.pk)
            self.assertEqual(
                fresh.is_calculated, CalculationModel.SUCCESS,
                f"{r.name!r} must be SUCCESS — WFT blocked until it finished",
            )


# ---------------------------------------------------------------------
# 8.30 — FireAndForget exit does NOT drain by itself
# ---------------------------------------------------------------------
class TestCluster08i_FFDoesNotBlockOnExit(unittest.TestCase):
    """The whole point of ``FireAndForget`` is that its exit is
    non-blocking — ``dispatched_results`` lingers after the ``with``
    block ends. Only explicit ``wait_for_completion`` drains it.

    Asserting this with ContextVar inspection + a MagicMock ``delay``
    keeps the test deterministic: eager mode would short-circuit the
    "dispatch returned but task still pending" distinction, and the
    contract we care about here is the router behaviour, not the
    task body."""

    def setUp(self):
        _reset_ctx()
        self.addCleanup(_reset_ctx)
        self._env = mock.patch.dict(os.environ, {"CELERY_ACTIVE": "true"}, clear=False)
        self._env.start()
        self.addCleanup(self._env.stop)

    # -- 8.30 ----------------------------------------------------------
    def test_8_30_ff_exit_does_not_drain_dispatched_results(self) -> None:
        """Scenario 8.30: ``dispatched_results`` survives FF ``__exit__``
        and is only drained if the caller explicitly invokes
        ``wait_for_completion``. This is the entire behavioural
        difference between FF and WFT.
        """
        task = _fake_task("email_report")

        ff = FireAndForget()
        ff._active = True
        # __enter__ pushes onto the stack when _active is True.
        with ff:
            unblock_tasks_context.get()["unblock_context_stack"].append(ff)
            bound = EnhancedBoundTaskMethod(instance="o", task=task)
            bound()

            self.assertEqual(
                len(ff.dispatched_results), 1,
                "FF captured the dispatched result",
            )

        # After exit the result MUST still be on the FF instance —
        # FF's whole promise is "don't wait on these".
        self.assertEqual(
            len(ff.dispatched_results), 1,
            "FF exit must NOT drain dispatched_results — if it did, "
            "the caller would wait on the task anyway and FF would "
            "be indistinguishable from WFT",
        )

        # Explicit drain: FF.wait_for_completion calls .get() on each
        # stored result.
        stored_result = ff.dispatched_results[0]
        ff.wait_for_completion()

        stored_result.get.assert_called_once_with()
        self.assertEqual(
            ff.dispatched_results, [],
            "wait_for_completion must clear the results after explicit drain",
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()


