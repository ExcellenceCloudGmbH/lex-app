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
class NonAtomicChildCalc(CalculationModel):
    """Leaf child calc with ``is_atomic=False`` for hierarchy matrix tests."""

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
                f"NonAtomicChildCalc({self.name!r}) failing on purpose"
            )


def _run_matrix_parent(parent, child_cls):
    """Shared parent implementation for the atomicity matrix tests."""
    child = child_cls(
        name=f"{parent.name}-child",
        should_fail=parent.child_should_fail,
    )
    child.is_calculated = CalculationModel.IN_PROGRESS
    child.save()
    if child.is_calculated == CalculationModel.ERROR:
        raise RuntimeError(
            f"{type(parent).__name__}({parent.name!r}) propagating child failure"
        )
    if parent.parent_should_fail:
        raise RuntimeError(
            f"{type(parent).__name__}({parent.name!r}) failing after child"
        )


@_permissive
class AtomicParentAtomicChildMatrixCalc(CalculationModel):
    """Atomic parent spawning an atomic child; parent/child failures are toggleable."""

    name = models.CharField(max_length=200)
    parent_should_fail = models.BooleanField(default=False)
    child_should_fail = models.BooleanField(default=False)
    calculation_error_message = models.TextField(blank=True, default="")

    is_atomic = True

    class Meta:
        app_label = "lex_app"

    def __str__(self) -> str:  # pragma: no cover
        return self.name

    def calculate(self):
        _run_matrix_parent(self, ChildCalc)


@_permissive
class AtomicParentNonAtomicChildMatrixCalc(CalculationModel):
    """Atomic parent spawning a non-atomic child."""

    name = models.CharField(max_length=200)
    parent_should_fail = models.BooleanField(default=False)
    child_should_fail = models.BooleanField(default=False)
    calculation_error_message = models.TextField(blank=True, default="")

    is_atomic = True

    class Meta:
        app_label = "lex_app"

    def __str__(self) -> str:  # pragma: no cover
        return self.name

    def calculate(self):
        _run_matrix_parent(self, NonAtomicChildCalc)


@_permissive
class NonAtomicParentAtomicChildMatrixCalc(CalculationModel):
    """Non-atomic parent spawning an atomic child."""

    name = models.CharField(max_length=200)
    parent_should_fail = models.BooleanField(default=False)
    child_should_fail = models.BooleanField(default=False)
    calculation_error_message = models.TextField(blank=True, default="")

    is_atomic = False

    class Meta:
        app_label = "lex_app"

    def __str__(self) -> str:  # pragma: no cover
        return self.name

    def calculate(self):
        _run_matrix_parent(self, ChildCalc)


