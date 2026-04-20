"""
Shared models for Cluster 8 — Celery & Async.

* :class:`CeleryCalc` — a :class:`CalculationModel` with a
  ``@lex_shared_task``-decorated ``calculate()``. When
  ``CELERY_ACTIVE=False`` we expect the framework to fall back to
  synchronous execution (the happy path for the vast majority of
  customers who never turn Celery on).
* :class:`CelerySyncCalc` — no task decorator; exists so we can
  exercise ``should_use_celery()`` returning False due to *absence*
  of a ``.delay`` attribute (independent of env vars).
"""

from __future__ import annotations

from django.db import models

from lex.core.models.CalculationModel import CalculationModel
from lex.core.models.LexModel import PermissionResult


def _permissive(cls):
    cls.permission_read = lambda self, uc: PermissionResult.allow_all("cluster 8")
    cls.permission_edit = lambda self, uc: PermissionResult.allow_all("cluster 8")
    cls.permission_create = lambda self, uc: True
    cls.permission_delete = lambda self, uc: True
    cls.permission_list = lambda self, uc: True
    return cls


@_permissive
class CelerySyncCalc(CalculationModel):
    """Plain calc — no task decorator. should_use_celery must return False."""

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
            raise RuntimeError(f"CelerySyncCalc({self.name!r}) failing on purpose")


@_permissive
class CeleryCalc(CalculationModel):
    """
    Celery-aware calc — ``calculate()`` is wrapped with
    ``@lex_shared_task`` if the framework exposes the decorator. If
    not importable at module-load time we fall back to a plain method
    and the Cluster-8 tests that require the decorator are skipped.
    """

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
            raise RuntimeError(f"CeleryCalc({self.name!r}) failing on purpose")


try:  # pragma: no cover — environment dependent
    from lex.lex_app.celery_tasks import lex_shared_task

    CeleryCalc.calculate = lex_shared_task()(CeleryCalc.calculate)
    CELERY_DECORATOR_AVAILABLE = True
except Exception:  # pragma: no cover
    CELERY_DECORATOR_AVAILABLE = False


ALL_MODELS = [CelerySyncCalc, CeleryCalc]

CELERY_SYNC = "celerysynccalc"
CELERY = "celerycalc"

