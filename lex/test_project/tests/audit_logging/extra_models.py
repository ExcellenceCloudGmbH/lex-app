"""
Cluster 6 — extended audit-logging fixtures.

Adds models the new gap sub-clusters 6d-6g need that the original
``models.py`` does not carry:

* ``AuditPreValItem`` — has a ``pre_validation`` hook that can be
  toggled to raise on demand. Drives 6.45-6.47 (failure-path audit
  rows reachable today, replaces the previously-skipped 6.4).

The original ``AuditSimpleItem`` / ``AuditAtomicCalc`` are unchanged.
"""

from __future__ import annotations

from django.db import models
from lex.core.exceptions import ValidationError as LexValidationError
from lex.core.models.LexModel import LexModel, PermissionResult


def _permissive(cls):
    cls.permission_read = lambda self, uc: PermissionResult.allow_all("cluster 6")
    cls.permission_edit = lambda self, uc: PermissionResult.allow_all("cluster 6")
    cls.permission_create = lambda self, uc: True
    cls.permission_delete = lambda self, uc: True
    cls.permission_list = lambda self, uc: True
    return cls


@_permissive
class AuditPreValItem(LexModel):
    """LexModel with a togglable ``pre_validation`` raise.

    Used by 6d failure-path scenarios: setting class attribute
    ``_should_fail_prevalidation = True`` makes the next save raise
    a :class:`LexValidationError` from ``pre_validation``.
    """

    name = models.CharField(max_length=200)
    value = models.IntegerField(default=0)

    _should_fail_prevalidation = False

    class Meta:
        app_label = "lex_app"

    def __str__(self) -> str:  # pragma: no cover
        return self.name

    def pre_validation(self):
        if type(self)._should_fail_prevalidation:
            raise LexValidationError(
                f"Pre-validation rejected {self.name!r} on purpose"
            )


AUDIT_PREVAL = "auditprevalitem"


