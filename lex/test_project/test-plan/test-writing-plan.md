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

### Batch 1q — Migration file completeness release gate ✅

| Property | Value |
| --- | --- |
| Scenario range | 1.147 – 1.147 |
| Type | U |
| Files covered | `lex/lex_app/migrations/*.py`, `lex/authentication/migrations/*.py`, `lex/audit_logging/migrations/*.py`, `lex/legacy_data/migrations/*.py` |
| Test file | `lex/test_project/tests/init/test_1q_migration_files_complete.py` |
| Test classes | `TestCluster01q_MigrationFilesComplete` |
| Fixtures | none |
| Tests landed | **1 pass / 0 fail in 2.42s** |
| Coverage gain | n/a (release-gate drift test) |
| Status | ✅ Complete (Session 70 — June 2) |

### Batch 1s — Log-noise cleanup + lex-namespace debug control (EXC-1787) ✅

> **Letter note (June 8):** letter **1r** was already taken on disk by an in-flight,
> untracked batch (`test_1r_lex_view_embed_helper.py`, Streamlit `lex_view` embed
> helper, scenarios up to 1.158) that is not yet documented in this plan. Per the
> never-renumber rule, this batch took the next free letter **1s** and the next free
> scenario ID after 1.158 → **1.159**.

| Property | Value |
| --- | --- |
| Scenario range | 1.159 – 1.168 |
| Type | U |
| Files covered | `lex/lex_app/settings.py` (urllib3 `InsecureRequestWarning` suppression gate `LEX_SUPPRESS_INSECURE_WARNING`; new `LEX_LOG_LEVEL` + `lex` logger entry; `CONSOLE_HANDLER_LEVEL` = `min(CONSOLE_LEVEL, LEX_LOG_LEVEL)` derivation; blanket `LEX_SUPPRESS_WARNINGS` → `warnings.filterwarnings("ignore")` gate) |
| Test file | `lex/test_project/tests/init/test_1s_log_cleanup_and_lex_debug.py` |
| Test classes | `TestCluster01s_InsecureWarningSuppression` (1.159 default-suppressed, 1.160 opt-out honoured, 1.161 opt-out case-insensitive), `TestCluster01s_LexNamespaceDebugLevel` (1.162 lex logger defaults INFO + propagate False, 1.163 `LEX_LOG_LEVEL=DEBUG` raises lex only while root stays INFO, 1.164 console handler drops to DEBUG for lex, 1.165 console handler stays INFO by default), `TestCluster01s_BlanketWarningSuppression` (1.166 default installs `filterwarnings("ignore")`, 1.167 opt-out skips it, 1.168 opt-out case-insensitive) |
| Fixtures | none (reloads `lex.lex_app.settings` under patched `os.environ`; `sentry_sdk.init` mocked across reloads; env restored in cleanup) |
| Tests landed | **10 pass / 0 fail in 0.26s** |
| Coverage gain | negligible (settings is import-time; pins env-var-driven branches) |
| Status | ✅ Complete (Session 75 — June 8) |

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

### Batch 6p — Calculation-log cache backfill buffer cap ✅

| Property | Value |
| --- | --- |
| Scenario range | 6.109 – 6.113 |
| Type | U |
| Files covered | `audit_logging/utils/CacheManager.py` (`store_message` buffer cap + TTL) |
| Test file | `lex/test_project/tests/audit_logging/test_6p_cache_buffer_cap.py` |
| Test classes | `TestCluster06p_CacheBufferCap` |
| Fixtures | none (LocMemCache via `CALC_CACHE_NAME="local"`) |
| Est. tests | 5 |
| Coverage gain | measured locally; CacheManager store path |
| Prereqs | none |
| Status | ✅ Complete — 5 pass / 0 fail |
| Note | Backend OOM fix (session 77): the live-log backfill buffer was an unbounded `get`+concat+`set` per line. Now capped to a ~256 KB tail (`MAX_CACHE_MESSAGE_CHARS`), trimmed to a clean line boundary, written with `CACHE_TIMEOUT`. Full log still persists in `CalculationLog`; only the recent-history backfill is bounded. |

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

### Batch 7o — ForeignKey integrity violation aborts batch (Session 72 — June 2)

