"""
Shared models for Cluster 6 — Audit Logging.

Rule #3: per-cluster models, never reused. Both are minimal: the
cluster is about *who/what* the framework records, not field shapes.
"""

from __future__ import annotations

from django.db import models

from lex.core.models.CalculationModel import CalculationModel
from lex.core.models.LexModel import LexModel, PermissionResult


def _permissive(cls):
    cls.permission_read = lambda self, uc: PermissionResult.allow_all("cluster 6")
    cls.permission_edit = lambda self, uc: PermissionResult.allow_all("cluster 6")
    cls.permission_create = lambda self, uc: True
    cls.permission_delete = lambda self, uc: True
    cls.permission_list = lambda self, uc: True
    return cls


@_permissive
class AuditSimpleItem(LexModel):
    """Plain LexModel — audit rows for API create/update/delete."""

    name = models.CharField(max_length=200)
    value = models.IntegerField(default=0)

    class Meta:
        app_label = "lex_app"

    def __str__(self) -> str:  # pragma: no cover
        return self.name


@_permissive
class AuditAtomicCalc(CalculationModel):
    """Atomic calc — audit rows must survive a calculation failure."""

    name = models.CharField(max_length=200)
    should_fail = models.BooleanField(default=False)
    calculation_error_message = models.TextField(blank=True, default="")

    is_atomic = True

    class Meta:
        app_label = "lex_app"

    def __str__(self) -> str:  # pragma: no cover
        return self.name

    def calculate(self):
        if self.should_fail:
            raise RuntimeError(f"AuditAtomicCalc({self.name!r}) failing on purpose")


@_permissive
class AuditNonAtomicCalc(CalculationModel):
    """Non-atomic calc — ``is_atomic = False`` opts out of the
    surrounding API ``transaction.atomic()`` block (see
    ``should_use_atomic_model_operations`` in
    ``lex.core.models.LexModel``).

    Used by the API-driven calc audit tests in 6i to pin the contract
    that audit rows are created the same way regardless of whether the
    view wrapped the call in an atomic block.
    """

    name = models.CharField(max_length=200)
    should_fail = models.BooleanField(default=False)
    calculation_error_message = models.TextField(blank=True, default="")

    is_atomic = False

    class Meta:
        app_label = "lex_app"

    def __str__(self) -> str:  # pragma: no cover
        return self.name

    def calculate(self):
        if self.should_fail:
            raise RuntimeError(
                f"AuditNonAtomicCalc({self.name!r}) failing on purpose"
            )


ALL_MODELS = [AuditSimpleItem, AuditAtomicCalc, AuditNonAtomicCalc]

AUDIT_SIMPLE = "auditsimpleitem"
AUDIT_ATOMIC = "auditatomiccalc"
AUDIT_NONATOMIC = "auditnonatomiccalc"

