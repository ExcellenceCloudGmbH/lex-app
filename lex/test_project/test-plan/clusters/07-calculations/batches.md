## Cluster 7 — Calculation State Machine (existing 7a–7j, plus new 7k)

> **Renumbering note (May 12):** the plan's original 7i/7j/7k labels collided with already-shipped sub-clusters (7i = 2-level atomicity matrix Session 42; 7j = 3-level matrix Session 45). The supervisor's "exceptions / restrictions / XLSX" batch landed under **7k**; the queue + signals batches shift to **7l / 7m**.

### Batch 7k — Exceptions, restrictions & XLSX field ✅

| Property | Value |
| --- | --- |
| Scenario range | 7.122 – 7.142 |
| Type | U |
| Files covered | `core/exceptions.py`, `core/mixins/ModelModificationRestriction.py`, `core/fields/XLSX_field.py` (coverage spotter — exhaustive battery already at `lex/tests/unit/api/test_xlsx_field.py`) |
| Test file | `lex/test_project/tests/calculations/test_7k_exceptions_restrictions_xlsx.py` |
| Test classes | `TestCluster07k_HelperPrimitives`, `TestCluster07k_PreferredSelectors`, `TestCluster07k_ResolveHelpers`, `TestCluster07k_ExceptionClasses`, `TestCluster07k_ModelModificationRestriction`, `TestCluster07k_XLSXFieldCoverageSpotter` |
| Fixtures | none (synthetic exception chains built inline) |
| Tests landed | **21 pass / 0 fail in 0.001s** |
| Coverage gain | +1.0 % (estimated) |
| Status |  Complete (Session 55 — May 12). XLSX_field full coverage delegated to the existing 378-line `lex/tests/unit/api/test_xlsx_field.py`; 7.142 spotter pins the format-constant tuples + `max_length` migration sensitivity at the cluster-7 dashboard level. |

### ~~Batch 7l — Recalculation queue & dispatch~~ *(rolled back upstream — spec preserved for future re-activation)*

> **Status (May 12):** the recalc-queue surface (`ObjectsToRecalculateStore` + `update_handler`) was rolled back upstream after a first-pass implementation surfaced a `CalculatedModelMixinMeta.__new__` `KeyError: 'defining_fields'` issue with stub subclasses (workaround used `_FakeCalcMixinBase` + `unittest.mock.patch`). The on-disk test file was reverted; the spec below is kept commented out so the slot/letter is reserved and we can re-activate once the upstream queue ships in a stable form.

<!--
| Property | Value |
| --- | --- |
| Scenario range | 7.143 – 7.154 |
| Type | I |
| Files covered | `core/calculated_updates/ObjectsToRecalculateStore.py`, `core/calculated_updates/update_handler.py` |
| Test file | `lex/test_project/tests/calculations/test_7l_recalc_queue.py` |
| Test classes | `TestObjectsToRecalculateStore` (push/pop/dedupe), `TestUpdateHandlerDispatch` (FK chains, depth limits, cycle protection) |
| Fixtures | `ParentCalc → ChildCalc → GrandchildCalc` (already in `calculations/models.py`) |
| Est. tests | ~12 |
| Coverage gain | +0.8 % |
| Prereqs | none |
| Implementation notes (rolled back) | `CalculatedModelMixinMeta.__new__` reads `attrs['defining_fields']` unconditionally (line ~976 of `lex/core/mixins/CalculatedModelMixin.py`) — any stub subclass without that attribute raises `KeyError` at class-construction time. Workaround during the first pass was a plain `_FakeCalcMixinBase` stand-in plus `unittest.mock.patch("lex.core.calculated_updates.update_handler.CalculatedModelMixin", _FakeCalcMixinBase)`. Re-use this pattern when the batch is re-activated. Also: dependency dict-keys must be hashable — `SimpleNamespace` is not, use a small plain class. |
-->


### Batch 7m — Calculation signals & active-state store

| Property | Value |
| --- | --- |
| Scenario range | 7.155 – 7.165 |
| Type | I |
| Files covered | `core/signals/CalculationSignals.py`, `core/signals/ActiveCalculationStateStore.py` |
| Test file | `lex/test_project/tests/calculations/test_7m_calc_signals.py` |
| Test classes | `TestCalculationSignalsPrePost` (signal fires, payload shape), `TestActiveCalculationStateStore` (registration, cleanup on success + on failure) |
| Fixtures | `AtomicCalc`, `FailingCalc` (existing) |
| Est. tests | ~10 |
| Coverage gain | +0.6 % |
| Prereqs | none |

