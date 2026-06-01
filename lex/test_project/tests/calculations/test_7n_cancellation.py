"""Cluster 7n: cancellation of an in-progress calculation.

Intent
------
A long-running ``CalculationModel`` calculation must be cancellable
instantly from the UI when it is running on a Celery worker. The user
clicks **Abort**; the framework's contract (see
``docs/features/processing/calculations.md`` — the state machine
already advertises ``IN_PROGRESS → CANCELLED``) is:

* the running Celery task is revoked with SIGTERM (instant kill),
* the row transitions ``IN_PROGRESS → CANCELLED`` and persists,
* every descendant calculation that was dispatched from inside the
  cancelled parent (sharing its ``calculation_id``) is revoked and
  marked ``CANCELLED`` too — "abort" stops the whole tree, not just the
  entry point the button was attached to,
* a synchronously-running calculation (no ``task_id`` registered) is
  not cancellable in this design and surfaces a clear no-op response
  rather than silently lying.

A regression that lost any of these would leave a customer staring at
a "spinner" they cannot stop — the worst possible UX for a long
calculation that ran the wrong inputs.

Cluster 7n — scenarios 7.166–7.175. Type: I (TestCase — exercises the
``CalculationModel.cancel()`` public classmethod against real DB rows
and the in-memory active-state store; mocks only ``celery.current_app``
because the test environment has no broker).
Covers: ``lex/core/models/CalculationModel.py`` (``cancel``,
``_persist_cancelled``, ``_persist_cancelled_by_entry``,
``dispatch_calculation_task`` task_id capture, ``CalculationCancelled``),
``lex/core/signals/ActiveCalculationStateStore.py`` (``set_task_id``,
``get_task_id``, ``find_descendants``).
Run: python -m lex pytest lex/test_project/tests/calculations/test_7n_cancellation.py -v
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from django.test import TestCase

from lex.core.models.CalculationModel import CalculationCancelled, CalculationModel
from lex.core.signals.ActiveCalculationStateStore import ActiveCalculationStateStore
from lex.tests.e2e._e2e_test_case import E2ETestCase

from .models import ALL_MODELS, AtomicCalc, ChildCalc, ParentCalc

pytestmark = pytest.mark.calculations


def _record_id(instance) -> str:
    return f"{instance._meta.model_name}_{instance.pk}"


class TestCluster07n_Cancellation(E2ETestCase):
    """Cluster 7n: cancel() public API + recursive descendant cancel."""

    e2e_models = ALL_MODELS
    # We need the real ActiveCalculationStateStore.mark_in_progress to set
    # up IN_PROGRESS state in the store before calling cancel(). E2ETestCase
    # patches it out by default to keep happy-path tests deterministic.
    e2e_unpatch = {"mark_in_progress"}

    def setUp(self):
        super().setUp()
        ActiveCalculationStateStore.clear_all()
        # Patch celery's revoke so tests run without a broker. We capture
        # the calls to assert "did revoke fire?" at the assertion stage.
        self._revoke_patch = patch(
            "lex.core.models.CalculationModel.CalculationModel._revoke_celery_task"
        )
        self.revoke_mock = self._revoke_patch.start()

    def tearDown(self):
        self._revoke_patch.stop()
        ActiveCalculationStateStore.clear_all()
        super().tearDown()

    # ------------------------------------------------------------------
    # 7.166 — happy path: in-progress Celery calc revokes + persists CANCELLED
    # ------------------------------------------------------------------
    def test_07_166_cancel_in_progress_celery_calc_revokes_and_persists_cancelled(self):
        """
        Scenario 7.166: cancel() on an IN_PROGRESS Celery-dispatched calc.
        Given: an AtomicCalc row saved IN_PROGRESS with a Celery task_id
               registered in the active-state store.
        When:  CalculationModel.cancel(instance) is called.
        Then:  app.control.revoke(task_id, terminate=True, SIGTERM) fires,
               row persists is_calculated=CANCELLED, the report says
               cancelled=True with the task_id listed.
        """
        calc = AtomicCalc.objects.create(name="cancel-target")
        calc.is_calculated = CalculationModel.IN_PROGRESS
        calc.save(skip_hooks=True)
        ActiveCalculationStateStore.mark_in_progress(
            record_id=_record_id(calc),
            calculation_id="calc-abc",
            record=str(calc),
            model_label=calc._meta.label_lower,
            record_pk=calc.pk,
        )
        ActiveCalculationStateStore.set_task_id(_record_id(calc), "task-123")

        report = CalculationModel.cancel(calc)

        self.assertTrue(
            report["cancelled"],
            msg=f"cancel() should report cancelled=True, got {report!r}",
        )
        self.assertEqual(report["status"], CalculationModel.CANCELLED)
        self.assertEqual(report["revoked_tasks"], ["task-123"])
        self.revoke_mock.assert_called_once_with("task-123")
        calc.refresh_from_db()
        self.assertEqual(
            calc.is_calculated,
            CalculationModel.CANCELLED,
            msg="is_calculated must persist CANCELLED after cancel",
        )

    # ------------------------------------------------------------------
    # 7.167 — sync-dispatched calc (no task_id) is not cancellable
    # ------------------------------------------------------------------
    def test_07_167_sync_calc_without_task_id_reports_not_cancellable(self):
        """
        Scenario 7.167: cancel() on an IN_PROGRESS calc with NO task_id.
        Given: an IN_PROGRESS row that was never registered with a Celery
               task_id (synchronous execution path).
        When:  cancel() is called.
        Then:  cancelled=False, cancellable=False, reason explains why,
               the row stays IN_PROGRESS, no revoke fired.
        """
        calc = AtomicCalc.objects.create(name="sync-calc")
        calc.is_calculated = CalculationModel.IN_PROGRESS
        calc.save(skip_hooks=True)
        ActiveCalculationStateStore.mark_in_progress(
            record_id=_record_id(calc),
            calculation_id="calc-sync",
            record=str(calc),
            model_label=calc._meta.label_lower,
            record_pk=calc.pk,
        )
        # Deliberately do NOT call set_task_id.

        report = CalculationModel.cancel(calc)

        self.assertFalse(report["cancelled"], msg="sync calc must not report cancelled")
        self.assertFalse(report["cancellable"])
        self.assertEqual(report.get("reason"), "sync_calculation_not_cancellable")
        self.revoke_mock.assert_not_called()
        calc.refresh_from_db()
        self.assertEqual(
            calc.is_calculated,
            CalculationModel.IN_PROGRESS,
            msg="non-cancellable sync calc must remain IN_PROGRESS",
        )

    # ------------------------------------------------------------------
    # 7.168 — cancel() on terminal state is a clean no-op
    # ------------------------------------------------------------------
    def test_07_168_cancel_on_terminal_state_is_noop(self):
        """
        Scenario 7.168: cancel() on a row already in SUCCESS / ERROR / CANCELLED.
        Given: a row at SUCCESS (could equally be ERROR or CANCELLED).
        When:  cancel() is called.
        Then:  cancelled=False, status reflects the existing terminal
               state, no revoke fired — operation is idempotent.
        """
        calc = AtomicCalc.objects.create(name="already-done")
        calc.is_calculated = CalculationModel.SUCCESS
        calc.save(skip_hooks=True)

        report = CalculationModel.cancel(calc)

        self.assertFalse(report["cancelled"])
        self.assertFalse(report["cancellable"])
        self.assertEqual(report["status"], CalculationModel.SUCCESS)
        self.assertEqual(report.get("reason"), "not_in_progress")
        self.revoke_mock.assert_not_called()

    # ------------------------------------------------------------------
    # 7.169 — recursive cancel reaches descendants sharing calculation_id
    # ------------------------------------------------------------------
    def test_07_169_recursive_cancel_revokes_descendants_sharing_calculation_id(self):
        """
        Scenario 7.169: cancel() with recursive=True (default) cancels
        every active descendant that shares the parent's calculation_id.
        Given: a parent IN_PROGRESS calc with two child IN_PROGRESS calcs
               that were dispatched from inside the parent (so they were
               registered against the parent's calculation_id).
        When:  cancel(parent) is called.
        Then:  parent's task is revoked, BOTH children's tasks are
               revoked, all three rows persist CANCELLED, and
               descendants_cancelled == 2.
        """
        parent = ParentCalc.objects.create(name="recursive-parent")
        parent.is_calculated = CalculationModel.IN_PROGRESS
        parent.save(skip_hooks=True)
        child_a = ChildCalc.objects.create(name="child-a")
        child_a.is_calculated = CalculationModel.IN_PROGRESS
        child_a.save(skip_hooks=True)
        child_b = ChildCalc.objects.create(name="child-b")
        child_b.is_calculated = CalculationModel.IN_PROGRESS
        child_b.save(skip_hooks=True)

        shared_calc_id = "calc-tree-1"
        for inst, task in (
            (parent, "task-parent"),
            (child_a, "task-child-a"),
            (child_b, "task-child-b"),
        ):
            ActiveCalculationStateStore.mark_in_progress(
                record_id=_record_id(inst),
                calculation_id=shared_calc_id,
                record=str(inst),
                model_label=inst._meta.label_lower,
                record_pk=inst.pk,
            )
            ActiveCalculationStateStore.set_task_id(_record_id(inst), task)

        report = CalculationModel.cancel(parent)

        self.assertTrue(report["cancelled"])
        self.assertEqual(
            report["descendants_cancelled"],
            2,
            msg=f"both children must count toward descendants, got {report!r}",
        )
        self.assertEqual(
            sorted(report["revoked_tasks"]),
            ["task-child-a", "task-child-b", "task-parent"],
        )
        # Every row, parent + both children, persisted CANCELLED.
        for inst in (parent, child_a, child_b):
            inst.refresh_from_db()
            self.assertEqual(
                inst.is_calculated,
                CalculationModel.CANCELLED,
                msg=f"{inst.name} must be CANCELLED after recursive cancel",
            )

    # ------------------------------------------------------------------
    # 7.170 — recursive=False leaves descendants running
    # ------------------------------------------------------------------
    def test_07_170_non_recursive_cancel_leaves_descendants_running(self):
        """
        Scenario 7.170: cancel(recursive=False) targets only the entry point.
        Given: same parent + 2 children fixture as 7.169.
        When:  cancel(parent, recursive=False) is called.
        Then:  parent persists CANCELLED; the two children stay IN_PROGRESS
               and their Celery tasks are NOT revoked.
        """
        parent = ParentCalc.objects.create(name="lone-parent")
        parent.is_calculated = CalculationModel.IN_PROGRESS
        parent.save(skip_hooks=True)
        child = ChildCalc.objects.create(name="lone-child")
        child.is_calculated = CalculationModel.IN_PROGRESS
        child.save(skip_hooks=True)

        for inst, task in ((parent, "task-p"), (child, "task-c")):
            ActiveCalculationStateStore.mark_in_progress(
                record_id=_record_id(inst),
                calculation_id="calc-pair-1",
                record=str(inst),
                model_label=inst._meta.label_lower,
                record_pk=inst.pk,
            )
            ActiveCalculationStateStore.set_task_id(_record_id(inst), task)

        report = CalculationModel.cancel(parent, recursive=False)

        self.assertTrue(report["cancelled"])
        self.assertEqual(report["descendants_cancelled"], 0)
        self.assertEqual(report["revoked_tasks"], ["task-p"])
        self.revoke_mock.assert_called_once_with("task-p")
        parent.refresh_from_db()
        child.refresh_from_db()
        self.assertEqual(parent.is_calculated, CalculationModel.CANCELLED)
        self.assertEqual(
            child.is_calculated,
            CalculationModel.IN_PROGRESS,
            msg="non-recursive cancel must leave descendants untouched",
        )


class TestCluster07n_StateStoreTaskIdAndDescendants(TestCase):
    """7.171 / 7.172 — direct contract tests on ActiveCalculationStateStore."""

    def setUp(self):
        ActiveCalculationStateStore.clear_all()

    def tearDown(self):
        ActiveCalculationStateStore.clear_all()

    # ------------------------------------------------------------------
    # 7.171 — set_task_id preserves entry, mark_in_progress re-entry keeps task_id
    # ------------------------------------------------------------------
    def test_07_171_set_task_id_persists_and_survives_mark_in_progress_reentry(self):
        """
        Scenario 7.171: re-entrant ``calculate_hook`` must not orphan the task_id.
        Given: a record_id registered + a task_id attached.
        When:  mark_in_progress is called again for the same record_id
               (re-entrant hook or recovery path).
        Then:  the previously-stored task_id is preserved, not cleared.
        """
        record_id = "atomiccalc_42"
        ActiveCalculationStateStore.mark_in_progress(
            record_id=record_id,
            calculation_id="calc-X",
            record="foo",
            model_label="lex_app.atomiccalc",
            record_pk=42,
        )
        ActiveCalculationStateStore.set_task_id(record_id, "task-XYZ")
        self.assertEqual(ActiveCalculationStateStore.get_task_id(record_id), "task-XYZ")

        # Second mark_in_progress (e.g. hook re-entry) — must NOT drop task_id.
        ActiveCalculationStateStore.mark_in_progress(
            record_id=record_id,
            calculation_id="calc-X",
            record="foo",
            model_label="lex_app.atomiccalc",
            record_pk=42,
        )
        self.assertEqual(
            ActiveCalculationStateStore.get_task_id(record_id),
            "task-XYZ",
            msg="re-entrant mark_in_progress must preserve the registered task_id",
        )

    # ------------------------------------------------------------------
    # 7.172 — find_descendants returns every entry sharing the calculation_id
    # ------------------------------------------------------------------
    def test_07_172_find_descendants_groups_by_shared_calculation_id(self):
        """
        Scenario 7.172: descendants of a calc are those sharing its
        calculation_id — the same key used to group cache/audit entries.
        Given: three entries on calc-A and two entries on calc-B.
        When:  find_descendants("calc-A") is called.
        Then:  exactly the three calc-A entries come back; calc-B is
               not returned; empty calculation_id returns []; missing
               calculation_id returns [].
        """
        for label, pk, cid in (
            ("lex_app.parentcalc", 1, "calc-A"),
            ("lex_app.childcalc", 2, "calc-A"),
            ("lex_app.childcalc", 3, "calc-A"),
            ("lex_app.atomiccalc", 4, "calc-B"),
            ("lex_app.atomiccalc", 5, "calc-B"),
        ):
            model_name = label.split(".", 1)[1]
            ActiveCalculationStateStore.mark_in_progress(
                record_id=f"{model_name}_{pk}",
                calculation_id=cid,
                record=f"{model_name}_{pk}",
                model_label=label,
                record_pk=pk,
            )
        a = ActiveCalculationStateStore.find_descendants("calc-A")
        self.assertEqual(
            len(a), 3, msg=f"expected 3 calc-A descendants, got {a!r}"
        )
        self.assertTrue(all(e["calculation_id"] == "calc-A" for e in a))
        self.assertEqual(ActiveCalculationStateStore.find_descendants(""), [])
        self.assertEqual(ActiveCalculationStateStore.find_descendants("calc-missing"), [])


class TestCluster07n_CalculationCancelledException(TestCase):
    """7.173 — the cooperative ``CalculationCancelled`` marker."""

    def test_07_173_calculation_cancelled_exception_carries_reason(self):
        """
        Scenario 7.173: CalculationCancelled is an Exception with a reason.
        Given: code raises CalculationCancelled("user clicked abort").
        When:  the exception is caught.
        Then:  isinstance(exc, Exception), exc.reason matches, and str(exc)
               surfaces the reason — important because CallbackTask's
               failure logger writes str(exc) into the audit row.
        """
        try:
            raise CalculationCancelled("user clicked abort")
        except Exception as exc:  # noqa: BLE001 — intentional
            self.assertIsInstance(exc, CalculationCancelled)
            self.assertEqual(exc.reason, "user clicked abort")
            self.assertIn("user clicked abort", str(exc))


class TestCluster07n_InProcessCancellationLandsAborted(E2ETestCase):
    """7.174 / 7.175 — in-process exception paths must persist CANCELLED.

    Cancellation can reach the calculation through three distinct
    execution paths, each with its own machinery — covering one tells
    you nothing about the others:

    1. The cancel REST endpoint pre-emptively writes CANCELLED via
       ``_persist_cancelled`` — covered by 7.166.
    2. The Celery worker observes the revoke and
       ``CallbackTask.on_failure`` maps the exception onto CANCELLED —
       covered by cluster 8u.
    3. **The in-process exception path** — ``execute_calculation_sync``'s
       except branch, and the outer ``calculate_hook`` except branch —
       sees a cancellation exception bubble up (e.g. a nested sync calc
       raised ``CalculationCancelled`` cooperatively, or a Celery worker
       propagated ``Terminated`` synchronously into the dispatching
       thread). **These** scenarios cover path 3. A regression here
       would silently flip ``is_calculated`` to ``ERROR`` on top of the
       cancellation — which is the original bug that triggered the user
       report this scenario was added for.
    """

    e2e_models = ALL_MODELS

    def test_07_174_execute_calculation_sync_persists_cancelled_on_cancellation(self):
        """
        Scenario 7.174: ``execute_calculation_sync`` recognises a
        cancellation exception and writes CANCELLED instead of ERROR.

        Given: an IN_PROGRESS AtomicCalc whose user ``calculate()``
               raises ``CalculationCancelled`` (the cooperative marker
               that worker-side cancel signals also collapse onto).
        When:  ``execute_calculation_sync`` runs the calc.
        Then:  the row persists ``is_calculated=CANCELLED``, NOT ERROR.
        """
        calc = AtomicCalc.objects.create(name="in-proc-cancel-sync")
        calc.is_calculated = CalculationModel.IN_PROGRESS
        calc.save(skip_hooks=True)

        def _raise_cancel(self):
            raise CalculationCancelled("worker received SIGTERM")

        with patch.object(AtomicCalc, "calculate", _raise_cancel):
            with self.assertRaises(CalculationCancelled):
                calc.execute_calculation_sync()

        calc.refresh_from_db()
        self.assertEqual(
            calc.is_calculated,
            CalculationModel.CANCELLED,
            msg=(
                "in-process cancellation must land in CANCELLED — finding "
                "ERROR here is the original bug: the user pressed cancel "
                "but the audit shows a crash"
            ),
        )

    def test_07_175_calculate_hook_persists_cancelled_and_skips_error_chain(self):
        """
        Scenario 7.175: ``calculate_hook``'s top-level except recognises
        the cancellation and (a) writes CANCELLED on self, and (b) does
        NOT call ``persist_error_state`` on the calc_obj chain —
        descendants were already revoked + flipped to CANCELLED by the
        recursive cancel walk, and overwriting them with ERROR here
        would corrupt the terminal state of the whole tree.

        Given: an IN_PROGRESS AtomicCalc whose ``calculate()`` raises
               ``CalculationCancelled``.
        When:  ``calculate_hook`` runs (the wrapper that sits between
               ``save()`` and ``execute_calculation_sync``).
        Then:  the row persists CANCELLED, and ``persist_error_state``
               is NEVER called.
        """
        from lex.core.models.CalculationModel import CalculationModelException

        calc = AtomicCalc.objects.create(name="in-proc-cancel-hook")
        calc.is_calculated = CalculationModel.IN_PROGRESS
        calc.save(skip_hooks=True)

        def _raise_cancel(self):
            raise CalculationCancelled("revoked mid-flight")

        with patch.object(AtomicCalc, "calculate", _raise_cancel), patch.object(
            CalculationModel, "persist_error_state"
        ) as persist_error_mock:
            with self.assertRaises(CalculationModelException):
                calc.calculate_hook()

        persist_error_mock.assert_not_called()
        calc.refresh_from_db()
        self.assertEqual(
            calc.is_calculated,
            CalculationModel.CANCELLED,
            msg=(
                "calculate_hook must persist CANCELLED on cancellation — "
                "and must NOT cascade ERROR onto already-cancelled "
                "descendants via persist_error_state"
            ),
        )





