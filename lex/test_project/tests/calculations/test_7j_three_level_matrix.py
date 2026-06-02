"""
Cluster 7j: Grandparent / parent / child atomicity matrix.

Extends sub-cluster 7i (parent/child) with a third level. Each of the
three calcs is independently:

* atomic or non-atomic   (3 booleans → 8 atomicity triplets)
* configured to fail or pass at the appropriate moment in its
  ``calculate()``                       (3 booleans → 8 outcome triplets)

Total: **8 × 8 = 64 scenarios**, scenario IDs 7.48 – 7.111.

Two contracts are pinned by the matrix:

1. **Failure precedence:** ``c_fail > p_fail > gp_fail``. A child failure
   short-circuits the parent's own failure code path and re-raises;
   the parent's failure short-circuits the grandparent's. This is
   the same convention sub-cluster 7i pinned at 2 levels — 7j proves
   it composes through 3.

2. **Atomicity-driven rollback** of a *successful* descendant when
   an ancestor fails: the descendant's row survives at SUCCESS only
   if **no atomic node sits between the descendant's save and the
   failing ancestor (inclusive)**. A failed calc itself always lands
   at ERROR via ``persist_error_state``, which writes outside any
   transaction and therefore survives an ancestor's atomic rollback.

The 64 scenarios are generated at class-definition time via a loop
that ``setattr``-s one method per (atomicity, outcome) pair onto the
test class. Each method asserts the expected (gp_state, p_state,
c_state) triple. ``None`` means "the row was rolled back and never
existed in the persisted state" (which is distinct from ERROR).

Method-name convention: ``test_7_<NN>_<atomicity>_<outcome>``, where
``<atomicity>`` is e.g. ``aaa`` (atomic / atomic / atomic) and
``<outcome>`` is the three letters T/F for gp_fail/p_fail/c_fail.
Example: ``test_7_75_ana_TFT`` — atomic-GP, non-atomic-P, atomic-C,
gp configured to fail, p not, c configured to fail.
"""

from __future__ import annotations

import unittest

from django.core.exceptions import ObjectDoesNotExist
from lex.core.models.CalculationModel import CalculationModel
from lex.tests.e2e._e2e_test_case import E2ETestCase

from .models import (
    ALL_MODELS,
    AtomicParentAtomicChildMatrixCalc,
    AtomicParentNonAtomicChildMatrixCalc,
    ChildCalc,
    NonAtomicChildCalc,
    NonAtomicParentAtomicChildMatrixCalc,
    NonAtomicParentNonAtomicChildMatrixCalc,
    TRIPLE_CLASSES,
)

import pytest

pytestmark = pytest.mark.calculations

# ---------------------------------------------------------------------
# Per-(p_atomic, c_atomic) → (parent_cls, child_cls) lookup so the
# test runner can fetch the persisted "middle" and "leaf" rows after
# the calc chain.
# ---------------------------------------------------------------------
_PARENT_CHILD_LOOKUP = {
    (True,  True):  (AtomicParentAtomicChildMatrixCalc, ChildCalc),
    (True,  False): (AtomicParentNonAtomicChildMatrixCalc, NonAtomicChildCalc),
    (False, True):  (NonAtomicParentAtomicChildMatrixCalc, ChildCalc),
    (False, False): (NonAtomicParentNonAtomicChildMatrixCalc, NonAtomicChildCalc),
}


