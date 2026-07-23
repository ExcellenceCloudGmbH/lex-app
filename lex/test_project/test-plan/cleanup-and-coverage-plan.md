# Cleanup & Coverage Completion Plan (May 2026)

> **Source:** Supervisor categorisation of ~150 framework files into EXCLUDE / DELETE / COMPLETE-TESTS / LATER.
> **Status:** EXCLUDE applied to `.coveragerc`. DELETE candidates dependency-checked. COMPLETE-TESTS mapped to clusters.
> **Back to:** [Test Plan Index](index.md) · [Clusters](clusters/) · [Progress](progress.md)

---

## 1. EXCLUDE — done ✅

The 16 files below have been added to **both** `[run] omit` and `[report] omit` in `.coveragerc`. They are scaffolding / legacy / console-only code and do not warrant coverage.

```
lex/test_project/apps.py
lex/tools/test_groups.py
lex/api/views/sharepoint/DeleteUnusedFiles.py
lex/lex_app/routing.py
lex/audit_logging/handlers/ConsoleHandler.py
lex/authentication/utils/lex_authentication.py
lex/utilities/channel_layer.py
lex/legacy_data/admin/read_only_admin.py
lex/api/utils/collection_utils.py
lex/core/models/HTMLReport.py
lex/legacy_data/models/legacy_calculation_id.py
lex/legacy_data/models/legacy_log.py
lex/legacy_data/models/legacy_user_change_log.py
lex/api/views/project_info/ProjectInfo.py
lex/legacy_data/models/legacy_calculation_log.py
lex/legacy_data/serializers/legacy_data_serializers.py
```

---

## 2. DELETE — dependency triage

> **Re-verified May 12, 2026** — second-pass grep on every exported symbol (function/class names, not just module paths) revealed several files that the first pass had marked as "needs cleanup" are actually fully dead, and one new dead duplicate was discovered.

### 2a. EXECUTED ✅ — fully dead, deleted

These 10 files had **zero live references** after the second-pass symbol grep and have been **removed from the tree** in this pass:

| File | Why safe |
| --- | --- |
| `lex/utilities/decorators/injector.py` | `inject`/`LexInjector` — only "hit" was the English word *inject* in a comment in `import_system/model_loader.py`. |
| `lex/api/views/authentication/auth.py` | `provider_logout` — 0 refs anywhere. |
| `lex/authentication/utils/TokenContext.py` | `TokenContext` — 0 refs. |
| `lex/lex_app/groups.py` | `create_groups` — 0 refs. |
| `lex/authentication/utils/auth_helpers.py` | `resolve_user` — 0 refs (sole hit was a commented-out import in `rbac/RBACInfo.py:5`). |
| `lex/api/views/authentication/helpers.py` | `sync_user_permissions` — 0 refs. The `from .helpers import …` in `api/utils/__init__.py` resolves to **`api/utils/helpers.py`** (a different sibling, 12.9 KB, very much alive). |
| `lex/api/views/utils.py` | `get_user_name` / `get_user_email` — 0 refs. The `title_for_model` hits all resolve to `process_admin/models/utils.py` via the relative `from .utils import` in `ModelContainer.py:149` (different file). |
| `lex/process_admin/utils/temporal_reconciler.py` | `TemporalReconciler` — sole reference is in a multi-line commented-out block in `ModelEntryProviderMixin.py:50–56`. Decision from §2e is now resolved: dead. |
| `lex/api/views/authentication/UserPermissionView.py` | **Bonus find.** Dead duplicate of `lex/authentication/views/permissions.py` — same class name `UserPermissionsView`, but 0 importers. The live one is in `authentication/views/permissions.py` (wired in `process_admin_site.py:264`). |
| `lex/authentication/views.py` | Re-export shim — 0 importers. With `permissions.py`, `token_views.py`, `user_api.py` either staying or moving in their own PRs, the shim is unneeded either way. |

**Verification after deletion:**
- All 10 files removed; `python -m compileall lex/` produces only one pre-existing unrelated SyntaxError in `lex/tests/test_lex_cli.py:797` (already on the skipped-tests list).
- Re-grep confirms zero new dangling references; the only "hits" left for deleted symbols are the same English-word-in-comment and commented-out-block lines noted above.

