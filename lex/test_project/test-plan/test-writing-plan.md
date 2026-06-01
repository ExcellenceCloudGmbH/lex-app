# Test-Writing Plan — COMPLETE bucket (May 2026)

> **Source:** §4 of [cleanup-and-coverage-plan.md](cleanup-and-coverage-plan.md) — supervisor's ~95-file COMPLETE list.
> **Goal:** turn every file in that list into one or more sub-cluster batches with concrete scenario IDs, test-class targets, fixtures, and an execution order.
> **Naming:** new sub-clusters extend existing clusters with the next free letter (e.g. cluster 6 already has 6a–6f → next is **6g**). Scenario IDs continue from the cluster's current max. Cluster numbers themselves are **never renumbered** — that would invalidate report/progress tracking.
> **Test types:** **U** = `SimpleTestCase` (no DB), **I** = `TestCase` (per-test transaction), **E** = REST through `APIClient`.
> **Back to:** [Index](index.md) · [Clusters](test-clusters.md) · [Cleanup plan](cleanup-and-coverage-plan.md)

---

## Conventions for this plan

1. **One batch = one sub-cluster = one PR.** Keeps reviews bounded and lets coverage gates ratchet up cleanly.
2. **Files needing a usage check first** (`WebSocketNotifier`, `CalculationLogConsumer`, `UserAPIView` vs `user_api`) are **not slotted** until the supervisor decision lands. Each is parked in §6 "Pending decisions".
3. **Files already covered by an in-flight Tier-A cluster** in the coverage forecast (`ModelExport.py` → 13f, `List.py` → 14f, `base_serializers.py` → 12g, `celery_tasks.py` → 8j, `LexModel.py` → 3b/4i existing, `CalculatedModelMixin.py` → 7h) are **referenced, not re-slotted**. They keep their forecasted home.
4. Every batch lists: scenario range, files covered, test classes, fixtures, file path, est. tests, est. coverage gain, prerequisite PRs.
5. **Files don't always map 1:1 to a single batch** — e.g. `LexLogger.py` shows up in cluster 6 (audit fill) and again in the bug-§1 fix PR. That's deliberate.

---

## Cluster 1 — Init / Project Bootstrap (existing 1a–1n + new 1o)

> **Renumbering note (May 12):** the plan's original placeholder names (1d/1e/1f) collided with sub-clusters that already shipped (1d–1n exist). The next free letter is **1o**, the next free scenario ID is **1.110**. Future batches in this cluster: **1p**, **1q**, **1r**.

### Batch 1o — Lazy imports + sync-exclusion + history-config helpers ✅

| Property | Value |
| --- | --- |
| Scenario range | 1.110 – 1.124 |
| Type | U |
| Files covered | `lex/process_admin/__init__.py`, `lex/lex_app/__init__.py`, `lex/lex_app/keycloak_exclusions.py`, `lex/lex_app/simple_history_config.py` |
| Test file | `lex/test_project/tests/init/test_1o_lazy_imports_and_helpers.py` |
| Test classes | `TestCluster01o_ProcessAdminLazyGetattr`, `TestCluster01o_KeycloakExclusions`, `TestCluster01o_SimpleHistoryConfig`, `TestCluster01o_LexAppPackageAlias` |
| Fixtures | none (synthetic models built with `type()` + `types.SimpleNamespace`) |
| Tests landed | **15 pass / 0 fail in 0.001s** |
| Coverage gain | +0.4 % (estimated; measured on next coverage run) |
| Status |  Complete (Session 53 — May 12) |

### Batch 1p — Settings / config / URLs / top-level views ✅

| Property | Value |
| --- | --- |
| Scenario range | 1.125 – 1.146 |
| Type | U + I |
| Files covered | `lex/lex_app/settings.py`, `lex/lex_app/urls.py`, `lex/lex_app/views.py`, `lex/utilities/config/generic_app_config.py`, `lex/core/config.py` |
| Test file | `lex/test_project/tests/init/test_1p_settings_urls_views.py` |
| Test classes | `TestCluster01p_SettingsConstants`, `TestCluster01p_UrlConfResolves`, `TestCluster01p_HealthEndpoint`, `TestCluster01p_LexProjectConfig`, `TestCluster01p_GenericAppConfigHelpers` |
| Fixtures | `tempfile.TemporaryDirectory` for `lex_config.py` writing; no new models |
| Tests landed | **22 pass / 0 fail in 0.016s** |
| Coverage gain | +0.6 % (estimated; measured on next coverage run) |
| Status |  Complete (Session 54 — May 12). Note: `lex/lex_app/apps.py` AppConfig.ready surface deferred to **1q** — it requires real bootstrap fixtures. |