# ---------------------------------------------------------------------
# Expected-state computation. Mirror of the documented contract above.
# Returns a 3-tuple (gp_state, p_state, c_state) where each is a
# CalculationModel.* constant or ``None`` (= row rolled back, no
# persisted row in the DB).
# ---------------------------------------------------------------------
def _expected_states(
    gp_atomic: bool, p_atomic: bool, c_atomic: bool,
    gp_fail: bool,   p_fail: bool,   c_fail: bool,
) -> tuple[str | None, str | None, str | None]:
    """Compute (gp_state, p_state, c_state) for one matrix cell.

    Two atomicity rules govern the matrix, and they are *different*
    for failing levels vs successful ones:

    1. **Failure precedence: ``c_fail > p_fail > gp_fail``.** A child
       failure short-circuits the parent's own failure code (the
       parent re-raises immediately); the parent's failure
       short-circuits the grandparent's. Therefore::

           gp_raised = gp_fail or p_fail or c_fail
           p_raised  = p_fail or c_fail
           c_raised  = c_fail

    2. **Failing level — only the outermost atomic ancestor wipes
       it.** A raising calc's ERROR row is written through a path
       that *survives* nested rollbacks (its own atomic savepoint;
       any intermediate atomic ancestor that also rolls back). Only
       the **outermost** atomic ancestor whose rollback collapses
       the entire connection wipes it. In a 3-level chain the
       outermost candidate is GP::

           failing_level_wiped = (level != 'GP') and gp_atomic and gp_raised

       A failing level X = ERROR if not wiped; None otherwise. GP
       has no outer ancestor and therefore always lands at ERROR
       when it raises.

    3. **Successful descendant — any atomic ancestor that raises
       wipes it.** A normal ``save()`` writes through the active
       connection-level transaction. When any atomic ancestor's
       block rolls back, that descendant's IN_PROGRESS / SUCCESS
       rows go with it::

           p_succ_wiped = gp_atomic and gp_raised
           c_succ_wiped =
               (p_atomic and p_raised) or (gp_atomic and gp_raised)

    These are the same rules the 7i 2-level matrix obeys; 7j just
    adds the third level which makes rule 2 distinguishable from
    rule 3 (in the 2-level matrix the "outermost" candidate IS the
    only ancestor, so the two coincide).
    """
    SUCCESS = CalculationModel.SUCCESS
    ERROR = CalculationModel.ERROR

    gp_raised = gp_fail or p_fail or c_fail
    p_raised  = p_fail  or c_fail
    c_raised  = c_fail

    outer_wipes = gp_atomic and gp_raised  # only GP wipes a failing level

    # GP: no outer ancestor, always survives at the appropriate state.
    gp_state: str | None = ERROR if gp_raised else SUCCESS

    # P:
    if p_raised:
        p_state: str | None = None if outer_wipes else ERROR
    elif outer_wipes:
        # P succeeded but GP rolled back over it.
        p_state = None
    else:
        p_state = SUCCESS

    # C:
    if c_raised:
        c_state: str | None = None if outer_wipes else ERROR
    elif (p_atomic and p_raised) or outer_wipes:
        # C succeeded but P or GP rolled back over it.
        c_state = None
    else:
        c_state = SUCCESS

    return (gp_state, p_state, c_state)


def _atomicity_label(gp_atomic, p_atomic, c_atomic) -> str:
    """``(True, False, True)`` → ``'ana'`` (atomic / non-atomic / atomic)."""
    return "".join("a" if a else "n" for a in (gp_atomic, p_atomic, c_atomic))


def _outcome_label(gp_fail, p_fail, c_fail) -> str:
    """``(False, True, False)`` → ``'FTF'`` — gp/p/c fail flags."""
    return "".join("T" if f else "F" for f in (gp_fail, p_fail, c_fail))


