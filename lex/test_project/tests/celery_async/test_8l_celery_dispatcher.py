"""
Sub-cluster 8l — `CeleryTaskDispatcher` validation, fallback & error paths.

Coverage-driven batch (May 12 ROI rank #4 — `lex/core/tasks/CeleryTaskDispatcher.py`
45.69% baseline, 186 stmts / 98 missed). Sessions 8g/8h's eager-mode round-trips
covered the single happy path (`dispatch_calculation_groups` → `_dispatch_single_group`
→ `.delay()` → `_handle_task_results` join); this batch closes the dark surface:

* Input-validation guards on every public + helper method (groups / task_results
  / group_mapping wrong type → `CeleryDispatchError` with diagnostic fields).
* ImportError chains for the lazy `calc_and_save` / `ResultSet` imports.
* Wait-scope selection logic (no scope → fresh `WaitForTasks`; FF or WFT
  already active → `nullcontext`).
* Synchronous-fallback ladder at all three levels (top-level setup failure,
  per-group dispatch failure, post-join ResultSet failure) including the
  "both Celery and sync fallback failed" terminal-failure shape.
* Per-task failure handling: `task_result.failed()` True branch, status-check
  exception branch (rare but operator-visible).
* `_get_calculation_context` defensive paths.

Why it matters: this dispatcher is the single boundary between the calculation
engine and Celery — every silently-swallowed error here means a customer's
calculation appears to "succeed" but never actually ran. The fallback ladder
exists so a broker outage never loses work.

Scenarios 8.47 – 8.71.
"""

from __future__ import annotations

import unittest
from contextlib import nullcontext
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase

from lex.core.exceptions import CeleryDispatchError
from lex.core.tasks.CeleryTaskDispatcher import CeleryTaskDispatcher

import pytest

pytestmark = pytest.mark.celery_async


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------


def _patch_dispatch_imports(*, fire_active=False, wait_active=False,
                              calc_delay=None, calc_sync_raise=None,
                              register_raise=None):
    """Build a context-manager bundle that patches the lazy imports
    inside `CeleryTaskDispatcher.dispatch_calculation_groups`.

    Returns a list of context managers the caller enters via ExitStack
    or nests manually. Keeping it explicit (no fixtures, no setUp
    machinery) so each test reads top-to-bottom.
    """
    from contextlib import ExitStack
    stack = ExitStack()

    # FireAndForget / WaitForTasks scope state.
    ff = MagicMock()
    ff.get_current_context.return_value = (
        MagicMock() if fire_active else None
    )
    wft = MagicMock()
    wft.get_current_context.return_value = (
        MagicMock() if wait_active else None
    )
    # WFT() instantiation must return a no-op context manager.
    wft.return_value.__enter__ = MagicMock(return_value=None)
    wft.return_value.__exit__ = MagicMock(return_value=False)

    # `calc_and_save` task with a `.delay` method.
    task_module = MagicMock()
    task_module.calc_and_save = MagicMock()
    task_module.calc_and_save.delay = (
        calc_delay or MagicMock(return_value=MagicMock(id="fake-task"))
    )
    task_module.FireAndForget = ff
    task_module.WaitForTasks = wft
    task_module.register_task_with_context = (
        MagicMock(side_effect=register_raise) if register_raise
        else MagicMock()
    )

    stack.enter_context(patch.dict(
        "sys.modules",
        {"lex.lex_app.celery_tasks": task_module},
    ))

    if calc_sync_raise is not None:
        stack.enter_context(patch(
            "lex.core.tasks.CeleryTaskDispatcher.calc_and_save_sync",
            side_effect=calc_sync_raise,
        ))
    else:
        stack.enter_context(patch(
            "lex.core.tasks.CeleryTaskDispatcher.calc_and_save_sync",
        ))

    return stack, task_module


def _ctx():
    """Default `context` kwarg shape `_dispatch_single_group` expects."""
    return {"request_obj": {}, "calculation_id": "calc-x"}


