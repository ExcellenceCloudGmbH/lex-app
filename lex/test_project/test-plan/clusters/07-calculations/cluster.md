## 7. Calculation State Machine

**What it tests:** The `CalculationModel` state machine — transitions between NOT_CALCULATED, IN_PROGRESS, SUCCESS, ERROR, ABORTED, CANCELLED — across atomic and non-atomic models, and parent→child calculation hierarchies.

**Why seventh:** Calculations are the core value proposition of the framework, but they depend on CRUD, validation, history, and audit all working. We test them after the foundations are solid.

**Models needed:**
- `AtomicCalc` — `CalculationModel` (default `is_atomic=True`), configurable success/failure
- `NonAtomicCalc` — `CalculationModel` with `is_atomic = False`, configurable success/failure
- `ParentCalc` — triggers `ChildCalc` from its `calculate()` method
- `ChildCalc` — triggered by parent, configurable success/failure
- `NonAtomicChildCalc` — child variant with `is_atomic = False`
- `AtomicParentAtomicChildMatrixCalc`, `AtomicParentNonAtomicChildMatrixCalc`, `NonAtomicParentAtomicChildMatrixCalc`, `NonAtomicParentNonAtomicChildMatrixCalc` — exhaustive parent/child atomicity matrix with independent parent/child failure toggles
- `GrandchildCalc` — 3-level hierarchy (parent → child → grandchild)
- `FailingCalc` — always raises an exception in `calculate()`

**Test scenarios:**

