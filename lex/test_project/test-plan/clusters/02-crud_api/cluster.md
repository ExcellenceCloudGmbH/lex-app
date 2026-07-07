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

### 2j. Instance API-key extraction and matching ✅

**Gap:** PR #615 ("feat(calculations): expose active release warnings") introduced two helpers in `lex/api/utils/api_key_requests.py` that allow the framework to authenticate machine-to-machine requests using a shared secret stored in `LEX_API_KEY`: `get_raw_api_key()` extracts the raw key from a `KeyParser` or falls back to the `Authorization: ApiKey <token>` header; `is_instance_api_key_request()` compares the extracted key to the env var. If either helper regresses — e.g. header fallback stops working, or the env-var comparison becomes case-insensitive — silent auth bypasses or auth failures will appear in production without any framework-level guard.

**Scenario range:** 2.97 – 2.107. **Test file:** `lex/test_project/tests/crud_api/test_2j_instance_api_key.py`. **Type:** U. **Status:** ✅ Complete (Session 80 — June 18). Covers `lex/api/utils/api_key_requests.py`.

---
