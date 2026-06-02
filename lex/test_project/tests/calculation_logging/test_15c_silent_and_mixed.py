"""Cluster 15c — Silent children + mixed-siblings regression gate.

Scenarios 15.11 – 15.13. The mixed-siblings scenario (15.12) is the
direct regression gate for the production "divergent child calculation"
investigation: a child that doesn't call LexLogger creates ZERO
CalculationLog rows even though calc_and_save_sync wraps it in
model_logging_context.
"""
from __future__ import annotations

from lex.audit_logging.models.CalculationLog import CalculationLog

from . import _CalcLogTestCase
from .models import LogLoudChild, LogRootCalc, LogSilentChild

import pytest

pytestmark = pytest.mark.calculation_logging


class TestCluster15c_SilentAndMixed(_CalcLogTestCase):
    """Silent children produce no rows; mixed siblings produce exactly
    the right count."""

    def test_15_11_silent_children_produce_no_rows(self):
        """15.11: LogSilentChild.create(unit=['u1','u2','u3']) — exactly
        1 CalculationLog row (the root's). Zero rows for any
        LogSilentChild instance despite model_logging_context wrapping
        each one.
        """
        root = self._save_root(child_mode="silent", units_csv="u1,u2,u3")
        self.assert_log_row(root, parent=None, contains="root root-silent")
        self.assert_total_rows(1)
        for unit in ("u1", "u2", "u3"):
            silent = LogSilentChild.objects.get(unit=unit)
            self.assertTrue(
                silent.touched,
                f"Sanity: LogSilentChild(unit={unit}).calculate() ran "
                f"(touched=True). If False, calc_and_save_sync didn't "
                f"invoke calculate() — the test isn't actually exercising "
                f"the silent-child code path.",
            )
            self.assert_no_log_row(silent)

    def test_15_12_mixed_siblings_regression_gate(self):
        """15.12: PRODUCTION REGRESSION GATE.

        Root.calculate() triggers LogLoudChild.create(unit=['u1','u2'])
        AND LogSilentChild.create(unit=['u1','u2']) — exact shape of
        the production case (Infra Fund 1+2 loud, PE_LTIP_* etc.
        silent).

        Asserts:
          * exactly 3 CalculationLog rows total
          * 1 row for root (parent_log_id IS NULL)
          * 1 row for each loud child (parent_log_id = root_row.id)
          * 0 rows for either silent child

        If this test ever flips (e.g., suddenly produces 5 rows), the
        framework has regressed and silent children are wrongly
        producing rows.
        """
        root = self._save_root(child_mode="mixed", units_csv="u1,u2")
        root_row = self.assert_log_row(root, parent=None)

        for unit in ("u1", "u2"):
            loud = LogLoudChild.objects.get(unit=unit)
            self.assert_log_row(loud, parent=root_row, contains=f"loud {unit}")

            silent = LogSilentChild.objects.get(unit=unit)
            self.assertTrue(
                silent.touched,
                f"Sanity: LogSilentChild(unit={unit}).calculate() ran "
                f"(touched=True). If False, calc_and_save_sync didn't "
                f"invoke calculate() — and the test is vacuously satisfied "
                f"because no row was expected for a silent child anyway. "
                f"This is the PRODUCTION REGRESSION GATE; verify the "
                f"silent-child code path is actually being exercised.",
            )
            self.assert_no_log_row(silent)

        self.assert_total_rows(3)

    def test_15_13_silent_wrapping_preserves_calculation_id(self):
        """15.13: After silent children are pushed/popped from the
        stack, surviving loud rows still carry the root's
        calculationId — the silent wrapping is observationally
        invisible to operation_context.
        """
        self._save_root(child_mode="mixed", units_csv="u1,u2")
        ids = set(
            CalculationLog.objects.values_list("calculationId", flat=True)
        )
        self.assertEqual(
            ids, {self.calc_id},
            f"Expected every row to share calculationId={self.calc_id}, "
            f"found {ids}.",
        )
        # Defensive: explicitly check the loud child rows specifically
        # (not just the root's).
        loud_rows = CalculationLog.objects.filter(
            content_type__model="logloudchild",
        )
        self.assertEqual(loud_rows.count(), 2)
        for row in loud_rows:
            self.assertEqual(row.calculationId, self.calc_id)
