## 8. Celery & Async

**What it tests:** Task dispatch to Celery, sync fallback when Celery is unavailable, `FireAndForget` / `WaitForTasks` context managers, and nested calculation dispatch.

**Why eighth:** Celery is an optional scaling layer on top of the calculation engine. Most customers start without it and add it later.

**Models needed:**
- `AtomicCalc` (reused)
- `CeleryCalc` — `CalculationModel` with `@lex_shared_task`-decorated `calculate()`

**Test scenarios:**

| # | Scenario | What We Assert |
|---|----------|----------------|
| 8.1 | `should_use_celery()` returns False when CELERY_ACTIVE=False | Calculation runs synchronously |
| 8.2 | `should_use_celery()` returns False when no `delay` attribute | Sync fallback |
| 8.3 | Sync execution produces correct final state | SUCCESS with correct history |
| 8.4 | Sync execution handles failure correctly | ERROR with correct history and error message |
| 8.5 | `dispatch_calculation_task` extracts context correctly | `operation_context` serialized without unpicklable objects |
| 8.6 | Nested calculation inside Celery worker runs synchronously | `is_celery_worker_process()` detected, no recursive dispatch |

### 8g. Task infrastructure — `lex/lex_app/celery_tasks.py` 

**Gap:** `lex/lex_app/celery_tasks.py` is ~958 lines of customer-visible Celery plumbing (``CallbackTask``, ``CeleryCalculationContext``, ``FireAndForget`` / ``WaitForTasks``, ``EnhancedBoundTaskMethod``, the ``lex_shared_task`` decorator). Cluster 8a covered only the ``should_use_celery()`` / sync-fallback surface. 8g drives the remaining customer-visible code without requiring a Redis broker.

**No broker needed.** Every scenario is broker-free:

1. Branches gated on ``CELERY_ACTIVE`` are driven by ``patch.dict(os.environ, {"CELERY_ACTIVE": "true"})`` — the framework never tries to open a broker connection on its own.
2. Scenarios that need a ``.delay(...)`` return value patch ``.delay`` onto a fake task to return a ``MagicMock`` stand-in for Celery's ``AsyncResult``.
3. ``WaitForTasks.wait_for_completion`` normally calls ``allow_join_result()`` (which talks to the broker in production); 8.14 swaps it for ``contextlib.nullcontext`` so the blocking path is exercised end-to-end without a connection.
4. The ``CallbackTask.on_success`` / ``on_failure`` scenarios instantiate the task class directly and drive real ORM + real signals — Celery itself never runs.

| # | Scenario | What We Assert |
|---|----------|----------------|
| 8.7 | ``CallbackTask.on_success`` → ``is_calculated = SUCCESS`` on the persisted row | Direct queryset ``.update()`` (no stale-snapshot overwrite); ``ensure_terminal_calculation_audit`` called with ``audit_status="success"`` |
| 8.8 | ``CallbackTask.on_failure`` → ``is_calculated = ERROR`` | Status persisted; audit called with ``audit_status="failure"`` + exception message + stack-trace string forwarded from ``einfo`` |
| 8.9 | ``initial_data_upload`` task short-circuits both callbacks | No status update, no audit call — regression gate against the opt-out ever being forgotten |
| 8.10 | ``CeleryCalculationContext(context=…, model_context=None)`` stamps and restores ``operation_context`` | Incoming ``calculation_id`` preserved; ``celery_task=True``, ``task_name="calc_and_save"``, fresh ``operation_id`` minted; prior context restored on exit |
| 8.11 | ``CeleryCalculationContext(context=None, …)`` is a safe no-op | ``operation_context`` NOT mutated — matches the sync-mode bypass in ``lex_shared_task`` |
| 8.12 | ``EnhancedBoundTaskMethod`` runs sync when CELERY_ACTIVE=true but no FF/WFT scope is active | ``.delay`` NOT called; task body invoked with ``(instance, *args, **kwargs)`` |
| 8.13 | FireAndForget priority — wins over an enclosing WaitForTasks | ``.delay`` called; result registered on FF scope, NOT on the outer WFT scope |
| 8.14 | WaitForTasks dispatches via ``.delay`` and blocks on scope exit | Result registered on WFT; on exit ``.get()`` is called (``allow_join_result`` swapped for ``nullcontext``); ``dispatched_results`` cleared |
| 8.15 | ``lex_shared_task`` wrapper pops reserved kwargs and enters ``CeleryCalculationContext`` when truthy ``context`` is supplied | Body sees only the user kwargs; returns ``(inner_result, args)`` — the shape ``CallbackTask._extract_model_instances`` depends on |

