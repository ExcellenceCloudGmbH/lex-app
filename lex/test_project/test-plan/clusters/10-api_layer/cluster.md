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

---

### 10o. `api/htmlreport/<name>` hands a report the request, and the Streamlit frame a credential ✅

**What it tests:** the route every `HTMLReport` in a project is rendered through, and the one report the framework ships. The `Streamlit` report frames the project's dashboard, which lives on its own host behind the auth proxy; a cross-origin frame cannot authenticate itself, so the embedding page must hand it the bootstrap `auth_token` the proxy exchanges for a session cookie. The route supplies the request that makes this possible — but only to a report that declares a second parameter, so `HTMLReport`'s published `get_html(self, user)` is unchanged for every project that subclasses it.

**Why a regression matters:** framed without a credential, the dashboard 401s, and a framed 401 is answered with a page that moves the *top* window to the dashboard host's login — so clicking **Streamlit** inside an instance took the whole browser tab out of it. And this route renders every report a project has, so widening the call signature for all of them would break them all at once.

| Scenario | Title | Asserts |
| --- | --- | --- |
| 10.72 | the sidebar entry frames a dashboard that can let the user in | the frame is on `STREAMLIT_URL` carrying the session's access token as `auth_token`; the response answers `Cache-Control: no-store` |
| 10.73 | nothing to hand it means nothing handed | no OIDC token on the session → no `auth_token` key at all, not an empty or placeholder one |
| 10.74 | the published `get_html(self, user)` is unchanged | the one-parameter report renders untouched; the two-parameter one gets the request; a `TypeError` from inside a report is not read as a signature |

**Scenario range:** 10.72 – 10.74. **Test file:** `lex/test_project/tests/api_layer/test_10o_html_report_request_and_streamlit_frame.py`. **Type:** E (10.74 also U). **Status:** ✅ Complete (2026-11-03). Batch: [10o](batches.md).
