# Liveness-aware Startup Sweep Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
> **Also REQUIRED:** Use the `lex-testing` skill for every test task — it governs cluster allocation (cluster 8, next-free-letter **8x**), the Step 5 confirmation gate, coverage pairing, and the Step 7 plan-file sync.

**Goal:** Make the startup `IN_PROGRESS → ABORTED` calculation sweep defer to the recovery registry, so calculations owned by a live or recoverable worker are never aborted out from under the machinery that would finish or resume them.

**Architecture:** Add one pure helper `tracked_calculation_record_ids()` to the recovery supervisor that reports every `(model_label, pk)` a tracked recovery task owns (alive *or* expired-but-tracked). The startup sweep (`_handle_calculation_model_reset`) consults that set and skips owned rows, aborting only genuinely untracked ones. When recovery is off / Redis is down, the set is empty and behavior is identical to today.

**Tech Stack:** Django, Celery, Redis-backed recovery registry, pytest via `python -m lex pytest`, `SimpleTestCase` + `E2ETestCase` (TransactionTestCase).

**Reference spec:** [docs/superpowers/specs/2026-06-08-liveness-aware-startup-sweep-design.md](../specs/2026-06-08-liveness-aware-startup-sweep-design.md)

---

## File Structure

- **Modify** `lex/lex_app/celery_recovery/supervisor.py` — add `tracked_calculation_record_ids()` beside the existing `_extract_calculation_models`. Pure read of the registry; reuses the extractor.
- **Modify** `lex/process_admin/utils/model_registration.py`
  - `_handle_calculation_model_reset` — add optional `tracked_record_ids` param + the skip-if-owned filter (`_meta.label_lower, pk`).
  - `register_models` loop (~line 96) — compute the set once and pass it down.
- **Create** `lex/test_project/tests/celery_async/test_8x_startup_reset_recovery_handoff.py` — the paired tests (cluster 8x, scenarios 8.103–8.114). Reuses `CelerySyncCalc` from `tests/celery_async/models.py` and `E2ETestCase`.
- **Sync (Step 7, via lex-testing)** the four plan files: `test-clusters.md`, `progress/dashboard.md`, `test-writing-plan.md`, `progress/session-log.md`.

---

## Task 1: `tracked_calculation_record_ids()` helper + its unit tests (8.103–8.108)

**Files:**
- Modify: `lex/lex_app/celery_recovery/supervisor.py` (add function after `_extract_calculation_models`, which ends at line 121)
- Create: `lex/test_project/tests/celery_async/test_8x_startup_reset_recovery_handoff.py`

- [ ] **Step 1: Write the failing unit tests (Component A)**

Create the test file with the module header and the `tracked_calculation_record_ids` test class. (Component B classes are added in Task 2 — leave them out for now so the file runs.)

