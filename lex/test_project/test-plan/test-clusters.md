# Test Clusters

> **Back to:** [Test Plan Index](index.md) | **Progress:** [Progress & Organization](progress.md)  
> **Audience:** Engineering leadership, developers

---

## Ordering: The User Journey

Clusters are ordered by **how a customer first encounters the framework**, not by internal architecture. A new user:

1. Sets up a project and runs `lex setup` + `lex Init` → **Init — Project Bootstrap**
2. Creates, reads, updates, deletes records through the REST API → **CRUD via REST API**
3. Adds validation rules to protect data quality → **Validation Hooks**
4. Controls who can see and edit what → **Permissions**
5. Views the change history of records → **History & Bitemporal**
6. Checks audit logs for compliance → **Audit Logging**
7. Adds calculations that derive values from data → **Calculation State Machine**
8. Scales calculations with Celery → **Celery & Async**
9. Gets real-time updates in the UI → **Signals & WebSocket**
10. Builds integrations through the REST API → **API Layer**
11. Runs their real dataset through it all — and expects it to finish in reasonable time → **Stress & Performance**
12. Builds a frontend or integration that consumes the JSON — and expects the shape to be stable → **Serializer Contract**
13. Clicks "Export to Excel" from the AG Grid UI — with filters, grouping, row selection, and FKs — and expects the file to be correct → **Export Endpoint**
14. Scrolls, sorts, filters, groups and pivots in the AG Grid UI — and expects every query to return the right rows → **AG Grid Query Endpoint**

This ordering means: **if cluster N is broken, clusters N+1 through 10 are also likely broken.** We test foundations first.

---

## Testing Philosophy

