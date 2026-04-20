"""
Cluster 6a: API-path audit log rows.

Intent (from docs/features/audit-logging/):

    Every API create / update / delete produces a row in the
    ``AuditLog`` table with:
      - ``actor`` resolved from the authenticated user
      - ``action`` = create / update / delete
      - ``payload`` including the changed fields
      - ``status`` = success on normal completion, failure otherwise

Scenario numbering matches
docs/test-plan/test-clusters.md#6-audit-logging.

Note:
    The ``AuditLog`` table is registered as a real Django model via
    ``lex.audit_logging.models.AuditLog``. Tests that assert on audit
    rows need the ``AuditLog`` table to exist in the test DB; that
    requires the audit-logging app to be fully migrated in the test
    environment. Scenarios 6.1–6.4 are **@skip**'d pending a shared
    audit-log test fixture; actor-resolution coverage (which is
    observable on ``created_by`` / ``edited_by`` fields directly) is
    in ``test_6c_actor_resolution.py``.
"""

from __future__ import annotations

import unittest


class TestCluster06a_APIAuditLog(unittest.TestCase):
    """API path writes AuditLog rows — skipped pending AuditLog fixture."""

    @unittest.skip(
        "Scenario 6.1: API create produces audit log. Requires AuditLog "
        "table + middleware wiring that is not enabled in E2ETestCase. "
        "Re-enable once a Cluster-6 fixture lands."
    )
    def test_6_1_api_create_audit_row(self) -> None:
        """Scenario 6.1: API create → AuditLog row with action=create."""

    @unittest.skip("Scenario 6.2: pending AuditLog fixture (see 6.1)")
    def test_6_2_api_update_audit_row(self) -> None:
        """Scenario 6.2: API update → AuditLog row with action=update."""

    @unittest.skip("Scenario 6.3: pending AuditLog fixture (see 6.1)")
    def test_6_3_api_delete_audit_row(self) -> None:
        """Scenario 6.3: API delete → AuditLog row with action=delete."""

    @unittest.skip("Scenario 6.4: pending AuditLog fixture (see 6.1)")
    def test_6_4_api_failure_audit_row(self) -> None:
        """Scenario 6.4: Failed API op → AuditLog row with status=failure."""


if __name__ == "__main__":  # pragma: no cover
    unittest.main()

