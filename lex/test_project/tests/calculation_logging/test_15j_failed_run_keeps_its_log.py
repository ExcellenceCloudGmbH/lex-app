"""Cluster 15j — a failed run keeps what it logged.

Scenarios 15.47 – 15.50.

Intent (review of the calculation log popup): "Why is there no display of the
calculation logs from the successful parts of the calculation until it raises
an error?" A CalculationLog row is persisted on commit, so a calculation that
fails inside its transaction — every ``is_atomic`` model — was rolled back with
every row it wrote. The steps that succeeded vanished with the one that failed,
and the log popup could show the traceback but nothing that led up to it.

The live cache is not transactional: it holds the run's whole log under the
root's key until the run ends. ``CalculationLog.keep_rolled_back_log`` writes
that text back as the run's log in the failure path, just before the cache is
purged.
"""
from __future__ import annotations

from unittest import mock

import pytest

from lex.audit_logging.models.AuditLog import AuditLog
from lex.audit_logging.models.CalculationLog import CalculationLog
from lex.audit_logging.serializers.AuditLogSerializer import AuditLogDefaultSerializer
from lex.audit_logging.utils.CacheManager import CacheManager

from . import _CalcLogTestCase
from .models import LogRootCalc

pytestmark = pytest.mark.calculation_logging


class TestCluster15j_FailedRunKeepsItsLog(_CalcLogTestCase):
    """What a failed atomic run logged before it failed survives the rollback."""

    # The harness stubs the live cache (every key is "test_key" and nothing is
    # stored); these scenarios are about what the real cache holds.
    e2e_unpatch = {"ensure_terminal_calculation_audit", "store_message", "build_cache_key"}

    def _fail_root(self) -> LogRootCalc:
        """Run a LogRootCalc that logs a step, then raises inside its transaction."""
        from lex.audit_logging.utils.ModelContext import model_logging_context
        from lex.core.models.CalculationModel import CalculationModelException

        root = LogRootCalc.objects.create(
            name="root-fail", child_mode="fail_after_log", units_csv=""
        )
        root.is_calculated = LogRootCalc.IN_PROGRESS
        root.save(skip_hooks=True)
        with model_logging_context(root):
            with self.assertRaises(CalculationModelException):
                root.calculate_hook()
        return root

    def test_15_47_a_failed_atomic_run_keeps_the_steps_it_logged(self):
        """15.47: the root's row holds everything logged before the failure."""
        root = self._fail_root()

        root.refresh_from_db()
        self.assertEqual(root.is_calculated, LogRootCalc.ERROR)
        row = self.assert_log_row(root, parent=None, contains="root root-fail")
        self.assertIn("step one done", row.calculation_log)

    def test_15_48_the_audit_trail_now_says_the_failed_run_has_a_log(self):
        """15.48: the audit row the log popup reads for a failed run reports
        its log, so the popup opens it instead of saying it was rolled back."""
        self._fail_root()

        audit_row = AuditLog.objects.filter(calculation_id=self.calc_id).order_by("id").first()
        serializer = AuditLogDefaultSerializer()
        self.assertIs(serializer.get_lex_reserved_has_calculation_log(audit_row), True)

    def test_15_49_a_run_whose_rows_survived_is_left_alone(self):
        """15.49: rows that outlived the run are the log; nothing is added."""
        root = self._save_root(child_mode="log_only", units_csv="")
        self.assert_total_rows(1)

        kept = CalculationLog.keep_rolled_back_log(
            self.calc_id, f"logrootcalc_{root.pk}", root
        )

        self.assertIs(kept, False)
        self.assert_total_rows(1)

    def test_15_50_an_empty_or_broken_cache_costs_the_log_not_the_failure_path(self):
        """15.50: nothing cached writes nothing, and a cache that raises is
        swallowed — the failure path must still report the first failure."""
        root = LogRootCalc.objects.create(name="root-idle", child_mode="log_only", units_csv="")
        record = f"logrootcalc_{root.pk}"

        self.assertIs(CalculationLog.keep_rolled_back_log(self.calc_id, record, root), False)
        with mock.patch.object(CacheManager, "get_message", side_effect=RuntimeError("down")):
            self.assertIs(CalculationLog.keep_rolled_back_log(self.calc_id, record, root), False)
        self.assert_total_rows(0)