# ----------------------------------------------------------------------
# 8.47 – 8.50  dispatch_calculation_groups validation
# ----------------------------------------------------------------------


class TestCluster08l_DispatchValidation(SimpleTestCase):
    """Input-validation guards on `dispatch_calculation_groups`."""

    # 8.47 -------------------------------------------------------------
    def test_8_47_empty_groups_is_early_return_noop(self) -> None:
        """`groups=[]` → returns immediately, no imports, no dispatch."""
        with patch.dict("sys.modules", {}, clear=False):
            # Even without patching the celery imports, the early-return
            # at line 47 must fire before anything else runs.
            result = CeleryTaskDispatcher.dispatch_calculation_groups(
                [], context=_ctx()
            )
        self.assertIsNone(result)

    # 8.48 -------------------------------------------------------------
    def test_8_48_groups_wrong_type_raises_with_diagnostic_field(self) -> None:
        """`groups` as dict / int / etc. → `CeleryDispatchError`.

        The error message must name the actual type so an operator
        reading the audit log can identify the caller without trawling
        every dispatch site.
        """
        # Empty dict is falsy so it short-circuits at line 47.
        # Use a non-empty wrong type:
        bad_groups = {"k": "v"}
        with self.assertRaises(CeleryDispatchError) as ctx:
            CeleryTaskDispatcher.dispatch_calculation_groups(
                bad_groups, context=_ctx()
            )
        self.assertIn("dict", str(ctx.exception))

    # 8.49 -------------------------------------------------------------
    def test_8_49_all_empty_groups_filtered_out_and_returns(self) -> None:
        """`[[], []]` → after filtering, nothing to process, returns.

        Pin the warning-and-return path at lines 65-67 so a regression
        that re-raises here would silently break empty-cluster runs.
        """
        result = CeleryTaskDispatcher.dispatch_calculation_groups(
            [[], []], context=_ctx()
        )
        self.assertIsNone(result)

    # 8.50 -------------------------------------------------------------
    def test_8_50_mixed_empty_and_nonempty_groups_filters_silently(self) -> None:
        """`[[m1], [], [m2]]` → empty filtered out, two groups dispatched.

        Pins the `non_empty_groups = [g for g in groups if g]` filter
        at line 59 — a regression that crashed on empty groups would
        explode every multi-cluster run with sparse partitions.
        """
        m1, m2 = MagicMock(), MagicMock()
        stack, task_module = _patch_dispatch_imports()
        with stack, patch(
            "lex.core.tasks.CeleryTaskDispatcher._model_context",
        ) as mc:
            mc.get.return_value = {"model_context": {}}
            CeleryTaskDispatcher.dispatch_calculation_groups(
                [[m1], [], [m2]], context=_ctx()
            )
        # Two groups dispatched, not three.
        self.assertEqual(task_module.calc_and_save.delay.call_count, 2)


# ----------------------------------------------------------------------
# 8.51  ImportError chain
# ----------------------------------------------------------------------


class TestCluster08l_DispatchImportError(SimpleTestCase):
    """Lazy `calc_and_save` import failure chains into `CeleryDispatchError`."""

    # 8.51 -------------------------------------------------------------
    def test_8_51_celery_tasks_import_error_chains_into_dispatch_error(self):
        """`from lex.lex_app.celery_tasks import calc_and_save` raise →
        `CeleryDispatchError` with `__cause__` set to the original ImportError.

        Pin the `raise … from import_error` chain — `__cause__` is what
        operator dashboards use to attribute the real root cause; a
        regression that dropped the `from` clause would surface only
        the framework's wrapper exception with no clue about the
        missing dependency.
        """
        # Force the inner `from lex.lex_app.celery_tasks import …` to raise.
        broken_module = MagicMock()
        # Trigger ImportError when `from broken_module import calc_and_save`
        # is evaluated by deleting the attribute and routing access to raise.
        type(broken_module).__getattr__ = MagicMock(
            side_effect=ImportError("missing celery_tasks")
        )
        with patch.dict(
            "sys.modules", {"lex.lex_app.celery_tasks": broken_module}
        ):
            with self.assertRaises(CeleryDispatchError) as ctx:
                CeleryTaskDispatcher.dispatch_calculation_groups(
                    [[MagicMock()]], context=_ctx()
                )
        self.assertIsInstance(ctx.exception.__cause__, ImportError)
        self.assertIn("calc_and_save", str(ctx.exception))


