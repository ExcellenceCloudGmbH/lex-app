"""The positional arguments of ``create(*args)`` reach ``calculate()`` on every route.

Intent: ``CalculatedModelMixin.create(*args, **kwargs)`` documents ``*args`` as
"Arguments passed to the calculate() method of each model"; ``**kwargs`` are the
field overrides. Which route ``create()`` takes depends on configuration
(``docs/reference/CalculatedModelMixin Internals.md``): with Celery inactive it
streams (default) or materialises (``LEX_SYNC_STREAMING_EXPANSION=false``); with
``CELERY_ACTIVE=true`` and a ``@lex_shared_task`` ``calculate()`` it sends one
``calc_and_save`` message per group and falls back to ``calc_and_save_sync`` when
a task fails. Turning Celery on must not change what a calculation computes, so
every route has to deliver the same arguments.

Until BUG-054 the Celery task called ``model.lex_func()()`` with no arguments, so
``create(x)`` computed with ``x`` locally and without it in production. Each
group message is ``(group, group_index, *create_args)``: the task now names the
index slot and passes the rest on. The message keeps the shape it always had,
so a message queued, redelivered or requeued by worker recovery across an
upgrade is read the same way by an old and a new worker (8.159).

Cluster 8e: scenarios 8.157-8.163. Type: I (real rows; Celery runs in eager mode).
Covers: lex/lex_app/celery_tasks.py (calc_and_save),
lex/core/tasks/CeleryTaskDispatcher.py (the group message it sends),
lex/core/mixins/CalculatedModelMixin.py (create and its sync consumers),
lex/core/models/CalculationModel.py (dispatch_calculation_task, the task's other sender).
Run: python -m lex pytest lex/test_project/tests/celery_async/test_8e_create_args_reach_calculate.py -v
"""

from __future__ import annotations

import json
import os
import unittest
from contextlib import contextmanager
from copy import deepcopy
from unittest import mock

import pytest
from celery.signals import task_postrun, task_prerun

from lex.api.utils import operation_context
from lex.audit_logging.utils.ModelContext import _model_context
from lex.core.models.CalculationModel import CalculationModel
from lex.lex_app.celery_tasks import calc_and_save, tasks_context, unblock_tasks_context
from lex.test_project.tests._e2e_test_case import E2ETestCase

from .models import CREATE_ARGS_MODELS, CelerySyncCalc, CreateArgsBatch, NoArgBatch
from .test_8h_eager_end_to_end import _celery_eager, _reset_dispatch_ctx

pytestmark = pytest.mark.celery_async

CREATE_ARGS = ("Q4", 7)
REGIONS = {"US", "EU", "APAC"}


@contextmanager
def _worker_dispatch_scopes():
    """Run each eager task with the empty dispatch scopes a worker process starts with.

    Eager mode runs a task in the caller's thread, where the dispatcher's own
    ``WaitForTasks`` scope is still open. A worker is another process with no
    such scope, so it calls the row's ``calculate()`` directly. Seen through the
    caller's scope, the task would send ``calculate()`` off as one more message,
    which Celery pickles even in eager mode: it would run on a copy, and the task
    would save the row without its result. That never happens on a worker.
    """
    tokens = {}

    def _enter(task_id=None, **_):
        tokens[task_id] = (
            tasks_context.set({"task_context_stack": []}),
            unblock_tasks_context.set({"unblock_context_stack": []}),
        )

    def _leave(task_id=None, **_):
        wait_token, fire_token = tokens.pop(task_id)
        unblock_tasks_context.reset(fire_token)
        tasks_context.reset(wait_token)

    task_prerun.connect(_enter, weak=False)
    task_postrun.connect(_leave, weak=False)
    try:
        yield
    finally:
        task_prerun.disconnect(_enter)
        task_postrun.disconnect(_leave)


@contextmanager
def _executed_tasks():
    """Record ``(task name, positional args)`` for every task Celery runs.

    ``task_prerun`` is sent for eager tasks as for a worker's, so the list says
    which messages were executed, with the arguments the task received.
    """
    executed = []

    def _record(sender=None, task=None, args=None, **_):
        executed.append((task.name, tuple(args or ())))

    task_prerun.connect(_record, weak=False)
    try:
        yield executed
    finally:
        task_prerun.disconnect(_record)


@contextmanager
def _on_celery(propagate: bool = True):
    """Celery active in eager mode, tasks run as on a worker; yields the executed tasks."""
    with _celery_eager(propagate=propagate), _worker_dispatch_scopes(), \
            _executed_tasks() as executed:
        yield executed


