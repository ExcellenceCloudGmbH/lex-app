"""Sub-cluster 8ab — `CeleryTaskDispatcher` join is worker-safe (`allow_join_result`).

Intent: the framework lets a calculation dispatch *nested* fan-out work — a calc
running inside a Celery worker can itself partition its models and dispatch them as
child tasks (this is the `CalculatedModelMixin` combinatorial fan-out via
`CeleryTaskDispatcher.dispatch_calculation_groups`). When that fan-out blocks on the
child results it calls `ResultSet.join()`. Celery **hard-forbids** `result.get()` /
`ResultSet.join()` from inside a worker ("Never call result.get() within a task!")
unless the call is wrapped in `allow_join_result()`. Before the fix the join was
unwrapped; an `is_celery_worker_process()` guard *masked* it by forcing nested calcs
to run inline. Removing that guard (cluster 7q) exposed the unwrapped join, and a
real nested `InvestmentPosting` run crashed with that exact assertion, fell back to a
complete-sync re-run, and re-committed already-persisted rows → duplicate-key
violation. The fix wraps `rs.join(propagate=False)` in `allow_join_result()`,
mirroring `WaitForTasks.wait_for_completion` which already blocks under the same
guard. A regression here silently re-introduces the production crash + double-write.

These scenarios reproduce the crash deterministically **without a broker**: Celery's
real `allow_join_result()` toggles the thread-local `task_join_will_block` flag, and
`assert_will_not_block()` (invoked at the top of the real `ResultSet.join`) raises the
production error whenever that flag is set. We set the flag to simulate "inside a
worker", give the fake `ResultSet` a `.join` that calls the **real**
`assert_will_not_block()`, and keep the **real** `allow_join_result` so the wrap is
genuinely exercised — only `ResultSet` itself is stubbed so no backend is contacted.

Cluster 8ab — scenarios 8.129–8.138. Type: U.
Covers: lex/core/tasks/CeleryTaskDispatcher.py (`_handle_task_results` join wrap;
        `dispatch_calculation_groups` nested-in-worker end-to-end path).
Run: python -m lex pytest lex/test_project/tests/celery_async/test_8ab_dispatcher_join_worker_safe.py -v
"""

from __future__ import annotations

import types
import unittest
from contextlib import ExitStack
from unittest.mock import MagicMock, patch

import pytest
from django.test import SimpleTestCase

from celery.result import assert_will_not_block
from celery._state import _set_task_join_will_block, task_join_will_block

from lex.core.exceptions import CeleryDispatchError
from lex.core.tasks.CeleryTaskDispatcher import CeleryTaskDispatcher

pytestmark = pytest.mark.celery_async


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------


def _fake_task(task_id, *, failed=False, failed_raise=None):
    """AsyncResult-like stand-in for `_handle_task_results` iteration."""
    t = MagicMock()
    t.id = task_id
    if failed_raise is not None:
        t.failed.side_effect = failed_raise
    else:
        t.failed.return_value = failed
    t.result = "boom" if failed else None
    return t


def _fake_resultset_factory(*, join_raise=None, record=None):
    """Build a `ResultSet` replacement whose `.join` behaves like the real one.

    The real `celery.result.ResultSet.join` calls `assert_will_not_block()`
    at the top — that is precisely what raises "Never call result.get()
    within a task!" when we are (simulating) inside a worker. Our fake calls
    the **real** `assert_will_not_block()` so the worker-safety of the caller
    is genuinely under test; only the backend round-trip is stubbed away.

    `record` (a dict) captures observability: whether the block-assertion
    passed and the flag value seen *during* the join.
    """

    def _factory(task_results):
        rs = MagicMock(name="ResultSet")

        def _join(propagate=False):
            # Mirror real ResultSet.join: this is the forbidden call.
            assert_will_not_block()
            if record is not None:
                record["join_called"] = True
                record["flag_during_join"] = task_join_will_block()
            if join_raise is not None:
                raise join_raise
            return []

        rs.join.side_effect = _join
        return rs

    return _factory


def _patch_celery_result(*, join_raise=None, record=None):
    """Patch ONLY `ResultSet` on the real `celery.result` module.

    Leaving the real `allow_join_result` / `assert_will_not_block` in place is
    the whole point: the fix under test relies on the real context manager
    clearing the thread-local join-block flag.
    """
    return patch(
        "celery.result.ResultSet",
        side_effect=_fake_resultset_factory(join_raise=join_raise, record=record),
    )


def _simulate_inside_worker():
    """Set the thread-local flag Celery sets while a worker executes a task."""
    _set_task_join_will_block(True)


