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

> `ModelExport.py` (cluster 13f), `List.py` AG-Grid path (14f), `base_serializers.py` (12g) keep their forecasted homes.

---

### Batch 10o — Read-only calculation-status endpoint ✅

| Property | Value |
| --- | --- |
| Scenario range | 10.72 – 10.83 |
| Type | E |
| Files covered | `views/calculations/CalculationStatus.py` (`CalculationStatus`, `_readable_or_none`, `_envelope`, `_calculate_permission`, `_denial_reason`, `_TriggerProbeRequest`, `_latest_run_rows`, `_run_window`, `_log_tail`), route `GET /api/model_entries/<model:model_container>/<int:pk>/calculation-status` |
| Test file | `lex/test_project/tests/api_layer/test_10o_calculation_status_endpoint.py` |
| Test classes | `TestCluster10o_CalculationStatusEndpoint` (10.72–10.75, 10.77–10.81 — the status contract), `TestCluster10o_CalculationStatusEndpoint_ReadDenied` (10.76 — read permission, on `AuthenticatedE2ETestCase` because the denial is a real user's), `TestCluster10o_CalculationStatus_CalculatePermission` (10.82–10.83 — `can_calculate`, each scenario also issuing the real trigger) |
| Fixtures | none — `ApiLayerCalc` / `ApiLayerCalcReadRestricted` / `ApiLayerCalcCalculateRestricted` from the cluster's `models.py`, plus an in-file `_make_logs` helper that writes `CalculationLog` rows and rewrites their `auto_now_add` stamps so the run has one unambiguous order. `ApiLayerCalcCalculateRestricted` carries a `modification_restriction` that refuses one Django group, which is what `One.update` actually consults — a model readable by everyone and runnable only by some |
| Tests landed | **12 pass / 0 fail** |
| Coverage gain | measured locally; the new status view end to end |
| Prereqs | none |
| Status | ✅ Complete — 12 pass / 0 fail |
| Note | The narrow read the Streamlit widget (batch 1ab) polls every two seconds. Polling a full record serialization to learn one enum is wasteful on wide models and cannot carry the log tail, hence a purpose-built endpoint — but a purpose-built endpoint is also a new place for a permission to be forgotten. 10.76 is the gate: reads go through `UserReadRestrictionFilterBackend`, the same backend `ListModelEntries` applies to every list read, rather than a hand-rolled check — read permission in this codebase is a *queryset filter*, not a boolean, and reusing the backend inherits every special case for free. An unreadable record answers with the same 404 **and the same body** as a missing one, because a distinguishable response would itself confirm the record exists and leak its calculation state. The rest is poll economics and honesty about *which run* is being described: `include_log` is opt-in so the ordinary poll never touches `CalculationLog` (10.77); the tail is bounded to `LOG_TAIL_LIMIT = 50` and reports truncation from one over-fetched row rather than a COUNT over a table that grows with every line ever logged (10.78–10.79); and both the tail and the run timings are scoped by `Subquery` to the newest `calculationId` (10.80–10.81) — the record carries no timestamp of its own, since PR #675 deliberately leaves `edited_at` unstamped by a calculation, so an unscoped window would report days for a run that took seconds and pad a short re-run's tail out of the previous run's lines. 10.73 keeps `ABORTED` and `CANCELLED` distinct from `ERROR`. **10.82–10.83** add `can_calculate` / `calculate_denied_reason`, the flag the widget draws its button from. Read permission cannot answer "may this caller run it" — `ApiLayerCalcCalculateRestricted` is readable by everyone and runnable only outside one group, so inferring one from the other is wrong for exactly that record. The endpoint therefore instantiates `UserPermission`, the permission class `OneModelEntry` itself declares, and evaluates it against the trigger's own `{"calculate": "true"}` payload; nothing is re-derived, so the two cannot drift. Both scenarios also issue the real `PATCH` and assert its status, because the flag is a promise about a *different* endpoint and a promise nobody checks is how these come apart — 10.82 pins the refusal at 403, 10.83 pins acceptance at 202. The asymmetry matters: an enabled button that then 403s is recoverable and the widget already handles it, a button disabled for someone who may run the record is a dead end, which is why a permission check that raises reports the button as enabled. |

---