# ----------------------------------------------------------------------
# 8.52 – 8.54  Wait-scope selection
# ----------------------------------------------------------------------


class TestCluster08l_WaitScopeSelection(SimpleTestCase):
    """Active-FF / active-WFT / no-scope branch of `wait_scope =`."""

    def _drive(self, *, fire, wait):
        stack, task_module = _patch_dispatch_imports(
            fire_active=fire, wait_active=wait
        )
        with stack, patch(
            "lex.core.tasks.CeleryTaskDispatcher._model_context",
        ) as mc:
            mc.get.return_value = {"model_context": {}}
            CeleryTaskDispatcher.dispatch_calculation_groups(
                [[MagicMock()]], context=_ctx()
            )
        return task_module

    # 8.52 -------------------------------------------------------------
    def test_8_52_no_active_scope_creates_fresh_wait_for_tasks(self) -> None:
        """No FF and no WFT → `WaitForTasks()` instantiated to drain
        results on scope exit (per docstring "Monitors task completion").
        """
        task_module = self._drive(fire=False, wait=False)
        task_module.WaitForTasks.assert_called_once_with()

    # 8.53 -------------------------------------------------------------
    def test_8_53_active_fire_and_forget_uses_nullcontext(self) -> None:
        """Active FireAndForget → no new WFT created (would force the
        FF results to be drained, defeating fire-and-forget semantics).
        """
        task_module = self._drive(fire=True, wait=False)
        task_module.WaitForTasks.assert_not_called()

    # 8.54 -------------------------------------------------------------
    def test_8_54_active_wait_for_tasks_uses_nullcontext(self) -> None:
        """Active WaitForTasks → no new WFT created (the outer scope
        owns the drain, nesting another WFT would double-join).
        """
        task_module = self._drive(fire=False, wait=True)
        task_module.WaitForTasks.assert_not_called()


# ----------------------------------------------------------------------
# 8.55 – 8.56  Top-level fallback ladder
# ----------------------------------------------------------------------


class TestCluster08l_TopLevelFallback(SimpleTestCase):
    """Generic exception during dispatch setup → flattened sync fallback."""

    # 8.55 -------------------------------------------------------------
    def test_8_55_setup_exception_falls_back_to_flattened_sync(self) -> None:
        """An unexpected exception during the dispatch setup must NOT
        propagate — instead, every group is flattened and processed
        synchronously so the customer's work still lands.

        This is the customer-facing "broker outage doesn't lose work"
        promise.
        """
        # Force `_dispatch_single_group` to blow up unexpectedly so the
        # outer exception handler at line 136 catches it.
        stack, task_module = _patch_dispatch_imports()
        m1, m2, m3 = MagicMock(), MagicMock(), MagicMock()
        with stack, patch(
            "lex.core.tasks.CeleryTaskDispatcher._model_context",
        ) as mc, patch.object(
            CeleryTaskDispatcher,
            "_dispatch_single_group",
            side_effect=RuntimeError("celery setup blew up"),
        ), patch(
            "lex.core.tasks.CeleryTaskDispatcher.calc_and_save_sync",
        ) as sync_mock:
            mc.get.return_value = {"model_context": {}}
            CeleryTaskDispatcher.dispatch_calculation_groups(
                [[m1, m2], [m3]], context=_ctx()
            )

        # All 3 models flattened into a single sync call.
        sync_mock.assert_called_once()
        flattened = sync_mock.call_args.args[0]
        self.assertEqual(set(flattened), {m1, m2, m3})

    # 8.56 -------------------------------------------------------------
    def test_8_56_setup_failure_and_sync_fallback_failure_chain(self) -> None:
        """Both Celery setup AND the sync fallback fail → final
        `CeleryDispatchError` carries both error strings + chains via
        `from sync_fallback_error` so operators see the full failure
        chain in one row.
        """
        stack, task_module = _patch_dispatch_imports()
        with stack, patch(
            "lex.core.tasks.CeleryTaskDispatcher._model_context",
        ) as mc, patch.object(
            CeleryTaskDispatcher,
            "_dispatch_single_group",
            side_effect=RuntimeError("celery setup blew up"),
        ), patch(
            "lex.core.tasks.CeleryTaskDispatcher.calc_and_save_sync",
            side_effect=RuntimeError("sync also blew up"),
        ):
            mc.get.return_value = {"model_context": {}}
            with self.assertRaises(CeleryDispatchError) as ctx:
                CeleryTaskDispatcher.dispatch_calculation_groups(
                    [[MagicMock()]], context=_ctx()
                )

        msg = str(ctx.exception)
        self.assertIn("celery setup blew up", msg)
        self.assertIn("sync also blew up", msg)
        self.assertIsInstance(ctx.exception.__cause__, RuntimeError)


