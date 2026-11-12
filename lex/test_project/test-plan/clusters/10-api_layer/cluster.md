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

---

### 10o. Error bodies, OIDC redirects, unknown api/ paths, global search ?q=, one KeycloakManager ✅

**What it tests:** error responses of the generic endpoints no longer carry a server traceback by default (`LEX_SEC_TRACEBACKS`, `LEX_API_TRACEBACKS`); `next`/`fail` on `/oidc/*` cannot send a signed-in user to a foreign site (`LEX_SEC_OIDC_REDIRECT`); an unknown endpoint below `api/` is a JSON 404 instead of the SPA's index.html with 200; the `?q=` form of the global search the bundled frontend calls is routed; requests share one KeycloakManager.

| Scenario | Title | Asserts |
| --- | --- | --- |
| 10.83 | no traceback by default | create and a raising `permission_create` answer 500 with `error`, no `traceback` (the `run_step` probe went with the ProcessFlow endpoint — 10q) |
| 10.84 | traceback when allowed | `LEX_API_TRACEBACKS=true`, legacy, DEBUG show it; `LEX_API_TRACEBACKS=false` wins over DEBUG |
| 10.85 | redirect targets | relative, own host, `CSRF_TRUSTED_ORIGINS` (wildcard, :3000), CORS origins pass; foreign, `//`, `/\`, `javascript:`, look-alike, userinfo hosts 400 on authenticate, logout, total_logout; other paths not inspected; opt-out passes |
| 10.86 | guard in the stack | installed before the session middleware; `/oidc/authenticate` and `/oidc/total_logout` with a foreign `next` answer 400 |
| 10.87 | unknown api/ path | JSON 404 with `error`; `api/health` and a model list still 200 |
| 10.88 | SPA catch-all | api paths 404, app routes index 200, also below `DJANGO_BASE_PATH`; legacy serves the index |
| 10.89 | global search `?q=` | `?q=needle` and `/needle` find the row; empty `q` the sentinel |
| 10.90 | one KeycloakManager | three token requests build one manager and get permissions and roles; no token builds none |

**Scenario range:** 10.83 – 10.90. **Test file:** `lex/test_project/tests/api_layer/test_10o_error_and_route_hardening.py`. **Type:** E (10.85, 10.88, 10.90 U). **Status:** ✅ Complete (2026-10-18). Batch: [10o](batches.md).

### 10p. File-download endpoints write an audit entry

Four endpoints serve a file, each through its own machine, and ``file-download``
branches again per ``STORAGE_TYPE``. Each is recorded as an ``AuditLog`` row with
``action='download'`` — see [[history-and-audit/audit logs]].

| # | Scenario | What We Assert |
|---|----------|----------------|
| 10.91 | Local download | One entry naming the field, filename, byte size and `delivery: stream`, its generic relation pointing at the record so the download shows on that record's audit tab |
| 10.92 | Refused download | Status `failure` with the refusing 404 and nothing attached — an attempt to take data out is recorded whether or not it succeeded |
| 10.93 | GCS download | `delivery: url` — the caller received access, not bytes, and the trail tells the two apart |
| 10.94 | SharePoint download | Own entry with `storage: SHAREPOINT`, `delivery: stream`; shares no code with `file-download` |
| 10.95 | SharePoint share link | `delivery: share_link` plus the SharePoint document id — no bytes crossed the wire, but the file left the system's control |
| 10.96 | SharePoint preview link | `delivery: preview_link`, distinguishable from the share link |

**Scenario range:** 10.91 – 10.96. **Test file:** `lex/test_project/tests/api_layer/test_10p_file_download_audit.py`. **Type:** E. **Status:** ✅ Complete (2026-10-22). Batch: [10p](batches.md).

---

### 10q. The Process and ProcessFlow endpoints are gone from the route table ✅

**What it tests:** the two public routes the Process feature owned — `api/run_step/<model>/<pk>` (the ProcessFlow `CreateOrUpdate` view) and `api/process/<process_name>` (the `ProcessStructure` view) — no longer reverse, no longer appear in the route table under their paths, and their view modules no longer import. The third scenario is the other half: the removal ran through the one url list the entire frontend is served from, so every neighbouring endpoint is checked to still resolve.

**Why a regression matters:** `run_step` was a second write path into every model with its own copy of the permission checks; a route that comes back by accident is a write surface nobody audits. And a cascade that takes a neighbour out with it breaks every screen in a project.

| Scenario | Title | Asserts |
| --- | --- | --- |
| 10.97 | `run_step` is not routed | `NoReverseMatch`, no `run_step` name or `api/run_step/` path, `lex.api.views.process_flow` raises `ModuleNotFoundError` |
| 10.98 | process structure is not routed | `NoReverseMatch`, no `process` name or `api/process/` path, no `ProcessStructure` on either module |
| 10.99 | the neighbours are untouched | `model-structure`, `project-info`, `calculationlog-tree`, `init-calculation-logs`, `clean-calculations`, `widget-structure`, `global-search-query`, `user-permissions`, `log` and `htmlreports` all still reverse |

**Scenario range:** 10.97 – 10.99. **Test file:** `lex/test_project/tests/api_layer/test_10q_process_routes_removed.py`. **Type:** U. **Status:** ✅ Complete (2026-10-23). Batch: [10q](batches.md).

---

### 10r. `api/htmlreport/<name>` hands a report the request, and the Streamlit frame a credential ✅

**What it tests:** the route every `HTMLReport` in a project is rendered through, and the one report the framework ships. The `Streamlit` report frames the project's dashboard, which lives on its own host behind the auth proxy; a cross-origin frame cannot authenticate itself, so the embedding page must hand it the bootstrap `auth_token` the proxy exchanges for a session cookie. The route supplies the request that makes this possible — but only to a report that declares a second parameter, so `HTMLReport`'s published `get_html(self, user)` is unchanged for every project that subclasses it.

**Why a regression matters:** framed without a credential, the dashboard 401s, and a framed 401 is answered with a page that moves the *top* window to the dashboard host's login — so clicking **Streamlit** inside an instance took the whole browser tab out of it. And this route renders every report a project has, so widening the call signature for all of them would break them all at once.

| Scenario | Title | Asserts |
| --- | --- | --- |
| 10.100 | the sidebar entry frames a dashboard that can let the user in | the frame is on `STREAMLIT_URL` carrying the session's access token as `auth_token`; the response answers `Cache-Control: no-store` |
| 10.101 | nothing to hand it means nothing handed | no OIDC token on the session → no `auth_token` key at all, not an empty or placeholder one |
| 10.102 | the published `get_html(self, user)` is unchanged | the one-parameter report renders untouched; the two-parameter one gets the request; a `TypeError` from inside a report is not read as a signature |

**Scenario range:** 10.100 – 10.102. **Test file:** `lex/test_project/tests/api_layer/test_10r_html_report_request_and_streamlit_frame.py`. **Type:** E (10.102 also U). **Status:** ✅ Complete (2026-11-03). Batch: [10r](batches.md).

### 10ad — `model_info` ships the project's per-column display options

**What it tests:** a project writes how its own columns should look on its serializer
(`Meta.lex_column_options`), and the endpoint the frontend blocks on before drawing any
table carries it. LEX-1556: a customer asked for a narrower ID column, and `list_ui` could
say exactly one thing (`hide_actions_column`) and nothing about a column.

| Scenario | Title | Asserts |
| --- | --- | --- |
| 10.184 | the declaration reaches the frontend | `list_ui.columns` keyed by field name, carrying the declared options; `hide_actions_column` untouched |
| 10.185 | a plain DRF serializer gets the same contract | a project's own non-`LexSerializer` class answers identically — one payload, one reader, no second code path |
| 10.186 | a model declaring nothing answers the key | `columns: {}`, so "declared nothing" and "declared an empty thing" cannot diverge between readers |
| 10.187 | a malformed declaration never breaks the endpoint | the response still arrives and the one usable declaration survives; a typo costs that option, not every list view of the model |

**Scenario range:** 10.184 – 10.187. **Test file:** `lex/test_project/tests/api_layer/test_10ad_list_ui_column_options.py`. **Type:** U. **Status:** ✅ Complete (2026-11-10). Batch: [10ad](batches.md). Paired with [12p](../12-serializers/cluster.md) and frontend clusters F03p/F03q.

---

### 10ac. Scheduled jobs through the REST API ✅

**What it tests:** Schedules are created, edited, switched off and run through the generic API and UI: every definition mistake answers 400 naming the field, a save records who the job runs as and moves its next slot, the ▶ button runs it now, a schedule declared in code can be switched off but not rewritten, and an edit never resets a scheduled job's status.

**Why a regression matters:** Stored schedules that can never run, a job running as someone who never saved it, the UI and the code fighting over a declared schedule, or an edit hiding a running job from the overlap check.

| Scenario | Title | Asserts |
| --- | --- | --- |
| 10.175 | create sets next slot and run-as | 201; next slot = next 06:00 UTC; `run_as` = the creator; enabled |
| 10.176 | mistakes answer 400 naming the field | bad cron, seconds field, no / both schedules, past `run_at`, unknown zone, unknown / non-calculation / self model, missing record, unknown function; nothing stored |
| 10.177 | an edit moves the slot and the run-as | a second person's cron edit moves the slot and becomes `run_as`; off → no slot; on → a future slot |
| 10.178 | ▶ runs now | PATCH `calculate=true` runs the target; job SUCCESS; next slot unchanged |
| 10.179 | code-declared schedules | a cron edit answers 400 pointing at `@lex_job`; `enabled=false` works |
| 10.180 | bare model names | `scheduledtargetcalc` is stored as `lex_app.ScheduledTargetCalc` |
| 10.181 | in the sidebar | `api/model-structure` lists `scheduledjob` |
| 10.182 | an edit during a run | the edit is stored; the job stays IN_PROGRESS; a due slot is skipped; one run |
| 10.183 | `reset_calculation_on_edit` | a model setting it `False` keeps SUCCESS through an edit (and says so); an ordinary model is still reset |

**Scenario range:** 10.175 – 10.183. **Test file:** `lex/test_project/tests/api_layer/test_10ac_scheduled_jobs_api.py`. **Type:** E. **Status:** ✅ Complete (2026-11-10). Batch: [10ac](batches.md).
