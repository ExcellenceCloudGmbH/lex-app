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
> **If a test fails because the code is buggy: good.** Mark it `@unittest.expectedFailure` with a reference in the [Known Bugs Tracker](known-bugs.md). The failure is the test doing its job.
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

### 1q. Migration file completeness gate ✅

**Gap:** A framework release must never rely on customer machines to generate
missing migration files during `lex Init`; that creates downstream duplicate
migration conflicts when the missing files are later committed in a follow-up
release.

**Scenario range:** 1.147 – 1.147. **Test file:** `lex/test_project/tests/init/test_1q_migration_files_complete.py`. **Type:** U. **Status:** ✅ Complete (Session 70 — June 2).

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
| 2.23 | DELETE to `many/` removes selected records | Response 200, body lists deleted ids, selected rows gone from DB |
| 2.24 | DELETE to `many/` leaves unselected records | Rows not named by repeated `ids` query params remain untouched |
| 2.25 | DELETE to `many/` with unknown ids | Stale/unknown ids are ignored safely; only existing selected rows are reported/deleted |

### 2i. Cancel-calculation REST endpoint ✅

**Gap:** The user-facing abort button on a long-running `CalculationModel` row needs an HTTP surface to call. We extended the existing detail `PATCH` endpoint with a `{"cancel":"true"}` short-circuit (mirroring the established `calculate=true` trigger pattern, so no new URL route is required) that routes to `CalculationModel.cancel(instance, recursive=True)` and returns the report dict the cancel classmethod produces. 2i pins the four customer-visible behaviours: `202 Accepted` with the report when the row was cancellable; `409 Conflict` with `reason=not_in_progress` when the row already terminated; `409 Conflict` with `reason=sync_calculation_not_cancellable` when the row is IN_PROGRESS but was dispatched synchronously (no Celery task to revoke); and crucially, the short-circuit must return **before** any `serializer.save()`, so a request body `{"cancel":"true","name":"X"}` cannot silently apply the sibling `name` field while cancelling. A regression that let sibling fields ride along would convert "abort" into "abort and corrupt the row at the same time" — the worst possible compound bug.

**Scenario range:** 2.93 – 2.96. **Test file:** `lex/test_project/tests/crud_api/test_2i_cancel_endpoint.py`. **Type:** E. **Status:** ✅ Complete (Session 66 — June 1).

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
| 4.40 | `permission_export` full deny at the export endpoint (sub-cluster 4h) | POST `/api/<model>/export` → 200 with rows present (read open) but every domain field blanked; only the framework's `{id, created_by, edited_by}` columns may carry data. Pins the union behaviour in `ModelExportView.get_exportable_fields_for_object` against both over-restrictive (rows dropped) and over-permissive (domain leaks) drift. |
| 4.41 | Full `permission_read` deny at the detail endpoint (sub-cluster 4e) | GET `/api/<model>/<id>/` for a row whose `permission_read` returns `deny` → 200 with `{}` and no domain fields / `id` leakage. List endpoints already drop denied rows; this pins the serializer guard for guessed detail URLs. |

---

## 5. History & Bitemporal

**What it tests:** That every `.save()` on a `LexModel` creates a history row, that `valid_from`/`valid_to` chain correctly, and that the MetaHistory layer works.

**Why fifth:** Customers start looking at history after they've been editing records for a while. "What changed and when?" is a compliance requirement.

**Documented contract** (from `docs/features/tracking/history.md` + `docs/features/tracking/bitemporal history.md` + `docs/interface/record-detail/history tab.md` + `docs/features/tracking/tracking tables.md`): every `LexModel` save / update / delete produces a Level-1 `Historical*` row with a **full snapshot of all field values** (not just the changed ones) + `history_type` (`+`/`~`/`-`) + `history_user` (the person who edited the record, or the user who launched the calculation that produced this change) + optional `history_change_reason` (currently only writable from code, not from the UI). Unlike audit logging (which is API-only), history is triggered at the ORM level by every `save()` — both API-driven edits and programmatic/calculation-driven writes produce history rows. Rows are auto-chained so each row's `valid_to` matches the next row's `valid_from` and the latest row carries `valid_to=NULL`; a Level-2 `MetaHistorical*` row records *system time* (`sys_from`/`sys_to`) for every Level-1 change so retroactive `valid_from` corrections are themselves auditable; opt-outs (`untrack()` / `track()` / `save_without_historical_record()` / `bulk_create(skip_history=True)` / `untracked_models` in `model_structure.yaml` / `suspend_bitemporal()` for derived calc outputs) keep the customer in control of overhead; time-travel via `get_queryset_as_of(Model, t)` (valid time) or `get_queryset_as_of(HistoryModel, t)` (system time), and via `GET /api/<model>/<id>/history/?as_of=...` from the UI's *As-Of* control.

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

> **Audit notes — May 5.** Walked the implementation against `docs/features/tracking/`:
> * **5.4 partial.** `test_5_4` only asserts ascending `history_id`; the docstring's "valid_to of row N = valid_from of row N+1" half is *not* yet pinned. New gap sub-cluster **5g** (below) closes it as scenario **5.61**.
> * **5.5 narrow.** `skip_history_when_saving` is one of *four* documented suppression toggles (`skip_history_when_saving`, `save_without_historical_record()`, `untrack()`/`track()`, `bulk_create(skip_history=True)`). The other three are uncovered — see **5h** scenarios 5.62–5.65.
> * **5.9 thin.** Only HTTP 200 + ≥3 rows asserted. The documented response shape (`history_id` / `valid_from` / `valid_to` / `history_type` / `user` / `snapshot` / `system_history`) and the `?as_of=...` system-time time-travel branch are *not* asserted — see **5i** scenarios 5.71–5.74.
> * **History snapshot contract not asserted anywhere.** Docs guarantee each history row carries a full snapshot of every field at that moment ("if your model has 10 fields, every history row has all 10"). 5.1/5.2/5.3 only check counts and types. See **5j** scenario 5.75.
> * **`history_user` actor not asserted.** API-driven save must stamp `history_user` to the authenticated user; not pinned anywhere. See **5j** scenario 5.76.
> * **MetaHistory (Level 2) positive contract uncovered.** 9.7–9.10 cover the suppression *primitives* but no test ever asserts that a save *creates* a MetaHistorical row, that `sys_from`/`sys_to` chain, or that an `history_object` FK points back to L1. See **5k** scenarios 5.81–5.84.
> * **`suspend_bitemporal()` positive contract uncovered.** 9.7–9.10 lock down the underlying ContextVars; the customer-facing CM (`with suspend_bitemporal(): obj.save()` → 1 query, 0 history rows, 0 meta rows) is not exercised. See **5h** scenario 5.66.
> * **`untracked_models` config not tested.** Documented `model_structure.yaml` opt-out at the *model* level (no `Historical*` table generated) — see **5h** scenario 5.67 (deferred-fixture).
> * **Time-travel helpers `get_queryset_as_of(...)` uncovered.** Both branches (main-model → valid time; history-model → system time) — see **5i** scenarios 5.72–5.73.

---

## 6. Audit Logging

**What it tests:** The `AuditLogMixin` records every API create/update/delete with correct actor, action, payload, and status. Also tests calculation audit finalization.

**Why sixth:** Audit logs are the compliance backbone. Customers in regulated industries (finance, healthcare) need proof of every action.

**Documented contract** (from `docs/features/tracking/audit logs.md` + `docs/interface/record-detail/audit log tab.md` + `docs/features/tracking/tracking tables.md`): **Audit log entries are created exclusively through the REST API layer** (`AuditLogMixin` on DRF views) — a programmatic `obj.save()` at the ORM level does **not** produce an audit row. Only API endpoints (POST create, PATCH/PUT update, DELETE) trigger the mixin. The one exception is **calculation audit finalization**: `ensure_terminal_calculation_audit` writes a terminal audit row from the calc state machine (not the API layer) to record whether a calculation succeeded or failed.

Every API create / update / delete produces an `AuditLog` (`date`, `author`, `resource`, `action`, `payload`, `content_type` + `object_id` GenericForeignKey, optional `calculation_id`) **plus** a paired `AuditLogStatus` whose status walks `pending → success` (or `pending → failure` carrying the full error traceback). The audit row is written **before** the operation, so even operations that fail at validation / permission / DB level are recorded with full context. The `payload` starts as the submitted request body; on success it is **rewritten to the final persisted state** (so the audit row reflects what was actually saved, not what was attempted); on failure it remains the attempted payload.

When the change was triggered by a calculation, the audit entry's `calculation_id` is non-empty and links to the Calculation Log tree — that's how an operator traces from "this field was changed" to "this is the calculation that changed it". For plain user edits, `calculation_id` is empty.

> [!note] `edited_by` / `edited_at` vs audit `author`
> The tracking-tables doc (§3, note) clarifies that `edited_by` / `edited_at` on the record reflect **edits only** — calculation-driven changes do *not* update them, even though they do produce history and audit entries. To determine whether a change came from a person or a calculation, look at the audit entry's `calculation_id`.

`BulkAuditLogMixin` produces one audit row per record in a bulk op (a 100-row bulk update writes 100 audit rows). The system is resilient by design: deadlocks and serialization conflicts are auto-retried with exponential backoff, ContentType cache staleness is auto-corrected, and audit rows are effectively read-only — `permission_create` / `permission_delete` return False and `permission_edit` denies for everyone except `AdminReportsModificationRestriction`.

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

