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

### 10d. Global search with the Reflex report registered ✅

**What it tests:** with `IS_REFLEX_ENABLED=true` lex-app registers a `Reflex` report — an `HTMLReport` framing the project's Reflex dashboards, with no table behind it. The global search walks every registered container, so it must skip the report as it skips `streamlit`.

**Why a regression matters:** the search would read the report's `_meta`, raise `AttributeError`, and turn every search in the frontend into a 500 for any project that enables Reflex.

| Scenario | Title | Asserts |
| --- | --- | --- |
| 10.82 | the Reflex report is skipped | a search over the report's real container and a model with a matching row answers 200 with that row alone |

**Scenario range:** 10.82. **Test file:** `lex/test_project/tests/api_layer/test_10d_reflex_search_exclusion.py`. **Type:** I. **Status:** ✅ Complete (2026-09-28). Covers `lex/api/views/global_search_for_models/Search.py`. Batch: [10d](batches.md).

---

### 10o. Project API endpoints mounted through lex; Process route removed ✅

**What it tests:** LexAPIView endpoints need sign-in unless public; PUBLIC_PATHS and public=True skip the login check; one transaction per request rolls back on an error answer; lookups apply read permission (404); strict_serializer refuses unknown keys; ensure_csrf sets the cookie; a signed webhook acts as its service user; nothing is mounted without configuration; api/process/<name> is gone.

**Why a regression matters:** Project endpoints that bypass sign-in, commit half a failed request or leak unreadable rows would reintroduce the problems lex already solves for its own endpoints.

| Scenario | Title | Asserts |
| --- | --- | --- |
| 10.83 | Process route gone | api/process/x resolves to the frontend catch-all; no Process export |
| 10.84 | sign-in required | 200 for a signed-in caller, never 200 anonymously |
| 10.85 | public paths | public=True and PUBLIC_PATHS answer anonymously; signed-in caller recognised |
| 10.86 | rollback on error answer | 409 LexActionError body; the written row does not exist |
| 10.87 | lookup applies read permission | readable 200; denied and missing ids both 404 |
| 10.88 | strict input | extra key 400 naming it; valid input 200 |
| 10.89 | CSRF bootstrap | csrftoken cookie set |
| 10.90 | signed webhook | valid HMAC 200 as the service user with the raw body; wrong signature 401 |
| 10.91 | unconfigured | would-be project path falls to the catch-all |

**Scenario range:** 10.83-10.91. **Test file:** `lex/test_project/tests/api_layer/test_10o_project_api.py`. **Type:** E. **Status:** ✅ Complete (2026-10-16). Covers `lex/lex_app/urls.py`, `lex/api/project/` (views, public_paths), `lex/authentication/middleware.py` (public paths), `lex/process_admin/sites/process_admin_site.py` (Process removed). Batch: [10o](batches.md).

---

### 10p. Error bodies: no tracebacks by default, structured on request ✅

**What it tests:** A failing generic create answers 500 with its error but without a traceback unless DEBUG, LEX_API_TRACEBACKS or legacy mode; DRF field errors keep their shape unless LEX_ERROR_FORMAT=structured; project endpoints answer {code, detail, error, fields}; DB_ERROR_MAPPER turns database errors into business codes; EXCEPTION_HANDLER replaces lex's handler.

**Why a regression matters:** Tracebacks in production responses reveal source paths and SQL; changing DRF's default bodies would break the bundled frontend's error display.

| Scenario | Title | Asserts |
| --- | --- | --- |
| 10.92 | no traceback by default | 500 with error, no traceback |
| 10.93 | tracebacks when allowed | LEX_API_TRACEBACKS, legacy, DEBUG |
| 10.94 | DRF bodies unchanged | field dict without code; structured on project endpoints and with LEX_ERROR_FORMAT |
| 10.95 | project errors structured | 500 INTERNAL_ERROR without exception text |
| 10.96 | DB_ERROR_MAPPER | 500 without, 409 TRIGGER_REFUSED with the mapper |
| 10.97 | handler override | project handler's 418 returned |