| # | Scenario | What We Assert |
|---|----------|----------------|
| 7.1 | Successful atomic calculation | Final state = SUCCESS, history shows [IN_PROGRESS, SUCCESS] |
| 7.2 | Failing atomic calculation | Final state = ERROR, history shows [IN_PROGRESS, ERROR] |
| 7.3 | Successful non-atomic calculation | Final state = SUCCESS, history shows [IN_PROGRESS, SUCCESS] |
| 7.4 | Failing non-atomic calculation | Final state = ERROR, history shows [IN_PROGRESS, ERROR] |
| 7.5 | Parent success, child success | Both = SUCCESS, both histories show [IN_PROGRESS, SUCCESS] |
| 7.6 | Parent success, child fails | Child = ERROR, parent = ERROR (propagation), both have history trail |
| 7.7 | Non-atomic parent, atomic child fails | Both should show IN_PROGRESS → ERROR in history |
| 7.8 | 3-level hierarchy, grandchild fails | Error propagates up, all three show history trail |
| 7.9 | Re-calculate after failure | Second calculation succeeds, history shows full lifecycle |
| 7.10 | `persist_error_state` deduplication | Calling persist_error_state twice doesn't create duplicate ERROR saves |
| 7.11 | Re-entrancy guard | `calculate_hook` skips when `_calculation_hook_in_progress` is set |
| 7.12 | `calculate()` does NOT call `self.save()` | Framework handles saves; model's calculate just does computation |
| 7.13 | Error message stored | When calculation fails, `calculation_error_message` or `error_message` field populated |
| 7.14 | Calculation via REST API (PATCH is_calculated=IN_PROGRESS) | API path commits IN_PROGRESS independently, then hooks run |
| 7.176 | ForeignKey integrity violation inside `CalculatedModelMixin.create()` aborts immediately | Unhandled FK integrity failures are propagated and later models in the batch are not processed |
| 7.177 | Any calculation error inside `CalculatedModelMixin.create()` aborts immediately | Any error during calculate() or save() stops the batch; later models are not processed |
| 7.32 | Atomic parent, atomic child, both pass | Parent and child both settle at SUCCESS |
| 7.33 | Atomic parent, atomic child, child fails | Child settles at ERROR and propagates ERROR to parent |
| 7.34 | Atomic parent, atomic child, parent fails after child pass | Parent settles at ERROR; successful child is rolled back by the parent's atomic transaction |
| 7.35 | Atomic parent, atomic child, both fail | Child ERROR persists and parent settles at ERROR via propagation |
| 7.36 | Atomic parent, non-atomic child, both pass | Parent and non-atomic child both settle at SUCCESS |
| 7.37 | Atomic parent, non-atomic child, child fails | Non-atomic child ERROR persists and propagates ERROR to parent |
| 7.38 | Atomic parent, non-atomic child, parent fails after child pass | Parent settles at ERROR; successful non-atomic child is still rolled back because it ran inside the parent's transaction |
| 7.39 | Atomic parent, non-atomic child, both fail | Non-atomic child ERROR persists and parent settles at ERROR via propagation |
| 7.40 | Non-atomic parent, atomic child, both pass | Parent and child both settle at SUCCESS |
| 7.41 | Non-atomic parent, atomic child, child fails | Atomic child ERROR persists and propagates ERROR to non-atomic parent |
| 7.42 | Non-atomic parent, atomic child, parent fails after child pass | Parent settles at ERROR while successful child remains SUCCESS |
| 7.43 | Non-atomic parent, atomic child, both fail | Child ERROR persists and parent settles at ERROR via propagation |
| 7.44 | Non-atomic parent, non-atomic child, both pass | Parent and child both settle at SUCCESS |
| 7.45 | Non-atomic parent, non-atomic child, child fails | Child ERROR persists and propagates ERROR to parent |
| 7.46 | Non-atomic parent, non-atomic child, parent fails after child pass | Parent settles at ERROR while successful child remains SUCCESS |
| 7.47 | Non-atomic parent, non-atomic child, both fail | Child ERROR persists and parent settles at ERROR via propagation |
| 7.48 – 7.111 | **Sub-cluster 7j — Grandparent / parent / child atomicity matrix (64 scenarios).** Full 3-level extension of 7i: every combination of grandparent / parent / child × atomic / non-atomic × fail / no-fail (8 atomicity triplets × 8 outcome triplets). Pins the two atomicity rules the framework actually obeys: **(a)** a failing level's ERROR row survives nested savepoints (its own atomic block, intermediate atomic ancestors) but is wiped by the **outermost** atomic ancestor's rollback (in 3 levels: only GP wipes a failing P or C); **(b)** a successful descendant's row is wiped by **any** atomic ancestor that raises. Failure precedence stays `c_fail > p_fail > gp_fail`. Tests are parametrically generated as `test_7_NN_<aaa>_<TFT>` where letters encode atomicity (a/n) and outcome (T/F) for gp/p/c. Method names are stable so a CI failure log identifies the cell directly. |

### 7e. Persistence internals + two-phase `save()` ✅

**Gap:** Cluster 7's behavior tests (7a–7d, 7i, 7j) all rely on a single underlying contract: when a `CalculationModel` is saved with `is_calculated=IN_PROGRESS`, the framework writes the IN_PROGRESS row in a short atomic block (Phase 1) and runs `calculate_hook` *outside* that block (Phase 2). If Phase 2 crashes, the IN_PROGRESS row is already committed and the failure is recorded as a clean ERROR — never a forever-spinning IN_PROGRESS. 7e pins both that observable contract and the small set of bookkeeping helpers that hold it up (`_terminal_state_identity`, the terminal-state and IN_PROGRESS persistence markers, the missing-IN_PROGRESS-history recovery queue, and `before_save`'s `is_creation` flag).

**Scenario numbering note:** 7e runs in the **7.112 – 7.121** band (immediately after the 7j matrix ends at 7.111), because the 7.15 – 7.31 range is already claimed by sub-clusters 7f (combination engine, 7.15–7.22) and 7g (`create()` pipeline, 7.25–7.31).

**Models needed:**
- `AtomicCalc` (reused) — drives the helper-level safety-net checks and the SUCCESS branch of two-phase save.
- `FailingCalc` (reused) — drives the Phase-2-failure branch.
- `PersistenceProbeCalc` (new) — same shape as `AtomicCalc` but exposes an instance-level `_probe` callback fired from inside `calculate()`. Lets a test re-query the DB *while* Phase 2 is running and observe what Phase 1 has already committed.