### Batch 1q — `lex Init` end-to-end on fresh DB *(next)*

| Property | Value |
| --- | --- |
| Scenario range | 1.143 – 1.150 |
| Type | I |
| Files covered | `lex/lex_app/management/commands/init.py` (gap fill on top of 1g/1h/1n) |
| Test file | `lex/test_project/tests/init/test_1q_init_command_full_flow.py` |
| Test classes | `TestInitCommandFreshDb`, `TestInitCommandIdempotent`, `TestInitCommandKeycloakSync` (mocked at HTTP boundary) |
| Fixtures | mock Keycloak admin client; minimal `INITIAL_DATA` JSON |
| Est. tests | ~10 |
| Coverage gain | +0.3 % |
| Prereqs | 1p |

---

## Cluster 2 — CRUD via REST API (existing 2a–2e)

### Batch 2f — Model-entry mixins & serialisers

| Property | Value |
| --- | --- |
| Scenario range | 2.40 – 2.55 |
| Type | I + E |
| Files covered | `mixins/ModelEntryProviderMixin.py`, `mixins/DestroyOneWithPayloadMixin.py`, `mixins/PermissionAwareSerializerMixin.py` |
| Test file | `lex/test_project/tests/crud_api/test_2f_model_entry_mixins.py` |
| Test classes | `TestModelClassResolution` (URL kwarg → ContentType → model), `TestDestroyOneReturnsPayload` (DELETE returns the deleted instance), `TestPermissionAwareSerializerStripsFields` (per-user field masking) |
| Fixtures | `ProtectedItem` (already exists in `permissions/models.py`); add `OwnedItem` if not present |
| Est. tests | ~14 |
| Coverage gain | +0.7 % |
| Prereqs | none |

### Batch 2g — One / Many / List + filter backends

| Property | Value |
| --- | --- |
| Scenario range | 2.56 – 2.78 |
| Type | E |
| Files covered | `views/model_entries/One.py`, `Many.py`, **partial** `List.py` (rest is in 14f), `filter_backends.py`, `api/filters/GenericFilters.py` |
| Test file | `lex/test_project/tests/crud_api/test_2g_one_many_filters.py` |
| Test classes | `TestOneEndpoint` (GET/PUT/PATCH/DELETE happy + 404), `TestManyEndpoint` (paginated GET, bulk POST), `TestListBasicShape` (defer AG-Grid specifics to 14f), `TestFilterBackendQueryParser`, `TestGenericFilterClasses` |
| Fixtures | `SimpleItem`, `TrackedItem` |
| Est. tests | ~22 |
| Coverage gain | +1.2 % |
| Prereqs | 2f |

### Batch 2h — Structure / fields / lex-API endpoints

| Property | Value |
| --- | --- |
| Scenario range | 2.79 – 2.92 |
| Type | E |
| Files covered | `views/ModelStructureObtainView.py`, `views/model_info/Fields.py`, `views/lex_api/LexAPI.py`, `api/utils/helpers.py`, `api/utils/Context.py`, `api/utils/api_key_requests.py` |
| Test file | `lex/test_project/tests/crud_api/test_2h_structure_and_lex_api.py` |
| Test classes | `TestModelStructureObtain`, `TestFieldsEndpoint` (per-model field metadata), `TestLexApiDispatcher`, `TestApiUtilHelpers` (U), `TestRequestContextObject` (U), `TestApiKeyAuthenticatedRequest` |
| Fixtures | API-key fixture (already in cluster 4) |
| Est. tests | ~15 |
| Coverage gain | +0.6 % |
| Prereqs | 2f |

### Batch 2i — Cancel-calculation REST endpoint (Session 67 — June 1)