```python
"""Startup reset hands recoverable calculations to the recovery supervisor.

Intent
------
On backend boot the startup sweep flips every ``IN_PROGRESS`` calculation row to
``ABORTED``. In a split web/worker deployment the worker pods survive a backend
restart and keep running the work — so a blind sweep aborts live calculations,
and (because PR #603's terminal guard then refuses to requeue a now-terminal
row) permanently loses calculations a dead-but-tracked worker would have been
resumed for. The fix makes the sweep defer to the recovery registry: any row a
tracked recovery task owns (alive heartbeat *or* expired-but-tracked) is left
``IN_PROGRESS`` for the supervisor to finish or resume; only genuinely untracked
rows are aborted. When recovery is off / Redis is down the registry reports
nothing tracked, so behavior is identical to today.

Cluster 8x — scenarios 8.103–8.114. Type: U (helper logic, mocked registry) + I
(real ``CalculationModel`` rows driven through the startup sweep).
Covers: lex/lex_app/celery_recovery/supervisor.py,
        lex/process_admin/utils/model_registration.py.
Run: python -m lex pytest lex/test_project/tests/celery_async/test_8x_startup_reset_recovery_handoff.py -v
"""

from __future__ import annotations

import os
from unittest import mock

import pytest
from django.test import SimpleTestCase

from lex.core.models.CalculationModel import CalculationModel
from lex.lex_app.celery_recovery import supervisor
from lex.process_admin.utils.model_registration import ModelRegistration
from lex.test_project.tests._e2e_test_case import E2ETestCase

from .models import CelerySyncCalc

pytestmark = pytest.mark.celery_async


class TestCluster08x_TrackedRecordIds(SimpleTestCase):
    """Cluster 8x: the registry → owned-record-ids lookup the sweep relies on."""

    def _instance(self, pk):
        """An unsaved CalculationModel instance carrying a pk (no DB needed)."""
        inst = CelerySyncCalc(name=f"calc-{pk}")
        inst.pk = pk
        return inst

    def _patch_registry(self, *, tracked, payloads):
        """Patch list_tracked + get_payload for the given task→payload map."""
        return [
            mock.patch.object(supervisor.registry, "list_tracked", return_value=tracked),
            mock.patch.object(
                supervisor.registry, "get_payload",
                side_effect=lambda tid: payloads.get(tid),
            ),
        ]

    def test_08_103_alive_tracked_task_contributes_its_rows(self):
        """
        Scenario 8.103: a tracked task's calc rows are reported as owned.
        Given: one tracked task whose payload args carry a calc row (pk=7).
        When:  tracked_calculation_record_ids() is computed.
        Then:  the (label_lower, 7) pair is in the returned set — the sweep will
               skip that row instead of aborting it.
        """
        inst = self._instance(7)
        payloads = {"t1": {"args": ([inst],), "name": "calc_and_save"}}
        p1, p2 = self._patch_registry(tracked=["t1"], payloads=payloads)
        with p1, p2:
            result = supervisor.tracked_calculation_record_ids()
        self.assertIn((CelerySyncCalc._meta.label_lower, 7), result)

    def test_08_104_expired_but_tracked_task_still_contributes(self):
        """
        Scenario 8.104: ownership does NOT depend on a live heartbeat.
        Given: a tracked task (its worker died — is_alive would be False) whose
               payload still carries a calc row.
        When:  tracked_calculation_record_ids() is computed.
        Then:  the row is still reported owned — the supervisor will requeue and
               resume it, so the startup sweep must not abort it. (No is_alive
               call is made; ownership = tracked-at-all.)
        """
        inst = self._instance(9)
        payloads = {"dead": {"args": ([inst],), "name": "calc_and_save"}}
        p1, p2 = self._patch_registry(tracked=["dead"], payloads=payloads)
        with p1, p2, mock.patch.object(supervisor.registry, "is_alive") as alive:
            result = supervisor.tracked_calculation_record_ids()
        self.assertIn((CelerySyncCalc._meta.label_lower, 9), result)
        alive.assert_not_called()

    def test_08_105_empty_registry_returns_empty_set(self):
        """
        Scenario 8.105: recovery off / Redis down → nothing owned → back-compat.
        Given: registry.list_tracked() returns [] (disabled or unreadable).
        When:  tracked_calculation_record_ids() is computed.
        Then:  it returns an empty set, so the sweep aborts every stuck row
               exactly as it does today.
        """
        p1, p2 = self._patch_registry(tracked=[], payloads={})
        with p1, p2:
            result = supervisor.tracked_calculation_record_ids()
        self.assertEqual(result, set())

    def test_08_106_payload_without_calc_instances_contributes_nothing(self):
        """
        Scenario 8.106: a tracked non-calc task owns no rows.
        Given: a tracked task whose payload args carry no CalculationModel.
        When:  tracked_calculation_record_ids() is computed.
        Then:  it contributes nothing — unrelated stuck rows still abort.
        """
        payloads = {"t1": {"args": (["not-a-model"],), "name": "load_data"}}
        p1, p2 = self._patch_registry(tracked=["t1"], payloads=payloads)
        with p1, p2:
            result = supervisor.tracked_calculation_record_ids()
        self.assertEqual(result, set())

    def test_08_107_multiple_tasks_are_unioned(self):
        """
        Scenario 8.107: every tracked task's rows are collected.
        Given: two tracked tasks, each owning a distinct calc row.
        When:  tracked_calculation_record_ids() is computed.
        Then:  both (label, pk) pairs are present — ownership is the union.
        """
        a, b = self._instance(1), self._instance(2)
        payloads = {
            "t1": {"args": ([a],), "name": "calc_and_save"},
            "t2": {"args": ([b],), "name": "calc_and_save"},
        }
        p1, p2 = self._patch_registry(tracked=["t1", "t2"], payloads=payloads)
        with p1, p2:
            result = supervisor.tracked_calculation_record_ids()
        self.assertEqual(
            result,
            {(CelerySyncCalc._meta.label_lower, 1),
             (CelerySyncCalc._meta.label_lower, 2)},
        )

    def test_08_108_instance_without_pk_is_excluded(self):
        """
        Scenario 8.108: a row with no pk can never match a stuck DB row.
        Given: a tracked task whose calc instance has pk=None.
        When:  tracked_calculation_record_ids() is computed.
        Then:  it is excluded — only persisted rows (with a pk) are protectable.
        """
        inst = CelerySyncCalc(name="no-pk")  # pk stays None
        payloads = {"t1": {"args": ([inst],), "name": "calc_and_save"}}
        p1, p2 = self._patch_registry(tracked=["t1"], payloads=payloads)
        with p1, p2:
            result = supervisor.tracked_calculation_record_ids()
        self.assertEqual(result, set())
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m lex pytest lex/test_project/tests/celery_async/test_8x_startup_reset_recovery_handoff.py -v`
Expected: FAIL — `AttributeError: module 'lex...supervisor' has no attribute 'tracked_calculation_record_ids'`.