**Test scenarios:**

| # | Scenario | What We Assert |
|---|----------|----------------|
| 7.112 | `_terminal_state_identity` collapses to `(class, pk)` for saved rows and falls back to `id(obj)` for unsaved rows | Two refs to the same persisted row dedupe; two unsaved instances never collide |
| 7.113 | Terminal-state persistence marker (`_mark_` / `_has_` / `_clear_terminal_state_persistence`) | State-specific snapshot; `None` is a safe no-op; clear actually removes the attribute |
| 7.114 | `_apply_in_progress_state_persistence` sets the marker for IN_PROGRESS (and drops any pending history snapshot), clears it for SUCCESS/ERROR, and is a no-op on `None` | Pending-history attribute is removed once IN_PROGRESS is committed |
| 7.115 | `_register_in_progress_state_persistence` runs immediately when `connection.in_atomic_block` is False, but defers to `transaction.on_commit` when True | The deferred callback is captured and only sets the marker after commit fires — so a rollback can never leave a marker claiming "IN_PROGRESS persisted" for a row that never landed on disk |
| 7.116 | `_queue_missing_in_progress_history` skips when the marker is already set; otherwise stashes a snapshot + history metadata for replay; `None` is safe | Pending snapshot is discarded if IN_PROGRESS is known committed; otherwise carries `snapshot`, `history_date`, `history_change_reason` |
| 7.117 | `before_save` sets `is_creation = True` for inserts (`_state.adding`) and `False` for updates | Customer signal handlers can branch reliably on this flag |
| 7.118 | Two-phase save: while `calculate()` is running, a fresh DB query for the same pk already returns `is_calculated = IN_PROGRESS` | If Phase 1 wrapped Phase 2 in the same atomic block, other clients (and the spinner) would never see IN_PROGRESS |
| 7.119 | Two-phase save: after a successful save, the SUCCESS terminal-state marker is recorded on the instance | Protects `persist_error_state` idempotency (scenario 7.10) on retry / Celery callback paths |
| 7.120 | Two-phase save: a Phase-2 raise leaves the row at ERROR (never stuck at IN_PROGRESS) | The customer-visible promise of two-phase save: a crash is a clean ERROR, not a hung spinner |
| 7.121 | A regular save with no IN_PROGRESS flip skips the two-phase path entirely | `calculate()` is NOT called when only a non-state field changes — two-phase is reserved for the IN_PROGRESS transition |

**Status:** ✅ Complete — covers `lex/test_project/tests/calculations/test_7e_persistence_internals.py`.

### 7n. Calculation cancellation — state machine + recursive cancel ✅

**Gap:** The published state machine in `docs/features/processing/calculations.md` advertised `IN_PROGRESS → ABORTED` (the startup-reset terminal state), but the only wiring in the framework was the **startup** sweep in `process_admin/utils/model_registration.py` (any IN_PROGRESS row found at boot is reset to ABORTED). There was no live cancel path — no public method, no REST handler, no Celery revoke — so the user-facing abort button had nothing to call. 7n closes that gap end-to-end on the state-machine side by introducing a **separate** `CANCELLED` terminal state for explicit user cancellation (kept distinct from the recovery-only `ABORTED` so the audit answers "did the operator stop it?" vs "did the framework give up on a crashed worker's row?"): `CalculationModel.cancel(instance, *, recursive=True, reason="")` revokes the Celery task by id (instant kill via `SIGTERM`), persists `CANCELLED` (and the cancellation reason into `calculation_error_message`/`error_message`), and — by default — recurses to every active descendant sharing the parent's `calculation_id`. Sync-dispatched calcs have no `task_id` to revoke; the method returns `cancellable=False, reason="sync_calculation_not_cancellable"` so the API layer can surface a precise 409 instead of silently pretending the cancel worked. Companion store contracts (`ActiveCalculationStateStore.set_task_id` / `get_task_id` / `find_descendants`) and the `CalculationCancelled` marker exception are pinned here too — they're the load-bearing seam between the REST short-circuit (cluster 2i) and the Celery callback (cluster 8u).