def _ctx():
    """Default `context` kwarg shape `_dispatch_single_group` expects."""
    return {"request_obj": {}, "calculation_id": "calc-x"}


def _patch_dispatch_imports(*, fire_active=False, wait_active=False):
    """Patch the lazy imports inside `dispatch_calculation_groups`.

    Mirrors the 8l helper: stubs `calc_and_save.delay`, the FF/WFT scope
    lookups, and `register_task_with_context`, so the end-to-end path can run
    without a broker. Returns `(ExitStack, task_module)`.
    """
    stack = ExitStack()

    ff = MagicMock()
    ff.get_current_context.return_value = MagicMock() if fire_active else None
    wft = MagicMock()
    wft.get_current_context.return_value = MagicMock() if wait_active else None
    wft.return_value.__enter__ = MagicMock(return_value=None)
    wft.return_value.__exit__ = MagicMock(return_value=False)

    task_module = MagicMock()
    task_module.calc_and_save = MagicMock()
    task_module.calc_and_save.delay = MagicMock(
        side_effect=lambda *a, **k: _fake_task(f"t{id(a)}")
    )
    task_module.FireAndForget = ff
    task_module.WaitForTasks = wft
    task_module.register_task_with_context = MagicMock()

    stack.enter_context(
        patch.dict("sys.modules", {"lex.lex_app.celery_tasks": task_module})
    )
    stack.enter_context(
        patch("lex.core.tasks.CeleryTaskDispatcher.calc_and_save_sync")
    )
    return stack, task_module


# ----------------------------------------------------------------------
# 8.129 – 8.138  _handle_task_results join wrapped in allow_join_result
# ----------------------------------------------------------------------


