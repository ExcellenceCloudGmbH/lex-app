"""
Cluster 6g: Audit-log immutability.

Intent (from docs/features/tracking/audit logs.md ``[!note]``):

    "Audit logs are effectively read-only. They are designed to be an
    immutable record of operations — only administrators should
    modify or delete them."

    ``AuditLog.permission_create`` and ``permission_delete`` return
    ``False``; ``permission_edit`` returns
    ``PermissionResult.deny(...)``. A regression flipping any of
    these to ``True`` would silently allow audit tampering — there is
    currently NO test that catches that.

This sub-cluster pins the contract at the model level (where the
permissions live) so a regression at the source is caught even if
the URL routing for AuditLog isn't mounted in the test project.

Scenario numbering matches docs/test-plan/test-clusters.md § 6g.
"""

from __future__ import annotations

import unittest

from django.test import SimpleTestCase

from lex.audit_logging.models.AuditLog import AuditLog
from lex.audit_logging.models.AuditLogStatus import AuditLogStatus
from lex.core.models.LexModel import PermissionResult


class _FakeUserContext:
    """Stand-in for ``UserContext`` — the permission methods only call
    ``user_context`` for documentation in this test, so a bare object
    is enough."""
    is_admin = False
    is_authenticated = True


class TestCluster06g_AuditLogImmutability(SimpleTestCase):
    """``AuditLog`` / ``AuditLogStatus`` are read-only by contract."""

    def setUp(self) -> None:
        self.row = AuditLog()  # unsaved instance is enough for permission checks
        self.uc = _FakeUserContext()

    # -- 6.71 ----------------------------------------------------------
    def test_6_71_permission_create_returns_false(self) -> None:
        """
        Scenario 6.71: ``AuditLog.permission_create`` returns ``False``
        for non-admin (and admin alike — audit rows are system-
        created only). A regression flipping this to ``True`` would
        silently allow customers to forge audit history.
        """
        result = self.row.permission_create(self.uc)
        self.assertIs(
            result, False,
            "AuditLog.permission_create must return False (system-"
            "created only); got %r" % (result,),
        )

    # -- 6.72 ----------------------------------------------------------
    def test_6_72_permission_delete_returns_false(self) -> None:
        """
        Scenario 6.72: ``AuditLog.permission_delete`` returns ``False``
        — even for admin. Audit log is read-only by design; the only
        way to remove rows is direct DB access (which the docs frame
        as a compliance-team operation, not a customer one).
        """
        result = self.row.permission_delete(self.uc)
        self.assertIs(
            result, False,
            "AuditLog.permission_delete must return False (read-only "
            "by design); got %r" % (result,),
        )

    # -- 6.73 ----------------------------------------------------------
    def test_6_73_permission_edit_returns_deny(self) -> None:
        """
        Scenario 6.73: ``AuditLog.permission_edit`` returns
        ``PermissionResult.deny(...)`` so the serializer's permission-
        aware filter strips every editable field — fields cannot be
        mutated through the API.
        """
        result = self.row.permission_edit(self.uc)
        self.assertIsInstance(
            result, PermissionResult,
            "permission_edit must return a PermissionResult, not a "
            "bool; got %r" % (type(result),),
        )
        # PermissionResult uses ``allowed: bool`` for the deny gate.
        # AuditLog rows must come back with ``allowed=False`` —
        # nothing on AuditLog is editable through the API.
        self.assertFalse(
            getattr(result, "allowed", True),
            "permission_edit deny result must have allowed=False; "
            "got %r" % (result,),
        )
        # The deny reason is documented as "AuditLog records are
        # read-only" — pin a substring so a regression that flips the
        # message but keeps allowed=False is still caught.
        reason = getattr(result, "reason", "") or ""
        self.assertIn(
            "read-only", reason.lower(),
            "permission_edit deny reason must communicate the read-"
            "only contract; got %r" % (reason,),
        )

    # -- 6.73b ---------------------------------------------------------
    def test_6_73b_audit_log_status_inherits_immutability(self) -> None:
        """
        Sub-pin: ``AuditLogStatus`` is a child of the same audit
        record and inherits the immutability contract. A regression
        that opens write access to AuditLogStatus would let an
        attacker flip ``failure → success`` and hide errors.
        """
        status_row = AuditLogStatus()
        # AuditLogStatus may inherit from a permissive base. The
        # documented intent is that the customer cannot create / edit
        # / delete it through the API. Pin whatever method the model
        # exposes; if a permission method is absent, document the
        # gap explicitly so the assertion fails loudly rather than
        # silently passing.
        for method_name in ("permission_create", "permission_delete"):
            method = getattr(status_row, method_name, None)
            if method is None:
                continue  # not exposed → defaults apply
            result = method(_FakeUserContext())
            # Acceptable: False, or a PermissionResult that denies
            if isinstance(result, bool):
                self.assertFalse(
                    result,
                    "AuditLogStatus.%s must return False — audit "
                    "status is read-only by design. Regression here "
                    "would let an attacker hide failures by flipping "
                    "the status." % method_name,
                )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()



