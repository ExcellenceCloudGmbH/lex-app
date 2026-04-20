"""
Shared models for Cluster 10 — API Layer.

Per rule #3 we keep dedicated models even though the shapes overlap
with Cluster 2 / 7. The cluster asserts the *HTTP* contract — the
observable REST-layer wiring on top of everything below.
"""

from __future__ import annotations

from django.db import models

from lex.core.models.CalculationModel import CalculationModel
from lex.core.models.LexModel import LexModel, PermissionResult


def _permissive(cls):
    cls.permission_read = lambda self, uc: PermissionResult.allow_all("cluster 10")
    cls.permission_edit = lambda self, uc: PermissionResult.allow_all("cluster 10")
    cls.permission_create = lambda self, uc: True
    cls.permission_delete = lambda self, uc: True
    cls.permission_list = lambda self, uc: True
    return cls


@_permissive
class ApiSimpleItem(LexModel):
    """Plain LexModel for CRUD + history endpoint tests."""

    name = models.CharField(max_length=200)
    value = models.IntegerField(default=0)

    class Meta:
        app_label = "lex_app"

    def __str__(self) -> str:  # pragma: no cover
        return self.name


@_permissive
class ApiAtomicCalc(CalculationModel):
    """Atomic calc for the API-trigger scenario (10.8)."""

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
            raise RuntimeError(f"ApiAtomicCalc({self.name!r}) failing on purpose")


ALL_MODELS = [ApiSimpleItem, ApiAtomicCalc]

API_SIMPLE = "apisimpleitem"
API_ATOMIC = "apiatomiccalc"

