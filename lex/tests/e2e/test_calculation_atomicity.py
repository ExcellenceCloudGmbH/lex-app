"""
E2E Test: Calculation Atomicity

Focused tests for CalculationModel atomicity guarantees:

1. ``is_atomic=True`` (default) — failure rolls back all DB changes.
2. ``is_atomic=False`` — partial results persist even on failure.
3. Nested parent→child calculations — each tracks state independently.
4. ``post_validation`` rollback — model reverts to snapshot after failure.
"""

import os
from unittest.mock import patch

from django.db import connection, models
from django.test import TransactionTestCase

from lex.api.utils import OperationContext
from lex.audit_logging.utils.ModelContext import model_logging_context
from lex.core.exceptions import ValidationError
from lex.core.models.CalculationModel import (
    CalculationModel,
    CalculationModelException,
)
from lex.core.models.LexModel import LexModel, PermissionResult
from lex.tests.e2e._e2e_test_case import E2ETestCase


# ====================================================================
#  Test models
# ====================================================================


class AtomicCalcModel(CalculationModel):
    """Default is_atomic=True — failure rolls back."""

    value = models.FloatField(default=0)
    intermediate = models.FloatField(default=0)
    calculation_error_message = models.TextField(blank=True, default="")

    is_atomic = True
    class Meta:
        app_label = "lex_app"

    def calculate(self):
        # Write intermediate result, then fail
        self.intermediate = 42
        raise ValueError("Intentional failure after partial write")

    def permission_read(self, uc):
        return PermissionResult.allow_all("test")

    def permission_edit(self, uc):
        return PermissionResult.allow_all("test")

    def permission_create(self, uc):
        return True

    def permission_delete(self, uc):
        return True


class NonAtomicCalcModel(CalculationModel):
    """is_atomic=False — partial results persist."""

    value = models.FloatField(default=0)
    intermediate = models.FloatField(default=0)
    is_atomic = False
    calculation_error_message = models.TextField(blank=True, default="")

    class Meta:
        app_label = "lex_app"

    def calculate(self):
        self.intermediate = 99
        raise ValueError("Intentional failure — partial data should persist")

    def permission_read(self, uc):
        return PermissionResult.allow_all("test")

    def permission_edit(self, uc):
        return PermissionResult.allow_all("test")

    def permission_create(self, uc):
        return True

    def permission_delete(self, uc):
        return True


class SuccessfulCalcModel(CalculationModel):
    """A calculation that always succeeds — for happy-path tests."""

    input_value = models.FloatField(default=0)
    result = models.FloatField(default=0)
    calculation_error_message = models.TextField(blank=True, default="")

    class Meta:
        app_label = "lex_app"

    def calculate(self):
        self.result = self.input_value * 2

    def permission_read(self, uc):
        return PermissionResult.allow_all("test")

    def permission_edit(self, uc):
        return PermissionResult.allow_all("test")

    def permission_create(self, uc):
        return True

    def permission_delete(self, uc):
        return True


class ChildCalcModel(CalculationModel):
    """Child calculation used in nested parent→child tests."""

    factor = models.FloatField(default=0)
    output = models.FloatField(default=0)
    calculation_error_message = models.TextField(blank=True, default="")

    class Meta:
        app_label = "lex_app"

    def calculate(self):
        self.output = self.factor * 10

    def permission_read(self, uc):
        return PermissionResult.allow_all("test")

    def permission_edit(self, uc):
        return PermissionResult.allow_all("test")

    def permission_create(self, uc):
        return True

    def permission_delete(self, uc):
        return True


class ParentCalcModel(CalculationModel):
    """Parent that triggers child calculations inside model_logging_context."""

    total = models.FloatField(default=0)
    child_count = models.IntegerField(default=0)
    calculation_error_message = models.TextField(blank=True, default="")

    class Meta:
        app_label = "lex_app"

    def calculate(self):
        children = ChildCalcModel.objects.all()
        self.child_count = children.count()
        total = 0
        for child in children:
            with model_logging_context(child):
                child.is_calculated = CalculationModel.IN_PROGRESS
                child.save()
            child.refresh_from_db()
            total += child.output
        self.total = total

    def permission_read(self, uc):
        return PermissionResult.allow_all("test")

    def permission_edit(self, uc):
        return PermissionResult.allow_all("test")

    def permission_create(self, uc):
        return True

    def permission_delete(self, uc):
        return True