### 2b. Safe to delete next (need a tiny side-edit)

| File | Side-edit required |
| --- | --- |
| `lex/helpers/cache_tables.py` | Sole importer is `lex/lex_app/management/commands/Init2.py`. **Init2 itself has 0 references** anywhere in the codebase (not registered, not subclassed, not invoked) and is already in the coverage `omit` list. Drop both files in the same commit. |
| `lex/core/transactions/transactions.py` | `as_transaction` is only re-exported by sibling `lex/core/transactions/__init__.py`. Nobody else uses it. Delete the **entire `lex/core/transactions/` package** (both files). |
| `lex/authentication/admin.py` | File contains only `class ProfileAdmin(admin.ModelAdmin)` with a commented-out `@admin.register(Profile)`. Nothing imports it. Delete. |
| `lex/authentication/management/commands/createprofiles.py` | The command name has 0 references; only imports `Profile` (deleted in 2c). Delete. |

### 2c. Profile model removal (chained — needs a Django migration)

`lex/authentication/models/Profile.py` is referenced by:
- `lex/authentication/admin.py` (deleted in 2b)
- `lex/authentication/management/commands/createprofiles.py` (deleted in 2b)
- `lex/authentication/models/__init__.py:1` (`from .Profile import Profile`)
- `lex/lex_app/apps.py:136` (`excluded_model_names = {'Profile', 'HistoricalProfile'}` — string compare, just remove from set)
- `lex/authentication/models.py` (placeholder comment only — can stay or be removed)
- `lex/authentication/migrations/0001_initial.py` (frozen — handled by new migration)

**Steps:**
1. Drop the `from .Profile import Profile` line in `models/__init__.py` (and the `__all__` entry if any).
2. Remove `'Profile'` and `'HistoricalProfile'` from the set in `lex_app/apps.py:136`.
3. Delete `lex/authentication/models/Profile.py`.
4. `python manage.py makemigrations authentication` to record the table drop.

### 2d. Routed in `process_admin_site.py` — supervisor decision needed

These 5 files are **actively wired into URL routes**. Deleting them is a behaviour change (endpoints disappear). Listed for the supervisor to confirm the routes can be removed:

| File | Live route in `process_admin_site.py` |
| --- | --- |
| `lex/authentication/views/permissions.py` | `path("api/user_permissions/", UserPermissionsView.as_view())` (line 264) |
| `lex/api/views/process_flow/CreateOrUpdate.py` | route at line 221 (`CreateOrUpdate.as_view()`) |
| `lex/api/views/LexLoggerView/LexLoggerView.py` | `path("api/logs", LexLoggerView.as_view(), name="log")` (line 256) |
| `lex/api/views/model_info/Widgets.py` | `path("api/widget_structure", Widgets.as_view(), name="widget-structure")` (line 245) |
| `lex/authentication/views/token_views.py` | `path('api/auth/streamlit-token/', StreamlitTokenView.as_view())` (line 194) — also tied to whether Streamlit support is being dropped wholesale. |

**These should not be deleted by an automated pass.** Each removes a public endpoint and at least one of them (`api/widget_structure`) may still be consumed by the frontend. Surface to supervisor before action.

---

## 3. `?????` — `lex/process_admin/__init__.py`

Inspected: the file is a 52-line lazy-loader using `__getattr__` to dispatch 8 attribute names (`ProcessAdminSite`, `processAdminSite`, `adminSite`, `ModelCollection`, `ModelContainer`, `ModelRegistration`, `ModelStructure`, `ModelStructureBuilder`) to their real modules, with a final `raise AttributeError` for anything else.

**Decision:** **Keep + test.** It's nine trivial branches; one parametrised test in `lex.test_project.tests.init.test_process_admin_lazy_imports` (cluster 1) gets every branch covered in ~20 lines.

---

## 4. COMPLETE THE TESTS — cluster mapping

Each ~95 file is slotted into an existing test-plan cluster. Where a file does not fit, a new sub-cluster (suffix letter) is opened. Test types: **U** = unit / `SimpleTestCase`, **I** = integration / `TestCase`, **E** = end-to-end / API.

### Cluster 1 — Init / Project Bootstrap