**Scenario range:** 7.166 – 7.175. **Test file:** `lex/test_project/tests/calculations/test_7n_cancellation.py`. **Type:** I. **Status:** ✅ Complete (Session 66 — June 1; extended Session 67 with 7.174 / 7.175 covering the in-process exception paths — `execute_calculation_sync` and the outer `calculate_hook` except branches — so a `CalculationCancelled` (or any worker-side `Terminated` / `SoftTimeLimitExceeded` / `WorkerLostError` / `TaskRevokedError` propagated synchronously) lands in `CANCELLED`, not `ERROR`; the hook path additionally skips `persist_error_state` on cancellation so descendants already revoked by the recursive walk are not overwritten with ERROR).

### 7o. Any error aborts calculated batches ✅

**Gap:** `CalculatedModelMixin.create()`'s synchronous pipeline (`calc_and_save_sync`) was tolerating partial failures and continuing to process later models in the batch. Any error during calculation or saving is a non-recoverable condition that must abort the entire batch immediately — whether it's an IntegrityError, a RuntimeError, or any other exception. This ensures data consistency and prevents silent partial processing.

**Scenario range:** 7.176 – 7.177. **Test file:** `lex/test_project/tests/calculations/test_7o_fk_violation_abort.py`. **Type:** I. **Status:** ✅ Complete (Session 72 — June 2).

---

### 7p. Sync-mode streaming combinatorial expansion (OOM fix) ✅

In sync mode (`CELERY_ACTIVE=False`) a calculation runs inside the web/ASGI process. `CalculatedModelMixin.create()`'s four-stage pipeline (generate → prepare → cluster → dispatch) materialized **all N** expanded models and held them alive for the whole run, so peak memory was O(N) and a large fan-out OOM-killed the pod. 7p adds a depth-first `generate_model_combinations_streaming` generator that yields one fully-expanded model at a time (O(depth) live), and a `calc_and_save_streaming` consumer that prepares → calculates → saves → releases each one. `create()` branches on mode at the top: the Celery path and the legacy list/cluster helpers are untouched; the sync path skips stages 1–3. A `LEX_SYNC_STREAMING_EXPANSION=false` env var rolls back to the legacy materialized path. The expansion is dependency-aware (a field's values can depend on an earlier field), so the generator reuses `_get_field_values` and recurses depth-first to preserve identical semantics.

**Scenario range:** 7.178 – 7.187. **Test file:** `lex/test_project/tests/calculations/test_7p_streaming_expansion.py`. **Type:** U (+ I for the E2E sync create path). **Status:** ✅ Complete (Session 79 — June 10). Covers `lex/core/mixins/CalculatedModelMixin.py`. Equivalence gate: ordered defining-field fingerprint identical legacy-vs-streaming; bounded-peak memory regression test; N≈10k benchmark in `docs/runs/`.

**Out of scope (accepted by design):** when two expanded combinations resolve to the *same* defining-field tuple within one `create()`, streaming saves each model before preparing the next, so the second `delete_models_with_same_defining_fields()` sees the first's just-saved row and reuses it (one row) where the legacy batch-prepare would insert two. This degenerate same-tuple-collision case is intentionally not covered by the equivalence suite; the `LEX_SYNC_STREAMING_EXPANSION=false` env var is the rollback path if a project relies on the legacy behavior.

---

### 7m. `CalculationSignals.update_calculation_status` + `One.py` `model_name` propagation ✅

**Gap:** PR #615 threaded `model_name=instance._meta.object_name` through two call sites: `update_calculation_status` in `CalculationSignals.py` now passes it to `mark_in_progress` on the IN_PROGRESS branch, and `One.update()` in `One.py` does the same in the early-registration block when `calculate=true`. The `ActiveCalculationStateStore` uses `model_name` to partition active-calculation lookups; if the propagation silently regresses (e.g. the kwarg is dropped or passed as `None`), the store records calculations under the wrong key and the active-release endpoint returns stale or missing data.

