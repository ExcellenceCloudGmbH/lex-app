# Test-Suite Dashboard

> **Back to:** [Progress index](../progress.md) | [Test Plan Index](../index.md)
> **Audience:** Engineering leadership, QA supervisors, anyone scanning suite health at a glance.
> **Update cadence:** after every work session — update the per-cluster table here. Bug rows live in **[`known-bugs.md`](../known-bugs.md)** (the gate-enforced source of truth), not here. The Copilot test-bot updates only the cluster row(s) it touched and (in modes B/C) appends a row to `known-bugs.md`.
>
> **Status cells are terse on purpose** — they carry only the at-a-glance signal (state + BUG refs + skip/xfail reasons + scenario range). Full scenario definitions live in [`../test-clusters.md`](../test-clusters.md); the per-session "what each batch covered" narrative lives in [`session-log.md`](session-log.md).

---

## At a glance

| Cluster | Scenarios | Implemented | Passing | Expected Failures | Not Started | Status |
|---------|-----------|-------------|---------|-------------------|-------------|--------|
| 1. Init — Project Bootstrap | 23 | 23 | 23 | 0 | 0 |  Complete (1.6b = CI showcase: detect-changes / makemigrations / migrate / Keycloak sync in one `lex Init`) |
| 1g. `KeycloakSyncManager` admin-facing methods (coverage-driven — April 24) | 10 | 10 | 10 | 0 | 0 |  Complete — `_make_sync_manager()` fixture, no Keycloak/DB |
| 1h. Bootstrap flow — URL + HTTP polling (coverage-driven — April 24) | 15 | 15 | 15 | 0 | 0 |  Complete — `requests.get` patched, network-free |
| 1i. Initial-data upload journey (coverage-driven — April 24) | 14 | 14 | 13 | 0 | 0 |  Complete (1 env-gated skip — 1.59 needs `INITIAL_DATA_AUDIT_LOGGING=true`) |
| 1o. Lazy imports + sync-exclusion + history-config helpers (coverage-driven — May 12) | 15 | 15 | 15 | 0 | 0 |  Complete — scenarios 1.110–1.124 |
| 1p. Settings / URLs / health view / config singletons (coverage-driven — May 12) | 22 | 22 | 22 | 0 | 0 |  Complete — scenarios 1.125–1.146 |
| 1q. Migration file completeness release gate (Session 70 — June 2) | 1 | 1 | 1 | 0 | 0 | ✅ Complete — scenario 1.147; `lex makemigrations ... --check --dry-run` must stay clean for framework apps |
| 1s. Log-noise cleanup + lex-namespace debug control — EXC-1787 (Session 75 — June 8) | 10 | 10 | 10 | 0 | 0 | ✅ Complete — scenarios 1.159–1.168; urllib3 InsecureRequestWarning gate + `LEX_LOG_LEVEL` lex-only DEBUG + console-handler level + blanket `LEX_SUPPRESS_WARNINGS` filter |
| 7k. Core exceptions + `ModelModificationRestriction` ABC + XLSXField spotter (coverage-driven — May 12) | 21 | 21 | 21 | 0 | 0 |  Complete — scenarios 7.122–7.142 |
| 2. CRUD via REST API | 23 | 23 | 23 | 0 | 0 |  Complete (Many bulk-write reconciled to DELETE-only; BUG-004 removed per product decision; BUG-005 fixed; 2.1 = CRUD showcase) |
| 2i. Cancel-calculation REST endpoint (Session 67 — June 1) | 4 | 4 | 4 | 0 | 0 | ✅ Complete — scenarios 2.93–2.96; pins the `PATCH cancel=true` short-circuit (202 / 409 / sibling-fields-ignored) |
| 3. Validation Hooks | 8 | 9 | 9 | 0 | 0 |  Complete |
| 4. Permissions | 14 | 14 | 12 | 2 | 0 |  Complete (BUG-008/010 tracked; 4.40 export full-deny; 4.41 read full-deny at detail endpoint) |
| 4e. Read-restriction filter backend (planned — April 21) | 7 | 7 | 5 | 0 | 0 |  Complete (2 skipped — 4.14/4.15 AuditLog DB-filter path deferred; 4.41 pins detail-GET read-deny) |
| 4f. Serializer-level masking (planned — April 21) | 8 | 8 | 8 | 0 | 0 |  Complete — 4.19–4.26 |
| 5. History & Bitemporal | 10 | 10 | 10 | 0 | 0 |  Complete |
| 5.11. History fallback-snapshot path (planned — April 21) | 1 | 1 | 1 | 0 | 0 |  Complete |
| 5g. History `valid_to` chaining contract (Session 51 — May 5) | 2 | 2 | 2 | 0 | 0 |  Complete — 5.61/5.61b close 5.4's unasserted half |
| 5h. History suppression toolkit (Session 51 — May 5) | 6 | 6 | 4 | 1 | 1 |  Complete (5.66 xfail — public `suspend_bitemporal()` CM not yet exposed; 5.67 deferred — needs fixture) |
| 5i. History API contract (Session 51 — May 5) | 4 | 4 | 2 | 0 | 2 |  Complete (5.73/5.74 auto-skip — MetaHistorical* not wired on test fixture; prod path in integration tests) |
| 5j. History snapshot completeness + `history_user` actor (Session 51 — May 5) | 2 | 2 | 2 | 0 | 0 |  Complete — 5.75 full snapshot, 5.76 `history_user` stamped |
| 5k. MetaHistory positive contract (Session 51 — May 5) | 4 | 4 | 0 | 1 | 3 |  Complete (auto-skip — MetaHistorical* not wired; prod registration covered in unit tests) |
| 5l. Future-dated bitemporal saves — scheduled activation contract (planned — May 7) | 7 | 0 | 0 | 0 | 7 |  Not started — save side of the future-activation handoff (worker side = 8.43); scenarios 5.91–5.97, reuses `HistSimpleItem` |
| 6. Audit Logging | 10 | 10 | 7 | 2 | 0 |  Complete (6.4 retired for 6.45; BUG-001 on 6.10; BUG-007 on 6.7) |
| 6d. Audit-log payload + GenericForeignKey (Session 51 — May 5) | 8 | 8 | 6 | 0 | 2 |  Complete — 6.41–6.46 (6.47/6.48 skipped — need failure-injection / signal-spy fixture) |
| 6e. Bulk audit (`BulkAuditLogMixin`) (Session 51 — May 5) | 3 | 3 | 2 | 0 | 1 |  Complete — 6.51/6.53 (6.52 partial-success skipped — needs per-row deny / DB-error fixture) |
| 6f. Audit-log resilience (Session 51 — May 5) | 3 | 3 | 3 | 0 | 0 |  Complete — pins `RETRYABLE_SQLSTATE_CODES` + `MAX_UPDATE_RETRIES` |
| 6g. Audit-log immutability (Session 51 — May 5) | 3 | 3 | 3 | 0 | 0 |  Complete — create/delete/edit all denied (incl. `AuditLogStatus`) |
| 6j. Audit-utils singletons & data-model contracts (coverage-driven — May 12) | 16 | 16 | 16 | 0 | 0 |  Complete — scenarios 6.80–6.95 |
| 6n. `AuditLogSerializer` + `AuditLogMixinSerializer` surface (coverage-driven — May 12) | 15 | 15 | 15 | 0 | 0 |  Complete — scenarios 6.141–6.155 |
| 7. Calculation State Machine | 95 | 95 | 94 | 1 | 0 |  Complete (BUG-009 tracked; 7i 2-level matrix 7.32–7.47; 7j 3-level matrix 7.48–7.111) |
| 7g. `CalculatedModel.create()` pipeline (coverage-driven — April 21) | 6 | 6 | 6 | 0 | 0 |  Complete — drove `CalculatedModelMixin` 33.74% → 64.75% (`CombinatorialCalc` fixture added April 23) |
| 7h. `_dispatch_model_processing` Celery branch (coverage-driven — April 23) | 1 | 1 | 1 | 0 | 0 |  Complete — async branch 7g never reached; broker-free |
| 7n. Calculation cancellation — state machine + recursive cancel (Session 67 — June 1) | 8 | 8 | 8 | 0 | 0 | ✅ Complete — scenarios 7.166–7.173; pins `CalculationModel.cancel()` happy path + sync-not-cancellable + recursive descendant revoke (mocks `_revoke_celery_task`) |
| 7o. ForeignKey integrity violation abort semantics (Session 72 — June 2) | 1 | 1 | 1 | 0 | 0 | ✅ Complete — scenario 7.176; unhandled FK integrity errors abort `CalculatedModelMixin.create()` immediately (no silent continuation to later rows) |
| 8. Celery & Async | 6 | 6 | 6 | 0 | 0 |  Complete |
| 8g. Task infrastructure (planned — April 24) | 9 | 9 | 9 | 0 | 0 |  Complete — Redis-free (`celery_tasks.py`) |
| 8h. Celery **eager-mode** end-to-end (planned — April 24) | 6 | 6 | 6 | 0 | 0 |  Complete — 8.16–8.21; broker-free eager (`celery_tasks.py` 46%→55%, `CeleryTaskDispatcher` 0%→45%) |
| 8i. `WaitForTasks` / `FireAndForget` scope contracts (planned — April 24) | 9 | 9 | 9 | 0 | 0 |  Complete — 8.22–8.30 (priority/nesting/filters/no-op/propagation) |
| 8j. Celery task bodies — `load_data` / `calc_and_save` / `activate_history_version` (coverage-driven — April 24) | 14 | 14 | 14 | 0 | 0 |  Complete — task bodies at `celery_tasks.py` 696–957; broker-free |
| 8k. Redis broker integration examples (opt-in — May 4) | 2 | 2 | 2 | 0 | 0 |  Complete — 8.45/8.46 env-gated (`LEX_RUN_REDIS_CELERY_TESTS=true`); wired into `celery_redis_broker_example.yml` |
| 8m. Undecorated `CalculationModel` dispatched via generic `calc_and_save` (behaviour change — June 1) | 2 | 2 | 2 | 0 | 0 |  Complete — scenarios 8.49/8.50; every root calc now uses Celery when `CELERY_ACTIVE=true`, regardless of `@lex_shared_task` |
| 8u. Cancellation-aware `CallbackTask` failure mapping (Session 67 — June 1; extended Session 68 + Session 69) | 5 | 5 | 5 | 0 | 0 | ✅ Complete — scenarios 8.73–8.77; `_is_cancellation_exception` maps `TaskRevokedError` / `SoftTimeLimitExceeded` / `WorkerLostError` / `Terminated` / `CalculationCancelled` → `CANCELLED`; 8.77 pins `_update_model_status` audit-status mapping (SUCCESS → `success`, CANCELLED → `cancelled`, ERROR → `failure` — three distinct AuditLogStatus values so the compliance trail tells apart "completed OK", "operator cancelled", "crashed") |
| 8v. Cluster-wide cascade cancellation — Redis cancel index (Session 74 — June 5) | 12 | 12 | 12 | 0 | 0 | ✅ Complete — scenarios 8.78–8.89; best-effort Redis tree index (HASH keyed by `calculation_id`) written through at `ActiveCalculationStateStore.set_task_id`/`clear` so `cancel()` discovers + revokes the whole calc tree across KEDA pods, plus a `calculation_id` cooperative marker `calc_and_save` self-aborts on; all Redis ops degrade silently (no client / `CELERY_ACTIVE` off / `LEX_CLUSTER_CANCEL_ENABLED=false` → no-op). 8.87 drives real `cancel()` E2E (revokes a child registered only on another pod) |
| 9. Signals & WebSocket | 6 | 6 | 6 | 0 | 0 |  Complete |
| 9.7–9.10. Bitemporal signal branches (planned — April 21) | 4 | 4 | 4 | 0 | 0 |  Complete — suppression primitives in `bitemporal_signals.py` |
| 9d. `ActiveCalculationStateStore` full surface (coverage-driven — May 12) | 24 | 24 | 24 | 0 | 0 |  Complete — scenarios 9.11–9.28; coverage 27.03% → ~95% |
| 9e. Generic CRUD mutation broadcast — live list refresh (Session 73 — June 3) | 8 | 8 | 8 | 0 | 0 | ✅ Complete — scenarios 9.29–9.36; plain CRUD now emits a `model_data_update` `record_mutation` so open lists refresh without manual reload |
| 10. API Layer | 9 | 9 | 8 | 0 | 0 |  Complete (10.9 dup skipped; 10.8 retired — BUG-009 at 7.14) |
| 10e. Schema introspection (planned — April 21) | 4 | 4 | 4 | 0 | 0 |  Complete (BUG-015 documented in-test — CharField w/o default reports `required=False`) |
| 10f. Global search (planned — April 21) | 4 | 4 | 4 | 0 | 0 |  Complete — 10.15–10.16b |
| 10h. `LexAPI` outbound-client SDK (coverage-driven — May 12) | 7 | 7 | 7 | 0 | 0 |  Complete — scenarios 10.17–10.23; `requests` patched |
| 11. Stress & Performance | 20 | 20 | 15 | 2 | 0 |  Complete (3 skipped — 11.12/11.15/11.19 at SMALL; BUG-011 tracked) |
| 12. Serializer Contract | 32 | 32 | 29 | 3 | 0 |  Complete — 12a–12e (BUG-012/013 type round-trip xfail; BUG-017 cache xfail) |
| 12f. Serializer write paths — M2M & nested FK (planned — April 21) | 3 | 3 | 3 | 0 | 0 |  Complete — 12.29–12.31 (`TagItem`/`TaggableItem`/`EditScopedItem` fixtures added April 23) |
| 13. Export Endpoint | 12 | 12 | 12 | 0 | 0 |  Complete — 13a–13d (BUG-014 fixed) |
| 14. AG Grid Query Endpoint | 25 | 25 | 24 | 0 | 0 |  Complete — 14a–14e (BUG-016 deferred, 1 skip) |
| **Total** | **583** | **583** | **562** | **3** | **9** | Per-session narrative (what each session added, scenario renumberings, deferrals) lives in [`session-log.md`](session-log.md) — the single chronological record. |

**Status legend:**
-  Not started — no tests written yet
-  In progress — some scenarios implemented, not all passing
-  Complete — all scenarios implemented and passing (or marked as expected failure with tracked bug)
-  Blocked — depends on a framework fix before tests can pass

---

## What Counts as "Done" for a Cluster

The per-cluster Definition of Done (and what " In progress" means) lives in
**[conventions.md → Definition of Done](conventions.md#definition-of-done-per-cluster)**, the
single home for what the status states mean. The table above only records which state each cluster
is in.

---

## Known Bugs

The full bug tracker (BUG-NNN rows — severity, owning cluster, test, status) lives in **[`known-bugs.md`](../known-bugs.md)**, the single source of truth enforced by the PR-shape gate. Update it (not this file) when a test surfaces or resolves a framework bug.

---

> **Back to:** [Progress index](../progress.md) | **See also:** [Conventions](conventions.md) | [Session Log](session-log.md)
