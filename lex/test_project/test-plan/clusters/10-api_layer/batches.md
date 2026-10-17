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

### Batch 10r — Database context per request and per after-commit callback ✅

| Property | Value |
| --- | --- |
| Scenario range | 10.102-10.105 |
| Type | I + E |
| Files covered | `lex/core/db_context.py` (LexDbContextMiddleware, apply, on_commit), `lex/api/project/views.py` (LexAPIView.initial), MIDDLEWARE |
| Test file | `lex/test_project/tests/api_layer/test_10r_db_context.py` |
| Test classes | `TestCluster10r_DbContext` |
| Fixtures | a hook writing the PostgreSQL setting lex.cluster10_user; the public db-context probe in `_project_api.py` |
| Tests landed | **4 pass / 0 fail** |
| Status | ✅ Complete |
| Note | Runs against PostgreSQL's set_config/current_setting, the mechanism a row-level-security project uses. |

### Batch 10s — Record and collection actions ✅

| Property | Value |
| --- | --- |
| Scenario range | 10.106-10.112 |
| Type | E |
| Files covered | `lex/api/actions.py` (lex_action, actions_for, ModelActionView), action routes in `lex/process_admin/sites/process_admin_site.py` |
| Test file | `lex/test_project/tests/api_layer/test_10s_record_actions.py` |
| Test classes | `TestCluster10s_RecordActions` |
| Fixtures | `ActionItem` with approve, explode, download, unguarded and batch actions |
| Tests landed | **7 pass / 0 fail** |
| Status | ✅ Complete |
| Note | calculate=true/cancel=true stay as they are; actions are additive. |

### Batch 10t — Keycloak calls and session rows per request ✅

| Property | Value |
| --- | --- |
| Scenario range | 10.113-10.116 |
| Type | U + I |
| Files covered | `lex/api/middleware/keycloak_permissions.py`, `lex/authentication/middleware.py` (StatelessBearerSessionMiddleware) |
| Test file | `lex/test_project/tests/api_layer/test_10t_keycloak_and_bearer_performance.py` |
| Test classes | `TestCluster10t_KeycloakManagerAndCache`, `TestCluster10t_StatelessBearer` |
| Fixtures | a fake KeycloakManager counting instances and UMA calls; unsigned JWT-shaped tokens with exp; a patched bearer backend |
| Tests landed | **4 pass / 0 fail** |
| Status | ✅ Complete |
| Note | The shared manager is rebuilt when the KeycloakManager class is replaced (tests patching it, 4.32b). |

### Batch 10u — Schema, docs and CLI share the SCHEMA settings ✅

| Property | Value |
| --- | --- |
| Scenario range | 10.117-10.121 |
| Type | E + U |
| Files covered | `lex/api/schema.py` (project_schema_settings, ProjectSchemaView, ProjectRedocView), `lex/api/management/commands/schema.py`, `lex/api/schema_extensions.py` (LexAPIKeyAuthenticationScheme) |
| Test file | `lex/test_project/tests/api_layer/test_10u_schema_settings.py` |
| Test classes | `TestCluster10u_SchemaSettings` |
| Fixtures | the project API fixture (`_project_api`), SCHEMA with REDOC_DIST and ENUM_NAME_OVERRIDES |
| Tests landed | **5 pass / 0 fail** |
| Status | ✅ Complete |
| Note | The overridden get() methods keep extend_schema(exclude=True), as drf-spectacular's own. |

### Batch 10v — Every error of a generic endpoint reaches the exception handler ✅

| Property | Value |
| --- | --- |
| Scenario range | 10.122-10.127 |
| Type | E |
| Files covered | `lex/api/errors.py` (error_from_body, raise_or_return, surface), `lex/api/views/model_entries/One.py`, `Many.py`, `History.py`, `lex/api/views/model_entries/mixins/DestroyOneWithPayloadMixin.py`, `lex/api/views/process_flow/CreateOrUpdate.py`, `lex/api/views/file_operations/ModelExport.py`, `lex/process_admin/views/model_relation_views.py`, `lex/api/views/model_entries/mixins/ModelEntryProviderMixin.py`, `lex/core/security_flags.py` (ERRORS) |
| Test file | `lex/test_project/tests/api_layer/test_10v_errors_reach_the_handler.py` |
| Test classes | `TestCluster10v_ErrorsReachTheHandler`, `TestCluster10v_ExportAndReports` |
| Fixtures | ApiProjectItem with patched permission hooks and save(), a recording EXCEPTION_HANDLER, DB_ERROR_MAPPER, the export models |
| Tests landed | **6 pass / 0 fail** |
| Status | ✅ Complete |
| Note | Direct-call unit tests of the destroy mixin (10k) and file download (10l) now see the raised LexActionError; they render it as the handler would. |