> ### ⚠️ THE GOLDEN RULE
>
> **Test what the framework is _trying to achieve_, not what the current code happens to do.**
>
> The source code is an **incomplete story**. It has bugs, workarounds, and shortcuts. If we write tests that mirror the code, we lock in those bugs as "correct behavior" and the test suite becomes a shield for broken features instead of a detector of them.
>
> **How to find the intent:**
> 1. Read the docs in [docs/features/](../features/), [docs/reference/](../reference/), and [docs/tutorial/](../tutorial/)
> 2. Read the public API docstrings
> 3. Ask: _"What would a customer reasonably expect from this feature?"_
> 4. Write the test for **that** — even if the current code fails it
>
> **If a test fails because the code is buggy: good.** Mark it `@unittest.expectedFailure` with a reference in [progress.md — Known Bugs Tracker](progress.md#known-bugs-tracker). The failure is the test doing its job.
>
> **Never adjust a test to match broken behavior.** That is overfitting, and it is how we ended up with 2,000 green tests that missed real production bugs.

---

### The Rules

Every test in every cluster follows these rules:

1. **Test intent, not implementation** — derived from docs and reasonable customer expectations, not from reading the current `save()` / `calculate_hook()` source
2. **Use the same code path as a customer** — `instance.save()`, REST API calls, documented patterns — never `skip_hooks=True` + manual hook calls to work around bugs
3. **Mock only at true external boundaries** — Celery broker, WebSocket, Redis, S3, Keycloak HTTP
4. **Real Django models with real database tables** — no stubs, no fakes, no `_make_model_stub()`
5. **Assert on observable behavior** — final state in DB, history rows, API response codes and bodies. Never assert on mock call counts as a substitute for behavior.
6. **A failing test is valuable** — if it exposes a real bug, track it and keep it. Do not delete or weaken it.

### Red flags that mean a test is overfitting

If any of these are true, the test is probably wrong:

- ❌ Test sets up the exact internal state the implementation needs, then asserts that state survives
- ❌ Test mocks a method on the class under test
- ❌ Test mocks the ORM while testing ORM-dependent code
- ❌ Test asserts `mock.called_once_with(...)` but never checks the real effect
- ❌ Test passes even when the feature is known to be broken
- ❌ Test breaks when an internal helper is renamed, even though the feature still works
- ❌ Comment in test says _"work around framework bug"_ or _"use skip_hooks to avoid X"_

---

## 1. Init — Project Bootstrap

**What it tests:** The two CLI commands a brand-new user runs on day one:

1. **`lex setup`** — scaffolds `.env`, `.run/` (PyCharm configs), and `migrations/` in a fresh project directory
2. **`lex Init`** — applies migrations, syncs Django models to Keycloak (as Resources + Scopes), registers default roles and policies, and loads `INITIAL_DATA` if configured

If either command is broken, a new customer cannot start using the framework at all. This is the front door.

**Why first:** Every other cluster depends on a running database, a synced Keycloak realm, and loaded seed data. Without `lex setup` and `lex Init`, nothing else works.

**Fixtures needed:**
- A minimal test project directory (temp dir) with `lex_config.py`, `app.py`, and a sample model
- Mock Keycloak admin API (real HTTP boundary)
- Test JSON seed file referenced by `INITIAL_DATA`
- `SeedableItem` — simple `LexModel` used as the target of seed data

### 1a. `lex setup` — scaffolding

| # | Scenario | What We Assert |
|---|----------|----------------|
| 1.1 | Fresh directory | `.env`, `.run/`, `migrations/__init__.py` created |
| 1.2 | Existing `.env` preserved | Re-running `setup` does not overwrite user's `.env` |
| 1.3 | `.run/` configs regenerated | PyCharm run configurations (`Init`, `Start`, `Streamlit`) written with correct paths |
| 1.4 | Missing project root | Clear error message, no partial scaffolding |
| 1.5 | `find_project_root` resolves correctly | Walks up to find `lex_config.py` or uses cwd |

### 1b. `lex Init` — first-run initialization

| # | Scenario | What We Assert |
|---|----------|----------------|
| 1.6 | First run on empty DB | Migrations applied, tables created for all project models |
| 1.7 | Second run (idempotent) | No-op for migrations, Keycloak sync shows no drift, no errors |
| 1.8 | Adds a new model, runs again | New table created, new Keycloak resource registered with default scopes (`list`, `read`, `create`, `edit`, `delete`, `export`) |
| 1.9 | Renames a model, runs again | Keycloak resource renamed (not duplicated), old name removed |
| 1.10 | Deletes a model, runs again | Keycloak resource removed, excluded from sync list |
| 1.11 | Default roles registered | `admin`, `standard`, `view-only` roles created in Keycloak |
| 1.12 | Default scope → policy mapping | `create`/`delete` → admin only; `read`/`list` → all roles; `edit`/`export` → admin + standard |
| 1.13 | Keycloak unavailable (timeout) | Non-fatal error, clear message, local state consistent |
| 1.14 | Missing Keycloak env vars | Fails fast with actionable error naming the missing variable |
| 1.15 | Keycloak state file updated | `.keycloak_state.json` reflects current synced state |
| 1.16 | Excluded apps skipped | `legacy_data`, historical/metahistorical models, `AuditLog` not synced to Keycloak |

### 1c. `INITIAL_DATA` loading (part of `lex Init`)

| # | Scenario | What We Assert |
|---|----------|----------------|
| 1.17 | Seed data loads on empty database | Records created with correct field values |
| 1.18 | Seed data skips when data already exists | No duplicates, no errors, existing data untouched |
| 1.19 | Invalid seed data format | Clear error message, no partial load |
| 1.20 | Seed data with foreign key references | Related records resolved correctly |
| 1.21 | `lex_config.py` parses | `INITIAL_DATA` path and `PROJECT_GROUPS` read correctly |
| 1.22 | Missing seed file | Skipped gracefully with a log message, `Init` still succeeds |

---

## 2. CRUD via REST API

**What it tests:** The full CRUD lifecycle **as a customer performs it** — through authenticated HTTP requests to the REST API. This is how the frontend, integrations, and API clients actually talk to the framework.

**Why second:** After setup, the first thing a customer does is open the UI or hit the API to create/edit/delete records. If CRUD over HTTP is broken, the product is unusable even if the ORM works.

**Why API-first (not ORM-first):** Customers never call `instance.save()` directly. They click a button in the UI → frontend sends a PATCH → the framework routes it through the serializer, permission layer, hooks, and audit system. Testing the ORM in isolation misses every bug in that chain. We test the chain.

**Models needed:**
- `SimpleItem` — plain `LexModel` (name, value, description)
- `TrackedItem` — `LexModel` used to verify `created_by`/`edited_by` actor resolution over API

**Test setup:** All tests use `APIClient` with a force-logged-in user and the `process_admin_rest_api` URL namespace (same path the frontend uses).

### 2a. Create (POST)

| # | Scenario | What We Assert |
|---|----------|----------------|
| 2.1 | POST creates a record (**CI showcase test**) | Response 201, body includes `id`, record in DB. This is the CRUD showcase scenario driven by `.github/workflows/showcase_tests.yml`. |
| 2.2 | POST sets framework-managed fields | `created_at`, `edited_at`, `created_by` = authenticated user's email |
| 2.3 | POST with missing required field | Response 400, error names the missing field, no record created |
| 2.4 | POST with invalid field type | Response 400, no record created |
| 2.5 | POST with extra unknown fields | Unknown fields ignored, record created with known fields only |
| 2.6 | POST while unauthenticated | Response 401 or redirect, no record created |
| 2.7 | POST via API key | `created_by` = `"Technical User"` |

### 2b. Read (GET)

| # | Scenario | What We Assert |
|---|----------|----------------|
| 2.8 | GET detail returns the record | Response 200, all readable fields present with correct values |
| 2.9 | GET detail for non-existent id | Response 404 |
| 2.10 | GET list returns all records | Response 200, count matches DB |
| 2.11 | GET list respects pagination | `results` and count/next/previous keys present when paginated |
| 2.12 | GET while unauthenticated | Response 401 or redirect |

### 2c. Update (PATCH / PUT)

| # | Scenario | What We Assert |
|---|----------|----------------|
| 2.13 | PATCH updates only specified fields | Response 200, changed field updated, other fields untouched |
| 2.14 | PATCH updates `edited_at` and `edited_by` | `edited_at` changes, `edited_by` = authenticated user, `created_at`/`created_by` unchanged |
| 2.15 | PATCH with invalid value | Response 400, record in DB unchanged |
| 2.16 | PUT replaces the record | Response 200, all fields match request body |
| 2.17 | PATCH non-existent id | Response 404 |
| 2.18 | PATCH while unauthenticated | Response 401 or redirect, record unchanged |

### 2d. Delete (DELETE)

| # | Scenario | What We Assert |
|---|----------|----------------|
| 2.19 | DELETE removes the record | Response 204 (or 200), record gone from DB |
| 2.20 | DELETE non-existent id | Response 404 |
| 2.21 | DELETE while unauthenticated | Response 401, record still in DB |
| 2.22 | DELETE then GET returns 404 | Confirms deletion propagated through the read path |

### 2e. Bulk operations (Many endpoint)

| # | Scenario | What We Assert |
|---|----------|----------------|
| 2.23 | POST to `many/` creates multiple records | Response 201, all records in DB with correct values |
| 2.24 | PATCH to `many/` updates multiple records | Response 200, each record's changes applied |
| 2.25 | Bulk create with one invalid record | Behavior matches documented contract (all-or-nothing vs partial) |

---

## 3. Validation Hooks

**What it tests:** `pre_validation()` (cancel save before it happens) and `post_validation()` (rollback after save if validation fails). This is the first layer of data quality a customer adds.

**Why third:** Once a customer is creating records, the next question is "how do I prevent bad data?" Validation hooks are the answer.

**Models needed:**
- `PreValidatedItem` — raises exception in `pre_validation()` for specific values
- `PostValidatedItem` — raises exception in `post_validation()` for specific values
- `HookOrderItem` — records hook execution order in a class-level list

**Test scenarios:**

| # | Scenario | What We Assert |
|---|----------|----------------|
| 3.1 | `pre_validation` passes | Record saved normally |
| 3.2 | `pre_validation` raises exception | Save cancelled, no DB change, no history row |
| 3.3 | `post_validation` passes | Record saved, history created |
| 3.4 | `post_validation` raises exception | Record rolled back to pre-save state, error raised |
| 3.5 | Hook execution order on create | BEFORE_CREATE → BEFORE_SAVE → (save) → AFTER_SAVE → AFTER_CREATE |
| 3.6 | Hook execution order on update | BEFORE_UPDATE → BEFORE_SAVE → (save) → AFTER_SAVE → AFTER_UPDATE |
| 3.7 | Validation recursion guard | `_validation_in_progress` prevents infinite recursion |
| 3.8 | Rollback restores field values | After `post_validation` failure, DB record matches pre-save snapshot |

---

## 4. Permissions

**What it tests:** Field-level (`permission_read/edit/export` → `PermissionResult`) and action-level (`permission_create/delete/list` → `bool`) access control. This is how customers protect sensitive data.

**Why fourth:** After basic CRUD and validation, the customer asks "who can see what?" Permissions control the answer.

**Models needed:**
- `ProtectedItem` — `LexModel` with custom `permission_read/edit/delete` overrides
- `FieldLevelItem` — `LexModel` with `allow_fields()` / `allow_all_except()` patterns
- `KeycloakItem` — `LexModel` using default Keycloak scope-based permissions

**Test scenarios:**

| # | Scenario | What We Assert |
|---|----------|----------------|
| 4.1 | Superuser reads all fields | `permission_read` returns `allow_all` |
| 4.2 | Regular user reads allowed fields only | API response only includes permitted field names |
| 4.3 | `allow_all_except` hides sensitive fields | Excluded fields absent from API response |
| 4.4 | `permission_edit` restricts editable fields | PATCH to restricted field is rejected or ignored |
| 4.5 | `permission_delete` denies deletion | DELETE returns 403 |
| 4.6 | `permission_create` denies creation | POST returns 403 |
| 4.7 | Keycloak scope fallback — read scope present | Default `permission_read` allows all fields |
| 4.8 | Keycloak scope fallback — no scopes | Default `permission_read` denies |
| 4.9 | Legacy `can_read()` compatibility | `can_read(request)` returns same fields as `permission_read` |
| 4.10 | `UserContext.from_request` builds correct context | Groups, scopes, roles, email correctly populated |
| 4.11 | API key context | `client_roles` includes "api_key", scopes from key identity |
| 4.12 | `with_instance` resolves instance-specific Keycloak scopes | Scopes matched by `rsname` and `resource_set_id` |

---

## 5. History & Bitemporal

**What it tests:** That every `.save()` on a `LexModel` creates a history row, that `valid_from`/`valid_to` chain correctly, and that the MetaHistory layer works.

**Why fifth:** Customers start looking at history after they've been editing records for a while. "What changed and when?" is a compliance requirement.

**Models needed:**
- `SimpleItem` (reused from Cluster 2)
- `AtomicCalc`, `NonAtomicCalc` (from Cluster 7)

**Test scenarios:**

| # | Scenario | What We Assert |
|---|----------|----------------|
| 5.1 | Create a record | Exactly 1 history row, type = "+" (created) |
| 5.2 | Update a record | New history row, type = "~" (changed), `valid_from` = `edited_at` |
| 5.3 | Delete a record | New history row, type = "-" (deleted) |
| 5.4 | Multiple updates | History rows ordered by `history_date`, `valid_to` of row N = `valid_from` of row N+1 |
| 5.5 | `skip_history_when_saving` | No history row created for that save |
| 5.6 | Calculation success history | History shows NOT_CALCULATED → IN_PROGRESS → SUCCESS (3 rows) |
| 5.7 | Calculation failure history | History shows NOT_CALCULATED → IN_PROGRESS → ERROR (3 rows) |
| 5.8 | History survives failed atomic calculation | IN_PROGRESS history row is NOT rolled back (**known bug — expected failure**) |
| 5.9 | History via API (`/history/` endpoint) | Returns correct history rows with field values at each point in time |
| 5.10 | Concurrent edits produce distinct history rows | Two rapid saves → two distinct history entries |

---

## 6. Audit Logging

**What it tests:** The `AuditLogMixin` records every API create/update/delete with correct actor, action, payload, and status. Also tests calculation audit finalization.

**Why sixth:** Audit logs are the compliance backbone. Customers in regulated industries (finance, healthcare) need proof of every action.

**Models needed:**
- `SimpleItem` (reused)
- `AtomicCalc` (from Cluster 7)

**Test scenarios:**

| # | Scenario | What We Assert |
|---|----------|----------------|
| 6.1 | API create produces audit log | Audit log with action=create, correct actor, status=success |
| 6.2 | API update produces audit log | Audit log with action=update, payload includes changed fields |
| 6.3 | API delete produces audit log | Audit log with action=delete |
| 6.4 | Failed API operation | Audit log with status=failure |
| 6.5 | Calculation audit — success | Terminal audit log with audit_status=success |
| 6.6 | Calculation audit — failure | Terminal audit log with audit_status=failure, includes error message |
| 6.7 | Actor resolution — authenticated user | `created_by`/`edited_by` = user email or username |
| 6.8 | Actor resolution — API key | `created_by`/`edited_by` = "Technical User" |
| 6.9 | Actor resolution — no context | `created_by`/`edited_by` = "Initial Data Upload" (fallback) |
| 6.10 | Audit log survives calculation failure | `_finalize_pending_terminal_audit` runs even when `save()` atomic block rolls back |

---

## 7. Calculation State Machine

**What it tests:** The `CalculationModel` state machine — transitions between NOT_CALCULATED, IN_PROGRESS, SUCCESS, ERROR, ABORTED — across atomic and non-atomic models, and parent→child calculation hierarchies.

**Why seventh:** Calculations are the core value proposition of the framework, but they depend on CRUD, validation, history, and audit all working. We test them after the foundations are solid.

**Models needed:**
- `AtomicCalc` — `CalculationModel` (default `is_atomic=True`), configurable success/failure
- `NonAtomicCalc` — `CalculationModel` with `is_atomic = False`, configurable success/failure
- `ParentCalc` — triggers `ChildCalc` from its `calculate()` method
- `ChildCalc` — triggered by parent, configurable success/failure
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

---

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

---

## 9. Signals & WebSocket

**What it tests:** `ActiveCalculationStateStore` tracking, `WebSocketNotifier` broadcasts, `CacheManager` cleanup, and `update_calculation_status` signal.

**Why ninth:** Real-time UI updates are the last user-facing layer. If signals are broken, the UI shows stale data but the backend still works.

**Models needed:**
- `AtomicCalc` (reused)
- `ParentCalc`, `ChildCalc` (reused)

**Test scenarios:**

| # | Scenario | What We Assert |
|---|----------|----------------|
| 9.1 | `mark_in_progress` registers record in state store | Record retrievable by `get_calculation_id` |
| 9.2 | Calculation completion cleans up state store | Record removed after SUCCESS/ERROR |
| 9.3 | WebSocket notification sent on state change | `send_calculation_update` called with correct model/state |
| 9.4 | Root process cleans up cache | `CacheManager.cleanup_calculation` called for root |
| 9.5 | Child process skips cache cleanup | Cleanup NOT called for child process |
| 9.6 | `update_calculation_status` called with error details on failure | Exception details and stack trace included |

---

## 10. API Layer

**What it tests:** REST API endpoints — One (create/read/update/delete), List, Many, History — including serializer behavior, filter backends, and the API-specific calculation trigger path.

**Why last:** The API is the external interface. It wraps everything above (CRUD, permissions, history, calculations) into HTTP endpoints. If the layers below work, the API layer is mostly wiring.

**Models needed:**
- `SimpleItem` (reused)
- `AtomicCalc` (reused)

**Test scenarios:**

| # | Scenario | What We Assert |
|---|----------|----------------|
| 10.1 | POST creates record | 201, record in DB, response includes `id` |
| 10.2 | GET retrieves record | 200, correct field values |
| 10.3 | PATCH updates fields | 200, only specified fields changed |
| 10.4 | DELETE removes record | 204/200, record gone |
| 10.5 | GET list returns all records | 200, correct count |
| 10.6 | GET history returns history rows | 200, ordered by `history_date` |
| 10.7 | Many endpoint (bulk operations) | Correct batch handling |
| 10.8 | API triggers calculation (PATCH `is_calculated=IN_PROGRESS`) | IN_PROGRESS committed independently, hooks fire, final state correct |
| 10.9 | Invalid data returns 400 | Validation errors in response |
| 10.10 | Unauthenticated request handled | 401 or redirect |

---

## 11. Stress & Performance

**What it tests:** How the framework behaves **at volume** — not with 3 seeded rows, but with realistic production-scale data (~20k rows in key tables, with foreign-key fan-out). Everything in clusters 1–10 is correctness; this cluster is **scalability and regression detection on execution time**.

**Why it matters:** every customer-visible bug we've hit on staging in the last 18 months has shown up at scale first — the 500-row demo dataset passes, the real dataset of 20k invoices stalls or times out. Correctness tests will never catch:

- a save path that drifts from `O(n)` to `O(n²)` because someone added a `.objects.get()` inside a loop;
- an export that fits in memory at 500 rows and crashes the worker at 20k;
- a period calculation whose dependency graph causes re-fetches of the same rows 100 times;
- a bulk API call whose serializer runs one query per row (the classic N+1 on write).

This cluster runs rarely (nightly + release gate, not on every PR) and asserts on **time budgets** and **query counts**, not just final state.

**Why last:** needs everything else to work first. You can't stress-test a broken save or a broken calculation.

**Data volume conventions:**

| Label | Rows | Purpose |
|-------|------|---------|
| `SMALL` | 500 | Smoke — runs on every PR, catches gross regressions fast |
| `MEDIUM` | 5 000 | Nightly — catches algorithmic drift early |
| `LARGE` | 20 000 | Release gate — proof of production readiness |

Each test declares the volume it targets; CI decides which to run via a pytest/Django test tag.

**Models needed** (dedicated to this cluster — do NOT reuse the lightweight cluster 2/5 models, stress data pollutes them):

- `StressInvoice` — `LexModel` with ~15 fields (mix of `CharField`, `DecimalField`, `DateField`, one `ForeignKey` to `StressCounterparty`). This is the "wide row" under test — mirrors the shape of the real customer invoice table.
- `StressCounterparty` — ~200 rows, referenced by `StressInvoice.counterparty`. Provides the FK fan-out so export joins are realistic.
- `StressPeriod` — `LexModel` with a `valid_from` / `valid_to` pair. 20k rows spread across rolling 12-month windows; the period-calc test walks these.
- `PeriodAggregateCalc` — `CalculationModel` whose `calculate()` aggregates all `StressInvoice` rows inside a `StressPeriod` and writes a derived total. This is the work unit for the period-calculation scenarios.
- `DependentPeriodCalc` — `CalculationModel` that depends on `PeriodAggregateCalc` outputs for the previous 3 periods, so the dependency graph has real depth.

**Test harness** (will live in `lex/test_project/tests/stress/`):

- **`StressTestCase` base class** extending `E2ETestCase`, adding:
  - `bulk_seed(model, n, batch_size=1000)` — uses `bulk_create` with `ignore_conflicts=False` and chunked batches so we don't blow memory seeding 20k rows.
  - `assert_runtime_under(seconds, label)` — wraps `time.perf_counter()` and fails with a clear `"<label>: took X.XXs, budget Y.YYs"` message.
  - `assert_query_count_at_most(n, label)` — uses `CaptureQueriesContext` so we catch N+1 regressions with a concrete upper bound.
  - `measure(label)` — context manager that records `(duration, query_count, peak_memory_mb)` into a per-run JSON report written to `test_report/stress/<run-id>.json`. Budgets are asserted inside the CM; the JSON is for trend analysis.
- **Seed once, reuse across tests.** Stress data is seeded in `setUpClass`, not `setUp`, and wrapped in `TransactionTestCase` with `available_apps` discipline so the 20k rows survive per-test cleanup. The class tears everything down at the end.
- **Deterministic data.** Seeder uses a fixed `random.seed(42)` so runtime trends across CI runs reflect code changes, not input variance.

**Test scenarios:**

| # | Scenario | Volume | What We Measure / Assert |
|---|----------|--------|--------------------------|
| 11.1 | `bulk_create` seed — baseline insertion | LARGE (20k) | **Time < 30s**, no per-row save signals firing, history rows NOT written (documented bulk-path behaviour) |
| 11.2 | ORM `.save()` loop — single-row insert at scale | MEDIUM (5k) | Time budget asserts linear scaling; query count ≤ `2×n` (insert + history insert), catches signal-handler drift |
| 11.3 | List endpoint paginated read | LARGE | **p95 < 500ms per page of 100**, query count ≤ 4 per page (select + count + 2 FK prefetch), no N+1 on `counterparty` join |
| 11.4 | List endpoint filter + sort | LARGE | Filter returns correct subset; time < 1s; confirms the indexed path is used (`EXPLAIN` assertion — query plan contains `Index Scan`, not `Seq Scan`) |
| 11.5 | Full-table export (`export_url` / `/many/` GET) | LARGE | **Time < 15s end-to-end**, streaming response (not a memory-resident list), peak memory < 200MB, exported row count == 20k exactly |
| 11.6 | Excel / CSV writer loop | LARGE | Writer doesn't do per-row DB hits; one query to read, one pass to write; catches the "iterate queryset without `.iterator()`" regression |
| 11.7 | `PeriodAggregateCalc` over a single period | LARGE invoices in one period | Time < 5s; query count < 10 regardless of invoice count (one aggregate query, not one per invoice) |
| 11.8 | `PeriodAggregateCalc` over **all 12 periods** | LARGE across 12 periods | **Time < 60s total**, confirms no cross-period re-fetch, history rows correct for every period |
| 11.9 | `DependentPeriodCalc` — 3-period dependency chain × 12 periods | LARGE | Dependency resolution does each underlying period once, not once per dependent. Total time < 90s; query count grows `O(periods)`, not `O(periods²)` |
| 11.10 | Bulk API POST (`POST /many/`) at volume | MEDIUM (5k in one call) | Single serializer compile; single `bulk_create`; time < 10s; exactly 5k rows persisted — catches per-row `save()` regressions in the bulk path. (Depends on BUG-006 fix — until then marked `@expectedFailure`.) |
| 11.11 | Bulk PATCH over filtered subset | MEDIUM | Time < 5s; exactly 1 UPDATE query (not n); history rows written correctly |
| 11.12 | Concurrent writes — 10 parallel clients each patching its own subset | MEDIUM | No deadlocks, no lost updates; audit rows from all 10 clients present; `created_by` correct per row |
| 11.13 | History-table growth — 20k updates to one row | LARGE (1 row, 20k revisions) | History query paginates in < 500ms at p95; `history_date` index actually used (`EXPLAIN` check); no blow-up on `.history.all()` when the queryset is consumed lazily |
| 11.14 | Permission filtering at scale | LARGE | `permission_read` invoked **once per list request**, not once per row; list endpoint time < 1s even with a custom `permission_read` that does work |
| 11.15 | Audit-log write throughput | LARGE | 20k API writes produce 20k audit rows in < 60s total; no audit-log writer contention (bounded connection count) |

### FK fan-out sub-cluster (11.16 – 11.20)

A dedicated dataset — ``FKHeavyInvoice`` with **four FK relationships**
(``counterparty``, ``period``, ``category``, ``currency``) — at **25 000
rows** proves the framework's behaviour under the classic production
workload: "a wide row with lots of joins, multiplied by a year of data".
Four joins is the threshold where the naive ORM path (one lazy SELECT
per FK attribute access) goes from annoying to catastrophic —
25 000 × 4 = 100 000 round trips on a well-intentioned-but-wrong loop.

Every scenario in this sub-cluster targets the customer-facing
regression: "Export last year's data takes 5 minutes and 2 GB of RAM".

| # | Scenario | Volume | What We Measure / Assert |
|---|----------|--------|--------------------------|
| 11.16 | Full REST ``list`` export of 25k × 4-FK rows | 25k default (2.5k on SMALL) | Exact row count, all 4 FKs present in serialized body, tier time budget. Generous budget until BUG-011 is fixed |
| 11.17 | Documented CSV export — ``select_related`` + ``.iterator()`` over 4 FKs | 25k | **Query count ≤ 8** regardless of row count — 1 main SELECT with four JOINs + session/auth noise |
| 11.18 | Anti-baseline: naive iteration, no ``select_related``, no ``.iterator()`` | 2 000 rows scanned | Query count is **at least** ``2.5 × rows`` (clear N+1 signal). Records the delta vs 11.17 in the trend report — a 1-query-vs-8001-query gap. If a future optimisation makes this bounded, the test fails and we update the reference docs |
| 11.19 | ``.iterator()`` vs materialised ``list()`` peak memory | 25k | ``tracemalloc``-measured iterator peak ≤ 50% of list peak (typically 1/10). Skipped at SMALL — delta too small to distinguish reliably |
| 11.20 | Aggregated JOIN — ``Sum(invoices__amount_net)`` grouped by counterparty | 25k | Exactly 1 SELECT (+ ≤ 4 session noise). Aggregate sum matches the raw table aggregate — proves the JOIN covers every row and the GROUP BY doesn't drop partitions |

Observed on first run (SMALL, 2.5k rows):

* **11.17**: 1 query for the entire export (documented path).
* **11.18**: 8 001 queries for the same work done naively — a clear **8 000× query-count delta** between the documented and naive paths. That is the cost the team now has a regression gate on.
* **11.20**: 1 query for the full grouped aggregate over the FK join.

**Budgets are hard gates.** A scenario that goes 10% over budget is a failure, not a warning. Budgets are tuned once on the CI runner baseline (recorded in `test_report/stress/baseline.json`) and tightened — never loosened — with each release. If a scenario legitimately needs more time (new feature, new work in the hot path), the PR must update the baseline **in the same commit** that adds the work, so the change is reviewable.

**What is explicitly NOT tested here:**

- ❌ Correctness beyond what the smaller clusters already cover. If `PeriodAggregateCalc` gets the wrong answer, cluster 7 should catch it at 10 rows. Cluster 11 is only about **how long** and **how many queries**.
- ❌ Network latency / cross-region behaviour. This cluster runs on a single CI runner against a local Postgres.
- ❌ Frontend performance. Separate concern, different toolchain.

**Reporting:**

Every stress run writes `test_report/stress/<timestamp>.json` with per-scenario `(duration, query_count, peak_memory_mb)`. A nightly CI job appends the latest run to a rolling `test_report/stress/trend.jsonl` and fails the job if any scenario's **90-day moving average** exceeds its budget by 5%. This gives us drift detection that a single-run budget check misses.

---

## 12. Serializer Contract

**What it tests:** the **JSON shape** the REST API hands the frontend
and external integrations. Cluster 2 tests that HTTP verbs work;
cluster 4 tests that permission predicates fire; this cluster tests
the **payload the customer actually sees and sends**. One bad
serializer change (decimal precision lost, datetime stripped of
timezone, `lex_reserved_scopes` key removed, FK rendered as id
instead of `{id: ...}`) silently breaks the entire UI — without
tripping any existing cluster.

**Why it matters:** the `LexSerializer` / `RestApiModelSerializer`
layer is the single translation boundary between the Django ORM and
the outside world. It owns field visibility (`permission_read`
filtering), type round-tripping (Decimal / DateTime / Date / FK),
framework-managed fields (`id_field`, `short_description`,
`lex_reserved_scopes`), and the history/meta-history unwrap. Every
one of those is a customer-visible contract that today has no
dedicated coverage — the existing serializer unit tests
(`lex/tests/unit/serialization/`) exercise helpers in isolation, not
the end-to-end JSON contract.

**Why cluster 12:** everything it depends on (CRUD over HTTP,
permission predicates, history rows) is already green in clusters
2 / 4 / 5. The serializer is the thin translation layer on top.

**Models needed** (dedicated to this cluster — the existing
cluster-2 models are too shallow; we need a model with one of every
"interesting" field type):

- `WideItem` — `LexModel` carrying one field per type:
  `DecimalField(max_digits=12, decimal_places=4)`, `DateTimeField`,
  `DateField`, `TimeField`, `UUIDField`, `TextField`,
  `CharField(choices=...)`, `JSONField`, `BooleanField`, and
  `ForeignKey` to `RelatedItem`.
- `RelatedItem` — tiny FK target (`name`, `code`).
- `ProtectedWideItem` — same shape as `WideItem` but with a
  non-trivial `permission_read` that returns
  `PermissionResult.allow_fields({"name", "amount"})` for non-admin.
  Proves field-level filtering survives round-trip.

### 12a. Read contract — field visibility & framework-managed fields

| # | Scenario | What We Assert |
|---|----------|----------------|
| 12.1 | GET detail returns all framework-managed keys | Response JSON contains `id`, `id_field`, `short_description`, `lex_reserved_scopes` alongside every model field |
| 12.2 | `short_description` == `str(instance)` | The custom `__str__` of `WideItem` is what the UI receives — no stale cache, no default `Model object (1)` |
| 12.3 | `lex_reserved_scopes` shape | Keys are exactly `{"edit", "delete", "export"}`; `edit` is a **sorted list of field names**; `delete` / `export` are `bool` |
| 12.4 | `permission_read` → `allow_fields({"a", "b"})` | GET detail response contains ONLY `a`, `b`, and the framework-managed keys — every other model field is stripped |
| 12.5 | `permission_read` denies entirely | List endpoint omits the record completely (`FilteredListSerializer` drops empty dicts); detail endpoint returns `{}` or 404-equivalent |
| 12.6 | History-row GET unwraps to main model | Field visibility on a history row matches the main model's `permission_read` |
| 12.7 | MetaHistory scopes are fixed | `lex_reserved_scopes` on a MetaHistorical instance is `{"edit": [], "delete": False, "export": False}` regardless of caller |
| 12.8 | `lex_reserved_scopes.edit` reflects `permission_edit` | When `permission_edit` returns `allow_fields({"x"})`, `edit == ["x"]` — no more, no less |

### 12b. Type round-trip — what goes in comes back out

| # | Scenario | What We Assert |
|---|----------|----------------|
| 12.9 | `DecimalField` preserves precision | POST `"1234.5678"` → GET returns `"1234.5678"` (string, not float). No silent truncation to 2 dp |
| 12.10 | `DateTimeField` round-trip keeps timezone | POST a UTC ISO-8601 string → GET returns a tz-aware ISO-8601 string with the same instant |
| 12.11 | `DateField` uses `YYYY-MM-DD` | POST `"2026-04-21"` → GET returns `"2026-04-21"` — not a datetime, not a Unix timestamp |
| 12.12 | `UUIDField` is a string | POST/GET value is an RFC 4122 string, not a Python `UUID` repr |
| 12.13 | Nullable `ForeignKey` unset | GET returns `null` (not `0`, not `""`, not a stub dict) |
| 12.14 | `ForeignKey` set | GET returns either the FK id or a `{"id": ..., "short_description": ...}` dict per documented contract (whichever the framework's chosen shape is — test locks it in) |
| 12.15 | PATCH accepts FK as `{"id": X}` dict | `_parse_value_for_field` extracts the id; FK resolves to the target row |
| 12.16 | PATCH rejects invalid `choices` | Response 400 with field-level error; DB unchanged |
| 12.17 | `TextField` preserves unicode & newlines | Multi-line unicode string survives POST → GET byte-for-byte |
| 12.18 | `JSONField` preserves structure | Nested dict/list round-trips with key order preserved (for dicts) / element order preserved (for lists) |
| 12.19 | Unknown field in PATCH payload ignored | Response 200, unknown key silently dropped, known fields applied (mirrors 2.5 but asserts at serializer level) |

### 12c. List & Many contract

| # | Scenario | What We Assert |
|---|----------|----------------|
| 12.20 | `FilteredListSerializer` drops records that serialize to `{}` | List with 3 rows, one denied by `permission_read`, returns 2 rows — not 3 with one empty dict |
| 12.21 | List response row shape matches detail | Every row in a list response has the same framework-managed keys as the detail endpoint |
| 12.22 | `/many/` POST field validation | Mirrors 12.16 for bulk: first invalid record's error names the field; contract (all-or-nothing vs partial) matches 2.25 |

### 12d. AuditLog payload filtering

| # | Scenario | What We Assert |
|---|----------|----------------|
| 12.23 | AuditLog payload with FK the caller cannot read | That FK key is stripped from `payload`; rest of payload survives |
| 12.24 | AuditLog payload: unreadable fields pruned from `updates` | `payload.updates` contains only fields the caller is permitted to read on the target model |
| 12.25 | AuditLog payload when target model denies entirely | `payload` becomes `{}` (or only the pinned `id` / `short_description` keys) |

### 12e. Serializer factory contract

| # | Scenario | What We Assert |
|---|----------|----------------|
| 12.26 | `model2serializer` always injects internal fields | Every auto-generated serializer has `id_field`, `short_description`, `lex_reserved_scopes` in its `Meta.fields` |
| 12.27 | `_wrap_custom_serializer` preserves user fields + adds internals | A model's `api_serializers` entry keeps its declared `Meta.fields` AND gets the framework internals appended |
| 12.28 | Serializer is cached per model | Two calls to `get_serializer_map_for_model` return the same class object (not rebuilt per request) |

**What is explicitly NOT tested here:**

- ❌ **DB-side correctness.** If `DecimalField` precision is wrong in Postgres, that's a model-layer issue, not the serializer. Cluster 2 covers the DB round-trip.
- ❌ **Permission logic.** Cluster 4 owns `permission_read` / `permission_edit` semantics. Here we only assert the serializer *honors* the result.
- ❌ **History chaining.** Cluster 5. Here we only assert a history row, when serialized, produces the right JSON shape.

---

## 13. Export Endpoint

**What it tests:** the real ``POST /api/<model>/export`` endpoint —
the one the AG Grid UI hits when a customer clicks *Export to
Excel*. Cluster 11 benchmarks the ORM-level export *pattern* (one
SQL with ``select_related`` + ``.iterator()``); this cluster tests
the **HTTP endpoint** that wraps it, plus all 17 helper methods
inside :class:`ModelExportView` that translate an AG Grid payload
into an ``.xlsx`` file.

**Why it matters:** export is the single most user-visible feature
of the framework that has **zero E2E coverage** today. The existing
tests in ``lex/tests/unit/grid/`` call a few utility methods in
isolation; they don't drive the ``post()`` entry point and they
never exercise the grouped / selection / FK-display paths. That's
how we ended up with 17 methods that no test touches even though
we have a cluster named *FK-heavy export*.

**Why last:** every feature it depends on (CRUD, permissions,
history, FK relations, AG Grid filter/sort pipeline) must already
be green. The export endpoint is the wide-surface integration
point that wires them all together into a single binary artefact.

**Design principle:** tests are **scenario-driven, not method-
driven**. A single "AG grouped export with FK display names"
scenario fires through ``post`` → ``_normalize_ag_request`` →
``_build_ag_grid_dataframe`` → ``_collect_ag_export_rows`` →
``_apply_export_mask_to_ag_rows`` → ``_refresh_hierarchy_labels_
with_readable_values`` → ``_apply_foreign_key_display_names`` →
``_apply_ag_column_layout`` in one shot. We assert on the
**returned .xlsx file contents** using ``pandas.read_excel``, not
on internal state.

**Models needed** (dedicated — same rule-#3 discipline as the other
clusters):

- ``ExportCategory`` — FK target. ``__str__`` returns
  ``f"Cat<{name}>"`` so FK-display-name assertions have a clear,
  non-default expected value.
- ``ExportItem`` — ``LexModel`` with ``name`` / ``amount``
  (Decimal) / ``status`` (choice) / ``category`` (FK). Default
  ``permission_export`` so the uniform fast path fires.
- ``ExportMaskedItem`` — same shape, but ``permission_export``
  returns ``allow_fields({"id", "name"})`` for non-admins. Used to
  lock in the field-level export mask contract.

### 13a. Legacy (non-AG) export path

| # | Scenario | What We Assert |
|---|----------|----------------|
| 13.1 | Empty queryset → 404 | Body ``{"error": "No data available for export"}``; no ``.xlsx`` bytes |
| 13.2 | 5 rows, default permissions, with FK | HTTP 200 + ``.xlsx`` body; row count matches DB; FK column shows ``str(category)`` (**``Cat<...>``**), not the integer pk |
| 13.3 | ``filtered_export`` base64-encoded id list | Only the selected ids are in the exported sheet (legacy path routes through ``PrimaryKeyListFilterBackend.filter_for_export``) |
| 13.4 | ``permission_export`` restricts fields | Restricted columns are present in the sheet but blank; allowed columns carry values |

### 13b. AG Grid export path — flat

| # | Scenario | What We Assert |
|---|----------|----------------|
| 13.5 | AG flat with ``columns`` payload | Fast path fires (``_try_build_flat_fast_export_dataframe`` returns a non-None df); exported columns are in the requested order with ``headerName`` applied; FK column shows readable name |
| 13.6 | AG ``columns`` including ``short_description`` | Computed field silently skipped by ``_resolve_export_field_paths``; fast path still succeeds; other requested columns still in the sheet |
| 13.7 | AG ``endRow`` over ``MAX_AG_EXPORT_ROWS`` | Clamped silently; export does not raise |

### 13c. AG Grid export path — grouped & selected

| # | Scenario | What We Assert |
|---|----------|----------------|
| 13.8 | AG ``rowGroupCols`` with 2 levels | Sheet contains group-header rows with indented ``__ag_group_hierarchy_label`` and a recorded group depth (Excel outline level); ``_collect_ag_export_rows`` recursion visits both levels |
| 13.9 | AG ``selection.groupKeyPaths`` filter | Only rows matching the selected group key are in the sheet; ``_coerce_group_key`` converts ``"1"`` → ``int(1)`` for an integer FK and ``"null"`` → ``__isnull=True`` |
| 13.10 | AG payload + base64 ``filtered_export`` | ``_extract_selected_ids_for_export`` path taken; only the decoded ids in the sheet (this is the AG-path analogue of 13.3) |

### 13d. Auth & edge cases

| # | Scenario | What We Assert |
|---|----------|----------------|
| 13.11 | Unauthenticated POST | 401 / 403; no ``.xlsx`` bytes |
| 13.12 | Per-object ``permission_export`` (non-uniform) | ``_compute_uniform_export_mask`` returns ``None``; slow per-row mask runs; each row's columns are masked according to that row's own permission result |

**What is explicitly NOT tested here:**

- ❌ **Excel formatting cosmetics** (column widths, cell colours,
  freeze panes beyond the default). Those are xlsxwriter's job.
- ❌ **Streaming memory ceiling.** Cluster 11 owns that.
- ❌ **Pivot mode.** The endpoint supports ``pivotMode`` but it is
  not wired to the UI in this framework and is out of scope until a
  customer-visible pivot surface exists.

---

## 14. AG Grid Query Endpoint

**What it tests:** the real ``GET /api/model_entries/<model>/list``
and ``POST`` against the same URL — the endpoint the AG Grid UI hits
for every scroll, sort, filter, group, and pivot. Cluster 2 tests
basic list GET semantics; this cluster tests the **30+ helper
functions and methods in** ``lex/api/views/model_entries/List.py``
that translate UI query-params / AG Grid JSON into Django ORM
queries.

**Why it matters:** this is the hottest single endpoint in the
framework. Every table the user sees routes through it. A silent
bug in ``_coerce_value`` (string ``"1"`` not converted to ``int``
for an ``IntegerField``) drops every filtered query to zero rows
with no error. A bug in ``_apply_sort_model`` makes the grid
appear to ignore header-clicks. A bug in ``_build_filter_q`` makes
the filter dropdown do nothing. Today the cluster has **zero E2E
coverage** — we test the serializer, the export, but not the
query that feeds them.

**Why after cluster 13:** the export endpoint re-uses
``ListModelEntries._apply_filter_model`` / ``_apply_sort_model``
/ ``_execute_ag_grid_request`` under the hood. If the query path
is broken, the export is broken in the same way. Testing the
query path AFTER the export path means any regression that
shows up only in a list/grid context (not in export) gets a
dedicated gate here.

**Design principle:** same rule as cluster 13 — **scenarios
spanning multiple methods, not unit tests of single helpers**.
One "filter by date range, sort DESC by amount, paginate" test
fires through ``post`` → ``_normalize_ag_request`` →
``_apply_filter_model`` → ``_build_filter_q`` (date branch) →
``_parse_ag_date`` → ``_apply_sort_model`` → ``_execute_leaf_level``
in a single flight. Assertions are on the HTTP response body, not
on internal state.

**Models needed:**

- ``QueryCategory`` — small FK target with a distinctive
  ``__str__``.
- ``QueryItem`` — wide row carrying one field per "interesting"
  filter type: ``name`` (``CharField``), ``amount`` (``Decimal``),
  ``count`` (``Integer``), ``is_active`` (``Boolean``),
  ``created_on`` (``Date``), ``created_at_ts`` (``DateTimeField``),
  ``status`` (``CharField(choices=...)``), ``metadata``
  (``JSONField``), ``category`` (``FK → QueryCategory``). Open
  permissions so the tests focus on query mechanics.

### 14a. GET list — query-param filtering & ordering

| # | Scenario | What We Assert |
|---|----------|----------------|
| 14.1 | ``?name__icontains=alp`` | Only rows matching the substring come back; ``_resolve_lookup`` routes to the safe ``__icontains`` lookup |
| 14.2 | ``?amount__gte=100&amount__lte=500`` | Decimal range filtering; ``_coerce_value`` converts strings to ``Decimal`` |
| 14.3 | ``?status__in=active,archived`` | Comma-separated ``in`` lookup; ``_build_query_from_values`` splits the single string |
| 14.4 | ``?count!=0`` (negated key) | Trailing ``!`` negates the filter; ``apply_query_param_filters`` routes through ``.exclude`` |
| 14.5 | ``?ordering=-amount`` | Rows returned in descending ``amount`` order; ``apply_ordering`` resolves the field path |
| 14.6 | ``?perPage=-1`` | Pagination response includes EVERY filtered row in ``results`` (``CustomPageNumberPagination.paginate_queryset`` ``-1`` branch) |
| 14.7 | ``?pk_only=true&status=active`` | Response shape is ``{"ids": [...], "count": N}`` — not the full row payload. Fast ``list()`` shortcut. |

### 14b. AG Grid POST — flat leaf

| # | Scenario | What We Assert |
|---|----------|----------------|
| 14.8 | POST with ``{startRow: 0, endRow: 2}`` | ``rowData`` has exactly 2 rows; ``rowCount`` matches DB total; ``_execute_leaf_level`` page slice |
| 14.9 | ``filterModel.text`` ``contains`` | Only matching rows come back; ``_build_filter_q`` text branch |
| 14.10 | ``filterModel.number`` ``inRange`` + ``sortModel`` DESC | Both applied: rows in range AND sorted DESC. Spans ``_apply_filter_model`` + ``_apply_sort_model`` + ``_coerce_value`` |
| 14.11 | ``filterModel.date`` ``greaterThan`` on a ``DateField`` | Date filter routes to ``__gt`` via ``_parse_ag_date`` |
| 14.12 | ``filterModel.date`` ``equals`` on a ``DateTimeField`` **with time** | ``_ag_filter_has_time`` → True, filter routes to ``__gte / __lt`` second-precision window via ``_parse_ag_datetime`` |
| 14.13 | ``filterModel.set`` | ``__in`` lookup against the chosen set of values |
| 14.14 | ``filterModel`` with ``operator: "OR"`` + multiple conditions | Rows matching EITHER condition; ``_build_filter_q`` recursion path |

### 14c. AG Grid POST — grouping, aggregation, pivot

| # | Scenario | What We Assert |
|---|----------|----------------|
| 14.15 | ``rowGroupCols: [category]`` at level 0 | ``rowData`` is one row per category with ``__childCount``; ``_execute_group_level`` |
| 14.16 | ``rowGroupCols: [category]`` + ``valueCols: [amount:sum]`` | Each group row carries the correct SUM; ``_build_value_annotations`` + ``_build_agg_expression`` |
| 14.17 | Drill into a group — ``rowGroupCols: [category]`` + ``groupKeys: [<cat-pk>]`` | Leaf rows for that category only; ``_apply_group_key_filters`` |
| 14.18 | ``pivotMode: true`` + ``pivotCols: [status]`` + ``valueCols: [amount:sum]`` | ``rowData`` contains one aggregated row; ``pivotResultFields`` lists the generated columns; ``_execute_pivot_mode`` + ``_build_pivot_annotations`` + ``_build_conditional_agg_expression`` |

### 14d. Edge cases

| # | Scenario | What We Assert |
|---|----------|----------------|
| 14.19 | ``filterModel`` naming a field that does not exist | Ignored silently by ``_is_valid_field_path``; response is the unfiltered set (does not crash) |
| 14.20 | ``sortModel`` with ``colId: "non_existent"`` | Silently dropped by ``_is_valid_field_path``; default PK order applied |

### 14e. Secondary filter / sort branches (April 21) 🟢

The 14b baseline hits the main text / number / date / set / compound-OR paths. The **long tail of operation-type branches** in ``_build_filter_q`` that the AG Grid header dropdowns actually emit in production was still cold. 14e closes those gaps with 4 table-driven scenarios + 1 xfail capturing a real framework bug.

| # | Scenario | What We Assert |
|---|----------|----------------|
| 14.21 | Text filter op variants — ``startsWith`` / ``endsWith`` / ``equals`` / ``notEqual`` / ``notContains`` | Each op returns the correct row set; one ``subTest`` per op so a regression names the failing branch |
| 14.22 | Number filter op variants — ``lessThan`` / ``lessThanOrEqual`` / ``greaterThanOrEqual`` / ``notEqual`` / ``inRange`` + date ``blank`` (which DOES work) | Same table-driven shape |
| 14.23 | Legacy ``condition1`` / ``condition2`` shape — both AND and OR operators | Older AG Grid clients still send this shape; endpoint must serve both frontend versions from one deploy |
| 14.24 | ``?ordering=-amount,name`` multi-field CSV + unknown token silently dropped | Primary + secondary sort both applied; ``?ordering=not_a_real_field,-amount`` returns 200 (no 500 on schema drift) |
| 14.25 | **BUG-016** — ``blank`` / ``notBlank`` filter ops are unreachable | Text ``blank`` / ``notBlank`` and date ``notBlank`` silently return every row because the early-return guards at the top of each filter-type branch in ``_build_filter_q`` short-circuit on missing ``filter`` value before the per-op dispatch can run. `xfail` — will pass once the framework bypass-list is widened to include ``notBlank`` (and the text branch special-cases ``blank`` / ``notBlank`` the way the date branch already does) |

**Status:** 🟢 Complete — 4 pass + 1 xfail (BUG-016).

**What is explicitly NOT tested here:**

- ❌ **AuditLog deferred-permission leaf path**. Covered
  transitively by cluster 6 (audit) which already drives the same
  code through a different fixture.
- ❌ **Performance**. Cluster 11 owns volume & query-count
  budgets.
- ❌ **Export-specific layout / FK display names**. Cluster 13.

---

## Planned Expansions — Coverage-Driven Sub-Clusters

After landing Clusters 1–14 (176+ scenarios, 14 real framework bugs surfaced), a coverage audit (April 21, baseline **42.63%** overall) flagged six source files as "customer-visible, high-impact, low-coverage". Rather than invent new top-level clusters, each gap folds into an **existing** cluster as a new sub-cluster. This keeps the user-journey narrative intact and avoids a "Cluster 15/16/17/…" proliferation.

Priorities below are ordered by expected coverage delta × customer-visibility.

### 4e. Read-restriction filter backend — `UserReadRestrictionFilterBackend`

**Gap:** `lex/api/views/model_entries/filter_backends.py` — 198 stmts, **28.97%** covered. Every List / Export / History query passes through this; also the lookup table for BUG-011 (permission O(n)).

**Models:** `ProtectedItem` (reused from 4a) + new `MixedResourceItem` for the AuditLog content-type visibility path.

| # | Scenario | What We Assert |
|---|----------|----------------|
| 4.13 | Per-row visibility — mixed allowed/denied rows in one page | Only allowed rows in response, `rowCount` reflects filtered total |
| 4.14 | AuditLog resource filter — `_build_auditlog_db_visibility_filters` | Rows for resources the user can't read are excluded at the DB level (no Python-side filtering for handled resources) |
| 4.15 | AuditLog deferred-permission path — mixed handled + residual resources | Residual rows are permission-checked via `can_read_from_payload`; handled rows go through the DB filter |
| 4.16 | `pk_only=true` fast path honours permissions | Denied rows excluded from id list; count matches allowed subset |
| 4.17 | Superuser bypass | `permission_read` never invoked; all rows returned |
| 4.18 | Deny-all short-circuit — `permission_read → deny` | Zero rows, zero SQL beyond the permission probe |

### 4f. Serializer-level masking — `PermissionAwareSerializerMixin`

**Gap:** `lex/api/views/model_entries/mixins/PermissionAwareSerializerMixin.py` — 102 stmts, **9.33%** covered. Paired with 12c/12e on the write side (see below).

**Models:** `ProtectedWideItem` (reused from 12) + new `NestedProtectedItem` (FK → restricted model).

| # | Scenario | What We Assert |
|---|----------|----------------|
| 4.19 | Read-mask drops denied fields from detail payload | Field absent, pinned `id` / `short_description` always present |
| 4.20 | Read-mask applied consistently across list, detail, many | Same field set in each endpoint for the same user |
| 4.21 | Nested FK field is redacted when target row denied | FK-dict replaced with scalar id-only (or absent per spec) |
| 4.22 | `allow_fields({...})` and `allow_all_except({...})` produce identical output for matching specs | Round-trip equivalence |

### 12f. Serializer write paths — M2M & FK-nested create

**Gap:** `lex/api/serializers/base_serializers.py` still has 108 missing stmts, concentrated in write paths (lines 447–459, 472–478, 530–549) — the M2M and FK-nested-create branches.

| # | Scenario | What We Assert |
|---|----------|----------------|
| 12.26 | M2M write via POST with list of pks | Relationship created, through-table rows correct |
| 12.27 | M2M write via PATCH replaces existing set | Old relations removed, new ones added, one atomic op |
| 12.28 | Nested FK create — POST with inline `{"related": {"name": ...}}` | New related row created and referenced; no orphan if parent fails validation |

### 10e. Schema introspection — `ModelStructureObtainView`, `Fields`, `Widgets`

**Gap:** `ModelStructureObtainView.py` (102 stmts, 21.54%), `model_info/Fields.py` (67 stmts, 22.35%). Drives every frontend form. A bug here breaks the UI for every model.

**Models:** reuse `SimpleItem`, `ProtectedItem`, `WideItem` (12b).

| # | Scenario | What We Assert |
|---|----------|----------------|
| 10.11 | `/api/model-structure/` returns expected tree for a seeded registry | Every registered model + its fields + FK edges appear |
| 10.12 | Field metadata — type, nullable, choices, help_text | Customer-visible schema matches model definition |
| 10.13 | Widget hints — `XLSXField` / `PDFField` surfaced | Frontend can distinguish scalar vs upload field |
| 10.14 | Permission-aware schema — denied fields omitted per-user | Same endpoint returns different schema for superuser vs restricted user |

### 10f. Global search — `Search.py` 🟢

**Gap:** 28 stmts, baseline **34.21%**. Small surface, user-facing — the nav-bar search box hits this endpoint.

**Models:** reuses `SchemaItem` from 10e (a varied-field model — `SearchVector` indexes the `name` CharField).

Shipped 4 scenarios. The view depends only on `self.model_collection` + `self.kwargs['query']`, so the tests build a `SimpleNamespace(all_containers=[...])` stand-in and drive `Search.get` directly — no URL wiring required. `UserPermission` is patched open; the exclusion-list contract is asserted independently.

| # | Scenario | What We Assert |
|---|----------|----------------|
| 10.15 | Query matches a text field on a registered model | Response is `{data, total}`; hit has `id` / `model` / `content.description` / `url` (routing) |
| 10.15b | Zero matches returns the documented `"No match found"` sentinel string | Frontend branches on response type — drift would silently break the search box |
| 10.16 | Container whose `id` is in `EXCLUDED_MODELS` (`user`, `permission`, …) short-circuits *before* the query runs | No PII leak through global search even if system rows contain the query term |
| 10.16b | `EXCLUDED_TYPES` still contains every non-text field type (`FloatField`, `BooleanField`, `IntegerField`, `FileField`, `ForeignKey`) | Regression gate — if a non-text type slips out, `SearchVector` 500s at runtime |

**Status:** 🟢 Complete — 4 pass / 0 fail.

### 5.11 — History fallback-snapshot path 🟢

**Gap:** `History.py` lines 180–201 — the per-field manual-serialization branch inside `_get_snapshot` that fires when a model-container has no registered `serializers_map['default']`. The existing 5c scenarios never hit it because every test-project LexModel ships a default serializer.

**Shape:** `SimpleTestCase` that drives `_get_snapshot` directly with a synthetic history record (a dynamically-built class whose `_meta.fields` yields `.name`-carrying fakes). Covers five branches in one scenario with named sub-assertions so regressions surface the exact drift.

| # | Scenario | What We Assert |
|---|----------|----------------|
| 5.11 | Fallback snapshot contract | **(a)** `CONTROL_FIELDS` (`history_id` / `valid_from` / `sys_from` / `meta_task_status` / …) filtered out even when populated — frontend must not see system columns inside the business payload; **(b)** `datetime` → `isoformat()`; **(c)** `date` → `isoformat()`; **(d)** non-primitive object coerced via `str()` so DRF's JSON encoder doesn't blow up; **(e)** primitives (`int` / `bool` / `None`) + containers (`list` / `dict`) pass through unchanged |

**Status:** 🟢 Complete — 1 pass / 0 fail.

### 9.7 – 9.10 — Bitemporal suppression guards 🟢

**Gap:** `bitemporal_signals.py` at 46.60% baseline. The three `ContextVar`-backed suppression guards (`suppress_main_table_sync`, `suppress_history_valid_to_chaining`, `suppress_meta_sys_to_chaining`) are consulted by every handler in the file — early-return at lines 118, 274, and the Level-2 meta-chaining guard. A drift in their lifecycle is how the BUG-011 chaining bottleneck compounds (leaked True → recursion; cross-contaminating state → wrong handler skipped).

Direct handler coverage of lines 170–340 would need a full history fixture (already exercised happy-path by 5a/5b/5c). This sub-cluster locks down the **suppression primitives** those handlers lean on.

**Shape:** `SimpleTestCase` — pure Python, no DB, no models. Runs in 1ms.

| # | Scenario | What We Assert |
|---|----------|----------------|
| 9.7 | Guard lifecycle (before / inside / after) for all three guards | `ContextVar` defaults False; flips True on enter; resets False on exit. A leaked True across request boundaries silently skips bitemporal maintenance. |
| 9.8 | Nested suspension stacks and unwinds | Inner `with` exit does **not** deactivate the outer context — this is what the handlers' internal `with suppress_*(): record.save(...)` depends on to avoid unbounded recursion |
| 9.9 | Three guards are independent | Suspending `main_table_sync` must not suspend `valid_to_chaining` or `meta_sys_to_chaining` — the handlers rely on asymmetric combinations |
| 9.10 | Suspension is thread-local | Background thread sees `False` even while the parent thread holds a suspension — guarantees Celery-worker parallel requests don't silently share suspension state |

**Status:** 🟢 Complete — 4 pass / 0 fail.

### 7g — `CalculatedModel.create()` pipeline (end-to-end) 🟢

**Gap:** `lex/core/mixins/CalculatedModelMixin.py` baseline **33.74%** after 7a–7f. The remaining 369 missing statements were concentrated in the four-step orchestrator invoked by `Model.create(**overrides)`:

1. `_generate_model_combinations` (1346-1401)
2. `_prepare_models_for_processing` (1403-1494)
3. `_create_processing_clusters` (1497-1576)
4. `_dispatch_model_processing` — sync branch (1579-1713)
5. `calc_and_save_sync` (843-971)
6. `delete_models_with_same_defining_fields` (1715-1807)

**Model:** new `CombinatorialCalc` — a non-atomic `CalculatedModelMixin` subclass with `defining_fields = ["region", "category"]` and `parallelizable_fields = ["region"]`. A single `create()` call walks every one of the six sections above.

**Shape:** `E2ETestCase` — runs under `CELERY_ACTIVE=False` (the documented sync fallback); same environment every other Cluster 7 scenario uses.

| # | Scenario | What We Assert |
|---|----------|----------------|
| 7.25 | `Model.create(region=[...], category=[...])` cartesian expansion | 3×2 = 6 rows persisted, each with `name` set by `calculate()`; exercises combination generator → prepare → cluster → dispatch → `calc_and_save_sync` |
| 7.26 | `Model.create()` with no kwargs falls back to `get_selected_key_list` | Default 2×2 = 4 rows, one per `(region, category)` combo |
| 7.27 | Partial failure — `calculate()` raises for one region | `calc_and_save_sync` catches + accumulates the error and keeps going; failed rows are NOT saved; successful rows persist; "processed_count > 0" warning branch fires |
| 7.28 | `Model.create()` is idempotent on rerun | `delete_models_with_same_defining_fields` detects existing rows; pk set unchanged between runs |
| 7.29 | Empty `get_selected_key_list` return prunes the whole branch | Zero rows, no error — exercises the `if not field_values: continue` + early-break |
| 7.30 | `delete_models_with_same_defining_fields` on un-saved instance | Returns `self` and resets a stale pk to `None` so caller can INSERT |

**Status:** 🟢 Complete — 6 pass / 0 fail. Drove `CalculatedModelMixin.py` from **33.74% → 64.75%** (+31 pts, +170 lines covered).

### Sequencing

```
4e  (filter_backends)      — biggest coverage delta, unlocks BUG-011 work
4f  (serializer masking)   — hand-in-glove with 4e; shares test models
12f (M2M / nested FK write) — closes base_serializers write paths
10e (schema introspection) — small surface, frontend-critical
10f (global search)        — small, user-facing
5.11 + 9.7-9.10            — close remaining holes in already-🟢 clusters
7g  (CalculatedModel.create pipeline) — closes the largest single source-file gap
```

**Why no new top-level clusters:** every gap is a *facet* of an existing user-journey concern. Permission enforcement belongs in Cluster 4. Schema & search belong in Cluster 10 (API Layer). Write-path serializer behaviour is Cluster 12. Signal branches are Cluster 9. Splitting them out would fragment the story and duplicate setup.

---

## Model Inventory (Summary)

| Model | Type | Used In Clusters |
|-------|------|-----------------|
| `SeedableItem` | LexModel | 1 (1c) |
| `SimpleItem` | LexModel | 2, 5, 6, 10 |
| `TrackedItem` | LexModel | 2 |
| `PreValidatedItem` | LexModel | 3 |
| `PostValidatedItem` | LexModel | 3 |
| `HookOrderItem` | LexModel | 3 |
| `ProtectedItem` | LexModel | 4 |
| `FieldLevelItem` | LexModel | 4 |
| `KeycloakItem` | LexModel | 4 |
| `AtomicCalc` | CalculationModel | 5, 6, 7, 8, 9, 10 |
| `NonAtomicCalc` | CalculationModel (is_atomic=False) | 5, 7 |
| `ParentCalc` | CalculationModel | 7, 9 |
| `ChildCalc` | CalculationModel | 7, 9 |
| `GrandchildCalc` | CalculationModel | 7 |
| `FailingCalc` | CalculationModel | 7 |
| `CombinatorialCalc` | CalculatedModelMixin (`defining_fields`, `parallelizable_fields`) | 7 (7g) |
| `CeleryCalc` | CalculationModel (@lex_shared_task) | 8 |
| `StressCounterparty` | LexModel (small FK target) | 11 |
| `StressInvoice` | LexModel (wide row, FK to StressCounterparty) | 11 |
| `StressPeriod` | LexModel (bitemporal `valid_from` / `valid_to`) | 11 |
| `PeriodAggregateCalc` | CalculationModel (aggregates StressInvoice over StressPeriod) | 11 |
| `DependentPeriodCalc` | CalculationModel (depends on prior 3 periods of PeriodAggregateCalc) | 11 |
| `FKHeavyCategory` | LexModel (small FK target, ~20 rows) | 11 (FK-heavy) |
| `FKHeavyCurrency` | LexModel (small FK target, ~5 rows) | 11 (FK-heavy) |
| `FKHeavyInvoice` | LexModel (25k rows, 4 FKs to Counterparty/Period/Category/Currency) | 11 (FK-heavy) |
| `RelatedItem` | LexModel (small FK target) | 12 |
| `WideItem` | LexModel (one field per type — Decimal/DateTime/Date/Time/UUID/JSON/choices/FK) | 12 |
| `ProtectedWideItem` | LexModel (WideItem shape + restrictive `permission_read`) | 12 |
| `ExportCategory` | LexModel (small FK target with distinctive `__str__`) | 13 |
| `ExportItem` | LexModel (name / Decimal / choice / FK to ExportCategory; default export perms) | 13 |
| `ExportMaskedItem` | LexModel (same shape; `permission_export` → `allow_fields({"id","name"})`) | 13 |
| `QueryCategory` | LexModel (small FK target; distinctive `__str__`) | 14 |
| `QueryItem` | LexModel (name / Decimal / Integer / Boolean / Date / DateTime / choice / JSON / FK) | 14 |

---

> **Back to:** [Test Plan Index](index.md) | **See also:** [Why the Shift](why-the-shift.md) | [Expected Results](expected-results.md) | [Progress](progress.md)