**Scenario range:** 10.92-10.97. **Test file:** `lex/test_project/tests/api_layer/test_10p_error_contract.py`. **Type:** E. **Status:** ✅ Complete (2026-10-16). Covers `lex/api/errors.py`, error bodies in `lex/api/views/model_entries/One.py` and `process_flow/CreateOrUpdate.py`, REST_FRAMEWORK EXCEPTION_HANDLER. Batch: [10p](batches.md).

---

### 10q. OpenAPI schema of the project API ✅

**What it tests:** With SCHEMA set the schema endpoint documents only the project prefix with the configured title and version, lists bearerAuth, serves a public Redoc page, and `lex schema --validate --file` writes it; without SCHEMA nothing is served.

**Why a regression matters:** Client generation and integrator docs depend on a valid schema that does not leak lex's generic endpoints.

| Scenario | Title | Asserts |
| --- | --- | --- |
| 10.98 | schema covers the project API | title, version, only project paths |
| 10.99 | security schemes and docs | bearerAuth; docs page 200 |
| 10.100 | CLI | valid YAML with only project paths |
| 10.101 | unconfigured | schema and docs fall to the catch-all |

**Scenario range:** 10.98-10.101. **Test file:** `lex/test_project/tests/api_layer/test_10q_openapi_schema.py`. **Type:** E. **Status:** ✅ Complete (2026-10-16). Covers `lex/api/schema.py`, `lex/api/schema_extensions.py`, `lex/api/management/commands/schema.py`, schema routes in `lex/lex_app/urls.py`. Batch: [10q](batches.md).

---

### 10r. Database context per request and per after-commit callback ✅

**What it tests:** Each request runs in one transaction where the database knows the user; the setting does not leak into the next request; without DB_CONTEXT there is no extra transaction; on_commit callbacks run in a fresh transaction with the user.

**Why a regression matters:** Row-level security fails open or closed exactly where the context is missing.

| Scenario | Title | Asserts |
| --- | --- | --- |
| 10.102 | request knows the user | probe sees e2e_user; hook local=True inside a transaction |
| 10.103 | no leak | anonymous request after it sees '' |
| 10.104 | no hook | no call, no transaction |
| 10.105 | on_commit | callback sees the user in a transaction |

**Scenario range:** 10.102-10.105. **Test file:** `lex/test_project/tests/api_layer/test_10r_db_context.py`. **Type:** I + E. **Status:** ✅ Complete (2026-10-16). Covers `lex/core/db_context.py` (LexDbContextMiddleware, apply, on_commit), `lex/api/project/views.py` (LexAPIView.initial), MIDDLEWARE. Batch: [10r](batches.md).

---

### 10s. Record and collection actions ✅

**What it tests:** Record actions run with validated input; input is strict; permission and visibility gate every action and an action without a permission is refused; errors roll back; file results pass through and methods are enforced; collection actions run without a record; unknown or misplaced actions are 404.

**Why a regression matters:** Each action is a write path; it must not be weaker than PATCH.

| Scenario | Title | Asserts |
| --- | --- | --- |
| 10.106 | record action | 200 with the result; row approved |
| 10.107 | strict input | UNKNOWN_FIELDS; missing note 400; row unchanged |
| 10.108 | permission and visibility | locked 400, hidden 404, unguarded 400 |
| 10.109 | rollback | 409 NOT_NOW; side-effect row absent |
| 10.110 | file result | regular 400, superuser CSV, POST 405 |
| 10.111 | collection action | three ids; count 9 is 400 |
| 10.112 | unknown/misplaced | all 404 |

**Scenario range:** 10.106-10.112. **Test file:** `lex/test_project/tests/api_layer/test_10s_record_actions.py`. **Type:** E. **Status:** ✅ Complete (2026-10-16). Covers `lex/api/actions.py` (lex_action, actions_for, ModelActionView), action routes in `lex/process_admin/sites/process_admin_site.py`. Batch: [10s](batches.md).

---