### Batch 10w — The scope a request acts in (REQUEST_SCOPE) ✅

| Property | Value |
| --- | --- |
| Scenario range | 10.128-10.131 |
| Type | U + E |
| Files covered | `lex/core/request_scope.py`, `lex/core/db_context.py` (apply with scope), `lex/api/views/model_entries/List.py` (reserved parameter), `lex/api/views/model_entries/mixins/ModelEntryProviderMixin.py` (get_queryset, initial), `lex/api/views/model_entries/History.py` (_record_readable), `lex/api/actions.py` (_resolve_instance), `lex/core/extensions.py` |
| Test file | `lex/test_project/tests/api_layer/test_10w_request_scope.py` |
| Test classes | `TestCluster10w_ScopeUnit`, `TestCluster10w_RequestScope` |
| Fixtures | ActionItem with a patched lex_scope_queryset, a test resolver refusing one value and defaulting another, a recording DB_CONTEXT |
| Tests landed | **5 pass / 0 fail** |
| Status | ✅ Complete |
| Note | A row outside the scope has no history either: History judges the live row by the scoped queryset before falling back to the last known state of deleted rows. |

### Batch 10x — Read models: views and projections on the generic endpoints ✅

| Property | Value |
| --- | --- |
| Scenario range | 10.132-10.137 |
| Type | U + E |
| Files covered | `lex/core/read_models.py` (is_read_model, validate, list_queryset, CreateView), `lex/core/models/LexReadModel.py`, `lex/lex_app/simple_history_config.py`, `lex/api/views/model_entries/mixins/ModelEntryProviderMixin.py` (writes refused, as_of), `lex/api/views/model_entries/List.py`, `lex/api/views/model_entries/History.py`, `lex/api/serializers/base_serializers.py` (lex_prepare_page), `lex/core/extensions.py` |
| Test file | `lex/test_project/tests/api_layer/test_10x_read_models.py` |
| Test classes | `TestCluster10x_ReadModelUnit`, `TestCluster10x_ReadModels` |
| Fixtures | ActionItemSummary (a security_invoker view over ActionItem created with CreateView), ActionItemCard (a proxy projection with its own serializer, list selection and page preparation) |
| Tests landed | **6 pass / 0 fail** |
| Status | ✅ Complete |
| Note | A proxy inherits its base table's history manager; the history endpoint and as_of check is_read_model first, so a projection never shows the base table's history. |

### Batch 10y — Actions as the operation primitive: input-aware permission, answers, throttle, audit ✅

| Property | Value |
| --- | --- |
| Scenario range | 10.138-10.143 |
| Type | E |
| Files covered | `lex/api/actions.py` (lex_action options, ActionResult, ActionThrottle, ModelActionView _permitted/_respond/_audit/_audit_failure/get_throttles, denied_answer), `lex/core/extensions.py` (ACTION_DENIED, THROTTLE_RATES, _validate_actions), `lex/audit_logging/models/AuditLog.py` (action choice), `lex/audit_logging/migrations/0009_auditlog_action_choice.py`, `lex/api/project/__init__.py` |
| Test file | `lex/test_project/tests/api_layer/test_10y_hardened_actions.py` |
| Test classes | `TestCluster10y_HardenedActions` |
| Fixtures | ActionItem's cluster-10y actions (spend, hidden_action, state_bound, look, ping, upsert, fail_loose), a throttle rate set per test, the audit tables |
| Tests landed | **6 pass / 0 fail** |
| Status | ✅ Complete |
| Note | A permission method taking (user_context, data) receives the validated input; one taking only user_context is called as before, so existing actions are unchanged. |
