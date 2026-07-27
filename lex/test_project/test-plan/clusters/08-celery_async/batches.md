## Cluster 8 — Celery & Async (existing 8a–8g)

### Batch 8h — Dispatcher & local scheduler

| Property | Value |
| --- | --- |
| Scenario range | 8.40 – 8.52 |
| Type | I |
| Files covered | `core/tasks/CeleryTaskDispatcher.py`, `process_admin/utils/local_scheduler.py` |
| Test file | `lex/test_project/tests/celery/test_dispatcher_and_local_scheduler.py` |
| Test classes | `TestCeleryTaskDispatcherRouting` (CELERY_ACTIVE on/off branches — use `patch.dict` per the test-suite rule), `TestLocalSchedulerFallback` (sync execution, error capture, status update) |
| Fixtures | `AtomicCalc`, `NonAtomicCalc` |
| Est. tests | ~12 |
| Coverage gain | +0.7 % |
| Prereqs | none |

### Batch 8i — `celery.py` app config

| Property | Value |
| --- | --- |
| Scenario range | 8.53 – 8.57 |
| Type | U |
| Files covered | `lex/lex_app/celery.py` |
| Test file | `lex/test_project/tests/celery/test_celery_app_config.py` |
| Test classes | `TestCeleryAppConfig` (broker URL resolution, autodiscover, beat schedule registration) |
| Fixtures | none |
| Est. tests | ~5 |
| Coverage gain | +0.2 % |
| Prereqs | none |

> `celery_tasks.py` keeps its slot in cluster 8j (Tier-A coverage forecast).

### Batch 8u — Cancellation-aware CallbackTask failure mapping (Session 67 — June 1)

| Property | Value |
| --- | --- |
| Scenario range | 8.73 – 8.77 |
| Type | U |
| Files covered | `lex/lex_app/celery_tasks.py` — `_is_cancellation_exception` helper + the `CallbackTask.on_failure` branch that maps `TaskRevokedError` / `SoftTimeLimitExceeded` / `WorkerLostError` / `billiard.Terminated` / `CalculationCancelled` onto `CANCELLED` (not `ERROR`); **Session 68** added 8.77 pinning `CallbackTask._update_model_status`'s audit-status mapping, and **Session 69** widened the mapping to three distinct values — SUCCESS → `success`, CANCELLED → `cancelled`, ERROR → `failure` — so the audit row can distinguish a user-cancelled run from a crash without relying on the error_message string |
| Test file | `lex/test_project/tests/celery_async/test_8u_cancel_revoke.py` |
| Test classes | `TestCluster08u_CancellationExceptionDetector` (every documented cancellation class recognised; generic runtime errors rejected; subclasses of cancellation classes still detected; `None` handled safely); `TestCluster08u_AuditStatusForTerminalStates` (8.77 — SUCCESS → `success`, ERROR / CANCELLED → `failure`) |
| Fixtures | none (synthetic exception stand-ins mirroring celery/billiard class names so the worker stack does not have to be imported; 8.77 mocks `ensure_terminal_calculation_audit` to observe the `audit_status` argument) |
| Est. tests | 5 (12 sub-test cases) |
| Coverage gain | +0.15 % |
| Prereqs | none |
| Status | ✅ Complete (Session 67 — 4 scenarios; Session 68 extended +1 — 5 pass / 0 fail / 12 sub-tests pass / 0.11s; runs broker-free, DB-free) |

---

### Batch 8v — Cluster-wide cascade cancellation: Redis cancel index (Session 74 — June 5)