| File | Type | Notes |
| --- | --- | --- |
| `lex/lex_app/__init__.py` | U | App-init side effects, version export. |
| `lex/lex_app/apps.py` | U | `AppConfig.ready()` signal wiring. |
| `lex/process_admin/__init__.py` | U | Lazy `__getattr__` branches (see §3). |
| `lex/lex_app/management/commands/init.py` | I | Full `lex Init` flow on a fresh DB. |
| `lex/lex_app/keycloak_exclusions.py` | U | Excluded-path matcher edge cases. |
| `lex/lex_app/settings.py` | U | Env-var resolution + DB target switching. |
| `lex/lex_app/urls.py` | U | URL conf resolves; reverse() for key names. |
| `lex/utilities/config/generic_app_config.py` | U | Config loader defaults + overrides. |
| `lex/core/config.py` | U | Framework-level config singleton. |
| `lex/lex_app/simple_history_config.py` | U | django-simple-history wiring. |
| `lex/lex_app/views.py` | I | Top-level views (health, login redirect). |

### Cluster 2 — CRUD via REST API

| File | Type | Notes |
| --- | --- | --- |
| `lex/api/views/model_entries/One.py` | E | Single-record GET / PUT / DELETE. |
| `lex/api/views/model_entries/Many.py` | E | List + create. |
| `lex/api/views/model_entries/List.py` | E | AG Grid list view (full). |
| `lex/api/views/model_entries/filter_backends.py` | U | Filter parsing + Q construction. |
| `lex/api/views/model_entries/mixins/ModelEntryProviderMixin.py` | I | Resolves model class per request. |
| `lex/api/views/model_entries/mixins/DestroyOneWithPayloadMixin.py` | I | Delete with response payload. |
| `lex/api/views/model_entries/mixins/PermissionAwareSerializerMixin.py` | I | Per-field stripping. |
| `lex/api/views/ModelStructureObtainView.py` | E | `/api/model-structure/` endpoint. |
| `lex/api/views/model_info/Fields.py` | E | Field metadata endpoint. |
| `lex/api/views/lex_api/LexAPI.py` | E | Generic lex-API dispatcher. |
| `lex/api/utils/helpers.py` | U | Misc request helpers. |
| `lex/api/utils/Context.py` | U | Request context object. |
| `lex/api/utils/api_key_requests.py` | I | API-key-authenticated request helper. |
| `lex/api/filters/GenericFilters.py` | U | Generic filter classes. |

### Cluster 3 — Validation Hooks (no new files)

### Cluster 4 — Permissions

| File | Type | Notes |
| --- | --- | --- |
| `lex/api/middleware/keycloak_permissions.py` | I | Per-request permission resolution. |
| `lex/api/views/permissions/ModelPermissions.py` | I | Model-level allow/deny. |
| `lex/api/views/permissions/UserPermission.py` | I | Per-user permission view. |
| `lex/authentication/authentication_backends/BearerMiddlewareAuthentication.py` | I | Bearer-token middleware. |
| `lex/api/views/authentication/UserAPIView.py` **OR** `lex/authentication/views/user_api.py` | E | **Action: identify which is live and delete the other.** Then test the survivor. |

### Cluster 5 — History & Bitemporal

| File | Type | Notes |
| --- | --- | --- |
| `lex/core/services/Bitemporal.py` | I | Bitemporal core service. |
| `lex/core/services/bitemporal_signals.py` | I | Signal dispatch. |
| `lex/core/services/StandardHistory.py` | I | Standard (non-bitemporal) history. |
| `lex/core/services/MetaHistory.py` | I | Cross-model meta history. |
| `lex/process_admin/utils/bitemporal_sync.py` | I | Sync legacy → bitemporal. |
| `lex/api/views/model_entries/History.py` | E | History API endpoint. |

### Cluster 6 — Audit Logging

