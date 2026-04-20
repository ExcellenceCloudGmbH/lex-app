"""
Cluster 6b: Calculation audit finalization.

Intent (from docs/features/audit-logging/ and
docs/features/calculations/):

    Every calculation ends with a *terminal* audit log entry whose
    ``audit_status`` is ``success`` or ``failure``. On failure the entry
    includes the exception message. Crucially, the entry must be
    written even when the calculation's ``save()`` atomic block rolls
    back (scenario 6.10 — linked to BUG-001).

Scenario numbering matches
docs/test-plan/test-clusters.md#6-audit-logging.
"""

from __future__ import annotations

import unittest


class TestCluster06b_CalculationAudit(unittest.TestCase):
    """Calculation audit finalization — skipped pending AuditLog fixture."""

    @unittest.skip(
        "Scenario 6.5: Calculation audit — success. Requires AuditLog "
        "fixture (see test_6a_api_audit.py)."
    )
    def test_6_5_calc_success_terminal_audit(self) -> None:
        """Scenario 6.5: Success → terminal audit with audit_status=success."""

    @unittest.skip(
        "Scenario 6.6: Calculation audit — failure. Requires AuditLog fixture."
    )
    def test_6_6_calc_failure_terminal_audit(self) -> None:
        """Scenario 6.6: Failure → terminal audit with audit_status=failure."""

    @unittest.skip(
        "Scenario 6.10: Audit must survive calculation rollback (BUG-001 "
        "family). Requires AuditLog fixture AND the framework fix for "
        "BUG-001 before it can pass — track as expectedFailure once the "
        "fixture lands."
    )
    def test_6_10_audit_survives_calculation_failure(self) -> None:
        """Scenario 6.10: ``_finalize_pending_terminal_audit`` runs through rollback."""


if __name__ == "__main__":  # pragma: no cover
    unittest.main()