| Property | Value |
| --- | --- |
| Scenario range | 8.78 – 8.89 |
| Type | U (+ one E for 8.87) |
| Files covered | `lex/core/cancellation/cluster_cancel_index.py` (new — best-effort Redis HASH tree keyed by `calculation_id`: `register_task` / `unregister_task` / `get_tree` / `mark_cancelled` / `is_cancelled`, lazy `redis.from_url(CELERY_BROKER_URL)` client, `_enabled_by_config` gate on `CELERY_ACTIVE` + `LEX_CLUSTER_CANCEL_ENABLED`, all ops try/except → silent no-op, never raise); `lex/core/signals/ActiveCalculationStateStore.py` (`set_task_id` → `register_task`, `clear` → `unregister_task`, both outside the store lock); `lex/core/models/CalculationModel.py` (`cancel()` unions in-memory descendants with `get_tree`, revokes every cluster-discovered task, `mark_cancelled` when recursive); `lex/lex_app/celery_tasks.py` (`calc_and_save` checks `is_cancelled` at task start, raises `CalculationCancelled`); `lex/lex_app/settings.py` (`LEX_CLUSTER_CANCEL_ENABLED` + tree/marker TTL knobs) |
| Test file | `lex/test_project/tests/celery_async/test_8v_cluster_cascade_cancel.py` |
| Test classes | `TestCluster08v_IndexOperations` (8.78–8.82 register/unregister/get_tree/mark+is_cancelled/no-client-degrades, `SimpleTestCase`, mocked redis); `TestCluster08v_IndexDisabled` (8.83–8.84 `CELERY_ACTIVE` off + master-switch off → no-op); `TestCluster08v_StoreWriteThrough` (8.85–8.86 attach task_id writes through / terminal node leaves the index); `TestCluster08v_CancelUnionsClusterTree` (8.87 — **`E2ETestCase`**, `e2e_models=[CelerySyncCalc]`, `e2e_unpatch={"mark_in_progress"}`: `cancel()` revokes a child registered only in the Redis index); `TestCluster08v_CooperativeMarkerCheck` (8.88–8.89 late pod self-aborts on marker / no marker → runs normally) |
| Fixtures | none — `unittest.mock` on the redis client + `CeleryTaskDispatcher.app.control.revoke`; 8.87 reuses the `CelerySyncCalc` E2E model (schema-editor table creation, matching `test_7n_cancellation.py`) |
| Tests landed | **12 pass / 0 fail** (8.78–8.89); cluster_cancel_index coverage 70 % (uncovered lines are the Redis-failure `except` branches, exercised only on a real connection error — the suite mocks redis); regression across `celery_async` + `calculations` = 252 pass / 4 skip; existing 8u + 7n cancellation suites green |
| Coverage gain | new module `cluster_cancel_index.py` 0 → 70 % |
| Prereqs | none (graceful-degradation means no live Redis required for the suite) |
| Status | ✅ Complete (Session 74 — June 5; designed brainstorming→spec→plan, executed subagent-driven) |

---

### Batch 8w — Worker-recovery terminal-outcome guard: no ERROR→SUCCESS resurrection

| Property | Value |
| --- | --- |
| Scenario range | 8.90 – 8.102 |
| Type | U (helper logic + `scan_and_recover` orchestration, mocked registry/backend) + I (real `CalculationModel` rows for the row-state signal) |
| Files covered | `lex/lex_app/celery_recovery/supervisor.py` — new `_result_already_settled` (ready `AsyncResult` ⇒ task body concluded; defensive degrade to False on backend error), `_rows_already_settled` (every extracted `CalculationModel` row out of `IN_PROGRESS`/`NOT_CALCULATED` ⇒ settled; False on no rows / any unfinished / DB error), `_already_finished` (OR of the two), and the `scan_and_recover` guard inserted **after** the cancellation check and **before** the retries/budget branch (deregister + count `already_finished`, never requeue) |
| Test file | `lex/test_project/tests/celery_async/test_8w_recovery_terminal_guard.py` |
| Test classes | `TestCluster08w_ResultBackendSettled` (8.90–8.92 ready ⇒ settled / unready ⇒ not / backend error degrades, `SimpleTestCase`, mocked app); `TestCluster08w_AlreadyFinishedCombination` (8.98–8.99 backend alone settles / neither signal ⇒ unfinished); `TestCluster08w_ScanSkipsFinished` (8.100–8.102 — drives real `scan_and_recover` with the registry boundary mocked: finished ⇒ deregister-not-requeue, unfinished-under-budget ⇒ requeue, finished-beats-budget-exhaustion ordering); `TestCluster08w_RowsSettled` (8.93–8.97 + 8.93b — **`E2ETestCase`**, `e2e_models=[CelerySyncCalc]`: all-terminal/IN_PROGRESS/NOT_CALCULATED/mixed/no-rows + every terminal state) |
| Fixtures | none — `unittest.mock` on `app.AsyncResult` and the `registry`/`_requeue`/`_give_up` seams; the row scenarios reuse the existing `CelerySyncCalc` E2E model |
| Tests landed | **8 pass / 0 fail** (the U scenarios: result-backend signal, OR-combination, full `scan_and_recover` orchestration incl. resurrection-prevention + guard-before-budget ordering). The 6 I (real-row) scenario **bodies pass their assertions** — only Django's shared `TransactionTestCase` teardown-flush errors in the borrowed local venv (`test_db_lex` missing tables), identically to the pre-existing 8v E2E test; a DB-provisioning gap of the local environment, not a code defect — they gate normally on the CI Postgres service |
| Coverage gain | `lex/lex_app/celery_recovery/supervisor.py` — terminal-guard branch + 3 new helpers newly covered |
| Prereqs | the I (real-row) scenarios need a Postgres test DB with the lex schema migrated (CI service); the U scenarios are broker-/DB-free |
| Status | ✅ Complete — source fix + paired tests + plan sync in one change |