| Property | Value |
| --- | --- |
| Scenario range | 2.93 – 2.96 |
| Type | E |
| Files covered | `lex/api/views/model_entries/One.py` (the new `cancel=true` short-circuit branch in `OneModelEntry.update`) |
| Test file | `lex/test_project/tests/crud_api/test_2i_cancel_endpoint.py` |
| Test classes | `TestCluster02i_CancelCalculationEndpoint` (PATCH with body `{"cancel":"true"}` → 202 on cancellable IN_PROGRESS, 409 on terminal state, 409 with `reason=sync_calculation_not_cancellable` when no Celery task_id, sibling fields ignored) |
| Fixtures | `AtomicCalc` (from cluster 7); patches `CalculationModel._revoke_celery_task` so no broker is needed |
| Est. tests | 4 |
| Coverage gain | +0.1 % |
| Prereqs | none |
| Status | ✅ Complete (Session 67 — 4 scenarios; passes locally for pure-logic, DB-needing scenarios require a CI-configured test DB) |

---

## Cluster 4 — Permissions (existing 4a–4i)

### Batch 4j — Middleware & bearer-token authentication

| Property | Value |
| --- | --- |
| Scenario range | 4.35 – 4.46 |
| Type | I |
| Files covered | `api/middleware/keycloak_permissions.py`, `authentication/authentication_backends/BearerMiddlewareAuthentication.py` |
| Test file | `lex/test_project/tests/permissions/test_4j_keycloak_middleware.py` |
| Test classes | `TestKeycloakPermissionMiddleware` (request → UserContext attachment, scope evaluation, denial path), `TestBearerMiddlewareAuthentication` (valid token, expired, missing, malformed) |
| Fixtures | mock Keycloak token decoder |
| Est. tests | ~12 |
| Coverage gain | +0.7 % |
| Prereqs | none |

### Batch 4k — Permission views

| Property | Value |
| --- | --- |
| Scenario range | 4.47 – 4.55 |
| Type | E |
| Files covered | `views/permissions/ModelPermissions.py`, `views/permissions/UserPermission.py` |
| Test file | `lex/test_project/tests/permissions/test_4k_permission_views.py` |
| Test classes | `TestModelPermissionsEndpoint`, `TestUserPermissionEndpoint` |
| Fixtures | superuser, regular user, group-membership fixture |
| Est. tests | ~9 |
| Coverage gain | +0.4 % |
| Prereqs | 4j |

### Batch 4l — User API endpoint *(blocked — see §6 decision #2)*

`UserAPIView.py` vs `user_api.py` — slot once supervisor confirms which is live.

---

## Cluster 5 — History & Bitemporal (existing 5a–5d)

### Batch 5e — Service layer

| Property | Value |
| --- | --- |
| Scenario range | 5.30 – 5.48 |
| Type | I |
| Files covered | `core/services/Bitemporal.py`, `core/services/bitemporal_signals.py`, `core/services/StandardHistory.py`, `core/services/MetaHistory.py`, `process_admin/utils/bitemporal_sync.py` |
| Test file | `lex/test_project/tests/history/test_5e_bitemporal_services.py` |
| Test classes | `TestBitemporalCore` (intervals, valid_from/to chaining), `TestBitemporalSignals` (pre_save → row close + new row), `TestStandardHistory` (non-bitemporal branch), `TestMetaHistory` (cross-model linking), `TestBitemporalSync` (legacy → bitemporal migration) |
| Fixtures | `BitemporalItem`, `LegacyVersionedItem` |
| Est. tests | ~20 |
| Coverage gain | +1.4 % |
| Prereqs | none |

### Batch 5f — History REST endpoint

| Property | Value |
| --- | --- |
| Scenario range | 5.49 – 5.55 |
| Type | E |
| Files covered | `api/views/model_entries/History.py` |
| Test file | `lex/test_project/tests/history/test_5f_history_endpoint.py` |
| Test classes | `TestHistoryEndpointShape`, `TestHistoryFilters` (date range, user), `TestHistoryPermissionGating` |
| Fixtures | reuse 5e fixtures + 4j users |
| Est. tests | ~8 |
| Coverage gain | +0.4 % |
| Prereqs | 5e + 4j |

---