**Status:**  Complete — 9 pass / 0 fail / 0 xfail.

### 8u. Cancellation-aware `CallbackTask` failure mapping ✅

**Gap:** When a user presses Abort on a long-running calculation, the framework's new `CalculationModel.cancel()` calls `app.control.revoke(task_id, terminate=True, signal="SIGTERM")` — the worker process then raises one of `TaskRevokedError` / `SoftTimeLimitExceeded` / `WorkerLostError` / `billiard.Terminated` (plus the framework's own `CalculationCancelled` marker when cooperative cancel is in play). Before 8u, `CallbackTask.on_failure` treated every exception identically and persisted `is_calculated=ERROR`. That would flip every cancelled calculation to ERROR — the wrong incident signal (the audit row would say "calculation failed" when it actually succeeded at being cancelled), and would page the on-call team every time someone clicks Abort. 8u pins `_is_cancellation_exception` as the class-name-matching detector (walks the MRO so subclasses are also recognised, no hard imports of celery/billiard internals) and the `on_failure` branch that consumes it to write `CANCELLED` instead.

**Scenario range:** 8.73 – 8.77. **Test file:** `lex/test_project/tests/celery_async/test_8u_cancel_revoke.py`. **Type:** U. **Status:** ✅ Complete (Session 67 — June 1; extended Session 68 with 8.77 covering the audit-status mapping, and Session 69 tightened it to SUCCESS → `success`, CANCELLED → `cancelled`, ERROR → `failure` so the four non-pending `AuditLogStatus.status` values — including the startup-reset `aborted` — are all distinct; 5 pass / 12 sub-tests / 0.11s broker-free).

### 8v. Cluster-wide cascade cancellation — Redis cancel index ✅

**Gap:** `cancel()` could only revoke the task_ids it found in `ActiveCalculationStateStore`, which is **per-process in-memory**. When a calculation tree fans children out to other KEDA worker pods, those child task_ids live in *those* pods' memory — the root pod's `cancel()` never sees them, so pressing Abort flipped the root to CANCELLED while dangling children on other pods kept executing. 8v pins the fix: a best-effort Redis "cluster cancel index" (a HASH tree keyed by `calculation_id`) written through at the existing `ActiveCalculationStateStore.set_task_id` / `clear` chokepoints, so `cancel()` can discover and revoke the **whole tree cluster-wide**, plus a `calculation_id`-keyed cooperative cancelled-marker checked at `calc_and_save` start as a non-relied-upon net for the late-booting-pod / discovery-gap case. Every Redis op degrades silently (no client / `CELERY_ACTIVE` off / `LEX_CLUSTER_CANCEL_ENABLED=false` → full no-op) so the index can never break a calculation.

**Why a mix of U and one E:** the index operations, the disabled-config no-ops, the store write-through, and the cooperative marker check are pure logic driven against a mocked Redis client (U). Scenario 8.87 — that `cancel()` revokes a child registered *only* in the Redis index (i.e. on another pod) — is the integration surface, so it drives the real `cancel()` end-to-end through an `E2ETestCase` (schema-editor table creation matching `test_7n_cancellation.py`).

**Scenario range:** 8.78 – 8.89. **Test file:** `lex/test_project/tests/celery_async/test_8v_cluster_cascade_cancel.py`. **Type:** U (+ one E for 8.87). **Status:** ✅ Complete (Session 74 — June 5). Covers `lex/core/cancellation/cluster_cancel_index.py` (register/unregister/get_tree/mark_cancelled/is_cancelled + disabled-config no-ops), the `ActiveCalculationStateStore` write-through, the `cancel()` cluster-tree union+revoke, and the `calc_and_save` cooperative self-abort. 12 pass / 0 fail; existing 8u + 7n cancellation suites stay green; regression across `celery_async` + `calculations` = 252 pass / 4 skip.

### Sub-cluster 8w — worker-recovery terminal-outcome guard (no resurrection)

**Gap:** the heartbeat recovery supervisor (`lex/lex_app/celery_recovery/supervisor.py`) requeues tasks whose worker died, but an expired heartbeat only proves the *worker* is gone — not that the *task* was unfinished. A worker can persist a calculation's terminal state (e.g. `ERROR`) and then be hard-killed (SIGKILL / OOM / eviction) *before* `task_postrun` deregistered it; the stale registry entry then looks like a dead in-progress task, the supervisor requeues the same `task_id`, the re-run succeeds, and a row the user already saw as `ERROR` silently flips to `SUCCESS` — the ERROR→SUCCESS resurrection bug observed in the cluster. 8w pins the fix: before requeueing, `scan_and_recover` consults two authoritative completion signals — a *ready* `AsyncResult` (with `task_reject_on_worker_lost` a merely-lost worker never writes one, so a ready result proves the body ran) and the calculation rows themselves (every row out of `IN_PROGRESS`). If either says "done", the supervisor deregisters instead of requeueing.

