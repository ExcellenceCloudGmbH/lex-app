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


@_permissive
class AuditFileItem(LexModel):
    """A record with a file — the download surface of the audit trail."""

    name = models.CharField(max_length=200)
    attachment = models.FileField(upload_to="cluster6s/", blank=True, null=True)

    class Meta:
        app_label = "lex_app"

    def __str__(self) -> str:  # pragma: no cover
        return self.name

    def permission_export(self, uc):
        return PermissionResult.allow_all("cluster 6")


class AuditGuardedFile(LexModel):
    """A record only its owner may read, and only exporters may export.

    Cluster 6t: who reads the export / download entries of the trail.
    A blank record (no owner) is readable by nobody, so a check that
    judges the entry by a record rebuilt from its payload denies
    everyone — the gap 6t pins closed.
    """

    EXPORTERS = {"exporter"}

    name = models.CharField(max_length=200)
    owner = models.CharField(max_length=150, blank=True, default="")
    attachment = models.FileField(upload_to="cluster6t/", blank=True, null=True)

    class Meta:
        app_label = "lex_app"

    def __str__(self) -> str:  # pragma: no cover
        return self.name

    @staticmethod
    def _username(uc):
        return getattr(getattr(uc, "user", None), "username", None)

    def permission_read(self, uc):
        if self.owner and self._username(uc) == self.owner:
            return PermissionResult.allow_all("cluster 6t: owner")
        return PermissionResult.deny("cluster 6t: not the owner")

    def permission_export(self, uc):
        if self._username(uc) in self.EXPORTERS:
            return PermissionResult.allow_all("cluster 6t: exporter")
        return PermissionResult.deny("cluster 6t: not an exporter")

    def permission_list(self, uc):
        return True

    def permission_edit(self, uc):
        return PermissionResult.allow_all("cluster 6t")

    def permission_create(self, uc):
        return True

    def permission_delete(self, uc):
        return True


ALL_MODELS = [AuditSimpleItem, AuditAtomicCalc, AuditNonAtomicCalc]
FILE_MODELS = [AuditFileItem]
GUARDED_FILE_MODELS = [AuditGuardedFile]

AUDIT_SIMPLE = "auditsimpleitem"
AUDIT_ATOMIC = "auditatomiccalc"
AUDIT_NONATOMIC = "auditnonatomiccalc"
AUDIT_FILE = "auditfileitem"
AUDIT_GUARDED = "auditguardedfile"