## Cluster 6 — Audit Logging (existing 6a–6f)

This is the biggest single chunk — 18 files. Split into **three** batches so reviews stay tractable.

### Batch 6g — Models & enums

| Property | Value |
| --- | --- |
| Scenario range | 6.30 – 6.42 |
| Type | U + I |
| Files covered | `models/AuditLog.py`, `models/AuditLogStatus.py`, `models/CalculationLog.py` |
| Test file | `lex/test_project/tests/audit_logging/test_6g_audit_models.py` |
| Test classes | `TestAuditLogModelFields`, `TestAuditLogStatusTransitions`, `TestCalculationLogParentLinking` |
| Fixtures | `AuditedItem`, `CalcWithLogging` |
| Est. tests | ~14 |
| Coverage gain | +0.8 % |
| Prereqs | none |

### Batch 6h — Mixins & utils

| Property | Value |
| --- | --- |
| Scenario range | 6.43 – 6.62 |
| Type | I |
| Files covered | `mixins/AuditLogMixin.py`, `mixins/BulkAuditLogMixin.py`, `utils/ModelContext.py`, `utils/ContextResolver.py`, `utils/DataModels.py`, `utils/calculation_audit.py`, `utils/InitialDataAuditLogger.py`, `utils/config.py`, `utils/content_types.py` |
| Test file | `lex/test_project/tests/audit_logging/test_6h_audit_mixins_and_utils.py` |
| Test classes | one per file (9 classes) — keeps the failure point unambiguous in CI |
| Fixtures | reuse 6g + a `BulkOpItem` |
| Est. tests | ~30 |
| Coverage gain | +1.5 % |
| Prereqs | 6g |

### Batch 6i — Serialisers & handlers (incl. bug-§1 LexLogger surface)

| Property | Value |
| --- | --- |
| Scenario range | 6.63 – 6.78 |
| Type | I |
| Files covered | `serializers/AuditLogSerializer.py`, `serializers/AuditLogMixinSerializer.py`, `serializers/CalculationLogSerializer.py`, `handlers/LexLogger.py`, `handlers/WebSocketHandler.py` |
| Test file | `lex/test_project/tests/audit_logging/test_6i_audit_serializers_and_handlers.py` |
| Test classes | `TestAuditLogSerializerShape`, `TestAuditLogMixinSerializerExtras`, `TestCalculationLogSerializerTree`, `TestLexLoggerBuilderAndPersist`, `TestWebSocketHandlerEmits` (mock channel layer) |
| Fixtures | reuse 6g/6h |
| Est. tests | ~16 |
| Coverage gain | +0.8 % |
| Prereqs | 6g, 6h |
| Note | This is the right time to add the regression test for [`NOTES_TODO.md` §1](../../../NOTES_TODO.md) — the duplicated-children bug. Place it as `TestCalculationLogTreeBugRegression` here, mark `expectedFailure` until the framework fix lands. |

---

## Cluster 7 — Calculation State Machine (existing 7a–7j, plus new 7k)

> **Renumbering note (May 12):** the plan's original 7i/7j/7k labels collided with already-shipped sub-clusters (7i = 2-level atomicity matrix Session 42; 7j = 3-level matrix Session 45). The supervisor's "exceptions / restrictions / XLSX" batch landed under **7k**; the queue + signals batches shift to **7l / 7m**.

### Batch 7k — Exceptions, restrictions & XLSX field ✅

| Property | Value |
| --- | --- |
| Scenario range | 7.122 – 7.142 |
| Type | U |
| Files covered | `core/exceptions.py`, `core/mixins/ModelModificationRestriction.py`, `core/fields/XLSX_field.py` (coverage spotter — exhaustive battery already at `lex/tests/unit/api/test_xlsx_field.py`) |
| Test file | `lex/test_project/tests/calculations/test_7k_exceptions_restrictions_xlsx.py` |
| Test classes | `TestCluster07k_HelperPrimitives`, `TestCluster07k_PreferredSelectors`, `TestCluster07k_ResolveHelpers`, `TestCluster07k_ExceptionClasses`, `TestCluster07k_ModelModificationRestriction`, `TestCluster07k_XLSXFieldCoverageSpotter` |
| Fixtures | none (synthetic exception chains built inline) |
| Tests landed | **21 pass / 0 fail in 0.001s** |
| Coverage gain | +1.0 % (estimated) |
| Status |  Complete (Session 55 — May 12). XLSX_field full coverage delegated to the existing 378-line `lex/tests/unit/api/test_xlsx_field.py`; 7.142 spotter pins the format-constant tuples + `max_length` migration sensitivity at the cluster-7 dashboard level. |

