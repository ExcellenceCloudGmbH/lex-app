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

### Batch 10o — Project API endpoints mounted through lex; Process route removed ✅

| Property | Value |
| --- | --- |
| Scenario range | 10.83-10.91 |
| Type | E |
| Files covered | `lex/lex_app/urls.py`, `lex/api/project/` (views, public_paths), `lex/authentication/middleware.py` (public paths), `lex/process_admin/sites/process_admin_site.py` (Process removed) |
| Test file | `lex/test_project/tests/api_layer/test_10o_project_api.py` |
| Test classes | `TestCluster10o_ProjectApi`, `TestCluster10o_ProjectApiUnmounted` |
| Fixtures | `api_layer/_project_api.py` as API_URLCONF; `ApiProjectItem` (row-level read rule); `use_lex_extensions` helper on E2ETestCase |
| Tests landed | **9 pass / 0 fail** |
| Status | ✅ Complete |
| Note | E2ETestCase.use_lex_extensions overrides the extension keys and rebuilds the URL configuration for one test, undoing both afterwards. |

### Batch 10p — Error bodies: no tracebacks by default, structured on request ✅

| Property | Value |
| --- | --- |
| Scenario range | 10.92-10.97 |
| Type | E |
| Files covered | `lex/api/errors.py`, error bodies in `lex/api/views/model_entries/One.py` and `process_flow/CreateOrUpdate.py`, REST_FRAMEWORK EXCEPTION_HANDLER |
| Test file | `lex/test_project/tests/api_layer/test_10p_error_contract.py` |
| Test classes | `TestCluster10p_ErrorContract` |
| Fixtures | `ApiProjectItem` whose save() raises for name 'explode'; `_project_api.py` crash and database-failure views |
| Tests landed | **6 pass / 0 fail** |
| Status | ✅ Complete |
| Note | The traceback stays in the audit log's failure entry; only the HTTP body loses it. |

### Batch 10q — OpenAPI schema of the project API ✅

| Property | Value |
| --- | --- |
| Scenario range | 10.98-10.101 |
| Type | E |
| Files covered | `lex/api/schema.py`, `lex/api/schema_extensions.py`, `lex/api/management/commands/schema.py`, schema routes in `lex/lex_app/urls.py` |
| Test file | `lex/test_project/tests/api_layer/test_10q_openapi_schema.py` |
| Test classes | `TestCluster10q_OpenApiSchema` |
| Fixtures | `_project_api.py`; SCHEMA with title, version and public=True |
| Tests landed | **4 pass / 0 fail** |
| Status | ✅ Complete |
| Note | drf-spectacular 0.28.0 is pinned in requirements.txt and installed in INSTALLED_APPS; nothing runs until a schema is requested. |