- [ ] **Step 3: Implement the helper**

In `lex/lex_app/celery_recovery/supervisor.py`, immediately after `_extract_calculation_models` (which ends at line 121) add:

```python
def tracked_calculation_record_ids() -> set:
    """``(_meta.label_lower, pk)`` for every calculation row a tracked recovery
    task currently owns — whether its worker is alive or merely tracked.

    The startup reset sweep (``process_admin.utils.model_registration``) consults
    this to decide which stuck ``IN_PROGRESS`` rows it must NOT abort: a row owned
    by a tracked task will either be finished by its still-running worker or
    requeued/resumed by :func:`scan_and_recover`. Aborting it at startup would,
    via the terminal-outcome guard, block that resume and lose recoverable state.

    Ownership deliberately does not depend on a live heartbeat — an expired-but-
    tracked task (its worker died) is exactly the case the supervisor resumes.
    Best-effort: when recovery is disabled or Redis is unreadable,
    ``registry.list_tracked()`` returns ``[]`` so the set is empty and the sweep
    keeps its original blind-abort behavior.
    """
    owned = set()
    for task_id in registry.list_tracked():
        payload = registry.get_payload(task_id) or {}
        for instance in _extract_calculation_models(payload.get("args")):
            if instance.pk is not None:
                owned.add((type(instance)._meta.label_lower, instance.pk))
    return owned
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m lex pytest lex/test_project/tests/celery_async/test_8x_startup_reset_recovery_handoff.py -v`
Expected: PASS — 6 passed (8.103–8.108).

- [ ] **Step 5: Commit**

```bash
git add lex/lex_app/celery_recovery/supervisor.py \
        lex/test_project/tests/celery_async/test_8x_startup_reset_recovery_handoff.py
git commit -m "Add tracked_calculation_record_ids recovery-ownership lookup (8x: 8.103-8.108)"
```

---

## Task 2: startup sweep defers to ownership + its E2E tests (8.109–8.114)

**Files:**
- Modify: `lex/process_admin/utils/model_registration.py:384-430` (`_handle_calculation_model_reset`) and the caller loop at `:96-97`.
- Modify: `lex/test_project/tests/celery_async/test_8x_startup_reset_recovery_handoff.py` (append Component B classes)

- [ ] **Step 1: Write the failing E2E tests (Component B)**

Append to the test file:

```python
class TestCluster08x_StartupSweepDefersToOwnership(E2ETestCase):
    """Cluster 8x: the startup sweep skips rows the recovery registry owns.

    Real ``CalculationModel`` rows are driven straight through
    ``_handle_calculation_model_reset`` with the recovery-ownership set injected,
    so the abort/skip decision is the genuine one. The default E2E patch already
    mocks ``ensure_terminal_calculation_audit``; we read that mock from
    ``self._patch_map`` to prove whether an audit row would have been written.
    """

    e2e_models = [CelerySyncCalc]

    def _make_in_progress(self, name):
        row = CelerySyncCalc.objects.create(name=name)
        row.is_calculated = CalculationModel.IN_PROGRESS
        row.save(skip_hooks=True)
        return row

    def _owned(self, *rows):
        return {(CelerySyncCalc._meta.label_lower, r.pk) for r in rows}

    def _run_sweep(self, tracked_record_ids):
        with mock.patch.dict(os.environ, {"CALLED_FROM_START_COMMAND": "1"}):
            ModelRegistration._handle_calculation_model_reset(
                CelerySyncCalc, tracked_record_ids=tracked_record_ids,
            )

    def test_08_109_owned_row_stays_in_progress_and_is_not_audited(self):
        """
        Scenario 8.109: a row a tracked task owns is left for recovery.
        Given: an IN_PROGRESS row whose (label, pk) is in the owned set.
        When:  the startup sweep runs.
        Then:  the row stays IN_PROGRESS and no aborted-audit is written — the
               worker (alive) or the supervisor (resume) will conclude it.
        """
        row = self._make_in_progress("live")
        audit = self._patch_map["ensure_terminal_calculation_audit"]
        self._run_sweep(self._owned(row))
        row.refresh_from_db()
        self.assertEqual(row.is_calculated, CalculationModel.IN_PROGRESS)
        audit.assert_not_called()

    def test_08_110_untracked_row_is_aborted_and_audited(self):
        """
        Scenario 8.110: an unowned row is the only thing the sweep aborts.
        Given: an IN_PROGRESS row that no tracked task owns (empty owned set).
        When:  the startup sweep runs.
        Then:  the row flips to ABORTED and an aborted-audit is written — today's
               behavior, preserved for genuinely unrecoverable rows.
        """
        row = self._make_in_progress("orphan")
        audit = self._patch_map["ensure_terminal_calculation_audit"]
        self._run_sweep(set())
        row.refresh_from_db()
        self.assertEqual(row.is_calculated, CalculationModel.ABORTED)
        audit.assert_called_once()

    def test_08_111_mixed_rows_only_unowned_is_aborted(self):
        """
        Scenario 8.111: ownership is per-row, not all-or-nothing.
        Given: two IN_PROGRESS rows — one owned by a tracked task, one not.
        When:  the startup sweep runs.
        Then:  the owned row stays IN_PROGRESS, the unowned row goes ABORTED.
        """
        owned_row = self._make_in_progress("keep")
        orphan_row = self._make_in_progress("drop")
        self._run_sweep(self._owned(owned_row))
        owned_row.refresh_from_db()
        orphan_row.refresh_from_db()
        self.assertEqual(owned_row.is_calculated, CalculationModel.IN_PROGRESS)
        self.assertEqual(orphan_row.is_calculated, CalculationModel.ABORTED)

    def test_08_112_empty_ownership_aborts_all_rows_backcompat(self):
        """
        Scenario 8.112: recovery off → identical to the original blind sweep.
        Given: two IN_PROGRESS rows and an empty owned set (recovery disabled /
               Redis down — tracked_calculation_record_ids() returns set()).
        When:  the startup sweep runs.
        Then:  both rows are aborted — no regression when recovery is unavailable.
        """
        r1 = self._make_in_progress("a")
        r2 = self._make_in_progress("b")
        self._run_sweep(set())
        r1.refresh_from_db()
        r2.refresh_from_db()
        self.assertEqual(r1.is_calculated, CalculationModel.ABORTED)
        self.assertEqual(r2.is_calculated, CalculationModel.ABORTED)

    def test_08_113_gate_off_is_a_noop(self):
        """
        Scenario 8.113: without CALLED_FROM_START_COMMAND the sweep does nothing.
        Given: an IN_PROGRESS row and the start-command gate unset.
        When:  _handle_calculation_model_reset is invoked.
        Then:  the row is untouched — the gate still fully short-circuits the
               sweep, so the new ownership logic never runs outside startup.
        """
        row = self._make_in_progress("gated")
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("CALLED_FROM_START_COMMAND", None)
            ModelRegistration._handle_calculation_model_reset(
                CelerySyncCalc, tracked_record_ids=set(),
            )
        row.refresh_from_db()
        self.assertEqual(row.is_calculated, CalculationModel.IN_PROGRESS)

    def test_08_114_precomputed_set_skips_the_registry_read(self):
        """
        Scenario 8.114: passing the set in avoids a per-model registry hit.
        Given: a precomputed tracked_record_ids is supplied to the sweep.
        When:  the sweep runs.
        Then:  it uses the given set and does NOT call
               tracked_calculation_record_ids() again — the caller computes once
               and threads it through the per-model loop.
        """
        row = self._make_in_progress("precomputed")
        with mock.patch.object(
            supervisor, "tracked_calculation_record_ids",
        ) as compute:
            self._run_sweep(self._owned(row))
        compute.assert_not_called()
        row.refresh_from_db()
        self.assertEqual(row.is_calculated, CalculationModel.IN_PROGRESS)
```