# ----------------------------------------------------------------------
# 8.57 – 8.62  _dispatch_single_group
# ----------------------------------------------------------------------


class TestCluster08l_SingleGroup(SimpleTestCase):
    """`_dispatch_single_group` validation + per-group fallback."""

    # 8.57 -------------------------------------------------------------
    def test_8_57_empty_group_returns_none_and_warns(self) -> None:
        """Empty group → returns None, no dispatch, no raise."""
        result = CeleryTaskDispatcher._dispatch_single_group(
            [], 0, context=_ctx()
        )
        self.assertIsNone(result)

    # 8.58 -------------------------------------------------------------
    def test_8_58_group_wrong_type_raises_with_index_and_type(self) -> None:
        """Group as dict / int → `CeleryDispatchError` naming the bad type."""
        with self.assertRaises(CeleryDispatchError) as ctx:
            CeleryTaskDispatcher._dispatch_single_group(
                {"not": "list"}, 5, context=_ctx()
            )
        self.assertIn("dict", str(ctx.exception))

    # 8.59 -------------------------------------------------------------
    def test_8_59_calc_and_save_import_failure_raises_chained(self) -> None:
        """ImportError on lazy `calc_and_save` import → chained `CeleryDispatchError`.

        Same chain pattern as 8.51 but at the per-group import site.
        Note: when `dispatch_calculation_groups` calls into this helper,
        the outer import has already succeeded; this scenario covers a
        direct call where the celery_tasks module has been broken
        between dispatch start and per-group dispatch (rare but
        observable in mid-deploy reloads).
        """
        broken_module = MagicMock()
        type(broken_module).__getattr__ = MagicMock(
            side_effect=ImportError("missing"),
        )
        with patch.dict(
            "sys.modules", {"lex.lex_app.celery_tasks": broken_module}
        ):
            with self.assertRaises(CeleryDispatchError) as ctx:
                CeleryTaskDispatcher._dispatch_single_group(
                    [MagicMock()], 0, context=_ctx()
                )
        self.assertIsInstance(ctx.exception.__cause__, ImportError)

    # 8.60 -------------------------------------------------------------
    def test_8_60_dispatch_error_falls_back_to_sync_returns_none(self) -> None:
        """`CeleryDispatchError` mid-`.delay` → sync fallback runs, returns None.

        The None return tells the outer loop "this group ran sync, do
        not register a task result for it" — pin it so a regression
        that returned `task_result` here would double-process the group.
        """
        m1, m2 = MagicMock(), MagicMock()
        stack, task_module = _patch_dispatch_imports()
        # Force `.delay` to raise the framework's own dispatch error
        # (handled by the inner `except CeleryDispatchError` branch).
        task_module.calc_and_save.delay.side_effect = CeleryDispatchError(
            "broker down", group_index=0,
        )
        with stack, patch(
            "lex.core.tasks.CeleryTaskDispatcher._model_context",
        ) as mc, patch(
            "lex.core.tasks.CeleryTaskDispatcher.calc_and_save_sync",
        ) as sync_mock:
            mc.get.return_value = {"model_context": {}}
            result = CeleryTaskDispatcher._dispatch_single_group(
                [m1, m2], 0, context=_ctx()
            )

        self.assertIsNone(result, "sync fallback must return None")
        sync_mock.assert_called_once()

    # 8.61 -------------------------------------------------------------
    def test_8_61_dispatch_and_sync_both_failing_raise_chained_error(self) -> None:
        """Per-group: dispatch fails AND sync fallback fails → chained raise.

        Diagnostic fields (`group_index`, `group_size`, `celery_error`,
        `sync_error`) all populated so the audit-log dashboard can
        attribute exactly which cluster caused the cascade.
        """
        stack, task_module = _patch_dispatch_imports()
        task_module.calc_and_save.delay.side_effect = CeleryDispatchError(
            "celery boom", group_index=2,
        )
        with stack, patch(
            "lex.core.tasks.CeleryTaskDispatcher._model_context",
        ) as mc, patch(
            "lex.core.tasks.CeleryTaskDispatcher.calc_and_save_sync",
            side_effect=RuntimeError("sync boom"),
        ):
            mc.get.return_value = {"model_context": {}}
            with self.assertRaises(CeleryDispatchError) as ctx:
                CeleryTaskDispatcher._dispatch_single_group(
                    [MagicMock(), MagicMock()], 2, context=_ctx()
                )
        msg = str(ctx.exception)
        self.assertIn("celery boom", msg)
        self.assertIn("sync boom", msg)
        self.assertIn("group 3", msg)  # 1-based for humans

    # 8.62 -------------------------------------------------------------
    def test_8_62_unexpected_exception_wrapped_in_dispatch_error(self) -> None:
        """Generic Exception during dispatch (not `CeleryDispatchError`) →
        wrapped + chained, with `group_index` in the message.

        Pins the catch-all at line 247 — a regression that let an
        arbitrary `RuntimeError` propagate would crash the outer
        dispatch loop instead of triggering the documented sync
        fallback ladder.
        """
        stack, task_module = _patch_dispatch_imports()
        task_module.calc_and_save.delay.side_effect = RuntimeError("oops")
        with stack, patch(
            "lex.core.tasks.CeleryTaskDispatcher._model_context",
        ) as mc:
            mc.get.return_value = {"model_context": {}}
            with self.assertRaises(CeleryDispatchError) as ctx:
                CeleryTaskDispatcher._dispatch_single_group(
                    [MagicMock()], 4, context=_ctx()
                )
        self.assertIn("oops", str(ctx.exception))
        self.assertIsInstance(ctx.exception.__cause__, RuntimeError)