### ~~Batch 7l — Recalculation queue & dispatch~~ *(rolled back upstream — spec preserved for future re-activation)*

> **Status (May 12):** the recalc-queue surface (`ObjectsToRecalculateStore` + `update_handler`) was rolled back upstream after a first-pass implementation surfaced a `CalculatedModelMixinMeta.__new__` `KeyError: 'defining_fields'` issue with stub subclasses (workaround used `_FakeCalcMixinBase` + `unittest.mock.patch`). The on-disk test file was reverted; the spec below is kept commented out so the slot/letter is reserved and we can re-activate once the upstream queue ships in a stable form.

<!--
| Property | Value |
| --- | --- |
| Scenario range | 7.143 – 7.154 |
| Type | I |
| Files covered | `core/calculated_updates/ObjectsToRecalculateStore.py`, `core/calculated_updates/update_handler.py` |
| Test file | `lex/test_project/tests/calculations/test_7l_recalc_queue.py` |
| Test classes | `TestObjectsToRecalculateStore` (push/pop/dedupe), `TestUpdateHandlerDispatch` (FK chains, depth limits, cycle protection) |
| Fixtures | `ParentCalc → ChildCalc → GrandchildCalc` (already in `calculations/models.py`) |
| Est. tests | ~12 |
| Coverage gain | +0.8 % |
| Prereqs | none |
| Implementation notes (rolled back) | `CalculatedModelMixinMeta.__new__` reads `attrs['defining_fields']` unconditionally (line ~976 of `lex/core/mixins/CalculatedModelMixin.py`) — any stub subclass without that attribute raises `KeyError` at class-construction time. Workaround during the first pass was a plain `_FakeCalcMixinBase` stand-in plus `unittest.mock.patch("lex.core.calculated_updates.update_handler.CalculatedModelMixin", _FakeCalcMixinBase)`. Re-use this pattern when the batch is re-activated. Also: dependency dict-keys must be hashable — `SimpleNamespace` is not, use a small plain class. |
-->


### Batch 7m — Calculation signals & active-state store

| Property | Value |
| --- | --- |
| Scenario range | 7.155 – 7.165 |
| Type | I |
| Files covered | `core/signals/CalculationSignals.py`, `core/signals/ActiveCalculationStateStore.py` |
| Test file | `lex/test_project/tests/calculations/test_7m_calc_signals.py` |
| Test classes | `TestCalculationSignalsPrePost` (signal fires, payload shape), `TestActiveCalculationStateStore` (registration, cleanup on success + on failure) |
| Fixtures | `AtomicCalc`, `FailingCalc` (existing) |
| Est. tests | ~10 |
| Coverage gain | +0.6 % |
| Prereqs | none |

> `LexModel.py`, `CalculationModel.py`, `CalculatedModelMixin.py` keep their forecasted homes (4i existing + 7h Tier-A clusters in the coverage plan). Do not duplicate.

### Batch 7n — Calculation cancellation, state machine + recursive cancel (Session 67 — June 1)