class PostValidationFund(LexModel):
    """A model with a post_validation that fires after save."""

    name = models.CharField(max_length=200)
    budget = models.FloatField(default=0)
    budget_limit = models.FloatField(default=100_000)

    class Meta:
        app_label = "lex_app"

    def post_validation(self):
        if self.budget > self.budget_limit:
            raise ValueError(
                f"Budget {self.budget} exceeds limit {self.budget_limit}."
            )

    def permission_read(self, uc):
        return PermissionResult.allow_all("test")

    def permission_edit(self, uc):
        return PermissionResult.allow_all("test")

    def permission_create(self, uc):
        return True

    def permission_delete(self, uc):
        return True


ALL_MODELS = [
    AtomicCalcModel,
    NonAtomicCalcModel,
    SuccessfulCalcModel,
    ChildCalcModel,
    ParentCalcModel,
    PostValidationFund,
]


# ====================================================================
#  Tests
# ====================================================================


class TestAtomicCalculation(E2ETestCase):
    """Default is_atomic=True — failure sets ERROR and preserves error details."""

    e2e_models = ALL_MODELS

    def test_atomic_failure_sets_error_state(self):
        """Atomic calculation failure → ERROR state with error message.

        The object must end in ERROR state and the error message must
        be persisted for diagnostics.
        """
        with OperationContext(calculation_id="atomic-fail"):
            obj = AtomicCalcModel.objects.create(value=10)
            with self.assertRaises(CalculationModelException):
                with model_logging_context(obj):
                    obj.is_calculated = CalculationModel.IN_PROGRESS
                    obj.save()

        obj.refresh_from_db()
        self.assertEqual(obj.is_calculated, CalculationModel.ERROR)
        self.assertIn("Intentional failure", obj.calculation_error_message)


class TestNonAtomicCalculation(E2ETestCase):
    """is_atomic=False — partial results persist despite failure."""

    e2e_models = ALL_MODELS

    def test_non_atomic_failure_preserves_partial(self):
        """Non-atomic failure → intermediate=99 (persisted)."""
        with OperationContext(calculation_id="non-atomic-fail"):
            obj = NonAtomicCalcModel.objects.create(value=10)
            with self.assertRaises(CalculationModelException):
                with model_logging_context(obj):
                    obj.is_calculated = CalculationModel.IN_PROGRESS
                    obj.save()

        obj.refresh_from_db()
        self.assertEqual(obj.is_calculated, CalculationModel.ERROR)
        self.assertEqual(obj.intermediate, 99)


class TestSuccessfulCalculation(E2ETestCase):
    """Happy path — calculation completes and persists result."""

    e2e_models = ALL_MODELS

    def test_success_persists_result(self):
        """Successful calculation → result=20, status=SUCCESS."""
        with OperationContext(calculation_id="calc-success"):
            obj = SuccessfulCalcModel.objects.create(input_value=10)
            with model_logging_context(obj):
                obj.is_calculated = CalculationModel.IN_PROGRESS
                obj.save()

        obj.refresh_from_db()
        self.assertEqual(obj.is_calculated, CalculationModel.SUCCESS)
        self.assertEqual(obj.result, 20)


class TestNestedCalculation(E2ETestCase):
    """Parent triggers child calculations; both succeed independently."""

    e2e_models = ALL_MODELS

    def test_parent_triggers_children(self):
        """Parent calculates total from 3 child outputs."""
        with OperationContext(actor="Analyst"):
            ChildCalcModel.objects.create(factor=1)
            ChildCalcModel.objects.create(factor=2)
            ChildCalcModel.objects.create(factor=3)
            parent = ParentCalcModel.objects.create()

        with OperationContext(calculation_id="nested-calc"):
            with model_logging_context(parent):
                parent.is_calculated = CalculationModel.IN_PROGRESS
                parent.save()

        parent.refresh_from_db()
        self.assertEqual(parent.is_calculated, CalculationModel.SUCCESS)
        self.assertEqual(parent.child_count, 3)
        self.assertEqual(parent.total, 60)  # (1+2+3)*10

        # Each child also succeeded independently
        for child in ChildCalcModel.objects.all():
            self.assertEqual(child.is_calculated, CalculationModel.SUCCESS)
            self.assertGreater(child.output, 0)

    def test_parent_with_no_children(self):
        """Parent with no children → total=0, child_count=0."""
        with OperationContext(actor="PM"):
            parent = ParentCalcModel.objects.create()

        with OperationContext(calculation_id="empty-parent"):
            with model_logging_context(parent):
                parent.is_calculated = CalculationModel.IN_PROGRESS
                parent.save()

        parent.refresh_from_db()
        self.assertEqual(parent.is_calculated, CalculationModel.SUCCESS)
        self.assertEqual(parent.total, 0)
        self.assertEqual(parent.child_count, 0)