class TestCluster08e_CreateArgsReachCalculate(E2ETestCase):
    """Cluster 8e: ``create(*args)`` arguments reach ``calculate()`` on every route."""

    e2e_models = CREATE_ARGS_MODELS

    def setUp(self) -> None:
        super().setUp()
        _reset_dispatch_ctx()
        self.addCleanup(_reset_dispatch_ctx)
        # A fresh operation context, as a request or a calculation opens one.
        token = operation_context.set(
            {"operation_id": "", "request_obj": "", "calculation_id": "", "audit_log_temp": None}
        )
        self.addCleanup(operation_context.reset, token)
        CreateArgsBatch._region_keys = ["US", "EU", "APAC"]
        CreateArgsBatch.calls = []
        CreateArgsBatch.fail_once_for = set()
        NoArgBatch._region_keys = ["US", "EU", "APAC"]

    # -- helpers -------------------------------------------------------
    def _received_by_region(self) -> dict:
        """Each stored row's ``received_args``, decoded, keyed by region."""
        return {
            row.region: json.loads(row.received_args) if row.received_args else None
            for row in CreateArgsBatch.objects.all()
        }

    def _group_messages(self, executed: list) -> list:
        """The ``calc_and_save`` messages Celery executed, in order."""
        return [args for name, args in executed if name == calc_and_save.name]

    def _assert_every_row_received(self, expected: list, route: str) -> None:
        received = self._received_by_region()
        self.assertEqual(
            set(received), REGIONS,
            f"{route}: create() must store one row per region; stored {sorted(received)}",
        )
        wrong = {region: got for region, got in received.items() if got != expected}
        self.assertEqual(
            wrong, {},
            f"{route}: every row's calculate() must receive create()'s arguments "
            f"{expected}; these rows got something else: {wrong}",
        )

    # -- 8.157 ---------------------------------------------------------
    def test_08_157_celery_route_delivers_create_args_to_every_group(self) -> None:
        """
        Scenario 8.157: on the Celery route every group's rows get create()'s arguments.
        Given: CELERY_ACTIVE=true and a @lex_shared_task calculate(), with the rows
               grouped one Celery task per region (three groups, indexes 0-2)
        When:  CreateArgsBatch.create("Q4", 7) runs
        Then:  three calc_and_save messages run, and every row's calculate()
               receives exactly ("Q4", 7): not nothing, and not the group index
        """
        with _on_celery() as executed:
            CreateArgsBatch.create(*CREATE_ARGS)

        self.assertEqual(
            len(self._group_messages(executed)), 3,
            f"precondition: create() must take the Celery route, one calc_and_save "
            f"message per region; Celery ran {[name for name, _ in executed]}",
        )
        self._assert_every_row_received(list(CREATE_ARGS), "Celery route")
        self.assertEqual(
            {args for _, args in CreateArgsBatch.calls}, {CREATE_ARGS},
            f"no calculate() call may receive other arguments, such as the group "
            f"index; calls were {CreateArgsBatch.calls}",
        )

    # -- 8.158 ---------------------------------------------------------
    def test_08_158_celery_route_without_args_calls_calculate_without_args(self) -> None:
        """
        Scenario 8.158: create() with no arguments still works on the Celery route.
        Given: CELERY_ACTIVE=true and a @lex_shared_task calculate(self) that takes
               no arguments, like most customer batches
        When:  NoArgBatch.create() runs, three groups
        Then:  every row is calculated and saved; the group index is never handed
               to calculate(), which would raise TypeError
        """
        with _on_celery() as executed:
            NoArgBatch.create()

        self.assertEqual(
            len(self._group_messages(executed)), 3,
            f"precondition: create() must take the Celery route; Celery ran "
            f"{[name for name, _ in executed]}",
        )
        stored = {row.region: row.calculated for row in NoArgBatch.objects.all()}
        self.assertEqual(
            stored, {region: True for region in REGIONS},
            f"every row must be calculated and saved; stored {stored}",
        )

    # -- 8.159 ---------------------------------------------------------
    def test_08_159_worker_reads_the_group_message_shape(self) -> None:
        """
        Scenario 8.159: a worker running a group message passes only create()'s arguments on.
        Given: a group message in the shape the dispatcher sends, and every message
               queued by v2.4.2 has: (group, group_index, *create_args), with the
               dispatcher's context and model_context
        When:  a worker runs it: calc_and_save executed directly, with no dispatch
               scope open, so the row's calculate() is called inside the task
        Then:  calculate() receives ("Q4", 7), the row is saved with it, and no
               further task is started
        """
        row = CreateArgsBatch(region="EU")
        message_args = ([row], 2, *CREATE_ARGS)
        message_kwargs = {
            "context": {**operation_context.get(), "request_obj": {}},
            "model_context": deepcopy(_model_context.get()["model_context"]),
        }

        with _on_celery() as executed:
            result = calc_and_save.apply(args=message_args, kwargs=message_kwargs)

        self.assertTrue(result.successful(), f"the task must succeed; it ended {result.state}: {result.result!r}")
        self.assertEqual(
            [name for name, _ in executed], [calc_and_save.name],
            "precondition: calculate() must run inside the task, as in a worker "
            f"with no dispatch scope; Celery ran {[name for name, _ in executed]}",
        )
        self.assertEqual(
            CreateArgsBatch.calls, [("EU", CREATE_ARGS)],
            "calculate() must receive the arguments after the group index, and only those",
        )
        self.assertEqual(
            json.loads(CreateArgsBatch.objects.get(region="EU").received_args), list(CREATE_ARGS),
            "the row must be saved with what calculate() computed from the arguments",
        )

    # -- 8.160 ---------------------------------------------------------
    def test_08_160_dispatch_calculation_task_message_still_runs_calculate(self) -> None:
        """
        Scenario 8.160: the task's other sender, dispatch_calculation_task, still works.
        Given: an undecorated CalculationModel row; dispatch_calculation_task sends
               calc_and_save([row], context=..., model_context=...): no group index
               and no arguments
        When:  the row is saved IN_PROGRESS with Celery active, which dispatches it
        Then:  the task runs once, calculate() runs with no arguments (it takes none),
               and CallbackTask, which looks for the instance in the first argument,
               flips the row to SUCCESS
        """
        calc = CelerySyncCalc.objects.create(name="s8-160")

        with _on_celery() as executed, mock.patch(
            "lex.lex_app.celery_tasks.ensure_terminal_calculation_audit"
        ):
            calc.is_calculated = CalculationModel.IN_PROGRESS
            calc.save()

        self.assertEqual(
            self._group_messages(executed), [([calc],)],
            f"precondition: the generic task must run once with the instance alone; ran {executed}",
        )
        self.assertEqual(
            CelerySyncCalc.objects.get(pk=calc.pk).is_calculated, CalculationModel.SUCCESS,
            "calculate() must succeed and CallbackTask must find the row in the first argument",
        )

    # -- 8.161 ---------------------------------------------------------
    def test_08_161_sync_streaming_route_delivers_create_args(self) -> None:
        """
        Scenario 8.161: the default sync route still delivers create()'s arguments.
        Given: Celery inactive and LEX_SYNC_STREAMING_EXPANSION unset (streaming)
        When:  CreateArgsBatch.create("Q4", 7) runs
        Then:  no Celery task runs, and every row's calculate() receives ("Q4", 7)
        """
        with mock.patch.dict(os.environ), _executed_tasks() as executed:
            os.environ.pop("LEX_SYNC_STREAMING_EXPANSION", None)
            CreateArgsBatch.create(*CREATE_ARGS)

        self.assertEqual(
            self._group_messages(executed), [],
            "precondition: with Celery inactive no calc_and_save message may run",
        )
        self._assert_every_row_received(list(CREATE_ARGS), "streaming sync route")

    # -- 8.162 ---------------------------------------------------------
    def test_08_162_sync_materialised_route_delivers_create_args(self) -> None:
        """
        Scenario 8.162: the materialising sync route still delivers create()'s arguments.
        Given: Celery inactive and LEX_SYNC_STREAMING_EXPANSION=false, the rollback
               to the four-step pipeline that ends in calc_and_save_sync
        When:  CreateArgsBatch.create("Q4", 7) runs
        Then:  no Celery task runs, and every row's calculate() receives ("Q4", 7)
        """
        with mock.patch.dict(os.environ, {"LEX_SYNC_STREAMING_EXPANSION": "false"}), \
                _executed_tasks() as executed:
            CreateArgsBatch.create(*CREATE_ARGS)

        self.assertEqual(
            self._group_messages(executed), [],
            "precondition: with Celery inactive no calc_and_save message may run",
        )
        self._assert_every_row_received(list(CREATE_ARGS), "materialising sync route")

    # -- 8.163 ---------------------------------------------------------
    def test_08_163_celery_fallback_reruns_with_create_args(self) -> None:
        """
        Scenario 8.163: when a Celery task fails, the synchronous re-run uses the same arguments.
        Given: the Celery route of 8.157, where the EU row's calculate() fails once,
               as a transient error in a worker would
        When:  CreateArgsBatch.create("Q4", 7) runs and the dispatcher falls back to
               calc_and_save_sync for what failed
        Then:  EU is calculated a second time, every row ends with ("Q4", 7), and
               every calculate() call, in a task or in the fallback, received ("Q4", 7)
        """
        CreateArgsBatch.fail_once_for = {"EU"}

        with _on_celery(propagate=False) as executed:
            CreateArgsBatch.create(*CREATE_ARGS)

        self.assertTrue(
            self._group_messages(executed),
            "precondition: create() must take the Celery route before falling back",
        )
        eu_calls = [args for region, args in CreateArgsBatch.calls if region == "EU"]
        self.assertGreaterEqual(
            len(eu_calls), 2,
            f"precondition: EU must fail in its task and be re-run by the fallback; "
            f"calls were {CreateArgsBatch.calls}",
        )
        self._assert_every_row_received(list(CREATE_ARGS), "Celery route with fallback")
        self.assertEqual(
            {args for _, args in CreateArgsBatch.calls}, {CREATE_ARGS},
            f"every calculate() call must receive create()'s arguments; calls were "
            f"{CreateArgsBatch.calls}",
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