| Property | Value |
| --- | --- |
| Scenario range | 7.166 – 7.173 |
| Type | I |
| Files covered | `lex/core/models/CalculationModel.py` (new `CalculationModel.cancel()` classmethod + `_persist_cancelled` / `_persist_cancelled_by_entry` helpers + `CalculationCancelled` marker exception + `dispatch_calculation_task` task_id capture); `lex/core/signals/ActiveCalculationStateStore.py` (new `set_task_id` / `get_task_id` / `find_descendants`; `mark_in_progress` preserves task_id across re-entry) |
| Test file | `lex/test_project/tests/calculations/test_7n_cancellation.py` |
| Test classes | `TestCluster07n_Cancellation` (Celery-cancellable → CANCELLED; sync-not-cancellable → reason flag; terminal-state idempotent; recursive cancel reaches every descendant sharing `calculation_id`; `recursive=False` leaves descendants); `TestCluster07n_StateStoreTaskIdAndDescendants` (`set_task_id` survives `mark_in_progress` re-entry; `find_descendants` groups by shared `calculation_id`); `TestCluster07n_CalculationCancelledException` (marker exception carries reason) |
| Fixtures | `AtomicCalc`, `ParentCalc`, `ChildCalc` (existing); `CalculationModel._revoke_celery_task` patched so no Celery broker is needed |
| Est. tests | 8 |
| Coverage gain | +0.5 % |
| Prereqs | none |
| Status | ✅ Complete (Session 67 — 8 scenarios; 3 pure-logic pass locally, 5 DB-needing scenarios require CI test DB) |


---

## Cluster 8 — Celery & Async (existing 8a–8g)

### Batch 8h — Dispatcher & local scheduler

| Property | Value |
| --- | --- |
| Scenario range | 8.40 – 8.52 |
| Type | I |
| Files covered | `core/tasks/CeleryTaskDispatcher.py`, `process_admin/utils/local_scheduler.py` |
| Test file | `lex/test_project/tests/celery/test_dispatcher_and_local_scheduler.py` |
| Test classes | `TestCeleryTaskDispatcherRouting` (CELERY_ACTIVE on/off branches — use `patch.dict` per the test-suite rule), `TestLocalSchedulerFallback` (sync execution, error capture, status update) |
| Fixtures | `AtomicCalc`, `NonAtomicCalc` |
| Est. tests | ~12 |
| Coverage gain | +0.7 % |
| Prereqs | none |

### Batch 8i — `celery.py` app config

| Property | Value |
| --- | --- |
| Scenario range | 8.53 – 8.57 |
| Type | U |
| Files covered | `lex/lex_app/celery.py` |
| Test file | `lex/test_project/tests/celery/test_celery_app_config.py` |
| Test classes | `TestCeleryAppConfig` (broker URL resolution, autodiscover, beat schedule registration) |
| Fixtures | none |
| Est. tests | ~5 |
| Coverage gain | +0.2 % |
| Prereqs | none |

> `celery_tasks.py` keeps its slot in cluster 8j (Tier-A coverage forecast).

### Batch 8u — Cancellation-aware CallbackTask failure mapping (Session 67 — June 1)

| Property | Value |
| --- | --- |
| Scenario range | 8.73 – 8.77 |
| Type | U |
| Files covered | `lex/lex_app/celery_tasks.py` — `_is_cancellation_exception` helper + the `CallbackTask.on_failure` branch that maps `TaskRevokedError` / `SoftTimeLimitExceeded` / `WorkerLostError` / `billiard.Terminated` / `CalculationCancelled` onto `CANCELLED` (not `ERROR`); **Session 68** also pins `CallbackTask._update_model_status`'s audit-status mapping (8.77) so the terminal-audit row records `failure` for both ERROR and CANCELLED, never `success` for a non-SUCCESS terminal state |
| Test file | `lex/test_project/tests/celery_async/test_8u_cancel_revoke.py` |
| Test classes | `TestCluster08u_CancellationExceptionDetector` (every documented cancellation class recognised; generic runtime errors rejected; subclasses of cancellation classes still detected; `None` handled safely); `TestCluster08u_AuditStatusForTerminalStates` (8.77 — SUCCESS → `success`, ERROR / CANCELLED → `failure`) |
| Fixtures | none (synthetic exception stand-ins mirroring celery/billiard class names so the worker stack does not have to be imported; 8.77 mocks `ensure_terminal_calculation_audit` to observe the `audit_status` argument) |
| Est. tests | 5 (12 sub-test cases) |
| Coverage gain | +0.15 % |
| Prereqs | none |
| Status | ✅ Complete (Session 67 — 4 scenarios; Session 68 extended +1 — 5 pass / 0 fail / 12 sub-tests pass / 0.11s; runs broker-free, DB-free) |

---

## Cluster 9 — Signals & WebSocket (existing 9a)

### Batch 9b — Consumers (excluding usage-blocked ones)

