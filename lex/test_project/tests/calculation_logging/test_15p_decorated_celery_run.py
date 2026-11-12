"""A CalculationModel dispatched to Celery runs in a frame of its own, decorated or not.

Intent: docs/calculations/logging.md (Nested Calculations) promises that a child
calculation's log nests under the calculation that started it, and
docs/reference/CalculationModel Internals.md says a ``@lex_shared_task``
``calculate()`` is dispatched to a worker as itself, with the audit logging
context propagated, where an undecorated one is wrapped in the generic
``calc_and_save`` task. Turning Celery on, or decorating ``calculate()``, must
not change the log tree. Only the root of a run may end the run's live log
(cluster 9c), and a run must not lose what it logged (cluster 15j).

``calculate_hook`` gives the model it calculates a frame of its own on the model
context stack (``CalculationModel.child_calculation_context``), from before the run
is dispatched until the hook's failure handling is done. The task is shipped a
copy of the stack with the model on top: a decorated ``calculate()`` runs on that
stack as it is, and the generic task finds the model already on top. The hook's
failure handling, which runs in the caller's thread when the task failed, decides
from the stack whether the run is the root. Before BUG-056 the frame was opened
only for a synchronous run, so on Celery:

- a nested model whose ``calculate()`` is decorated filed its lines under its
  caller (15.72), and so did whatever it saved in turn (15.73);
- a decorated root saved with nothing on the stack filed its lines in a node that
  belongs to no record (15.74);
- on both Celery routes, a nested model that failed was taken for the root, and
  purged the run's live log while the root was still running (15.76, 15.77).

15.75 guards that the generic task agrees with the decorated one for an unframed
root. 15.71 already pins the tree for an undecorated nested model.

Driven end to end through Celery's eager mode (the harness of cluster 8h): real
``.delay()``, real ``@lex_shared_task`` wrapper, real ``calc_and_save``, real
``CallbackTask``. Only the broker is replaced, by Celery itself. None of these
scenarios routes a decorated ``calculate()`` through ``calc_and_save``, so eager
mode's view of the caller's dispatch scope does not change the route they take.

Cluster 15p — scenarios 15.72–15.77. Type: I.
Covers: lex/core/models/CalculationModel.py (calculate_hook,
child_calculation_context, dispatch_calculation_task),
lex/lex_app/celery_tasks.py (lex_shared_task wrapper, calc_and_save, CallbackTask).
Run: python -m lex pytest lex/test_project/tests/calculation_logging/test_15p_decorated_celery_run.py -v
"""
from __future__ import annotations

from contextlib import contextmanager

import pytest
from celery.signals import task_prerun

from lex.audit_logging.models.AuditLog import AuditLog
from lex.audit_logging.models.AuditLogStatus import AuditLogStatus
from lex.audit_logging.models.CalculationLog import CalculationLog
from lex.audit_logging.utils.ModelContext import _model_context
from lex.lex_app.celery_tasks import calc_and_save
from lex.test_project.tests.celery_async.test_8h_eager_end_to_end import _celery_eager

from . import _CalcLogTestCase
from .models import (
    ALL_MODELS,
    DECORATED_RUN_MODELS,
    NESTED_RUN_MODELS,
    LogCeleryFailingInnerSavedCalc,
    LogCeleryInnerSavedCalc,
    LogCeleryMiddleSavedCalc,
    LogCeleryRootCalc,
    LogFailingInnerSavedCalc,
    LogInnerSavedCalc,
    LogRootCalc,
)
from .test_15o_nested_calculation_run import _live_log_purged_like_redis

pytestmark = pytest.mark.calculation_logging

GENERIC_TASK = calc_and_save.name


@contextmanager
def _on_celery():
    """Celery active in eager mode; yields the names of the tasks Celery runs.

    ``task_prerun`` is sent for an eager task as for a worker's, so the list says
    which route each calculation took.
    """
    names = []

    def _record(sender=None, task=None, **_):
        names.append(task.name)

    task_prerun.connect(_record, weak=False)
    try:
        with _celery_eager():
            yield names
    finally:
        task_prerun.disconnect(_record)


