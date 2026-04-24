"""
Cluster 6b: Calculation audit finalization.

Intent (from docs/features/audit-logging/ and
docs/features/calculations/):

    Every root calculation ends with a *terminal* AuditLog row whose
    ``AuditLogStatus`` is ``'success'`` or ``'failure'``. On failure
    the status carries the traceback so operators can diagnose it.

    Crucially, the terminal audit must survive a calculation rollback
    (scenario 6.10 — BUG-001 family): even when the calculation's
    atomic transaction rolls back, the audit of *why* it failed must
    remain.

The Pass B2 fixture unblocks these scenarios by:

    * creating the ``AuditLog`` / ``AuditLogStatus`` tables in
      ``e2e_framework_models``;
    * unpatching ``ensure_terminal_calculation_audit`` so the real
      function runs against the live tables.

Scenario numbering matches
docs/test-plan/test-clusters.md#6-audit-logging.
"""

from __future__ import annotations

import unittest

from lex.audit_logging.models.AuditLog import AuditLog
from lex.audit_logging.models.AuditLogStatus import AuditLogStatus
from lex.audit_logging.utils.calculation_audit import (
    ensure_terminal_calculation_audit,
)
from lex.tests.e2e._e2e_test_case import E2ETestCase

from .models import ALL_MODELS, AuditAtomicCalc


class TestCluster06b_CalculationAudit(E2ETestCase):
    """Calculation audit finalization — live under the Pass B2 fixture."""

    e2e_models = ALL_MODELS
    e2e_framework_models = [AuditLog, AuditLogStatus]
    # Let the real function run — it writes to the live tables above.
    e2e_unpatch = {"ensure_terminal_calculation_audit"}

    def test_6_5_calc_success_terminal_audit(self) -> None:
        """
        Scenario 6.5: Successful calculation → terminal AuditLog with
        ``AuditLogStatus.status = 'success'``.

        We call the terminal-audit function directly with a saved root
        calculation instance; it mirrors exactly what the calc state
        machine does on SUCCESS.
        """
        AuditLog.objects.all().delete()
        calc = AuditAtomicCalc.objects.create(name="c6-5", should_fail=False)

        result = ensure_terminal_calculation_audit(
            calc,
            audit_status="success",
            context_data={"calculation_id": f"calc_{calc.pk}"},
        )

        self.assertIsNotNone(
            result,
            "ensure_terminal_calculation_audit must return the AuditLog "
            "row it created or updated for a root calculation.",
        )
        rows = AuditLog.objects.filter(
            resource="auditatomiccalc", object_id=calc.pk,
        )
        self.assertEqual(
            rows.count(), 1,
            "A successful root calculation must produce exactly one "
            f"terminal AuditLog row; got {rows.count()}.",
        )
        self.assertEqual(
            AuditLogStatus.objects.filter(audit_log=rows.first()).first().status,
            "success",
            "A successful calculation must finalize AuditLogStatus to "
            "'success'.",
        )

    def test_6_6_calc_failure_terminal_audit(self) -> None:
        """
        Scenario 6.6: Failed calculation → terminal AuditLog with
        ``status='failure'`` and a traceback attached.

        The traceback is what lets an operator diagnose the failure
        after the fact — without it the audit trail is useless.
        """
        AuditLog.objects.all().delete()
        calc = AuditAtomicCalc.objects.create(name="c6-6")

        result = ensure_terminal_calculation_audit(
            calc,
            audit_status="failure",
            error_message="boom",
            stack_trace="Traceback (simulated):\n  RuntimeError: boom",
            context_data={"calculation_id": f"calc_{calc.pk}"},
        )

        self.assertIsNotNone(result)
        status_row = AuditLogStatus.objects.filter(audit_log=result).first()
        self.assertEqual(
            status_row.status, "failure",
            "A failed calculation must finalize AuditLogStatus to 'failure'.",
        )
        self.assertIn(
            "boom", (status_row.error_traceback or ""),
            "The terminal-audit failure row must carry the traceback "
            "so operators can diagnose the failure after the fact; "
            f"got {status_row.error_traceback!r}.",
        )

    # @unittest.expectedFailure  # BUG-001 family: atomic rollback erases audit row
    def test_6_10_audit_survives_calculation_failure(self) -> None:
        """
        Scenario 6.10: Audit must survive a calculation's atomic rollback.

        Documented intent: ``ensure_terminal_calculation_audit`` uses a
        **separate** transaction so the audit row persists even when the
        outer calculation transaction rolls back.

        Expected failure: when the outer transaction is still active and
        rolls back, the inner audit row is rolled back too — the
        "separate transaction" guarantee hasn't been implemented yet.
        Linked to BUG-001 family (save() + IN_PROGRESS + hooks all in
        one atomic block).
        """
        from django.db import transaction

        AuditLog.objects.all().delete()
        calc = AuditAtomicCalc.objects.create(name="c6-10")

        try:
            with transaction.atomic():
                ensure_terminal_calculation_audit(
                    calc,
                    audit_status="failure",
                    error_message="rollback test",
                    stack_trace="Traceback (rollback):",
                    context_data={"calculation_id": f"calc_{calc.pk}"},
                )
                # Force the outer transaction to abort.
                raise RuntimeError("outer rollback")
        except RuntimeError:
            pass

        self.assertEqual(
            AuditLog.objects.filter(
                resource="auditatomiccalc", object_id=calc.pk,
            ).count(),
            1,
            "Terminal audit row must survive an outer transaction "
            "rollback — the documented contract is that failure "
            "diagnostics are never lost, no matter what happens to the "
            "calculation itself.",
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()




