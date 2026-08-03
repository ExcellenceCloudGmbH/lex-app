"""Cluster 8c — Celery dispatch must not move edited_at / edited_by either.

Intent: cluster 7s pins the audit-column contract for Celery-OFF paths. The
same contract must hold when the calculation runs on a worker — and, crucially,
it must hold *identically for both Celery dispatch paths*. A calculation is
dispatched either through the generic ``calc_and_save`` task (undecorated
``calculate()``) or directly via ``func.delay`` (``@lex_shared_task``-decorated
``calculate()``). Whether a project happens to decorate its calculation method
is an implementation detail of that project; it must not change whether the
framework stamps audit columns.

This was BUG-028: ``calc_and_save`` wrapped the user's code in
``calculation_execution_context()`` but the ``lex_shared_task`` worker wrapper
did not, so a decorated ``calculate()`` stamped ``edited_at``/``edited_by`` even
on a clean SUCCESS while an identical undecorated one did not. Fixed by having
the wrapper enter the same context; these scenarios are now live regression
gates.

Cluster 8c — scenarios 8.145–8.152. Type: I.
Covers: lex/lex_app/celery_tasks.py (lex_shared_task wrapper, calc_and_save,
        CallbackTask status persistence),
        lex/core/models/CalculationModel.py (dispatch_calculation_task).
Run: python -m lex pytest lex/test_project/tests/celery_async/test_8c_calculation_audit_columns.py -v
"""

from __future__ import annotations

import time

import pytest

from lex.core.models.CalculationModel import CalculationModel
from lex.test_project.tests._e2e_test_case import E2ETestCase

from .models import (
    ALL_MODELS,
    AuditCeleryChild,
    AuditDecoratedCalc,
    AuditDecoratedFailingCalc,
    AuditDecoratedParentCalc,
    AuditUndecoratedCalc,
)
from .test_8h_eager_end_to_end import _celery_eager

pytestmark = pytest.mark.celery_async