class TestPostValidationRollback(E2ETestCase):
    """post_validation raises → model reverts to pre-save snapshot."""

    e2e_models = ALL_MODELS

    def test_over_budget_rolls_back(self):
        """Budget exceeds limit → post_validation rolls back."""
        with OperationContext(actor="User"):
            fund = PostValidationFund.objects.create(
                name="Growth Fund", budget=50_000, budget_limit=100_000,
            )
        with OperationContext(actor="User"):
            with self.assertRaises(ValidationError):
                fund.budget = 150_000
                fund.save()

        fund.refresh_from_db()
        self.assertEqual(fund.budget, 50_000)  # reverted

    def test_within_budget_succeeds(self):
        """Budget within limit → save succeeds."""
        with OperationContext(actor="User"):
            fund = PostValidationFund.objects.create(
                name="Safe Fund", budget=30_000, budget_limit=100_000,
            )
        with OperationContext(actor="User"):
            fund.budget = 90_000
            fund.save()

        fund.refresh_from_db()
        self.assertEqual(fund.budget, 90_000)


# ====================================================================
#  History-tracking models (for TestCalculationHistoryInAtomicity)
# ====================================================================


class HistChild(CalculationModel):
    """Child calculation with configurable failure for history tests."""

    factor = models.FloatField(default=1)
    output = models.FloatField(default=0)
    should_fail = models.BooleanField(default=False)
    calculation_error_message = models.TextField(blank=True, default="")
    is_atomic = True

    class Meta:
        app_label = "lex_app"

    def calculate(self):
        if self.should_fail:
            raise ValueError("Child configured to fail")
        self.output = self.factor * 10

    def permission_read(self, uc):
        return PermissionResult.allow_all("test")

    def permission_edit(self, uc):
        return PermissionResult.allow_all("test")

    def permission_create(self, uc):
        return True

    def permission_delete(self, uc):
        return True


class HistParent(CalculationModel):
    """Atomic parent (default) that triggers HistChild calculations."""

    total = models.FloatField(default=0)
    calculation_error_message = models.TextField(blank=True, default="")

    is_atomic = True
    class Meta:
        app_label = "lex_app"

    def calculate(self):
        children = HistChild.objects.all()
        total = 0
        for child in children:
            with model_logging_context(child):
                child.is_calculated = CalculationModel.IN_PROGRESS
                child.save()
            child.refresh_from_db()
            total += child.output
        self.total = total

    def permission_read(self, uc):
        return PermissionResult.allow_all("test")

    def permission_edit(self, uc):
        return PermissionResult.allow_all("test")

    def permission_create(self, uc):
        return True

    def permission_delete(self, uc):
        return True


class HistNonAtomicParent(CalculationModel):
    """Non-atomic parent — no transaction.atomic() wrapper in execute_calculation_sync."""

    total = models.FloatField(default=0)
    is_atomic = False
    calculation_error_message = models.TextField(blank=True, default="")
    class Meta:
        app_label = "lex_app"

    def calculate(self):
        children = HistChild.objects.all()
        total = 0
        for child in children:
            with model_logging_context(child):
                child.is_calculated = CalculationModel.IN_PROGRESS
                child.save()
            child.refresh_from_db()
            total += child.output
        self.total = total

    def permission_read(self, uc):
        return PermissionResult.allow_all("test")

    def permission_edit(self, uc):
        return PermissionResult.allow_all("test")

    def permission_create(self, uc):
        return True

    def permission_delete(self, uc):
        return True


class HistGrandparent(CalculationModel):
    """Top-level calc in a 3-level hierarchy: Grandparent → HistParent → HistChild."""

    grand_total = models.FloatField(default=0)
    calculation_error_message = models.TextField(blank=True, default="")
    is_atomic = True
    class Meta:
        app_label = "lex_app"

    def calculate(self):
        parents = HistParent.objects.all()
        grand_total = 0
        for parent in parents:
            with model_logging_context(parent):
                parent.is_calculated = CalculationModel.IN_PROGRESS
                parent.save()
            parent.refresh_from_db()
            grand_total += parent.total
        self.grand_total = grand_total

    def permission_read(self, uc):
        return PermissionResult.allow_all("test")

    def permission_edit(self, uc):
        return PermissionResult.allow_all("test")

    def permission_create(self, uc):
        return True

    def permission_delete(self, uc):
        return True


