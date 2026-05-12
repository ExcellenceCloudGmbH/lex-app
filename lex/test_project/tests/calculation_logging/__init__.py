"""
Cluster 15 — Calculation Logging (LexLogger).

Shared base class and helpers for scenarios 15.1 – 15.18.
"""

from __future__ import annotations

from typing import Tuple
from uuid import uuid4

from django.contrib.contenttypes.models import ContentType

from lex.api.utils import operation_context
from lex.audit_logging.models.AuditLog import AuditLog
from lex.audit_logging.models.AuditLogStatus import AuditLogStatus
from lex.audit_logging.models.CalculationLog import CalculationLog
from lex.tests.e2e._e2e_test_case import E2ETestCase

from .models import ALL_MODELS


def _seed_operation_context_and_audit_log() -> Tuple[str, AuditLog]:
    """
    Generate a unique calculation_id, write the matching AuditLog row,
    and push the calculation_id into operation_context.

    Returns ``(calculation_id, audit_log)``.
    """
    calc_id = f"calc_15_{uuid4().hex}"
    audit_log = AuditLog.objects.create(
        calculation_id=calc_id,
        resource="logrootcalc",
        action="calculate",
        author="cluster-15-tests",
    )
    current = dict(operation_context.get() or {})
    current["calculation_id"] = calc_id
    current.setdefault("request_obj", None)
    operation_context.set(current)
    return calc_id, audit_log


class _CalcLogTestCase(E2ETestCase):
    """
    Shared base for all Cluster 15 test files. Registers the six test
    models, ensures CalculationLog + AuditLog tables exist, and seeds
    a fresh (calculation_id, AuditLog) pair on every test.
    """

    e2e_models = ALL_MODELS
    e2e_framework_models = [CalculationLog, AuditLog, AuditLogStatus]
    e2e_unpatch = {"ensure_terminal_calculation_audit"}

    def setUp(self):
        super().setUp()
        # Framework tables are NOT cleared by E2ETestCase.setUp —
        # we clear here so each scenario starts with zero rows.
        CalculationLog.objects.all().delete()
        AuditLog.objects.all().delete()
        self.calc_id, self.audit_log = _seed_operation_context_and_audit_log()

    # ── shared assertion helpers ─────────────────────────────────────

    def _ct(self, instance) -> ContentType:
        return ContentType.objects.get_for_model(type(instance))

    def assert_log_row(self, instance, *, parent=None, contains=None):
        """
        Assert exactly one CalculationLog row exists for
        (content_type=type(instance), object_id=instance.pk,
         calculationId=self.calc_id). Returns the row.

        Optional:
          parent  — another CalculationLog row (or None for NULL)
                    that should match the row's parent_log_id.
          contains — substring that must appear in calculation_log.
        """
        rows = list(CalculationLog.objects.filter(
            calculationId=self.calc_id,
            content_type=self._ct(instance),
            object_id=instance.pk,
        ))
        self.assertEqual(
            len(rows), 1,
            f"Expected exactly 1 CalculationLog row for "
            f"{type(instance).__name__}(pk={instance.pk}), got {len(rows)}.",
        )
        row = rows[0]
        if parent is None:
            self.assertIsNone(
                row.parent_log_id,
                f"Expected parent_log_id IS NULL for {row}, "
                f"got {row.parent_log_id}.",
            )
        else:
            self.assertEqual(
                row.parent_log_id, parent.id,
                f"Expected parent_log_id={parent.id}, got {row.parent_log_id}.",
            )
        if contains is not None:
            self.assertIn(
                contains, row.calculation_log,
                f"Expected substring {contains!r} in calculation_log, "
                f"got {row.calculation_log!r}.",
            )
        return row

    def assert_no_log_row(self, instance):
        rows = CalculationLog.objects.filter(
            calculationId=self.calc_id,
            content_type=self._ct(instance),
            object_id=instance.pk,
        )
        self.assertEqual(
            rows.count(), 0,
            f"Expected zero CalculationLog rows for "
            f"{type(instance).__name__}(pk={instance.pk}), "
            f"got {rows.count()}.",
        )

    def assert_total_rows(self, expected: int):
        actual = CalculationLog.objects.filter(calculationId=self.calc_id).count()
        self.assertEqual(
            actual, expected,
            f"Expected {expected} CalculationLog rows total for "
            f"calculationId={self.calc_id}, got {actual}.",
        )
