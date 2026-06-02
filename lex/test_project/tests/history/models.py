"""
Shared models for Cluster 5 — History & Bitemporal.

Each cluster owns its models (rule #3, no cross-cluster imports). Here
we define:

* :class:`HistSimpleItem` — a plain :class:`LexModel` for the basic
  history tests (create / update / delete / multi-update / skip).
* :class:`HistAtomicCalc` — a :class:`CalculationModel` with the default
  ``is_atomic=True``; used to assert the calculation-history trail
  (NOT_CALCULATED → IN_PROGRESS → SUCCESS/ERROR) and the
  "IN_PROGRESS row survives a failed atomic calc" intent (BUG-001).
* :class:`HistNonAtomicCalc` — same, but with ``is_atomic = False``.
"""

from __future__ import annotations

from django.db import models
from lex.core.models.CalculationModel import CalculationModel
from lex.core.models.LexModel import LexModel, PermissionResult


def _permissive(cls):
    """Attach wide-open permission methods — Cluster 4 covers authz."""

    def _read(self, uc):
        return PermissionResult.allow_all("cluster 5")

    def _edit(self, uc):
        return PermissionResult.allow_all("cluster 5")

    cls.permission_read = _read
    cls.permission_edit = _edit
    cls.permission_create = lambda self, uc: True
    cls.permission_delete = lambda self, uc: True
    cls.permission_list = lambda self, uc: True
    return cls


@_permissive
class HistSimpleItem(LexModel):
    """Plain LexModel — the workhorse for basic history-trail tests."""

    name = models.CharField(max_length=200)
    value = models.IntegerField(default=0)

    class Meta:
        app_label = "lex_app"

    def __str__(self) -> str:  # pragma: no cover
        return self.name


@_permissive
class HistAtomicCalc(CalculationModel):
    """Atomic calculation — toggleable success/failure via ``should_fail``."""

    name = models.CharField(max_length=200)
    should_fail = models.BooleanField(default=False)
    calculation_error_message = models.TextField(blank=True, default="")

    # is_atomic default is True (inherited); we make it explicit.
    is_atomic = True

    class Meta:
        app_label = "lex_app"

    def __str__(self) -> str:  # pragma: no cover
        return self.name

    def calculate(self):
        if self.should_fail:
            raise RuntimeError(f"HistAtomicCalc({self.name!r}) failing on purpose")


@_permissive
class HistNonAtomicCalc(CalculationModel):
    """Non-atomic calculation (``is_atomic = False``)."""

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
            raise RuntimeError(f"HistNonAtomicCalc({self.name!r}) failing on purpose")


ALL_MODELS = [HistSimpleItem, HistAtomicCalc, HistNonAtomicCalc]

HIST_SIMPLE = "histsimpleitem"
HIST_ATOMIC = "histatomiccalc"
HIST_NON_ATOMIC = "histnonatomiccalc"