- [ ] **Step 2: Run the new tests to verify they fail**

Run: `python -m lex pytest "lex/test_project/tests/celery_async/test_8x_startup_reset_recovery_handoff.py::TestCluster08x_StartupSweepDefersToOwnership" -v`
Expected: FAIL — `_handle_calculation_model_reset()` does not accept `tracked_record_ids` (TypeError: unexpected keyword argument), and owned rows are still aborted.

Note (local env): the 6 real-row scenarios run on `E2ETestCase` (TransactionTestCase). In the borrowed local venv the *assertions* pass but the shared teardown-flush errors identically to the existing 8v/8w E2E tests (a local Postgres-provisioning gap, not a code defect). They pass clean on CI Postgres. Judge correctness by the assertion outcome, not the teardown error.

- [ ] **Step 3: Implement the filter in `_handle_calculation_model_reset`**

In `lex/process_admin/utils/model_registration.py`, replace the method signature and body at `:384` so it accepts the optional set, computes it when absent, and skips owned rows. The `asyncio`/`nest_asyncio` wrapper and audit call are left exactly as-is.

```python
    @classmethod
    def _handle_calculation_model_reset(
        cls,
        model: Type[models.Model],
        tracked_record_ids=None,
    ) -> None:
        """
        Reset CalculationModel instances left in IN_PROGRESS state on startup.

        Rows owned by a tracked recovery task (alive worker, or expired-but-
        tracked → resumed by the supervisor) are left IN_PROGRESS so recovery
        can finish them; only genuinely untracked rows are flipped to ABORTED.
        ``tracked_record_ids`` is the ``{(label_lower, pk)}`` ownership set; when
        omitted it is computed once here (the caller passes it in to avoid a
        per-model registry read).

        Uses per-instance ``.save(skip_hooks=True)`` so that
        django-simple-history records an ABORTED history row for each
        affected record, and creates the corresponding AuditLog /
        AuditLogStatus entries for a complete audit trail.
        """
        from lex.core.models.CalculationModel import CalculationModel

        if not os.getenv("CALLED_FROM_START_COMMAND"):
            return

        if tracked_record_ids is None:
            from lex.lex_app.celery_recovery.supervisor import (
                tracked_calculation_record_ids,
            )
            tracked_record_ids = tracked_calculation_record_ids()

        model_label = model._meta.label_lower

        @sync_to_async
        def reset_instances_with_aborted_calculations():
            from lex.audit_logging.utils.calculation_audit import (
                ensure_terminal_calculation_audit,
            )

            stuck = list(
                model.objects.filter(is_calculated=CalculationModel.IN_PROGRESS)
            )
            for instance in stuck:
                if (model_label, instance.pk) in tracked_record_ids:
                    # Owned by the recovery machinery: a live worker will finish
                    # it, or the supervisor will requeue/resume it. Aborting here
                    # would, via the terminal-outcome guard, block that resume
                    # and permanently lose recoverable calculation state.
                    continue
                instance.is_calculated = CalculationModel.ABORTED
                instance._history_change_reason = (
                    "Startup reset: calculation was still IN_PROGRESS"
                )
                instance.save(skip_hooks=True)

                try:
                    ensure_terminal_calculation_audit(
                        instance,
                        audit_status="aborted",
                        error_message="Calculation aborted during startup reset",
                    )
                except Exception:
                    logger.warning(
                        "Failed to create audit log for startup-aborted %s (pk=%s)",
                        model.__name__,
                        instance.pk,
                        exc_info=True,
                    )

        nest_asyncio.apply()
        loop = asyncio.get_event_loop()
        loop.run_until_complete(reset_instances_with_aborted_calculations())
```

- [ ] **Step 4: Run the new tests to verify they pass**

Run: `python -m lex pytest "lex/test_project/tests/celery_async/test_8x_startup_reset_recovery_handoff.py::TestCluster08x_StartupSweepDefersToOwnership" -v`
Expected: PASS on every assertion (modulo the documented local teardown-flush error on the TransactionTestCase scenarios).

