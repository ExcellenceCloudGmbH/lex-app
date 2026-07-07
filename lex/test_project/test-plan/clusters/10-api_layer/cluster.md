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

### 10m. Calculation-log tree pagination + N+1 fix ✅

**What it tests:** `CalculationLogTreeView` (the endpoint that backfills the calculation-log tree when the panel opens). Before this change the view ran `CalculationLog.objects.all()` (or every row for one calc) and the serializer issued one child query per node (N+1) — loading the whole table into memory in one shot, a second compounding offender behind the backend OOM. The fix paginates with `limit`/`offset` (default 1000, max 5000), resolves every page's child ids in a single query via a prefetched `children_map`, and checks `parent_log_id` instead of lazy-loading the parent FK per node.

**Why a regression matters:** the unbounded query + N+1 is the read-path twin of the 6p buffer leak — together they were the calculation-log OOM. Pagination caps how much a single request can materialize; the `assertNumQueries` gate is what stops the N+1 from silently creeping back.

**Scenario range:** 10.61 – 10.66. **Test file:** `lex/test_project/tests/api_layer/test_10m_calculation_log_tree.py`. **Type:** I. **Status:** ✅ Complete (Session 77 — June 8). Scenarios: 10.61 default limit bounds the page; 10.62 offset walks the dataset; 10.63 child ids resolved per parent; 10.64 `isRoot` present only for parentless rows (stripped on children); 10.65 the integration surface — `assertNumQueries(3)` across 25 parents+children proves the N+1 is gone; 10.66 `calculation_id` filter scopes the rows.

---
