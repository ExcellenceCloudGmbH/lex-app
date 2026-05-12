"""
Cluster 15b — single-root logging topology (15.5–15.6).

Pins the simplest customer-visible shape: a root calculation that calls
``LexLogger().log()`` once produces exactly one ``CalculationLog`` row,
correctly tied back to the root via the GenericForeignKey.
"""
from __future__ import annotations

from django.contrib.contenttypes.models import ContentType

from . import _CalcLogTestCase
from .models import LogRootCalc


class TestCluster15b_SingleRootLogging(_CalcLogTestCase):
    """Single-row, NULL parent, GFK back-reference."""

    # -- 15.5 ----------------------------------------------------------
    def test_15_5_log_only_mode_writes_one_root_row(self):
        """``child_mode="log_only"`` writes exactly one row, parented
        to NULL (root has nothing above it on the model-context stack).
        """
        root = LogRootCalc(name="r5", child_mode="log_only")
        self.drive_root(root)

        self.assert_total_rows(1)
        self.assert_log_row(root, parent=None)

    # -- 15.6 ----------------------------------------------------------
    def test_15_6_root_row_points_at_logrootcalc_via_gfk(self):
        """The single root row's ``content_type`` / ``object_id`` /
        ``audit_log`` triple points at the LogRootCalc instance under
        this scenario's seeded AuditLog — the GFK back-reference the
        per-record Audit-Log Tab UI relies on.
        """
        root = LogRootCalc(name="r6", child_mode="log_only")
        self.drive_root(root)

        row = self.assert_log_row(root, parent=None)
        self.assertEqual(
            row.content_type,
            ContentType.objects.get_for_model(LogRootCalc),
            "content_type must point at LogRootCalc.",
        )
        self.assertEqual(
            row.object_id, root.pk,
            f"object_id must be root.pk={root.pk}, got {row.object_id}.",
        )
        self.assertEqual(
            row.audit_log_id, self.audit_log.pk,
            "Row must be tied to the AuditLog seeded for this calc_id.",
        )