| Property | Value |
| --- | --- |
| Scenario range | 9.10 – 9.24 |
| Type | I |
| Files covered | `consumers/CalculationsConsumer.py`, `consumers/UpdateCalculationStatusConsumer.py`, `consumers/LogConsumer.py`, `consumers/BackendHealthConsumer.py` |
| Test file | `lex/test_project/tests/websocket/test_consumers.py` |
| Test classes | one per consumer (4 classes). Use Channels' `WebsocketCommunicator`. |
| Fixtures | in-memory channel layer (`CHANNEL_LAYERS = {"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}}`) |
| Est. tests | ~14 |
| Coverage gain | +0.7 % |
| Prereqs | none |
| Note | Supervisor's list had a typo — `ex/api/consumers/CalculationsConsumer.py` → corrected to `lex/…`. |

`CalculationLogConsumer.py` is **parked** until §6 decision #3 confirms it's still wired anywhere.

---

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

> `ModelExport.py` (cluster 13f), `List.py` AG-Grid path (14f), `base_serializers.py` (12g) keep their forecasted homes.

---

## Cluster 13 — Process Admin (new — opens here)

### Batch 13a — Container, collection, model registration

| Property | Value |
| --- | --- |
| Scenario range | 13.1 – 13.18 |
| Type | U + I |
| Files covered | `process_admin/models/ModelContainer.py`, `models/ModelCollection.py`, `models/ModelProcessAdmin.py`, `models/utils.py`, `utils/model_registration.py` |
| Test file | `lex/test_project/tests/process_admin/test_container_and_registration.py` |
| Test classes | `TestModelContainerResolution`, `TestModelCollectionStructure`, `TestModelProcessAdminRegistration`, `TestProcessAdminUtils` (U), `TestModelRegistrationFlow` |
| Fixtures | reuses existing test_project models — they're already registered |
| Est. tests | ~20 |
| Coverage gain | +1.2 % |
| Prereqs | none |

### Batch 13b — Structure builder & relation views

| Property | Value |
| --- | --- |
| Scenario range | 13.19 – 13.30 |
| Type | U + E |
| Files covered | `process_admin/utils/model_structure.py` (gap fill — partial coverage exists), `utils/model_structure_builder.py`, `views/model_relation_views.py` |
| Test file | `lex/test_project/tests/process_admin/test_structure_and_relations.py` |
| Test classes | `TestModelStructureNormalisation` (covers the dict-vs-list `_normalize_model_list` fix), `TestModelStructureBuilder`, `TestModelRelationEndpoints` |
| Fixtures | `model_structure.yaml` test fixture (dict + list variants) |
| Est. tests | ~14 |
| Coverage gain | +0.7 % |
| Prereqs | 13a |

### Batch 13c — `process_admin_site.py` *(blocked on §2d cascade)*

Defer until PR-5 in the cleanup plan lands. Once routes are pruned, write 6–8 tests covering:
- Site instantiation
- URL conf assembly (the surviving routes)
- `get_urls()` ordering
- Auth gating on the admin entrypoint

---

## 5. LATER bucket (deferred — keep in backlog)