**Why a mix of U and I:** the result-backend signal, the OR-combination, and the full `scan_and_recover` orchestration (deregister-not-requeue, no-false-positive on a live task, and guard-before-budget ordering) are pure logic driven against a mocked registry/backend (U). The row-state signal — the half that catches the reported bug — is exercised against real persisted `CalculationModel` rows through an `E2ETestCase` (I).

**Scenario range:** 8.90 – 8.102. **Test file:** `lex/test_project/tests/celery_async/test_8w_recovery_terminal_guard.py`. **Type:** U (+ I for the real-row signal). **Status:** ✅ Complete. Covers `lex/lex_app/celery_recovery/supervisor.py` (`_result_already_settled`, `_rows_already_settled`, `_already_finished`, and the `scan_and_recover` terminal guard). 8 U scenarios pass locally; the 6 I (real-row) scenarios pass their assertions — only the shared `TransactionTestCase` teardown-flush errors in the borrowed local venv, identically to the pre-existing E2E suite (a DB-provisioning gap, not a code issue); they gate normally on the CI Postgres.

### Sub-cluster 8x — liveness-aware startup reset (recovery hand-off)

**Gap:** the boot-time sweep `process_admin/utils/model_registration._handle_calculation_model_reset` flips *every* row left in `IN_PROGRESS` to `ABORTED`, on the assumption "backend restart ⇒ every in-progress calc is dead." In the split web/worker deployment that is false: the Celery worker pods are **separate** and survive a backend restart, so they keep running the work to completion. The blind sweep therefore aborts calculations that are still computing — and worse, because 8w's terminal-outcome guard then refuses to requeue a now-terminal row, a *dead-but-tracked* worker's row that the recovery supervisor would have resumed is permanently lost the moment the sweep aborts it. 8x pins the fix: the sweep defers to the recovery registry. Any stuck row owned by a tracked recovery task — alive heartbeat **or** expired-but-tracked — is left `IN_PROGRESS` for the live worker to finish or the supervisor to requeue/resume; only genuinely untracked rows are aborted. The decision is deliberately "owned by recovery at all?", not "alive?", precisely so it composes with 8w instead of fighting it. When recovery is off / Redis is unreadable the registry reports nothing tracked, so the set is empty and behaviour is byte-for-byte the original blind sweep.

**Why a mix of U and I:** the ownership lookup `tracked_calculation_record_ids()` (registry → `{(label_lower, pk)}`, alive-or-expired, degrades to empty) is pure logic driven against a mocked registry (U). The sweep's actual abort/skip decision — the half that protects live calculations — is exercised against real persisted `CalculationModel` rows driven straight through `_handle_calculation_model_reset` with the ownership set injected, asserting both the row's terminal state and whether an aborted-audit was written (I, `E2ETestCase`).

**Scenario range:** 8.103 – 8.115. **Test file:** `lex/test_project/tests/celery_async/test_8x_startup_reset_recovery_handoff.py`. **Type:** U (+ I for the real-row sweep). **Status:** ✅ Complete. Covers `lex/lex_app/celery_recovery/supervisor.py` (`tracked_calculation_record_ids`) and `lex/process_admin/utils/model_registration.py` (`_handle_calculation_model_reset` skip-if-owned filter + the `register_models` compute-once threading, gated so no registry read happens outside startup). 6 U scenarios pass locally; the 7 I (real-row) scenarios pass their assertions — only the shared `TransactionTestCase` teardown-flush errors in the borrowed local venv, identically to the pre-existing E2E suite (a DB-provisioning gap, not a code issue); they gate normally on the CI Postgres.

### Sub-cluster 8y — embedded-beat recovery driver (admin-visible schedule, queue isolation)