# ---------------------------------------------------------------------
# Test class with parametrically generated methods.
# ---------------------------------------------------------------------
class TestCluster07j_ThreeLevelMatrix(E2ETestCase):
    """Exhaustive 3-level grandparent/parent/child atomicity & failure
    propagation matrix — 64 scenarios.

    The test methods are generated at class-load time below. Run the
    full cluster with::

        lex test lex.test_project.tests.calculations.test_7j_three_level_matrix \
            --verbosity=2 --noinput

    Each method's docstring documents (a) the atomicity triplet,
    (b) the failure flags, and (c) the expected (gp, p, c) terminal
    states.
    """

    e2e_models = ALL_MODELS

    def _run_3l_case(
        self,
        *,
        gp_atomic: bool, p_atomic: bool, c_atomic: bool,
        gp_fail: bool,   p_fail: bool,   c_fail: bool,
        scenario: str,
    ) -> None:
        """Drive one matrix cell and assert the persisted terminal state.

        ``scenario`` is the human-readable scenario id (e.g.
        ``"7.75 (ana / TFT)"``) — it appears in every assertion
        message so a CI failure log identifies the cell directly.
        """
        gp_cls = TRIPLE_CLASSES[(gp_atomic, p_atomic, c_atomic)]
        parent_cls, child_cls = _PARENT_CHILD_LOOKUP[(p_atomic, c_atomic)]

        gp_name = f"3l-{_atomicity_label(gp_atomic, p_atomic, c_atomic)}-" \
                  f"{_outcome_label(gp_fail, p_fail, c_fail)}"

        gp = gp_cls(
            name=gp_name,
            gp_should_fail=gp_fail,
            p_should_fail=p_fail,
            c_should_fail=c_fail,
        )
        gp.is_calculated = CalculationModel.IN_PROGRESS
        try:
            gp.save()
        except Exception:
            # Failures surface through .save(). The contract under
            # test is the persisted terminal state — re-fetch below.
            pass

        # --- Fetch persisted state for all three levels --------------
        # GP first: its row is written at ERROR via persist_error_state
        # if it (or any descendant) raised, and at SUCCESS via the
        # normal save path otherwise. It always exists.
        try:
            gp.refresh_from_db()
            gp_state: str | None = gp.is_calculated
        except ObjectDoesNotExist:  # pragma: no cover - sanity guard
            gp_state = None

        # Parent: ``-p`` suffix per the convention in
        # _run_3l_grandparent.
        p_state: str | None = None
        try:
            p_obj = parent_cls.objects.get(name=f"{gp_name}-p")
            p_state = p_obj.is_calculated
        except ObjectDoesNotExist:
            p_state = None

        # Child: ``-p-child`` suffix because the matrix-parent
        # appends ``-child`` to its own name.
        c_state: str | None = None
        try:
            c_obj = child_cls.objects.get(name=f"{gp_name}-p-child")
            c_state = c_obj.is_calculated
        except ObjectDoesNotExist:
            c_state = None

        # --- Compute and compare expectations ------------------------
        exp_gp, exp_p, exp_c = _expected_states(
            gp_atomic, p_atomic, c_atomic,
            gp_fail, p_fail, c_fail,
        )

        self.assertEqual(
            gp_state, exp_gp,
            f"[{scenario}] GP terminal state drift. "
            f"atomicity=(gp={gp_atomic}, p={p_atomic}, c={c_atomic}); "
            f"fail=(gp={gp_fail}, p={p_fail}, c={c_fail}); "
            f"expected gp={exp_gp!r}, got {gp_state!r}.",
        )
        self.assertEqual(
            p_state, exp_p,
            f"[{scenario}] Parent terminal state drift. "
            f"atomicity=(gp={gp_atomic}, p={p_atomic}, c={c_atomic}); "
            f"fail=(gp={gp_fail}, p={p_fail}, c={c_fail}); "
            f"expected p={exp_p!r}, got {p_state!r} "
            f"(None = row rolled back, not persisted at all).",
        )
        self.assertEqual(
            c_state, exp_c,
            f"[{scenario}] Child terminal state drift. "
            f"atomicity=(gp={gp_atomic}, p={p_atomic}, c={c_atomic}); "
            f"fail=(gp={gp_fail}, p={p_fail}, c={c_fail}); "
            f"expected c={exp_c!r}, got {c_state!r} "
            f"(None = row rolled back, not persisted at all).",
        )


# ---------------------------------------------------------------------
# Method generation. Iterates all 8 atomicity triplets × 8 outcome
# triplets and binds one ``test_7_NN_<atomicity>_<outcome>`` method
# per cell to the test class above.
# ---------------------------------------------------------------------
def _generate_matrix_tests() -> None:
    seq = 0
    for gp_atomic in (True, False):
        for p_atomic in (True, False):
            for c_atomic in (True, False):
                for gp_fail in (False, True):
                    for p_fail in (False, True):
                        for c_fail in (False, True):
                            scenario_num = 48 + seq
                            atomicity = _atomicity_label(
                                gp_atomic, p_atomic, c_atomic,
                            )
                            outcome = _outcome_label(
                                gp_fail, p_fail, c_fail,
                            )
                            method_name = (
                                f"test_7_{scenario_num}_{atomicity}_{outcome}"
                            )
                            scenario = f"7.{scenario_num} ({atomicity} / {outcome})"

                            # Snapshot loop variables into default-
                            # argument values so the closure binds the
                            # cell-specific config, not the loop's
                            # final state.
                            def _runner(
                                self,
                                _gp_atomic=gp_atomic, _p_atomic=p_atomic,
                                _c_atomic=c_atomic,
                                _gp_fail=gp_fail, _p_fail=p_fail,
                                _c_fail=c_fail,
                                _scenario=scenario,
                            ):
                                self._run_3l_case(
                                    gp_atomic=_gp_atomic,
                                    p_atomic=_p_atomic,
                                    c_atomic=_c_atomic,
                                    gp_fail=_gp_fail,
                                    p_fail=_p_fail,
                                    c_fail=_c_fail,
                                    scenario=_scenario,
                                )

                            _runner.__doc__ = (
                                f"Scenario {scenario}: gp_atomic={gp_atomic}, "
                                f"p_atomic={p_atomic}, c_atomic={c_atomic}, "
                                f"gp_fail={gp_fail}, p_fail={p_fail}, "
                                f"c_fail={c_fail}."
                            )
                            _runner.__name__ = method_name

                            setattr(
                                TestCluster07j_ThreeLevelMatrix,
                                method_name,
                                _runner,
                            )
                            seq += 1
    assert seq == 64, f"3-level matrix must enumerate 64 cells; got {seq}"


_generate_matrix_tests()


if __name__ == "__main__":  # pragma: no cover
    unittest.main()




