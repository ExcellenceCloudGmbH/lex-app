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

### Batch 10s — The usage payload: api/user/'s "usage" key, the per-instance hash, the switch ✅

| Property | Value |
| --- | --- |
| Scenario range | 10.102 – 10.116 |
| Type | U (10.113, 10.116 I/E — drive the real `api/user/` view) |
| Files covered | `lex/core/services/usage_config.py` (new), `lex/authentication/views/user_api.py` (`CurrentUser.get`) |
| Test file | `lex/test_project/tests/api_layer/test_10s_usage_payload.py` |
| Test classes | `TestCluster10s_UsageMetricsSwitch` (10.102–10.105), `TestCluster10s_UserHash` (10.106–10.107, 10.114), `TestCluster10s_UsagePayload` (10.108–10.112, 10.115), `TestCluster10s_CurrentUserEndpoint` (10.113, 10.116) |
| Fixtures | a minimal `_User` stand-in carrying only `username`, for the pure-logic tests; a real `auth.User` (`get_user_model().objects.create_user`) for the endpoint tests, driven with `APIRequestFactory` + `force_authenticate` as `test_10m_calculation_log_tree.py` does |
| Tests landed | **15 pass / 0 fail** |
| Status | ✅ Complete |
| Note | `user_hash()`'s formula is pinned by a known-answer case (10.114) against a hash computed independently in the test, which also fixes the salt's encoding: its literal UTF-8 bytes are the HMAC key, since `LEX_USAGE_SALT` is an opaque string like `LEX_API_KEY` (never hex-decoded), not `bytes.fromhex(salt)`. The endpoint tests (10.113, 10.116) mock only the external OIDC userinfo call (`lex.authentication.views.user_api.requests.get`) and drive the view's real code path, which merges `usage_payload(request.user)` into the unchanged userinfo body and falls back to `{"enabled": false}` when building it fails. |

| Scenario | Title | Asserts |
| --- | --- | --- |
| 10.102 | unset defaults to enabled | `usage_metrics_enabled()` True with `LEX_USAGE_METRICS` unset |
| 10.103 | recognised true-like values | true/1/yes/on, case- and whitespace-insensitive |
| 10.104 | recognised false-like values | false/0/no/off, case- and whitespace-insensitive |
| 10.105 | unrecognised value fails closed, warns once | an invalid value is off, with exactly one `logger.warning` across two calls |
| 10.106 | user_hash deterministic | the same user + salt hash the same way, 32 hex characters |
| 10.107 | different salt, different hash | the per-instance salt is load-bearing for cross-instance privacy |
| 10.108 | full payload when fully configured | `enabled`, `posthog_key`, default `posthog_host`, `instance_rid`, `user_hash` |
| 10.109 | explicit POSTHOG_HOST honoured | an override replaces the EU default |
| 10.110 | switch off collapses to {"enabled": false} | no other keys present |
| 10.111 | missing salt collapses to {"enabled": false} | no other keys present |
| 10.112 | empty POSTHOG_PROJECT_KEY collapses to {"enabled": false} | no other keys present |
| 10.113 | api/user/ carries "usage" | the real `CurrentUser` view's response includes `usage` matching `usage_payload(request.user)`, userinfo fields unchanged |
| 10.114 | hash matches the formula, known-answer | `user_hash()` equals `hmac.new(salt utf-8, username utf-8, sha256).hexdigest()[:32]` computed independently |
| 10.115 | unset instance identifier collapses to {"enabled": false} | `INSTANCE_RESOURCE_IDENTIFIER` empty or absent, no other keys present |
| 10.116 | a failing usage payload never blocks sign-in | `usage_payload` raising still gives 200, the userinfo fields, `usage` exactly `{"enabled": false}` and one WARNING |