**Gap:** worker recovery shipped with a single cluster driver — the always-on `recovery-supervisor` pod looping `scan_and_recover()` in-process. Operators wanted the *scheduler tooling of Celery beat* — a DB-driven schedule visible and editable in the Django admin (`django_celery_beat` `PeriodicTask` rows via `DatabaseScheduler`) — **without** giving up the property that makes the supervisor correct on this cluster, where workers are a KEDA ScaledJob scale-to-0. Vanilla beat is wrong here: beat only *enqueues* the sweep; a worker must dequeue it, and with scale-to-0 workers that is a chicken-and-egg loop (to detect a dead worker, beat needs a worker to run the sweep), forces a KEDA cold-start every interval, and pollutes the very Redis list KEDA scales on. The chosen driver is an **embedded-beat, self-consuming pod**: `celery worker -B -Q recovery --concurrency 1 --scheduler django_celery_beat.schedulers:DatabaseScheduler` — the same singleton pod fires the existing `sweep_dead_workers` task onto a dedicated `recovery` queue it consumes *itself*, so the scan runs in-process (no worker needed to *detect* deaths), and `_requeue` re-dispatches recovered work to the calc's main queue, which raises `listLength` and drives KEDA 0→N. The recovery pod subscribes to **only** `-Q recovery`, so recovered work always flows outward and never loops back (the non-circular property).

**Why pure U:** every make-or-break property of this driver is a wiring invariant that fails *silently*, and each is exercisable without a broker, Redis, or Celery itself: the beat-schedule dict naming the registered sweep task, the `frozenset` of heartbeat-untracked task names, the sweep's target queue vs. the main default queue (settings reads), `_requeue`'s queue selection (fake app records `send_task`), and the argv `beat_main` hands `app.worker_main` (mocked on the one canonical app from `supervisor._get_app()`).

**Scenario range:** 8.116 – 8.122. **Test file:** `lex/test_project/tests/celery_async/test_8y_beat_recovery_driver.py`. **Type:** U. **Status:** ✅ Complete (Session 81 — June 11). Covers `lex/lex_app/settings.py` (`CELERY_BEAT_SCHEDULE`), `lex/lex_app/celery_recovery/entrypoint.py` (`beat_main`), and `lex/lex_app/celery_recovery/supervisor.py` (`_requeue` routing — pinned). 8.116 schedule names the registered sweep; 8.117 sweep excluded from heartbeat tracking; 8.118 sweep routed to the dedicated `recovery` queue; 8.119 that queue is distinct from the main default queue; 8.120 `_requeue` targets the payload's main queue, never `recovery`; 8.121 missing-queue fallback is the default main queue; 8.122 `beat_main` launches `worker -B -Q recovery --scheduler DatabaseScheduler`. 7 pass / 0 fail locally (0.09s). Infra (chart `celery_beat_recovery.yaml` + `workers.recoveryDriver` selector) lives in `LEX_TERRAFORM_MODULES` on a matching branch.

---

### Sub-cluster 8aa — post-task warm shutdown honours the idle-shutdown master switch

**Gap:** `LEX_WORKER_IDLE_SHUTDOWN_ENABLED` is the single master switch meant to turn off *all* worker self-termination. `lex/lex_app/celery.py` has three self-termination paths: the `task_postrun` post-task warm shutdown (`shutdown_worker_after_task_completion`, for KEDA ScaledJob workers that should exit after their one task), the `task_revoked` cancel fast-path, and the `worker_ready` idle watchdog. The revoke path and the watchdog both early-return on `not _idle_shutdown_enabled()`, but the `task_postrun` handler only checked `_is_non_local_deployment_target()` and `task is None` — it ignored the switch. The embedded-beat recovery pod (`celery_beat_recovery.yaml`) sets the switch to `false` because it runs `celery worker -B` and is idle by design between sweeps; with the guard missing it warm-shut-down after its first `sweep_dead_workers` and crash-looped, taking beat with it (observed in prod: sweep succeeds, then `worker: Warm shutdown` / `beat: Shutting down`). The fix adds the same `_idle_shutdown_enabled()` guard so the switch disables every path, matching the promise in the chart comment (`celery_beat_recovery.yaml:81-90`).

| # | Scenario | What We Assert |
|---|----------|----------------|
| 8.125 | Master switch off ⇒ no post-task shutdown | non-local target + `_idle_shutdown_enabled()` False: `task_postrun` sends no warm-shutdown broadcast, so the recovery-beat pod survives between sweeps |
| 8.126 | Switch on + non-local ⇒ broadcast still fires | the KEDA scale-to-zero path is preserved: the warm shutdown is broadcast to the completing worker with `completed_task_id` excluded from the idle check |
| 8.127 | Local deployment target ⇒ never broadcasts | dev/local runs never self-terminate, regardless of the switch |
| 8.128 | `task=None` ⇒ safe no-op | the defensive path returns without raising and broadcasts nothing |

