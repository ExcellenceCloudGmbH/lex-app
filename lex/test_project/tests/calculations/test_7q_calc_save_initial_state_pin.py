"""Cluster 7q: calc-path skips the per-save initial-state re-baseline.

Intent: ``LexModel.save()`` defers django-lifecycle's change-tracking
re-baseline to ``transaction.on_commit(self._reset_initial_state)``. A whole
calculation runs inside ONE outer ``transaction.atomic()``
(``CalculationModel.execute_calculation_sync``), so those commit callbacks
never drain mid-calc — every queued bound method pins its instance AND its
``_initial_state`` snapshot alive for the entire run, turning N bulk saves
into O(N) retained Python memory (the v2 calc-memory regression vs v1's plain
``Model.save``).

The fix (``LexModel._register_initial_state_reset``) skips registering that
callback when ``_in_calculation_execution`` is True. It is safe because the
callback never fires until the OUTERMOST atomic commits — so the re-baseline
never takes effect during a calculation anyway — and calc instances are
write-once-and-discarded.

These tests pin both halves of that contract:
  * request-path saves STILL register + apply the re-baseline (no regression);
  * calc-path saves do NOT register it, and the registration count stays O(1)
    regardless of how many rows the calculation saves.

Cluster 7q — scenarios 7.188-7.192. Type: U (gate logic) + I (E2E paths).
Covers: lex/core/models/LexModel.py (_register_initial_state_reset).
Run: python -m lex pytest lex/test_project/tests/calculations/test_7q_calc_save_initial_state_pin.py -v
"""

from __future__ import annotations

from unittest import mock

import pytest
from django.test import SimpleTestCase
from lex.core.models import LexModel as lexmodel_module
from lex.core.models.CalculationModel import calculation_execution_context
from lex.test_project.tests._e2e_test_case import E2ETestCase
from lex.test_project.tests.calculations.models import (
    ALL_MODELS,
    CombinatorialCalc,
    FKAbortTarget,
)

pytestmark = pytest.mark.calculations


def _count_reset_registrations(on_commit_mock):
    """How many queued callbacks are a ``_reset_initial_state`` bound method."""
    return sum(
        1
        for call in on_commit_mock.call_args_list
        if getattr(call.args[0], "__name__", None) == "_reset_initial_state"
    )


class TestCluster07q_RegistrationGate(SimpleTestCase):
    """Unit: the gate registers on the request path, skips on the calc path."""

    def test_7_188_request_path_registers_reset(self):
        """Scenario 7.188.

        Given a LexModel instance saved outside any calculation
        When _register_initial_state_reset runs (_in_calculation_execution False)
        Then it queues exactly one transaction.on_commit(self._reset_initial_state).
        """
        obj = FKAbortTarget(name="req")
        with mock.patch.object(lexmodel_module.transaction, "on_commit") as on_commit:
            obj._register_initial_state_reset()

        assert on_commit.call_count == 1
        assert on_commit.call_args.args[0] == obj._reset_initial_state

    def test_7_189_calc_path_skips_reset(self):
        """Scenario 7.189.

        Given a LexModel instance saved by calculation user-code
        When _register_initial_state_reset runs inside calculation_execution_context
        Then no on_commit callback is queued (the memory pin is avoided).
        """
        obj = FKAbortTarget(name="calc")
        with mock.patch.object(lexmodel_module.transaction, "on_commit") as on_commit:
            with calculation_execution_context():
                obj._register_initial_state_reset()

        assert on_commit.call_count == 0


class TestCluster07q_RequestPathRebaseline(E2ETestCase):
    """E2E: a normal (non-calc) save still re-baselines the initial state."""

    e2e_models = ALL_MODELS

    def test_7_190_request_save_rebaselines_initial_state(self):
        """Scenario 7.190.

        Given a saved FKAbortTarget whose name is then changed
        When it is saved again outside any calculation (autocommit fires on_commit)
        Then has_changed() clears and initial_value() advances to the new value —
            proving the request-path re-baseline is preserved by the fix.
        """
        obj = FKAbortTarget(name="original")
        obj.save()
        assert obj.initial_value("name") == "original"

        obj.name = "edited"
        assert obj.has_changed("name") is True

        obj.save()
        # E2ETestCase is a TransactionTestCase (autocommit) so on_commit has fired.
        assert obj.has_changed("name") is False
        assert obj.initial_value("name") == "edited"


class TestCluster07q_CalcPathPinBounded(E2ETestCase):
    """E2E: a real calculation produces correct rows and pins O(1), not O(N)."""

    e2e_models = ALL_MODELS

    def setUp(self):
        super().setUp()
        # Reset mutable class-level knobs between tests (same as 7g/7p).
        CombinatorialCalc._region_keys = ["US", "EU", "APAC"]
        CombinatorialCalc._category_keys = ["A", "B"]
        CombinatorialCalc.fail_for_region = None

    def _run_create_counting_resets(self):
        """Run create() while counting _reset_initial_state on_commit registrations.

        Wraps the real on_commit so behaviour is unchanged — we only observe.
        """
        real_on_commit = lexmodel_module.transaction.on_commit
        with mock.patch.object(
            lexmodel_module.transaction,
            "on_commit",
            side_effect=real_on_commit,
        ) as spy:
            CombinatorialCalc.create()
        return _count_reset_registrations(spy)

    def test_7_191_calc_create_saves_correct_rows(self):
        """Scenario 7.191.

        Given the combinatorial calculation over region x category
        When create() runs in sync mode
        Then every expected (region, category) row is saved and named — the
            skipped re-baseline does not corrupt calc output.
        """
        CombinatorialCalc.create()
        rows = set(
            CombinatorialCalc.objects.values_list("region", "category", "name")
        )
        expected = {
            (r, c, f"{r}-{c}")
            for r in ("US", "EU", "APAC")
            for c in ("A", "B")
        }
        assert rows == expected
        assert len(rows) == 6

    def test_7_192_calc_reset_registrations_do_not_scale_with_n(self):
        """Scenario 7.192.

        Given two calculation runs that save a different number of rows
        When each runs create() in sync mode
        Then the count of queued _reset_initial_state callbacks issued from
            inside the calculation is the same (O(1)) — proving the per-save
            memory pin no longer grows with N (the regression guard).
        """
        # Small run: 1 region x 1 category = 1 saved row.
        CombinatorialCalc._region_keys = ["US"]
        CombinatorialCalc._category_keys = ["A"]
        CombinatorialCalc.objects.all().delete()
        small_resets = self._run_create_counting_resets()

        # Large run: 3 regions x 3 categories = 9 saved rows.
        CombinatorialCalc._region_keys = ["US", "EU", "APAC"]
        CombinatorialCalc._category_keys = ["A", "B", "C"]
        CombinatorialCalc.objects.all().delete()
        large_resets = self._run_create_counting_resets()

        # The per-row calc saves register ZERO resets in both runs; the count
        # must not grow with the number of saved rows.
        assert small_resets == large_resets