- [ ] **Step 5: Thread the ownership set through the caller (compute once)**

In `lex/process_admin/utils/model_registration.py`, in `register_models`, compute the set lazily on the first CalculationModel and reuse it for the rest of the loop. Initialize the local before the `for model in models:` loop (around line 63):

```python
        history_ok = []
        history_skipped = []
        history_failed = []
        tracked_record_ids = None  # computed once on the first CalculationModel
```

And replace the existing call at `:96-97`:

```python
                    if issubclass(model, CalculationModel):
                        if tracked_record_ids is None:
                            from lex.lex_app.celery_recovery.supervisor import (
                                tracked_calculation_record_ids,
                            )
                            tracked_record_ids = tracked_calculation_record_ids()
                        cls._handle_calculation_model_reset(
                            model, tracked_record_ids=tracked_record_ids,
                        )
```

- [ ] **Step 6: Re-run the whole 8x file + a smoke check of the existing startup-reset tests**

Run: `python -m lex pytest lex/test_project/tests/celery_async/test_8x_startup_reset_recovery_handoff.py -v`
Expected: 6 U scenarios PASS clean; 6 I scenarios pass their assertions (teardown caveat as above).

Run the pre-existing direct-caller tests that exercise `_handle_calculation_model_reset` to confirm the optional param did not break them:
Run: `python -m lex pytest lex/core/tests/test_calculation_history_transitions.py -v`
Expected: no new failures introduced by this change (some are pre-skipped — unchanged).

- [ ] **Step 7: Commit**

```bash
git add lex/process_admin/utils/model_registration.py \
        lex/test_project/tests/celery_async/test_8x_startup_reset_recovery_handoff.py
git commit -m "Startup reset defers IN_PROGRESS rows owned by recovery (8x: 8.109-8.114)"
```

---

## Task 3: Plan-file sync (lex-testing Step 7)

**Files (all pure additions — never renumber existing entries):**
- Modify: `lex/test_project/test-plan/test-clusters.md` — add a "Sub-cluster 8x" section after the 8w block, with the 8.103–8.114 scenario table.
- Modify: `lex/test_project/test-plan/progress/dashboard.md` — add an 8x row.
- Modify: `lex/test_project/test-plan/test-writing-plan.md` — add a "Batch 8x" block.
- Modify: `lex/test_project/test-plan/progress/session-log.md` — add a new session row describing the 8x work.

- [ ] **Step 1:** Use the `lex-testing` skill's Step 7 guidance to write each entry, mirroring the exact format of the existing 8w entries. Keep all four consistent (same scenario count: 12; same file path; same covered-source list).

- [ ] **Step 2: Commit**

```bash
git add lex/test_project/test-plan/test-clusters.md \
        lex/test_project/test-plan/progress/dashboard.md \
        lex/test_project/test-plan/test-writing-plan.md \
        lex/test_project/test-plan/progress/session-log.md
git commit -m "Plan sync: cluster 8x startup-reset recovery handoff (8.103-8.114)"
```

---

## Self-Review

**Spec coverage:**
- Component 1 `tracked_calculation_record_ids()` → Task 1. ✓
- Component 2 sweep consults it (optional param + filter) → Task 2 Steps 3. ✓
- Caller computes once → Task 2 Step 5. ✓
- "Old-logic observation: leave the asyncio wrapper" → honored (wrapper untouched in Step 3). ✓
- Degrades safely (empty set when recovery off) → 8.105 (helper) + 8.112 (sweep). ✓
- Edge cases 1–10 in spec → mapped: live/expired-tracked (8.103/8.104), untracked-abort (8.110), recovery-off (8.105/8.112), no-calc-payload (8.106), mixed (8.111), different-row-not-protected (implicit in 8.110/8.111 ownership-by-pk), gate-off (8.113), pk-None (8.108), many-subclasses/compute-once (8.114). ✓
- Test plan layers A (SimpleTestCase) + B (E2E) → Tasks 1 & 2. ✓

**Placeholder scan:** none — every step has concrete code or an exact command.

**Type consistency:** `tracked_calculation_record_ids()` (no args, returns `set` of `(label_lower, pk)`) is defined identically in Task 1 Step 3 and consumed in Task 2 Steps 3 & 5 and tests 8.103–8.114. `_handle_calculation_model_reset(cls, model, tracked_record_ids=None)` signature matches its call sites. ✓
