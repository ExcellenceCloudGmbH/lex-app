## Cluster 10 — API Layer (existing 10a–10f)

### Batch 10g — Calculation-log tree, clean, init, PDF

| Property | Value |
| --- | --- |
| Scenario range | 10.30 – 10.45 |
| Type | E |
| Files covered | `views/model_entries/CalculationLogTreeView.py`, `views/model_entries/serializers/CalculationLogTreeSerializer.py`, `views/calculations/CleanCalculations.py`, `views/calculations/InitCalculationLogs.py`, `views/calculations/DownloadMarkdownPdf.py` |
| Test file | `lex/test_project/tests/api_layer/test_calc_endpoints.py` |
| Test classes | `TestCalculationLogTreeEndpoint`, `TestCalculationLogTreeSerializerShape`, `TestCleanCalculationsEndpoint`, `TestInitCalculationLogsEndpoint`, `TestDownloadMarkdownPdfEndpoint` |
| Fixtures | `CalcWithLogging` from 6g, plus a fixture seeding a small log tree |
| Est. tests | ~15 |
| Coverage gain | +0.9 % |
| Prereqs | 6g, 6i |

### Batch 10h — File operations & SharePoint

| Property | Value |
| --- | --- |
| Scenario range | 10.46 – 10.60 |
| Type | E + I |
| Files covered | `views/file_operations/FileDownload.py`, `utilities/storage/custom_storage.py`, `views/sharepoint/SharePointPreview.py`, `SharePointShareLink.py`, `SharePointFileDownload.py` |
| Test file | `lex/test_project/tests/api_layer/test_files_and_sharepoint.py` |
| Test classes | `TestFileDownloadEndpoint`, `TestCustomStorageBackendSelection` (env-driven branch), `TestSharePointPreview`, `TestSharePointShareLink`, `TestSharePointFileDownload` (mock SP HTTP boundary) |
| Fixtures | `FileBackedItem` (model with a FileField); mock SharePoint client |
| Est. tests | ~14 |
| Coverage gain | +0.8 % |
| Prereqs | none |

### Batch 10m — Calculation-log tree pagination + N+1 fix ✅

| Property | Value |
| --- | --- |
| Scenario range | 10.61 – 10.66 |
| Type | I |
| Files covered | `views/model_entries/CalculationLogTreeView.py`, `views/model_entries/serializers/CalculationLogTreeSerializer.py` |
| Test file | `lex/test_project/tests/api_layer/test_10m_calculation_log_tree.py` |
| Test classes | `TestCluster10m_TreeViewPagination` |
| Fixtures | none (creates `CalculationLog` rows inline) |
| Est. tests | 6 |
| Coverage gain | measured locally; tree view + serializer |
| Prereqs | none |
| Status | ✅ Complete — 6 pass / 0 fail |
| Note | Backend OOM fix (session 77). The tree endpoint previously loaded the whole `CalculationLog` table (or every row for a calc) with a per-node child query (N+1). Now: limit/offset pagination (`DEFAULT_LIMIT=1000`, `MAX_LIMIT=5000`, `has_more`), children resolved for the whole page in one query via serializer context, `get_isRoot` reads `parent_log_id` (no lazy parent fetch). Scenario 10.61 placed past the 10.60 ceiling; the 10g-reserved "calculation-log tree" slot was never implemented (the on-disk 10g file became `one_endpoint_lifecycle`), so this lands as 10m. |

### Batch 10d — Global search with the Reflex report registered ✅

| Property | Value |
| --- | --- |
| Scenario range | 10.82 |
| Type | I |
| Files covered | `lex/api/views/global_search_for_models/Search.py` (`EXCLUDED_MODELS`) |
| Test file | `lex/test_project/tests/api_layer/test_10d_reflex_search_exclusion.py` |
| Test classes | `TestCluster10d_ReflexSearchExclusion` |
| Fixtures | `SchemaItem` (shared api_layer model); the real `ModelContainer` of the `Reflex` report beside it; `UserPermission` forced open as in 10f |
| Tests landed | **1 pass / 0 fail** |
| Status | ✅ Complete |
| Note | 10.82: with the Reflex report registered (`IS_REFLEX_ENABLED=true`), the global search answers with the model's matches and skips the report's container (id `reflex`) as it skips `streamlit`. Against the tree without the exclusion it fails with `AttributeError: type object 'Reflex' has no attribute '_meta'` — every search a 500. Letter d was free; scenario placed after 10i's 10.81. |

> `ModelExport.py` (cluster 13f), `List.py` AG-Grid path (14f), `base_serializers.py` (12g) keep their forecasted homes.

---

### Batch 10o — Error bodies, OIDC redirects, unknown api/ paths, global search ?q=, one KeycloakManager ✅