**Scenario range:** 7.188 – 7.195. **Test file:** `lex/test_project/tests/calculations/test_7m_calc_signals.py`. **Type:** U + E. **Status:** ✅ Complete (Session 80 — June 18). Covers `lex/core/signals/CalculationSignals.py`, `lex/api/views/model_entries/One.py`.

---

### 7q. Nested fan-out dispatches by default from inside a worker ✅

**Gap:** A real production calc (`InvestmentPosting`, 65 568 models across 50 clusters) ran *synchronously* instead of dispatching to Celery. Root cause: an `is_celery_worker_process()` worker-detection guard (added commit `40d17b3`, 2026-03-29) made nested fan-out **opt-in** — a calculation already executing inside a Celery worker would collapse its nested work to an inline synchronous run unless the caller opened an explicit async context. This silently serialised large combinatorial calcs onto a single worker slot. The guard was removed in **both** dispatch paths so the default is "always dispatch": `CalculatedModelMixin._dispatch_model_processing` (fan-out) now dispatches whenever `CELERY_ACTIVE` + `.delay` exist, and `CalculationModel.calculate_hook` (single-instance) opens its own `WaitForTasks` when no async context is active (reusing an outer scope when present). When no context is active the dispatcher blocks on the children — correctness/ordering preserved at the cost of holding the worker slot, per the explicit "don't worry about blocking" decision. `is_celery_worker_process()` itself is retained for diagnostics/logging (still asserted correct by 8.6).

**Scenario range:** 7.196 – 7.201. **Test file:** `lex/test_project/tests/calculations/test_7q_worker_default_dispatch.py`. **Type:** E. **Status:** ✅ Complete (Session 86 — June 29). Covers `lex/core/mixins/CalculatedModelMixin.py` (`_dispatch_model_processing`) and `lex/core/models/CalculationModel.py` (`calculate_hook` dispatch branch). 7.196–7.198 drive the mixin (inside-worker, no-context / WaitForTasks / FireAndForget — all fan out to `CeleryTaskDispatcher`, never `calc_and_save_sync`); 7.199–7.201 drive `CalculationModel` (outer `WaitForTasks` ⇒ drains on scope exit; `FireAndForget` ⇒ never blocks). 6 pass / 0 fail. Companion stale-test updates: `test_calculation_wait_contexts.py`, `test_calculated_model_mixin.py`, and 8.6 message in `test_8b_dispatch_context.py`. **Session 91 note:** Session 89 briefly flipped scenario 7.199 to inline (an abort-safety guard, draft cluster 7r) — that was withdrawn before commit per the developer's explicit requirement that nested calcs dispatch and parallelise. Always-dispatch is the pinned default on BOTH paths; the abort→resume hole is closed by the cluster cancel marker instead (see 8ad).

---

### 7r. (withdrawn — Session 91) Per-instance inline-inside-worker guard

**Withdrawn before commit.** Session 89 drafted this batch (scenarios 7.202–7.204) to restore an `is_celery_worker_process()` inline guard on the per-instance `CalculationModel.calculate_hook` path as the Report 1 (abort→resume) fix. Live verification showed it broke the framework's core parallelism contract: a nested calc inside a worker (e.g. a project's `CalculateNAV` inside another calculation) ran inline on the parent's worker instead of dispatching to a free one, and only an explicit `WaitForTasks` restored dispatch. Per the developer's explicit requirement ("calculation can dispatch calculations"), the guard, the 7.199 flip, and this batch's test file were withdrawn; always-dispatch (7q) is the pinned default on both paths. Report 1 abort-safety is provided by the restart-surviving cluster cancel marker instead — see **cluster 8ad**. Letter 7r stays reserved for this record; scenarios 7.202–7.204 were never landed and the next cluster-7 batch may reuse them.

---