@_permissive
class NonAtomicParentNonAtomicChildMatrixCalc(CalculationModel):
    """Non-atomic parent spawning a non-atomic child."""

    name = models.CharField(max_length=200)
    parent_should_fail = models.BooleanField(default=False)
    child_should_fail = models.BooleanField(default=False)
    calculation_error_message = models.TextField(blank=True, default="")

    is_atomic = False

    class Meta:
        app_label = "lex_app"

    def __str__(self) -> str:  # pragma: no cover
        return self.name

    def calculate(self):
        _run_matrix_parent(self, NonAtomicChildCalc)


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
class PersistenceProbeCalc(CalculationModel):
    """Cluster 7e fixture — exposes a hook for inspecting persistence mid-calc.

    Used by sub-cluster 7e (persistence internals) to observe what the
    framework has committed to the DB while ``calculate()`` is still
    running. Assign a callable to the *instance* attribute ``_probe``
    before saving with ``is_calculated=IN_PROGRESS``; ``calculate()``
    will invoke it (passing ``self``). Set ``should_fail=True`` to make
    ``calculate()`` raise *after* the probe runs.
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
        probe = getattr(self, "_probe", None)
        if probe is not None:
            probe(self)
        if self.should_fail:
            raise RuntimeError(
                f"PersistenceProbeCalc({self.name!r}) failing on purpose"
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


# ---------------------------------------------------------------------
# 3-level grandparent / parent / child matrix (sub-cluster 7j)
# ---------------------------------------------------------------------
#
# Eight grandparent classes — one per (gp, p, c) atomicity triplet.
# Each grandparent's ``calculate()`` spawns the matching 2-level
# matrix-parent class from above, which itself spawns the matching
# child. We reuse the existing 2-level matrix parents instead of
# defining new "mid" classes because the 2-level matrix already
# encodes (p_atomic × c_atomic) and is exhaustively tested in 7i —
# the 3-level layer only needs to add the GP dimension.
#
# Naming convention: ``Triple_<GP><P><C>_Calc`` where each letter is
# ``A`` (atomic) or ``N`` (non-atomic). Eight classes total.
# ---------------------------------------------------------------------

# Pick the right 2-level matrix parent for a given (p_atomic, c_atomic).
def _matrix_parent_for(p_atomic: bool, c_atomic: bool):
    if p_atomic and c_atomic:
        return AtomicParentAtomicChildMatrixCalc
    if p_atomic and not c_atomic:
        return AtomicParentNonAtomicChildMatrixCalc
    if not p_atomic and c_atomic:
        return NonAtomicParentAtomicChildMatrixCalc
    return NonAtomicParentNonAtomicChildMatrixCalc


def _run_3l_grandparent(gp, parent_cls):
    """Shared grandparent ``calculate()`` body.

    Spawns the configured 2-level matrix parent, then propagates that
    parent's failure (if any), or raises its own failure
    (``gp_should_fail``) only after the parent succeeds. This gives the
    documented failure precedence: ``c_fail`` > ``p_fail`` > ``gp_fail``.
    """
    parent = parent_cls(
        name=f"{gp.name}-p",
        parent_should_fail=gp.p_should_fail,
        child_should_fail=gp.c_should_fail,
    )
    parent.is_calculated = CalculationModel.IN_PROGRESS
    try:
        parent.save()
    except Exception:
        # Failures from the parent calc surface through .save(); the
        # framework persists the row at ERROR via persist_error_state.
        # Re-fetch so we can branch on the persisted terminal state.
        pass

    # Re-read parent's persisted state — it may exist at ERROR even if
    # an atomic ancestor rolled back its IN_PROGRESS row, because
    # ``persist_error_state`` writes outside the transaction.
    try:
        parent.refresh_from_db()
    except Exception:
        # Parent row vanished entirely (atomic GP rollback wiped the
        # IN_PROGRESS row before any failure could be recorded → all
        # three calcs succeeded but GP itself is going to raise). Fall
        # through; gp_should_fail handling below covers it.
        return

    if parent.is_calculated == CalculationModel.ERROR:
        raise RuntimeError(
            f"{type(gp).__name__}({gp.name!r}) propagating parent failure",
        )
    if gp.gp_should_fail:
        raise RuntimeError(
            f"{type(gp).__name__}({gp.name!r}) failing after parent",
        )


# Helper for the 8 thin grandparent class bodies. Each just sets
# ``is_atomic`` and binds the right matrix-parent. Defined inline
# because Django needs an explicit class per model for the ORM /
# migration system.

@_permissive
class Triple_AAA_Calc(CalculationModel):
    """3-level matrix: atomic GP → atomic P → atomic C."""
    name = models.CharField(max_length=200)
    gp_should_fail = models.BooleanField(default=False)
    p_should_fail = models.BooleanField(default=False)
    c_should_fail = models.BooleanField(default=False)
    calculation_error_message = models.TextField(blank=True, default="")

    is_atomic = True

    class Meta:
        app_label = "lex_app"

    def __str__(self) -> str:  # pragma: no cover
        return self.name

    def calculate(self):
        _run_3l_grandparent(self, AtomicParentAtomicChildMatrixCalc)


@_permissive
class Triple_AAN_Calc(CalculationModel):
    """3-level matrix: atomic GP → atomic P → non-atomic C."""
    name = models.CharField(max_length=200)
    gp_should_fail = models.BooleanField(default=False)
    p_should_fail = models.BooleanField(default=False)
    c_should_fail = models.BooleanField(default=False)
    calculation_error_message = models.TextField(blank=True, default="")

    is_atomic = True

    class Meta:
        app_label = "lex_app"

    def __str__(self) -> str:  # pragma: no cover
        return self.name

    def calculate(self):
        _run_3l_grandparent(self, AtomicParentNonAtomicChildMatrixCalc)


@_permissive
class Triple_ANA_Calc(CalculationModel):
    """3-level matrix: atomic GP → non-atomic P → atomic C."""
    name = models.CharField(max_length=200)
    gp_should_fail = models.BooleanField(default=False)
    p_should_fail = models.BooleanField(default=False)
    c_should_fail = models.BooleanField(default=False)
    calculation_error_message = models.TextField(blank=True, default="")

    is_atomic = True

    class Meta:
        app_label = "lex_app"

    def __str__(self) -> str:  # pragma: no cover
        return self.name

    def calculate(self):
        _run_3l_grandparent(self, NonAtomicParentAtomicChildMatrixCalc)


@_permissive
class Triple_ANN_Calc(CalculationModel):
    """3-level matrix: atomic GP → non-atomic P → non-atomic C."""
    name = models.CharField(max_length=200)
    gp_should_fail = models.BooleanField(default=False)
    p_should_fail = models.BooleanField(default=False)
    c_should_fail = models.BooleanField(default=False)
    calculation_error_message = models.TextField(blank=True, default="")

    is_atomic = True

    class Meta:
        app_label = "lex_app"

    def __str__(self) -> str:  # pragma: no cover
        return self.name

    def calculate(self):
        _run_3l_grandparent(self, NonAtomicParentNonAtomicChildMatrixCalc)


@_permissive
class Triple_NAA_Calc(CalculationModel):
    """3-level matrix: non-atomic GP → atomic P → atomic C."""
    name = models.CharField(max_length=200)
    gp_should_fail = models.BooleanField(default=False)
    p_should_fail = models.BooleanField(default=False)
    c_should_fail = models.BooleanField(default=False)
    calculation_error_message = models.TextField(blank=True, default="")

    is_atomic = False

    class Meta:
        app_label = "lex_app"

    def __str__(self) -> str:  # pragma: no cover
        return self.name

    def calculate(self):
        _run_3l_grandparent(self, AtomicParentAtomicChildMatrixCalc)


@_permissive
class Triple_NAN_Calc(CalculationModel):
    """3-level matrix: non-atomic GP → atomic P → non-atomic C."""
    name = models.CharField(max_length=200)
    gp_should_fail = models.BooleanField(default=False)
    p_should_fail = models.BooleanField(default=False)
    c_should_fail = models.BooleanField(default=False)
    calculation_error_message = models.TextField(blank=True, default="")

    is_atomic = False

    class Meta:
        app_label = "lex_app"

    def __str__(self) -> str:  # pragma: no cover
        return self.name

    def calculate(self):
        _run_3l_grandparent(self, AtomicParentNonAtomicChildMatrixCalc)


@_permissive
class Triple_NNA_Calc(CalculationModel):
    """3-level matrix: non-atomic GP → non-atomic P → atomic C."""
    name = models.CharField(max_length=200)
    gp_should_fail = models.BooleanField(default=False)
    p_should_fail = models.BooleanField(default=False)
    c_should_fail = models.BooleanField(default=False)
    calculation_error_message = models.TextField(blank=True, default="")

    is_atomic = False

    class Meta:
        app_label = "lex_app"

    def __str__(self) -> str:  # pragma: no cover
        return self.name

    def calculate(self):
        _run_3l_grandparent(self, NonAtomicParentAtomicChildMatrixCalc)


@_permissive
class Triple_NNN_Calc(CalculationModel):
    """3-level matrix: non-atomic GP → non-atomic P → non-atomic C."""
    name = models.CharField(max_length=200)
    gp_should_fail = models.BooleanField(default=False)
    p_should_fail = models.BooleanField(default=False)
    c_should_fail = models.BooleanField(default=False)
    calculation_error_message = models.TextField(blank=True, default="")

    is_atomic = False

    class Meta:
        app_label = "lex_app"

    def __str__(self) -> str:  # pragma: no cover
        return self.name

    def calculate(self):
        _run_3l_grandparent(self, NonAtomicParentNonAtomicChildMatrixCalc)


TRIPLE_CLASSES = {
    (True,  True,  True):  Triple_AAA_Calc,
    (True,  True,  False): Triple_AAN_Calc,
    (True,  False, True):  Triple_ANA_Calc,
    (True,  False, False): Triple_ANN_Calc,
    (False, True,  True):  Triple_NAA_Calc,
    (False, True,  False): Triple_NAN_Calc,
    (False, False, True):  Triple_NNA_Calc,
    (False, False, False): Triple_NNN_Calc,
}


ALL_MODELS = [
    AtomicCalc, NonAtomicCalc,
    ChildCalc, NonAtomicChildCalc,
    AtomicParentAtomicChildMatrixCalc,
    AtomicParentNonAtomicChildMatrixCalc,
    NonAtomicParentAtomicChildMatrixCalc,
    NonAtomicParentNonAtomicChildMatrixCalc,
    ParentCalc,
    GrandchildCalc, MidCalc,
    NonAtomicParentCalc,
    FailingCalc,
    PersistenceProbeCalc,
    # 3-level matrix grandparents (sub-cluster 7j)
    Triple_AAA_Calc, Triple_AAN_Calc, Triple_ANA_Calc, Triple_ANN_Calc,
    Triple_NAA_Calc, Triple_NAN_Calc, Triple_NNA_Calc, Triple_NNN_Calc,
    # Combinatorial / create()-pipeline model (7g + 7h).
    # (Declared below so the mixin import is nearby.)
]

ATOMIC = "atomiccalc"
NON_ATOMIC = "nonatomiccalc"
CHILD = "childcalc"
NON_ATOMIC_CHILD = "nonatomicchildcalc"
PARENT = "parentcalc"
MID = "midcalc"
GRANDCHILD = "grandchildcalc"
FAILING = "failingcalc"


# ---------------------------------------------------------------------
# Combinatorial / create() pipeline fixture (7g + 7h)
# ---------------------------------------------------------------------
from lex.core.mixins.CalculatedModelMixin import CalculatedModelMixin  # noqa: E402


class CombinatorialCalc(CalculatedModelMixin):
    """Cartesian-product calculation over ``region × category``.

    Used by sub-clusters 7g (sync ``create()`` pipeline) and 7h
    (Celery-dispatch branch of ``_dispatch_model_processing``). The
    key-list class attributes are reset in each test's ``setUp``.
    """

    region = models.CharField(max_length=16)
    category = models.CharField(max_length=16)
    name = models.CharField(max_length=200, blank=True, default="")

    defining_fields = ["region", "category"]
    parallelizable_fields = ["region"]

    # Mutable class-level test knobs — reset per test.
    _region_keys = ["US", "EU"]
    _category_keys = ["A", "B"]
    fail_for_region: "str | None" = None

    is_atomic = True

    class Meta:
        app_label = "lex_app"

    def __str__(self) -> str:  # pragma: no cover
        return self.name or f"{self.region}-{self.category}"

    def get_selected_key_list(self, key):
        if key == "region":
            return list(type(self)._region_keys)
        if key == "category":
            return list(type(self)._category_keys)
        return []

    def calculate(self, *args, **kwargs):
        if (
            type(self).fail_for_region is not None
            and self.region == type(self).fail_for_region
        ):
            raise RuntimeError(
                f"CombinatorialCalc failing on purpose for region={self.region!r}"
            )
        self.name = f"{self.region}-{self.category}"

    def permission_read(self, uc):
        return PermissionResult.allow_all("cluster 7: combinatorial")

    def permission_edit(self, uc):
        return PermissionResult.allow_all("cluster 7: combinatorial")

    def permission_create(self, uc):
        return True

    def permission_delete(self, uc):
        return True

    def permission_list(self, uc):
        return True


ALL_MODELS.append(CombinatorialCalc)
COMBINATORIAL = "combinatorialcalc"


@_permissive
class FKAbortTarget(LexModel):
    """Target row used to force FK violations in Cluster 7o."""

    name = models.CharField(max_length=64)

    class Meta:
        app_label = "lex_app"


@_permissive
class FKAbortWrite(LexModel):
    """Write emitted from calculate(); FK must stay valid."""

    marker = models.CharField(max_length=32)
    target = models.ForeignKey(FKAbortTarget, on_delete=models.CASCADE)

    class Meta:
        app_label = "lex_app"


class FKViolationAbortCalc(CalculatedModelMixin):
    """CalculatedModelMixin fixture that emits one bad FK write mid-batch."""

    step = models.CharField(max_length=32)
    name = models.CharField(max_length=64, blank=True, default="")

    defining_fields = ["step"]
    parallelizable_fields = []
    _steps = ["before", "bad", "after"]
    _valid_target_id = None

    class Meta:
        app_label = "lex_app"

    def get_selected_key_list(self, key):
        if key == "step":
            return list(type(self)._steps)
        return []

    def calculate(self, *args, **kwargs):
        target_id = (
            -999999
            if self.step == "bad"
            else type(self)._valid_target_id
        )
        FKAbortWrite.objects.create(marker=self.step, target_id=target_id)
        self.name = self.step

    def permission_read(self, uc):
        return PermissionResult.allow_all("cluster 7: fk abort")

    def permission_edit(self, uc):
        return PermissionResult.allow_all("cluster 7: fk abort")

    def permission_create(self, uc):
        return True

    def permission_delete(self, uc):
        return True

    def permission_list(self, uc):
        return True


ALL_MODELS.extend([FKAbortTarget, FKAbortWrite, FKViolationAbortCalc])