| Property | Value |
| --- | --- |
| Scenario range | 10.83 – 10.90 |
| Type | E (10.85, 10.88, 10.90 U) |
| Files covered | `lex/core/security_flags.py` (`expose_tracebacks`), `lex/api/security.py` (`error_detail`), `lex/api/views/model_entries/One.py`, `lex/authentication/middleware.py` (`OidcRedirectGuardMiddleware`), `lex/lex_app/settings.py` (`MIDDLEWARE`), `lex/react/views.py`, `lex/process_admin/sites/process_admin_site.py`, `lex/api/views/global_search_for_models/Search.py`, `lex/api/middleware/keycloak_permissions.py` |
| Test file | `lex/test_project/tests/api_layer/test_10o_error_and_route_hardening.py` |
| Test classes | `TestCluster10o_Tracebacks`, `TestCluster10o_OidcRedirects`, `TestCluster10o_OidcRedirectInStack`, `TestCluster10o_UnknownApiPaths`, `TestCluster10o_ServeReact`, `TestCluster10o_GlobalSearchQuery`, `TestCluster10o_KeycloakManagerPerProcess` |
| Fixtures | new `ExplodingItem` (save raises for "explode") and `ApiSimpleItem` in `api_layer/models.py` (`HARDENING_MODELS`); a fake `KeycloakManager`; a temporary SPA build |
| Tests landed | **8 pass / 0 fail** |
| Status | ✅ Complete — see [session note](../../progress/sessions/2026-10-18-v2-security-hardening-internal-config.md) |

---

### Batch 10p — File-download endpoints write an audit entry ✅

| Property | Value |
| --- | --- |
| Scenario range | 10.91 – 10.96 |
| Type | E |
| Files covered | `lex/api/views/file_operations/FileDownload.py`, `lex/api/views/sharepoint/SharePointFileDownload.py`, `SharePointShareLink.py`, `SharePointPreview.py` |
| Test file | `lex/test_project/tests/api_layer/test_10p_file_download_audit.py` |
| Test classes | `TestCluster10p_FileDownloadAudit` (10.91 local download → record, field, filename, size and `delivery: stream`, generic relation pointing at the record; 10.92 a refused download → `failure` with its status code and nothing attached; 10.93 GCS → `delivery: url`) · `TestCluster10p_SharePointDownloadAudit` (10.94 SharePoint stream; 10.95 share link; 10.96 preview link, each told apart by `delivery`) |
| Fixtures | `DownloadableDocument` in `tests/api_layer/models.py` — the cluster's own `FileField` model; `AuditLog` + `AuditLogStatus` in `e2e_framework_models`; SharePoint's SDK mocked at the project boundary |
| Tests landed | **6 pass / 0 fail** |
| Coverage gain | audit coverage for all four file-serving endpoints and both storage branches of `file-download`; 10l's `FieldFile` stubs completed with `name` / `size` |
| Status | ✅ Complete — see [session note](../../progress/sessions/2026-10-22-export-download-audit.md) |

---

---

### Batch 10q — the Process and ProcessFlow endpoints are gone from the route table ✅

| Property | Value |
| --- | --- |
| Scenario range | 10.97 – 10.99 |
| Type | U |
| Files covered | `lex/process_admin/sites/process_admin_site.py` (the url list), `lex/api/views/process_flow/` (deleted), `lex/process_admin/views/__init__.py` and `lex/api/views/ModelStructureObtainView.py` (`ProcessStructure` deleted) |
| Test file | `lex/test_project/tests/api_layer/test_10q_process_routes_removed.py` |
| Test classes | `TestCluster10q_ProcessRoutesRemoved` (10.97–10.99) |
| Fixtures | none — the project's own URLconf, walked for every `(name, path)` it serves |
| Tests landed | **3 pass / 0 fail** |
| Also changed | 10.83 no longer drives the removed second write path; 4.90 retired (it pinned that endpoint alone); 4.105 creates through the CRUD endpoint instead; 4.108 drops its probe |
| Status | ✅ Complete — resolves pending decision 4 in [clusters/README.md](../README.md) for `CreateOrUpdate` |

| Scenario | Title | Asserts |
| --- | --- | --- |
| 10.97 | `run_step` is not routed | `reverse` raises `NoReverseMatch`, no route named `run_step` or served under `api/run_step/`, `lex.api.views.process_flow[.CreateOrUpdate]` raises `ModuleNotFoundError` |
| 10.98 | process structure is not routed | `reverse` raises `NoReverseMatch`, no route named `process` or served under `api/process/`, neither module exposes `ProcessStructure` |
| 10.99 | the neighbours are untouched | the ten endpoints the bundled frontend loads a project with all still reverse |