class TestCluster08ab_HandleResultsJoinWorkerSafe(SimpleTestCase):
    """`_handle_task_results` blocks on the ResultSet under `allow_join_result`."""

    def setUp(self) -> None:
        # Every test that simulates a worker must leave the global flag clean
        # for the next test, regardless of pass/fail.
        self.addCleanup(_set_task_join_will_block, False)

    # 8.129 ------------------------------------------------------------
    def test_8_129_join_inside_worker_does_not_raise_never_call_get(self) -> None:
        """Inside a (simulated) worker the join must NOT raise the Celery
        "Never call result.get() within a task!" assertion.

        Scenario 8.129: the exact production-crash regression.
        Given: the thread-local worker join-block flag is set (as Celery does
               while a worker runs a task), and the ResultSet.join calls the
               real `assert_will_not_block()`.
        When:  `_handle_task_results` runs (its join is wrapped in
               `allow_join_result`).
        Then:  no RuntimeError escapes and the join actually executes with the
               block-flag cleared — proving the wrap did its job.
        """
        record = {}
        _simulate_inside_worker()
        t1, t2 = _fake_task("t1"), _fake_task("t2")
        with _patch_celery_result(record=record), patch(
            "lex.core.tasks.CeleryTaskDispatcher.calc_and_save_sync"
        ) as sync_mock:
            CeleryTaskDispatcher._handle_task_results(
                [t1, t2], {"t1": [MagicMock()], "t2": [MagicMock()]}
            )
        self.assertTrue(
            record.get("join_called"),
            "join must run under allow_join_result, not be skipped",
        )
        self.assertFalse(
            record.get("flag_during_join"),
            "allow_join_result must clear the join-block flag during the join",
        )
        sync_mock.assert_not_called()

    # 8.130 ------------------------------------------------------------
    def test_8_130_worker_join_block_flag_restored_after_return(self) -> None:
        """`allow_join_result` restores the worker join-block flag on exit.

        Scenario 8.130: no state leak.
        Given: the worker join-block flag is set True before the call.
        When:  `_handle_task_results` completes.
        Then:  the flag is True again — the context manager restored it, so a
               subsequent real `.get()` in the same worker task is still
               correctly forbidden (the guard is not permanently disabled).
        """
        _simulate_inside_worker()
        t = _fake_task("t")
        with _patch_celery_result(), patch(
            "lex.core.tasks.CeleryTaskDispatcher.calc_and_save_sync"
        ):
            CeleryTaskDispatcher._handle_task_results([t], {"t": [MagicMock()]})
        self.assertTrue(
            task_join_will_block(),
            "the worker join-block flag must be restored after the wrapped join",
        )

    # 8.131 ------------------------------------------------------------
    def test_8_131_non_worker_context_unaffected_all_success(self) -> None:
        """Outside a worker the wrap is transparent: join runs, no fallback.

        Scenario 8.131: the fix must not change the ordinary (non-nested) path.
        Given: the join-block flag is False (top-level dispatch, not in a task).
        When:  all tasks succeed.
        Then:  join runs once, no group is routed to sync, flag stays False.
        """
        _set_task_join_will_block(False)
        record = {}
        t1, t2 = _fake_task("t1"), _fake_task("t2")
        with _patch_celery_result(record=record), patch(
            "lex.core.tasks.CeleryTaskDispatcher.calc_and_save_sync"
        ) as sync_mock:
            CeleryTaskDispatcher._handle_task_results(
                [t1, t2], {"t1": [MagicMock()], "t2": [MagicMock()]}
            )
        self.assertTrue(record.get("join_called"))
        sync_mock.assert_not_called()
        self.assertFalse(task_join_will_block())

    # 8.132 ------------------------------------------------------------
    def test_8_132_worker_join_safe_and_failed_group_still_retried(self) -> None:
        """Inside a worker, the wrap must not break the sync-retry ladder.

        Scenario 8.132: join-safety composes with per-task failure routing.
        Given: simulated worker; one task reports `.failed()` True.
        When:  `_handle_task_results` runs.
        Then:  no worker crash AND the failed task's mapped group is handed to
               `calc_and_save_sync` (the good group is not).
        """
        _simulate_inside_worker()
        t_good, t_bad = _fake_task("good"), _fake_task("bad", failed=True)
        m_good, m_bad = MagicMock(), MagicMock()
        with _patch_celery_result(), patch(
            "lex.core.tasks.CeleryTaskDispatcher.calc_and_save_sync"
        ) as sync_mock:
            CeleryTaskDispatcher._handle_task_results(
                [t_good, t_bad], {"good": [m_good], "bad": [m_bad]}
            )
        sync_mock.assert_called_once()
        called_group = sync_mock.call_args.args[0]
        self.assertIn(m_bad, called_group)
        self.assertNotIn(m_good, called_group)

    # 8.133 ------------------------------------------------------------
    def test_8_133_worker_join_raises_falls_back_to_complete_sync(self) -> None:
        """Inside a worker, a genuine join failure still triggers complete sync.

        Scenario 8.133: the wrap must not swallow the fallback ladder.
        Given: simulated worker; the (block-safe) join itself raises
               (backend unreachable) — a real failure, not the block assertion.
        When:  `_handle_task_results` runs.
        Then:  every mapped group is flattened into one `calc_and_save_sync`
               call (the "we don't know which failed, re-run all" fallback).
        """
        _simulate_inside_worker()
        m1, m2 = MagicMock(), MagicMock()
        with _patch_celery_result(
            join_raise=RuntimeError("backend unreachable")
        ), patch(
            "lex.core.tasks.CeleryTaskDispatcher.calc_and_save_sync"
        ) as sync_mock:
            CeleryTaskDispatcher._handle_task_results(
                [_fake_task("t1"), _fake_task("t2")],
                {"t1": [m1], "t2": [m2]},
            )
        sync_mock.assert_called_once()
        flattened = sync_mock.call_args.args[0]
        self.assertEqual(set(flattened), {m1, m2})

    # 8.134 ------------------------------------------------------------
    def test_8_134_worker_join_safe_when_failed_check_itself_raises(self) -> None:
        """Inside a worker, a raising `.failed()` still queues the group.

        Scenario 8.134: join-safety composes with the defensive status-check
        branch (a transient worker/broker comm error while reading status).
        Given: simulated worker; `task.failed()` raises.
        When:  `_handle_task_results` runs.
        Then:  no worker crash AND the group is assumed failed and queued for
               synchronous retry (never silently dropped).
        """
        _simulate_inside_worker()
        t = _fake_task("flaky", failed_raise=RuntimeError("connection reset"))
        m = MagicMock()
        with _patch_celery_result(), patch(
            "lex.core.tasks.CeleryTaskDispatcher.calc_and_save_sync"
        ) as sync_mock:
            CeleryTaskDispatcher._handle_task_results([t], {"flaky": [m]})
        sync_mock.assert_called_once()

    # 8.135 ------------------------------------------------------------
    def test_8_135_allow_join_result_import_is_required(self) -> None:
        """The `allow_join_result` symbol must stay in the celery.result import.

        Scenario 8.135: guard against a regression that drops the import (which
        would re-expose the unwrapped join). If `celery.result` lacks
        `allow_join_result`, the lazy `from celery.result import ResultSet,
        allow_join_result` must fail loudly as a chained `CeleryDispatchError`
        rather than silently proceeding with an unwrapped join.
        Given: a `celery.result` module exposing `ResultSet` but NOT
               `allow_join_result`.
        When:  `_handle_task_results` runs.
        Then:  it raises `CeleryDispatchError` chained from the ImportError.
        """
        stub = types.ModuleType("celery.result")
        stub.ResultSet = MagicMock()  # present
        # allow_join_result deliberately absent → `from ... import ...` raises.
        with patch.dict("sys.modules", {"celery.result": stub}):
            with self.assertRaises(CeleryDispatchError) as ctx:
                CeleryTaskDispatcher._handle_task_results(
                    [_fake_task("t1")], {"t1": [MagicMock()]}
                )
        self.assertIsInstance(ctx.exception.__cause__, ImportError)


