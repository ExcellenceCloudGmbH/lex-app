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

---

### Batch 10o — `api/htmlreport/<name>` hands a report the request, and the Streamlit frame a credential ✅

| Property | Value |
| --- | --- |
| Scenario range | 10.72 – 10.74 |
| Type | E (10.74 also U) |
| Files covered | `lex/process_admin/views/model_relation_views.py` (`Overview`, `render_html_report`), `lex/lex_app/streamlit/Streamlit.py` (`get_html`) |
| Test file | `lex/test_project/tests/api_layer/test_10o_html_report_request_and_streamlit_frame.py` |
| Test classes | `TestCluster10o_StreamlitFrameCredential` (10.72–10.74), `TestCluster10o_RenderDispatch` (10.74, unit half) |
| Fixtures | the real `Streamlit` report and two probe `HTMLReport` subclasses registered on `processAdminSite.html_reports` for the test; `E2ETestCase`'s session-authenticated `APIClient`, with `oidc_access_token` set and cleared on the session |
| Tests landed | **4 pass / 0 fail** |
| Status | ✅ Complete |

| Scenario | Title | Asserts |
| --- | --- | --- |
| 10.72 | the sidebar entry frames a dashboard that can let the user in | the frame's URL is on `STREAMLIT_URL` and carries the session's access token as `auth_token`, and the response carrying it answers `Cache-Control: no-store` |
| 10.73 | nothing to hand it means nothing handed | no OIDC token on the session → the host is still framed, with no `auth_token` key at all |
| 10.74 | the published `get_html(self, user)` is unchanged | a report declaring only `(self, user)` renders through the route untouched; one declaring a second parameter receives the request; a `TypeError` raised *inside* a report is not mistaken for a signature |

**Why a regression matters:** the customer-visible failure in 2.1.12 was not a broken page — it was the whole browser tab leaving the instance. Clicking **Streamlit** framed the dashboard host with no credential, the proxy 401'd the frame, and the framed-401 page moved the top window to that host's login, where a live SSO session completed the round trip before anyone could read it. 10.73 is the half that keeps the fix honest: an absent credential must stay absent rather than become a placeholder the proxy has to parse and reject. 10.74 is the blast radius — this route renders *every* report in a project, so a signature change here breaks every project report at once.
