"""
Cluster 15g — idempotency and per-calc isolation (15.16–15.17).

15.16 pins the row-dedup contract: a re-run of the same root under the
same ``calculation_id`` does not duplicate ``CalculationLog`` rows; the
same row's ``calculation_log`` text is *appended* to.

15.17 pins the cross-calc-id isolation contract: two runs with two
distinct ``calculation_id`` / ``AuditLog`` pairs produce two
independent parent chains, and filtering by ``calculationId`` returns
only that calculation's rows.
"""
from __future__ import annotations

from . import _CalcLogTestCase, _seed_operation_context_and_audit_log
from lex.audit_logging.models.CalculationLog import CalculationLog
from .models import LogRootCalc


class TestCluster15g_IdempotencyAndIsolation(_CalcLogTestCase):
    """Re-runs dedup; cross-calc rows do not bleed."""

    # -- 15.16 ---------------------------------------------------------
    def test_15_16_rerunning_root_does_not_duplicate_rows(self):
        """Re-driving the same root under the same calc_id keeps the
        row count at 2 (root + loud child) and appends each new
        message to the existing rows.
        """
        root = LogRootCalc(name="r16", child_mode="loud", units_csv="u1")
        self.drive_root(root)
        self.assert_total_rows(2)

        root_row_first = self.assert_log_row(root, parent=None)
        first_body = root_row_first.calculation_log

        # Re-drive the same root — same calc_id, same model_logging_context.
        self.drive_root(root)

        self.assert_total_rows(2)
        root_row_again = self.assert_log_row(root, parent=None)
        self.assertEqual(
            root_row_again.id, root_row_first.id,
            "Re-run must update the SAME row, not create a new one.",
        )
        # Message must have been appended (length grew, "root r16" appears
        # at least twice).
        self.assertGreater(
            len(root_row_again.calculation_log), len(first_body),
            "calculation_log text must be appended on re-run.",
        )
        self.assertGreaterEqual(
            root_row_again.calculation_log.count("root r16"), 2,
            "Re-run should append a second 'root r16' message to the row.",
        )

    # -- 15.17 ---------------------------------------------------------
    def test_15_17_calculation_id_isolation_across_two_audit_logs(self):
        """Two roots driven under two distinct (calc_id, AuditLog) pairs
        produce two independent row sets — filtering by calculationId
        returns each set in isolation, with independent parent chains.
        """
        # Run 1 — uses self.calc_id seeded by setUp.
        root1 = LogRootCalc(name="r17a", child_mode="loud", units_csv="u1")
        self.drive_root(root1)
        first_calc_id = self.calc_id

        first_count = CalculationLog.objects.filter(
            calculationId=first_calc_id,
        ).count()
        self.assertEqual(first_count, 2)

        # Run 2 — fresh calc_id + AuditLog. Overwrite instance attrs
        # so the helpers (assert_log_row, assert_total_rows) see run 2's
        # rows. _seed_operation_context_and_audit_log returns a context
        # token; stash it on self so tearDown's reset() unwinds the
        # final state, not the intermediate one.
        new_calc_id, new_audit_log, new_token = (
            _seed_operation_context_and_audit_log()
        )
        self.calc_id = new_calc_id
        self.audit_log = new_audit_log
        self._ctx_token = new_token

        root2 = LogRootCalc(name="r17b", child_mode="loud", units_csv="u1")
        self.drive_root(root2)

        # Run 2's filter sees its own 2 rows.
        self.assert_total_rows(2)

        # Run 1's filter is unaffected.
        self.assertEqual(
            CalculationLog.objects.filter(calculationId=first_calc_id).count(),
            2,
            "First calculation's row count must not have changed when the "
            "second calculation ran under a different calc_id.",
        )

        # Parent chains independent: run 2's child parents to run 2's
        # root, not to anything from run 1. There are now two
        # LogLoudChild rows (one per run, both unit="u1"); identify
        # run 2's child by filtering CalculationLog on the new calc_id.
        run2_root_row = self.assert_log_row(root2, parent=None)
        run2_child_log = CalculationLog.objects.filter(
            calculationId=new_calc_id,
            content_type__model="logloudchild",
        ).first()
        self.assertIsNotNone(
            run2_child_log,
            "Run 2 must have produced a LogLoudChild CalculationLog row.",
        )
        self.assertEqual(
            run2_child_log.parent_log_id, run2_root_row.id,
            "Run 2's child must parent to run 2's root, not run 1's.",
        )