> `LexModel.py`, `CalculationModel.py`, `CalculatedModelMixin.py` keep their forecasted homes (4i existing + 7h Tier-A clusters in the coverage plan). Do not duplicate.

### Batch 7n — Calculation cancellation, state machine + recursive cancel (Session 67 — June 1)

| Property | Value |
| --- | --- |
| Scenario range | 7.166 – 7.173 |
| Type | I |
| Files covered | `lex/core/models/CalculationModel.py` (new `CalculationModel.cancel()` classmethod + `_persist_cancelled` / `_persist_cancelled_by_entry` helpers + `CalculationCancelled` marker exception + `dispatch_calculation_task` task_id capture); `lex/core/signals/ActiveCalculationStateStore.py` (new `set_task_id` / `get_task_id` / `find_descendants`; `mark_in_progress` preserves task_id across re-entry) |
| Test file | `lex/test_project/tests/calculations/test_7n_cancellation.py` |
| Test classes | `TestCluster07n_Cancellation` (Celery-cancellable → CANCELLED; sync-not-cancellable → reason flag; terminal-state idempotent; recursive cancel reaches every descendant sharing `calculation_id`; `recursive=False` leaves descendants); `TestCluster07n_StateStoreTaskIdAndDescendants` (`set_task_id` survives `mark_in_progress` re-entry; `find_descendants` groups by shared `calculation_id`); `TestCluster07n_CalculationCancelledException` (marker exception carries reason) |
| Fixtures | `AtomicCalc`, `ParentCalc`, `ChildCalc` (existing); `CalculationModel._revoke_celery_task` patched so no Celery broker is needed |
| Est. tests | 8 |
| Coverage gain | +0.5 % |
| Prereqs | none |
| Status | ✅ Complete (Session 67 — 8 scenarios; 3 pure-logic pass locally, 5 DB-needing scenarios require CI test DB) |

### Batch 7o — ForeignKey integrity violation aborts batch (Session 72 — June 2)

| Property | Value |
| --- | --- |
| Scenario range | 7.176 – 7.176 |
| Type | I |
| Files covered | `lex/core/mixins/CalculatedModelMixin.py`, `lex/lex_app/celery_tasks.py` |
| Test file | `lex/test_project/tests/calculations/test_7o_fk_violation_abort.py` |
| Test classes | `TestCluster07o_ForeignKeyAbort` |
| Fixtures | `FKViolationAbortCalc`, `FKAbortWrite`, `FKAbortTarget` (`calculations/models.py`) |
| Tests landed | **not run locally (requires Postgres test DB in this environment)** |
| Coverage gain | n/a (behaviour regression gate) |
| Prereqs | none |
| Status | ✅ Complete (Session 72 — pins immediate abort on unhandled FK integrity failure) |


---

### Batch 7p — Sync-mode streaming combinatorial expansion (OOM fix) ✅

| Property | Value |
| --- | --- |
| Scenario range | 7.178 – 7.187 |
| Type | U (generator + flag, pure logic) + I (E2E sync create saved rows) |
| Files covered | `lex/core/mixins/CalculatedModelMixin.py` (`generate_model_combinations_streaming`, `calc_and_save_streaming`, `_sync_streaming_enabled`, `create()` mode branch) |
| Test file | `lex/test_project/tests/calculations/test_7p_streaming_expansion.py` |
| Test classes | `TestCluster07p_StreamingEquivalence` (7.178–7.182), `TestCluster07p_StreamingFlag` (7.183–7.185), `TestCluster07p_SyncCreateEquivalence` (7.186), `TestCluster07p_StreamingMemoryBound` (7.187) |
| Fixtures | `_FakeCalcModel` (pure logic); existing `CombinatorialCalc` E2E model (reused, no new model) |
| Tests landed | 10 pass / 0 fail |
| Coverage gain | streaming generator + consumer + flag + create() sync branch |
| Status | ✅ Complete (Session 79 — June 10) |
| Note | Backend OOM fix: sync-mode expansion now streams (O(depth)) instead of materializing all N. `LEX_SYNC_STREAMING_EXPANSION=false` valve. Benchmark in `docs/runs/`. |