**Why a regression matters:** `run_step` was a second write path into every model, carrying its own copy of the permission checks the generic CRUD endpoints apply. A route that comes back by accident is a write surface nobody audits any more. And the removal had to be surgical — it ran through the one url list the whole frontend is served from, so 10.99 is the half that proves nothing else went with it.

**Allocation note:** first written as 10p/10.91–10.94. [10p](#batch-10p--file-download-endpoints-write-an-audit-entry-) (#840) landed that letter and range on `lex-app-v2` while this branch was open, so the batch re-took the next free pair when the base was merged in. Letters are never renumbered; this one moved before it ever landed.

---

### Batch 10r — `api/htmlreport/<name>` hands a report the request, and the Streamlit frame a credential ✅

| Property | Value |
| --- | --- |
| Scenario range | 10.100 – 10.102 |
| Type | E (10.102 also U) |
| Files covered | `lex/process_admin/views/model_relation_views.py` (`Overview`, `render_html_report`), `lex/lex_app/streamlit/Streamlit.py` (`get_html`) |
| Test file | `lex/test_project/tests/api_layer/test_10r_html_report_request_and_streamlit_frame.py` |
| Test classes | `TestCluster10r_StreamlitFrameCredential` (10.100–10.102), `TestCluster10r_RenderDispatch` (10.102, unit half) |
| Fixtures | the real `Streamlit` report and two probe `HTMLReport` subclasses registered on `processAdminSite.html_reports` for the test; `E2ETestCase`'s session-authenticated `APIClient`, with `oidc_access_token` set and cleared on the session |
| Tests landed | **4 pass / 0 fail** |
| Status | ✅ Complete |

| Scenario | Title | Asserts |
| --- | --- | --- |
| 10.100 | the sidebar entry frames a dashboard that can let the user in | the frame's URL is on `STREAMLIT_URL` and carries the session's access token as `auth_token`, and the response carrying it answers `Cache-Control: no-store` |
| 10.101 | nothing to hand it means nothing handed | no OIDC token on the session → the host is still framed, with no `auth_token` key at all |
| 10.102 | the published `get_html(self, user)` is unchanged | a report declaring only `(self, user)` renders through the route untouched; one declaring a second parameter receives the request; a `TypeError` raised *inside* a report is not mistaken for a signature |

**Why a regression matters:** the customer-visible failure was not a broken page — it was the whole browser tab leaving the instance. Clicking **Streamlit** framed the dashboard host with no credential, the proxy 401'd the frame, and the framed-401 page moved the top window to that host's login, where a live SSO session completed the round trip before anyone could read it. 10.101 is the half that keeps the fix honest: an absent credential must stay absent rather than become a placeholder the proxy has to parse and reject. 10.102 is the blast radius — this route renders *every* report in a project, so a signature change here breaks every project report at once.

---

### Batch 10ad — `model_info` ships the project's per-column display options ✅

| Property | Value |
| --- | --- |
| Scenario range | 10.184 – 10.187 |
| Type | U |
| Files covered | `lex/api/views/model_info/Fields.py` (`create_list_ui_info`), `lex/api/serializers/list_ui_columns.py` |
| Test file | `lex/test_project/tests/api_layer/test_10ad_list_ui_column_options.py` |
| Test classes | `TestCluster10ad_ListUiColumnOptions` (10.184–10.187) |
| Fixtures | none — hand-rolled `model._meta` / serializer stand-ins, as 10i does |
| Tests landed | **5 pass / 0 fail** |
| Also changed | 10.25 and 12.35 no longer assert that `list_ui` has nothing else in it — their claims (the `hide_actions_column` flag is reflected, and survives wrapping) are unchanged |
| Status | ✅ Complete — LEX-1556; paired with [12p](../12-serializers/batches.md) and frontend F03p/F03q |

| Scenario | Title | Asserts |
| --- | --- | --- |
| 10.184 | the declaration reaches the frontend | `list_ui.columns` keyed by field name with the declared options; `hide_actions_column` still answered |
| 10.185 | a plain DRF serializer gets the same contract | a non-`LexSerializer` class answers identically, flag and columns |
| 10.186 | a model declaring nothing answers the key | `columns: {}` |
| 10.187 | a malformed declaration never breaks the endpoint | the response arrives; the one usable declaration survives; a non-mapping declaration is simply absent |

**Why a regression matters:** this payload is fetched before the frontend can draw a table
at all. An exception raised while describing a **display preference** would take down every
list view of that model — catastrophically out of proportion to the mistake that caused it.
That is why 10.187 exists and why the normaliser validates rather than passes through.

10.185 is the surface most likely to be missed: the endpoint serves both `LexSerializer`
subclasses and a project's own hand-written DRF class, and it is the hand-written kind that
a project reaching for column options is most likely to have.
