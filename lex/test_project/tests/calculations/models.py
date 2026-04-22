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

from lex.core.mixins.CalculatedModelMixin import CalculatedModelMixin
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
class NonAtomicParentCalc(CalculationModel):
    """
    Non-atomic parent that spawns an atomic :class:`ChildCalc`.

    Used by scenario 7.7 to prove that error-propagation works
    regardless of ``is_atomic`` on the *parent* — the child's failure
    must still trail up and mark the parent as ERROR.
    """

    name = models.CharField(max_length=200)
    child_should_fail = models.BooleanField(default=False)
    calculation_error_message = models.TextField(blank=True, default="")

    is_atomic = False

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
                f"NonAtomicParentCalc({self.name!r}) propagating child "
                "failure",
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


@_permissive
class CombinatorialCalc(CalculatedModelMixin):
    """
    Test model for Cluster 7g — exercises the full ``create()`` pipeline.

    Declares two ``defining_fields`` (``region``, ``category``) and one
    ``parallelizable_field`` (``region``) so that
    :py:meth:`CalculatedModelMixin.create` walks through all four steps:
    combination generation → preparation (duplicate handling) →
    clustering → dispatch (sync because ``CELERY_ACTIVE=False`` in CI).

    ``get_selected_key_list`` returns the configured key ring per field so
    that ``Model.create()`` without kwargs still expands. Tests can flip
    ``fail_for_region`` on the class to have one of the combinations
    raise inside ``calculate`` — this exercises the partial-failure
    branch in :func:`calc_and_save_sync`.
    """

    name = models.CharField(max_length=200, blank=True, default="")
    region = models.CharField(max_length=50, blank=True, default="")
    category = models.CharField(max_length=50, blank=True, default="")
    calculation_error_message = models.TextField(blank=True, default="")

    is_atomic = False
    defining_fields = ["region", "category"]
    parallelizable_fields = ["region"]

    # Class-level registries — tests toggle these via setUp/tearDown.
    _region_keys: list[str] = ["US", "EU"]
    _category_keys: list[str] = ["A", "B"]
    fail_for_region: str | None = None

    class Meta:
        app_label = "lex_app"

    def __str__(self) -> str:  # pragma: no cover
        return f"{self.region}/{self.category}"

    def get_selected_key_list(self, field_name):
        if field_name == "region":
            return list(type(self)._region_keys)
        if field_name == "category":
            return list(type(self)._category_keys)
        return []

    def calculate(self):
        if type(self).fail_for_region and self.region == type(self).fail_for_region:
            raise RuntimeError(
                f"CombinatorialCalc({self.region!r}/{self.category!r}) "
                "failing on purpose",
            )
        self.name = f"{self.region}-{self.category}"


ALL_MODELS = [
    AtomicCalc, NonAtomicCalc,
    ChildCalc, ParentCalc,
    GrandchildCalc, MidCalc,
    NonAtomicParentCalc,
    FailingCalc,
    CombinatorialCalc,
]

ATOMIC = "atomiccalc"
NON_ATOMIC = "nonatomiccalc"
CHILD = "childcalc"
PARENT = "parentcalc"
MID = "midcalc"
GRANDCHILD = "grandchildcalc"
FAILING = "failingcalc"