Covered in [cleanup-and-coverage-plan.md §5](cleanup-and-coverage-plan.md#5-later-deferred-but-tracked). When picked up, slot as:

| File | Suggested home |
| --- | --- |
| `core/middleware/embed_token_auth.py` | new **4m** |
| `core/middleware/embed_xframe.py` | **4m** (same batch) |
| `api/filters/FilterTreeNode.py` | **2g extension** |
| `api/utils/temporal.py` | **5e extension** |
| `lex_app/fast_health.py` | **1e extension** |
| `runtime_config.py` | **1e extension** |

---

## 6. Pending decisions blocking specific batches

| # | Question | Blocks |
| --- | --- | --- |
| 1 | `audit_logging/utils/WebSocketNotifier.py` — used anywhere? If no → DELETE. If yes → add a class to **6i**. | 6i final scope |
| 2 | `api/views/authentication/UserAPIView.py` vs `authentication/views/user_api.py` — which is live? Delete the other. | **4l** can be opened |
| 3 | `api/consumers/CalculationLogConsumer.py` — still used? If no → DELETE. If yes → add to **9b** as a 5th consumer. | 9b final scope |
| 4 | `process_admin_site.py` route cascade (cleanup §2d) — supervisor confirmation to drop `api/widget_structure`, `api/logs`, `streamlit-token`, `user_permissions`, `CreateOrUpdate` routes. | **13c** |

---

## 7. Suggested execution order (PR-by-PR)

Numbering continues from the cleanup-and-coverage-plan §6 list (PR-1 … PR-5 already defined there).

| PR | Batches | Why this order |
| --- | --- | --- |
| PR-6  | 1o ✅ / 1p ✅ / 1q | Foundation. Unblocks meaningful coverage measurement on init/config code that everything else imports. **1o + 1p landed Sessions 53–54.** |
| PR-7  | 6g, 6h, 6i | Largest single coverage win and unblocks 10g. Bug-§1 regression test lands here. |
| PR-8  | 7k ✅ / ~~7l~~ (rolled back) / 7m | Calculation-edge code. Independent of cluster 6. **7k landed Session 55** — exceptions / restrictions / XLSX spotter. **7l rolled back upstream** — recalc-queue surface no longer ships; spec kept commented out for re-activation. PR-8 continues with **7m** (calculation signals + active-state store) as the next batch. |
| PR-9  | 2f, 2g, 2h | CRUD surface — depends on nothing new but benefits from the audit fixtures already added by PR-7. |
| PR-10 | 4j, 4k | Permissions middleware + views. |
| PR-11 | 5e, 5f | Bitemporal services + history endpoint. |
| PR-12 | 8h, 8i | Celery dispatch & app config. |
| PR-13 | 9b | WebSocket consumers (minus blocked one). |
| PR-14 | 10g, 10h | Calculation + file/SharePoint endpoints. |
| PR-15 | 13a, 13b | Process Admin (without the routed-site batch). |
| PR-16 | 4l, 13c, plus any "blocked" batch unblocked by §6 decisions | Sweep-up. |

Each PR raises `COVERAGE_FAIL_UNDER` by **its own forecasted gain, rounded down**, never up by more than the actual measured gain. Threshold goes one direction — up.

---

## 8. Coverage forecast (delta on top of cleanup-plan §7)

| Batch | Tests | Δ coverage |
| --- | --- | --- |
| 1d + 1e + 1f | ~42 | +1.3 % |
| 6g + 6h + 6i | ~60 | +3.1 % |
| 7i + 7j + 7k | ~44 | +2.4 % |
| 2f + 2g + 2h | ~51 | +2.5 % |
| 4j + 4k | ~21 | +1.1 % |
| 5e + 5f | ~28 | +1.8 % |
| 8h + 8i | ~17 | +0.9 % |
| 9b | ~14 | +0.7 % |
| 10g + 10h | ~29 | +1.7 % |
| 13a + 13b | ~34 | +1.9 % |
| **Subtotal (this plan)** | **~340** | **+17.4 %** |

Combined with cleanup-plan §7 (EXCLUDE + safe deletes + Tier-A clusters), realistic landing range is **62 % → 78–82 %** by end of PR-16, slightly above the cleanup-plan forecast because the per-file batches catch corner-case branches the Tier-A clusters miss.

---

## 9. Rules every batch must follow

Same as cluster-doc Golden Rule. Reproduced here to keep this doc self-contained:

1. Test customer-visible behaviour, not internal calls.
2. Real DB models — no `_make_model_stub`.
3. Mock only true external boundaries (Keycloak HTTP, Celery broker, channel layer, S3, SharePoint).
4. `patch.dict("os.environ", ...)` for `CELERY_ACTIVE` / DB-target switches — never rely on `.env` leakage in CI.
5. Module + class docstrings on every test file. They are the living documentation.
6. Bare `except:` is banned — always `except Exception:` (or a specific subclass).
7. If a test exposes a real bug → `@unittest.expectedFailure` with a tracker entry, **don't weaken it.**

---

> **Runner note (May 2026):** this suite runs under `python -m lex pytest`.
> New batches add `pytestmark = pytest.mark.<cluster_slug>` to each test
> module. See [`progress/conventions.md` §How to Run Tests](progress/conventions.md#how-to-run-tests)
> for the runner commands.