---

### Batch 7m — `CalculationSignals` + `One.py` `model_name` propagation (Session 80 — June 18)

| Property | Value |
| --- | --- |
| Scenario range | 7.188 – 7.195 |
| Type | U + E |
| Files covered | `lex/core/signals/CalculationSignals.py` (`update_calculation_status` IN_PROGRESS branch), `lex/api/views/model_entries/One.py` (early-registration `mark_in_progress` call) |
| Test file | `lex/test_project/tests/calculations/test_7m_calc_signals.py` |
| Test classes | `TestCluster07m_SignalModelName` (7.188–7.191 — IN_PROGRESS passes object_name, SUCCESS/ERROR do not call mark_in_progress, non-calculation model returns early), `TestCluster07m_OneModelNamePropagation` (7.192–7.193 — calculate=true passes model_name to store, record_id matches) |
| Fixtures | `_FakeInstance` / `_FakeSignal` (pure unit); `AtomicCalc` (reused from cluster 7, via E2ETestCase) |
| Tests landed | 8 pass / 0 fail |
| Coverage gain | `CalculationSignals.py` IN_PROGRESS `model_name` kwarg path; `One.py` early-registration `mark_in_progress(model_name=…)` branch |
| Status | ✅ Complete (Session 80 — June 18) |

---

### Batch 7q — Nested fan-out dispatches by default from inside a worker (Session 86 — June 29)

| Property | Value |
| --- | --- |
| Scenario range | 7.196 – 7.201 |
| Type | E |
| Files covered | `lex/core/mixins/CalculatedModelMixin.py` (`_dispatch_model_processing` — removed the `is_celery_worker_process()` inline-inside-worker guard; now fans out to `CeleryTaskDispatcher` whenever `CELERY_ACTIVE` + `cls.calculate.delay` exist) and `lex/core/models/CalculationModel.py` (`calculate_hook` dispatch branch — removed the `elif is_celery_worker_process(): execute_calculation_sync()` branch; now always dispatches, reusing an outer async context or opening its own `WaitForTasks` + blocking on the child result). Driven by a production bug: `InvestmentPosting` (65 568 models, 50 clusters) ran synchronously on one worker slot because the guard made nested fan-out opt-in |
| Test file | `lex/test_project/tests/calculations/test_7q_worker_default_dispatch.py` |
| Test classes | `TestCluster07q_MixinWorkerDefaultDispatch` (7.196 no-context / 7.197 WaitForTasks / 7.198 FireAndForget — all fan out to the dispatcher, never `calc_and_save_sync`); `TestCluster07q_CalculationModelWorkerDefaultDispatch` (7.199 no-context ⇒ own WaitForTasks dispatches + blocks; 7.200 outer WaitForTasks ⇒ child drained only on scope exit; 7.201 FireAndForget ⇒ dispatches, never blocks) |
| Fixtures | `CombinatorialCalc` / `AtomicCalc` (reused from cluster 7, via E2ETestCase); explicit async contexts entered inside the `_celery_is_active=True` patch via a `_worker_patches` helper-CM so they register on the contextvar stack |
| Tests landed | 6 pass / 0 fail. Companion stale-test updates flipped to the new default: `test_calculation_wait_contexts.py` (2 pass), `test_calculated_model_mixin.py` (dispatch tests pass; `test_create_treats_empty_selections_as_valid_noop` is a pre-existing streaming-path failure unrelated to this change — confirmed via git stash), `test_8b_dispatch_context.py` 8.6 message (2 pass) |
| Coverage gain | the default-dispatch branch of both `_dispatch_model_processing` and `calculate_hook` (previously only the explicit-context and inline-worker branches were exercised) |
| Status | ✅ Complete — source fix (2 files) + paired tests + stale-test fixes + plan sync in one change. Allocated `7q` (next free letter after `7p`); 7.196 picks up after cluster-7 scenario max 7.195. **Session 91 note:** Session 89 briefly flipped 7.199 to inline (draft Batch 7r); withdrawn before commit — always-dispatch is the pinned default on both paths, abort-safety via the cancel marker (Batch 8ad) |

---