| File | Type | Notes |
| --- | --- | --- |
| `lex/audit_logging/models/AuditLog.py` | U+I | Model fields, indexes, statuses. |
| `lex/audit_logging/models/AuditLogStatus.py` | U | Enum / transition rules. |
| `lex/audit_logging/models/CalculationLog.py` | U+I | CalculationLog + parent_log linking. |
| `lex/audit_logging/mixins/AuditLogMixin.py` | I | Mixin behaviour on save. |
| `lex/audit_logging/mixins/BulkAuditLogMixin.py` | I | Bulk-op auditing. |
| `lex/audit_logging/utils/ModelContext.py` | U | Context manager stack. |
| `lex/audit_logging/utils/ContextResolver.py` | U | Stack → parent/current resolution. |
| `lex/audit_logging/utils/DataModels.py` | U | Dataclasses / payloads. |
| `lex/audit_logging/utils/calculation_audit.py` | I | CalculationLog helpers. |
| `lex/audit_logging/utils/InitialDataAuditLogger.py` | I | Bulk init-data path. |
| `lex/audit_logging/utils/config.py` | U | Audit feature flags. |
| `lex/audit_logging/utils/content_types.py` | U | CT cache + lookup. |
| `lex/audit_logging/utils/WebSocketNotifier.py` | I/skip | **First: confirm usage. If dead, move to DELETE.** |
| `lex/audit_logging/serializers/AuditLogSerializer.py` | I | Serializer contract. |
| `lex/audit_logging/serializers/AuditLogMixinSerializer.py` | I | Mixin-aware serializer. |
| `lex/audit_logging/serializers/CalculationLogSerializer.py` | I | CalculationLog payload shape. |
| `lex/audit_logging/handlers/LexLogger.py` | U+I | Builder + persist path. Tied to bug §1 of NOTES_TODO. |
| `lex/audit_logging/handlers/WebSocketHandler.py` | I | WS handler push. |

### Cluster 7 — Calculation State Machine

| File | Type | Notes |
| --- | --- | --- |
| `lex/core/models/LexModel.py` | I | Existing 4i covers permission helpers; add save/lifecycle gaps. |
| `lex/core/models/CalculationModel.py` | I | State transitions, execute_calculation paths. |
| `lex/core/mixins/CalculatedModelMixin.py` | I | Combination engine, parallelisation. |
| `lex/core/mixins/ModelModificationRestriction.py` | U | Read-only window enforcement. |
| `lex/core/exceptions.py` | U | All custom exception types. |
| `lex/core/fields/XLSX_field.py` | U+I | **Supervisor: test fully.** Encode/decode, large-file edge cases, missing-sheet errors. |
| `lex/core/calculated_updates/ObjectsToRecalculateStore.py` | U | Recalc queue. |
| `lex/core/calculated_updates/update_handler.py` | I | Recalc dispatcher. |
| `lex/core/signals/CalculationSignals.py` | I | Pre/post signals. |
| `lex/core/signals/ActiveCalculationStateStore.py` | U | Active-state tracker. |

### Cluster 8 — Celery & Async

| File | Type | Notes |
| --- | --- | --- |
| `lex/lex_app/celery.py` | U | App config, autodiscover. |
| `lex/lex_app/celery_tasks.py` | I | Task definitions. |
| `lex/core/tasks/CeleryTaskDispatcher.py` | I | Local-vs-celery routing. |
| `lex/process_admin/utils/local_scheduler.py` | I | Local fallback scheduler. |

### Cluster 9 — Signals & WebSocket

| File | Type | Notes |
| --- | --- | --- |
| `lex/api/consumers/CalculationsConsumer.py` | I | (note: supervisor list had typo `ex/…`) |
| `lex/api/consumers/CalculationLogConsumer.py` | I/skip | **First: confirm still used. If dead, DELETE.** |
| `lex/api/consumers/UpdateCalculationStatusConsumer.py` | I | Status push. |
| `lex/api/consumers/LogConsumer.py` | I | Log push. |
| `lex/api/consumers/BackendHealthConsumer.py` | I | Health WS. |

### Cluster 10 — API Layer (cross-cutting)

| File | Type | Notes |
| --- | --- | --- |
| `lex/api/serializers/base_serializers.py` | U+I | Big surface — slated for cluster 12g in coverage plan. |
| `lex/api/views/model_entries/CalculationLogTreeView.py` | E | Tree endpoint — relates to bug §1. |
| `lex/api/views/model_entries/serializers/CalculationLogTreeSerializer.py` | I | Tree shape. |
| `lex/api/views/calculations/CleanCalculations.py` | E | Admin cleanup endpoint. |
| `lex/api/views/calculations/InitCalculationLogs.py` | E | Init endpoint. |
| `lex/api/views/calculations/DownloadMarkdownPdf.py` | E | PDF export. |
| `lex/api/views/file_operations/FileDownload.py` | E | Generic file download. |
| `lex/api/views/file_operations/ModelExport.py` | E | Cluster 13f in coverage plan (Excel export). |
| `lex/api/views/sharepoint/SharePointPreview.py` | I | SharePoint integration. |
| `lex/api/views/sharepoint/SharePointShareLink.py` | I | Share-link generation. |
| `lex/api/views/sharepoint/SharePointFileDownload.py` | I | File download. |
| `lex/utilities/storage/custom_storage.py` | U | Storage backend selection. |