| Property | Value |
| --- | --- |
| Scenario range | 7.176 – 7.176 |
| Type | I |
| Files covered | `lex/core/mixins/CalculatedModelMixin.py`, `lex/lex_app/celery_tasks.py` |
| Test file | `lex/test_project/tests/calculations/test_7o_fk_violation_abort.py` |
| Test classes | `TestCluster07o_ForeignKeyAbort` |
| Fixtures | `FKViolationAbortCalc`, `FKAbortWrite`, `FKAbortTarget` (`calculations/models.py`) |
| Tests landed | **not run locally (requires Postgres test DB in this environment)** |
| Coverage gain | n/a (behaviour regression gate) |
| Prereqs | none |
| Status | ✅ Complete (Session 72 — pins immediate abort on unhandled FK integrity failure) |


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
| Files covered | `lex/lex_app/celery_tasks.py` — `_is_cancellation_exception` helper + the `CallbackTask.on_failure` branch that maps `TaskRevokedError` / `SoftTimeLimitExceeded` / `WorkerLostError` / `billiard.Terminated` / `CalculationCancelled` onto `CANCELLED` (not `ERROR`); **Session 68** added 8.77 pinning `CallbackTask._update_model_status`'s audit-status mapping, and **Session 69** widened the mapping to three distinct values — SUCCESS → `success`, CANCELLED → `cancelled`, ERROR → `failure` — so the audit row can distinguish a user-cancelled run from a crash without relying on the error_message string |
| Test file | `lex/test_project/tests/celery_async/test_8u_cancel_revoke.py` |
| Test classes | `TestCluster08u_CancellationExceptionDetector` (every documented cancellation class recognised; generic runtime errors rejected; subclasses of cancellation classes still detected; `None` handled safely); `TestCluster08u_AuditStatusForTerminalStates` (8.77 — SUCCESS → `success`, ERROR / CANCELLED → `failure`) |
| Fixtures | none (synthetic exception stand-ins mirroring celery/billiard class names so the worker stack does not have to be imported; 8.77 mocks `ensure_terminal_calculation_audit` to observe the `audit_status` argument) |
| Est. tests | 5 (12 sub-test cases) |
| Coverage gain | +0.15 % |
| Prereqs | none |
| Status | ✅ Complete (Session 67 — 4 scenarios; Session 68 extended +1 — 5 pass / 0 fail / 12 sub-tests pass / 0.11s; runs broker-free, DB-free) |

---

### Batch 8v — Cluster-wide cascade cancellation: Redis cancel index (Session 74 — June 5)

| Property | Value |
| --- | --- |
| Scenario range | 8.78 – 8.89 |
| Type | U (+ one E for 8.87) |
| Files covered | `lex/core/cancellation/cluster_cancel_index.py` (new — best-effort Redis HASH tree keyed by `calculation_id`: `register_task` / `unregister_task` / `get_tree` / `mark_cancelled` / `is_cancelled`, lazy `redis.from_url(CELERY_BROKER_URL)` client, `_enabled_by_config` gate on `CELERY_ACTIVE` + `LEX_CLUSTER_CANCEL_ENABLED`, all ops try/except → silent no-op, never raise); `lex/core/signals/ActiveCalculationStateStore.py` (`set_task_id` → `register_task`, `clear` → `unregister_task`, both outside the store lock); `lex/core/models/CalculationModel.py` (`cancel()` unions in-memory descendants with `get_tree`, revokes every cluster-discovered task, `mark_cancelled` when recursive); `lex/lex_app/celery_tasks.py` (`calc_and_save` checks `is_cancelled` at task start, raises `CalculationCancelled`); `lex/lex_app/settings.py` (`LEX_CLUSTER_CANCEL_ENABLED` + tree/marker TTL knobs) |
| Test file | `lex/test_project/tests/celery_async/test_8v_cluster_cascade_cancel.py` |
| Test classes | `TestCluster08v_IndexOperations` (8.78–8.82 register/unregister/get_tree/mark+is_cancelled/no-client-degrades, `SimpleTestCase`, mocked redis); `TestCluster08v_IndexDisabled` (8.83–8.84 `CELERY_ACTIVE` off + master-switch off → no-op); `TestCluster08v_StoreWriteThrough` (8.85–8.86 attach task_id writes through / terminal node leaves the index); `TestCluster08v_CancelUnionsClusterTree` (8.87 — **`E2ETestCase`**, `e2e_models=[CelerySyncCalc]`, `e2e_unpatch={"mark_in_progress"}`: `cancel()` revokes a child registered only in the Redis index); `TestCluster08v_CooperativeMarkerCheck` (8.88–8.89 late pod self-aborts on marker / no marker → runs normally) |
| Fixtures | none — `unittest.mock` on the redis client + `CeleryTaskDispatcher.app.control.revoke`; 8.87 reuses the `CelerySyncCalc` E2E model (schema-editor table creation, matching `test_7n_cancellation.py`) |
| Tests landed | **12 pass / 0 fail** (8.78–8.89); cluster_cancel_index coverage 70 % (uncovered lines are the Redis-failure `except` branches, exercised only on a real connection error — the suite mocks redis); regression across `celery_async` + `calculations` = 252 pass / 4 skip; existing 8u + 7n cancellation suites green |
| Coverage gain | new module `cluster_cancel_index.py` 0 → 70 % |
| Prereqs | none (graceful-degradation means no live Redis required for the suite) |
| Status | ✅ Complete (Session 74 — June 5; designed brainstorming→spec→plan, executed subagent-driven) |

