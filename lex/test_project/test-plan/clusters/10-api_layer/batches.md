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

### Batch 10o — Usage recorder and interface area events (fleet metrics, phase 1) ✅

| Property | Value |
| --- | --- |
| Scenario range | 10.83 – 10.118 |
| Type | U + I + E |
| Files covered | `lex/core/sql/usage_metrics.sql`, `lex/core/migrations/0002_usage_metrics.py`, `lex/core/services/usage_areas.py`, `lex/core/services/usage_metrics.py`, `lex/core/middleware/usage_recorder.py`, `lex/api/views/usage/UsageArea.py`, the `usage-area` route in `lex/process_admin/sites/process_admin_site.py` |
| Test file | `lex/test_project/tests/api_layer/test_10o_usage_recorder.py` |
| Test classes | `TestCluster10o_UsageTables`, `TestCluster10o_AreaMap`, `TestCluster10o_Rules`, `TestCluster10o_Recorder`, `TestCluster10o_RecorderEndToEnd`, `TestCluster10o_UsageAreaEndpoint` |
| Fixtures | api_layer `ALL_MODELS`; `usage_metrics.clear_all()` + `usage_metrics.recorder.reset()` in setUp/tearDown (the tables are raw SQL, outside the model registry, so per-test flush never clears them); a CSRF-enforcing `APIClient(enforce_csrf_checks=True)` for the endpoint |
| Tests landed | **36 pass / 0 fail** |
| Status | ✅ Complete |
| Note | fleet metrics phase 1 (LEX-749), release R3. Design: `docs/superpowers/specs/2026-09-29-fleet-metrics-phase1-design.md` §5.3.1–§5.3.2. Raw-SQL tables (`lex_usage_daily` -- no time of day, only a UTC day; `lex_runtime_versions`, PostgreSQL-only — classes skip cleanly elsewhere) keyed so API activity (`api`) and interface use (`ui`/`ui_embed`) never merge; 10.103 guards the API area map against renamed routes; 10.104 pins `UI_AREAS` to the spec, which the interface mirrors in `src/usage/areas.ts` (process-admin-general-client batch F02 `d`). The endpoint is session-only with DRF's CSRF check in force (10.111) and never accepts an API key (10.110). 10.115–10.117 pin the hourly version throttle and the switch's fail-closed parsing; 10.118 checks that a request with no actor (an anonymous `/health` probe) writes neither table, including the version row, and that an authenticated request right after it still finds the version due. `TestCluster10o_RecorderEndToEnd` and `TestCluster10o_UsageAreaEndpoint` now skip on a non-PostgreSQL backend before their parent's `setUp` runs patches, instead of erroring on the missing tables. |

---