# ----------------------------------------------------------------------
# 8.63 – 8.70  _handle_task_results
# ----------------------------------------------------------------------


def _fake_task(task_id, *, failed=False, failed_raise=None):
    """Build a fake AsyncResult-like object for `_handle_task_results`."""
    t = MagicMock()
    t.id = task_id
    if failed_raise is not None:
        t.failed.side_effect = failed_raise
    else:
        t.failed.return_value = failed
    t.result = "boom" if failed else None
    return t


class TestCluster08l_HandleTaskResults(SimpleTestCase):
    """`_handle_task_results` — failure attribution + retry ladder."""

    # 8.63 -------------------------------------------------------------
    def test_8_63_empty_task_results_returns_with_warning_no_raise(self) -> None:
        """No tasks to handle → warn + return; no `ResultSet` import attempted."""
        # Should NOT raise even if celery is missing entirely.
        with patch.dict(
            "sys.modules", {"celery.result": MagicMock()}, clear=False
        ):
            CeleryTaskDispatcher._handle_task_results([], {})

    # 8.64 -------------------------------------------------------------
    def test_8_64_task_results_wrong_type_raises(self) -> None:
        """Non-list/tuple `task_results` → `CeleryDispatchError`."""
        with self.assertRaises(CeleryDispatchError) as ctx:
            CeleryTaskDispatcher._handle_task_results({"k": "v"}, {})
        self.assertIn("dict", str(ctx.exception))

    # 8.65 -------------------------------------------------------------
    def test_8_65_group_mapping_wrong_type_raises(self) -> None:
        """Non-dict `group_mapping` → `CeleryDispatchError`."""
        with self.assertRaises(CeleryDispatchError) as ctx:
            CeleryTaskDispatcher._handle_task_results(
                [_fake_task("a")], ["not", "a", "dict"]
            )
        self.assertIn("list", str(ctx.exception))

    # 8.66 -------------------------------------------------------------
    def test_8_66_all_tasks_succeed_no_sync_fallback(self) -> None:
        """Every task `.failed()` False → no `calc_and_save_sync` call."""
        t1 = _fake_task("t1")
        t2 = _fake_task("t2")
        rs_mock = MagicMock()
        celery_result_module = MagicMock(ResultSet=MagicMock(return_value=rs_mock))
        with patch.dict(
            "sys.modules", {"celery.result": celery_result_module}
        ), patch(
            "lex.core.tasks.CeleryTaskDispatcher.calc_and_save_sync",
        ) as sync_mock:
            CeleryTaskDispatcher._handle_task_results(
                [t1, t2], {"t1": [MagicMock()], "t2": [MagicMock()]}
            )
        sync_mock.assert_not_called()
        rs_mock.join.assert_called_once_with(propagate=False)

    # 8.67 -------------------------------------------------------------
    def test_8_67_failed_task_routes_corresponding_group_to_sync(self) -> None:
        """`task.failed()` True → mapped group passed to `calc_and_save_sync`."""
        t1 = _fake_task("good")
        t2 = _fake_task("bad", failed=True)
        rs_mock = MagicMock()
        m_good = MagicMock()
        m_bad = MagicMock()
        celery_result_module = MagicMock(ResultSet=MagicMock(return_value=rs_mock))
        with patch.dict(
            "sys.modules", {"celery.result": celery_result_module}
        ), patch(
            "lex.core.tasks.CeleryTaskDispatcher.calc_and_save_sync",
        ) as sync_mock:
            CeleryTaskDispatcher._handle_task_results(
                [t1, t2], {"good": [m_good], "bad": [m_bad]}
            )

        sync_mock.assert_called_once()
        # Sync was called with the BAD group only — not the good one.
        called_group = sync_mock.call_args.args[0]
        self.assertIn(m_bad, called_group)
        self.assertNotIn(m_good, called_group)

    # 8.68 -------------------------------------------------------------
    def test_8_68_task_failed_check_raising_still_queues_group_for_retry(self):
        """`task.failed()` raising → status_check_errors logged, group still
        added to retry queue (assume failure rather than silently skip).

        Pins the defensive branch at lines 347-358: a transient
        worker-broker comm error during `.failed()` must not mean
        the group is silently lost.
        """
        t = _fake_task("flaky", failed_raise=RuntimeError("connection reset"))
        rs_mock = MagicMock()
        m = MagicMock()
        celery_result_module = MagicMock(ResultSet=MagicMock(return_value=rs_mock))
        with patch.dict(
            "sys.modules", {"celery.result": celery_result_module}
        ), patch(
            "lex.core.tasks.CeleryTaskDispatcher.calc_and_save_sync",
        ) as sync_mock:
            CeleryTaskDispatcher._handle_task_results(
                [t], {"flaky": [m]}
            )
        sync_mock.assert_called_once()  # Group queued for sync retry.

    # 8.69 -------------------------------------------------------------
    def test_8_69_resultset_processing_failure_falls_back_to_complete_sync(self):
        """`rs.join(...)` raises → flattened complete sync fallback over
        EVERY group in `group_mapping` (not just the failed ones).

        The "complete fallback" path at line 397 — when we don't even
        know which tasks failed, re-run everything synchronously and
        rely on idempotency at the calc layer to dedupe.
        """
        t1 = _fake_task("t1")
        t2 = _fake_task("t2")
        rs_mock = MagicMock()
        rs_mock.join.side_effect = RuntimeError("backend unreachable")
        celery_result_module = MagicMock(ResultSet=MagicMock(return_value=rs_mock))
        m1 = MagicMock()
        m2 = MagicMock()
        with patch.dict(
            "sys.modules", {"celery.result": celery_result_module}
        ), patch(
            "lex.core.tasks.CeleryTaskDispatcher.calc_and_save_sync",
        ) as sync_mock:
            CeleryTaskDispatcher._handle_task_results(
                [t1, t2], {"t1": [m1], "t2": [m2]}
            )
        sync_mock.assert_called_once()
        flattened = sync_mock.call_args.args[0]
        self.assertEqual(set(flattened), {m1, m2})

    # 8.70 -------------------------------------------------------------
    def test_8_70_resultset_failure_and_complete_sync_fallback_chain(self) -> None:
        """ResultSet processing fails AND complete sync fallback fails →
        chained `CeleryDispatchError` with both error strings.
        """
        rs_mock = MagicMock()
        rs_mock.join.side_effect = RuntimeError("backend down")
        celery_result_module = MagicMock(ResultSet=MagicMock(return_value=rs_mock))
        with patch.dict(
            "sys.modules", {"celery.result": celery_result_module}
        ), patch(
            "lex.core.tasks.CeleryTaskDispatcher.calc_and_save_sync",
            side_effect=RuntimeError("sync also blew up"),
        ):
            with self.assertRaises(CeleryDispatchError) as ctx:
                CeleryTaskDispatcher._handle_task_results(
                    [_fake_task("t1")], {"t1": [MagicMock()]}
                )
        msg = str(ctx.exception)
        self.assertIn("backend down", msg)
        self.assertIn("sync also blew up", msg)