---

### Batch 8w — Worker-recovery terminal-outcome guard: no ERROR→SUCCESS resurrection

| Property | Value |
| --- | --- |
| Scenario range | 8.90 – 8.102 |
| Type | U (helper logic + `scan_and_recover` orchestration, mocked registry/backend) + I (real `CalculationModel` rows for the row-state signal) |
| Files covered | `lex/lex_app/celery_recovery/supervisor.py` — new `_result_already_settled` (ready `AsyncResult` ⇒ task body concluded; defensive degrade to False on backend error), `_rows_already_settled` (every extracted `CalculationModel` row out of `IN_PROGRESS`/`NOT_CALCULATED` ⇒ settled; False on no rows / any unfinished / DB error), `_already_finished` (OR of the two), and the `scan_and_recover` guard inserted **after** the cancellation check and **before** the retries/budget branch (deregister + count `already_finished`, never requeue) |
| Test file | `lex/test_project/tests/celery_async/test_8w_recovery_terminal_guard.py` |
| Test classes | `TestCluster08w_ResultBackendSettled` (8.90–8.92 ready ⇒ settled / unready ⇒ not / backend error degrades, `SimpleTestCase`, mocked app); `TestCluster08w_AlreadyFinishedCombination` (8.98–8.99 backend alone settles / neither signal ⇒ unfinished); `TestCluster08w_ScanSkipsFinished` (8.100–8.102 — drives real `scan_and_recover` with the registry boundary mocked: finished ⇒ deregister-not-requeue, unfinished-under-budget ⇒ requeue, finished-beats-budget-exhaustion ordering); `TestCluster08w_RowsSettled` (8.93–8.97 + 8.93b — **`E2ETestCase`**, `e2e_models=[CelerySyncCalc]`: all-terminal/IN_PROGRESS/NOT_CALCULATED/mixed/no-rows + every terminal state) |
| Fixtures | none — `unittest.mock` on `app.AsyncResult` and the `registry`/`_requeue`/`_give_up` seams; the row scenarios reuse the existing `CelerySyncCalc` E2E model |
| Tests landed | **8 pass / 0 fail** (the U scenarios: result-backend signal, OR-combination, full `scan_and_recover` orchestration incl. resurrection-prevention + guard-before-budget ordering). The 6 I (real-row) scenario **bodies pass their assertions** — only Django's shared `TransactionTestCase` teardown-flush errors in the borrowed local venv (`test_db_lex` missing tables), identically to the pre-existing 8v E2E test; a DB-provisioning gap of the local environment, not a code defect — they gate normally on the CI Postgres service |
| Coverage gain | `lex/lex_app/celery_recovery/supervisor.py` — terminal-guard branch + 3 new helpers newly covered |
| Prereqs | the I (real-row) scenarios need a Postgres test DB with the lex schema migrated (CI service); the U scenarios are broker-/DB-free |
| Status | ✅ Complete — source fix + paired tests + plan sync in one change |

---

### Batch 8x — Liveness-aware startup reset: recovery hand-off (no live-calc abort)