**Scenario range:** 8.125 – 8.128. **Test file:** `lex/test_project/tests/celery_async/test_8aa_postrun_shutdown_guard.py`. **Type:** U. **Status:** ✅ Complete (Session 85 — June 26). Allocated `8aa` because `8y` (recovery driver) and `8z` (initial-data executor) were both already taken. Source: `lex/lex_app/celery.py` (`shutdown_worker_after_task_completion`). 4 U scenarios pass locally (broker-free, signal handler driven directly with mocked deployment-target / switch / `control.broadcast`).

---

### Cluster 8ab — Nested fan-out join is worker-safe (`allow_join_result`)

**Gap:** the framework lets a calculation dispatch *nested* fan-out work — a calc already running inside a Celery worker can partition its own models and dispatch them as child tasks via `CeleryTaskDispatcher.dispatch_calculation_groups`, then block on the children in `_handle_task_results` via `ResultSet.join()`. Celery **hard-forbids** `result.get()` / `ResultSet.join()` from inside a worker ("Never call result.get() within a task!") unless wrapped in `allow_join_result()`. The join was unwrapped; an `is_celery_worker_process()` guard *masked* it by forcing nested calcs to run inline. Removing that guard (cluster 7q — so nested fan-out dispatches by default, as customers expect) exposed the unwrapped join: a real nested `InvestmentPosting` run (5072 models, 2 tasks) crashed with that assertion, fell back to the complete-sync ladder, and re-committed already-persisted rows → duplicate-key violation. The fix wraps `rs.join(propagate=False)` in `allow_join_result()`, mirroring `WaitForTasks.wait_for_completion`, which already blocks under the same guard. These scenarios reproduce the crash deterministically **without a broker**: Celery's real `allow_join_result()` toggles the thread-local `task_join_will_block` flag and `assert_will_not_block()` (called at the top of the real `ResultSet.join`) raises the production error whenever that flag is set — so we set the flag to simulate "inside a worker", give a fake `ResultSet` a `.join` that calls the real `assert_will_not_block()`, and keep the real `allow_join_result` so the wrap is genuinely under test.

| # | Scenario | What We Assert |
|---|----------|----------------|
| 8.129 | In-worker join no longer raises | flag set (simulated worker) + fake join calls the real `assert_will_not_block()`: `_handle_task_results` completes, the join runs, and the block-flag is cleared *during* the join — the exact production-crash regression |
| 8.130 | Worker flag restored after return | `allow_join_result` is a proper CM: the join-block flag is True again after the call, so a later real `.get()` in the same worker task is still correctly forbidden (the guard is not permanently disabled) |
| 8.131 | Non-worker path unaffected | flag False (top-level dispatch): all-success → join runs once, no group routed to sync, flag stays False — the wrap is transparent to the ordinary path |
| 8.132 | Worker-safe + failed group still retried | simulated worker + one `.failed()` True: no crash AND the failed task's mapped group (only) goes to `calc_and_save_sync` |
| 8.133 | Worker-safe + join raises → complete sync | simulated worker + a genuine `join` failure (backend unreachable): every mapped group is flattened into one complete-sync fallback call |
| 8.134 | Worker-safe + raising status check | simulated worker + `task.failed()` raises: no crash AND the group is assumed failed and queued for sync retry (never silently dropped) |
| 8.135 | `allow_join_result` import required | a `celery.result` exposing `ResultSet` but not `allow_join_result` → the lazy import fails loudly as a chained `CeleryDispatchError` (guards against a regression that drops the import) |
| 8.136 | No context ⇒ implicit `WaitForTasks`, safe join | end-to-end `dispatch_calculation_groups` inside a worker, no explicit context: opens an implicit `WaitForTasks()` (same behaviour as before) and the join completes with the block-flag cleared — the production fan-out path |
| 8.137 | Explicit `WaitForTasks` reused, safe join | end-to-end inside a worker with an active `WaitForTasks`: no new WFT instantiated (nullcontext, no double-join) and the join stays worker-safe |
| 8.138 | Explicit `FireAndForget` reused, safe join | end-to-end inside a worker with an active `FireAndForget`: no implicit WFT (fire-and-forget preserved) and the join stays worker-safe |

**Scenario range:** 8.129 – 8.138. **Test file:** `lex/test_project/tests/celery_async/test_8ab_dispatcher_join_worker_safe.py`. **Type:** U. **Status:** ✅ Complete (Session 88 — July 1). Allocated `8ab` (next free letter after `8aa`); 8.129 picks up after cluster-8 scenario max 8.128. Source: `lex/core/tasks/CeleryTaskDispatcher.py` (`_handle_task_results` join wrap; `dispatch_calculation_groups` nested-in-worker path). 10 U scenarios pass locally (broker-/DB-free).

