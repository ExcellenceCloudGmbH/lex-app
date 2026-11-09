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

### Batch 10aa — lex's REST surface speaks the HTTP auth contract v2 ✅

| Property | Value |
| --- | --- |
| Scenario range | 10.152 – 10.166 |
| Type | E |
| Files covered | `lex/authentication/drf_exceptions.py` (new: `exception_handler`, `refusal_response`), `lex/authentication/authentication_backends/BearerMiddlewareAuthentication.py` (`authenticate_header`, bearer contexts only), `lex/authentication/identity.py` (new), `lex/authentication/views/user_api.py`, `lex/authentication/views/session_views.py` (new), `lex/authentication/capabilities.py` (new), `lex/authentication/views/token_views.py`, `lex/api/views/model_entries/One.py` and `mixins/DestroyOneWithPayloadMixin.py` (refusal bodies), `lex/lex_app/settings.py` (`REST_FRAMEWORK`), `lex/contracts/auth/v2/fixtures/http/*.json` (read) |
| Test file | `lex/test_project/tests/api_layer/test_10aa_auth_contract_v2.py` |
| Test classes | `TestCluster10aa_NotAuthenticated` (10.152–10.154), `TestCluster10aa_Refusals` (10.155), `TestCluster10aa_CurrentUser` (10.156–10.157), `TestCluster10aa_SessionEndpoints` (10.158–10.161), `TestCluster10aa_ContractFixtures` (10.162–10.166) |
| Fixtures | `create_oidc_session` sign-ins; django-oauth2-codeflow's `FakeOP` (bearer tokens, scripted refresh answers, the authorization round trip); `ContractItem` (new in `api_layer/models.py`: only superusers create/delete); the UMA call patched; outgoing HTTP refused in 10.156 |
| Tests landed | **15 pass / 0 fail** against django-oauth2-codeflow 3.0.0 |
| Also changed | 10o 10.90: the middleware builds no `KeycloakManager` (UMA is one cached call per token; roles come from the token) |
| Status | ✅ Complete — see [session note](../../progress/sessions/2026-11-06-auth-long-sessions-core.md) |

| Scenario | Title | Asserts |
| --- | --- | --- |
| 10.152 | anonymous DRF → 401 | 401, `WWW-Authenticate: Session realm=…`, `code`/`error` `not_authenticated`, `login_url`, no-store (BUG-008, unauthenticated half) |
| 10.153 | rejected bearer | 401 with `Bearer error="invalid_token"` |
| 10.154 | CSRF stays on | a session POST with a made-up bearer header and no CSRF token: 403 `csrf_failed`, no token handed out |
| 10.155 | refusals | create/delete 403 `permission_denied` (+ `detail`, documented `message`); DRF `PermissionDenied` coded; CSRF 403 `csrf_failed`; still signed in |
| 10.156 | `api/user/` from the sign-in | profile + `client_roles` for browser, bearer and Django-only logins; no bookkeeping, no token, no HTTP call |
| 10.157 | `api/user/` when unusable | ended sign-in 401 `session_expired` then signed out; Keycloak down 503 + `Retry-After`, sign-in kept |
| 10.158 | `api/auth/session` | 200 `{authenticated, user, capabilities}`; anonymous 401; process-admin route; capabilities from the helper |
| 10.159 | `oidc/auth-complete` | result message to own origin (opener, parent, BroadcastChannel); ok/false + error; hostile input not reflected; SAMEORIGIN; no token |
| 10.160 | silent re-auth round trip | `prompt=none` → `login_required` → ok:false; then a sign-in → ok:true and signed in |
| 10.161 | dashboard token | a token about to expire is renewed first; 401 after the sign-in ended |
| 10.162–10.166 | contract fixtures | `session_missing`, `session_expired`, `auth_unavailable` (refresh and UMA), `permission_denied`, `csrf_failed`: status, body keys/values, headers; no token |

**Why a regression matters:** a frontend decides from these answers whether to re-authenticate, retry or show a message. A 403 for a lost session, a 401 for a refusal, or an outage that looks like either signs users out or strands them on a dead page.

**Allocation note:** Letter aa from 10.152: r–z and scenarios up to 10.151 are held by in-flight branches (feat/ext-*, integration/lex-3-extensions, feat/fleet-metrics-*, feat/multi-value-filters, fix/streamlit-report-frames-unauthenticated).

---

### Batch 10ab — The app's half of the Streamlit hand-off ✅

| Property | Value |
| --- | --- |
| Scenario range | 10.167 – 10.174 |
| Type | E |
| Files covered | `lex/authentication/views/handoff_views.py` (new), `lex/authentication/dashboards/handoff.py` (`issue_code`, `redeem`), `lex/authentication/dashboards/sites.py`, `lex/authentication/capabilities.py` (`dashboard_handoff_surfaces`), `lex/authentication/views/token_views.py` (handoff placeholder), `lex/lex_app/urls.py` (`oidc/handoff`), `lex/lex_app/settings.py` (login and session exemptions) |
| Test file | `lex/test_project/tests/api_layer/test_10ab_dashboard_handoff_endpoint.py` |
| Test classes | `TestCluster10ab_SignedIn` (10.167), `TestCluster10ab_NotSignedIn` (10.168–10.169), `TestCluster10ab_Refusals` (10.170–10.171), `TestCluster10ab_Advertised` (10.172–10.174) |
| Fixtures | sign-ins through `lex.lex_app.tests.auth.sign_in`; `LEX_STREAMLIT_AUTH` and `LEX_INSTANCE_HOSTS` from the environment; `FakeOP` answering 503 for the outage |
| Tests landed | **8 pass / 0 fail** (13 subtests) |
| Mutation check | any return origin accepted, frames sent to the sign-in, the capability never advertised, no placeholder: each fails its scenario (10.170, 10.168, 10.172, 10.173) |
| Status | ✅ Complete — see [session note](../../progress/sessions/2026-11-06-lex-surfaces-streamlit.md) |

**Why a regression matters:** see [cluster.md § 10ab](cluster.md). The proxy's half of the hand-off is [01-init batch 1be](../01-init/batches.md).

**Allocation note:** Letter ab, from 10.167: r–z are held by in-flight branches (see [10aa](#batch-10aa--lexs-rest-surface-speaks-the-http-auth-contract-v2-)).