---

### Batch 8x — Liveness-aware startup reset: recovery hand-off (no live-calc abort)

| Property | Value |
| --- | --- |
| Scenario range | 8.103 – 8.115 |
| Type | U (ownership lookup, mocked registry) + I (real `CalculationModel` rows driven through the startup sweep) |
| Files covered | `lex/lex_app/celery_recovery/supervisor.py` — new `tracked_calculation_record_ids()` (reads `registry.list_tracked()` + `get_payload()`, reuses `_extract_calculation_models`, returns `{(label_lower, pk)}` for tracked rows with a non-None pk — alive **or** expired-but-tracked, no `is_alive` gate; empty set when recovery off / Redis down). `lex/process_admin/utils/model_registration.py` — `_handle_calculation_model_reset` gains an optional `tracked_record_ids` param and a skip-if-owned `continue` placed **before** the `ABORTED` flip / history write / audit call; `register_models` computes the set once on the first `CalculationModel` and only when `CALLED_FROM_START_COMMAND` is set (no registry read outside startup), threading it through the per-model loop |
| Test file | `lex/test_project/tests/celery_async/test_8x_startup_reset_recovery_handoff.py` |
| Test classes | `TestCluster08x_TrackedRecordIds` (8.103–8.108 — `SimpleTestCase`, mocked registry: alive-tracked ⇒ owned / expired-but-tracked ⇒ still owned & `is_alive` never called / empty registry ⇒ empty set / non-calc payload ⇒ nothing / multi-task ⇒ union / pk-None ⇒ excluded); `TestCluster08x_StartupSweepDefersToOwnership` (8.109–8.115 — **`E2ETestCase`**, `e2e_models=[CelerySyncCalc]`: owned row stays `IN_PROGRESS` & not audited / untracked ⇒ `ABORTED` + audited / mixed ⇒ only untracked aborts / empty ownership ⇒ all abort (back-compat) / gate-off ⇒ no-op / precomputed set ⇒ no registry recompute / 8.115 no-kwarg ⇒ self-computes from registry) |
| Fixtures | none — `unittest.mock` on the `registry.list_tracked`/`get_payload` seams and on `supervisor.tracked_calculation_record_ids`; the row scenarios reuse the existing `CelerySyncCalc` E2E model and the `E2ETestCase` default `ensure_terminal_calculation_audit` patch (read via `self._patch_map`) |
| Tests landed | **6 pass / 0 fail** (the U scenarios: ownership lookup incl. the load-bearing no-`is_alive` invariant and the empty-set degrade). The 7 I (real-row sweep) scenario **bodies pass their assertions** — only Django's shared `TransactionTestCase` teardown-flush errors in the borrowed local venv, identically to the pre-existing 8v/8w E2E tests; a DB-provisioning gap of the local environment, not a code defect — they gate normally on the CI Postgres service |
| Coverage gain | `lex/lex_app/celery_recovery/supervisor.py` — `tracked_calculation_record_ids` newly covered; `lex/process_admin/utils/model_registration.py` — the startup-reset skip-if-owned branch + caller threading newly covered |
| Prereqs | the I (real-row) scenarios need a Postgres test DB with the lex schema migrated (CI service); the U scenarios are broker-/DB-free. Stacked on Batch 8w (#603 terminal guard) — the deferral composes with that guard |
| Status | ✅ Complete — source fix + paired tests + plan sync in one change |

---

### Batch 8y — Embedded-beat recovery driver: schedule wiring, queue isolation, entrypoint

| Property | Value |
| --- | --- |
| Scenario range | 8.116 – 8.122 |
| Type | U |
| Files covered | `lex/lex_app/settings.py` — the `CELERY_BEAT_SCHEDULE` `lex-celery-recovery-sweep` entry: `task` bound to the registered `sweep_dead_workers` name, `options.queue = "recovery"` (off the main KEDA-watched queue), `options.expires` bounding stale ticks. `lex/lex_app/celery_recovery/entrypoint.py` — new `beat_main(argv=None)` obtains the one canonical app via `supervisor._get_app()` and calls `app.worker_main(["worker","-B","-Q","recovery","--concurrency","1","--scheduler","django_celery_beat.schedulers:DatabaseScheduler","-l","info", …])`; registered as the `lex-recovery-beat` console script in `pyproject.toml`. `lex/lex_app/celery_recovery/supervisor.py` — `_requeue` queue routing (unchanged, pinned: `incremented.get("queue") or _default_queue()` ⇒ recovered task to its main queue, never `recovery`). `lex/lex_app/celery_recovery/heartbeat.py` — `_UNTRACKED_TASK_NAMES` already excludes the sweep (pinned). |
| Test file | `lex/test_project/tests/celery_async/test_8y_beat_recovery_driver.py` |
| Test classes | `TestCluster08y_BeatScheduleWiring` (8.116–8.119 — `SimpleTestCase`, reads `django.conf.settings` + `heartbeat._UNTRACKED_TASK_NAMES`: schedule names the registered sweep / sweep excluded from heartbeat tracking / sweep on dedicated `recovery` queue / that queue ≠ main default queue); `TestCluster08y_RequeueRoutingInvariant` (8.120–8.121 — fake Celery app records `send_task`: recovered task to payload's main queue not `recovery` / missing-queue fallback is the default main queue); `TestCluster08y_RecoveryBeatEntrypoint` (8.122 — `mock.patch` `worker_main` on `supervisor._get_app()`: argv starts `worker` with `-B`, binds `-Q recovery`, selects the `DatabaseScheduler`) |
| Fixtures | none — `unittest.mock` on `app.worker_main` and a synthetic fake Celery app (`send_task`/`backend.mark_as_failure`); settings read directly from `django.conf.settings`. Broker, Redis, and Celery itself never contacted |
| Tests landed | **7 pass / 0 fail** locally (0.09s) — pure-logic U across the settings dict, the heartbeat frozenset, `_requeue`'s queue selection, and the `worker_main` argv |
| Coverage gain | `lex/lex_app/celery_recovery/entrypoint.py` — `beat_main` + factored `_bootstrap_django` newly covered; `lex/lex_app/celery_recovery/supervisor.py` — `_requeue` main-queue routing pinned; `settings.py` `CELERY_BEAT_SCHEDULE` recovery entry asserted |
| Prereqs | none — all scenarios are broker-/DB-free. Infra (chart `celery_beat_recovery.yaml` + `workers.recoveryDriver` selector, supervisor gating) lives in `LEX_TERRAFORM_MODULES` on a matching branch and is out of scope for the framework test-plan |
| Status | ✅ Complete — source + paired cluster tests + plan sync in one change (tests were first mis-placed in the legacy `lex/tests/unit/infra/` audit tree; reverted and rewritten here per AGENTS.md Prime Directive 2) |

---

### Batch 8aa — Post-task warm shutdown honours the idle-shutdown master switch (Session 85 — June 26)

| Property | Value |
| --- | --- |
| Scenario range | 8.125 – 8.128 |
| Type | U |
| Files covered | `lex/lex_app/celery.py` — `shutdown_worker_after_task_completion` (the `task_postrun` handler). Added the missing `_idle_shutdown_enabled()` early-return guard so `LEX_WORKER_IDLE_SHUTDOWN_ENABLED=false` disables the post-task warm shutdown, matching the `task_revoked` fast-path and the `worker_ready` idle watchdog (which already gate on it). Fixes the embedded-beat recovery pod (`celery_beat_recovery.yaml`) crash-loop: it warm-shut-down after its first `sweep_dead_workers`, killing beat |
| Test file | `lex/test_project/tests/celery_async/test_8aa_postrun_shutdown_guard.py` |
| Test classes | `TestCluster08aa_PostrunShutdownGuard` (8.125 master switch off ⇒ no broadcast — the recovery-beat bug; 8.126 switch on + non-local ⇒ broadcast still fires to the completing worker with `completed_task_id` excluded; 8.127 local target ⇒ never broadcasts; 8.128 `task=None` ⇒ safe no-op) |
| Fixtures | none — `unittest.mock` on `_is_non_local_deployment_target` / `_idle_shutdown_enabled` and a task stand-in whose `.app.control.broadcast` is observed; broker-/DB-free |
| Tests landed | **4 pass / 0 fail** (8.125–8.128). Regression: full `celery_async` cluster + `lex/tests/unit/infra/test_worker_self_termination.py` = 144 pass / 4 skip / 12 subtests |
| Coverage gain | `lex/lex_app/celery.py` — the disabled-flag branch of `shutdown_worker_after_task_completion`, which previously had **no** test (the gap that let the bug ship) |
| Status | ✅ Complete — source fix + paired tests + plan sync in one change. Allocated `8aa` because `8y` (recovery driver) and `8z` (initial-data executor) were both already taken |

---

### Batch 8ab — Nested fan-out join is worker-safe: `allow_join_result` wrap (Session 88 — July 1)

| Property | Value |
| --- | --- |
| Scenario range | 8.129 – 8.138 |
| Type | U |
| Files covered | `lex/core/tasks/CeleryTaskDispatcher.py` — `_handle_task_results` now wraps `rs.join(propagate=False)` in `allow_join_result()` (import added to the lazy `from celery.result import ResultSet, allow_join_result`). A **nested** calc fanning out from inside a Celery worker (`dispatch_calculation_groups` → `_dispatch_single_group` → `_handle_task_results`) previously hit an unwrapped `ResultSet.join()`, which Celery hard-forbids inside a task ("Never call result.get() within a task!"). The old `is_celery_worker_process()` guard masked it by running nested calcs inline; removing that guard (batch 7q — nested fan-out now dispatches by default) exposed the crash. Prod repro: `InvestmentPosting` (5072 models, 2 tasks) crashed on the assertion, fell back to complete-sync, re-committed persisted rows → duplicate-key violation. Fix mirrors `WaitForTasks.wait_for_completion`, which already blocks under `allow_join_result` |
| Test file | `lex/test_project/tests/celery_async/test_8ab_dispatcher_join_worker_safe.py` |
| Test classes | `TestCluster08ab_HandleResultsJoinWorkerSafe` (8.129 in-worker join no longer raises — the exact crash regression; 8.130 worker join-block flag restored after return; 8.131 non-worker path unaffected; 8.132 worker-safe + failed group still retried; 8.133 worker-safe + join raises → complete sync; 8.134 worker-safe + raising status check still queues group; 8.135 `allow_join_result` import required); `TestCluster08ab_NestedDispatchWorkerSafe` (8.136 no context ⇒ implicit `WaitForTasks` + safe join — the production path; 8.137 explicit `WaitForTasks` reused + safe join; 8.138 explicit `FireAndForget` reused + safe join) |
| Fixtures | none — deterministic broker-free repro: `celery._state._set_task_join_will_block(True)` simulates a worker, a fake `ResultSet` (patched only on `celery.result.ResultSet`) whose `.join` calls the **real** `celery.result.assert_will_not_block()`, with the **real** `allow_join_result` left in place so the wrap is genuinely exercised. `calc_and_save.delay` / FF / WFT lazy imports mocked as in batch 8l |
| Tests landed | **10 pass / 0 fail** (8.129–8.138). Regression: full `celery_async` cluster + `test_7q_worker_default_dispatch.py` = 143 pass / 4 skip / 12 subtests |
| Coverage gain | `CeleryTaskDispatcher._handle_task_results` — the `allow_join_result`-wrapped join branch and its worker-context behaviour, plus the end-to-end nested `dispatch_calculation_groups` path under a simulated worker (previously only exercised outside a worker, where the missing wrap never triggered) |
| Status | ✅ Complete — source fix + paired tests + plan sync in one change. Allocated `8ab` (next free letter after `8aa`); 8.129 picks up after cluster-8 scenario max 8.128 |

---

### Batch 8ac — Complete-sync fallback is idempotent: Report 2 duplicate-key fix (Session 89 — July 1)

| Property | Value |
| --- | --- |
| Scenario range | 8.139 – 8.141 |
| Type | I (real DB — `CombinatorialCalc.defining_fields=[region, category]` yields a real `UniqueConstraint('defining_fields_CombinatorialCalc')`) |
| Files covered | `lex/core/mixins/CalculatedModelMixin.py` (`calc_and_save_sync` — now calls `delete_models_with_same_defining_fields()` immediately before `prepared.lex_func()(*args)` / `prepared.save()`, mirroring `calc_and_save_streaming`); exercises `lex/core/tasks/CeleryTaskDispatcher.py`'s complete-sync fallback. Production Report 2 (local, `celery --concurrency 3` + 14 workers): a connection storm (`FATAL: sorry, too many clients already`) crashed fan-out setup → the fallback ran `calc_and_save_sync(all_models)` on models dedup-resolved at T0 (pk reset for fresh INSERT) → a sibling had since committed the row → blind `save()` INSERTed a duplicate → `duplicate key value violates unique constraint "defining_fields_EndBalance"` (558 models). Re-resolving before save makes it idempotent: 1 existing row → UPDATE, 0 → INSERT. Option A (chosen over restoring the mixin inline guard) so 7q's parallel fan-out is preserved; one edit fixes all four fallback call sites (`CeleryTaskDispatcher.py:147, 229, 378, 412`) |
| Test file | `lex/test_project/tests/celery_async/test_8ac_sync_fallback_idempotent.py` |
| Test classes | `TestCluster08ac_SyncFallbackIdempotent` (8.139 fallback on a model whose defining-fields row a sibling already committed ⇒ re-resolves → UPDATE, same pk, exactly 1 row; 8.140 fallback on a fresh model ⇒ INSERT, 1 row; 8.141 end-to-end `dispatch_calculation_groups` with `_dispatch_single_group` raising a simulated connection storm + one row pre-committed ⇒ complete-sync fallback ⇒ NO duplicate key, (US,A) UPDATEd + (EU,B) INSERTed) |
| Fixtures | `CombinatorialCalc` (reused from cluster 7, via E2ETestCase — its defining-fields UNIQUE constraint is what makes the duplicate-key path real); `fail_for_region` reset to `None` in `setUp` |
| Tests landed | 3 pass / 0 fail (8.139–8.141). Regression: full `celery_async` cluster = 140 pass / 4 skip |
| Coverage gain | the re-resolve-before-save branch of `calc_and_save_sync` + the complete-sync fallback in `dispatch_calculation_groups`, now pinned as a live Report 2 duplicate-key regression gate |
| Status | ✅ Complete — source fix + paired tests + plan sync in one change. Allocated `8ac` (next free letter after `8ab`); 8.139 picks up after cluster-8 scenario max 8.138 |

---

### Batch 8ad — Dispatched @lex_shared_task self-aborts on the cluster cancel marker: Report 1 fix with dispatch preserved (Session 91 — July 2)

| Property | Value |
| --- | --- |
| Scenario range | 8.142 – 8.144 |
| Type | U (`SimpleTestCase` — pure wrapper logic; the cancel index is mocked at the module boundary) |
| Files covered | `lex/lex_app/celery_tasks.py` (`lex_shared_task` wrapper — cooperative cancel-marker check at task start). Replaces the withdrawn Batch 7r inline guard as the Report 1 (abort→resume) fix: nested calcs keep DISPATCHING by default (7q — the developer's explicit parallelism requirement), and abort-safety comes from the restart-surviving Redis marker `cancel()` already persists (`cluster_cancel_index.mark_cancelled`). `calc_and_save` already checked the marker; decorated calculate methods dispatched via `func.delay` (e.g. a project's `CalculateNAV.calculate`) did not — the uncovered hole. The wrapper now raises `CalculationCancelled` before running anything when a dispatched execution (`context` kwarg with a calculation_id) finds its marker set, so a broker-redelivered child of an aborted calculation lands CANCELLED instead of silently resuming. Sync calls carry no context and never touch the cancel index |
| Test file | `lex/test_project/tests/celery_async/test_8ad_dispatched_task_cancel_marker.py` |
| Test classes | `TestCluster08ad_DispatchedTaskCancelMarker` (8.142 dispatched run + marker set ⇒ raises `CalculationCancelled` before the wrapped function runs, marker consulted for that calculation_id; 8.143 dispatched run + no marker ⇒ marker consulted, function runs exactly once; 8.144 synchronous run (no context kwarg) ⇒ cancel index never consulted, function runs — zero Redis dependency in sync mode) |
| Fixtures | none (module-level `@lex_shared_task` probe function; `_celery_is_active` patched False so calling the descriptor executes the task body in-process, the same code path a worker runs for a redelivered message) |
| Tests landed | 3 pass / 0 fail (8.142–8.144). Regression: full `calculations` + `celery_async` trees = 325 pass / 7 skip / 0 fail |
| Coverage gain | the dispatched-context cancel-marker branch of the `lex_shared_task` wrapper — pinned as the live Report 1 abort→resume regression gate that does NOT sacrifice nested-dispatch parallelism |
| Status | ✅ Complete — source fix + paired tests + plan sync in one change. Allocated `8ad` (next free letter after `8ac`); 8.142 picks up after cluster-8 scenario max 8.141. Companion: Batch 7r withdrawn (inline guard + 7.199 flip + test file removed), 7q restored to committed assertions |

---

---

### Batch 8z — Dispatch-time claims, queue-verified recovery, age-gated startup reset, boot watchdog ✅

| Property | Value |
| --- | --- |
| Scenario range | 8.145 – 8.156 |
| Type | U (+ one E sweep class) |
| Files covered | `lex_app/celery_recovery/registry.py` (`claim_dispatched`, `task_id_in_queue`, `status` field), `lex_app/celery_recovery/supervisor.py` (dispatched lane, `LEX_TASK_DISPATCH_GRACE_SECONDS`), `lex_app/celery_tasks.py` (`CallbackTask.apply_async` claim choke point), `process_admin/utils/model_registration.py` (`LEX_STARTUP_ABORT_MIN_AGE_SECONDS` age-gate), `lex_app/celery.py` (boot watchdog, `LEX_WORKER_BOOT_TIMEOUT_SECONDS`) |
| Test file | `lex/test_project/tests/celery_async/test_8z_dispatch_claim_and_boot_guard.py` |
| Test classes | `TestCluster08z_DispatchClaim`, `TestCluster08z_SupervisorDispatchedLane`, `TestCluster08z_StartupAgeGate`, `TestCluster08z_BootWatchdog` |
| Fixtures | reuses `CelerySyncCalc`; mocked redis clients per the 8v/8w pattern |
| Est. tests | 12 |
| Coverage gain | dispatch-to-start ownership gap + degraded-broker self-termination |
| Prereqs | 8w/8x semantics (extended, not changed: tracked→skip is untouched) |
| Status | ✅ Complete — 12 pass / 0 fail |
| Note | Incident 2026-07-14 (instance 1410): a healthy calculation whose worker pod was still Pending was blind-aborted by a recovery-beat restart, because registry ownership began only at `task_prerun`. Ownership now starts at dispatch (`status="dispatched"`, `claimed_at`, no heartbeat — the broker message is the liveness story). The supervisor never requeues a claim that is (or might be) still queued — a same-task-id double dispatch is impossible by construction — and recovers only verifiably vanished messages (Redis flushed/evicted), the exact incident remediation that used to strand work. Startup reset spares untracked rows younger than the age-gate (default 1800s; 0 = legacy) so blind-abort degradation can't race a scheduling backlog. Boot watchdog (armed on `worker_init`, default 300s) terminates workers that boot into an unreachable broker and never fire `worker_ready`. **Deliberate 8x edit:** its `_run_sweep` pins ownership semantics with the age-gate disabled (`LEX_STARTUP_ABORT_MIN_AGE_SECONDS=0`); the gate has its own scenario 8.155. |

---

### Batch 8d — In-flight LIST mirror (recovery-pod scale signal) ✅

| Property | Value |
| --- | --- |
| Scenario range | 8.157 – 8.164 |
| Type | U |
| Files covered | `lex_app/celery_recovery/redis_keys.py` (`inflight_list_key`), `lex_app/celery_recovery/registry.py` (register/deregister mirror, `reconcile_inflight_list`), `lex_app/management/commands/run_recovery_supervisor.py` (startup reconcile) |
| Test file | `lex/test_project/tests/celery_async/test_8d_inflight_list_mirror.py` |
| Test classes | `TestCluster08d_InflightListMirror` |
| Fixtures | `FakeRedis` (in-file: strings/sets/lists/pipeline — real LLEN/LREM semantics) |
| Est. tests | 8 |
| Coverage gain | scale-signal path for scaling the recovery pod to zero |
| Prereqs | none (behaviourally inert until KEDA points at the list) |
| Status | ✅ Complete — 8 pass / 0 fail |
| Note | Lex-app half of scaling the recovery pod to zero (design locked as **Option A — parallel Redis list**, chosen over an HTTP metrics endpoint: KEDA's native `redis` scaler reads `LLEN`, reuses the existing worker TriggerAuthentication, and shares the work's failure domain — a leaked entry keeps recovery *up*, wasteful not unsafe). The registry maintains `<id>:lex:recover:inflight` as an exact mirror of the index SET: SADD-guarded LPUSH in `register()` (a requeue re-register never double-counts), LREM count-0 in `deregister()`, and a startup `reconcile_inflight_list()` in the supervisor command for mid-cutover/crash safety. 8.157 mirror-once across requeues; 8.158 deregister drains all occurrences; 8.159 reconcile rebuilds from the SET; 8.160 the sweep's give-up path drains the signal to zero. **Interaction with 8z (resolved here):** 8z and 8d land together, so `claim_dispatched()` carries the same SADD-guarded LPUSH. The consequence is behavioural, not cosmetic — the scale signal now rises at *dispatch* instead of at task start, so the supervisor is up while the worker pod is still Pending, which is exactly the window incident 1410 left unguarded. 8.161 pins that reconcile cannot drop an id registered mid-pass (a `DEL`+rebuild would, and the list would undercount → KEDA scales the supervisor away mid-calculation); 8.162 collapses duplicates so `LLEN` stays an exact count; 8.163 pins that the loop reconciles after *every* sweep, since an on-demand pod outlives many calculations and startup-only reconciliation would let drift pin it up until restart. **Infra companion (blocked):** recovery pod → KEDA ScaledObject on `LLEN inflight`; blocked on relocating the bitemporal future-activation clock to the global scheduler. |