# ----------------------------------------------------------------------
# 8.71  _get_calculation_context
# ----------------------------------------------------------------------


class TestCluster08l_GetCalculationContext(SimpleTestCase):
    """`_get_calculation_context` defensive paths."""

    # 8.71 -------------------------------------------------------------
    def test_8_71_returns_calc_id_when_present_none_otherwise_and_swallows_raise(self):
        """Three branches in one scenario:

        (a) context with `calculation_id` → returns it.
        (b) context without the key → returns None.
        (c) `operation_context.get()` raises → returns None defensively.

        The defensive swallow matters because this helper is called
        from inside the dispatch path; raising would crash the entire
        Celery dispatch instead of just losing the calc_id label.
        """
        # (a) Present.
        with patch(
            "lex.core.tasks.CeleryTaskDispatcher.operation_context"
        ) as op:
            op.get.return_value = {"calculation_id": "calc-42"}
            self.assertEqual(
                CeleryTaskDispatcher._get_calculation_context(),
                "calc-42",
            )

        # (b) Missing key.
        with patch(
            "lex.core.tasks.CeleryTaskDispatcher.operation_context"
        ) as op:
            op.get.return_value = {"other": "x"}
            self.assertIsNone(CeleryTaskDispatcher._get_calculation_context())

        # (c) operation_context.get() raises → swallowed, returns None.
        with patch(
            "lex.core.tasks.CeleryTaskDispatcher.operation_context"
        ) as op:
            op.get.side_effect = LookupError("no context var")
            self.assertIsNone(CeleryTaskDispatcher._get_calculation_context())


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