HISTORY_MODELS = [HistChild, HistParent, HistNonAtomicParent, HistGrandparent]


# ====================================================================
#  Calculation History × Atomicity Tests
# ====================================================================


class TestCalculationHistoryInAtomicity(E2ETestCase):
    """
    Verify that calculation state transitions create durable history
    entries and that atomicity does NOT revert the state-machine history.

    Correct behaviour
    -----------------
    Every calculation must leave a full audit trail in history:

        NOT_CALCULATED → IN_PROGRESS → SUCCESS / ERROR

    * **IN_PROGRESS** must be committed *before* ``calculate()`` runs,
      so the UI can show a spinner and history records the attempt.
    * **SUCCESS / ERROR** must be committed *after* ``calculate()``
      finishes (or fails), outside the calculation's atomic block.
    * Atomicity (``is_atomic``) controls whether the *data* changes
      made inside ``calculate()`` are rolled back — it must **never**
      roll back the state-machine history entries.

    Known framework bug (April 2026)
    ---------------------------------
    ``LexModel.save()`` wraps IN_PROGRESS write **and** ``calculate_hook``
    in a single ``transaction.atomic()``.  On failure the exception
    propagates out, the atomic rolls back, and the IN_PROGRESS history
    record is erased.  Tests below assert the *correct* behaviour —
    they will FAIL until the framework is fixed to commit IN_PROGRESS
    before entering the calculation's atomic block.

    No duplicate history rows
    -------------------------
    Regardless of success or failure, each state must appear at most
    once per calculation cycle.
    """

    e2e_models = HISTORY_MODELS

    # ── helpers ──────────────────────────────────────────────────────

    def _history_states(self, instance):
        """Return chronologically ordered list of ``is_calculated`` values."""
        return list(
            instance.history
            .order_by("history_id")
            .values_list("is_calculated", flat=True)
        )

    def _trigger_calc(self, instance):
        """Set IN_PROGRESS → save (django-lifecycle triggers calculate_hook)."""
        with model_logging_context(instance):
            instance.is_calculated = CalculationModel.IN_PROGRESS
            instance.save()

    # ── Single model ─────────────────────────────────────────────────

    def test_single_successful_calc_history_shows_progression(self):
        """
        Successful atomic calculation produces history entries that show
        the full state machine: NOT_CALCULATED → IN_PROGRESS → SUCCESS.
        """
        with OperationContext(calculation_id="hist-single-ok"):
            child = HistChild.objects.create(factor=5)
            self._trigger_calc(child)

        child.refresh_from_db()
        self.assertEqual(child.is_calculated, CalculationModel.SUCCESS)
        self.assertEqual(child.output, 50)

        states = self._history_states(child)
        self.assertEqual(states[0], CalculationModel.NOT_CALCULATED,
                         "First history entry is the create (NOT_CALCULATED)")
        self.assertIn(CalculationModel.IN_PROGRESS, states,
                      "IN_PROGRESS must appear in history")
        self.assertEqual(states[-1], CalculationModel.SUCCESS,
                         "Last history entry must be SUCCESS")

    def test_single_failed_calc_history_preserves_in_progress(self):
        """
        Failed atomic calculation must still show IN_PROGRESS in history.

        Correct history: NOT_CALCULATED → IN_PROGRESS → ERROR

        Atomicity should roll back *data* changes from calculate(),
        not the state-machine transition itself.
        """
        with OperationContext(calculation_id="hist-single-fail"):
            child = HistChild.objects.create(factor=5, should_fail=True)
            with self.assertRaises(CalculationModelException):
                self._trigger_calc(child)

        child.refresh_from_db()
        self.assertEqual(child.is_calculated, CalculationModel.ERROR)

        states = self._history_states(child)
        self.assertEqual(states[0], CalculationModel.NOT_CALCULATED)
        self.assertIn(CalculationModel.IN_PROGRESS, states,
                      "IN_PROGRESS must survive in history — "
                      "atomicity must not revert the state-machine history")
        self.assertIn(CalculationModel.ERROR, states,
                      "ERROR must appear in history")
        self.assertLess(
            states.index(CalculationModel.IN_PROGRESS),
            states.index(CalculationModel.ERROR),
            "IN_PROGRESS must precede ERROR in history",
        )
        # No duplicates
        self.assertEqual(states.count(CalculationModel.IN_PROGRESS), 1,
                         f"Duplicate IN_PROGRESS: {states}")
        self.assertEqual(states.count(CalculationModel.ERROR), 1,
                         f"Duplicate ERROR: {states}")

    # ── Parent → Children: all succeed ───────────────────────────────

    def test_parent_children_all_succeed_full_history(self):
        """
        Atomic parent + 2 children, all succeed:
        the transaction commits, so every level retains
        IN_PROGRESS → SUCCESS in history.
        """
        with OperationContext(actor="Analyst"):
            HistChild.objects.create(factor=2)
            HistChild.objects.create(factor=3)
            parent = HistParent.objects.create()

        with OperationContext(calculation_id="hist-parent-ok"):
            self._trigger_calc(parent)

        parent.refresh_from_db()
        self.assertEqual(parent.is_calculated, CalculationModel.SUCCESS)
        self.assertEqual(parent.total, 50)  # (2+3)*10

        # Parent history
        parent_states = self._history_states(parent)
        self.assertIn(CalculationModel.IN_PROGRESS, parent_states)
        self.assertEqual(parent_states[-1], CalculationModel.SUCCESS)

        # Children history — transaction committed → all entries preserved
        for child in HistChild.objects.all():
            child_states = self._history_states(child)
            self.assertIn(CalculationModel.IN_PROGRESS, child_states,
                          "Child must have IN_PROGRESS in history after successful parent calc")
            self.assertEqual(child_states[-1], CalculationModel.SUCCESS,
                             "Child must end with SUCCESS in history")

    # ── Parent → Child fails (atomic parent) ─────────────────────────

    def test_atomic_parent_child_fails_parent_history_preserved(self):
        """
        Atomic parent, child raises in calculate().

        Correct history for parent: NOT_CALCULATED → IN_PROGRESS → ERROR
        Child must end in ERROR state.

        The IN_PROGRESS history entry must survive — atomicity should
        only roll back the data changes from calculate(), not the
        state-machine transitions.
        """
        with OperationContext(actor="PM"):
            HistChild.objects.create(factor=5, should_fail=True)
            parent = HistParent.objects.create()

        with OperationContext(calculation_id="hist-parent-fail"):
            with self.assertRaises(CalculationModelException):
                self._trigger_calc(parent)

        parent.refresh_from_db()
        self.assertEqual(parent.is_calculated, CalculationModel.ERROR)

        parent_states = self._history_states(parent)
        self.assertIn(CalculationModel.IN_PROGRESS, parent_states,
                      "Parent IN_PROGRESS must survive in history")
        self.assertIn(CalculationModel.ERROR, parent_states,
                      "Parent ERROR must appear in history")
        self.assertLess(
            parent_states.index(CalculationModel.IN_PROGRESS),
            parent_states.index(CalculationModel.ERROR),
        )

        # Child ends up in ERROR
        child = HistChild.objects.first()
        child.refresh_from_db()
        self.assertEqual(child.is_calculated, CalculationModel.ERROR,
                         "Child must be ERROR")

    # ── Parent → Child fails (non-atomic parent) ─────────────────────

    def test_non_atomic_parent_atomic_child_fails_both_history_preserved(self):
        """
        Non-atomic parent (``is_atomic=False``), atomic child (default).
        Child's calculate() raises → parent re-raises.

        Correct history for BOTH parent and child:
            NOT_CALCULATED → IN_PROGRESS → ERROR

        The IN_PROGRESS entry must survive for both levels. Atomicity
        controls whether *data* changes in calculate() are rolled back,
        not the state-machine history.
        """
        with OperationContext(actor="Trader"):
            HistChild.objects.create(factor=7, should_fail=True)
            parent = HistNonAtomicParent.objects.create()

        with OperationContext(calculation_id="hist-nonatom-fail"):
            with self.assertRaises(CalculationModelException):
                self._trigger_calc(parent)

        # ── Parent ───────────────────────────────────────────────────
        parent.refresh_from_db()
        self.assertEqual(parent.is_calculated, CalculationModel.ERROR)

        parent_states = self._history_states(parent)
        self.assertIn(CalculationModel.IN_PROGRESS, parent_states,
                      "Parent IN_PROGRESS must survive in history")
        self.assertIn(CalculationModel.ERROR, parent_states,
                      "Parent ERROR must appear in history")
        self.assertEqual(parent_states.count(CalculationModel.IN_PROGRESS), 1,
                         f"Duplicate parent IN_PROGRESS: {parent_states}")
        self.assertEqual(parent_states.count(CalculationModel.ERROR), 1,
                         f"Duplicate parent ERROR: {parent_states}")

        # ── Child ────────────────────────────────────────────────────
        child = HistChild.objects.first()
        child.refresh_from_db()
        self.assertEqual(child.is_calculated, CalculationModel.ERROR)

        child_states = self._history_states(child)
        self.assertIn(CalculationModel.IN_PROGRESS, child_states,
                      "Child IN_PROGRESS must survive in history")
        self.assertIn(CalculationModel.ERROR, child_states,
                      "Child ERROR must appear in history")
        self.assertEqual(child_states.count(CalculationModel.IN_PROGRESS), 1,
                         f"Duplicate child IN_PROGRESS: {child_states}")
        self.assertEqual(child_states.count(CalculationModel.ERROR), 1,
                         f"Duplicate child ERROR: {child_states}")

    # ── Atomic parent, mixed children (one succeeds, one fails) ──────

    def test_atomic_parent_mixed_children_one_fails(self):
        """
        Atomic parent with child₁ (succeeds) then child₂ (fails).

        Correct parent history: NOT_CALCULATED → IN_PROGRESS → ERROR.
        IN_PROGRESS must survive — atomicity only rolls back data.
        """
        with OperationContext(actor="PM"):
            HistChild.objects.create(factor=2, should_fail=False)
            HistChild.objects.create(factor=5, should_fail=True)
            parent = HistParent.objects.create()

        with OperationContext(calculation_id="hist-mixed"):
            with self.assertRaises(CalculationModelException):
                self._trigger_calc(parent)

        parent.refresh_from_db()
        self.assertEqual(parent.is_calculated, CalculationModel.ERROR)

        parent_states = self._history_states(parent)
        self.assertIn(CalculationModel.IN_PROGRESS, parent_states,
                      "Parent IN_PROGRESS must survive in history")
        self.assertIn(CalculationModel.ERROR, parent_states,
                      "Parent ERROR must appear in history")

    # ── Three-level hierarchy: all succeed ───────────────────────────

    def test_three_level_all_succeed_history_at_every_level(self):
        """
        Grandparent → Parent → 2 Children, all succeed:
        every level shows IN_PROGRESS → SUCCESS in history.
        """
        with OperationContext(actor="Analyst"):
            HistChild.objects.create(factor=1)
            HistChild.objects.create(factor=2)
            HistParent.objects.create()
            gp = HistGrandparent.objects.create()

        with OperationContext(calculation_id="hist-3lvl-ok"):
            self._trigger_calc(gp)

        gp.refresh_from_db()
        self.assertEqual(gp.is_calculated, CalculationModel.SUCCESS)
        self.assertEqual(gp.grand_total, 30)  # (1+2)*10

        # Grandparent history
        gp_states = self._history_states(gp)
        self.assertIn(CalculationModel.IN_PROGRESS, gp_states)
        self.assertEqual(gp_states[-1], CalculationModel.SUCCESS)

        # Parent history
        parent = HistParent.objects.first()
        parent_states = self._history_states(parent)
        self.assertIn(CalculationModel.IN_PROGRESS, parent_states)
        self.assertEqual(parent_states[-1], CalculationModel.SUCCESS)

        # Children history
        for child in HistChild.objects.all():
            child_states = self._history_states(child)
            self.assertIn(CalculationModel.IN_PROGRESS, child_states)
            self.assertEqual(child_states[-1], CalculationModel.SUCCESS)

    # ── Three-level hierarchy: leaf fails ────────────────────────────

    def test_three_level_leaf_fails_root_history_preserved(self):
        """
        Grandparent → Parent → Child(fails):
        cascading CalculationModelException propagates upward.

        Correct root history: NOT_CALCULATED → IN_PROGRESS → ERROR.
        IN_PROGRESS must survive at the root level.
        Intermediate and leaf must end in ERROR.
        """
        with OperationContext(actor="Risk"):
            HistChild.objects.create(factor=3, should_fail=True)
            HistParent.objects.create()
            gp = HistGrandparent.objects.create()

        with OperationContext(calculation_id="hist-3lvl-fail"):
            with self.assertRaises(CalculationModelException):
                self._trigger_calc(gp)

        gp.refresh_from_db()
        self.assertEqual(gp.is_calculated, CalculationModel.ERROR)

        gp_states = self._history_states(gp)
        self.assertIn(CalculationModel.IN_PROGRESS, gp_states,
                      "Root IN_PROGRESS must survive in history")
        self.assertIn(CalculationModel.ERROR, gp_states,
                      "Root ERROR must appear in history")

        # Intermediate (parent) ends up in ERROR
        parent = HistParent.objects.first()
        parent.refresh_from_db()
        self.assertEqual(parent.is_calculated, CalculationModel.ERROR,
                         "Intermediate model must reach ERROR")

        # Leaf (child) also ends up in ERROR
        child = HistChild.objects.first()
        child.refresh_from_db()
        self.assertEqual(child.is_calculated, CalculationModel.ERROR,
                         "Leaf model must reach ERROR")

    # ── Non-atomic parent + atomic child: exact history rows ─────────

    def test_non_atomic_parent_atomic_child_fails_exact_history(self):
        """
        Non-atomic parent (``is_atomic=False``) triggers an atomic child.
        Child's ``calculate()`` raises → parent re-raises.

        Correct history (no duplicates):

        Parent:  NOT_CALCULATED → IN_PROGRESS → ERROR   (3 entries)
        Child:   NOT_CALCULATED → IN_PROGRESS → ERROR   (3 entries)

        IN_PROGRESS must survive for both levels.
        """
        with OperationContext(actor="PM"):
            child = HistChild.objects.create(factor=9, should_fail=True)
            parent = HistNonAtomicParent.objects.create()

        with OperationContext(calculation_id="exact-hist-nonatom"):
            with self.assertRaises(CalculationModelException):
                self._trigger_calc(parent)

        # ── Parent assertions ────────────────────────────────────────
        parent.refresh_from_db()
        self.assertEqual(parent.is_calculated, CalculationModel.ERROR)

        parent_states = self._history_states(parent)
        self.assertEqual(parent_states[0], CalculationModel.NOT_CALCULATED,
                         "Parent history[0] must be NOT_CALCULATED (create)")
        self.assertIn(CalculationModel.IN_PROGRESS, parent_states,
                      "Parent IN_PROGRESS must survive in history")
        self.assertEqual(parent_states[-1], CalculationModel.ERROR,
                         "Parent history[-1] must be ERROR")
        self.assertEqual(parent_states.count(CalculationModel.IN_PROGRESS), 1,
                         f"Duplicate parent IN_PROGRESS: {parent_states}")
        self.assertEqual(parent_states.count(CalculationModel.ERROR), 1,
                         f"Duplicate parent ERROR: {parent_states}")

        # ── Child assertions ─────────────────────────────────────────
        child.refresh_from_db()
        self.assertEqual(child.is_calculated, CalculationModel.ERROR)

        child_states = self._history_states(child)
        self.assertEqual(child_states[0], CalculationModel.NOT_CALCULATED,
                         "Child history[0] must be NOT_CALCULATED (create)")
        self.assertIn(CalculationModel.IN_PROGRESS, child_states,
                      "Child IN_PROGRESS must survive in history")
        self.assertIn(CalculationModel.ERROR, child_states,
                      "Child must have ERROR in history")
        self.assertLess(
            child_states.index(CalculationModel.IN_PROGRESS),
            child_states.index(CalculationModel.ERROR),
            "Child: IN_PROGRESS must precede ERROR",
        )
        self.assertEqual(child_states.count(CalculationModel.IN_PROGRESS), 1,
                         f"Duplicate child IN_PROGRESS: {child_states}")
        self.assertEqual(child_states.count(CalculationModel.ERROR), 1,
                         f"Duplicate child ERROR: {child_states}")

    def test_non_atomic_parent_atomic_child_succeeds_exact_history(self):
        """
        Same topology (non-atomic parent + atomic child) but calculation
        succeeds — verify exact history with no duplicates.

        Parent:  NOT_CALCULATED → IN_PROGRESS → SUCCESS   (3 entries)
        Child:   NOT_CALCULATED → IN_PROGRESS → SUCCESS   (3 entries)
        """
        with OperationContext(actor="Analyst"):
            child = HistChild.objects.create(factor=4, should_fail=False)
            parent = HistNonAtomicParent.objects.create()

        with OperationContext(calculation_id="exact-hist-nonatom-ok"):
            self._trigger_calc(parent)

        # ── Parent ───────────────────────────────────────────────────
        parent.refresh_from_db()
        self.assertEqual(parent.is_calculated, CalculationModel.SUCCESS)
        self.assertEqual(parent.total, 40)  # 4 * 10

        parent_states = self._history_states(parent)
        self.assertEqual(parent_states[0], CalculationModel.NOT_CALCULATED)
        self.assertEqual(parent_states[-1], CalculationModel.SUCCESS)
        self.assertEqual(
            parent_states.count(CalculationModel.IN_PROGRESS), 1,
            f"Parent has duplicate IN_PROGRESS: {parent_states}",
        )
        self.assertEqual(
            parent_states.count(CalculationModel.SUCCESS), 1,
            f"Parent has duplicate SUCCESS: {parent_states}",
        )

        # ── Child ────────────────────────────────────────────────────
        child.refresh_from_db()
        self.assertEqual(child.is_calculated, CalculationModel.SUCCESS)

        child_states = self._history_states(child)
        self.assertEqual(child_states[0], CalculationModel.NOT_CALCULATED)
        self.assertEqual(child_states[-1], CalculationModel.SUCCESS)
        self.assertEqual(
            child_states.count(CalculationModel.IN_PROGRESS), 1,
            f"Child has duplicate IN_PROGRESS: {child_states}",
        )
        self.assertEqual(
            child_states.count(CalculationModel.SUCCESS), 1,
            f"Child has duplicate SUCCESS: {child_states}",
        )

    # ── Atomic parent + atomic child: duplicate guard ────────────────

    def test_atomic_parent_atomic_child_fails_no_duplicate_history(self):
        """
        Atomic parent + atomic child, child fails.

        Correct parent history: NOT_CALCULATED → IN_PROGRESS → ERROR.
        No duplicate rows.
        """
        with OperationContext(actor="Risk"):
            HistChild.objects.create(factor=3, should_fail=True)
            parent = HistParent.objects.create()

        with OperationContext(calculation_id="exact-hist-atom-fail"):
            with self.assertRaises(CalculationModelException):
                self._trigger_calc(parent)

        parent.refresh_from_db()
        self.assertEqual(parent.is_calculated, CalculationModel.ERROR)

        parent_states = self._history_states(parent)
        self.assertIn(CalculationModel.IN_PROGRESS, parent_states,
                      "Parent IN_PROGRESS must survive in history")
        self.assertIn(CalculationModel.ERROR, parent_states,
                      "Parent ERROR must appear in history")
        self.assertEqual(parent_states.count(CalculationModel.IN_PROGRESS), 1,
                         f"Duplicate IN_PROGRESS: {parent_states}")
        self.assertEqual(parent_states.count(CalculationModel.ERROR), 1,
                         f"Duplicate ERROR: {parent_states}")

    def test_atomic_parent_atomic_child_succeeds_no_duplicate_history(self):
        """
        Atomic parent + atomic child, all succeed.

        Every level: exactly one IN_PROGRESS, exactly one SUCCESS,
        no duplicates.
        """
        with OperationContext(actor="Analyst"):
            HistChild.objects.create(factor=6)
            parent = HistParent.objects.create()

        with OperationContext(calculation_id="exact-hist-atom-ok"):
            self._trigger_calc(parent)

        # ── Parent ───────────────────────────────────────────────────
        parent.refresh_from_db()
        self.assertEqual(parent.is_calculated, CalculationModel.SUCCESS)

        parent_states = self._history_states(parent)
        self.assertEqual(parent_states[0], CalculationModel.NOT_CALCULATED)
        self.assertEqual(parent_states[-1], CalculationModel.SUCCESS)
        self.assertEqual(
            parent_states.count(CalculationModel.IN_PROGRESS), 1,
            f"Atomic parent has duplicate IN_PROGRESS: {parent_states}",
        )
        self.assertEqual(
            parent_states.count(CalculationModel.SUCCESS), 1,
            f"Atomic parent has duplicate SUCCESS: {parent_states}",
        )

        # ── Child ────────────────────────────────────────────────────
        child = HistChild.objects.first()
        child.refresh_from_db()
        child_states = self._history_states(child)
        self.assertEqual(child_states[0], CalculationModel.NOT_CALCULATED)
        self.assertEqual(child_states[-1], CalculationModel.SUCCESS)
        self.assertEqual(
            child_states.count(CalculationModel.IN_PROGRESS), 1,
            f"Atomic child has duplicate IN_PROGRESS: {child_states}",
        )
        self.assertEqual(
            child_states.count(CalculationModel.SUCCESS), 1,
            f"Atomic child has duplicate SUCCESS: {child_states}",
        )