### Batch 7r — (withdrawn — Session 91) Per-instance inline-inside-worker guard

| Property | Value |
| --- | --- |
| Scenario range | 7.202 – 7.204 (never landed — reusable by the next cluster-7 batch) |
| Status | ❌ **Withdrawn before commit** (Session 91 — July 2). The Session 89 draft restored an `is_celery_worker_process()` inline guard on the per-instance `CalculationModel.calculate_hook` path as the Report 1 (abort→resume) fix. Live verification showed it broke the core parallelism contract: a nested calc inside a worker (e.g. a project's `CalculateNAV` inside another calculation) ran INLINE on the parent's worker instead of dispatching to a free one, and only an explicit `WaitForTasks` restored dispatch. Per the developer's explicit requirement ("calculation can dispatch calculations"), the guard, the 7q 7.199 flip, and `test_7r_nested_worker_inline_abort_safe.py` were all withdrawn; always-dispatch (7q) is the pinned default on both paths. Report 1 abort-safety is provided by the restart-surviving cluster cancel marker instead — see **Batch 8ad**. Letter 7r stays reserved for this record |

---

### Batch 7s — Calculations must not move `edited_at` / `edited_by` (Celery-OFF + startup) ✅

| Property | Value |
| --- | --- |
| Scenario range | 7.205 – 7.221 |
| Type | I/E |
| Files covered | `lex/core/models/LexModel.py` (`_should_skip_edited_fields_update`, `update_edited_at`, `update_edited_by`), `lex/core/models/CalculationModel.py` (`execute_calculation_sync`), `lex/process_admin/utils/model_registration.py` (`_handle_calculation_model_reset`) |
| Test file | `lex/test_project/tests/calculations/test_7s_calculation_audit_columns.py` |
| Test classes | `TestCluster07s_CalculationAuditColumns` — 7.205/7.206 sync SUCCESS leaves `edited_at`/`edited_by`; 7.207 ERROR; 7.208 CANCELLED; 7.209 child rows written as calculation *output*; 7.210 `created_at` immutable; 7.211 IN_PROGRESS-at-restart → ABORTED (the startup sweep); 7.212 recovery-tracked row skipped; **7.213–7.215 negative controls** (a real user edit stamps; a user edit *after* a calculation stamps — suppression must not outlive its window; an explicit `edited_at` override is honoured); 7.216 repeated recalculation never accumulates stamps; **7.217** Celery-OFF, row already IN_PROGRESS in the DB, server start → ABORTED must not reattribute `edited_by` (distinct sentinel); **7.218–7.221 the real HTTP `calculate=true` endpoint** — 7.218 success keeps both, **7.219 the reported case** (interrupted by a server restart → ABORTED, no stale stamp), 7.220 the stamp is absent *before* any completion could revert it (proves the guard, not the revert), 7.221 negative control (a genuine HTTP field edit still stamps) |
| Fixtures | `AuditColumnsChild`, `AuditColumnsCalc`, `AuditColumnsFailingCalc`, `AuditColumnsCancelCalc`, `AuditColumnsParentCalc` — added to `calculations/models.py`. Bodies mirror real project code (write a field, then `save()`). |
| Tests landed | **17 pass / 0 fail** |
| Coverage gain | the audit-column contract on every Celery-OFF calculation path — previously **zero** coverage anywhere in the suite |
| Status | ✅ Complete — all 12 pass: these paths were already correct (the sync terminal saves and the startup sweep all use `skip_hooks=True`), **except** the HTTP `calculate=true` trigger save, which stamped `edited_at`/`edited_by` and only appeared clean because a completing run reverted it (7.219/7.220 pinned the reported interrupted-then-ABORTED case). Fixed under BUG-028 by suppressing the trigger save in the guard via `_defer_calculate_hook`. The negative controls are the load-bearing half: they ensure the BUG-028 fix cannot over-suppress and stop stamping genuine user edits. |
| Note | Scenario 7.214 was originally specified as "a field edit sent alongside `calculate=true` still stamps". That turned out to rest on a false premise — a `calculate=true` request **silently discards** accompanying field changes (verified: the same edit without `calculate` applies normally), so `edited_at` correctly does not move. Retargeted to the stronger control above. The discarded-edit behaviour is a separate question, out of scope here. |

---