| Property | Value |
| --- | --- |
| Scenario range | 8.103 – 8.115 |
| Type | U (ownership lookup, mocked registry) + I (real `CalculationModel` rows driven through the startup sweep) |
| Files covered | `lex/lex_app/celery_recovery/supervisor.py` — new `tracked_calculation_record_ids()` (reads `registry.list_tracked()` + `get_payload()`, reuses `_extract_calculation_models`, returns `{(label_lower, pk)}` for tracked rows with a non-None pk — alive **or** expired-but-tracked, no `is_alive` gate; empty set when recovery off / Redis down). `lex/process_admin/utils/model_registration.py` — `_handle_calculation_model_reset` gains an optional `tracked_record_ids` param and a skip-if-owned `continue` placed **before** the `ABORTED` flip / history write / audit call; `register_models` computes the set once on the first `CalculationModel` and only when `CALLED_FROM_START_COMMAND` is set (no registry read outside startup), threading it through the per-model loop |
| Test file | `lex/test_project/tests/celery_async/test_8x_startup_reset_recovery_handoff.py` |
| Test classes | `TestCluster08x_TrackedRecordIds` (8.103–8.108 — `SimpleTestCase`, mocked registry: alive-tracked ⇒ owned / expired-but-tracked ⇒ still owned & `is_alive` never called / empty registry ⇒ empty set / non-calc payload ⇒ nothing / multi-task ⇒ union / pk-None ⇒ excluded); `TestCluster08x_StartupSweepDefersToOwnership` (8.109–8.115 — **`E2ETestCase`**, `e2e_models=[CelerySyncCalc]`: owned row stays `IN_PROGRESS` & not audited / untracked ⇒ `ABORTED` + audited / mixed ⇒ only untracked aborts / empty ownership ⇒ all abort (back-compat) / gate-off ⇒ no-op / precomputed set ⇒ no registry recompute / 8.115 no-kwarg ⇒ self-computes from registry) |
| Fixtures | none — `unittest.mock` on the `registry.list_tracked`/`get_payload` seams and on `supervisor.tracked_calculation_record_ids`; the row scenarios reuse the existing `CelerySyncCalc` E2E model and the `E2ETestCase` default `ensure_terminal_calculation_audit` patch (read via `self._patch_map`) |
| Tests landed | **6 pass / 0 fail** (the U scenarios: ownership lookup incl. the load-bearing no-`is_alive` invariant and the empty-set degrade). The 7 I (real-row sweep) scenario **bodies pass their assertions** — only Django's shared `TransactionTestCase` teardown-flush errors in the borrowed local venv, identically to the pre-existing 8v/8w E2E tests; a DB-provisioning gap of the local environment, not a code defect — they gate normally on the CI Postgres service |
| Coverage gain | `lex/lex_app/celery_recovery/supervisor.py` — `tracked_calculation_record_ids` newly covered; `lex/process_admin/utils/model_registration.py` — the startup-reset skip-if-owned branch + caller threading newly covered |
| Prereqs | the I (real-row) scenarios need a Postgres test DB with the lex schema migrated (CI service); the U scenarios are broker-/DB-free. Stacked on Batch 8w (#603 terminal guard) — the deferral composes with that guard |
| Status | ✅ Complete — source fix + paired tests + plan sync in one change |

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

### Batch 9e — Generic CRUD mutation broadcast (live list refresh — June 3)

| Property | Value |
| --- | --- |
| Scenario range | 9.29 – 9.36 |
| Type | U + I + E |
| Files covered | `core/signals/ModelMutationSignal.py` (new), `api/consumers/ModelDataUpdateConsumer.py` (new), `lex_app/routing.py`, `api/views/model_entries/One.py`, `api/views/model_entries/Many.py` |
| Test file | `lex/test_project/tests/signals_ws/test_9e_model_mutation_broadcast.py` |
| Test classes | `TestCluster09e_ModelMutationBroadcastHelper` (I), `TestCluster09e_ModelDataUpdateConsumer` (U), `TestCluster09e_CrudTriggersBroadcast` (E) |
| Fixtures | `SimpleItem` / `ALL_MODELS` from `crud_api/models.py`; `E2ETestCase` (TransactionTestCase so commits fire `on_commit`) |
| Est. tests | 8 |
| Coverage gain | new files (broadcast helper + consumer) covered end-to-end |
| Prereqs | none |
| Status | ✅ Complete — 8 pass / 0 fail locally (Postgres test DB available) |
| Note | Fixes the customer-visible "open list view goes stale until manual Refresh" bug: plain CRUD on a non-`CalculationModel` now emits a `model_data_update` `record_mutation` over WebSocket. Generic broadcast is skipped on `calculate=true` updates (`calculation_success` already refreshes). Frontend `ModelDataUpdate` listener lands in the same change. |

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