---

### Cluster 8ac — Complete-sync fallback is idempotent (Report 2 duplicate-key fix)

**Gap:** when the Celery fan-out setup crashes part-way, `CeleryTaskDispatcher.dispatch_calculation_groups` falls back to running every model inline via `calc_and_save_sync(all_models)`. Production Report 2 (local, `celery --concurrency 3` + 14 workers): `connection to server at "localhost" port 5432 failed: FATAL: sorry, too many clients already` → this fallback fired → `duplicate key value violates unique constraint "defining_fields_EndBalance"` (558 models). Mechanism: `_prepare_models_for_processing` dedup-resolves every model against the DB at T0 (before dispatch) and resets pks that had no existing row for a fresh INSERT. During fan-out a sibling child task commits row X; a later group trips the connection storm; the complete-sync fallback then blind-`save()`s the T0-prepared sibling whose pk is still null → a second INSERT collides with X. Unlike the normal (`_prepare_models_for_processing`) and streaming (`calc_and_save_streaming`) paths, the legacy `calc_and_save_sync` did **not** re-resolve before save — the idempotency gap. **Fix (Option A, chosen over restoring the mixin inline guard so 7q's parallel fan-out is preserved):** `calc_and_save_sync` now calls `delete_models_with_same_defining_fields()` immediately before save, mirroring `calc_and_save_streaming` — 1 existing row → returns it (UPDATE), 0 → pk already reset (INSERT). The resolver is itself idempotent, so re-preparing an as-yet-uncommitted model is a no-op. One edit fixes all four fallback call sites (`CeleryTaskDispatcher.py:147, 229, 378, 412`).

| # | Scenario | What We Assert |
|---|----------|----------------|
| 8.139 | Fallback UPDATEs a sibling's committed row | existing (US, A) row + fresh (US, A) instance (pk reset at T0) → `calc_and_save_sync` re-resolves → no IntegrityError, exactly 1 (US, A) row, same pk as the committed one (UPDATE not INSERT), name set by `calculate()` |
| 8.140 | Fallback INSERTs a fresh model | no (EU, B) row + fresh (EU, B) instance → INSERTs exactly one row, name set — the re-resolve is a no-op when nothing exists |
| 8.141 | End-to-end complete fallback, no duplicate key | existing (US, A) row + group `[(US,A),(EU,B)]`; `_dispatch_single_group` raises a simulated connection storm → `dispatch_calculation_groups` runs the complete-sync fallback over all models → NO duplicate-key IntegrityError; (US, A) UPDATEd (same pk), (EU, B) INSERTed, one row each |

**Scenario range:** 8.139 – 8.141. **Test file:** `lex/test_project/tests/celery_async/test_8ac_sync_fallback_idempotent.py`. **Type:** I (real DB — `CombinatorialCalc.defining_fields=[region,category]` yields a real UNIQUE constraint). **Status:** ✅ Complete (Session 89 — July 1). Allocated `8ac` (next free letter after `8ab`); 8.139 picks up after cluster-8 scenario max 8.138. Source: `lex/core/mixins/CalculatedModelMixin.py` (`calc_and_save_sync` re-resolve-before-save), `lex/core/tasks/CeleryTaskDispatcher.py` (complete-sync fallback path). 3 pass / 0 fail.

---

### Cluster 8ad — Dispatched @lex_shared_task self-aborts on the cluster cancel marker (Report 1 fix, dispatch preserved)

**Gap:** nested calculations DISPATCH by default (7q) — they must parallelise across workers, never collapse inline. That leaves the Report 1 abort→resume hole: a dispatched nested task is a broker message that outlives an abort (local cancellation is an in-memory revoke), so after a server+worker restart the broker redelivers the still-unacked task and the "cancelled" calculation silently resumes. The framework already had the right mechanism: `cancel()` persists a Redis "cancelled" marker per calculation_id (`cluster_cancel_index.mark_cancelled`, survives restarts), and `calc_and_save` checks it at task start. But a **decorated** calculate method (`@lex_shared_task`, e.g. a project's `CalculateNAV.calculate`) dispatches directly via `func.delay` and never consulted the marker — the uncovered hole. **Fix:** the generic `lex_shared_task` wrapper now performs the same cooperative check at task start — a dispatched execution (a `context` kwarg carrying a calculation_id) whose calc is marked cancelled raises `CalculationCancelled` before running anything, landing CANCELLED instead of resuming. Direct synchronous calls carry no context and never touch the cancel index (zero Redis dependency in sync mode; without Redis there is no broker to redeliver anyway).

**Scenario range:** 8.142 – 8.144. **Test file:** `lex/test_project/tests/celery_async/test_8ad_dispatched_task_cancel_marker.py`. **Type:** U. **Status:** ✅ Complete (Session 91 — July 2). Allocated `8ad` (next free letter after `8ac`); 8.142 picks up after cluster-8 scenario max 8.141. Source: `lex/lex_app/celery_tasks.py` (`lex_shared_task` wrapper). 8.142 dispatched run + marker set ⇒ raises `CalculationCancelled` before the wrapped function runs; 8.143 dispatched run + no marker ⇒ marker consulted, function runs normally; 8.144 synchronous run (no context) ⇒ cancel index never consulted, function runs. 3 pass / 0 fail.


---

### Cluster 8e — `create(*args)` reach `calculate()` on every route, Celery included (BUG-054)

**Gap:** `CalculatedModelMixin.create(*args)` passes its positional arguments to every generated row's `calculate()`. Both sync consumers did, and `CeleryTaskDispatcher` sent them in each group message, `(group, group_index, *args)`, but the generic Celery task `calc_and_save` called `model.lex_func()()`. Turning Celery on changed what a calculation computed. **Fix:** the task names the index slot, `calc_and_save(models, group_index=None, *args)`, and passes `args` on. The message keeps its shape, so a message queued, redelivered or requeued by worker recovery across an upgrade is read the same way by an old and a new worker.

The eager harness runs each task with the empty dispatch scopes a worker process starts with. Otherwise a task would see the dispatcher's own `WaitForTasks` scope, which is open in the same thread, and send `calculate()` off as one more message. Celery pickles a message even in eager mode, so `calculate()` would run on a copy and the task would save the row without its result, which never happens on a worker.

| # | Scenario | What We Assert |
|---|----------|----------------|
| 8.157 | Celery route, `create("Q4", 7)`, three region groups | three `calc_and_save` messages run, and every row's `calculate()` receives exactly `("Q4", 7)`: not nothing, and not the group index |
| 8.158 | Celery route, `create()` with a `calculate(self)` that takes no arguments | every row is calculated and saved; the group index is never handed to `calculate()` |
| 8.159 | A worker runs a group message `(group, 2, "Q4", 7)`, the shape v2.4.2 queued too | `calculate()` is called inside the task with `("Q4", 7)` only, the row is saved with it, and no further task starts |
| 8.160 | The task's other sender: a `CalculationModel` saved `IN_PROGRESS` dispatches `calc_and_save([row])` | the task runs once, `calculate()` runs with no arguments, and `CallbackTask` finds the row in the first argument and flips it to SUCCESS |
| 8.161 | Sync streaming route (default) | no task runs; every row receives `("Q4", 7)` |
| 8.162 | Sync materialising route (`LEX_SYNC_STREAMING_EXPANSION=false`) | no task runs; every row receives `("Q4", 7)` |
| 8.163 | Celery route where one row's `calculate()` fails once | the dispatcher's synchronous fallback calculates it again, every row ends with `("Q4", 7)`, and every call, in a task or in the fallback, received `("Q4", 7)` |

**Scenario range:** 8.157 – 8.163. **Test file:** `lex/test_project/tests/celery_async/test_8e_create_args_reach_calculate.py`. **Type:** I (real rows; Celery in eager mode). **Status:** ✅ Complete (2026-11-09). Batch: [8e](batches.md). Not covered separately: the per-group retry in `CeleryTaskDispatcher._handle_task_results` and the per-group fallback after a `CeleryDispatchError` from `.delay`. Neither changed, both already passed `*args` to `calc_and_save_sync`, and with the dispatcher's own `WaitForTasks` scope a failed task reaches the whole-batch fallback of 8.163 first.
### Cluster 8d — The startup reset works on every route and leaves asyncio usable (BUG-046)

**Gap:** the startup reset (`ModelRegistration.register_models` → `_handle_calculation_model_reset`, under `CALLED_FROM_START_COMMAND`) ran its ORM work through `nest_asyncio`: `nest_asyncio.apply()`, then `run_until_complete` on the current loop. That patched asyncio for the whole process. The patched `asyncio.run` never closes its loop, so after one `async_to_sync` with thread-sensitive work, asgiref found that open, stopped loop in the calling thread, and the next call blocked on it forever. Inside a running uvloop loop, which uvicorn uses by default whenever uvloop is installed, the patch raised `Can't patch loop of type uvloop.Loop` and stopped startup. **Fix:** the reset runs on a short-lived thread of its own (`_run_on_own_thread`). That thread has no event loop, whatever the caller's thread has, and it closes its database connection before it ends. Nothing in the process is patched.

| # | Scenario | What We Assert |
|---|----------|----------------|
| 8.154 | No running loop (a command, a Celery worker) | a fresh process registers its models under `lex start`. The stuck row is ABORTED, `asyncio.run` is still the standard library's, and two `async_to_sync` calls from the main thread, each with thread-sensitive ORM work, both return the row count |
| 8.155 | Inside a running asyncio loop (`lex start --loop asyncio`) | the same, with the models registered inside a running asyncio loop, where uvicorn sets Django up. No `SynchronousOnlyOperation`, no "loop is already running" |
| 8.156 | Inside a running uvloop loop (plain `lex start`) | the same inside a running uvloop loop. Startup carries on (it used to fail with "Can't patch loop of type uvloop.Loop"), the row is ABORTED, and both calls return. Skipped where uvloop is not installed (Windows) |

**Scenario range:** 8.154 – 8.156. **Test file:** `lex/test_project/tests/celery_async/test_8d_startup_reset_event_loops.py`. **Type:** I (a real `CalculationModel` row, reset by a fresh interpreter on this test's database). **Status:** ✅ Complete (2026-11-06). Batch: [8d](batches.md). The same bug in `LexAppConfig.ready()`, which applied the patch to every downstream process, is covered by cluster [1bo](../01-init/cluster.md).

---

### 8f. Scheduled dispatch — cron in a time zone, exactly-once claim, overlap, missed slots, one-off, the Celery route ✅

**What it tests:** The scheduler's clock calls `run_due_jobs(now)`; this pins what a cron expression means in a time zone and what a due slot does: it is claimed by exactly one clock, skipped while the previous run is still going, collapsed into one run when recently missed, recorded as missed beyond the grace window, run once for a one-off job, and sent to a Celery worker when Celery is on.

**Why a regression matters:** Double runs, runs at the wrong hour twice a year, a pile-up of runs after an outage, or jobs that silently never run.

| Scenario | Title | Asserts |
| --- | --- | --- |
| 8.167 | weekday schedule skips the weekend | `0 6 * * 1-5` after Friday 07:00 Berlin fires Mon–Thu 06:00 CET |
| 8.168 | classic cron syntax | steps, range steps, lists, month/weekday names, `@hourly`/`@monthly`; `7` = `0` = Sunday |
| 8.169 | day fields OR rule | both restricted → a day matching either fires; one `*` → only the other counts |
| 8.170 | unusable expressions refused | empty, 4 or 6 fields, out of range, backwards range, zero step, never fires, `@reboot`, unknown macro or name — each with its reason |
| 8.171 | spring-forward | a skipped 02:30 fires once at 03:00 CEST; a half-hourly schedule collapses the gap into one run |
| 8.172 | fall-back | a repeated 02:30 fires once; a half-hourly schedule does not rerun the repeated hour |
| 8.173 | unknown time zone refused | the message names a valid example |
| 8.174 | a due job runs and moves on | only the due job starts; `last_run_at` = now; next slot = next 06:00; a job not due is untouched |
| 8.175 | a disabled job never runs | no next slot; forced due, still ignored |
| 8.176 | two clocks, one run | two threads with their own connections tick at once; one starts the slot; the target runs once |
| 8.177 | runs never stack | a slot due mid-run is skipped with its reason and moves on; the run's own end-of-run save keeps the note and the moved slot |
| 8.178 | recent misses collapse | five missed 10-minute slots inside the grace window cost one run; next slot = next mark after now |
| 8.179 | old misses are recorded | a slot two hours late (grace one hour) does not run; the message names it; next slot = tomorrow |
| 8.180 | one-off runs once | next slot = `run_at`; it runs once; afterwards no next slot and no second run |
| 8.181 | the Celery route | with Celery on, `calc_and_save.delay` receives the job; nothing is calculated in the backend |
| 8.182 | an old copy cannot bring a slot back | a copy loaded before the clock ran the slot, renamed and saved after: name stored, slot and status kept, nothing runs again |

**Scenario range:** 8.167 – 8.182. **Test file:** `lex/test_project/tests/celery_async/test_8f_scheduled_dispatch.py`. **Type:** U + E. **Status:** ✅ Complete (2026-11-10). Batch: [8f](batches.md).