class TestCluster15p_DecoratedCeleryRun(_CalcLogTestCase):
    """Cluster 15p: a CalculationModel dispatched to Celery runs in a frame of its own."""

    e2e_models = ALL_MODELS + NESTED_RUN_MODELS + DECORATED_RUN_MODELS
    # The harness stubs the live cache (every key is "test_key" and nothing is
    # stored); 15.76 and 15.77 are about what the real cache holds.
    e2e_unpatch = {"ensure_terminal_calculation_audit", "store_message", "build_cache_key"}

    def _assert_one_root_node(self, root_row):
        parentless = list(
            CalculationLog.objects.filter(
                calculationId=self.calc_id, parent_log__isnull=True,
            ).values_list("pk", flat=True)
        )
        self.assertEqual(
            parentless, [root_row.pk],
            "The run's tree must have exactly one root node, the root's.",
        )

    # -- 15.72 ---------------------------------------------------------
    def test_15_72_a_nested_decorated_calculation_model_gets_its_own_node(self):
        """
        Scenario 15.72: a nested CalculationModel whose calculate() is a @lex_shared_task gets its own node
        Given: CELERY_ACTIVE with eager tasks; the root runs in the generic
               calc_and_save task and saves a CalculationModel whose
               calculate() is a @lex_shared_task, so that model is dispatched
               as its own decorated task
        When:  the root is saved
        Then:  the tree is the one 15.19 pins for the synchronous route and
               15.71 for the generic task: one row for the root, with no
               parent, holding the root's line and not the nested model's,
               and one row for the nested model under it, holding its line
        """
        with _on_celery() as tasks:
            root = self._save_root(child_mode="celery_calcmodel_chain", units_csv="x")

        self.assertIn(
            LogCeleryInnerSavedCalc.calculate.name, tasks,
            f"Sanity: the nested model must have run as its own decorated task; Celery ran {tasks}.",
        )
        inner = LogCeleryInnerSavedCalc.objects.get(name="x")
        self.assertEqual(
            inner.is_calculated, LogCeleryInnerSavedCalc.SUCCESS,
            "Sanity: the nested model's run must succeed.",
        )
        root_row = self.assert_log_row(root, parent=None, contains="root root-celery_calcmodel_chain")
        self.assertNotIn(
            "celery inner x", root_row.calculation_log,
            "The nested model's line must be filed under the nested model, not under its caller.",
        )
        self.assert_log_row(inner, parent=root_row, contains="celery inner x")
        self.assert_total_rows(2)

    # -- 15.73 ---------------------------------------------------------
    def test_15_73_what_a_decorated_model_saves_nests_under_it(self):
        """
        Scenario 15.73: a CalculationModel saved inside a decorated calculate() nests under it
        Given: CELERY_ACTIVE with eager tasks; the root saves a middle
               CalculationModel whose calculate() is a @lex_shared_task; on
               the worker, the middle logs a line and saves a LogInnerSavedCalc,
               which is dispatched to a calc_and_save task of its own
        When:  the root is saved
        Then:  the tree is the one 15.20 pins for the synchronous route: the
               root's row with no parent, the middle's row under it holding
               the middle's line, and the inner model's row under the middle's
        """
        with _on_celery() as tasks:
            root = self._save_root(child_mode="celery_calcmodel_chain_3", units_csv="x")

        self.assertIn(
            LogCeleryMiddleSavedCalc.calculate.name, tasks,
            f"Sanity: the middle must have run as its own decorated task; Celery ran {tasks}.",
        )
        self.assertEqual(
            tasks.count(GENERIC_TASK), 2,
            "Sanity: the root and the inner model must each have run in a calc_and_save "
            f"task; Celery ran {tasks}.",
        )
        root_row = self.assert_log_row(root, parent=None, contains="root root-celery_calcmodel_chain_3")
        middle = LogCeleryMiddleSavedCalc.objects.get(name="x")
        middle_row = self.assert_log_row(middle, parent=root_row, contains="celery middle x")
        inner = LogInnerSavedCalc.objects.get(name="inner-from-x")
        self.assert_log_row(inner, parent=middle_row, contains="inner inner-from-x")
        self.assert_total_rows(3)

    # -- 15.74 ---------------------------------------------------------
    def test_15_74_a_decorated_root_saved_with_nothing_on_the_stack_gets_its_own_node(self):
        """
        Scenario 15.74: a decorated root nobody put on the stack is the node its lines go to
        Given: CELERY_ACTIVE with eager tasks; a calculation id and its audit
               row, but nothing on the model context stack: the root is saved
               by code that does not wrap the save in model_logging_context,
               as 15.68 does on the synchronous route; the root's calculate()
               is a @lex_shared_task
        When:  the root is saved with is_calculated=IN_PROGRESS
        Then:  its line is filed in one row keyed to the record, with no
               parent, rather than in a node that belongs to no record; and
               the run is closed in the audit trail as the root's: the run's
               audit entry names the record, with status success
        """
        self.assertIsNone(
            _model_context.get()["model_context"].current,
            "Precondition: nothing is on the model context stack.",
        )
        with _on_celery() as tasks:
            root = LogCeleryRootCalc(name="unframed", is_calculated=LogCeleryRootCalc.IN_PROGRESS)
            root.save()

        self.assertEqual(
            tasks, [LogCeleryRootCalc.calculate.name],
            f"Sanity: the root must have run as its own decorated task; Celery ran {tasks}.",
        )
        root.refresh_from_db()
        self.assertEqual(root.is_calculated, LogCeleryRootCalc.SUCCESS, "Sanity: the run must succeed.")
        self.assert_log_row(root, parent=None, contains="celery root unframed")
        self.assert_total_rows(1)

        audit_log = AuditLog.objects.get(calculation_id=self.calc_id)
        self.assertEqual(
            (audit_log.content_type_id, audit_log.object_id), (self._ct(root).pk, root.pk),
            "The run's audit entry must name the record the run calculated.",
        )
        self.assertEqual(
            list(AuditLogStatus.objects.filter(audit_log=audit_log).values_list("status", flat=True)),
            ["success"],
            "The run must be closed in the audit trail as a success.",
        )

    # -- 15.75 ---------------------------------------------------------
    def test_15_75_the_generic_task_gives_an_unframed_root_the_same_node(self):
        """
        Scenario 15.75: the generic Celery task agrees for a root nobody put on the stack
        Given: as in 15.74, nothing on the model context stack, but the root's
               calculate() is undecorated, so it runs in the generic
               calc_and_save task
        When:  the root is saved with is_calculated=IN_PROGRESS
        Then:  its line is filed in one row keyed to the record, with no
               parent, as on the synchronous route (15.68) and the decorated
               route (15.74): the task finds the root on top of the stack it
               was shipped with, and does not nest it under a copy of itself
        """
        self.assertIsNone(
            _model_context.get()["model_context"].current,
            "Precondition: nothing is on the model context stack.",
        )
        with _on_celery() as tasks:
            root = LogRootCalc(
                name="root-unframed-celery", child_mode="log_only", units_csv="",
                is_calculated=LogRootCalc.IN_PROGRESS,
            )
            root.save()

        self.assertEqual(
            tasks, [GENERIC_TASK],
            f"Sanity: the root must have run in the generic calc_and_save task; Celery ran {tasks}.",
        )
        self.assert_log_row(root, parent=None, contains="root root-unframed-celery")
        self.assert_total_rows(1)

    # -- 15.76 ---------------------------------------------------------
    def test_15_76_generic_task_a_handled_nested_failure_leaves_the_live_log_alone(self):
        """
        Scenario 15.76: on the generic Celery task, a nested failure the root handles does not end the run's live log
        Given: CELERY_ACTIVE with eager tasks; the root logs a line and saves
               a nested CalculationModel with an undecorated calculate(),
               dispatched to a calc_and_save task of its own, which logs and
               then fails; the root handles the failure, reads the run's live
               log the way the log panel's backfill does, and logs again; the
               live log is purged as on Redis
        When:  the root is saved
        Then:  the run succeeds, and the live log read after the failure
               still holds the root's first line: only the root ends the run's
               live log, when the whole run ends (cluster 9c). The tree has
               one root node, the root's, holding both of its lines
        """
        with _live_log_purged_like_redis(), _on_celery() as tasks:
            root = self._save_root(child_mode="failing_caught_read_live_log", units_csv="x")

        self.assertEqual(
            tasks.count(GENERIC_TASK), 2,
            "Sanity: the root and the nested model must each have run in a calc_and_save "
            f"task; Celery ran {tasks}.",
        )
        self.assertEqual(
            LogFailingInnerSavedCalc.objects.get(name="x").is_calculated,
            LogFailingInnerSavedCalc.ERROR,
            "Sanity: the nested model's run must fail.",
        )
        root.refresh_from_db()
        self.assertEqual(root.is_calculated, LogRootCalc.SUCCESS, "Sanity: the root must recover.")
        live_log = root.live_log_after_inner or ""
        self.assertIn(
            "root root-failing_caught_read_live_log", live_log,
            "Someone opening the log panel after the nested model failed must still "
            f"see what the root logged before it; the live log was {live_log!r}.",
        )
        root_row = self.assert_log_row(
            root, parent=None, contains="root root-failing_caught_read_live_log",
        )
        self.assertIn("root recovered", root_row.calculation_log)
        self._assert_one_root_node(root_row)

    # -- 15.77 ---------------------------------------------------------
    def test_15_77_decorated_task_a_handled_nested_failure_leaves_the_live_log_alone(self):
        """
        Scenario 15.77: on the decorated route, a nested failure the root handles does not end the run's live log
        Given: as in 15.76, but the nested model's calculate() is a
               @lex_shared_task, so it runs as its own decorated task, logs
               and then fails
        When:  the root is saved
        Then:  the live log read after the failure still holds the root's
               first line; the tree has one root node, the root's, holding
               both of its lines and not the nested model's line, which is
               filed under the nested model
        """
        with _live_log_purged_like_redis(), _on_celery() as tasks:
            root = self._save_root(child_mode="celery_failing_caught_read_live_log", units_csv="x")

        self.assertIn(
            LogCeleryFailingInnerSavedCalc.calculate.name, tasks,
            f"Sanity: the nested model must have run as its own decorated task; Celery ran {tasks}.",
        )
        self.assertEqual(
            LogCeleryFailingInnerSavedCalc.objects.get(name="x").is_calculated,
            LogCeleryFailingInnerSavedCalc.ERROR,
            "Sanity: the nested model's run must fail.",
        )
        root.refresh_from_db()
        self.assertEqual(root.is_calculated, LogRootCalc.SUCCESS, "Sanity: the root must recover.")
        live_log = root.live_log_after_inner or ""
        self.assertIn(
            "root root-celery_failing_caught_read_live_log", live_log,
            "Someone opening the log panel after the nested model failed must still "
            f"see what the root logged before it; the live log was {live_log!r}.",
        )
        root_row = self.assert_log_row(
            root, parent=None, contains="root root-celery_failing_caught_read_live_log",
        )
        self.assertIn("root recovered", root_row.calculation_log)
        self.assertNotIn(
            "celery failing inner x", root_row.calculation_log,
            "The nested model's line must be filed under the nested model, not under its caller.",
        )
        self._assert_one_root_node(root_row)