# ----------------------------------------------------------------------
# 8.136 – 8.138  Nested dispatch end-to-end, worker-safe under every context
# ----------------------------------------------------------------------


class TestCluster08ab_NestedDispatchWorkerSafe(SimpleTestCase):
    """`dispatch_calculation_groups` end-to-end inside a worker stays safe.

    This is the *production* path: a calc already running inside a worker fans
    its models out and blocks on them. The wait-scope selection is pre-existing
    behaviour (cluster 8l 8.52–8.54); what is new here is that the whole path —
    scope selection → per-group `.delay()` → `_handle_task_results` join — runs
    with the worker join-block flag SET, so the join would crash without the
    fix. We assert the "same behaviour as before" for each context (implicit
    WaitForTasks when none is stated; explicit WaitForTasks / FireAndForget
    reused) while proving each is worker-safe.
    """

    def setUp(self) -> None:
        self.addCleanup(_set_task_join_will_block, False)

    def _drive(self, *, fire, wait, record):
        stack, task_module = _patch_dispatch_imports(
            fire_active=fire, wait_active=wait
        )
        with stack, _patch_celery_result(record=record), patch(
            "lex.core.tasks.CeleryTaskDispatcher._model_context"
        ) as mc:
            mc.get.return_value = {"model_context": {}}
            CeleryTaskDispatcher.dispatch_calculation_groups(
                [[MagicMock()], [MagicMock()]], context=_ctx()
            )
        return task_module

    # 8.136 ------------------------------------------------------------
    def test_8_136_no_context_opens_implicit_wait_and_join_is_worker_safe(self):
        """No explicit context inside a worker → implicit WaitForTasks + safe join.

        Scenario 8.136: the default nested fan-out path (the InvestmentPosting
        crash scenario).
        Given: simulated worker; neither FireAndForget nor WaitForTasks active.
        When:  `dispatch_calculation_groups` runs end-to-end.
        Then:  it opens an implicit `WaitForTasks()` (same behaviour as before),
               dispatches every group, and the join completes with the
               block-flag cleared — no "Never call result.get()" crash.
        """
        _simulate_inside_worker()
        record = {}
        task_module = self._drive(fire=False, wait=False, record=record)
        task_module.WaitForTasks.assert_called_once_with()
        self.assertTrue(record.get("join_called"))
        self.assertFalse(record.get("flag_during_join"))

    # 8.137 ------------------------------------------------------------
    def test_8_137_explicit_wait_reused_and_join_is_worker_safe(self) -> None:
        """Explicit WaitForTasks inside a worker → reused, join still safe.

        Scenario 8.137: an outer WaitForTasks owns the drain (nullcontext), and
        the fan-out join underneath is still worker-safe.
        Given: simulated worker; an active WaitForTasks context.
        When:  `dispatch_calculation_groups` runs.
        Then:  no new WaitForTasks is instantiated (no double-join), and the
               join completes without the worker crash.
        """
        _simulate_inside_worker()
        record = {}
        task_module = self._drive(fire=False, wait=True, record=record)
        task_module.WaitForTasks.assert_not_called()
        self.assertTrue(record.get("join_called"))
        self.assertFalse(record.get("flag_during_join"))

    # 8.138 ------------------------------------------------------------
    def test_8_138_explicit_fire_and_forget_reused_and_join_is_worker_safe(self):
        """Explicit FireAndForget inside a worker → reused, join still safe.

        Scenario 8.138: fire-and-forget semantics preserved (no implicit
        WaitForTasks that would force a drain), and the join the dispatcher
        performs is still worker-safe.
        Given: simulated worker; an active FireAndForget context.
        When:  `dispatch_calculation_groups` runs.
        Then:  no WaitForTasks is instantiated, and the join completes without
               the worker crash.
        """
        _simulate_inside_worker()
        record = {}
        task_module = self._drive(fire=True, wait=False, record=record)
        task_module.WaitForTasks.assert_not_called()
        self.assertTrue(record.get("join_called"))
        self.assertFalse(record.get("flag_during_join"))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