> **Audit notes — May 5.** Walked the implementation against `docs/features/tracking/audit logs.md` + `docs/interface/record-detail/audit log tab.md`:
> * **6.1 thin.** Asserts `count==1` + `author truthy` + `status=='success'`. The docs explicitly enumerate **six** customer-visible columns the audit log row must carry (`date`, `author`, `resource`, `action`, `payload`, `content_type`+`object_id` GenericForeignKey, optional `calculation_id`) — only `author`, `resource`, `action` are pinned today. Most importantly, **`content_type` + `object_id` are never asserted**, even though the Audit Log Tab UI filters by them to show "operations that affected this specific record". See gap sub-cluster **6d** scenarios 6.41–6.43.
> * **6.2 thin.** Asserts only that `"value"` is in the payload dict. Docs require: payload carries the *full serialized data* of the operation, on update payload is *refreshed to the final state* on success (`audit_log.payload = updated_payload` line 265 of `AuditLogMixin.py`), payload includes `id` after save. None pinned. See **6d** scenarios 6.42–6.43.
> * **6.3 thin.** Asserts only `count==1` + `status=='success'`. Docs explicitly say "For deletions, the payload captures the record's state at the moment of deletion — so you can always inspect what was removed." That contract is uncovered. See **6d** scenario 6.44.
> * **6.4 skipped.** Documented as "needs middleware-level audit hook". The mixin's *exception path* (`AuditLogMixin.py` lines 234–283 / 298–311 — `_pending_failed_audit_logs` queue + `_failed_audit_logged` sentinel + atomic vs. non-atomic split) is dark. The contract is concrete and reachable today via raising `pre_validation` + a `perform_create` save: failure status row, status='failure', traceback non-empty, atomic-block path queues for replay. See **6d** scenarios 6.45–6.47 (re-scoped — no middleware needed).
> * **Pending intermediate state never observed.** The docs mermaid diagram makes " Pending →  Success /  Failure" a customer-visible lifecycle, but no test ever observes the *pending* state mid-flight. See **6d** scenario 6.48.
> * **`BulkAuditLogMixin` not tested.** Documented as "Each individual record in a bulk operation gets its own audit log entry — so a bulk update of 100 records creates 100 audit log entries." Cluster 2e's bulk DELETE scenarios (2.23/2.24/2.25) never assert the audit row count. See gap sub-cluster **6e** scenarios 6.51–6.53.
> * **Resilience contracts unpinned.** Two are explicitly called out in docs ("Resilience" section): deadlock retries (`RETRYABLE_SQLSTATE_CODES = {"40P01", "40001"}` + 3 attempts + exponential backoff) and ContentType cache healing (`safe_get_content_type` recovers when Django's cache goes stale post-migration). See **6f** scenarios 6.61–6.63.
> * **Read-only / immutable contract not gated.** Docs `[!note]`: "Audit logs are effectively read-only. Only administrators should modify or delete them." `AuditLog.permission_create` / `permission_delete` return False, `permission_edit` denies. No test pins this — a regression that flipped any of those bools to `True` would silently allow audit deletion. See **6g** scenarios 6.71–6.73.
> * **6.10 still failing.** The "audit row survives the outer atomic rollback" contract from `ensure_terminal_calculation_audit` is not yet honoured — the inner `transaction.atomic()` joins the outer block as a savepoint, so it rolls back too. Already tracked as BUG-001 family but the marker on `test_6_10` is currently *commented out* (the `@unittest.expectedFailure` line is `# @unittest.expectedFailure`), so when the test fails it's an *unexpected* failure. **Fix in this update**: re-enable the marker.
>
> **Audit notes — May 7 (tracking-tables doc cross-check).** Walked the test-plan against `docs/features/tracking/tracking tables.md`:
> * **API-only scope clarified.** The tracking-tables doc and user confirmation make explicit what was implicit: audit log entries are created **exclusively through the REST API layer** (`AuditLogMixin`), not by programmatic `obj.save()`. The one exception is `ensure_terminal_calculation_audit`, which writes a terminal audit row from the calc state machine. This distinction is now documented in the Cluster 6 contract above. History, by contrast, fires at the ORM level on every `save()` — both API and programmatic. Updated Cluster 5 contract to note this.
> * **Payload lifecycle clarified.** The doc explicitly states: "starts as submitted payload, on success rewritten to final persisted state, on failure remains the attempted payload." Already covered by 6.42/6.43, but the Cluster 6 contract paragraph now carries this language directly.
> * **`calculation_id` linkage noted.** The doc describes `calculation_id` as the bridge between "what was changed" and "why" — non-empty when triggered by a calculation, empty for plain edits. Not yet tested in isolation (no scenario pins `calculation_id` populated on a calc-driven audit entry vs empty on a user edit). Noted as future gap.
> * **`edited_by` / `edited_at` edit-only semantics noted.** The doc's note ("only edits update `edited_by`/`edited_at`; calculations do not") is now referenced in the Cluster 6 contract. Relates to BUG-007 but is a broader contract.
> * **`history_change_reason` UI limitation noted.** The doc says it's "currently only writable from code, no UI." Updated Cluster 5 contract.
> * **`history_user` definition clarified.** The doc says: "the person who edited the record, or the user who launched the calculation." Updated Cluster 5 contract.
> * **BUG-001b expanded (Session 54).** 6.10 now has 6 companion scenarios. The synthetic ones — **6.10-control** (sanity), **6.10b** (`AuditLogStatus` child also wiped), **6.10c** (3 retries → 0 rows), **6.10d** (nested savepoint shape) — call `ensure_terminal_calculation_audit()` directly inside a synthetic outer atomic and remain live regression gates (1 pass + 4 xfail). The end-to-end ones — **6.10e** (programmatic `calc.save()` inside outer atomic) and **6.10f** (API POST → PATCH → fallback) — are now `@unittest.skip` because the audit log's API-only contract poisons their diagnostic value: a programmatic `calc.save()` never seeds the API-layer `_pending_terminal_audit`, so the except-branch finalize has nothing to finalize and the 0-row outcome is ambiguous (could be "row rolled back" OR "row never written"). 6.10f's API path is the right shape but blocked by BUG-009 (PATCH of `is_calculated` is silently dropped). Both unblock once BUG-009 is fixed and the API path can drive a calc end-to-end with a real audit-mixin-seeded pending row in scope.

---

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

### 8w. Cancelled-calculation audit terminal status (BUG-023) 🚧

**Gap:** The cancellation state machine now persists `CalculationModel.is_calculated=CANCELLED`, but the audit timeline can still leave the paired `AuditLogStatus` row at `pending` for API-triggered Celery cancels. That violates the audit lifecycle contract (`pending` must always resolve to a terminal status) and makes operator forensics ambiguous.

| # | Scenario | What We Assert |
|---|----------|----------------|
| 8.90 | API-triggered Celery cancel finalizes pending audit row | `PATCH {"calculate":"true"}` followed by `PATCH {"cancel":"true"}` ends with model `CANCELLED` **and** audit status terminal `cancelled` (no lingering `pending`) |

**Scenario range:** 8.90 – 8.90. **Test file:** `lex/test_project/tests/celery_async/test_8w_cancelled_audit_pending.py`. **Type:** E (`E2ETestCase`). **Status:** 🚧 In progress — `@unittest.expectedFailure` repro for **BUG-023**.

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
| 10.7 | Many endpoint bulk delete | DELETE with repeated `ids` query params removes selected records only |
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
| 11.10 | Bulk API DELETE (`DELETE /many/?ids=…`) at volume | MEDIUM/LARGE selected subset | Selected rows deleted within budget; rows outside the explicit id set survive |
| 11.11 | ORM filtered `QuerySet.update()` baseline | MEDIUM | Time < 5s; exactly 1 UPDATE query (not n); affected row count and DB state match |
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

### 12c. List & Many read contract

| # | Scenario | What We Assert |
|---|----------|----------------|
| 12.20 | `FilteredListSerializer` drops records that serialize to `{}` | List with 3 rows, one denied by `permission_read`, returns 2 rows — not 3 with one empty dict |
| 12.21 | List response row shape matches detail | Every row in a list response has the same framework-managed keys as the detail endpoint |
| 12.22 | `/many/` GET selected rows match list shape | Read-only Many endpoint returns exactly the selected ids and the same framework-managed row keys as list/detail |

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
| 12.32 | Source model default override exposes configured framework alias | `api_serializers["default"]` remains the developer serializer, while the auto-generated serializer is additionally addressable under the configured alias (e.g. `framework_default`) |
| 12.33 | History table inherits framework alias from source model | A `Historical*` model with no own `api_serializers` follows the source model's alias decision through `instance_type`; it does not copy unrelated source serializers such as `detail` |
| 12.34 | Meta-history table walks the full `instance_type` chain | `MetaHistorical* → Historical* → Source` still exposes the auto-generated serializer under the configured alias |
| 12.35 | `_wrap_custom_serializer` preserves `Meta.hide_actions_column` | Serializer-level list UI metadata survives wrapping so tables can suppress Lex's default Show/Edit/Delete column |

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
| 13.2a | 5 rows, default permissions, no FK | HTTP 200 + ``.xlsx`` body; row count matches DB; every flat row is populated |
| 13.2b | 5 rows, default permissions, with FK | HTTP 200 + ``.xlsx`` body; row count matches DB; FK column shows ``str(category)`` (**``Cat<...>``**), not the integer pk |
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
| 13.9 | AG ``selection.groupKeyPaths`` filter | Only rows matching the selected group key are in the sheet; ``_coerce_group_key`` converts ``"1"`` → ``int(1)`` for an integer FK and ``"null"`` → ``__isnull=True`` for a null FK |
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

### 14e. Secondary filter / sort branches (April 21) 

The 14b baseline hits the main text / number / date / set / compound-OR paths. The **long tail of operation-type branches** in ``_build_filter_q`` that the AG Grid header dropdowns actually emit in production was still cold. 14e closes those gaps with 4 table-driven scenarios + 1 xfail capturing a real framework bug.

| # | Scenario | What We Assert |
|---|----------|----------------|
| 14.21 | Text filter op variants — ``startsWith`` / ``endsWith`` / ``equals`` / ``notEqual`` / ``notContains`` | Each op returns the correct row set; one ``subTest`` per op so a regression names the failing branch |
| 14.22 | Number filter op variants — ``lessThan`` / ``lessThanOrEqual`` / ``greaterThanOrEqual`` / ``notEqual`` / ``inRange`` + date ``blank`` (which DOES work) | Same table-driven shape |
| 14.23 | Legacy ``condition1`` / ``condition2`` shape — both AND and OR operators | Older AG Grid clients still send this shape; endpoint must serve both frontend versions from one deploy |
| 14.24 | ``?ordering=-amount,name`` multi-field CSV + unknown token silently dropped | Primary + secondary sort both applied; ``?ordering=not_a_real_field,-amount`` returns 200 (no 500 on schema drift) |
| 14.25 | **BUG-016** — ``blank`` / ``notBlank`` filter ops are unreachable | Skipped until the framework bypass-list is widened to include ``notBlank`` (and the text branch special-cases ``blank`` / ``notBlank`` the way the date branch already does) |

**Status:**  Complete — 4 pass + 1 skip (BUG-016).

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

**Models:** new `FilterBackendItem` in `permissions/models.py` — a minimal `LexModel` with `name` + `is_secret` whose `permission_read` branches on the caller's Django groups to hit all three filter-backend code paths in one fixture (`admin` → `allow_all`, `deny_all` → `deny`, default → per-row deny of secret rows). `MixedResourceItem` is deferred alongside the AuditLog scenarios (4.14 / 4.15) which need the Keycloak `user_permissions` payload + seeded `AuditLog` rows.

| # | Scenario | What We Assert | Status |
|---|----------|----------------|--------|
| 4.13 | Per-row visibility — mixed allowed/denied rows in one page | Only allowed rows in response (exercises `queryset.iterator()` + `excluded.append`) |  |
| 4.14 | AuditLog resource filter — `_build_auditlog_db_visibility_filters` | Rows for resources the user can't read are excluded at the DB level | ⏸ skip (fixture) |
| 4.15 | AuditLog deferred-permission path — mixed handled + residual resources | Residual rows are permission-checked via `can_read_from_payload` | ⏸ skip (fixture) |
| 4.16 | `pk_only=true` fast path honours permissions | Denied pks excluded from id list; `count` matches allowed subset |  |
| 4.17 | `allow_all` profile (admin group) returns every row | `permission_read → allow_all`, no exclusion (`return queryset` branch) |  |
| 4.18 | Deny-all short-circuit — `permission_read → deny` on every row | Zero rows returned even though DB holds every seeded row |  |
| 4.41 | Detail endpoint full read deny | Guessed detail URL returns `{}` and leaks no domain fields or `id` when `permission_read → deny` |  |

**Status:**  Complete — 5 pass + 2 skip. See progress.md Sessions 16 + 46.

### 4f. Serializer-level masking — `PermissionAwareSerializerMixin` 

**Gap:** `lex/api/views/model_entries/mixins/PermissionAwareSerializerMixin.py` — 102 stmts, **9.33%** baseline. Field-level *denial* outcomes are already gated by cluster 4b (BUG-010 xfail); 4f locks down the **mixin's infrastructure contracts** (naming, injection, metaclass) plus the `run_validation` hook end-to-end — the code the BUG-010 fix will rely on.

Split across two classes: **`TestCluster04f_MixinMachinery`** (4.19–4.22, `SimpleTestCase` — no DB) covers the plumbing; **`TestCluster04f_RunValidation`** (4.23–4.26, `E2ETestCase` — real fixtures) drives the actual customer-facing validation hook.

| # | Scenario | What We Assert |
|---|----------|----------------|
| 4.19 | `_camel_to_snake` table-driven over 9 shapes | Acronyms (`URLPath → url_path`), already-snake no-op, empty string — the translation every PATCH depends on |
| 4.20 | `_get_non_editable_fields` contains pk + every `editable=False` column | Wrong set → false 403s on `id` |
| 4.21 | `add_permission_checks` decorator preserves `__name__` / `__module__` | Wrong → error traces show a wrapper class instead of the real serializer |
| 4.22 | `PermissionAwareSerializerMetaclass` auto-injects the mixin on LexModel-backed serializers | Plain Django-model serializers untouched |
| 4.23 | `run_validation` — change detection | PATCH with same value as stored on a denied field passes validation (the frontend's "send the whole form back" pattern must not false-403) |
| 4.24 | `run_validation` — changed denied field raises `PermissionDenied` | Message names the field; non-superuser denied, superuser allowed |
| 4.25 | `lexReservedMeta` key bypasses the check | Real `public_name` change still goes through normally |
| 4.26 | `run_validation` — create path | Regular user POSTing `ProtectedItem` gets `PermissionDenied` (model name in the message); admin passes |

**Status:**  Complete — 8 pass / 0 fail. See progress.md Sessions 17 + 19.

### 4i. `LexModel` permission helper convenience methods ✅

**Gap:** `lex/core/models/LexModel.py` ships seven public **shorthand helpers** customers compose inside their own `permission_read` / `permission_edit` overrides — `allow_all_if_superuser`, `allow_all_if_in_groups`, `allow_fields_if_owner`, `keycloak_fallback`, `allow_all_except_sensitive`, `allow_public_fields`, `allow_basic_fields` — plus six **legacy `can_*(request)` adapters** (`can_read` / `can_edit` / `can_export` / `can_create` / `can_delete` / `can_list`) for back-compat with pre-`PermissionResult` customer code. Cluster 4a–4h test the *outcome* of permission overrides through HTTP, but never directly pin the helpers' input/output contract. A drift in any helper silently weakens every customer model that composes it: returning `False` instead of `None` from an "intermediate" helper breaks the documented `or`-chain pattern; returning the wrong field set from `allow_public_fields` leaks PII.

**Why a sub-cluster of 4 (Permissions):** these helpers compose with the same `UserContext` / `PermissionResult` building blocks 4a–4h test against. They are not their own feature — they are convenience shortcuts inside the existing permission API.

**Scenario numbering** runs **4.27 – 4.34** in the free band between 4f's last (4.26) and 4h's first (4.40).

**Models needed:**
- `ProtectedItem` (reused) — every helper that doesn't need an FK runs against this lightweight fixture.
- `FieldLevelItem` (reused) — drives `can_read`'s `Set[str]` collapse from a real `permission_read` override that returns `allow_fields(...)`.
- `OwnedItem` (new) — adds an `owner` FK to `auth.User` so `allow_fields_if_owner`'s ownership check is exercised against a real ORM lookup.

**Test scenarios:**

| # | Scenario | What We Assert |
|---|----------|----------------|
| 4.27 | `allow_all_if_superuser` | Superuser → `allow_all` with documented reason; non-superuser → `None` so the caller falls through; custom `reason` propagates |
| 4.28 | `allow_all_if_in_groups` | Bare-string argument is normalised to a one-element set; any overlap of user's groups with the required set allows; no overlap returns `None`; default reason mentions the matched groups |
| 4.29 | `allow_fields_if_owner` | Owner + explicit `fields=` → `allow_fields(...)`; owner + `excluded_fields=` → `allow_all_except(...)`; owner + neither → `allow_all`; non-owner returns `None`; unauthenticated short-circuits *before* the FK lookup; `owner_field` pointing at a missing attribute returns `None` (never raises) |
| 4.30 | `keycloak_fallback` is the **terminal** helper | Scope present → `allow_all`; scope missing → `deny` (not `None` — terminal helpers never return `None`); unrelated scope (e.g. `write` for a `read` check) does not satisfy |
| 4.31 | `allow_all_except_sensitive` | No-arg call uses the documented PII default set (`password`, `ssn`, `credit_card`, `bank_account`, …); explicit `sensitive_fields=` *replaces* the default rather than extending it |
| 4.32 | `allow_public_fields` / `allow_basic_fields` | Returns the documented allowlist (`{id, name, title, description, created_at, edited_at, updated_at}` for `public`; `{id, name, email, created_at}` for `basic`) — locks the customer-facing constant against accidental drift |
| 4.33 | Helper composition contract | Every "intermediate" helper returns `None` (not `False`, not a denied `PermissionResult`) when inapplicable, so the documented `allow_X() or allow_Y() or keycloak_fallback()` one-liner short-circuits at the first match. The `or`-chain is exercised end-to-end and the short-circuit is asserted. |
| 4.34 | Legacy `can_*(request)` adapters | Field-returning adapters (`can_read` / `can_edit` / `can_export`) collapse a `PermissionResult` to a `Set[str]` of allowed field names; boolean adapters (`can_create` / `can_delete` / `can_list`) return the predicate's `bool` directly. Drives both a regular user and a superuser through `FieldLevelItem` / `ProtectedItem`. |

**Status:** ✅ Complete — 26 pass / 0 fail. Covers `lex/test_project/tests/permissions/test_4i_permission_helpers.py`.

### 12f. Serializer write paths — M2M & nested FK 

**Gap:** `lex/api/serializers/base_serializers.py` still had ~108 missing stmts on the write side — the M2M and FK-nested branches. 12f closes them with three end-to-end scenarios driving real POST/PATCH against the One endpoints.

**Models:** `TagItem` + `TaggableItem` (M2M `tags` + nullable FK `primary_tag`) in `serializers/models.py`.

> **Scenario numbers:** the originally-planned 12.26–12.28 slots were reassigned to 12e factory-contract scenarios (canonical per cluster 12). 12f was renumbered to **12.29–12.31** in the April 23 session.

| # | Scenario | What We Assert |
|---|----------|----------------|
| 12.29 | POST creates M2M through rows atomically | Through-table read back via ORM (not trusting the serializer echo) |
| 12.30 | PATCH with a different tag set **replaces** (not merges) | Guards the frontend's deselect UX from silently regressing |
| 12.31 | Nullable FK lifecycle | Attach-on-create → rewire via PATCH → detach to NULL |

**Status:**  Complete — 3 pass / 0 fail. See progress.md Sessions 18 + 29.

### 10e. Schema introspection — `create_field_info` + structure-tree pruning 

**Gap:** `ModelStructureObtainView.py` (102 stmts, 21.54% baseline) and `model_info/Fields.py` (67 stmts, 22.35% baseline) drive every frontend form + nav menu. A drift here renders the wrong widget for a field, or leaks denied models into the nav.

**Models:** new `SchemaFKTarget`, `SchemaItem` (one field per interesting type), `SchemaHiddenItem` (`permission_list → False`) in `api_layer/models.py`.

| # | Scenario | What We Assert |
|---|----------|----------------|
| 10.11 | Django field → API-type mapping | `name → string`, `amount → int`, `ratio → float`, `active → boolean`, `day → date`, `when → date_time`, `payload → json`, `target → foreign_key`. Table-driven — a new type trips a named `subTest` failure |
| 10.12 | `editable` / `required` / `default_value` / `is_pk` flags | AutoField pk is `is_pk=True` + `editable=False`; field with `default=` is `required=False`; **surfaces BUG-015** — `CharField` without explicit default reports `required=False` because Django's `get_default()` returns `""` |
| 10.13 | FK metadata exposes `target` | `target == related_model._meta.model_name` — frontend uses it to fetch dropdown values |
| 10.14 | `delete_restricted_nodes_from_model_structure` prunes denied models | Folders that only contained denied models are collapsed; nav must not show empty folders |

**Status:**  Complete — 4 pass / 0 fail (+ BUG-015 surfaced, Open). See progress.md Sessions 18 + 20.

### 10f. Global search — `Search.py` 

**Gap:** 28 stmts, baseline **34.21%**. Small surface, user-facing — the nav-bar search box hits this endpoint.

**Models:** reuses `SchemaItem` from 10e (a varied-field model — `SearchVector` indexes the `name` CharField).

Shipped 4 scenarios. The view depends only on `self.model_collection` + `self.kwargs['query']`, so the tests build a `SimpleNamespace(all_containers=[...])` stand-in and drive `Search.get` directly — no URL wiring required. `UserPermission` is patched open; the exclusion-list contract is asserted independently.

| # | Scenario | What We Assert |
|---|----------|----------------|
| 10.15 | Query matches a text field on a registered model | Response is `{data, total}`; hit has `id` / `model` / `content.description` / `url` (routing) |
| 10.15b | Zero matches returns the documented `"No match found"` sentinel string | Frontend branches on response type — drift would silently break the search box |
| 10.16 | Container whose `id` is in `EXCLUDED_MODELS` (`user`, `permission`, …) short-circuits *before* the query runs | No PII leak through global search even if system rows contain the query term |
| 10.16b | `EXCLUDED_TYPES` still contains every non-text field type (`FloatField`, `BooleanField`, `IntegerField`, `FileField`, `ForeignKey`) | Regression gate — if a non-text type slips out, `SearchVector` 500s at runtime |

**Status:**  Complete — 4 pass / 0 fail.

### 5.11 — History fallback-snapshot path 

**Gap (April 25):** `History.py` lines 180–201 — the per-field manual-serialization branch inside `_get_snapshot` that fires when a model-container has no registered `serializers_map['default']`. The existing 5c scenarios never hit it because every test-project LexModel ships a default serializer.

**Shape:** `SimpleTestCase` that drives `_get_snapshot` directly with a synthetic history record (a dynamically-built class whose `_meta.fields` yields `.name`-carrying fakes). Covers five branches in one scenario with named sub-assertions so regressions surface the exact drift.

| # | Scenario | What We Assert |
|---|----------|----------------|
| 5.11 | Fallback snapshot contract | **(a)** `CONTROL_FIELDS` (`history_id` / `valid_from` / `sys_from` / `meta_task_status` / …) filtered out even when populated — frontend must not see system columns inside the business payload; **(b)** `datetime` → `isoformat()`; **(c)** `date` → `isoformat()`; **(d)** non-primitive object coerced via `str()` so DRF's JSON encoder doesn't blow up; **(e)** primitives (`int` / `bool` / `None`) + containers (`list` / `dict`) pass through unchanged |

**Status:**  Complete — 1 pass / 0 fail.

### 9.7 – 9.10 — Bitemporal suppression guards 

**Gap (April 25):** the three `ContextVar`-backed suppression guards (`suppress_main_table_sync`, `suppress_history_valid_to_chaining`, `suppress_meta_sys_to_chaining`) are consulted by every handler in the file — early-return at lines 118, 274, and the Level-2 meta-chaining guard. A drift in their lifecycle is how the BUG-011 chaining bottleneck compounds (leaked True → recursion; cross-contaminating state → wrong handler skipped).

Direct handler coverage of lines 170–340 would need a full history fixture (already exercised happy-path by 5a/5b/5c). This sub-cluster locks down the **suppression primitives** those handlers lean on.

**Shape:** `SimpleTestCase` — pure Python, no DB, no models. Runs in 1ms.

| # | Scenario | What We Assert |
|---|----------|----------------|
| 9.7 | Guard lifecycle (before / inside / after) for all three guards | `ContextVar` defaults False; flips True on enter; resets False on exit. A leaked True across request boundaries silently skips bitemporal maintenance. |
| 9.8 | Nested suspension stacks and unwinds | Inner `with` exit does **not** deactivate the outer context — this is what the handlers' internal `with suppress_*(): record.save(...)` depends on to avoid unbounded recursion |
| 9.9 | Three guards are independent | Suspending `main_table_sync` must not suspend `valid_to_chaining` or `meta_sys_to_chaining` — the handlers rely on asymmetric combinations |
| 9.10 | Suspension is thread-local | Background thread sees `False` even while the parent thread holds a suspension — guarantees Celery-worker parallel requests don't silently share suspension state |

**Status:**  Complete — 4 pass / 0 fail.

### 9d — `ActiveCalculationStateStore` full surface (coverage-driven — May 12)

**Gap (May 12):** `lex/core/signals/ActiveCalculationStateStore.py` baseline **27.03%** (131 stmts, 86 missed). Two tests exist (9a/9b) but only exercise the store transitively through the `update_calculation_status` signal — the public accessors, the DB-validated `snapshot()` reconciliation path that the WebSocket consumer calls on every reconnect, the startup `validate_and_prune()` sweep, and the private model-resolution helpers (`_resolve_model_and_pk` / `_split_record_id` / `_find_model_by_name`) were all dark.

**Why it matters:** this store is the single source of truth that lets a re-connecting browser tab pick up the spinner mid-calculation. The previous DatabaseCache implementation lost entries written inside `transaction.atomic()` because the ASGI consumer ran on a different DB connection — the bug whose fix this whole file exists to protect. Anything that breaks `snapshot()` (stale entries leaking through, live entries disappearing) directly regresses the customer-visible "did my calculation crash or am I just disconnected?" UX.

**Shape:** `SimpleTestCase` — pure Python, no DB. Models are unmanaged (`Meta.managed = False`); DB-touching paths are MagicMock-driven via `patch.object(ActiveCalculationStateStore, '_resolve_model_and_pk', …)`. 24 tests run in 0.009s.

| # | Scenario | What We Assert |
|---|----------|----------------|
| 9.11 | `mark_in_progress` with empty `record_id` is a no-op | Early-return guard; store stays empty (line 56) |
| 9.12 | `mark_in_progress` persists full payload | All 5 fields land verbatim; `int` pk normalised to `str` so JSON-serializable downstream |
| 9.13 | Optional fields default to `''` not `None` | `record` falls back to `record_id`; `calculation_id` / `model_label` / `record_pk` blank-string defaults — downstream consumers iterate values directly so `None` would force `or ""` everywhere |
| 9.14 | `clear('')` is a no-op | Symmetric early-return guard (line 71); existing entry untouched |
| 9.15 | `clear` removes entry and is idempotent | Second `clear` of same id silent — no `KeyError`, no log spam |
| 9.16 | `clear_all` empties every entry | Startup-only sweep; works regardless of size |
| 9.17 | `mark_in_progress` overwrites existing entry | Re-marking same id replaces prior entry (re-fire after ABORTED reset) |
| 9.18 | `get_calculation_id` returns string when set | Live entry → calc id string |
| 9.19 | `get_calculation_id` returns None for missing or blank | Both dark branches: dict.get default + `isinstance(...) and calculation_id` truthiness guard (line 88) |
| 9.20 | `get_entry('')` returns `{}` | Symmetric defensive guard (line 94) |
| 9.21 | `get_entry` returns a defensive copy | Mutating result must NOT affect store; pins `dict(entry)` copy (line 99) — regression to bare `return entry` would let snapshot consumers mutate under the lock |
| 9.22 | `_split_record_id` parses `model_pk` on rightmost `_` | Handles model names containing underscores (`my_calc_model_7` → `("my_calc_model", "7")`) |
| 9.23 | `_split_record_id` rejects malformed input | Empty / no underscore / blank halves all → `(None, None)` |
| 9.24 | `_find_model_by_name` walks app registry | Returns `CalculationModel` subclass when match exists; `None` for unknown names |
| 9.25 | `_resolve_model_and_pk` prefers explicit `model_label` | `app_label.ModelName` resolves via `apps.get_model` before `record_id` parsing |
| 9.26 | `_resolve_model_and_pk` falls back to `record_id` parsing | No `model_label` → split + walk app registry |
| 9.27 | `_resolve_model_and_pk` rejects non-`CalculationModel` classes | Without this guard `snapshot()` would call `.objects.filter(...)` on arbitrary models and could leak unrelated state into the WebSocket payload |
| 9.27b | `_resolve_model_and_pk` returns `(None, None)` on full resolution failure | Empty entry early-out + `apps.get_model` raise → registry-walk fallback that also fails — no exception bubbles |
| 9.28 | `snapshot()` empty-store fast path | No entries → `[]` returned without DB hit |
| 9.28b | `snapshot()` returns live entries and prunes stale | Live IN_PROGRESS pass through; terminal-state entries dropped from BOTH the payload and the store — the WebSocket reconciliation contract |
| 9.28c | `snapshot()` keeps entry on DB exception | Defensive: better a possibly-stale spinner than a silently-dropped live calculation on a DB blip |
| 9.28d | `snapshot()` skips DB validation when resolver returns `(None, None)` | Unresolvable entry passes through unchecked; pins the `if model_class is not None and record_pk is not None` guard |
| 9.28e | `validate_and_prune()` keeps only IN_PROGRESS rows | Empty-store fast-path safe; stale (terminal state) / gone (instance None) / unresolvable all dropped |
| 9.28f | `validate_and_prune()` drops entry on DB exception | Documented behavioural difference from `snapshot()` — startup sweep is conservative-rebuild ("only keep what we can positively confirm"); a regression that adds "keep on exception" would have to revisit this test |

**Status:**  Complete — 24 pass / 0 fail / 0.009s. Coverage: 27.03% → ~95%+ (whole file minus 1-2 unreachable defensive branches).

### 9e — Generic CRUD mutation broadcast (live list refresh — June 3)

**Gap (June 3):** the framework broadcast *calculation* state changes over WebSocket (frontend listens, refreshes the AG Grid), but ordinary CRUD on a **non-`CalculationModel`** record emitted **no** WebSocket traffic at all. A list view open in another tab, iframe, or window silently went stale — the customer had to press "Refresh" to see a newly created/updated/deleted row. New surfaces: a generic `model_data_update`-group `record_mutation` broadcast emitted from every REST mutation entry point, and the consumer that fans it out.

**Covers:** `lex/core/signals/ModelMutationSignal.py` (new — `broadcast_model_mutation` defers a group send via `transaction.on_commit`), `lex/api/consumers/ModelDataUpdateConsumer.py` (new — joins `model_data_update`, forwards verbatim), `lex/lex_app/routing.py` (route `ws/model_data_update`), `lex/api/views/model_entries/One.py` (create/update/destroy; skips the generic broadcast when `calculate=true`), `lex/api/views/model_entries/Many.py` (bulk patch/delete).

**Shape:** U (helper message-shape) + I (`TestCase` — on_commit deferral, empty-name no-op) + U (`SimpleTestCase` consumer) + E (`E2ETestCase` — real REST CRUD end-to-end; `TransactionTestCase` so commits fire `on_commit`).

| # | Scenario | What We Assert |
|---|----------|----------------|
| 9.29 | `build_model_mutation_message` envelope | type `record_mutation`; payload carries `model_name`/`action`/`record_id` so the frontend can match the open resource |
| 9.30 | Empty `model_name` is a no-op | defensive guard — nothing ever reaches the channel layer |
| 9.31 | Broadcast deferred until commit | 0 sends before commit, exactly 1 to `model_data_update` after — emitting early would let a client refresh and miss the just-written row |
| 9.32 | Consumer joins group + forwards payload | `connect` adds to `model_data_update`; `record_mutation` forwards JSON to the socket verbatim |
| 9.33 | POST create emits `created` | real REST create broadcasts a `record_mutation` for the model |
| 9.34 | PATCH update emits `updated` | real REST update broadcasts |
| 9.35 | DELETE emits `deleted` | real REST delete broadcasts |
| 9.36 | Bulk DELETE (`Many` endpoint) emits `deleted` | bulk path broadcasts too |

**Status:** Complete — 8 pass / 0 fail locally (Postgres test DB available).

### 7g — `CalculatedModel.create()` pipeline (end-to-end) 

**Gap (April 25):** `CalculatedModelMixin.py` baseline **33.74%** after 7a–7f. The remaining 369 missing statements were concentrated in the four-step orchestrator invoked by `Model.create(**overrides)`:

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

**Status:**  Complete — 6 pass / 0 fail. Drove `CalculatedModelMixin.py` from **33.74% → 64.75%** (+31 pts, +170 lines covered).

### 1i. Initial-data upload — full journey end-to-end 

**Gap (April 24):** `InitialDataAuditLogger` (148 stmts, **12.64%** baseline) + `ProcessAdminTestCase` seed walker (`replace_tagged_parameters`, `get_test_data_from_path`, seed dispatcher). Never driven end-to-end by Cluster 1c — 1c only asserts post-`Init` database state, not the intermediate audit trail or the JSON-walker contracts.

**Intent** (per `docs/lex_topics/16-initial-data-upload.md`): seed files declare production-shaped data in JSON; on server start if every referenced model is empty, the framework walks the JSON top-down and applies `create` / `update` / `delete` actions in declaration order. `tag:` prefixes resolve to in-memory objects created earlier; `datetime:` strings parse through `dateutil`; `{"subprocess": path}` entries flatten recursively. Every operation emits an `AuditLog` + `AuditLogStatus(pending)` pair; `mark_operation_success/failure` advances the status; `finalize_batch` sweeps lingering pendings so the compliance view is always consistent.

**Models:** reuses `SimpleItem` from `crud_api/models.py` — a minimal LexModel with `name` + `value`. **Fixtures** (all new in `tests/fixtures/`): `seed_parent.json` (2 subprocess refs), `seed_child_01.json` (1 create), `seed_child_02.json` (2 creates), `test_seed_journey.json` (2 creates + 1 update + 1 delete).

**Shape:** five test classes — two `SimpleTestCase` (pure unit, no DB) + three `E2ETestCase` (real `AuditLog` / `AuditLogStatus` / `SimpleItem`). `ProcessAdminTestCase.setUp` is driven via a `runTest = lambda: None` sub-class and a monkey-patched `get_test_data`; `apps.get_app_config` is stubbed so the harness sees `SimpleItem` without registering the test project as an installed app.

| # | Scenario | What We Assert |
|---|----------|----------------|
| 1.51a | `tag:foo` resolves to previously-stored in-memory object | FK-by-reference mechanism — literals without a known prefix pass through unchanged |
| 1.51b | `datetime:YYYY-MM-DD` parses via `dateutil.parser` | Seed dates ship as strings; models receive real `datetime` |
| 1.52 | Recursive subprocess flattening preserves declared + internal order | Parent-before-child FK resolution depends on this ordering |
| 1.54 | `log_object_creation` writes AuditLog + pending AuditLogStatus | `author = "system (initial_data_upload)"`, `_audit_tag` in payload for trace-back |
| 1.54b | `_logged_ids` tracks every log in declaration order | `finalize_batch` sweep is scoped by this list — must not touch earlier sessions |
| 1.55 | `log_object_update` embeds instance pk in payload | Compliance view links audit row → DB row |
| 1.56 | `log_object_deletion` snapshots the instance via `generic_instance_payload` | Deleted row's fields survive in the audit trail after the main row is gone |
| 1.57 | `mark_operation_success` / `mark_operation_failure` advance status; idempotent | Retry must not duplicate status rows |
| 1.57b | `mark_operation_*` on `None` audit log is a no-op | The loader passes whatever `log_object_creation` returned — can be `None` |
| 1.58a | `finalize_batch()` clean run resolves lingering pending → success | Summary reports `pending_resolved` count (signal that a handler forgot to mark) |
| 1.58b | `finalize_batch(failure_error=…)` collapses pending → failure with error string | Outer-driver exception path — audit trail closes out even for aborted runs |
| 1.59 | Full create/update/delete drive with audit (env-gated) | DB has correct surviving row; 4 AuditLog rows in `[create, create, update, delete]` order; all statuses `success` |
| 1.59b | Full journey without audit still lands DB state | Audit is observability — disabling it must not regress data transitions |
| 1.60 | Crash path — `finalize_batch(failure_error=…)` after 3 pending ops | All 3 swept to `failure` with error string; `pending_resolved == 3` |

**Status:**  Complete — 13 pass / 0 fail / 1 env-gated skip (1.59). Targets ~140 lines in `InitialDataAuditLogger.py` + ~40 in `ProcessAdminTestCase`.

### 8j. Celery task bodies — `load_data` / `calc_and_save` / `activate_history_version` 

**Gap (April 24):** 8g–8i intentionally kept Celery tests broker-free so the normal suite stays deterministic: patched `.delay`, eager mode, and direct task-body invocation cover the Lex framework logic without requiring Redis. That leaves one environment-level risk unpinned: a real Celery producer must be able to publish to Redis, a worker must consume from Redis, and the result backend must return the payload.

**Shape:** two opt-in scenarios in `test_8k_redis_broker_integration.py`. The first is a `SimpleTestCase` JSON-safe smoke task that switches the Celery app to a Redis broker/result backend, starts an in-process Celery worker with `celery.contrib.testing.worker.start_worker`, publishes to a unique queue, and calls `AsyncResult.get()` through `allow_join_result()`. The second is an `E2ETestCase` using a real `CalculationModel` fixture and `WaitForTasks`; it dispatches the decorated bound `calculate()` method over Redis, blocks on the returned `AsyncResult`, and asserts `CallbackTask.on_success` persists `SUCCESS` plus reaches the terminal audit seam. Both producers pass an explicit temporary Redis connection so Celery app producer-pool caching cannot leak a previous broker URL into the example.

**Environment gate:** skipped by default. To run it, set `LEX_RUN_REDIS_CELERY_TESTS=true`; optionally set `LEX_CELERY_REDIS_TEST_URL` (default `redis://127.0.0.1:6379/15`). This keeps CI/laptops without Redis green while still giving DevOps a one-command real-broker check.

| # | Scenario | What We Assert |
|---|----------|----------------|
| 8.45 | Producer → Redis broker → in-process Celery worker → Redis result backend | Task is received and executed by the worker, result round-trips with a correlation id, and `task_always_eager` is false so this is not the eager-mode path. |
| 8.46 | `CalculationModel` + `WaitForTasks` over Redis broker | A persisted `CeleryCalc` in `IN_PROGRESS` dispatches via the real `EnhancedBoundTaskMethod`/`WaitForTasks` path to Redis, the worker runs the decorated `calculate()` task, `WaitForTasks` drains the `AsyncResult`, `CallbackTask.on_success` flips the row to `SUCCESS`, and terminal audit is invoked. |

**Status:**  Complete — 2 broker-backed passes when Redis is available; env-gated skips otherwise. The reusable `celery_redis_broker_example.yml` workflow runs the examples with PostgreSQL + Redis services and is called by `pip_publish.yml` before PyPI publishing.

### 1j. Keycloak client safety pre-flight — mocked 

**Gap (April 25):** `lex init` mutates the configured Keycloak client's resources / policies / permissions. Without a pre-flight gate, an operator who points the framework at a STANDARD or production client by accident silently rewrites authorization config — the very accident the controller's `is_confidential` + `client_type="DEVELOPMENT"` invariants exist to prevent on the create side.

**Shape:** `TestCase` with mocked `kc_manager` (same `_make_sync_manager()` pattern as 1e / 1g — bypass `__init__`, stub `kc_manager.admin`).

| # | Scenario | What We Assert |
|---|----------|----------------|
| 1.71 | Confidential + localhost redirect passes | Returns the rep; `admin.get_client` called once with `client_uuid` |
| 1.72 | `http://localhost:8000/*` is a localhost redirect | Port number doesn't matter — only the parsed hostname does |
| 1.73 | Only-localhost (no prod URI) is still a valid dev client | DEV-only test fixtures are not penalized |
| 1.74 | `publicClient=true` raises | Message names the client + 'confidential'; mentions `publicClient=true` |
| 1.75 | Missing `publicClient` flag is treated as unsafe | Never assume confidential by default |
| 1.76 | Prod-only `redirectUris` raises | Message names DEVELOPMENT + localhost + offending URI + clientId |
| 1.77 | Empty `redirectUris` raises with `<empty>` sentinel | Operator can tell the list was blank vs. populated-but-wrong |
| 1.78 | Missing `redirectUris` field raises | Malformed rep is rejected, not assumed-empty |
| 1.79 | `redirectUris` set to a string raises | Type check is real, not duck-typed |
| 1.80 | `localhost.example.com` does NOT match | Proves we use parsed `.hostname`, not substring |
| 1.81 | `_redirect_uris_indicate_development` accepts every dev shape | Case-insensitive host matching across http/https + ports |
| 1.82 | Helper rejects production / 127.0.0.1 / look-alike hosts | Loopback IP is NOT 'dev' — controller only emits literal `localhost` |
| 1.83 | Helper skips non-string entries (None, int, dict) | Mixed list with one valid localhost entry still passes |
| 1.84 | `KEYCLOAK_DEV_REDIRECT_HOST == "localhost"` is pinned | Any change is deliberate (touches both halves of the contract) |
| 1.86 | Empty `client_uuid` raises BEFORE any HTTP call | `admin.get_client.assert_not_called()` |
| 1.87 | `admin.get_client` raising → `CommandError` wraps + chains | `__cause__` is the original exception |
| 1.88 | Non-dict response shape raises | Defensive against SDK contract drift |
| 1.89 | Failing pre-flight aborts `init` BEFORE `process_model_changes` | `mgr.process_model_changes.assert_not_called()` |
| 1.90 | `--skip-client-preflight` short-circuits pre-flight | `admin.get_client.assert_not_called()`; stdout names the flag; sync still runs |

**Status:**  Complete — 20 pass / 0 fail in 0.007s. See progress.md Session 38.

### 1k. Keycloak client safety pre-flight — REAL Keycloak integration 

**Companion to 1j.** Drives `verify_client_is_safe_for_init` against a **live** Keycloak server using credentials from repo secrets / `os.environ` or the gitignored `lex/test_project/tests/init/.env` file — no mocks, no canned responses, every assertion bottoms out in an HTTP round-trip. Only a live server actually proves: (a) `KeycloakManager` initialization works end-to-end with the configured token endpoints; (b) the admin REST API actually accepts the response shape we parse; (c) the configured client's `publicClient` + `redirectUris` round-trip verbatim across the SDK boundary.

**Gating:** TWO levels — (a) `LEX_RUN_KEYCLOAK_INTEGRATION=1` must be set to enable; (b) the configured client must satisfy the 1j pre-flight (confidential + DEVELOPMENT) so the integration tests cannot be turned on against a production client by accident. Both gates fail-closed — the tests skip rather than error when the env is incomplete.

**Shape:** `TestCase` with the real `KeycloakManager` SDK; 4 read-only scenarios covering happy-path verification, live representation shape, env-var round trip through dotenv/repo-secret injection, and the pinned localhost dev-host constant.

**Status:**  Complete — 4 env-gated integration tests, all skip cleanly without live Keycloak, all pass against the configured dev tenant when integration env is wired. See progress.md Session 38.

### 1l. `lex init` full pipeline — REAL Keycloak integration 

**Companion to 1b (mocked `lex init` end-to-end) and 1f (Keycloak drift coverage with stubbed manager).** Drives the **same code path the real `lex init` command runs** against a live Keycloak server. Mocked tests cover *contract*; live tests prove three things only a real server can: (1) the Keycloak admin REST API actually accepts the payloads `KeycloakSyncManager` builds (schema drift on Keycloak's side fails here before production); (2) end-to-end timing works (token refresh, multi-call sequences, no race against the authz-import endpoint); (3) `last_authz_import_error` round-trips to `None` on success — what `Command.handle` actually checks.

**Gating:** TWO levels — (a) `LEX_KEYCLOAK_INTEGRATION=1` must be set to enable; (b) the configured client must satisfy the 1j pre-flight (confidential + DEVELOPMENT) so the integration tests cannot be turned on against a production client by accident. Both gates fail-closed — the tests skip rather than error when the env is incomplete.

**Shape:** `E2ETestCase` with real `KeycloakManager` + real `KeycloakSyncManager`; 7 scenarios driving `Command.handle` end-to-end across happy-path full sync, `--dry-run` no-op, drift recovery, idempotent rerun, snapshot/restore round-trip, `--skip-client-preflight` against real client, and `last_authz_import_error → None` assertion.

**Status:**  Complete — 7 env-gated integration tests, all skip cleanly without live Keycloak, all pass against the configured dev tenant when integration env is wired. See progress.md Session 38.

### 1m. `lex` CLI ↔ PyCharm `.run.xml` cross-file contract 

**Gap (April 25):** `generate_pycharm_configs.py` writes 16 `.run/*.run.xml` files an operator can click in PyCharm; each invokes the `lex` CLI with a specific subcommand. 1a covered three of those files (Init / Start / Streamlit); per-builder helpers in `lex/tests/unit/cli/test_lex_cli.py` validate Celery / Flower / MCP individually; nothing asserted "every PyCharm-clickable subcommand actually resolves through the CLI". A rename in either file would silently break a PyCharm action — caught only by a developer trying to use it.

**Shape:** `SimpleTestCase` only — fast, in-process (Click's `CliRunner`, no subprocess), Django bootstrap once per class for the dynamic-command lookup. Scaffolds `.run/` into a per-test `TemporaryDirectory` so the live project's `.run/` is never touched.

| # | Scenario | What We Assert |
|---|----------|----------------|
| 1.102 | Generated `.run.xml` set parity | Exactly the 16 expected files, no orphans, no missing — canary against "removed Test_Audit but constant still has it" drift |
| 1.103 | Every `.run.xml` `SCRIPT_NAME` is `lex` | A copy-paste of a different binary cannot bypass the CLI's env handling / Django bootstrap |
| 1.104 | First-token of every `.run.xml` `PARAMETERS` resolves | Either explicit `@lex.command(...)` Click handler OR registered Django management command — the cross-file contract that nothing else asserts |
| 1.105 | Explicit Click registry pinned | `celery` / `celery-workers` / `flower` / `streamlit` / `start` / `setup` / `setup-with-ai` / `ai-update` / `ai-faq` — removing one is what would silently break a `.run.xml` |
| 1.106 | `_SKIP_BOOTSTRAP_COMMANDS` is a subset of explicit registry | Otherwise listed names silently fall through to dynamic forwarding without `django.setup()` |
| 1.107 | `lex --help` exits 0 and names every explicit command | Click group itself wired correctly |
| 1.108 | `lex <cmd> --help` exits 0 for every explicit handler | Catches decorator typos / signature regressions that `--help` surfaces but real runs would mask |
| 1.109 | Every Django-side subcommand referenced by a `.run.xml` is registered | `init` / `migrate` / `makemigrations` / `flush` / `test` / `create_db` resolve through Django's command loader — otherwise dynamic forwarding produces a less-helpful error |

**Status:**  Complete — 8 pass / 0 fail in 0.020s. See progress.md Session 40.

### 5g. History `valid_to` chaining contract  — implemented

**Gap (May 5):** Cluster 5.4 documents the contract "`valid_to` of row N = `valid_from` of row N+1" but the implemented test only asserts ascending `history_id`. The chaining is the very thing that makes the bitemporal timeline contiguous (latest row carries `valid_to=NULL`); a regression here would silently produce gaps or overlaps in the timeline that no other test sees.

**Models:** reuses `HistSimpleItem` from Cluster 5.

| # | Scenario | What We Assert |
|---|----------|----------------|
| 5.61 | Three saves chain `valid_to → valid_from` end-to-end | For an ordered list of 3 history rows, `rows[0].valid_to == rows[1].valid_from`, `rows[1].valid_to == rows[2].valid_from`, `rows[2].valid_to is None` (latest row open-ended) |
| 5.61b | Delete closes the chain | After `delete()`, the `-` row's `valid_from` matches the previous row's `valid_to`; the `-` row's `valid_to` is `None` |

**Status:**  Implemented (Session 51). 5.61 + 5.61b both pass. See progress.md Session 51.

### 5h. History suppression toolkit (per-instance, per-save, bulk, model-level, calculation-level)  — implemented

**Gap (May 5):** `docs/features/tracking/history.md` + `bitemporal history.md` document **five** distinct suppression toggles. Cluster 5.5 covers exactly one (`skip_history_when_saving`). The remaining four — each customer-facing — are dark.

**Models:** reuses `HistSimpleItem`; one new `UntrackedItem` model with `skip_history_when_saving = True` baked in via `untracked_models` (deferred).

| # | Scenario | What We Assert |
|---|----------|----------------|
| 5.62 | `obj.save_without_historical_record()` | Single save with no history row appended; subsequent normal `.save()` resumes history (proves it's a single-save toggle, not a sticky flag) |
| 5.63 | `obj.untrack()` followed by `obj.save()` then `obj.track()` then `obj.save()` | First save produces no history row, second save produces a `~` row — proves the toggle is sticky between calls and `track()` re-enables |
| 5.64 | `Model.objects.bulk_create(objs, skip_history=True)` | N rows persisted, 0 history rows; subsequent `.save()` on one of those rows then produces a `~` history row (catching the case where `bulk_create` would otherwise leave the instance permanently untracked) |
| 5.65 | `bulk_create` without `skip_history` | Documented bulk-path behaviour: per-row history rows ARE created (this is the "make sure the default still works" gate) |
| 5.66 | `with suspend_bitemporal(): obj.save()` | Inside the block: zero L1 rows, zero L2 rows, exactly 1 raw INSERT/UPDATE; outside the block: full bitemporal chain runs again. Pins the documented "1 query inside, normal cost outside" contract from `bitemporal history.md` |
| 5.67 | `untracked_models` declared in `model_structure.yaml` | No `Historical*` table generated for the model; `model.history` manager raises / returns no rows. ⏸ deferred — needs a fresh test project with `model_structure.yaml`-loaded config to avoid mutating the live test_project model registry |

**Status:**  Implemented (Session 51). 5.62–5.65 pass; 5.66 (`suspend_bitemporal()` CM) tracked as `@expectedFailure` — docs reference it but only the lower-level guards (covered by 9.7–9.10) are exposed today; 5.67 deferred (fixture-shaped). See progress.md Session 51.

### 5i. History API contract — response shape + `as_of` time-travel  — implemented

**Gap (May 5):** Cluster 5.9 only asserts `200 OK + ≥3 rows`. The documented JSON contract from `bitemporal history.md` is much wider, and the `?as_of=...` system-time time-travel branch (the entire reason MetaHistory exists) is uncovered. A silent contract drift here would break the History tab UI without tripping any existing test.

**Models:** reuses `HistSimpleItem`; new helper `UserHistItem` with a `history_user` FK so the actor is observable on the response.

| # | Scenario | What We Assert |
|---|----------|----------------|
| 5.71 | `GET /history/` response shape | Each row carries `history_id`, `valid_from`, `valid_to`, `history_type`, `user` (`{id, email, name}` or `null`), `snapshot` (full field map), `system_history` (list of L2 records) — exactly the keys documented in `bitemporal history.md` |
| 5.72 | `get_queryset_as_of(Model, t)` — valid time | Returns history rows where `valid_from <= t AND (valid_to > t OR valid_to IS NULL)`; pre-`t` and post-`t` rows are filtered out |
| 5.73 | `get_queryset_as_of(HistoryModel, t)` — system time | Auto-detects history-model class, returns L2 meta rows with `sys_from <= t AND (sys_to > t OR sys_to IS NULL)` — answers "what did the system *believe* was true at t" |
| 5.74 | `GET /history/?as_of=2026-02-01T00:00:00Z` | Endpoint returns the L2 snapshot at that system time (the As-Of UI control's contract). Asserts the rows match the `get_queryset_as_of(HistoryModel, t)` set from 5.73 |

**Status:**  Implemented (Session 51). 5.71/5.72 pass; 5.73/5.74 (system-time `as_of` + `?as_of=...` REST branch) auto-skip on missing L2 fixture (covered at the unit level by `lex.tests.unit.api.test_history_endpoint` + `lex.tests.unit.infra.test_bitemporal_service`). See progress.md Session 51.

### 5j. History snapshot completeness + `history_user` actor  — implemented

**Gap (May 5):** Docs guarantee each L1 row carries every field's value at that moment. No test asserts this — only counts and types. Same for `history_user`: docs say "ForeignKey(User) — Who made the change" but the API path actor is not pinned.

**Models:** `HistSimpleItem` (existing) + `UserHistItem` (small `LexModel` with one tracked field, used to inspect `history_user` after API saves).

| # | Scenario | What We Assert |
|---|----------|----------------|
| 5.75 | After update, the new history row carries every model field's value | For a 4-field model, the `~` row has all 4 field values matching the post-update state; the prior `+` row has all 4 matching the pre-update state — proves the snapshot is full, not a diff |
| 5.76 | API-driven save stamps `history_user` to the authenticated user | POST + PATCH via `force_login`'d user → `item.history.first().history_user_id == user.pk` (or `history_user.email` matches). The `history_change_reason` field is `None` by default — also pinned so a default change is caught |

**Status:**  Implemented (Session 51). 5.75 (full snapshot, not a diff) + 5.76 (`history_user` actor stamping on the API path) both pass. See progress.md Session 51.

### 5k. MetaHistory positive contract  — implemented

**Gap (May 5):** 9.7–9.10 cover the suppression *primitives* (ContextVars), but no test asserts that a save() actually *creates* a MetaHistorical row, that `sys_from`/`sys_to` chain, or that an `history_object` FK points back to L1. The full bitemporal signal chain documented in `bitemporal history.md` "How the Signal Chain Works" is therefore not gated.

**Models:** reuses `HistSimpleItem` from Cluster 5.

| # | Scenario | What We Assert |
|---|----------|----------------|
| 5.81 | Single save → exactly 1 L2 row | After `obj.save()` (create), `MetaHistoricalHistSimpleItem.objects.count() == 1`; the row's `history_object_id == obj.history.first().history_id`; `sys_to is None`; `meta_history_type == "+"` |
| 5.82 | Three saves chain `sys_to → sys_from` | Identical contract to 5.61 but on the L2 table — proves `chain_sys_to` runs |
| 5.83 | Retroactive `valid_from` correction (the docs example) | (a) save with default `valid_from=now`, (b) save again with `valid_from=earlier_date` — L1 has 2 rows with the new row chained into the timeline; L2 has 2 rows with `sys_from` reflecting the *clock time* of each correction (NOT the customer-supplied `valid_from`) |
| 5.84 | `meta_task_status` defaults to `NONE` for direct saves | Scheduled bitemporal activations bump it to `SCHEDULED → ACTIVE` (closing the read side of the contract `activate_history_version` writes against — see 8j scenario 8.43) |

**Status:**  Implemented (Session 51). 5.81/5.82/5.84 pass; 5.83 (retroactive `valid_from` correction) tracked as `@expectedFailure` — documented intent the framework does not yet accept on user-supplied saves. Companion to 8.43 — closes the producer side of the activation contract that the worker side already pins.

### 6d. Audit-log payload + GenericForeignKey contract  — implemented

**Gap (May 5):** 6.1/6.2/6.3 are thin: count + status only, with one `assertIn("value", payload)` for update. The Audit Log Tab UI's documented columns (`date`, `author`, `resource`, `action`, expandable JSON `payload`, link to record via `content_type` + `object_id`, `calculation_id` link to calc log) are mostly unpinned. **Critical:** without `content_type` + `object_id`, the per-record Audit Log Tab cannot find rows, but no test catches a regression.

**Models:** reuses `AuditSimpleItem` from Cluster 6.

| # | Scenario | What We Assert |
|---|----------|----------------|
| 6.41 | Create audit row → `content_type` + `object_id` populated | After `POST /api/<model>/`, `audit_log.content_type == ContentType.objects.get_for_model(AuditSimpleItem)` and `audit_log.object_id == created_pk`. The `calculatable_object` GFK resolves back to the row. Without these, the Audit Log Tab UI cannot list operations affecting a specific record |
| 6.42 | Create audit payload carries the *full* request body + the post-save `id` | `payload == {"name": ..., "value": ..., "id": <created_pk>}` — the documented "full data + id-after-save" shape (line 227–231 of `AuditLogMixin.py`) |
| 6.43 | Update audit payload is *refreshed to final state* on success | `audit_log.payload` after PATCH equals the *full GET-shape* serialized representation including every field, not just the patched ones (the line 260 `payload = self.get_serializer(instance).data` contract). This is what makes audit logs reconstructable into "what the row looked like after this change" |
| 6.44 | Delete audit payload preserves the deleted record's pre-delete state | After DELETE, `audit_log.payload` carries every field's value at the moment of deletion + `id`. Docs: "you can always inspect what was removed" — currently no test asserts this |
| 6.45 | Failed `pre_validation` on POST → failure audit row | `pre_validation` raises → response 400/500, `AuditLogStatus.status == 'failure'`, `error_traceback` contains the exception class name and message, no DB row created. Replaces the previously-skipped 6.4 — reachable today through validation hooks (`PreValidatedItem`-style fixture), no middleware needed |
| 6.46 | Failure audit traceback round-trips through `resolve_exception_traceback` | Multi-line traceback string preserved — operators need full diagnostic info, not just the exception message |
| 6.47 | Atomic-block failure queues a replacement audit row | When `perform_create` fails inside an atomic block (`transaction.get_connection().in_atomic_block`), the in-flight failure status row rolls back with the request, and `_pending_failed_audit_logs` carries the queued replacement so the request-level fallback can persist it. Pins the line 238–246 branch |
| 6.48 | Pending state observable mid-flight | A `perform_create` paused in the serializer save (e.g. via a `pre_save` signal that captures status mid-call) sees `AuditLogStatus.status == 'pending'`. Documents the documented  → / lifecycle from the Audit Log Tab |

**Status:**  Implemented (Session 51). 6.41–6.46 pass live — including 6.45/6.46 which were planned as `@expectedFailure` but the framework already writes the failure audit row through the validation-hook path, so they stand as live regression gates. 6.47/6.48 auto-skip on missing fixture (atomic-block reentrancy + mid-flight pending observation). See progress.md Session 51.

### 6e. Bulk audit logging — `BulkAuditLogMixin`  — implemented

**Gap (May 5):** Docs explicitly say "a bulk update of 100 records creates 100 audit log entries". Cluster 2e's bulk DELETE scenarios (2.23/2.24/2.25) never assert the audit row count. `BulkAuditLogMixin` (167 stmts) is dark.

**Models:** reuses `AuditSimpleItem`.

| # | Scenario | What We Assert |
|---|----------|----------------|
| 6.51 | `DELETE /many/?ids=1,2,3` → 3 audit rows | One audit row per deleted record, all with `action='delete'`, payload carrying the deleted row's pre-delete state, status `success` |
| 6.52 | Bulk delete with one denied / failing row | The deletable rows produce success audit entries, the failing row produces a failure entry — the partial-success contract |
| 6.53 | Bulk delete preserves per-row `content_type` + `object_id` | Each audit row's GFK points back to its own pre-delete instance — Audit Log Tab on each individual record still works after the bulk op |

**Status:**  Implemented (Session 51). 6.51 passes; 6.52 (audit row count under `bulk_create`) auto-skips on missing fixture. See progress.md Session 51.

### 6f. Audit-log resilience — deadlock retries + ContentType cache healing  — implemented

**Gap (May 5):** Docs "Resilience" section calls out two contracts. Both unpinned.

**Models:** reuses `AuditSimpleItem`.

| # | Scenario | What We Assert |
|---|----------|----------------|
| 6.61 | Save raises `OperationalError(pgcode='40P01')` once, then succeeds | Retried automatically up to `MAX_UPDATE_RETRIES` (3); final state is success; backoff seen via `time.sleep` patch (~0.05 / 0.10 / 0.20s exponential). Pins `_save_with_retry` + `_is_retryable_db_error` + `RETRYABLE_SQLSTATE_CODES = {"40P01", "40001"}` |
| 6.62 | Save raises `OperationalError(pgcode='40P01')` 3 times | Re-raised on the 4th attempt as the original exception; failure audit row written with the traceback |
| 6.63 | `safe_get_content_type` heals stale ContentType cache | Patch `ContentType.objects.get_for_model` to raise `ContentType.DoesNotExist` on first call, succeed on second → the helper invalidates the cache and retries; audit row's `content_type` ultimately populated. Critical post-migration: docs "if Django's ContentType cache goes stale (e.g., after a migration), the system detects and auto-corrects it" |

**Status:**  Implemented (Session 51). Deadlock retry contract pinned — `40P01`/`40001` retry 2× with exponential backoff, exhaustion re-raises with `pgcode` preserved, non-retryable errors propagate immediately. ContentType cache-healing split into input-validation + recovery halves. See progress.md Session 51.

### 6g. Audit-log immutability  — implemented

**Gap (May 5):** Docs `[!note]`: "Audit logs are effectively read-only. They are designed to be an immutable record of operations — only administrators should modify or delete them." `AuditLog`/`AuditLogStatus` permissions explicitly enforce this (`permission_create=False`, `permission_delete=False`, `permission_edit→deny`). No test pins these — a regression flipping any to `True` would silently allow audit tampering.

**Models:** Existing framework `AuditLog` / `AuditLogStatus`.

| # | Scenario | What We Assert |
|---|----------|----------------|
| 6.71 | POST `/api/auditlog/` returns 403 for non-admin | `permission_create` returns False → 403; no audit row created. Same for `AuditLogStatus` |
| 6.72 | DELETE `/api/auditlog/<id>/` returns 403 | `permission_delete` returns False even for admin (read-only by design); audit row preserved |
| 6.73 | PATCH `/api/auditlog/<id>/` returns 403 | `permission_edit` → `PermissionResult.deny(...)`, fields cannot be mutated; audit row preserved verbatim |

**Status:**  Implemented (Session 51). `AuditLog.permission_create == False`, `permission_delete == False` even for admin, `permission_edit` returns `PermissionResult(allowed=False)` with the documented "read-only" reason; sub-pin on `AuditLogStatus` so a regression flipping write access (allowing `failure → success` rewrites) is caught. See progress.md Session 51.

---

### 6o. `BulkAuditLogMixin._normalize_bulk_payloads` four-branch matrix (coverage-driven — May 12) — implemented

**Gap:** 6e (Session 51) only drove the API-level happy DELETE-many path through `BulkAuditLogMixin`. The static `_normalize_bulk_payloads` helper that drives every bulk-write payload normalisation — the bridge between DRF's bulk serializer and the per-row audit-write loop — had every other branch unexercised. A regression that mis-aligned payloads to targets would silently mis-attribute audit evidence to the wrong row, a compliance regression visible only when an investigator notices the payloads don't match the IDs.

**Models:** None (uses `SimpleNamespace` target stand-ins; `_attach_related_instance_id` patched to a transparent identity).

| # | Scenario | What We Assert |
|---|----------|----------------|
| 6.156 | `len(payloads) == len(targets)` → strict 1-to-1 zip alignment | DRF's bulk serializer produces one payload per instance; helper preserves the mapping. Regression that swapped to broadcast or single-serialize would silently apply the same payload to every target |
| 6.157 | `len(payloads) == 1` and `len(targets) > 1` → broadcast | Uniform "delete-with-reason" bulk ops; helper replicates the one payload across every target. Regression that zip-truncated would leave N-1 audit rows with empty payloads |
| 6.158 | Dict / scalar payload (not a list) → `_serialize_payload(...) or {}` then replicated | The "PATCH same fields on N rows" path; pinned to land the dict on every target's audit row, not just the first |
| 6.159 | Falsy serialised payload → `{}` fallback fires | Empty dict (`_serialize_payload({}) → {}` falsy) and empty list (`_serialize_payload([])` enters list branch with len 0 → fall-through, `[]` falsy) both trigger `or {}`. Without the guard, audit rows would land `payload=None`, masking bulk-write evidence. **Pins documented quirk**: None is NOT rewritten to `{}` because `_serialize_payload(None)` returns the string `"None"` (truthy) and the `or {}` skips — callers must pass `{}` explicitly. Plus mismatched-length list (3 entries / 2 targets) falls through to single-serialize semantics, replicating the whole list across every target — pin so a regression that silently truncated to `targets[:len(payloads)]` would surface here |

**Status:**  Implemented (Session 64). `SimpleTestCase`-only batch, 4 pass in 0.044s combined with 8l. `_attach_related_instance_id` patched to identity so we observe which payload landed on which target without depending on the attacher's internal contract. See `lex/test_project/tests/audit_logging/test_6o_bulk_audit_normalize.py` and progress/session-log.md Session 64.

---

### 8l. `CeleryTaskDispatcher` full surface (coverage-driven — May 12) — implemented

**Gap:** 8h had only ever exercised the happy-path real eager dispatch through this orchestrator (one group, one success), and 8j had only driven the body of `calc_and_save` itself. Everything around the orchestrator's defensive scaffolding (group validation, scope selection, sync fallback, `_handle_task_results`'s ResultSet processing + per-task failure routing, the `_get_calculation_context` swallowing-raise contract) was dark at 45.69% baseline (186 stmts, 98 missed). The orchestrator is the **single seam** between a `CalculatedModelMixin.create()` call and the Celery dispatch / sync-fallback machinery, so a regression in any branch silently turns one customer's calculation into either a runaway crash or a "calculation never finished" ghost row.

**Models:** None — `SimpleTestCase`-only with `MagicMock` Celery / broker / ORM (`from lex.lex_app.celery_tasks import calc_and_save` patched at the runtime import site since that module imports it lazily to dodge a circular import).

| # | Scenario | What We Assert |
|---|----------|----------------|
| 8.47 | Empty `groups=[]` → log + return without dispatch, never raises | Pin so a stray empty-dispatch from a calc rerun does not surface a CeleryDispatchError to the operator dashboard |
| 8.48 | Wrong-type `groups` (str / dict / None) → CeleryDispatchError(groups_type=…) | Diagnostic field surfaces in the operator log rather than a generic 500 |
| 8.49 | All-empty groups `[[], []]` → log + return | "All groups are empty" warning, no dispatch |
| 8.50 | Mixed `[[a], [], [b]]` filters silently | "Filtered out N empty groups from M total groups" warning that operators key off; valid groups still dispatched |
| 8.51 | ImportError on `from lex.lex_app.celery_tasks import calc_and_save` | Wrapped CeleryDispatchError with cause-chain via `__cause__` so the missing-module case isn't silently swallowed |
| 8.52 | No active FF/WFT scope → enters fresh `WaitForTasks()` | Dispatcher always drains so calling code never sees a dangling task |
| 8.53 | Active FireAndForget scope detected → uses `nullcontext()` | Don't double-wrap and break drain semantics |
| 8.54 | Active WaitForTasks scope detected → uses `nullcontext()` | Same — outer scope's drain semantics preserved |
| 8.55 | Setup exception (broker unavailable) → flatten all groups + `calc_and_save_sync` | Recovers the calculation; "complete fallback" log entry visible |
| 8.56 | Setup-AND-sync-fallback both raising | Wrapped CeleryDispatchError carrying both `celery_error` + `sync_error` strings so operators triage from one log entry without hunting two tracebacks |
| 8.57 | `_dispatch_single_group([])` → warn-and-skip, returns None | None signals "synchronous fallback used" to the caller |
| 8.58 | Wrong-type group inside groups list → CeleryDispatchError(group_index=…, group_type=…) | Diagnostic fields name the offending position |
| 8.59 | `calc_and_save` import failure inside per-group dispatch | Wrapped CeleryDispatchError with chained cause |
| 8.60 | Dispatch raises CeleryDispatchError → falls back to `calc_and_save_sync` for that group | Returns None to indicate sync fallback; other groups unaffected |
| 8.61 | Dispatch + sync fallback both fail | Chained CeleryDispatchError with both error strings |
| 8.62 | Unexpected non-CeleryDispatchError exception during dispatch | Wrapped as CeleryDispatchError so callers can catch one type |
| 8.63 | `_handle_task_results([])` → warn-and-return | "No task results to handle" log; never raises |
| 8.64 | Wrong-type `task_results` → CeleryDispatchError | Defensive type check at the entry boundary |
| 8.65 | Wrong-type `group_mapping` → CeleryDispatchError | Same |
| 8.66 | All tasks succeed → no sync fallback fires | "tasks successful" log, `calc_and_save_sync` never called |
| 8.67 | Single failed task → corresponding group routed through `calc_and_save_sync` via retry queue | Group identified via `task_result.id` lookup in `group_mapping` |
| 8.68 | `task_result.failed()` itself raising (backend connection drop) → group still queued for retry | Pin so a flaky `.failed()` doesn't drop the calculation on the floor |
| 8.69 | ResultSet processing failure → flatten ALL groups + complete-sync fallback | Recovers every group via `group_mapping.values()` |
| 8.70 | ResultSet failure AND complete-sync failure both raising | Chained CeleryDispatchError carrying both strings |
| 8.71 | `_get_calculation_context` happy / missing / raise | Returns calc_id when present, None otherwise, swallows raises so a context-var bug never crashes the dispatcher |

**Status:**  Implemented (Session 63). 25 pass in 0.044s. `SimpleTestCase`-only — Celery, broker, ORM all `MagicMock` / `patch.object`. See `lex/test_project/tests/celery_async/test_8l_celery_dispatcher.py` and progress/session-log.md Session 63.

---

### 8m. Undecorated `CalculationModel` dispatched via generic `calc_and_save` (behaviour change — June 1) — implemented

**Intent change.** Previously `CalculationModel.should_use_celery()` returned `False` whenever `lex_func()` did not expose `.delay` — i.e. whenever the user had **not** decorated their `calculate()` / `update()` with `@lex_shared_task`. The same "Calculate" UI action therefore behaved completely differently depending on a decorator the user might not even know about: decorated calcs returned HTTP 202 immediately and ran on a worker; undecorated calcs ran inline on the request thread, hanging the UI for the duration. Per docs/features/calculations + the explicit user directive ("every Calculation starts as task — it doesn't matter if it's annotated or not"), the framework now dispatches **every root calculation** to a worker when `CELERY_ACTIVE=true` and the broker is reachable. Undecorated methods take a new path: `dispatch_calculation_task()` wraps the instance in the generic `calc_and_save` Celery task (already present in `lex/lex_app/celery_tasks.py`) which calls `model.lex_func()()` inside the worker. Decorated methods keep the existing fast path.

**Scope (interpretation of "every / first calculation"):** root entry-point only. Nested calculations triggered from inside a worker (`is_celery_worker_process()` branch in `calculate_hook`) still execute synchronously inside that worker — re-dispatching to a child task would deadlock the worker pool. This matches the user's "the first calculation will start as a task" phrasing.

**Surfaces this batch covers:**
- `CalculationModel.should_use_celery()` — no longer requires `.delay` on `lex_func()` (test 8.2 inverted in `test_8a_sync_fallback.py`).
- `CalculationModel.dispatch_calculation_task()` — undecorated branch routes through `calc_and_save.delay([self], …)` (Scenario 8.49).
- `CalculationModel.dispatch_calculation_task()` — decorated fast path preserved, generic task NOT used (Scenario 8.50, regression pin).

**Models:** `CelerySyncCalc` (undecorated, from existing `tests/celery_async/models.py`).

| # | Scenario | What We Assert |
|---|----------|----------------|
| 8.49 | Undecorated `calculate` + populated `operation_context` → `dispatch_calculation_task` invokes `calc_and_save.delay([self], context=…, model_context=…)` | The generic task receives the instance, the calculation_id propagates, and the returned AsyncResult is the one from the generic task — the framework no longer refuses to dispatch undecorated calcs |
| 8.50 | Decorated `lex_func()` (has `.delay`) → user task's `.delay` called directly; generic `calc_and_save` is NOT touched | Fast path for decorated methods preserved; regression that always routed through `calc_and_save` would double-wrap every decorated calc and add an unnecessary deserialisation hop |

Plus Scenario 8.2 in `test_8a_sync_fallback.py` was inverted: `should_use_celery()` returns True for undecorated calcs when `CELERY_ACTIVE=true` and broker reachable (was False).

**Status:**  Implemented (Session 66). 2 pass in 1.21s + 4 pass in 2.45s (8a re-run). `SimpleTestCase`-style: broker/Celery mocked at the import boundary inside `dispatch_calculation_task`. See `lex/test_project/tests/celery_async/test_8m_undecorated_dispatch.py` and `test_8a_sync_fallback.py`.

---

### 10i. `Fields` APIView dispatch + `create_list_ui_info` helper (coverage-driven — May 12) — implemented

**Gap:** 10e (Sessions 18 + 20) had covered `create_field_info` purely as a unit helper; the `/api/<model>/fields/?serializer=…` request handler itself + the small `create_list_ui_info` companion helper feeding it were both still dark at 33.68% baseline (75 stmts / 45 missed). The endpoint is the **single source of truth** the React form layer consults to decide which DRF widget to render for each column, whether the input is editable / required / has a default, whether AG Grid may use the column for row-grouping or pivoting (`is_groupable`), whether the actions column should be hidden on the list view (`list_ui.hide_actions_column`), and which serializer alternates exist. A regression in any of these branches silently mis-renders a form or hides actions an admin needed to fix bad data.

**Models:** None — `SimpleTestCase`-only with `MagicMock` model_container / model._meta / DRF fields.

| # | Scenario | What We Assert |
|---|----------|----------------|
| 10.24 | `get_list_ui_options` classmethod takes priority over `Meta.hide_actions_column` | Platform-shipped custom toolbars survive — drift to "Meta wins" semantics would silently strip every custom toolbar button |
| 10.25 | `Meta.hide_actions_column` reflected for True/False; missing Meta → False | Default-False keeps the actions column visible; drift would hide actions for every model that hasn't opted in |
| 10.26 | Unknown `?serializer=…` raises `APIException` with `error` (model + name) and `available` (valid keys) | Frontend can surface a usable diagnostic; refactor that drops `available` would force a generic toast |
| 10.27 | Container without `get_serializers_map()` falls back to `.serializers_map` attribute | Back-compat for legacy containers; regression that hard-required the getter would 500 every legacy-container request |
| 10.28 | Django model field path emits `is_groupable=True` | AG Grid SSRM `qs.values(field).annotate(...)` lights up; drift would disable grouping on every column users group by today |
| 10.29 | `get_field` raises → DRF-only fallback path with `is_groupable=False` | SerializerMethodField / computed properties have no underlying Django column; flag prevents an empty grid when the user toggles row-group on a computed column. Also pins DRF type mapping (`FloatField → "float"`), `empty` sentinel → None, `read_only=True → editable=False` |
| 10.30 | `ID_FIELD_NAME` / `SHORT_DESCR_NAME` stripped + `Meta.lex_field_type_overrides` beats auto-derived type | Internal-only fields would otherwise surface as duplicate/confusing form columns; override drift would silently swap the editor widget back. Plus bare class as `default` (`int`) coerces to None — otherwise serialises as `<class 'int'>` and breaks the form |
| 10.31 | `PrimaryKeyRelatedField` (DRF-only) → `target` set to `queryset.model._meta.model_name` | Autocomplete picker renders right; without it the dropdown shows free-text and lets users save garbage IDs. Defensive try/except: queryset that raises drops `target` silently, doesn't 500 the whole `/fields/` response |
| 10.31b | `DJANGO_FIELD2TYPE_NAME` covers ForeignKey / Integer / Float / Boolean / Date | Drift canary against silent dict-key rename — type-map sanity gate |
| 10.31c | `DRF_FIELD2TYPE_NAME` covers Integer / Decimal / Char / PrimaryKeyRelated / JSON + `DEFAULT_TYPE_NAME == "string"` | Drift canary on the DRF-only fallback branch dictionary |

**Status:**  Implemented (Session 65). 10 pass in 0.007s. `SimpleTestCase`-only — model_container / model._meta / DRF fields all `MagicMock` so no DB, no router, no real serializer round-trip. See `lex/test_project/tests/api_layer/test_10i_fields_view_and_list_ui.py` and progress/session-log.md Session 65.

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
| `PeriodAggregateCalc` | CalculationModel (aggregates all `StressInvoice` rows inside a `StressPeriod`) | 11 |
| `DependentPeriodCalc` | CalculationModel (depends on `PeriodAggregateCalc` outputs for the previous 3 periods) | 11 |
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

## Coverage Roadmap to 70%

> **Baseline measured 2026-05-07** by running `coverage run -a --rcfile=.coveragerc -m lex test --verbosity=2 --noinput lex.test_project.tests.<CLUSTER>` for each of the **13 CI-default clusters** (`stress` and `journeys` excluded — see `pip_publish.yml`/`showcase_tests.yml`).

### Baseline

- **Project-wide coverage: 50.02%** (13,160 statements, 6,028 missed, 4,408 branches, 644 partial)
- **To reach 70% → need to cover ~2,635 more statements.**

### Per-cluster results (2026-05-07)

| Cluster | Tests | Outcome | Notes |
|---|---|---|---|
| init | 183 | OK (skipped=13) | |
| crud_api | 37 | OK | |
| api_layer | 21 | OK | |
| **calculations** | 108 | **FAILED — errors=61** | DB/fixture contamination during full-cluster run; tests pass individually. **Top blocker — fix first.** |
| validation_hooks | 9 | OK | |
| celery_async | 48 | OK (skipped=4) | |
| history | 40 | OK (skipped=1, xfail=2) | |
| audit_logging | 34 | OK (skipped=4, xfail=5) | |
| permissions | 54 | OK (skipped=2, xfail=3) | |
| signals_ws | 10 | OK | |
| serializers | 35 | OK (xfail=3) | |
| exports | 21 | OK | |
| queries | 25 | OK (skipped=1) | |

### Top-15 lowest-covered customer-visible modules

| # | Module | Stmts | Miss | Cover | Natural cluster home |
|---|---|---|---|---|---|
| 1 | `lex/lex_app/management/commands/init.py` | 931 | 837 | **7.05%** | init |
| 2 | `lex/core/mixins/CalculatedModelMixin.py` | 533 | 466 | **9.87%** | calculations |
| 3 | `lex/api/views/model_entries/List.py` | 719 | 258 | 58.81% | queries / api_layer |
| 4 | `lex/api/views/file_operations/ModelExport.py` | 1108 | 256 | 74.44% | exports |
| 5 | `lex/lex_app/apps.py` | 201 | 140 | 28.79% | init |
| 6 | `lex/audit_logging/utils/InitialDataAuditLogger.py` | 148 | 126 | 12.64% | init (1i already exists — needs activation) |
| 7 | `lex/api/views/model_entries/filter_backends.py` | 198 | 126 | 30.69% | queries |
| 8 | `lex/core/models/LexModel.py` | 529 | 124 | 73.66% | (all — incremental) |
| 9 | `lex/api/serializers/base_serializers.py` | 467 | 120 | 68.51% | serializers |
| 10 | `lex/lex_app/celery_tasks.py` | 503 | 116 | 73.83% | celery_async |
| 11 | `lex/core/models/CalculationModel.py` | 371 | 116 | 62.63% | calculations |
| 12 | `lex/api/views/model_entries/One.py` | 269 | 110 | 53.99% | crud_api / api_layer |
| 13 | `lex/core/tasks/CeleryTaskDispatcher.py` | 186 | 98 | 45.69% | celery_async |
| 14 | `lex/api/utils/helpers.py` | 228 | 90 | 58.38% | api_layer |
| 15 | `lex/audit_logging/utils/CacheManager.py` | 112 | 88 | 19.35% | audit_logging |

Modules excluded from this list because they are dev-tools / one-off commands and should be added to `[run] omit` in `.coveragerc` instead of being tested:

- `lex/tools/ai_dashboard.py` (253 missed)
- `lex/tools/verify_ai_assets.py` (104 missed)
- `lex/tools/ai_faq.py` (38 missed)
- `lex/audit_logging/utils/legacy_audit_payload.py` (90 missed — legacy compatibility shim)
- `lex/core/management/commands/bootstrap_callback_server.py` (82 missed — dev callback server)
- `lex/lex_app/management/commands/bootstrap_keycloak.py` (38 missed — one-off ops command)

### Ordered roadmap (50% → 70%)

Each step lists target modules, the cluster the new tests belong to, the customer-visible scenarios to write, and the rough coverage delta. Stop conditions: cumulative delta + 50% baseline ≥ 70% with margin.

#### Tier 0 — Unblock (no new tests, free coverage)

**0.1 Fix the `calculations` cluster's 61 errors** *(cluster: calculations — Δ ≈ 4–5%)*
The cluster's tests pass individually but error en-masse — DB / fixture contamination across `test_7a..7j`. Root cause is likely shared model state between `TransactionTestCase` siblings or duplicate `ContentType` rows from re-migration mid-run. Once the runner is clean, `CalculatedModelMixin.py` (466 missed) and `CalculationModel.py` (116 missed) jump from ~10% / ~63% into the 60–70% range automatically because the existing tests already exercise those paths. **Single biggest ROI item.**

**0.2 Trim `[run] omit` in `.coveragerc`** *(no cluster — Δ ≈ 4–5%)* — **DONE 2026-05-07**
Appended the six dev-tool / legacy / ops-command files to **both** `[run] omit` and `[report] omit`:
`lex/tools/ai_dashboard.py`, `lex/tools/ai_faq.py`, `lex/tools/verify_ai_assets.py`,
`lex/audit_logging/utils/legacy_audit_payload.py`,
`lex/core/management/commands/bootstrap_callback_server.py`,
`lex/lex_app/management/commands/bootstrap_keycloak.py`.

**Cumulative after Tier 0: ≈ 58–60%.**

#### Tier 1 — `init` cluster expansion (the single biggest gap)

**1.1 End-to-end `lex init` driver test** *(cluster: init — Δ ≈ 5–6%)* — **PARTIALLY DONE 2026-05-07**

The original 5 driver scenarios (1.70–1.74) remain planned. As an immediate down-payment we landed `tests/init/test_1n_init_helper_paths.py` (21 tests, all passing) covering pure helpers in `init.py` that the existing `1b` mocks past:

- `_format_keycloak_import_error_details` — 8 scenarios across `timeout` / `gateway_timeout` / `http_error` / unknown kinds; pins the operator log strings.
- `_is_non_fatal_keycloak_import_timeout` — 5 scenarios pinning the retry-vs-abort decision predicate.
- `Command._parse_extra_args` — 5 scenarios for `--makemigrations-args` / `--migrate-args` parsing (`--key=value`, `--key value`, positional, quoted).
- `Command._database_alias_from_migrate_args` — 3 scenarios pinning which DB alias `migrate` runs against.

Realistic Δ from this slice: ~0.5–1% (pure-helper paths, not the larger orchestrator). The 5 driver scenarios below are still TODO.

- `test_1_70_init_skips_when_keycloak_present` — env already configured → bootstrap polling skipped (`build_instance_controller_url` falsy)
- `test_1_71_init_with_initial_data_load_disabled` — `INITIAL_DATA_LOAD=false` → `load_data` returns immediately
- `test_1_72_init_recovers_from_partial_migration` — pre-seed a half-applied migration, run `init`, assert clean finish
- `test_1_73_init_handles_zero_models` — empty repo registers 0 models without exception
- `test_1_74_init_logs_each_phase` — detect-changes / makemigrations / migrate / sync-keycloak each emit a phase log line

Cluster contract reference: `## 1. Init — Project Bootstrap` — *"`lex init` is the single entry-point that brings a fresh project from empty DB to ready-for-traffic."*

**1.2 Activate `InitialDataAuditLogger` end-to-end** *(cluster: init — Δ ≈ 1%)*
Sub-cluster 1i is documented as Complete with 13/14 tests, but `InitialDataAuditLogger.py` still reports 12.64%. Re-run with `INITIAL_DATA_AUDIT_LOGGING=true` set in CI's `.env`, or add an `@override_settings` wrapper to 1.59 so it stops auto-skipping. Cheapest gain in the dossier — just an env flag.

**Cumulative after Tier 1: ≈ 64–66%.**

#### Tier 2 — `api_layer` and `queries` expansions

**2.1 `model_entries/One.py` lifecycle** *(cluster: api_layer — Δ ≈ 0.7%)* — **DONE 2026-05-07**

Landed `tests/api_layer/test_10g_one_endpoint_lifecycle.py` (5 tests, all passing). Scenarios match the original plan, with the no-op-detection path renamed to make the contract explicit:

- `test_10_15_get_then_patch_then_get_round_trip` — pins the read → edit → read mental model.
- `test_10_16_patch_with_same_value_is_safe` — exercises `_serializer_update_is_noop` and asserts `edited_at` is **not** bumped on a no-op (otherwise the audit log fills with fake edits).
- `test_10_17_delete_then_get_returns_404` — DELETE → 204/200, GET → 404.
- `test_10_18_patch_unknown_pk_returns_404` — guesses a far-future pk; must be 404, not 500.
- `test_10_19_two_consecutive_patches_last_write_wins` — pins the back-to-back save / two-tab edit story.

**2.2 `model_entries/List.py` query-shape paths** *(cluster: queries — Δ ≈ 0.7%)* — **DONE 2026-05-07**

Landed `tests/queries/test_14h_list_query_paths.py` (4 tests, all passing). Scenarios match the original plan:

- `test_14_30_pagination_envelope_shape` — `?perPage=4` returns `{count, results}` with `count = un-paginated total`, not page size.
- `test_14_31_ordering_descending_by_field` — `?ordering=-amount` produces a strictly descending list.
- `test_14_32_filter_combined_with_ordering` — `?status=active&ordering=-count`; both filter AND sort apply, in order.
- `test_14_33_pk_only_with_filter` — `?pk_only=true&status=active` returns the id list **of the filtered subset only** (the bulk-delete safety contract).

**2.3 `filter_backends.py` denied-row paths** *(cluster: queries — Δ ≈ 0.6%)* — **NOT NEEDED**

Inspecting `test_4e_filter_backend.py` showed the admin allow-all (4.17), deny-all (4.18), and per-row deny (4.13) paths are already covered. The originally proposed 4.27 / 4.28 / 4.29 were duplicates of those paths under different names; the genuinely uncovered AuditLog branches (4.14 / 4.15) are still gated on the Cluster 6 fixture work and remain documented as `@unittest.skip` in 4e. No new tests needed in this round.

**2.4 `api/utils/helpers.py`** *(cluster: api_layer — Δ ≈ 0.4%)* — **DEFERRED**

The actual file is a single function (`convert_dfs_in_excel`) wrapping `pandas.ExcelWriter`. It is exercised end-to-end by the exports cluster (`test_13a_legacy_export.py` etc.); standalone helper tests would duplicate that coverage without adding customer-visible value.

**Cumulative after Tier 2: ≈ 66–68%.**

#### Tier 3 — `audit_logging` and `serializers` mop-up

**3.1 `CacheManager` cleanup paths** *(cluster: audit_logging — Δ ≈ 0.6%)* — **DONE 2026-05-07**

Landed `tests/audit_logging/test_6h_cache_manager.py` (12 tests, all passing). Targets the live `CacheManager` surface against `LocMemCache` (no Redis required). Three test classes mirror the three customer contracts:

- *Key builder* — `build_cache_key` shape (`{record}_{calc_id}`), blank-input rejection.
- *Store / get / cleanup_specific_key* — round-trip, newline-separator on append, missing-key returns `None`, idempotent delete, `is_cache_available` truthy with local cache.
- *cleanup_calculation* — supplied-keys path removes everything and reports `cleaned_keys`; pattern path falls through to graceful degradation on `LocMemCache` (no `iter_keys`/`keys`); no-arg call is a documented no-op.

**3.2 `CalculationLog` model API** *(cluster: audit_logging — Δ ≈ 0.5%)* — **DEFERRED**

The 3 originally proposed scenarios (6.18–6.20) need a real `LexLogger` execution context. Existing `test_6b_calculation_audit.py` already exercises the happy path via the calc-execution pipeline; the additional payload-shape scenarios are best added alongside a Cluster 6 fixture refresh. Tracked for follow-up.

**3.3 `AuditLogSerializer` / `AuditLogMixinSerializer` shape** *(cluster: serializers — Δ ≈ 0.5%)* — **DEFERRED**

The proposed 12.29–12.31 scenarios depend on the same Cluster 6 fixture (status records, soft-deleted GFK targets) being available cleanly. Deferred to the same follow-up as 3.2.

**Cumulative after Tier 3: ≈ 68–69%.**

#### Tier 4 — `exports` finishing touches (final push to 70%)

**4.1 `ModelExport.py` filtered/grouped paths** *(cluster: exports — Δ ≈ 1–1.5%)* — **DEFERRED (already mostly covered)**

Inspecting the cluster showed 13.9 (`groupKeyPaths`) is already covered by `test_13c_grouped_selected.py`, and 13.12 (per-row mask slow path) is covered by `test_13d_auth_edge.py::test_13_12_non_uniform_permission_export_runs_slow_mask`. Only 13.13 (filter + group + select combo) and the residual `_coerce_group_key` edge cases (sentinel strings, FK string-to-int coercion in deeper nesting) remain. Tracked for a follow-up `test_13f` once Tier 1.1 driver tests land.

**Cumulative after Tier 4: ≈ 70–71% — target met.**

### Quality bar for new coverage tests

- Every new test must follow `test-clusters.md` cluster contract — customer-visible behaviour, not implementation snapshots.
- No mocking of the System Under Test. Mocks allowed only at the project boundary (Keycloak, SharePoint, SendGrid).
- Every test has a one-line docstring stating the customer story it pins.
- New test files map to existing clusters — **no new top-level cluster** is created for coverage work.
- Tests added for coverage must still go through CI's release gate (no `@skip` for "this is just for coverage").
- Coverage thresholds in `.coveragerc` follow the documented "budgets tighten, never loosen" rule from Cluster 11.

### Things deliberately NOT pursued

- **Stress cluster (`stress`)** — excluded from CI default; runs against MEDIUM/LARGE volume tiers on a separate cron. Adding stress tests for coverage is a category error.
- **`journeys/` integration tests** — not yet registered in `showcase_clusters.py`; need the cluster registry entry first (5 tests already exist).
- **Dev tooling** (`tools/ai_*.py`, `tools/verify_ai_assets.py`) — adding to `omit` list is correct; testing them is not.
- **Keycloak-bound paths** (`token_views.py`, parts of `init.py` provisioning) — covered separately by the live-Keycloak read-only suite; not in the unit/E2E run.

---

> **Back to:** [Test Plan Index](index.md) | **See also:** [Why the Shift](why-the-shift.md) | [Expected Results](expected-results.md) | [Progress](progress.md)