class TestCluster08c_CalculationAuditColumns(E2ETestCase):
    """Cluster 8c: both Celery dispatch paths leave audit columns alone."""

    e2e_models = ALL_MODELS

    # -- helpers -------------------------------------------------------
    def _seed(self, model_cls, name, **kwargs):
        """Create a row and give it a real user edit so edited_at is populated."""
        item = model_cls.objects.create(name=name, **kwargs)
        item.name = f"{name}-edited"
        item.save()
        fresh = model_cls.objects.get(pk=item.pk)
        assert fresh.edited_at is not None, "fixture must start with edited_at set"
        return fresh.pk, fresh.edited_at, fresh.edited_by

    def _run_on_celery(self, model_cls, pk):
        """Trigger the calculation with Celery active (eager), then settle."""
        with _celery_eager():
            obj = model_cls.objects.get(pk=pk)
            obj.is_calculated = CalculationModel.IN_PROGRESS
            try:
                obj.save()
            except Exception:
                # failure paths surface the exception; terminal state still lands
                pass
        time.sleep(0.5)
        return model_cls.objects.get(pk=pk)

    # -- 8.145 — undecorated dispatch ----------------------------------
    def test_8_145_undecorated_celery_success_keeps_audit_columns(self) -> None:
        """
        Scenario 8.145: the generic calc_and_save path leaves the columns alone.
        Given: an undecorated calculate() that writes a field and saves
        When: it runs to SUCCESS on a Celery worker
        Then: edited_at and edited_by are unchanged
        """
        pk, before_at, before_by = self._seed(AuditUndecoratedCalc, "c145")
        after = self._run_on_celery(AuditUndecoratedCalc, pk)
        self.assertEqual(
            after.is_calculated, CalculationModel.SUCCESS,
            f"precondition: expected SUCCESS, got {after.is_calculated}.",
        )
        self.assertEqual(
            (after.edited_at, after.edited_by), (before_at, before_by),
            f"a Celery calculation is not a user edit; edited_at moved "
            f"{before_at} -> {after.edited_at}.",
        )

    # -- 8.146 / 8.147 — decorated dispatch ----------------------------
    def test_8_146_decorated_celery_success_keeps_edited_at(self) -> None:
        """
        Scenario 8.146: a decorated calculate() must not stamp edited_at.
        Given: a @lex_shared_task calculate() that writes a field and saves
        When: it runs to SUCCESS on a Celery worker
        Then: edited_at is unchanged — same as the undecorated path
        """
        pk, before_at, _ = self._seed(AuditDecoratedCalc, "c146")
        after = self._run_on_celery(AuditDecoratedCalc, pk)
        self.assertEqual(
            after.is_calculated, CalculationModel.SUCCESS,
            f"precondition: expected SUCCESS, got {after.is_calculated}.",
        )
        self.assertEqual(
            after.edited_at, before_at,
            f"a successful decorated calculation stamped edited_at "
            f"({before_at} -> {after.edited_at}) — an automated recalculation "
            f"is now indistinguishable from a human edit.",
        )

    def test_8_147_decorated_celery_success_keeps_edited_by(self) -> None:
        """
        Scenario 8.147: the same holds for edited_by.
        Given: the decorated calculate() above, with edited_by set to a
               distinct sentinel (a worker resolves the same fallback actor
               the fixture would otherwise carry, so an identical overwrite
               would be invisible — the sentinel makes the stamp detectable)
        When: it runs to SUCCESS on a Celery worker
        Then: edited_by is unchanged — the calc must not claim authorship
        """
        pk, _, _ = self._seed(AuditDecoratedCalc, "c147")
        sentinel = "a.human@example.com"
        # bypass hooks so the sentinel is not immediately re-resolved
        AuditDecoratedCalc.objects.filter(pk=pk).update(edited_by=sentinel)

        after = self._run_on_celery(AuditDecoratedCalc, pk)
        self.assertEqual(
            after.edited_by, sentinel,
            f"a decorated calculation reattributed edited_by "
            f"({sentinel!r} -> {after.edited_by!r}) — the audit trail now "
            f"credits the record's last change to the framework, not the user.",
        )

    # -- 8.148 — decorated failure --------------------------------------
    def test_8_148_decorated_celery_error_keeps_audit_columns(self) -> None:
        """
        Scenario 8.148: a failing decorated calculation leaves the columns alone.
        Given: a decorated calculate() that writes, saves, then raises
        When: it ends in a non-success terminal state on a worker
        Then: edited_at/edited_by are unchanged AND the field write is gone —
              the failure rolls the whole calculation back, so no partial state
              survives to be stamped.

        Note: this is protected by the rollback, not by the audit guard —
        the failed calculation's writes are rolled back. It is pinned so
        that if failure handling ever starts committing partial writes, the
        audit-column leak that would come with it is caught here.
        """
        pk, before_at, before_by = self._seed(AuditDecoratedFailingCalc, "c148")
        before_result = AuditDecoratedFailingCalc.objects.get(pk=pk).result

        after = self._run_on_celery(AuditDecoratedFailingCalc, pk)

        self.assertEqual(
            after.result, before_result,
            f"precondition: the failed calculation's write should have rolled "
            f"back; result {before_result} -> {after.result}.",
        )
        self.assertEqual(
            (after.edited_at, after.edited_by), (before_at, before_by),
            f"a failed decorated calculation stamped audit columns "
            f"(state={after.is_calculated}, edited_at {before_at} -> {after.edited_at}).",
        )

    # -- 8.149 — decorated, child output rows ---------------------------
    def test_8_149_decorated_celery_child_rows_keep_audit_columns(self) -> None:
        """
        Scenario 8.149: rows written as output of a decorated calculation are
        system writes, not user edits.
        Given: a pre-existing child row with edited_at set
        When: a decorated calculation updates and saves it on a worker
        Then: the child's audit columns are unchanged
        """
        child_pk, child_at, child_by = self._seed(AuditCeleryChild, "c149-child")
        pk, _, _ = self._seed(AuditDecoratedParentCalc, "c149", child_pk=child_pk)

        self._run_on_celery(AuditDecoratedParentCalc, pk)

        child = AuditCeleryChild.objects.get(pk=child_pk)
        self.assertEqual(
            child.payload, 1,
            "precondition: the calculation should have written the child.",
        )
        self.assertEqual(
            (child.edited_at, child.edited_by), (child_at, child_by),
            f"child rows written by a decorated calculation kept no audit "
            f"integrity; edited_at {child_at} -> {child.edited_at}.",
        )

    # -- 8.150 — terminal status write ----------------------------------
    def test_8_150_celery_terminal_status_write_keeps_audit_columns(self) -> None:
        """
        Scenario 8.150: persisting the terminal status is a framework write.
        Given: an undecorated calculation that settles on a worker
        When: the callback persists is_calculated (SUCCESS)
        Then: that status write did not stamp audit columns
        """
        pk, before_at, before_by = self._seed(AuditUndecoratedCalc, "c150")
        after = self._run_on_celery(AuditUndecoratedCalc, pk)
        self.assertIn(
            after.is_calculated,
            (CalculationModel.SUCCESS, CalculationModel.ERROR),
            f"precondition: expected a terminal state, got {after.is_calculated}.",
        )
        self.assertEqual(
            (after.edited_at, after.edited_by), (before_at, before_by),
            "writing the terminal calculation status must not stamp audit columns.",
        )

    # -- 8.151 — the invariant, stated directly -------------------------
    def test_8_151_decorated_and_undecorated_paths_agree(self) -> None:
        """
        Scenario 8.151: dispatch style must not change audit behaviour.
        Given: two models with byte-identical calculate() bodies, one decorated
        When: both run to SUCCESS on a Celery worker
        Then: both leave edited_at untouched — whether a project decorated its
              calculation method is not an audit-visible decision
        """
        un_pk, un_before, _ = self._seed(AuditUndecoratedCalc, "c151-un")
        de_pk, de_before, _ = self._seed(AuditDecoratedCalc, "c151-de")

        un_after = self._run_on_celery(AuditUndecoratedCalc, un_pk)
        de_after = self._run_on_celery(AuditDecoratedCalc, de_pk)

        undecorated_moved = un_after.edited_at != un_before
        decorated_moved = de_after.edited_at != de_before
        self.assertEqual(
            decorated_moved, undecorated_moved,
            f"identical calculate() bodies must produce identical audit "
            f"behaviour; undecorated moved={undecorated_moved}, "
            f"decorated moved={decorated_moved}.",
        )

    # -- 8.153 — decoration alone is not the trigger --------------------
    def test_8_153_decorated_calc_with_celery_off_keeps_audit_columns(self) -> None:
        """
        Scenario 8.153: a decorated calculate() is clean when Celery is OFF.
        Given: a @lex_shared_task-decorated calculate() that saves the record,
               with edited_by set to a distinct sentinel, and Celery inactive
        When: the calculation runs (falling back to synchronous execution)
        Then: neither column moves — the sync fallback wraps the body in
              calculation_execution_context() regardless of decoration

        This pins the boundary: the stamp required decoration *and*
        Celery dispatch, not decoration by itself. It also guards the fix —
        unifying the dispatch paths must not disturb the sync fallback that
        already works.
        """
        pk, before_at, _ = self._seed(AuditDecoratedCalc, "c153")
        sentinel = "a.human@example.com"
        AuditDecoratedCalc.objects.filter(pk=pk).update(edited_by=sentinel)

        # NOTE: no _celery_eager() here — this is the Celery-inactive path.
        obj = AuditDecoratedCalc.objects.get(pk=pk)
        obj.is_calculated = CalculationModel.IN_PROGRESS
        obj.save()
        time.sleep(0.5)

        after = AuditDecoratedCalc.objects.get(pk=pk)
        self.assertEqual(
            after.is_calculated, CalculationModel.SUCCESS,
            f"precondition: expected SUCCESS, got {after.is_calculated}.",
        )
        self.assertEqual(
            (after.edited_at, after.edited_by), (before_at, sentinel),
            f"a decorated calculation running WITHOUT Celery must not stamp "
            f"audit columns; edited_at {before_at} -> {after.edited_at}, "
            f"edited_by {sentinel!r} -> {after.edited_by!r}.",
        )

    # -- 8.152 — NEGATIVE CONTROL ---------------------------------------
    def test_8_152_user_edit_under_celery_still_stamps(self) -> None:
        """
        Scenario 8.152: suppression must not leak into real edits under Celery.
        Given: a record whose calculation has run on a worker
        When: a user then changes a project-defined field
        Then: edited_at moves — the audit trail still records human edits
        """
        pk, _, _ = self._seed(AuditUndecoratedCalc, "c152")
        self._run_on_celery(AuditUndecoratedCalc, pk)
        after_calc = AuditUndecoratedCalc.objects.get(pk=pk).edited_at

        obj = AuditUndecoratedCalc.objects.get(pk=pk)
        obj.name = "c152-user-edit"
        obj.save()

        after = AuditUndecoratedCalc.objects.get(pk=pk)
        self.assertNotEqual(
            after.edited_at, after_calc,
            "a genuine user edit after a Celery calculation MUST stamp "
            "edited_at — suppression has leaked beyond the calculation.",
        )
