"""
Shared models for Cluster 7 — Calculation State Machine.

Each model is scoped to a specific sub-cluster's scenarios:

* :class:`AtomicCalc` / :class:`NonAtomicCalc` — single-model success /
  failure paths. Toggle via ``should_fail``.
* :class:`ParentCalc` / :class:`ChildCalc` / :class:`GrandchildCalc` —
  hierarchy. The parent's ``calculate()`` creates/saves the child with
  ``is_calculated=IN_PROGRESS`` so the child's calc kicks off, and so
  on.
* :class:`FailingCalc` — always raises.

All models use the in-memory ``_should_fail_registry`` class attribute
pattern so the test can flip the failure mode of a *child* that will
be created inside the parent's calculate().
"""

from __future__ import annotations

from django.db import models

from lex.core.models.CalculationModel import CalculationModel
from lex.core.models.LexModel import PermissionResult


def _permissive(cls):
    cls.permission_read = lambda self, uc: PermissionResult.allow_all("cluster 7")
    cls.permission_edit = lambda self, uc: PermissionResult.allow_all("cluster 7")
    cls.permission_create = lambda self, uc: True
    cls.permission_delete = lambda self, uc: True
    cls.permission_list = lambda self, uc: True
    return cls


@_permissive
class AtomicCalc(CalculationModel):
    """Single-node atomic calculation (``is_atomic = True`` default)."""

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
            raise RuntimeError(f"AtomicCalc({self.name!r}) failing on purpose")


@_permissive
class NonAtomicCalc(CalculationModel):
    """Single-node non-atomic calculation (``is_atomic = False``)."""

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
            raise RuntimeError(f"NonAtomicCalc({self.name!r}) failing on purpose")


@_permissive
class ChildCalc(CalculationModel):
    """Leaf child calc — toggleable failure."""

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
            raise RuntimeError(f"ChildCalc({self.name!r}) failing on purpose")


@_permissive
class ParentCalc(CalculationModel):
    """
    Parent calc. Its ``calculate()`` creates a :class:`ChildCalc` and
    triggers the child's calculation by saving it with
    ``is_calculated=IN_PROGRESS``.

    ``child_should_fail`` flips the child's failure mode.
    """

    name = models.CharField(max_length=200)
    child_should_fail = models.BooleanField(default=False)
    calculation_error_message = models.TextField(blank=True, default="")

    is_atomic = True

    class Meta:
        app_label = "lex_app"

    def __str__(self) -> str:  # pragma: no cover
        return self.name

    def calculate(self):
        child = ChildCalc(
            name=f"{self.name}-child",
            should_fail=self.child_should_fail,
        )
        child.is_calculated = CalculationModel.IN_PROGRESS
        child.save()
        if child.is_calculated == CalculationModel.ERROR:
            raise RuntimeError(
                f"ParentCalc({self.name!r}) propagating child failure",
            )


@_permissive
class GrandchildCalc(CalculationModel):
    """Used in a 3-level hierarchy via :class:`MidCalc`."""

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
            raise RuntimeError(
                f"GrandchildCalc({self.name!r}) failing on purpose",
            )


@_permissive
class MidCalc(CalculationModel):
    """Middle link of a 3-level hierarchy. Spawns a GrandchildCalc."""

    name = models.CharField(max_length=200)
    grandchild_should_fail = models.BooleanField(default=False)
    calculation_error_message = models.TextField(blank=True, default="")

    is_atomic = True

    class Meta:
        app_label = "lex_app"

    def __str__(self) -> str:  # pragma: no cover
        return self.name

    def calculate(self):
        gc = GrandchildCalc(
            name=f"{self.name}-gc",
            should_fail=self.grandchild_should_fail,
        )
        gc.is_calculated = CalculationModel.IN_PROGRESS
        gc.save()
        if gc.is_calculated == CalculationModel.ERROR:
            raise RuntimeError(
                f"MidCalc({self.name!r}) propagating grandchild failure",
            )


@_permissive
class FailingCalc(CalculationModel):
    """Always raises in ``calculate()``."""

    name = models.CharField(max_length=200)
    calculation_error_message = models.TextField(blank=True, default="")

    is_atomic = True

    class Meta:
        app_label = "lex_app"

    def __str__(self) -> str:  # pragma: no cover
        return self.name

    def calculate(self):
        raise RuntimeError(f"FailingCalc({self.name!r}) always fails")


ALL_MODELS = [
    AtomicCalc, NonAtomicCalc,
    ChildCalc, ParentCalc,
    GrandchildCalc, MidCalc,
    FailingCalc,
]

ATOMIC = "atomiccalc"
NON_ATOMIC = "nonatomiccalc"
CHILD = "childcalc"
PARENT = "parentcalc"
MID = "midcalc"
GRANDCHILD = "grandchildcalc"
FAILING = "failingcalc"