### Cluster 13 — Process Admin (new)

| File | Type | Notes |
| --- | --- | --- |
| `lex/process_admin/sites/process_admin_site.py` | I | After §2c cascade is applied. |
| `lex/process_admin/models/ModelContainer.py` | U+I | Container resolution. |
| `lex/process_admin/models/ModelCollection.py` | U | Collection structure. |
| `lex/process_admin/models/ModelProcessAdmin.py` | U+I | Admin registration. |
| `lex/process_admin/models/utils.py` | U | Utility helpers. |
| `lex/process_admin/utils/model_structure.py` | U | Already partially covered. |
| `lex/process_admin/utils/model_structure_builder.py` | U | Builder. |
| `lex/process_admin/utils/model_registration.py` | I | Registration flow. |
| `lex/process_admin/views/model_relation_views.py` | E | Relation endpoints. |

---

## 5. LATER (deferred, but tracked)

| File | Why later |
| --- | --- |
| `lex/core/middleware/embed_token_auth.py` | Embed feature low-traffic. |
| `lex/core/middleware/embed_xframe.py` | Pairs with above. |
| `lex/api/filters/FilterTreeNode.py` | Internal helper used by GenericFilters. |
| `lex/api/utils/temporal.py` | Thin wrapper around `core.services.Bitemporal`. |
| `lex/lex_app/fast_health.py` | Liveness probe — low risk. |
| `lex/runtime_config.py` | Runtime-config read-mostly module. |

---

## 6. Execution order (suggested)

1. **PR-1: EXCLUDE** — already in `.coveragerc`. ✅ done.
2. **PR-2: Safe deletes (§2a)** — 10 files removed. ✅ done (May 12). `compileall` clean except for one pre-existing unrelated SyntaxError in `tests/test_lex_cli.py:797`.
3. **PR-3: Side-edit deletes (§2b)** — 4 files (`cache_tables.py` + `Init2.py`, the `core/transactions/` package, `authentication/admin.py`, `createprofiles.py`).
4. **PR-4: Profile model removal (§2c)** — needs the `models/__init__.py` edit, the `apps.py:136` edit, and a fresh migration.
5. **PR-5: URL-route deletes (§2d)** — only after supervisor confirms each endpoint can disappear (`api/widget_structure` and `api/logs` may still be consumed by the frontend).
6. **PR-6 → PR-N: Cluster-by-cluster test backfill** in the order in §4 (clusters 1, 4, 6, 7, 10, 13). Each PR raises `COVERAGE_FAIL_UNDER`.
7. **Decision points still open for supervisor:**
   - §2d — confirm each routed endpoint can be removed.
   - Cluster 4 `UserAPIView` vs `user_api.py` — which is live? (Note: `lex/api/views/authentication/UserPermissionView.py` was a similar dead duplicate and was deleted in PR-2.)
   - Cluster 6 `WebSocketNotifier` — used?
   - Cluster 9 `CalculationLogConsumer` — used?

---

## 7. Coverage impact estimate

| Step | Files | Approx coverage gain |
| --- | --- | --- |
| EXCLUDE | 16 | +1.0 % (removes 0-coverage denominator) |
| Safe deletes | 7 | +0.5 % |
| Cluster 6 audit-log fill | 18 | +3.0 % |
| Cluster 7 calc/LexModel fill | 10 | +3.0 % |
| Cluster 10 API fill (incl. ModelExport, List) | 12 | +4.0 % |
| Cluster 13 process_admin | 9 | +1.5 % |
| Cluster 1 init/config | 11 | +1.0 % |
| Cluster 2 CRUD fill | 14 | +1.5 % |
| **Total target** |  | **62 % → 76–78 %** |



